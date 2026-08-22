#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
從 rainfall-monitor 的 index.html 抽出 TOWN_GEO，計算各鄉鎮代表點
────────────────────────────────────────────────
TOWN_GEO 實際上是 GeoJSON FeatureCollection（鄉鎮界多邊形），
不是現成的 lat/lon 對照表，所以要自己算中心點。

代表點計算方式：
  取該鄉鎮「面積最大的多邊形外環」，計算其面積加權形心。
  比單純平均頂點準確，不會被離島或狹長地形拉偏。

執行方式（GitHub Actions 或本機皆可）：
    pip install requests
    python3 extract_town_geo.py

產出：town_geo.json
      格式 {"縣市|鄉鎮": {"lat": 24.123456, "lon": 121.456789}, ...}
"""
import json
import re
import sys
from collections import Counter

import requests

RAW_URL = "https://raw.githubusercontent.com/chiao-ntu/rainfall-monitor/main/index.html"

print("=" * 70)
print("抓取 rainfall-monitor/index.html")
print("=" * 70)

try:
    r = requests.get(RAW_URL, timeout=180)
    print(f"HTTP {r.status_code}, {len(r.content)} bytes")
    if r.status_code != 200:
        print("抓取失敗（非 200）")
        sys.exit(1)
    html = r.content.decode("utf-8", "replace")
except Exception as e:
    print(f"抓取失敗：{e}")
    sys.exit(1)

# ── 取出 TOWN_GEO ──
print("\n搜尋 TOWN_GEO…")
m = re.search(r"(?:const|let|var)\s+TOWN_GEO\s*=\s*(\{.*?\});", html, re.DOTALL)
if not m:
    print("  ✗ 找不到 TOWN_GEO")
    sys.exit(1)

try:
    geo = json.loads(m.group(1))
except Exception as e:
    print(f"  ✗ JSON 解析失敗：{e}")
    sys.exit(1)

features = geo.get("features", [])
print(f"  ✓ GeoJSON FeatureCollection，共 {len(features)} 個 feature")


# ── 多邊形形心計算 ──
def ring_area_centroid(ring):
    """
    計算單一環（closed ring）的面積與形心。
    ring: [[lon, lat], [lon, lat], ...]
    回傳 (abs_area, cx, cy)。退化情形回退為頂點平均。
    """
    n = len(ring)
    if n < 3:
        if n == 0:
            return 0.0, None, None
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return 0.0, sum(xs) / n, sum(ys) / n

    a2 = 0.0   # 2 * signed area
    cx = 0.0
    cy = 0.0
    for i in range(n):
        x0, y0 = ring[i][0], ring[i][1]
        x1, y1 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        cross = x0 * y1 - x1 * y0
        a2 += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross

    if abs(a2) < 1e-14:            # 面積趨近 0（退化多邊形）
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        return 0.0, sum(xs) / n, sum(ys) / n

    area = a2 / 2.0
    cx = cx / (3.0 * a2)
    cy = cy / (3.0 * a2)
    return abs(area), cx, cy


def largest_ring_centroid(geometry):
    """
    從 Polygon / MultiPolygon 取面積最大的外環形心，回傳 (lon, lat)。
    只取每個 polygon 的第 0 個 ring（外環），忽略內部孔洞。
    """
    if not geometry:
        return None, None

    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return None, None

    if gtype == "Polygon":
        polys = [coords]
    elif gtype == "MultiPolygon":
        polys = coords
    else:
        return None, None

    best_area = -1.0
    best_cx = best_cy = None
    for poly in polys:
        if not poly:
            continue
        outer = poly[0]
        area, cx, cy = ring_area_centroid(outer)
        if cx is None:
            continue
        if area > best_area:
            best_area, best_cx, best_cy = area, cx, cy

    return best_cx, best_cy


# ── 逐 feature 計算 ──
print("\n計算各鄉鎮代表點…")
out = {}
skipped = []

for feat in features:
    props = feat.get("properties") or {}
    county = (props.get("COUNTYNAME") or "").strip()
    town = (props.get("TOWNNAME") or "").strip()
    if not county or not town:
        skipped.append(str(props)[:60])
        continue

    lon, lat = largest_ring_centroid(feat.get("geometry"))
    if lon is None or lat is None:
        skipped.append(f"{county}{town}（幾何無法計算）")
        continue

    out[f"{county}|{town}"] = {
        "lat": round(float(lat), 6),
        "lon": round(float(lon), 6),
    }

print(f"  成功 {len(out)} 個鄉鎮")
if skipped:
    print(f"  ⚠ 略過 {len(skipped)} 筆：{skipped[:5]}")

# ── 座標合理性檢查（含離島） ──
bad = [k for k, v in out.items()
       if not (21.0 <= v["lat"] <= 26.5 and 118.0 <= v["lon"] <= 123.0)]
if bad:
    print(f"\n  ⚠ {len(bad)} 筆座標超出台灣範圍：")
    for k in bad[:10]:
        print(f"      {k} → {out[k]}")
else:
    print("\n  ✓ 所有座標都在台灣範圍內（含離島）")

# ── 抽樣檢查 ──
print("\n抽樣檢查（請確認與實際位置相符）：")
for probe in ["高雄市|六龜區", "屏東縣|三地門鄉", "屏東縣|獅子鄉",
              "南投縣|信義鄉", "花蓮縣|秀林鄉", "宜蘭縣|蘇澳鎮"]:
    if probe in out:
        v = out[probe]
        print(f"  {probe:<16} lat={v['lat']:.5f}  lon={v['lon']:.5f}")
    else:
        print(f"  {probe:<16} （不在資料中）")

# ── 縣市統計 ──
cnt = Counter(k.split("|")[0] for k in out)
print(f"\n縣市統計（{len(cnt)} 個縣市）：")
for county, n in sorted(cnt.items(), key=lambda x: -x[1]):
    print(f"  {county:<8} {n} 個鄉鎮")

# ── 寫出 ──
with open("town_geo.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1, sort_keys=True)

print(f"\n✅ 已寫出 town_geo.json（{len(out)} 個鄉鎮）")
print("請下載 artifact 後把 town_geo.json 上傳到對話。")
