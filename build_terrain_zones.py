#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""建立全國 368 鄉鎮市區「特性分區」四分類表。

分類定義（使用者訂定）：
  山區   ：鄉鎮市區內任一點海拔 > 1000m
  淺山區 ：最高海拔 800~1000m
  沿海地區：鄉鎮市區界臨海，且不屬於上述兩類
  平地   ：其餘

需要網路（Open-Meteo elevation API，與 fetch_rainfall.py 同一個資料來源）。
臨海判定已由 build_coastal.py 離線算好，本腳本只補海拔。

用法：
    python3 build_coastal.py          # 先產生 coastal.json（不需網路）
    python3 build_terrain_zones.py    # 再跑本腳本（需網路，約 5~15 分鐘）

輸出：
    terrain_zones.json   {"縣市鄉鎮": {"zone": "...", "max_elev": 1234.5, "coastal": true}}
    town_elev_cache.json 海拔快取（中斷後重跑不必重抓）
"""
import io, json, math, os, sys, time
try:
    import requests
except ImportError:
    sys.exit('需要 requests：pip install requests')
try:
    from shapely.geometry import shape, Point
    from shapely.prepared import prep
except ImportError:
    sys.exit('需要 shapely：pip install shapely')

SRC          = 'index.html'
COASTAL_FILE = 'coastal.json'
CACHE_FILE   = 'town_elev_cache.json'
OUT_FILE     = 'terrain_zones.json'
API          = 'https://api.open-meteo.com/v1/elevation'
GRID_DEG     = 0.008     # ≈0.9km 取樣間距；山區鄉鎮足以抓到稜線
BATCH        = 100       # Open-Meteo 單次上限
SLEEP        = 0.6       # 禮貌性間隔，避免 429
MAX_PTS_PER_TOWN = 900   # 單一鄉鎮取樣上限（防止超大鄉鎮爆量）

TH_MOUNTAIN, TH_FOOTHILL = 1000.0, 800.0


def load_town_geo(path=SRC):
    s = io.open(path, encoding='utf-8').read()
    i = s.index('const TOWN_GEO = ')
    j = s.index('\n', i)
    geo = json.loads(s[i + len('const TOWN_GEO = '):j].rstrip(';'))
    return [(f['properties']['COUNTYNAME'], f['properties']['TOWNNAME'],
             shape(f['geometry'])) for f in geo['features']]


def sample_points(geom):
    """在多邊形內取格點；小鄉鎮至少回形心與代表點，確保不會 0 點。"""
    minx, miny, maxx, maxy = geom.bounds
    pg = prep(geom)
    pts = []
    y = miny
    while y <= maxy:
        x = minx
        while x <= maxx:
            if pg.contains(Point(x, y)):
                pts.append((round(y, 5), round(x, 5)))
            x += GRID_DEG
        y += GRID_DEG
    if not pts:
        p = geom.representative_point()
        pts = [(round(p.y, 5), round(p.x, 5))]
    if len(pts) > MAX_PTS_PER_TOWN:                 # 均勻抽稀，保留分布
        step = math.ceil(len(pts) / MAX_PTS_PER_TOWN)
        pts = pts[::step]
    return pts


def fetch_elev(points, cache):
    """回傳 points 對應高程（公尺）；已在快取者不重抓。"""
    todo = [p for p in points if f'{p[0]},{p[1]}' not in cache]
    for i in range(0, len(todo), BATCH):
        chunk = todo[i:i + BATCH]
        lats = ','.join(str(a) for a, b in chunk)
        lons = ','.join(str(b) for a, b in chunk)
        for attempt in range(4):
            try:
                r = requests.get(API, params={'latitude': lats, 'longitude': lons}, timeout=60)
                if r.status_code == 429:
                    time.sleep(5 * (attempt + 1)); continue
                r.raise_for_status()
                el = r.json().get('elevation') or []
                if len(el) != len(chunk):
                    raise ValueError(f'回傳長度 {len(el)} != 請求 {len(chunk)}')
                for (a, b), e in zip(chunk, el):
                    cache[f'{a},{b}'] = e
                break
            except Exception as ex:
                if attempt == 3:
                    print(f'    批次失敗（已重試4次）：{ex}')
                else:
                    time.sleep(3 * (attempt + 1))
        time.sleep(SLEEP)
    return [cache.get(f'{p[0]},{p[1]}') for p in points]


def classify(max_elev, coastal):
    if max_elev is None:
        return None                       # 取不到海拔 → 不猜
    if max_elev > TH_MOUNTAIN:  return '山區'
    if max_elev >= TH_FOOTHILL: return '淺山區'
    if coastal:                 return '沿海地區'
    return '平地'


def main():
    if not os.path.exists(COASTAL_FILE):
        sys.exit(f'找不到 {COASTAL_FILE}，請先執行 build_coastal.py')
    coastal = json.load(io.open(COASTAL_FILE, encoding='utf-8'))
    cache = json.load(io.open(CACHE_FILE, encoding='utf-8')) if os.path.exists(CACHE_FILE) else {}
    towns = load_town_geo()
    print(f'鄉鎮 {len(towns)} 個，臨海表 {len(coastal)} 筆，海拔快取 {len(cache)} 點')

    out, stats, failed = {}, {}, []
    for n, (c, t, g) in enumerate(towns, 1):
        key = c + t
        pts = sample_points(g)
        elev = [e for e in fetch_elev(pts, cache) if e is not None]
        mx = max(elev) if elev else None
        zone = classify(mx, coastal.get(key, False))
        if zone is None: failed.append(key)
        out[key] = {'county': c, 'town': t, 'zone': zone,
                    'max_elev': None if mx is None else round(mx, 1),
                    'coastal': bool(coastal.get(key, False)),
                    'n_samples': len(pts)}
        stats[zone] = stats.get(zone, 0) + 1
        print(f'  [{n:3d}/{len(towns)}] {key:12s} 取樣{len(pts):4d}點 '
              f'最高{"—" if mx is None else f"{mx:6.0f}m"} → {zone}')
        if n % 20 == 0:
            json.dump(cache, io.open(CACHE_FILE, 'w', encoding='utf-8'), ensure_ascii=False)

    json.dump(cache, io.open(CACHE_FILE, 'w', encoding='utf-8'), ensure_ascii=False)
    json.dump(out, io.open(OUT_FILE, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, sort_keys=True)
    print(f'\n分類統計：{stats}')
    if failed:
        print(f'★ {len(failed)} 個鄉鎮取不到海拔（zone=null，請重跑補齊）：'
              + '、'.join(failed[:10]))
    print(f'已寫出 {OUT_FILE}')

    # 抽樣自檢：這幾個的分類應該很明確
    expect = {'南投縣仁愛鄉': '山區', '臺中市和平區': '山區', '嘉義縣阿里山鄉': '山區',
              '臺北市大安區': '平地', '雲林縣virtual': None}
    for k, v in expect.items():
        if k in out and v:
            got = out[k]['zone']
            print(f"  {'OK ' if got == v else '!! '}{k}: {got}（期望 {v}，"
                  f"最高 {out[k]['max_elev']}m）")


if __name__ == '__main__':
    main()
