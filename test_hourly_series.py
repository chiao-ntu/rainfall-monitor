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

print('\n── ⑤ 官方公式補算（改用 obs_history 逐日觀測）──────────')
#  上一版的自算吃 rain_hourly 的逐時資料，需要 168h/每日 90% 覆蓋。
#  實跑序列只有 34h、缺格 130 —— 條件從未成立。改由逐日歷史算，
#  因為官方公式要的本來就是日雨量。
import fetch_rainfall as F
from datetime import datetime as _dt
W = F.ETR2_WEIGHTS
_h = {'S1': {'2026-10-08': 30.0, '2026-10-07': 10.0, '2026-10-06': 5.0,
             '2026-10-05': 0.0, '2026-10-04': 2.0, '2026-10-03': 0.0,
             '2026-10-02': 1.0}}
_d = [30.0, 10.0, 5.0, 0.0, 2.0, 0.0, 1.0]
_want = round(sum(W[i] * _d[i] for i in range(7)), 1)
_got, _ex, _ = F.calc_etr2_at('S1', _h, _dt(2026, 10, 9, 0))
ok(_got == _want and _ex, f'⑤段末落在日界時精確（{_got} vs 手算 {_want}）')

print('\n── ⑥ 當日量大又缺逐時 → 不得硬猜 ─────────────────────')
_g2, _e2, _w2 = F.calc_etr2_at('S1', _h, _dt(2026, 10, 8, 18))
ok(_g2 is None, f'⑥當日已累積 30mm 但段末非日界時不得補（實得 {_g2}）')
_h3 = dict(_h); _h3['S1'] = dict(_h['S1']); _h3['S1']['2026-10-08'] = 1.0
_g3, _e3, _w3 = F.calc_etr2_at('S1', _h3, _dt(2026, 10, 8, 18))
ok(_g3 is not None and not _e3,
   f'⑥當日量小（1mm）時可補、但標為非精確（實得 {_g3}）')

print('\n── ⑦ 缺日不得當成 0（會低估 ETR2）────────────────────')
_h4 = {'S1': {'2026-10-08': 1.0, '2026-10-07': 10.0, '2026-10-05': 0.0}}
_g4, _, _w4 = F.calc_etr2_at('S1', _h4, _dt(2026, 10, 8, 18))
ok(_g4 is None, f'⑦前期有缺日時不得補（缺日當 0 會低估，實得 {_g4}）')
ok('無日雨量' in (_w4 or ''), f'⑦拒補原因要說明是哪一天缺（實得 {_w4!r}）')

print('\n── ⑦b 結構保證：日雨量齊全就不可能有洞 ────────────────')
#  這一項不是測某個案例，是測性質：隨機挖空任意組合的段，
#  只要 obs_history 有該日與前 6 日的日雨量，封口後過去段一律不得為 None。
#  前幾輪每次只修「這次的成因」，下一個成因又讓線斷掉 —— 要擋的是那件事。
import random as _rnd
_rnd.seed(42)
_bad, _cases = [], 0
for _trial in range(300):
    _base = _dt(2026, 10, 8).date()
    _hist = {}
    for _k in range(8):
        _d = (_base - __import__('datetime').timedelta(days=_k)).strftime('%Y-%m-%d')
        _hist[_d] = round(_rnd.choice([0.0, 0.0, 2.5, 18.0, 90.0]), 1)
    _H = {'S1': _hist}
    _segs = ['2026-10-08T00', '2026-10-08T06', '2026-10-08T12', '2026-10-08T18']
    _tail = sum(W[k] * _hist[(_base - __import__('datetime').timedelta(days=k))
                             .strftime('%Y-%m-%d')] for k in range(1, 7))
    _tot = _hist[_base.strftime('%Y-%m-%d')]
    #  隨機讓 0~3 段有官方值（R0 必須單調遞增才合物理）
    _r0 = sorted(_rnd.uniform(0, _tot) for _ in range(4))
    _es = [None] * 4
    for _i in range(4):
        if _rnd.random() < 0.45:
            _es[_i] = round(_r0[_i] + _tail, 1)
    _out, _nf, _u = F.seal_etr2_series(list(_es), _segs, 'S1', _H, _dt(2026, 10, 9, 12))
    _cases += 1
    if any(v is None for v in _out):
        _bad.append((_es, _out))
    #  封口值必須落在當日的物理範圍內：tail ≤ 值 ≤ tail + 當日總量
    for _v in _out:
        if _v is not None and not (_tail - 0.2 <= _v <= _tail + _tot + 0.2):
            _bad.append(('out-of-range', _v, _tail, _tot))
ok(not _bad, f'⑦b {_cases} 組隨機情境全部封口成功且落在物理範圍內'
              + (f'（失敗例 {_bad[0]}）' if _bad else ''))

#  反向：日雨量缺一天就不得硬填（寧可留白也不猜）
_H2 = {'S1': {'2026-10-08': 10.0, '2026-10-07': 5.0, '2026-10-05': 1.0}}
_o2, _n2, _ = F.seal_etr2_series([None] * 4, ['2026-10-08T00', '2026-10-08T06',
                                 '2026-10-08T12', '2026-10-08T18'], 'S1', _H2,
                                 _dt(2026, 10, 9, 12))
ok(_n2 == 0 and all(v is None for v in _o2),
   f'⑦b 前期日雨量缺漏時不得封口（實得 {_o2}）')

#  未來段不得填
_o3, _n3, _ = F.seal_etr2_series([None] * 4, ['2026-10-08T00', '2026-10-08T06',
                                 '2026-10-08T12', '2026-10-08T18'], 'S1', _H,
                                 _dt(2026, 10, 8, 9))
ok(_o3[2] is None and _o3[3] is None, f'⑦b 未來段不得填（實得 {_o3}）')

print('\n── ⑧ ETR2 計算只留一份實作 ─────────────────────────────')
#  逐時版的自算已移除（168h 覆蓋條件在實際排程下從未成立）。
#  計算應該只存在於資料取得得到的那一端。
ok(not hasattr(Q, 'backfill_swcb_calc'),
   '⑧逐時版自算已移除（條件從未成立的實作不可留著）')
ok(hasattr(F, 'calc_etr2_at') and hasattr(F, 'etr2_from_daily'),
   '⑧補算實作位於 fetch_rainfall（與 obs_history 同一側）')
ok(F.ETR2_WEIGHTS == [1.0, 0.7, 0.5, 0.4, 0.3, 0.2, 0.1],
   f'⑧官方權重未被更動（{F.ETR2_WEIGHTS}）')

print()
if fails:
    print(f'失敗 {len(fails)} 項')
    for f in fails:
        print('  - ' + f)
    sys.exit(1)
print('全部通過（9 組情境）')
