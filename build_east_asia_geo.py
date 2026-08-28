#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 Natural Earth 產生東亞國界／省界精簡圖資，供前端內嵌。

資料來源：Natural Earth（公眾領域，可自由使用）
  國界：ne_110m_admin_0_countries      —— 輪廓用途，110m 足夠
  省界：ne_10m_admin_1_states_provinces —— 50m 以下不含日韓菲，必須用 10m

處理：
  1. 只保留與東亞範圍相交的 feature（預設 100–150E、5–50N）
  2. Douglas-Peucker 簡化（防災圖臺只需輪廓，不需海岸細節）
  3. 座標取小數 2 位（約 1km，對本用途足夠）
  4. 丟棄簡化後過小的環（離島碎點會讓檔案暴增卻看不見）

輸出：east_asia_geo.json  {countries:[...], provinces:[...]}
      每筆 {name, name_zht, admin, rings:[[[lat,lng],...]]}
"""
import io, json, math, os, sys, urllib.request

BASE = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/'
COUNTRY_SRC = 'ne_110m_admin_0_countries.geojson'
STATE_SRC = 'ne_10m_admin_1_states_provinces.geojson'
OUT = 'east_asia_geo.json'

# 東亞範圍（含日本本州以北、菲律賓南端、中國東半部）
LON0, LON1, LAT0, LAT1 = 100.0, 150.0, 5.0, 50.0
# 只保留這些國家的省界（其餘國家只畫國界，避免檔案過大）
PROV_ADMINS = {'China', 'Japan', 'South Korea', 'North Korea', 'Philippines',
               'Vietnam', 'Taiwan'}
# ★ 容差與精度直接決定線條是否平滑。
#   初版用 0.05/0.03 度容差 + 小數 2 位（約 1km），線條呈現明顯稜角，
#   與臺灣圖資（小數 11 位、平均 35 點/環）的平滑度落差很大。
#   改為 0.008/0.005 度容差 + 小數 4 位（約 10m），視覺上即與臺灣一致。
TOL_COUNTRY = 0.008     # 國界簡化容差（度）
TOL_PROV = 0.005        # 省界稍細
COORD_DP = 4            # 座標小數位數（4 位≈10m，肉眼已無鋸齒）
MIN_RING_PTS = 4        # 簡化後少於此點數的環直接丟棄
MIN_RING_SPAN = 0.08    # 環的對角跨距小於此值視為碎點，丟棄


def fetch(name):
    local = name.replace('.geojson', '.json')
    if os.path.exists(local):
        return json.load(io.open(local, encoding='utf-8'))
    print(f'  下載 {name} …')
    data = urllib.request.urlopen(BASE + name, timeout=180).read()
    io.open(local, 'wb').write(data)
    return json.loads(data)


def rdp(pts, tol):
    """Douglas-Peucker 簡化。pts 為 [(lng,lat), ...]。"""
    if len(pts) < 3:
        return pts
    x0, y0 = pts[0]
    x1, y1 = pts[-1]
    dx, dy = x1 - x0, y1 - y0
    den = math.hypot(dx, dy)
    imax, dmax = 0, -1.0
    for i in range(1, len(pts) - 1):
        px, py = pts[i]
        if den == 0:
            d = math.hypot(px - x0, py - y0)
        else:
            d = abs(dy * px - dx * py + x1 * y0 - y1 * x0) / den
        if d > dmax:
            imax, dmax = i, d
    if dmax > tol:
        left = rdp(pts[:imax + 1], tol)
        right = rdp(pts[imax:], tol)
        return left[:-1] + right
    return [pts[0], pts[-1]]


def rings_of(geom):
    t = geom.get('type')
    if t == 'Polygon':
        return [geom['coordinates'][0]]
    if t == 'MultiPolygon':
        return [poly[0] for poly in geom['coordinates'] if poly]
    return []


def in_range(ring):
    xs = [c[0] for c in ring]
    ys = [c[1] for c in ring]
    return (max(xs) >= LON0 and min(xs) <= LON1
            and max(ys) >= LAT0 and min(ys) <= LAT1)


def process(feat, tol):
    out = []
    for ring in rings_of(feat.get('geometry') or {}):
        if len(ring) < 3 or not in_range(ring):
            continue
        pts = [(float(c[0]), float(c[1])) for c in ring]
        simp = rdp(pts, tol)
        if len(simp) < MIN_RING_PTS:
            continue
        xs = [p[0] for p in simp]
        ys = [p[1] for p in simp]
        if math.hypot(max(xs) - min(xs), max(ys) - min(ys)) < MIN_RING_SPAN:
            continue
        # 前端用 [lat, lng]
        out.append([[round(p[1], COORD_DP), round(p[0], COORD_DP)] for p in simp])
    return out


def main():
    print('東亞邊界圖資產生')
    countries, provinces = [], []

    cj = fetch(COUNTRY_SRC)
    for f in cj['features']:
        p = f.get('properties', {})
        rings = process(f, TOL_COUNTRY)
        if not rings:
            continue
        countries.append({
            'name': p.get('NAME') or '',
            'name_zht': p.get('NAME_ZHT') or p.get('NAME_ZH') or '',
            'rings': rings,
        })
    print(f'  國界：{len(countries)} 國、'
          f'{sum(len(c["rings"]) for c in countries)} 環')

    sj = fetch(STATE_SRC)
    for f in sj['features']:
        p = f.get('properties', {})
        admin = p.get('admin') or ''
        if admin not in PROV_ADMINS:
            continue
        rings = process(f, TOL_PROV)
        if not rings:
            continue
        provinces.append({
            'name': p.get('name') or '',
            'name_zht': p.get('name_zht') or p.get('name_zh') or '',
            'admin': admin,
            'rings': rings,
        })
    print(f'  省界：{len(provinces)} 省、'
          f'{sum(len(x["rings"]) for x in provinces)} 環')

    # ★ 不輸出國界：110m 版精度遠低於 10m 省界，並陳時國界呈現明顯折線
    #   （橫穿臺灣海峽的粗黑線），視覺突兀且無助判讀。省界已含各國海岸輪廓。
    payload = {'_src': 'Natural Earth (public domain)',
               '_note': '僅省界／縣界；國界已移除（110m 精度過低）',
               'provinces': provinces}
    txt = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    io.open(OUT, 'w', encoding='utf-8').write(txt)
    print(f'  已寫出 {OUT}：{len(txt)//1024} KB')


if __name__ == '__main__':
    main()
