# -*- coding: utf-8 -*-
"""rain_hourly.json 滾動序列：部分來源失敗必須可自癒
---------------------------------------------------------------------
這支測試對應的是實際事故（2026-10-09 使用者回報）：
  圖上時雨量統計正常，但同一時段的 ETR2 完全空白。

根本原因在 fetch_qpesums_hourly.update_hourly_series()，兩層疊加：
  (1) `if not cwa and not swcb: return` —— 只有兩個來源都失敗才算失敗。
      水保署 API 單獨失敗時仍寫入 ser['swcb'][hour] = {}，
      把「抓不到」記錄成「這小時沒有 ETR2」。
  (2) `if hour_key in ser['hours']: 不覆寫` —— 整個小時跳過。
      腳本每 10 分鐘跑一次、一小時有 6 次機會，但第一次若水保署失敗，
      後面 5 次全部不再嘗試 → 那一小時永久沒有 ETR2。

另外驗證以官方公式自算 ETR2（Σ W[i]xR[i]）的正確性 ——
API 只給「現在」的值，過去的小時補抓不回來，只能由官方時雨量算出。
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

import fetch_qpesums_hourly as Q

fails = []


def ok(cond, msg):
    print(('OK  ' if cond else '!!  ') + msg)
    if not cond:
        fails.append(msg)


def run(now, cwa_ret, swcb_ret, path):
    """以指定的 API 回傳值跑一次 update_hourly_series"""
    Q.HOURLY_FILE = path
    Q.fetch_cwa_hourly = lambda: cwa_ret
    Q.fetch_swcb_hourly = lambda: swcb_ret
    Q.write_etr2_now = lambda swcb, n: None          # 本測試不涉及
    Q.update_hourly_series(now)
    if not os.path.exists(path):
        return None
    with open(path, encoding='utf-8') as f:
        return json.load(f)


tmp = tempfile.mkdtemp()
P = os.path.join(tmp, 'rain_hourly.json')
NOW = datetime(2026, 10, 8, 14, 5)
HK = '2026-10-08T14'
CWA_OK = {'寒溪': {'r1': 12.5}, '南山': {'r1': 3.0}}
SWCB_OK = {'寒溪s': 420.0, '南山': 310.0}

print('── ① CWA 成功、水保署失敗 ──────────────────────────────')
ser = run(NOW, CWA_OK, {}, P)
ok(ser is not None, '①有寫出檔案')
ok(HK in ser['hours'], '①該小時已記錄（時雨量有值）')
ok(ser['cwa'].get(HK), '①CWA 時雨量已寫入')
ok(HK not in ser['swcb'],
   f"①水保署失敗時**不得**寫入空字典（實得 {ser['swcb'].get(HK)!r}）")

print('\n── ② 同一小時再跑：水保署恢復，必須補寫 ────────────────')
ser = run(NOW + timedelta(minutes=10), {}, SWCB_OK, P)
ok(ser['swcb'].get(HK) == SWCB_OK,
   f"②同小時後續執行必須補上水保署值（實得 {ser['swcb'].get(HK)!r}）")
ok(ser['cwa'].get(HK) == {'寒溪': 12.5, '南山': 3.0},
   '②已存在的 CWA 值不得被覆寫')

print('\n── ③ 兩者都已存在：不得重抓 ────────────────────────────')
called = {'n': 0}


def _count_cwa():
    called['n'] += 1
    return CWA_OK


Q.HOURLY_FILE = P
Q.fetch_cwa_hourly = _count_cwa
Q.fetch_swcb_hourly = lambda: (called.__setitem__('n', called['n'] + 1), SWCB_OK)[1]
Q.update_hourly_series(NOW + timedelta(minutes=20))
ok(called['n'] == 0, f"③兩個來源都齊全時不得再呼叫 API（實得 {called['n']} 次）")

print('\n── ④ 兩者都失敗：不得留下空殼 ──────────────────────────')
P2 = os.path.join(tmp, 'b.json')
ser = run(datetime(2026, 10, 8, 15, 5), {}, {}, P2)
ok(ser is None or '2026-10-08T15' not in (ser or {}).get('hours', []),
   '④兩個來源都失敗時，該小時不得被記錄為已有資料')

print('\n── ⑤ 以官方公式自算 ETR2 ───────────────────────────────')
# 造 7 天完整時雨量：第 i 天每小時 rain_per_h[i] mm
P3 = os.path.join(tmp, 'c.json')
H = datetime(2026, 10, 8, 23)          # 取整日，R0 覆蓋 00~23 共 24 小時
rain_per_h = [1.0, 2.0, 0.0, 0.5, 0.0, 3.0, 1.0]     # 第 0~6 天（0＝當日）
cwa_block = {}
for i, rph in enumerate(rain_per_h):
    d = (H - timedelta(days=i)).date()
    n = 24
    for h in range(n):
        hk = datetime(d.year, d.month, d.day, h).strftime('%Y-%m-%dT%H')
        cwa_block[hk] = {'寒溪': rph}
ser3 = {'hours': sorted(cwa_block.keys()), 'cwa': cwa_block, 'swcb': {}}
Q.backfill_swcb_calc(ser3, H)
got = (ser3.get('swcb_calc') or {}).get(H.strftime('%Y-%m-%dT%H'), {}).get('寒溪')
want = round(sum(Q.ETR2_WEIGHTS[i] * rain_per_h[i] * 24 for i in range(7)), 1)
ok(got is not None, '⑤自算值有算出來')
ok(got is not None and abs(got - want) < 0.05,
   f'⑤自算 ETR2 = {got}（手算 Σ W[i]x{24}x日雨量 = {want}）')

print('\n── ⑥ 覆蓋不足不得以 0 充數 ─────────────────────────────')
# 把第 3 天挖掉一半小時 → 覆蓋率 50% < 90% → 必須不算
d3 = (H - timedelta(days=3)).date()
for h in range(12):
    cwa_block.pop(datetime(d3.year, d3.month, d3.day, h).strftime('%Y-%m-%dT%H'), None)
ser4 = {'hours': sorted(cwa_block.keys()), 'cwa': cwa_block, 'swcb': {}}
Q.backfill_swcb_calc(ser4, H)
got4 = (ser4.get('swcb_calc') or {}).get(H.strftime('%Y-%m-%dT%H'), {}).get('寒溪')
ok(got4 is None,
   f'⑥某一日時雨量覆蓋不足時不得自算（缺的小時若當成 0 會低估，實得 {got4}）')

print('\n── ⑦ 官方值存在的小時不得被自算值覆蓋 ──────────────────')
ser5 = {'hours': [H.strftime('%Y-%m-%dT%H')], 'cwa': cwa_block,
        'swcb': {H.strftime('%Y-%m-%dT%H'): {'寒溪': 999.0}}}
Q.backfill_swcb_calc(ser5, H)
ok(ser5['swcb'][H.strftime('%Y-%m-%dT%H')]['寒溪'] == 999.0,
   '⑦官方 API 值原封不動')
ok(not (ser5.get('swcb_calc') or {}).get(H.strftime('%Y-%m-%dT%H')),
   '⑦官方值已存在的小時不另外自算')

print('\n── ⑧ 權重必須與 fetch_rainfall.py 同一組 ───────────────')
import fetch_rainfall as F
ok(Q.ETR2_WEIGHTS == F.ETR2_WEIGHTS,
   f'⑧兩支腳本的 ETR2 權重一致（{Q.ETR2_WEIGHTS} vs {F.ETR2_WEIGHTS}）')

print()
if fails:
    print(f'失敗 {len(fails)} 項')
    for f in fails:
        print('  - ' + f)
    sys.exit(1)
print('全部通過（8 組情境）')
