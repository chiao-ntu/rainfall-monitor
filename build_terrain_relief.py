#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 20m DTM 產生「地形暈渲圖」PNG，供前端疊在地圖最下層。

做法（依使用者指定）：
  1. 海拔分級灰階（中央氣象署風格）：
       0-100m #F8F9FA ／ 100-500m #E9ECEF ／ 500-1500m #CED4DA
       1500-3000m #ADB5BD ／ >3000m #6C757D
  2. Hillshade：光源方位 315°（西北）、仰角 45°
  3. 兩者以「正片疊底」（Multiply）合成 —— 灰階提供高度資訊，
     暈渲提供立體感，相乘後山脊與谷地的紋理才會浮現。

★ 為何離線產生：DEM 全臺檔 757MB，不可能每次載入時運算；
  地形也幾乎不變，產生一次即可。

輸出：terrain_relief.png（含 alpha）＋ terrain_relief.json（地理範圍）
用法：python3 build_terrain_relief.py [目標寬度，預設 1200]
"""
import io, json, math, os, sys

DEM = 'dem/DEM_tawiwan_V2025.tif'
OUT_PNG = 'terrain_relief.png'
OUT_META = 'terrain_relief.json'

# 海拔分級灰階（上界, RGB）
ELEV_STOPS = [
    (100,  (0xF8, 0xF9, 0xFA)),
    (500,  (0xE9, 0xEC, 0xEF)),
    (1500, (0xCE, 0xD4, 0xDA)),
    (3000, (0xAD, 0xB5, 0xBD)),
    (1e9,  (0x6C, 0x75, 0x7D)),
]
AZIMUTH = 315.0     # 光源方位（西北）
ALTITUDE = 45.0     # 光源仰角


def _fill_inland_voids(dem, max_iter=60):
    """把被陸地包圍的缺值以鄰域平均逐步補起來（海域缺值保持 NaN）。

    做法：先用洪水填充從影像邊界標出「外部」（海域），其餘 NaN 即為內陸空洞；
    再對內陸空洞反覆取 3×3 鄰域平均，直到補滿或達迭代上限。
    """
    import numpy as np
    from collections import deque
    nan = ~np.isfinite(dem)
    H, W = dem.shape
    outside = np.zeros((H, W), dtype=bool)
    dq = deque()
    for x in range(W):
        for y in (0, H - 1):
            if nan[y, x] and not outside[y, x]:
                outside[y, x] = True; dq.append((y, x))
    for y in range(H):
        for x in (0, W - 1):
            if nan[y, x] and not outside[y, x]:
                outside[y, x] = True; dq.append((y, x))
    while dq:
        y, x = dq.popleft()
        for dy, dx in ((1,0), (-1,0), (0,1), (0,-1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < H and 0 <= nx < W and nan[ny, nx] and not outside[ny, nx]:
                outside[ny, nx] = True; dq.append((ny, nx))
    inland = nan & ~outside
    n0 = int(inland.sum())
    if not n0:
        return outside
    work = dem.copy()
    for _ in range(max_iter):
        todo = inland & ~np.isfinite(work)
        if not todo.any():
            break
        filled = np.where(np.isfinite(work), work, 0.0)
        cnt = np.isfinite(work).astype('float32')
        acc = np.zeros_like(filled); num = np.zeros_like(cnt)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dy == 0 and dx == 0: continue
                acc += np.roll(np.roll(filled, dy, 0), dx, 1)
                num += np.roll(np.roll(cnt, dy, 0), dx, 1)
        avg = np.where(num > 0, acc / np.maximum(num, 1), np.nan)
        work = np.where(todo & np.isfinite(avg), avg, work)
    dem[:] = work
    print(f'  內陸空洞補值 {n0} 像素（DTM 原始缺值，如雪山山脈方塊）')
    return outside


def main():
    if not os.path.exists(DEM):
        print(f'找不到 {DEM}（請先解壓 不分幅_全台20MDEM）')
        return 1
    target_w = int(sys.argv[1]) if len(sys.argv) > 1 else 1200

    import numpy as np
    import rasterio
    from rasterio.enums import Resampling
    from pyproj import Transformer

    src = rasterio.open(DEM)
    scale = target_w / src.width
    W = target_w
    H = max(1, int(round(src.height * scale)))
    print(f'降採樣 {src.width}×{src.height} → {W}×{H}')

    # ★ 用 masked 讀取：nodata 是 -32767，若直接 average 降採樣，
    #   海陸交界的格子會把 -32767 平均進去，產生大負值或被拉低的假高程
    #   （視覺上就是海岸出現破洞或異常色塊）。masked 會排除 nodata。
    dem_m = src.read(1, out_shape=(1, H, W),
                     resampling=Resampling.average, masked=True)
    dem = dem_m.filled(np.nan).astype('float32')[0] \
          if dem_m.ndim == 3 else dem_m.filled(np.nan).astype('float32')
    nodata = ~np.isfinite(dem) | (dem <= -1000)
    dem[nodata] = np.nan

    # ★ 補內陸空洞：DTM 原始檔在雪山山脈一帶（約 24.52N/121.08E）有整塊
    #   方形缺值，直接輸出會在山區留下白色破洞（實際那裡是 1200m 以上山地）。
    #   海域的缺值必須保留（要透明），故只補「四周被陸地包圍」的空洞。
    land_mask = _fill_inland_voids(dem)
    nodata = ~np.isfinite(dem)

    # 實際格距（公尺）：原始 20m × 降採樣倍率
    cell = 20.0 / scale

    # --- 1. 海拔灰階 ---
    rgb = np.zeros((H, W, 3), dtype='float32')
    prev = -1e9
    for top, c in ELEV_STOPS:
        m = (dem > prev) & (dem <= top)
        for k in range(3):
            rgb[..., k][m] = c[k]
        prev = top

    # --- 2. Hillshade ---
    filled = np.where(np.isnan(dem), 0.0, dem)
    dzdy, dzdx = np.gradient(filled, cell, cell)
    slope = np.arctan(np.hypot(dzdx, dzdy))
    aspect = np.arctan2(-dzdx, dzdy)
    az = math.radians(360.0 - AZIMUTH + 90.0)
    alt = math.radians(ALTITUDE)
    hs = (math.sin(alt) * np.cos(slope)
          + math.cos(alt) * np.sin(slope) * np.cos(az - aspect))
    hs = np.clip(hs, 0.0, 1.0)
    # 提高對比：純線性會太平；0.45~1.15 讓陰影明顯但不死黑
    hs = 0.45 + hs * 0.70
    hs = np.clip(hs, 0.0, 1.15)

    # --- 3. 正片疊底 ---
    out = np.clip(rgb * hs[..., None], 0, 255).astype('uint8')

    # ★ 只讓「外海」透明。DEM 的 nodata 也包含內陸水體（水庫、湖泊）與
    #   少數空洞，若一律透明，圖上會出現破洞（實測日月潭一帶破一個大洞）。
    #   做法：從影像四邊灌水做連通標記，只有連到邊界的 nodata 才是海。
    from collections import deque
    sea = np.zeros(nodata.shape, dtype=bool)
    dq = deque()
    Hh, Ww = nodata.shape
    for x in range(Ww):
        for y in (0, Hh - 1):
            if nodata[y, x] and not sea[y, x]: sea[y, x] = True; dq.append((y, x))
    for y in range(Hh):
        for x in (0, Ww - 1):
            if nodata[y, x] and not sea[y, x]: sea[y, x] = True; dq.append((y, x))
    while dq:
        y, x = dq.popleft()
        for dy, dx in ((1,0), (-1,0), (0,1), (0,-1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < Hh and 0 <= nx < Ww and nodata[ny, nx] and not sea[ny, nx]:
                sea[ny, nx] = True; dq.append((ny, nx))
    inland_hole = nodata & ~sea
    if inland_hole.any():
        # 內陸空洞：用最低海拔色填，並套用該處的暈渲，視覺上不突兀
        base = np.array(ELEV_STOPS[0][1], dtype='float32')
        out[inland_hole] = np.clip(base * hs[inland_hole][..., None],
                                   0, 255).astype('uint8')
        print(f'  內陸空洞補值 {int(inland_hole.sum())} 像素（水庫、湖泊等）')
    alpha = np.where(sea, 0, 255).astype('uint8')      # 僅外海透明

    try:
        from PIL import Image
    except ImportError:
        print('需要 Pillow：pip install pillow --break-system-packages')
        return 1
    img = Image.fromarray(np.dstack([out, alpha]), 'RGBA')
    img.save(OUT_PNG, optimize=True)

    # 地理範圍（轉為 WGS84 供 Leaflet imageOverlay）
    tr = Transformer.from_crs(src.crs, 'EPSG:4326', always_xy=True)
    b = src.bounds
    lo0, la0 = tr.transform(b.left, b.bottom)
    lo1, la1 = tr.transform(b.right, b.top)
    json.dump({'bounds': [[la0, lo0], [la1, lo1]],
               'size': [W, H], 'azimuth': AZIMUTH, 'altitude': ALTITUDE,
               'stops': [[t, '#%02X%02X%02X' % c] for t, c in ELEV_STOPS[:-1]]
                        + [[None, '#6C757D']]},
              io.open(OUT_META, 'w', encoding='utf-8'), ensure_ascii=False)
    kb = os.path.getsize(OUT_PNG) // 1024
    print(f'完成：{OUT_PNG}（{kb} KB）、{OUT_META}')
    print(f'  範圍 {la0:.3f}~{la1:.3f}N、{lo0:.3f}~{lo1:.3f}E')
    return 0


if __name__ == '__main__':
    sys.exit(main())
