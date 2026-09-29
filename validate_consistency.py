#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""收斂指標的假設檢驗 —— 資料累積後直接跑，不必再開發。

要回答的問題只有一個：
    「超額跳動大、而且修正方向錯」的那些報次，事後 MAE 是不是真的比較差？

相關性夠強，第二層（穩定度）才有資格接進權重；不夠強就只留作診斷顯示，
不去污染融合。這一步是刻意設計來「可能推翻自己」的 —— 沒有這一步，
把穩定度接進權重就只是憑感覺加參數。

用法：
    python3 validate_consistency.py                 # 讀 forecast_log.json + verify.json
    python3 validate_consistency.py --self-test     # 用合成資料自我驗證腳本邏輯
"""
import os, sys, json, math

os.environ.setdefault('CWA_API_KEY', 'dummy')
import fetch_rainfall as F

MIN_CASES = 40          # 少於此數不下結論（樣本太少的相關係數沒有意義）
BOOT_N    = 2000        # bootstrap 次數


def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 1e-12 or syy <= 1e-12:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / math.sqrt(sxx * syy)


def _boot_ci(xs, ys, n=BOOT_N, seed=1):
    import random
    rnd = random.Random(seed)
    N = len(xs)
    rs = []
    for _ in range(n):
        idx = [rnd.randrange(N) for _ in range(N)]
        r = _pearson([xs[i] for i in idx], [ys[i] for i in idx])
        if r is not None:
            rs.append(r)
    if len(rs) < 100:
        return None, None
    rs.sort()
    return rs[int(len(rs) * 0.025)], rs[int(len(rs) * 0.975)]


def collect(fclog, obs_by_date):
    """回傳每個（有效日, 模式）的 (超額跳動, 修正方向, 事後MAE)。"""
    out = []
    issues = fclog.get('issues') or {}
    valid_dates = sorted({d for v in issues.values() for d in (v or {})})
    for vd in valid_dates:
        obs = obs_by_date.get(vd)
        if not obs:
            continue
        cons = F.forecast_consistency(fclog, vd, obs)
        if not cons:
            continue
        # 該有效日「最後一報」的預報，用來算事後 MAE
        iss = sorted(k for k, v in issues.items() if vd in (v or {}))
        last = issues[iss[-1]][vd]
        for m, c in cons.items():
            fc = last.get(m)
            if not fc:
                continue
            pairs = [(f, o) for f, o in zip(fc, obs)
                     if f is not None and o is not None]
            if len(pairs) < 30:
                continue
            mae = sum(abs(f - o) for f, o in pairs) / len(pairs)
            out.append({'date': vd, 'model': m, 'excess': c.get('excess'),
                        'gain': c.get('gain'), 'jump': c.get('jump'), 'mae': mae})
    return out


def report(rows):
    rows = [r for r in rows if r['excess'] is not None and r['mae'] is not None]
    print(f"樣本：{len(rows)} 筆（有效日 × 模式）")
    if len(rows) < MIN_CASES:
        print(f"!! 樣本不足 {MIN_CASES} 筆，不下結論。請再累積幾天後重跑。")
        return 2

    # ① 超額跳動 vs 事後 MAE
    xs = [r['excess'] for r in rows]; ys = [r['mae'] for r in rows]
    r1 = _pearson(xs, ys)
    lo1, hi1 = _boot_ci(xs, ys)
    print(f"\n① 超額跳動 vs 事後 MAE：r = {r1:+.3f}"
          + (f"　95% CI [{lo1:+.3f}, {hi1:+.3f}]" if lo1 is not None else ""))

    # ② 分組：超額大且修正方向錯 vs 其餘
    g = [r for r in rows if r['gain'] is not None]
    bad = [r for r in g if r['excess'] > 0.10 and r['gain'] < 0]
    good = [r for r in g if not (r['excess'] > 0.10 and r['gain'] < 0)]
    print(f"\n② 分組（有 gain 的 {len(g)} 筆）")
    if bad and good:
        mb = sum(r['mae'] for r in bad) / len(bad)
        mg = sum(r['mae'] for r in good) / len(good)
        print(f"   超額大且修正錯：{len(bad)} 筆，平均 MAE {mb:.2f}")
        print(f"   其餘          ：{len(good)} 筆，平均 MAE {mg:.2f}")
        print(f"   差距：{mb - mg:+.2f} mm（{(mb/mg - 1)*100:+.0f}%）")
    else:
        mb = mg = None
        print("   其中一組為空，無法比較")

    # ③ 對照：領先翻對（超額大但修正對）該不該被罰
    lead = [r for r in g if r['excess'] > 0.10 and r['gain'] > 0]
    if lead and good:
        ml = sum(r['mae'] for r in lead) / len(lead)
        print(f"\n③ 領先翻對：{len(lead)} 筆，平均 MAE {ml:.2f}"
              f"（其餘 {mg:.2f}）")
        print("   " + ("→ 領先者並沒有比較差，證實不能只看超額跳動"
                       if ml <= mg * 1.05 else
                       "→ 領先者也偏差，需重新檢視 gain 的定義"))

    # 結論
    print("\n" + "=" * 52)
    strong = (r1 is not None and r1 > 0.25 and lo1 is not None and lo1 > 0
              and mb is not None and mb > mg * 1.15)
    if strong:
        print("結論：穩定度與事後誤差有正相關且信賴區間不跨 0 ——")
        print("      第二層可以接進權重（建議先以 ±20% 的限幅上線）。")
    else:
        print("結論：相關性不足以支撐接進權重。")
        print("      維持只顯示、不參與融合；再累積資料後重跑。")
    print("=" * 52)
    return 0 if strong else 1


def self_test():
    """合成資料：讓「超額大且修正錯」確實對應較大的 MAE，檢查腳本抓不抓得到。"""
    import random
    rnd = random.Random(7)
    N = 368
    rows = []
    for i in range(200):
        bad = (i % 3 == 0)
        excess = abs(rnd.gauss(0.6 if bad else 0.02, 0.15))
        gain = -abs(rnd.gauss(0.4, 0.2)) if bad else abs(rnd.gauss(0.3, 0.2))
        mae = (14.0 if bad else 8.0) + rnd.gauss(0, 2.0)
        rows.append({'date': f'd{i}', 'model': 'x', 'excess': excess,
                     'gain': gain, 'jump': excess, 'mae': max(0.1, mae)})
    print("=== 自我測試（合成資料，已知穩定度與誤差相關）===")
    rc = report(rows)
    print("\n自我測試通過（腳本抓到了相關性）" if rc == 0
          else "\n!! 自我測試失敗：腳本在已知有相關的資料上沒抓到")
    return 0 if rc == 0 else 1


def main():
    if '--self-test' in sys.argv:
        return self_test()
    if not os.path.exists(F.FORECAST_LOG_FILE):
        print(f"找不到 {F.FORECAST_LOG_FILE} —— 預報存檔尚未產生，"
              f"請先讓 fetch_rainfall.py 跑過幾輪。")
        return 2
    with open(F.FORECAST_LOG_FILE, encoding='utf-8') as f:
        fclog = json.load(f)
    # 觀測直接取自同一份存檔（update_forecast_log 一併寫入），
    #   鄉鎮索引保證與預報一致，不會錯位。
    obs_by_date = fclog.get('obs') or {}
    if not obs_by_date:
        print("!! 存檔中還沒有逐日觀測（需要 fetch_rainfall.py 跑過至少一輪新版）。")
        return 2
    print(f"存檔：{len(fclog.get('issues') or {})} 報、"
          f"{len(obs_by_date)} 天觀測")
    return report(collect(fclog, obs_by_date))


if __name__ == '__main__':
    raise SystemExit(main())
