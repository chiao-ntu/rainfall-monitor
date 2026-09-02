#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 20m DTM 產生「測站代碼 → 海拔」對照表。

用途：
  ○ 前端「海拔 vs 雨量」散佈圖（觀察地形雨效應）
  ○ 測站底圖著色時標示高程

為何離線產生：DTM 全臺檔 757MB，不適合每次排程解壓；測站位置也極少變動。
  行政區或測站有增修時重跑一次即可。

用法：
  python3 build_station_elev.py <測站清單.json 或 data.json>
  測站清單需含 {sid: {lat, lng}} 或 data.json 的 townships[].stations[]
輸出：station_elev.json  {"C0H9A0": 1743.2, ...}
"""
import json, io, os, sys

DEM = 'dem/DEM_tawiwan_V2025.tif'
ISLAND_DEM = [('dem_澎湖/DEM_Penghu_V2025.tif', ('澎湖',)),
              ('dem_金門/DEM_KinMen_V2025.tif', ('金門',))]
OUT = 'station_elev.json'


def load_points(path):
    """從 data.json 或簡易清單取出 {sid: (lat, lng)}。"""
    with io.open(path, encoding='utf-8') as f:
        d = json.load(f)
    pts = {}
    if isinstance(d, dict) and 'townships' in d:
        for t in d['townships']:
            for st in (t.get('stations') or []):
                sid = st.get('sid') or ''
                la, lo = st.get('lat'), st.get('lng')
                if sid and la and lo:
                    pts[sid] = (float(la), float(lo))
    elif isinstance(d, dict):
        for sid, v in d.items():
            la = v.get('lat') if isinstance(v, dict) else None
            lo = v.get('lng') if isinstance(v, dict) else None
            if la and lo:
                pts[sid] = (float(la), float(lo))
    return pts


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    src = sys.argv[1]
    if not os.path.exists(src):
        print(f'找不到 {src}')
        return 1
    pts = load_points(src)
    print(f'待查測站：{len(pts)} 站')
    if not pts:
        print('※ 來源檔沒有測站座標 —— 需先部署含 sid/lat/lng 的 fetch_rainfall.py')
        return 1

    import rasterio
    from pyproj import Transformer

    out = {}
    readers = []
    for path in [DEM] + [p for p, _ in ISLAND_DEM]:
        if os.path.exists(path):
            src_r = rasterio.open(path)
            readers.append((src_r,
                            Transformer.from_crs('EPSG:4326', src_r.crs,
                                                 always_xy=True)))
    if not readers:
        print(f'找不到 DTM（預期 {DEM}）')
        return 1

    for sid, (la, lo) in pts.items():
        for r, tr in readers:
            try:
                x, y = tr.transform(lo, la)
                v = next(r.sample([(x, y)]))[0]
                if v is not None and v > -1000:
                    out[sid] = round(float(v), 1)
                    break
            except Exception:
                continue

    json.dump(out, io.open(OUT, 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    vals = sorted(out.values())
    print(f'完成 {len(out)}/{len(pts)} 站 → {OUT}')
    if vals:
        print(f'  海拔範圍 {vals[0]:.0f} ~ {vals[-1]:.0f} m'
              f'（中位數 {vals[len(vals)//2]:.0f} m）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
