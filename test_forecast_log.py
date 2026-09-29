#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證預報存檔與型態一致性：能不能分出「領先翻對」與「獨自亂翻」。"""
import os, math, random
os.environ.setdefault('CWA_API_KEY', 'dummy')
import fetch_rainfall as F

fails = []
def ok(c, m):
    print(('OK  ' if c else '!!  ') + m)
    if not c: fails.append(m)

N = 368
# 兩種空間型態：東北部偏多 vs 西南部偏多（前半當東北、後半當西南）
NE = [8.0 if i < N // 2 else 1.0 for i in range(N)]
SW = [1.0 if i < N // 2 else 8.0 for i in range(N)]
def jitter(v, s=0.12):
    return [max(0.0, x * (1 + random.gauss(0, s))) for x in v]

def mklog(prev_map, new_map):
    return {'towns': [f't{i}' for i in range(N)],
            'issues': {'2026-09-27': {'2026-09-29': prev_map},
                       '2026-09-28': {'2026-09-29': new_map}}}

random.seed(3)
print("=== ① 去振幅：只放大雨量、型態不變 → 不算跳動 ===")
prev = {m: jitter(NE) for m in ('best', 'ecmwf', 'gfs', 'jma')}
new  = {m: [x * 2.5 for x in prev[m]] for m in prev}     # 整體翻 2.5 倍
r = F.forecast_consistency(mklog(prev, new), '2026-09-29')
ok(all(v['jump'] < 0.02 for v in r.values()),
   f"量翻 2.5 倍、型態不變 → 跳動 {max(v['jump'] for v in r.values()):.4f}（應接近 0）")

print("\n=== ② 大家一起翻 → 超額接近 0，不該有人被判定出問題 ===")
prev = {m: jitter(NE) for m in ('best', 'ecmwf', 'gfs', 'jma')}
new  = {m: jitter(SW) for m in ('best', 'ecmwf', 'gfs', 'jma')}
r = F.forecast_consistency(mklog(prev, new), '2026-09-29')
mx = max(abs(v['excess']) for v in r.values())
ok(all(v['jump'] > 1.0 for v in r.values()), "四個模式都確實翻盤（jump > 1）")
ok(mx < 0.15, f"但超額都接近 0（最大 {mx:.3f}）→ 沒有人被當成異常")

print("\n=== ③ 三個沒翻、一個自己翻 → 那一個超額明顯最大 ===")
prev = {m: jitter(NE) for m in ('best', 'ecmwf', 'gfs', 'jma')}
new  = {m: jitter(NE) for m in ('best', 'ecmwf', 'gfs')}
new['jma'] = jitter(SW)
r = F.forecast_consistency(mklog(prev, new), '2026-09-29')
lone = r['jma']['excess']; others = max(r[m]['excess'] for m in ('best','ecmwf','gfs'))
ok(lone > 1.0 and lone > others + 1.0,
   f"獨自翻的 jma 超額 {lone:.2f}，其餘最大 {others:.2f}")

print("\n=== ④ 領先翻對 vs 獨自亂翻：靠 gain 分辨（只看超額會罰錯人）===")
obs = jitter(SW, 0.20)          # 實際下在西南部
# 4-a：jma 領先翻到西南（翻對）
r_ok = F.forecast_consistency(mklog(prev, new), '2026-09-29', obs)
# 4-b：jma 獨自翻到東北以外的錯誤型態（實際仍是西南）
new_bad = {m: jitter(SW) for m in ('best', 'ecmwf', 'gfs')}
new_bad['jma'] = jitter(NE)
prev_all_sw = {m: jitter(SW) for m in ('best','ecmwf','gfs','jma')}
r_bad = F.forecast_consistency(mklog(prev_all_sw, new_bad), '2026-09-29', obs)
g_ok, g_bad = r_ok['jma'].get('gain'), r_bad['jma'].get('gain')
print(f"  領先翻對：超額 {r_ok['jma']['excess']:+.2f}　修正 {g_ok:+.3f}")
print(f"  獨自亂翻：超額 {r_bad['jma']['excess']:+.2f}　修正 {g_bad:+.3f}")
ok(g_ok is not None and g_ok > 0.3, f"領先翻對 → 修正為正（{g_ok:+.3f}）")
ok(g_bad is not None and g_bad < -0.3, f"獨自亂翻 → 修正為負（{g_bad:+.3f}）")
ok(r_ok['jma']['excess'] > 0.5 and r_bad['jma']['excess'] > 0.5,
   "兩者的超額都很大 → 只看超額會把領先者當成亂報，必須配 gain 一起看")

print("\n=== ⑤ 小雨日不計型態（雜訊）===")
lo = [0.3] * N
r = F.forecast_consistency(mklog({'best': lo, 'ecmwf': lo}, {'best': lo, 'ecmwf': lo}),
                           '2026-09-29')
ok(r == {}, f"全島 0.3mm → 不納入計算（得 {r}）")

print("\n=== ⑥ 只有一報時不計算 ===")
one = {'towns': [f't{i}' for i in range(N)],
       'issues': {'2026-09-28': {'2026-09-29': {'best': NE}}}}
ok(F.forecast_consistency(one, '2026-09-29') == {}, "只有一報 → 回空")

print("\n全部通過" if not fails else f"\n失敗 {len(fails)} 項：{fails}")
raise SystemExit(1 if fails else 0)
