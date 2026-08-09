#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""離線判定「鄉鎮市區界是否臨海」——不需要網路。

作法：把 368 鄉鎮多邊形聯集成「陸地」，取其外環（含各離島的外環）＝海岸線。
      某鄉鎮若其邊界與海岸線有實質接觸（長度 > 門檻），即判定為臨海。

★ 為什麼不是「邊界不與其他鄉鎮相鄰就算臨海」：
  相鄰判定會被拓樸縫隙（sliver）誤判，聯集後取外環穩定得多。
輸出：coastal.json  {"縣市鄉鎮": true/false, ...}
"""
import io, json, re, sys
from shapely.geometry import shape, LineString, MultiPolygon, Polygon
from shapely.ops import unary_union

SRC = 'index.html'
MIN_COAST_M = 300.0          # 與海岸線接觸須超過此長度才算臨海（濾掉端點觸碰）
BUF_DEG = 0.0008             # ≈80m：吸附容差，吸收圖資精度誤差


def load_town_geo(path=SRC):
    s = io.open(path, encoding='utf-8').read()
    i = s.index('const TOWN_GEO = ')
    j = s.index('\n', i)
    geo = json.loads(s[i + len('const TOWN_GEO = '):j].rstrip(';'))
    out = []
    for f in geo['features']:
        p = f['properties']
        out.append((p['COUNTYNAME'], p['TOWNNAME'], shape(f['geometry'])))
    return out


def deg_len_to_m(line, lat):
    """把「度」為單位的長度粗略換算成公尺（緯度 lat 附近）。"""
    import math
    return line.length * 111000.0 * max(0.3, math.cos(math.radians(lat)))


def main():
    towns = load_town_geo()
    print(f'鄉鎮多邊形：{len(towns)}')

    # 修復可能的自相交，再聯集成陸地
    polys = []
    for c, t, g in towns:
        gg = g if g.is_valid else g.buffer(0)
        polys.append(gg)
    land = unary_union(polys)
    print(f'陸地聯集完成：{land.geom_type}')

    # 海岸線＝陸地各部分的外環（不含內部湖泊等內環）
    parts = list(land.geoms) if isinstance(land, MultiPolygon) else [land]
    print(f'  陸地共 {len(parts)} 塊（本島＋離島）')
    coast = unary_union([LineString(p.exterior.coords) for p in parts])
    coast_buf = coast.buffer(BUF_DEG)

    res, stats = {}, {'coastal': 0, 'inland': 0}
    for c, t, g in towns:
        gg = g if g.is_valid else g.buffer(0)
        bnd = gg.boundary
        touch = bnd.intersection(coast_buf)
        lat = gg.centroid.y
        length_m = 0.0
        if not touch.is_empty:
            geoms = getattr(touch, 'geoms', [touch])
            for gm in geoms:
                if gm.geom_type in ('LineString', 'MultiLineString'):
                    length_m += deg_len_to_m(gm, lat)
        is_coastal = length_m > MIN_COAST_M
        res[c + t] = is_coastal
        stats['coastal' if is_coastal else 'inland'] += 1

    json.dump(res, io.open('coastal.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=0, sort_keys=True)
    print(f"臨海 {stats['coastal']} 個、非臨海 {stats['inland']} 個 → coastal.json")

    # 抽樣自檢：這些必為臨海／必為內陸
    must_coastal = ['新北市淡水區', '臺南市安平區', '屏東縣恆春鎮', '宜蘭縣蘇澳鎮',
                    '花蓮縣豐濱鄉', '臺東縣長濱鄉', '澎湖縣馬公市', '基隆市中正區',
                    '雲林縣口湖鄉', '彰化縣芳苑鄉', '高雄市旗津區', '金門縣金城鎮']
    must_inland = ['南投縣仁愛鄉', '南投縣信義鄉', '臺中市和平區', '嘉義縣阿里山鄉',
                   '臺北市大安區', '桃園市復興區', '新竹縣尖石鄉', '高雄市那瑪夏區',
                   '南投縣南投市', '臺中市西區']
    bad = []
    for k in must_coastal:
        if k in res and not res[k]: bad.append(f'{k} 應為臨海但判為內陸')
    for k in must_inland:
        if k in res and res[k]: bad.append(f'{k} 應為內陸但判為臨海')
    missing = [k for k in must_coastal + must_inland if k not in res]
    if missing:
        print('  （查無此鄉鎮，可能名稱寫法不同）:', '、'.join(missing))
    if bad:
        print('!! 自檢未通過：')
        for b in bad: print('   -', b)
        sys.exit(1)
    print('自檢通過：抽樣 22 個鄉鎮的臨海/內陸判定皆正確')


if __name__ == '__main__':
    main()
