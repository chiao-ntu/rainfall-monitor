#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證 rain_hourly.json 滾動序列：不覆寫、修剪、缺格偵測、雙來源失敗時不寫壞。"""
import os, json, tempfile, shutil
from datetime import datetime, timedelta
os.environ.setdefault('CWA_API_KEY', 'dummy')
import fetch_qpesums_hourly as H

tmp = tempfile.mkdtemp()
os.chdir(tmp)
H.HOURLY_FILE = "rain_hourly.json"

fails = []
def chk(label, got, exp):
    ok = got == exp
    if not ok: fails.append(label)
    print(f"  {'OK ' if ok else '!! '}{label}: {got!r}" + ("" if ok else f"  期望 {exp!r}"))

def fake(cwa=None, swcb=None):
    H.fetch_cwa_hourly  = lambda: (cwa  if cwa  is not None else {"六龜": {"r1": 12.5}})
    H.fetch_swcb_hourly = lambda: (swcb if swcb is not None else {"六龜": 431.2})

def load(): return json.load(open(H.HOURLY_FILE, encoding='utf-8'))

T0 = datetime(2026, 8, 1, 5, 12)

print("=== 首次寫入 ===")
fake(); H.update_hourly_series(T0)
d = load()
chk("hours 長度", len(d['hours']), 1)
chk("時戳格式", d['hours'][0], "2026-08-01T05")
chk("cwa 值", d['cwa']['2026-08-01T05']['六龜'], 12.5)
chk("swcb 值", d['swcb']['2026-08-01T05']['六龜'], 431.2)
chk("缺格數", d['gap_count'], 0)

print("\n=== 同小時重跑：不得覆寫 ===")
fake(cwa={"六龜": {"r1": 999.0}}, swcb={"六龜": 999.0})
H.update_hourly_series(T0.replace(minute=48))
d = load()
chk("cwa 值仍為原值", d['cwa']['2026-08-01T05']['六龜'], 12.5)
chk("hours 仍為 1 筆", len(d['hours']), 1)

print("\n=== 連續累積 6 小時 ===")
for i in range(1, 7):
    fake(cwa={"六龜": {"r1": float(i)}}, swcb={"六龜": 431.2 + i})
    H.update_hourly_series(T0 + timedelta(hours=i))
d = load()
chk("hours 長度", len(d['hours']), 7)
chk("升冪排序", d['hours'] == sorted(d['hours']), True)
chk("最後一筆時雨量", d['cwa']['2026-08-01T11']['六龜'], 6.0)
chk("缺格數", d['gap_count'], 0)

print("\n=== 跳過 3 小時（模擬 Actions 漏跑）→ 必須偵測到缺格 ===")
fake(cwa={"六龜": {"r1": 7.0}}, swcb={"六龜": 440.0})
H.update_hourly_series(T0 + timedelta(hours=10))
d = load()
chk("缺格數 = 3", d['gap_count'], 3)
chk("缺格清單", d['gaps'], ["2026-08-01T12", "2026-08-01T13", "2026-08-01T14"])
chk("hours 只有實際寫入的 8 筆", len(d['hours']), 8)

print("\n=== 兩來源同時失敗 → 不寫入、不破壞既有序列 ===")
before = load()
fake(cwa={}, swcb={})
H.update_hourly_series(T0 + timedelta(hours=11))
after = load()
chk("hours 未變", after['hours'], before['hours'])
chk("cwa 未變", after['cwa'], before['cwa'])

print("\n=== 單一來源失敗 → 仍寫入（另一來源有值）===")
fake(cwa={}, swcb={"六龜": 450.0})
H.update_hourly_series(T0 + timedelta(hours=12))
d = load()
chk("該小時已寫入", "2026-08-01T17" in d['hours'], True)
chk("cwa 該小時為空 dict", d['cwa']['2026-08-01T17'], {})
chk("swcb 該小時有值", d['swcb']['2026-08-01T17']['六龜'], 450.0)

print("\n=== 168h 修剪 ===")
H.KEEP_SERIES_HOURS = 168
far = T0 + timedelta(hours=200)
fake(); H.update_hourly_series(far)
d = load()
cutoff = (far - timedelta(hours=168)).strftime('%Y-%m-%dT%H')
chk("所有時戳都 >= cutoff", all(h >= cutoff for h in d['hours']), True)
chk("舊序列已清除（只剩最新一筆）", len(d['hours']), 1)
chk("cwa 桶同步修剪", set(d['cwa']) == set(d['hours']), True)
chk("swcb 桶同步修剪", set(d['swcb']) == set(d['hours']), True)

print("\n=== 壞檔容錯 ===")
open(H.HOURLY_FILE, 'w').write("{壞掉的 json")
fake(); H.update_hourly_series(T0)
d = load()
chk("壞檔後重建成功", len(d['hours']), 1)

print("\n=== _stn_key 正規化 ===")
for src, exp in [("太平山(1)w", "太平山"), ("知本(5)", "知本"), ("太麻里 2", "太麻里"),
                 ("高坡國小s", "高坡國小"), ("蘇樂S", "蘇樂"), ("茂林國小ｓ", "茂林國小"),
                 ("六龜", "六龜")]:
    chk(f"{src}", H._stn_key(src), exp)

shutil.rmtree(tmp, ignore_errors=True)
print("\n全部通過" if not fails else f"\n失敗 {len(fails)} 項：{fails}")
raise SystemExit(1 if fails else 0)
