#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""FORMOSA 數值來源稽核：把每個顯示的數字對回它的來源，逐項檢查。

為什麼需要這支：
  前幾輪的除錯一直在「猜資料長什麼樣 → 改 → 等你跑 → 還是錯」的迴圈裡。
  那個迴圈的成本是你的時間。這支把判斷依據一次攤開，不必再猜。

檢查項目
  A 內部一致性：etr2 / etr2_alert 是否等於 etr2_pct（算式自身對不對）
  B 觀測 vs 官方：以觀測逐日雨量套官方權重重算 ETR2，與官方值比對
      ★ 這是最關鍵的一條。官方權重是固定的 [1,.7,.5,.4,.3,.2,.1]，
        所以「沒下雨就不可能有高 ETR2」。落差大＝官方值過期，或我們的
        觀測漏了官方採用的那個站。兩者都必須查出來。
  C 觀測本身：負值、今日值與測站不符、逐日與滾動窗矛盾
  D 跨快照衰減：同一鄉鎮的 ETR2 在沒下雨的日子必須下降。
      不動＝來源給了過期值（這條只有多個 archive 快照才驗得出來）
  E 測站級 ETR2% 覆蓋率：有多少測站拿不到 ETR2%，以及指定站名的個案
  F 來源清單：每個欄位的實際來源與更新時間

用法
    python3 audit_data_sources.py                 # 讀 data.json + archive/
    python3 audit_data_sources.py <data.json>
    python3 audit_data_sources.py --town 宜蘭縣大同鄉   # 單一鄉鎮細查
回傳碼 0＝沒有嚴重問題，1＝有需要處理的項目。
"""
import json
import os
import sys
import glob
from datetime import datetime

W = [1.0, 0.7, 0.5, 0.4, 0.3, 0.2, 0.1]      # 官方 R0~R6 固定權重
GAP_WARN_MM = 50.0          # 觀測重算與官方值的落差警示門檻
PCT_TOL = 0.6               # etr2_pct 容許誤差（百分點）

issues = []
def flag(sev, msg):
    issues.append((sev, msg))
    print(f'{"!!" if sev == "high" else " ·"} {msg}')


def load(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def towns_of(d):
    for k in ('townships', 'towns', 'data'):
        v = d.get(k)
        if isinstance(v, list) and v and isinstance(v[0], dict): return v
    if isinstance(d, list): return d
    return []


def key(t):
    return (t.get('county') or '') + (t.get('township') or '')


def obs_etr2(t):
    """以觀測逐日雨量套官方權重重算 ETR2。回傳 (值, 有幾天有資料)。"""
    dr = t.get('daily_rain') or []
    n = 0; v = 0.0
    for i in range(7):
        r = dr[i] if i < len(dr) else None
        if r is None: continue
        v += W[i] * float(r); n += 1
    return round(v, 1), n


# ═══════════════════════════════════════════════════════
def main():
    argv = sys.argv[1:]
    only = None
    if '--town' in argv:
        i = argv.index('--town')
        if i + 1 < len(argv):
            only = argv[i + 1]
            del argv[i:i + 2]            # ★ 連值一起移除，否則會被當成檔案路徑
        else:
            del argv[i]
    args = [a for a in argv if not a.startswith('--')]
    path = args[0] if args else 'data.json'
    if not os.path.exists(path):
        print(f'找不到 {path}（請在 repo 根目錄執行，或指定路徑）')
        return 1
    d = load(path)
    ts = d.get('generated') or d.get('updated') or '?'
    T = towns_of(d)
    print('=' * 60)
    print(f'FORMOSA 數值來源稽核　資料時間 {ts}　鄉鎮 {len(T)}')
    print('=' * 60)

    if only:
        T2 = [t for t in T if key(t) == only or only in key(t)]
        if not T2:
            print(f'找不到鄉鎮 {only}'); return 1
        for t in T2: detail(t)
        return 0

    # ── A 內部一致性 ──
    print('\n[A] 內部一致性：etr2 ÷ etr2_alert 是否等於 etr2_pct')
    bad_a = []
    for t in T:
        e, a, p = t.get('etr2'), t.get('etr2_alert'), t.get('etr2_pct')
        if e is None or not a or p is None: continue
        exp = e / a                      # etr2_pct 是比值（0~1）
        if abs(exp - p) > PCT_TOL / 100:
            bad_a.append((key(t), e, a, p, round(exp, 4)))
    if bad_a:
        flag('high', f'A 有 {len(bad_a)} 個鄉鎮的 etr2_pct 與 etr2/etr2_alert 不符')
        for r in bad_a[:6]:
            print(f'      {r[0]}　etr2 {r[1]} / alert {r[2]} = {r[4]}，但 etr2_pct = {r[3]}')
    else:
        print('   OK　算式自身一致（分子分母同一個警戒單元）')

    # ── B 觀測 vs 官方（最關鍵）──
    print('\n[B] 觀測重算 vs 官方值　★ 沒下雨就不可能有高 ETR2')
    gaps = []
    for t in T:
        off = t.get('etr2')
        if off is None: continue
        ob, nday = obs_etr2(t)
        if nday < 3: continue             # 觀測天數太少不判斷
        gaps.append((off - ob, key(t), off, ob, t.get('etr2_src'),
                     (t.get('daily_rain') or [])[:5]))
    gaps.sort(reverse=True)
    big = [g for g in gaps if g[0] > GAP_WARN_MM]
    if big:
        flag('high', f'B 有 {len(big)} 個鄉鎮「官方值遠高於觀測可解釋的範圍」'
                     f'（落差 >{GAP_WARN_MM:.0f}mm）')
        print('      落差mm  鄉鎮              官方   觀測重算  來源   逐日雨量(今,昨,前,…)')
        for g in big[:12]:
            print(f'      {g[0]:7.1f}  {g[1]:<16} {g[2]:6.1f} {g[3]:8.1f}  {str(g[4]):<6} {g[5]}')
        print('      ── 判讀 ──')
        print('      逐日雨量幾乎都是 0 卻有高官方值 → 官方來源過期（ETR2 必須隨雨齡遞減）')
        print('      逐日雨量有雨但仍差很多 → 我們的觀測漏了官方採用的那個站')
    else:
        print(f'   OK　最大落差 {gaps[0][0]:.1f}mm（{gaps[0][1]}），在合理範圍')

    neg = [g for g in gaps if g[0] < -GAP_WARN_MM]
    if neg:
        flag('high', f'B2 有 {len(neg)} 個鄉鎮「觀測遠高於官方值」'
                     f'（官方漏報或我們的觀測偏高）')
        for g in neg[:6]:
            print(f'      {g[1]}　官方 {g[2]:.1f} / 觀測重算 {g[3]:.1f}　逐日 {g[5]}')

    # ── C 觀測本身 ──
    print('\n[C] 觀測資料本身')
    c_neg = [key(t) for t in T
             for r in (t.get('daily_rain') or []) if r is not None and r < 0]
    if c_neg:
        flag('high', f'C 有負值逐日雨量：{len(c_neg)} 筆（{c_neg[:5]}）')
    c_huge = [(key(t), r) for t in T
              for r in (t.get('daily_rain') or []) if r is not None and r > 1500]
    if c_huge:
        flag('high', f'C 有超過 1500mm 的單日雨量（物理上極罕見）：{c_huge[:5]}')
    # 今日值與 rain_24h 的關係：今日累積不應遠超過近24h
    c_bad = []
    for t in T:
        d0 = (t.get('daily_rain') or [None])[0]
        r24 = t.get('rain_24h')
        if d0 is None or r24 is None: continue
        if d0 > r24 + 30:                 # 容許跨日與時間差
            c_bad.append((key(t), d0, r24))
    if c_bad:
        flag('low', f'C 有 {len(c_bad)} 個鄉鎮「今日累積 > 近24h+30mm」'
                    f'（可能是跨日定版或來源不一致）')
        for r in c_bad[:6]: print(f'      {r[0]}　今日 {r[1]} / 近24h {r[2]}')
    if not (c_neg or c_huge or c_bad):
        print('   OK　無負值、無異常大值、今日累積與近24h相容')

    # ── D 跨快照衰減 ──
    print('\n[D] 跨快照：沒下雨的日子 ETR2 必須下降')
    snaps = sorted(glob.glob(os.path.join('archive', '*.json')))
    if len(snaps) < 2:
        print(f'   —　archive/ 只有 {len(snaps)} 個快照，無法比較'
              f'（累積幾天後再跑這條）')
    else:
        use = snaps[-6:]
        series = {}
        for sp in use:
            try: sd = load(sp)
            except Exception: continue
            for t in towns_of(sd):
                e = t.get('etr2')
                if e is None: continue
                series.setdefault(key(t), []).append(
                    (os.path.basename(sp), e, (t.get('daily_rain') or [None])[0]))
        frozen = []
        for k, rows in series.items():
            if len(rows) < 3: continue
            vals = [r[1] for r in rows]
            rains = [r[2] for r in rows if r[2] is not None]
            dry = all((r or 0) < 1.0 for r in rains) if rains else False
            if dry and len(set(round(v, 1) for v in vals)) == 1 and vals[0] > 20:
                frozen.append((k, vals[0], len(rows)))
        if frozen:
            flag('high', f'D 有 {len(frozen)} 個鄉鎮在「連續無雨」下 ETR2 完全不變'
                         f'（來源過期的強證據）')
            for r in frozen[:8]:
                print(f'      {r[0]}　ETR2 固定在 {r[1]}（{r[2]} 個快照都一樣）')
        else:
            print(f'   OK　{len(use)} 個快照中沒有「無雨但 ETR2 凍結」的鄉鎮')

    # ── E 測站級 ETR2% 覆蓋 ──
    print('\n[E] 測站級 ETR2% 覆蓋率')
    n_st = n_null = 0
    named = {}
    for t in T:
        if t.get('etr2') is None: continue
        for st in (t.get('stations') or []):
            n_st += 1
            if st.get('etr2_pct') is None: n_null += 1
            nm = st.get('name') or ''
            if '寒溪' in nm or nm in ('關山', '武陵'):
                named.setdefault(nm, []).append(
                    (key(t), st.get('etr2'), st.get('etr2_pct'), st.get('sid')))
    if n_st:
        r = n_null / n_st * 100
        msg = (f'E 測站 {n_st} 個，其中 {n_null} 個（{r:.0f}%）沒有 ETR2%')
        if r > 30: flag('high', msg)
        elif r > 10: flag('low', msg)
        else: print(f'   OK　{msg}')
    for nm, rows in sorted(named.items()):
        print(f'      「{nm}」{len(rows)} 筆：')
        for r in rows[:4]:
            print(f'        {r[0]}　etr2 {r[1]}　etr2_pct {r[2]}　sid {r[3]}')

    # ── G 對照官方基準表（最硬的一條）──
    print('\n[G] 對照水保署官方表')
    fx = None
    for fp in ('official_etr2_fixture.json',):
        if os.path.exists(fp):
            try: fx = load(fp)
            except Exception as e: print(f'   讀取 {fp} 失敗：{e}')
    if not fx:
        print('   —　找不到 official_etr2_fixture.json，略過'
              '（放進 repo 根目錄即可逐鄉鎮核對）')
    else:
        off = fx.get('townships') or {}
        print(f"   基準時刻 {fx.get('asof')}　官方鄉鎮 {len(off)} 個")
        print('   ※ ETR2 隨時間變動：只有在「系統資料時間」與基準時刻相近時，'
              '數值差異才有意義；\n'
              '     但「用哪一個測站」與「有沒有多出官方沒有的鄉鎮」隨時都該一致。')
        mine = {key(t): t for t in T}
        wrong_stn, miss, extra, diff = [], [], [], []
        for k, o in off.items():
            t = mine.get(k)
            if t is None or t.get('etr2') is None:
                miss.append((k, o['pct'], o['station'])); continue
            # 用的是不是官方那一站
            used = None
            for r in (t.get('slope_regions') or []):
                if r.get('etr2') == t.get('etr2'):
                    used = r.get('station'); break
            if used and used != o['station']:
                wrong_stn.append((k, used, o['station'], t.get('etr2_pct'), o['pct']))
            p = t.get('etr2_pct')
            if p is not None:
                diff.append((abs(p - o['pct']), k, p, o['pct'], used, o['station']))
        for k, t in mine.items():
            if t.get('etr2') is not None and k not in off:
                extra.append((k, t.get('etr2_pct')))
        if miss:
            flag('high', f'G 官方有 ETR2 但我們沒有：{len(miss)} 個鄉鎮')
            for r in miss[:8]:
                print(f'      {r[0]}　官方 {r[1]*100:.1f}%（{r[2]}）')
        if extra:
            flag('high', f'G 我們有 ETR2 但不在官方 159 個警戒鄉鎮內：{len(extra)} 個')
            for r in extra[:8]:
                print(f'      {r[0]}　我們 {(r[1] or 0)*100:.1f}%')
        if wrong_stn:
            flag('high', f'G 取值用的測站與官方不符：{len(wrong_stn)} 個鄉鎮'
                         f'（這是最嚴重的 —— 等於用了別的站的雨）')
            print('      鄉鎮              我們用的      官方指定      我們%   官方%')
            for r in wrong_stn[:10]:
                print(f'      {r[0]:<16} {r[1]:<12} {r[2]:<12} '
                      f'{(r[3] or 0)*100:5.1f}  {r[4]*100:5.1f}')
        if not (miss or extra or wrong_stn):
            print('   OK　鄉鎮集合與取值測站都與官方一致')
        if diff:
            diff.sort(reverse=True)
            print(f'   數值差異最大的 5 個（僅供參考，須注意時間差）：')
            for r in diff[:5]:
                print(f'      {r[1]:<16} 我們 {r[2]*100:5.1f}%　官方 {r[3]*100:5.1f}%'
                      f'　（我們用 {r[4]}／官方 {r[5]}）')

    # ── H 測站涵蓋（使用者要求：一個都不能漏）──
    print('\n[H] 官方測站涵蓋率')
    if not os.path.exists('slope_warning_stations.json'):
        print('   —　找不到 slope_warning_stations.json')
    else:
        try:
            sw = load('slope_warning_stations.json')
            want = set()
            for k, regs in (sw.get('townships') or {}).items():
                for r in regs:
                    if r.get('station'): want.add((k, r['station']))
            have = set()
            for t in T:
                for r in (t.get('slope_regions') or []):
                    if r.get('station'): have.add((key(t), r['station']))
            lack = sorted(want - have)
            print(f'   官方警戒單元 {len(want)} 個（鄉鎮×代表站）')
            if lack:
                flag('high', f'H 有 {len(lack)} 個官方警戒單元沒有出現在輸出中')
                for r in lack[:10]: print(f'      {r[0]}　「{r[1]}」')
            else:
                print('   OK　每個官方警戒單元都有對應輸出')
        except Exception as e:
            print(f'   讀取失敗：{e}')

    # ── F 來源清單 ──
    print('\n[F] 各數值的來源')
    src = [
        ('鄉鎮 ETR2／ETR2%', 'etr2 / etr2_pct',
         '水保署 GetDebrisRainData（逐潛勢溪流 STRT÷AlertValue 取最大）'),
        ('逐日觀測雨量', 'daily_rain[]',
         '氣象署 O-A0002 Now（今日）＋ rainfall_history.json（過去日定版）'),
        ('近24h觀測', 'rain_24h', '氣象署 O-A0002 Past24hr'),
        ('測站 ETR2', 'stations[].etr2',
         '水保署 STRT（經代表站名／站號解析）'),
        ('預測雨量（各模式）', 'qpf_*', 'Open-Meteo 各模式 6h 累積'),
        ('CWA 官方 QPF 色帶', 'qpf_cwa', 'CWA F-C0035 定量降水預報圖（色帶下界，僅著色）'),
        ('CWA 可加量', 'qpf_cwa_q', '同上，色帶上界÷窗段數（可累加）'),
        ('雷達 QPF', 'qpf_radar_1h', 'CWA F-B0046'),
        ('官方警特報', 'official_warn', 'CWA W-C0033-001'),
        ('官方土石流警戒', 'debris_alerts', '水保署 LandslideAlertOpenData'),
    ]
    print(f'      {"顯示項目":<22}{"欄位":<18}來源')
    for a, b, c in src:
        print(f'      {a:<22}{b:<18}{c}')
    for fld, lbl in (('cwa_qpf_src', 'CWA QPF 來源'),
                     ('cwa_qpf_segs', 'CWA 覆蓋段'),
                     ('bias_24h_median', '昨日偏差比中位數'),
                     ('bias_24h_n', '偏差比樣本數')):
        if fld in d: print(f'      · {lbl}：{d[fld]}')
    if d.get('bias_24h_n') is not None and d['bias_24h_n'] < 30:
        flag('low', f'F 偏差比樣本數只有 {d["bias_24h_n"]} 筆，'
                    f'自適應融合用它調權重會很不穩')

    # ── 結論 ──
    print('\n' + '=' * 60)
    hi = [m for s, m in issues if s == 'high']
    lo = [m for s, m in issues if s == 'low']
    if hi:
        print(f'需要處理（{len(hi)}）：')
        for m in hi: print(f'  !! {m}')
    if lo:
        print(f'留意（{len(lo)}）：')
        for m in lo: print(f'   · {m}')
    if not issues:
        print('所有檢查通過。')
    print('=' * 60)
    return 1 if hi else 0


def detail(t):
    """單一鄉鎮細查：把每個數字的來源與算式攤開。"""
    print(f"\n──── {key(t)} ────")
    ob, nday = obs_etr2(t)
    print(f"官方 etr2 = {t.get('etr2')}　警戒值 = {t.get('etr2_alert')}"
          f"　etr2_pct = {t.get('etr2_pct')}　來源 = {t.get('etr2_src')}")
    print(f"觀測重算 = {ob}（逐日雨量 {nday} 天有值）")
    dr = (t.get('daily_rain') or [])[:8]
    print(f"逐日雨量（今,昨,前,…）= {dr}")
    print(f"  權重 {W} → " + ' + '.join(
        f"{W[i]}×{dr[i] if i < len(dr) else 0}" for i in range(min(7, len(dr)))))
    g = (t.get('etr2') or 0) - ob
    print(f"落差 = {g:.1f} mm"
          + ("　⚠ 官方遠高於觀測可解釋範圍" if g > GAP_WARN_MM else ""))
    print(f"\n各警戒單元（slope_regions）：")
    for r in (t.get('slope_regions') or [])[:12]:
        print(f"  {r.get('village','')}　「{r.get('station','')}」"
              f"ETR2 {r.get('etr2')}／警戒 {r.get('alert')}"
              f" = {r.get('etr2_pct')}　來源 {r.get('src')}")
    print(f"\n測站（stations）：")
    for st in (t.get('stations') or [])[:12]:
        print(f"  {st.get('name','')}　sid {st.get('sid','')}"
              f"　etr2 {st.get('etr2')}　etr2_pct {st.get('etr2_pct')}")


if __name__ == '__main__':
    sys.exit(main())
