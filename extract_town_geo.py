#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
從 rainfall-monitor 的 index.html 抽出 TOWN_GEO（368 鄉鎮座標）
────────────────────────────────────────────────
用途：weather_bot 計算 ETR2% 未來時段時，需要鄉鎮經緯度去查 Open-Meteo。

執行方式（在能連 GitHub 的環境）：
    python3 extract_town_geo.py

產出：town_geo.json
      格式 {"縣市|鄉鎮": {"lat": 24.123, "lon": 121.456}, ...}

完成後把 town_geo.json 上傳到對話。
"""
import json
import re
import sys
import requests

RAW_URL = "https://raw.githubusercontent.com/chiao-ntu/rainfall-monitor/main/index.html"

print("=" * 70)
print("抓取 rainfall-monitor/index.html")
print("=" * 70)

try:
    r = requests.get(RAW_URL, timeout=120)
    print(f"HTTP {r.status_code}, {len(r.content)} bytes")
    if r.status_code != 200:
        print("抓取失敗")
        sys.exit(1)
    html = r.content.decode("utf-8", "replace")
except Exception as e:
    print(f"抓取失敗：{e}")
    sys.exit(1)

# ── 找 TOWN_GEO 的定義 ──
print("\n搜尋 TOWN_GEO 定義…")

# 常見寫法：const TOWN_GEO = {...};  或  const TOWN_GEO={...};
patterns = [
    r"(?:const|let|var)\s+TOWN_GEO\s*=\s*(\{.*?\});",
    r"TOWN_GEO\s*[:=]\s*(\{.*?\})\s*[,;]",
]

raw_obj = None
for pat in patterns:
    m = re.search(pat, html, re.DOTALL)
    if m:
        raw_obj = m.group(1)
        print(f"  找到（用 pattern: {pat[:40]}…），長度 {len(raw_obj)} 字元")
        break

if not raw_obj:
    print("  ✗ 找不到 TOWN_GEO")
    print("\n  嘗試搜尋所有可能的變數名…")
    candidates = re.findall(r"(?:const|let|var)\s+(\w*(?:GEO|geo|TOWN|town)\w*)\s*=", html)
    print(f"  含 GEO/TOWN 的變數：{sorted(set(candidates))[:20]}")
    sys.exit(1)

# ── 解析成 Python dict ──
print("\n解析 JSON…")
try:
    geo = json.loads(raw_obj)
except json.JSONDecodeError as e:
    print(f"  直接解析失敗（{e}），嘗試修正 JS 物件格式…")
    # JS 物件的 key 可能沒有引號，或用單引號
    fixed = raw_obj
    fixed = re.sub(r"'", '"', fixed)                       # 單引號 → 雙引號
    fixed = re.sub(r"(\{|,)\s*([A-Za-z_$][\w$]*)\s*:", r'\1"\2":', fixed)  # 無引號 key
    fixed = re.sub(r",\s*(\}|\])", r"\1", fixed)           # 移除尾逗號
    try:
        geo = json.loads(fixed)
        print("  修正後解析成功")
    except Exception as e2:
        print(f"  ✗ 仍失敗：{e2}")
        print(f"  前 500 字元供檢查：\n{raw_obj[:500]}")
        sys.exit(1)

print(f"  共 {len(geo)} 筆")

# ── 檢視結構 ──
print("\n前 5 筆內容：")
for i, (k, v) in enumerate(geo.items()):
    if i >= 5:
        break
    print(f"  {k!r} → {json.dumps(v, ensure_ascii=False)}")

# ── 標準化輸出格式 ──
print("\n標準化為 {縣市|鄉鎮: {lat, lon}}…")
out = {}
skipped = []

for key, val in geo.items():
    lat = lon = None
    if isinstance(val, dict):
        # 可能的欄位名
        for lat_k in ("lat", "latitude", "Lat", "Latitude", "y"):
            if lat_k in val:
                lat = val[lat_k]
                break
        for lon_k in ("lon", "lng", "longitude", "Lon", "Lng", "Longitude", "x"):
            if lon_k in val:
                lon = val[lon_k]
                break
    elif isinstance(val, (list, tuple)) and len(val) >= 2:
        # 可能是 [lat, lon] 或 [lon, lat]
        a, b = float(val[0]), float(val[1])
        # 台灣：lat 約 21.9-25.3，lon 約 119.3-122.0
        if 21 <= a <= 26 and 119 <= b <= 123:
            lat, lon = a, b
        elif 119 <= a <= 123 and 21 <= b <= 26:
            lat, lon = b, a

    if lat is None or lon is None:
        skipped.append(key)
        continue

    try:
        out[key] = {"lat": round(float(lat), 6), "lon": round(float(lon), 6)}
    except Exception:
        skipped.append(key)

print(f"  成功轉換 {len(out)} 筆")
if skipped:
    print(f"  ⚠ 無法解析 {len(skipped)} 筆：{skipped[:10]}")

# ── 座標合理性檢查 ──
bad = [k for k, v in out.items()
       if not (21 <= v["lat"] <= 26.5 and 118 <= v["lon"] <= 123)]
if bad:
    print(f"  ⚠ {len(bad)} 筆座標超出台灣範圍：{bad[:10]}")
else:
    print("  ✓ 所有座標都在台灣範圍內")

# ── 寫出 ──
with open("town_geo.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)

print(f"\n✅ 已寫出 town_geo.json（{len(out)} 筆）")
print("請把 town_geo.json 上傳到對話。")
