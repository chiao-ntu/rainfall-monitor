#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨日定版：驗證「當天最後一輪跑太早」不會讓該日觀測永遠低估。

重現使用者回報的情境：10/1 傍晚起下大雨，但當天最後一次成功執行是 19 時，
該日只累積到 19mm；跨日後官方統計是 100mm。
"""
import os, json, tempfile, shutil
os.environ.setdefault('CWA_API_KEY', 'dummy')
from datetime import datetime, timedelta
import fetch_rainfall as F

fails = []
def ok(c, m):
    print(('OK  ' if c else '!!  ') + m)
    if not c: fails.append(m)

tmp = tempfile.mkdtemp()
cwd = os.getcwd()
os.chdir(tmp)
F.HISTORY_FILE = os.path.join(tmp, 'obs_history.json')

SID = 'C0R160'          # 假測站
def stations(now_acc, r24, r2d=0.0, r3d=0.0):
    return {SID: {'county': '屏東縣', 'township': '泰武鄉',
                  'rain_now': now_acc, 'rain_24h': r24,
                  'rain_2d': r2d, 'rain_3d': r3d}}

print("=== 情境：10/1 最後一輪在 19 時，只看到 19mm ===")
t19 = datetime(2026, 10, 1, 19, 5)
F.update_history(stations(19.0, 19.0), t19)
h = json.load(open(F.HISTORY_FILE))
ok(h[SID]['2026-10-01'] == 19.0, f"10/1 當天記錄 = {h[SID]['2026-10-01']}mm（中途值）")

print("\n=== 跨日第一輪（10/2 00:05）：rain_24h 已涵蓋整個 10/1 ===")
t0005 = datetime(2026, 10, 2, 0, 5)
F.update_history(stations(0.0, 100.0), t0005)      # 今天剛開始、近24h=100
h = json.load(open(F.HISTORY_FILE))
ok(h[SID]['2026-10-01'] == 100.0,
   f"10/1 已定版為 {h[SID]['2026-10-01']}mm（不再是 19mm）")
ok(h[SID]['2026-10-02'] == 0.0, f"10/2 當天 = {h[SID]['2026-10-02']}mm（剛過午夜）")
ok(h[SID].get('FINAL') == '2026-10-01', "已標記定版")

print("\n=== 定版後不得再被後續執行改動 ===")
t0105 = datetime(2026, 10, 2, 1, 5)
F.update_history(stations(3.0, 60.0), t0105)       # 近24h 已開始滑出 10/1
h = json.load(open(F.HISTORY_FILE))
ok(h[SID]['2026-10-01'] == 100.0,
   f"01:05 再跑一輪，10/1 維持 {h[SID]['2026-10-01']}mm（未被較小的 rain_24h 蓋掉）")

print("\n=== 定版只取 max，既有完整值不會被改小 ===")
shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp, exist_ok=True)
F.update_history(stations(250.0, 250.0), datetime(2026, 10, 1, 23, 5))
F.update_history(stations(0.0, 180.0), datetime(2026, 10, 2, 0, 5))
h = json.load(open(F.HISTORY_FILE))
ok(h[SID]['2026-10-01'] == 250.0,
   f"23 時已記錄 250mm，跨日 rain_24h 只有 180 → 維持 {h[SID]['2026-10-01']}mm")

print("\n=== 超過時限（04 時以後）不再定版，避免 rain_24h 視窗偏離太多 ===")
shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp, exist_ok=True)
F.update_history(stations(19.0, 19.0), datetime(2026, 10, 1, 19, 5))
F.update_history(stations(5.0, 40.0), datetime(2026, 10, 2, 8, 5))
h = json.load(open(F.HISTORY_FILE))
ok(h[SID]['2026-10-01'] == 19.0,
   f"08 時執行 → 不動 10/1（維持 {h[SID]['2026-10-01']}mm），不以偏離的視窗覆寫")

print("\n=== 定版標記：跨多輪與 16 天修剪後都要還在，且不得被當成日雨量 ===")
shutil.rmtree(tmp, ignore_errors=True); os.makedirs(tmp, exist_ok=True)
F.update_history(stations(19.0, 19.0), datetime(2026, 10, 1, 19, 5))
F.update_history(stations(0.0, 100.0), datetime(2026, 10, 2, 0, 5))   # 定版
F.update_history(stations(8.0, 55.0), datetime(2026, 10, 2, 9, 5))    # 之後再跑幾輪
F.update_history(stations(12.0, 40.0), datetime(2026, 10, 2, 15, 5))
h = json.load(open(F.HISTORY_FILE))
ok(h[SID].get('FINAL') == '2026-10-01', "多輪執行與修剪後，定版標記仍在")
ok(h[SID]['2026-10-01'] == 100.0, f"10/1 定版值未被後續執行改動（{h[SID]['2026-10-01']}mm）")
arr = F.get_daily_rain_array(SID, h, datetime(2026, 10, 2, 15, 5), days=5)
ok(all(isinstance(x, float) for x in arr), f"日雨量陣列全為數值、未混入標記：{arr}")

os.chdir(cwd); shutil.rmtree(tmp, ignore_errors=True)
print("\n全部通過" if not fails else f"\n失敗 {len(fails)} 項：{fails}")
raise SystemExit(1 if fails else 0)
