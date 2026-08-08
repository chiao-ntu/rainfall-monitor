#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證 slope_est() 是否忠實反映技術指引的黃色警戒門檻，以及缺值不猜。"""
import os
os.environ.setdefault('CWA_API_KEY', 'dummy')
from fetch_rainfall import slope_est as S

fails = []
def chk(label, got, exp):
    ok = got == exp
    if not ok: fails.append(label)
    print(f"  {'OK ' if ok else '!! '}{label}: {got!r}" + ("" if ok else f"  期望 {exp!r}"))

print("=== 門檻比例：≦350mm 用 30%、≧400mm 用 40% ===")
chk("警戒值350 → 門檻0.30", S(100, 350, 0)['yellow_th'], 0.30)
chk("警戒值400 → 門檻0.40", S(100, 400, 0)['yellow_th'], 0.40)
chk("警戒值200 → 門檻0.30", S(100, 200, 0)['yellow_th'], 0.30)
chk("警戒值1500(大崩) → 門檻0.40", S(100, 1500, 0)['yellow_th'], 0.40)

print("\n=== 黃色警戒發布標準（警戒值300，門檻30%＝90mm）===")
# 實際未達30% → 即使預測爆表也不該發黃（指引明訂兩條件都要滿足）
r = S(80, 300, 500)
chk("實際80mm(<90) 預測+500 → 不符黃警標準", r['est_yellow_now'], False)
chk("  但未來24h會達紅",                     r['est_red_fc'],     True)
# 實際達30%、加預測超過 → 符合
r = S(90, 300, 250)
chk("實際90mm(=90) 預測+250 → 符合黃警標準", r['est_yellow_now'], True)
# 實際達30%、加預測仍不足 → 不符
r = S(90, 300, 100)
chk("實際90mm 預測+100(合190<300) → 不符",   r['est_yellow_now'], False)
chk("  未來24h也不會達紅",                   r['est_red_fc'],     False)

print("\n=== 邊界：恰好等於警戒值 ===")
r = S(300, 300, 0)
chk("實際已達警戒值 → est_red_fc True", r['est_red_fc'], True)
chk("pct = 1.0",                        r['pct'],        1.0)
r = S(299.9, 300, 0.05)
chk("合計299.95<300 → est_red_fc False", r['est_red_fc'], False)

print("\n=== 入夜前示警 ===")
chk("夜間QPF=None → night_warn None（不猜）", S(200, 300, 50, None)['night_warn'], None)
chk("實際200+夜間120=320≥300 → True",        S(200, 300, 50, 120)['night_warn'], True)
chk("實際200+夜間50=250<300 → False",        S(200, 300, 50, 50)['night_warn'],  False)

print("\n=== 缺值不猜（全部 None，不以 False 冒充未達標）===")
for lbl, args in [("etr2=None", (None, 300, 50)),
                  ("alert=None", (100, None, 50)),
                  ("alert=0",   (100, 0, 50))]:
    r = S(*args)
    bad = [k for k in ('est_yellow_now', 'est_red_fc', 'pct', 'fc_pct') if r[k] is not None]
    chk(f"{lbl} → 判定欄位全 None", bad, [])

print("\n=== qpf24=None 視為 0（無預報不等於會下雨）===")
r = S(100, 300, None)
chk("fc_etr2 == 實際值", r['fc_etr2'], 100.0)
chk("est_red_fc False",  r['est_red_fc'], False)

print("\n全部通過" if not fails else f"\n失敗 {len(fails)} 項：{fails}")
raise SystemExit(1 if fails else 0)
