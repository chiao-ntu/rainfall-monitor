#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證 _blend_yday 與實際播出邏輯一致，並確認融合在雨量上贏過最佳單模式。"""
import os, random, math
os.environ.setdefault('CWA_API_KEY', 'dummy')
from fetch_rainfall import _blend_yday, ADAPT_BLOCK

fails = []
def ok(c, m):
    print(('OK  ' if c else '!!  ') + m)
    if not c: fails.append(m)

MS = ['best','ecmwf','gfs','jma','aifs','graphcast','icon','kma','gem','ukmo','mf','cma','bom']

print("=== ① 排除清單：cma / icon 不得進入融合 ===")
t = {'model_yday': {m: 10.0 for m in MS}}
t['model_yday']['icon'] = 500.0      # 亂報
t['model_yday']['cma']  = 500.0
v = _blend_yday(t, '平地', None, None)
ok(abs(v - 10.0) < 1e-6, f"等權退回時已排除 {ADAPT_BLOCK}：得 {v}（應 10.0，不受 500 影響）")

print("\n=== ② 自適應權重：權重 0 的模式不計入 ===")
w = {'平地': {'ecmwf': 1.0, 'gfs': 1.0}}
t2 = {'model_yday': {'ecmwf': 20.0, 'gfs': 40.0, 'jma': 999.0}}
v = _blend_yday(t2, '平地', w, None)
ok(abs(v - 30.0) < 1e-6, f"只用 ecmwf/gfs：得 {v}（應 30.0）")

print("\n=== ③ 偏差校正：用上一輪的逐地形 bias，AI 模式上限 3.0、物理 2.0 ===")
sk = {'平地': {'ecmwf': {'decay': {'bias': 1.5, 'n': 50}},
               'aifs':  {'decay': {'bias': 4.0, 'n': 50}},
               'gfs':   {'decay': {'bias': 1.2, 'n': 5}}}}   # n<10 不套用
t3 = {'model_yday': {'ecmwf': 10.0}}
ok(abs(_blend_yday(t3, '平地', {'平地': {'ecmwf': 1}}, sk) - 15.0) < 1e-6,
   "物理模式 bias 1.5 → 10 校正為 15")
t4 = {'model_yday': {'aifs': 10.0}}
ok(abs(_blend_yday(t4, '平地', {'平地': {'aifs': 1}}, sk) - 30.0) < 1e-6,
   "AI 模式 bias 4.0 被限在 3.0 → 10 校正為 30")
t5 = {'model_yday': {'gfs': 10.0}}
ok(abs(_blend_yday(t5, '平地', {'平地': {'gfs': 1}}, sk) - 10.0) < 1e-6,
   "樣本不足（n=5）不套用校正 → 維持 10")

print("\n=== ④ 佐證原則：唯一一個遠高於中位數者降權至 0.35 ===")
t6 = {'model_yday': {'best': 10.0, 'ecmwf': 10.0, 'gfs': 10.0, 'jma': 200.0}}
w4 = {'平地': {m: 1.0 for m in ('best','ecmwf','gfs','jma')}}
v = _blend_yday(t6, '平地', w4, None)
exp = (10*1 + 10*1 + 10*1 + 200*0.35) / (1+1+1+0.35)
ok(abs(v - round(exp, 2)) < 0.02, f"孤例 200mm 降權：得 {v}（應 {exp:.2f}，未降權會是 57.5）")
t7 = {'model_yday': {'best': 10.0, 'ecmwf': 10.0, 'gfs': 180.0, 'jma': 200.0}}
v7 = _blend_yday(t7, '平地', w4, None)
exp7 = (10+10+180+200)/4
ok(abs(v7 - round(exp7, 2)) < 0.02, f"兩個模式都報高 → 互為佐證，不降權：得 {v7}（應 {exp7:.1f}）")

print("\n=== ⑤ 不得使用觀測值（校驗不能偷看答案）===")
t8 = {'model_yday': {'best': 50.0, 'ecmwf': 50.0}, 'daily_rain': [0, 0], 'radar_1h': 0}
t9 = {'model_yday': {'best': 50.0, 'ecmwf': 50.0}, 'daily_rain': [0, 300], 'radar_1h': 99}
ok(_blend_yday(t8, '平地', None, None) == _blend_yday(t9, '平地', None, None),
   "同樣的模式值、不同的觀測 → 融合值必須相同")

print("\n=== ⑥ 端到端：成員系統性偏多時，校正後的融合是否贏過最佳單模式 ===")
random.seed(11)
CORE = ['best','ecmwf','gfs','jma','aifs','graphcast']
BIASF = {'best':1.25,'ecmwf':1.35,'gfs':1.55,'jma':1.45,'aifs':1.70,'graphcast':1.60}
# 上一輪誤差表：bias = Σ觀測/Σ模式 ＝ 1/偏多倍數
sk6 = {'平地': {m: {'decay': {'bias': round(1/BIASF[m], 3), 'n': 200}} for m in CORE}}
w6 = {'平地': {m: 1.0 for m in CORE}}
ae_b = 0.0; ae_s = {m: 0.0 for m in CORE}; N = 40000
for _ in range(N):
    r = random.random()
    o = 0.0 if r < 0.62 else (0.5 + random.random()*11.5 if r < 0.90
         else (12 + random.random()*48 if r < 0.985 else 60 + random.random()*190))
    mv = {m: max(0.0, o*BIASF[m]*math.exp(random.gauss(0, 0.45))
                      + random.gauss(0, 2.0)) for m in CORE}
    t = {'model_yday': mv}
    b = _blend_yday(t, '平地', w6, sk6) or 0.0
    ae_b += abs(b - o)
    for m in CORE: ae_s[m] += abs(mv[m] - o)
mae_b = ae_b / N
best_m = min(ae_s, key=lambda m: ae_s[m]); mae_s = ae_s[best_m] / N
# 對照組：關掉偏差校正（＝修正前的做法）
ae_r = 0.0
random.seed(11)
for _ in range(N):
    r = random.random()
    o = 0.0 if r < 0.62 else (0.5 + random.random()*11.5 if r < 0.90
         else (12 + random.random()*48 if r < 0.985 else 60 + random.random()*190))
    mv = {m: max(0.0, o*BIASF[m]*math.exp(random.gauss(0, 0.45))
                      + random.gauss(0, 2.0)) for m in CORE}
    ae_r += abs(sum(mv.values())/len(mv) - o)
mae_r = ae_r / N
print(f"  校正後融合 MAE {mae_b:.2f}　最佳單模式 {best_m} {mae_s:.2f}　"
      f"未校正的等權平均 {mae_r:.2f}")
ok(mae_b < mae_s, f"融合贏過最佳單模式（誤差少 {(1-mae_b/mae_s)*100:.0f}%）")
ok(mae_b < mae_r, f"偏差校正確實有效（比未校正少 {(1-mae_b/mae_r)*100:.0f}%）")

print("\n全部通過" if not fails else f"\n失敗 {len(fails)} 項：{fails}")
raise SystemExit(1 if fails else 0)
