"""重要公共設施（政府開放資料）→ facilities.json ＋ 未採用清單 facilities_rejected.csv
   ★ 不猜座標：每一點都要落在資料自己寫的縣市（露營場再加鄉鎮）界內（容許 1 公里），
     對不上、座標為 0 或在臺灣範圍外的一律不上圖，列入未採用清單供人工確認。
   來源（使用者 2026-11-09 下載）：
     救援單位點位.csv（消防局、大隊、分隊；座標欄名寫 TWD97 但內容是經緯度）
     應變中心點位.csv（各縣市災害應變中心）
     全台露營場資料.csv（含營業狀態、是否合法、是否在原民區）
     地下水觀測井／地層下陷監測井／地層下陷監測站／深層水準樁（水利署，TWD97 座標）
     警察機關（警政署 5958、24419）、海嘯警報臺（34102）：opendata/raw/datagov/ 有檔才建"""
import csv, json, re, sys, os, collections
import pandas as pd
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
HERE = os.path.dirname(os.path.abspath(__file__))
OD = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'opendata')
OUT = sys.argv[2] if len(sys.argv) > 2 else HERE
P = json.load(open(os.path.join(HERE, 'town_polys.json'), encoding='utf-8'))
TOWN = {k: (v[:4], unary_union([Polygon(r).buffer(0) for r in v[4] if len(r) >= 3])) for k, v in P.items()}
CTY = collections.defaultdict(list)
for k, (bb, g) in TOWN.items(): CTY[k[:3]].append(g)
CTY = {k: unary_union(v) for k, v in CTY.items()}
norm = lambda s: str(s or '').replace('台', '臺').strip()
TOL = 0.01   # 約 1 公里
def town_of(x, y, prefer=None):
    """座標所在鄉鎮；界外 1 公里內有多個候選時，優先資料自己寫的縣市（界線附近的點不誤殺）。"""
    p = Point(x, y); cands = []
    for k, (bb, g) in TOWN.items():
        if bb[0] - TOL <= x <= bb[2] + TOL and bb[1] - TOL <= y <= bb[3] + TOL:
            d = g.distance(p)
            if d <= TOL: cands.append((d, k))
    if not cands: return None
    cands.sort()
    if prefer:
        for d, k in cands:
            if k.startswith(prefer): return k
    return cands[0][1]
def county_in(text):
    m = re.match(r'(..[縣市])', norm(text)); return m.group(1) if m else None
rej = []
def check(kind, name, x, y, county, town=None):
    try: x, y = float(x), float(y)
    except Exception: rej.append((kind, name, x, y, '座標缺漏')); return None
    if x != x or y != y: rej.append((kind, name, '', '', '座標缺漏')); return None
    if x == 0 and y == 0: rej.append((kind, name, x, y, '座標為 0（缺漏）')); return None
    if not (118 <= x <= 122.5 and 21.5 <= y <= 26.5): rej.append((kind, name, x, y, '座標在臺灣範圍外')); return None
    tw = town_of(x, y, (county or '') + (town or ''))
    if not tw: rej.append((kind, name, x, y, '不在任何鄉鎮界內')); return None
    if county and tw[:3] != county:
        rej.append((kind, name, x, y, f'資料寫{county}，座標落在{tw}')); return None
    if town and not tw.endswith(town):
        if CTY.get(county) is None or TOWN.get(county + town) is None or TOWN[county + town][1].distance(Point(x, y)) > TOL:
            rej.append((kind, name, x, y, f'資料寫{county}{town}，座標落在{tw}')); return None
    return round(x, 5), round(y, 5), tw
F = {'source': '政府資料開放平臺（使用者 2026-11-09 下載）'}
# 消防
f = pd.read_csv(os.path.join(OD, '救援單位點位.csv'), encoding='cp950').iloc[:, :5]
f.columns = ['n', 'a', 'tel', 'x', 'y']
out = []
for r in f.itertuples():
    n = norm(r.n); c = county_in(r.a) or county_in(n)
    g = check('消防單位', n, r.x, r.y, c)
    if g:
        lv = '消防局' if re.search(r'消防局$', n) else '大隊' if '大隊' in n else '分隊' if re.search(r'分隊|小隊', n) else '其他'
        out.append({'n': n, 'c': [g[0], g[1]], 'a': norm(r.a), 'tel': str(r.tel).strip(), 'tw': g[2], 'lv': lv})
F['fire'] = out
# 應變中心
e = pd.read_csv(os.path.join(OD, '應變中心點位.csv'))
out = []
for r in e.itertuples(index=False):
    if str(r[0]) == 'nan': continue        # 空白列
    n = norm(r[0]); c = county_in(n) or county_in(r[1])
    g = check('災害應變中心', n, r[4], r[5], c)
    if g: out.append({'n': n, 'c': [g[0], g[1]], 'a': norm(r[1]), 'tel': str(r[2]).strip(), 'tw': g[2],
                      'note': str(r[3]).strip()})
F['eoc'] = out
# 露營場
c = pd.read_csv(os.path.join(OD, '全台露營場資料.csv'))
out = []
for r in c.itertuples(index=False):
    d = dict(zip(c.columns, r))
    if d['營業狀態'] != '營業中': continue
    n = norm(d['露營場名稱']); cty = norm(d['縣市別']); tw = norm(d['鄉/鎮/市/區'])
    g = check('露營場', n, d['經度'], d['緯度'], cty, tw)
    if not g: continue
    o = {'n': n, 'c': [g[0], g[1]], 'a': norm(d['地址']), 'tw': g[2],
         'legal': d['符合相關法規露營場／違反相關法規露營場'] == '符合相關法規露營場',
         'ab': d['是否有在原民區'] == '是'}
    tel = '、'.join(str(x).strip() for x in (d['電話'], d['手機']) if str(x).strip() not in ('', 'nan'))
    if tel: o['tel'] = tel
    if str(d['違反相關法規']).strip() not in ('', 'nan'): o['vio'] = norm(d['違反相關法規']).replace('\\', '、')
    out.append(o)
F['camp'] = out
# ── 地下水與地層下陷監測（經濟部水利署；使用者 2026-11-09 下載，TWD97 座標）──
from pyproj import Transformer
_T97 = Transformer.from_crs('EPSG:3826', 'EPSG:4326', always_xy=True)
_T97_119 = Transformer.from_crs('EPSG:3825', 'EPSG:4326', always_xy=True)
def _ll97(x, y, county=None):
    """TWD97 二度分帶 → 經緯度。
       ★ 離島資料有的用 119 度帶（金門），有的用 121 度帶（澎湖，同一個水利署檔案就混用），
         不依縣市硬指定：兩個分帶都換算，只採用落在資料所寫縣市內的那一個；
         沒有寫縣市、或兩個都不在界內 → 回傳 121 度帶結果，交給 check() 判定（通常會被剔除）。"""
    try: x, y = float(x), float(y)
    except Exception: return None, None
    if y < 1e6 < x: x, y = y, x      # ★ 官方檔部分列 X、Y 對調（Y 在 240～280 萬、X 在 10～40 萬，量級可明確判別）
    if not (50000 < x < 400000 and 2400000 < y < 2800000): return None, None
    cands = [t.transform(x, y) for t in (_T97, _T97_119)]
    if county:
        hit = [c for c in cands if (town_of(c[0], c[1], county) or '').startswith(county)]
        if len(hit) == 1: return hit[0]
    return cands[0]
_TOWN_ONLY = collections.defaultdict(set)
for k in TOWN: _TOWN_ONLY[k[3:]].add(k[:3])
def county_of_addr(a):
    """地址的縣市；地址沒寫縣市但以全國唯一的鄉鎮名開頭（例：金寧鄉寧山段）→ 由鄉鎮名得縣市。"""
    c = county_in(a)
    if c: return c
    for t in sorted(_TOWN_ONLY, key=len, reverse=True):
        if t and norm(a).startswith(t) and len(_TOWN_ONLY[t]) == 1: return next(iter(_TOWN_ONLY[t]))
    return None
def _csv(name):
    fp = os.path.join(OD, name)
    return list(csv.DictReader(open(fp, encoding='utf-8-sig'))) if os.path.exists(fp) else None
rows = _csv('地下水觀測井.csv')
if rows is not None:
    out = []
    for d in rows:
        if d['disusedate'].strip() not in ('', '99999999'): continue          # 已廢井
        n = norm(d['wellname']); a = norm(d['locationaddress'])
        xy = (d['locationbytwd97'] or '').split()
        c = county_of_addr(a)
        lo, la = _ll97(*xy, county=c) if len(xy) == 2 else (None, None)
        if lo is None and xy and any(v.strip('0.') for v in xy):
            rej.append(('地下水觀測井', n, '', '', f'座標值異常（官方原值 TWD97：{" ".join(xy)}）')); continue
        # ★ 官方檔的 countyname/townname 欄部分列與地址不符（例：地址屏東縣枋寮鄉、欄位寫連江縣南竿鄉），
        #   以「地址寫的縣市」為準核對座標；欄位不一致的列在未採用清單註記但不因此剔除。
        g = check('地下水觀測井', n, lo, la, c)
        if not g: continue
        o = {'n': n, 'c': [g[0], g[1]], 'a': a, 'tw': g[2], 'id': d['wellidentifier'],
             'zone': norm(d['groundwaterzone'])}
        for k, col in (('dep', 'welldepth'), ('el', 'wellelevation')):
            try: o[k] = round(float(d[col]), 1)
            except Exception: pass
        out.append(o)
    F['gw'] = out
rows = _csv('地層下陷監測井.csv')
if rows is not None:
    out = []
    for d in rows:
        if d['status'].strip() != 'T': continue
        n = norm(d['landsubsidencemonitoringwellname'])
        lo, la = _ll97(d['x_3826'], d['y_3826'], norm(d['countyname']))
        g = check('地層下陷監測井', n, lo, la, norm(d['countyname']), norm(d['townname']))
        if g: out.append({'n': n, 'c': [g[0], g[1]], 'tw': g[2], 'id': d['wellidentifier'],
                          'zone': norm(d['groundwaterzonename']), 'since': d['settime'][:10]})
    F['lsw'] = out
rows = _csv('地層下陷監測站.csv')
if rows is not None:
    out = []
    for d in rows:
        if d['status'].strip() != 'T' or d['disusedate'].strip(): continue
        n = norm(d['observatoryname']); a = norm(d['address'])
        lo, la = _ll97(d['x_3826'], d['y_3826'], county_in(a))
        g = check('地層下陷監測站', n, lo, la, county_in(a))
        if g: out.append({'n': n, 'c': [g[0], g[1]], 'a': a, 'tw': g[2], 'id': d['observatoryidentifier'],
                          'since': d['establishedtime'][:10]})
    F['lsst'] = out
rows = _csv('深層水準樁.csv')
if rows is not None:
    out = []
    for d in rows:
        if d['status'].strip() != 'T': continue
        n = norm(d['deepbenchmarkchinesename'])
        lo, la = _ll97(d['x_3826'], d['y_3826'])
        g = check('深層水準樁', n, lo, la, None)
        if g:
            o = {'n': n, 'c': [g[0], g[1]], 'tw': g[2], 'id': d['identifier'], 'since': d['establishedtime'][:10]}
            try: o['dep'] = round(float(d['depth']), 1)
            except Exception: pass
            out.append(o)
    F['bm'] = out

# ── 警察機關、海嘯警報臺（警政署，data.gov.tw 5958／24419／34102；由 fetch_opendata.py 下載）──
RAW = os.path.join(HERE, 'opendata', 'raw', 'datagov')
def _raw_tables(did):
    d = os.path.join(RAW, did)
    if not os.path.isdir(d): return []
    tabs = []
    for fn in sorted(os.listdir(d)):
        fp = os.path.join(d, fn)
        if fn.lower().endswith('.csv'):
            b = open(fp, 'rb').read()
            for enc in ('utf-8-sig', 'cp950', 'big5hkscs'):
                try: tabs.append(list(csv.DictReader(b.decode(enc).splitlines()))); break
                except UnicodeDecodeError: pass
        elif fn.lower().endswith('.json') and not fn.startswith('_'):
            j = json.load(open(fp, encoding='utf-8-sig'))
            if isinstance(j, dict): j = next((v for v in j.values() if isinstance(v, list)), [])
            if isinstance(j, list) and j and isinstance(j[0], dict): tabs.append(j)
    return tabs
def _col(d, *names):
    for n in names:
        for k in d:
            if k and k.strip().lower() == n.lower(): return d[k]
    return None
pol = []
for did in ('5958', '24419'):
    for tab in _raw_tables(did):
        for d in tab:
            n = norm(_col(d, '中文單位名稱', '單位') or ''); a = norm(_col(d, '地址') or '')
            if not n: continue
            g = check('警察機關', n, _col(d, 'POINT_X', '經度'), _col(d, 'POINT_Y', '緯度'), county_in(a) or county_in(n))
            if g:
                lv = '警察局' if re.search(r'警察局$', n) else '分局' if n.endswith('分局') else \
                     '分駐（派出）所' if re.search(r'(分駐|派出)所$', n) else '其他'
                pol.append({'n': n, 'c': [g[0], g[1]], 'a': a, 'tel': str(_col(d, '電話') or '').strip(),
                            'tw': g[2], 'lv': lv, 'src': did})
if pol:
    seen = set(); F['police'] = [p for p in pol if not ((p['n'], p['c'][0], p['c'][1]) in seen or seen.add((p['n'], p['c'][0], p['c'][1])))]
sir = []
for tab in _raw_tables('34102'):
    for d in tab:
        n = norm(next((d[k] for k in d if k and ('名稱' in k or '站名' in k or '設置地點' in k)), '') or '')
        a = norm(_col(d, '地址', '設置地址') or '')
        x = _col(d, '經度', 'POINT_X', 'Longitude'); y = _col(d, '緯度', 'POINT_Y', 'Latitude')
        g = check('海嘯警報臺', n or a, x, y, county_in(a) or county_in(n))
        if g: sir.append({'n': n or a, 'c': [g[0], g[1]], 'a': a, 'tw': g[2]})
if sir: F['siren'] = sir

# ── 避難收容所（既有 shelters.geojson）→ 去除個資、核對座標後重新輸出 ──
#   ★ 原檔含「管理人姓名、管理人電話」屬個人資料，公開圖層一律不輸出（2026-11-08 使用者指示）。
SH_SRC = os.path.join(OD, 'shelters_原始.geojson')
if os.path.exists(SH_SRC):
    feats = []
    for f in json.load(open(SH_SRC, encoding='utf-8'))['features']:
        p = f.get('properties') or {}; c = (f.get('geometry') or {}).get('coordinates')
        n = norm(p.get('name'))
        if not c: rej.append(('避難收容所', n, '', '', '座標缺漏')); continue
        g = check('避難收容所', n, c[0], c[1], norm(p.get('cty')) or None, norm(p.get('twn')) or None)
        if not g: continue
        q = {k: p[k] for k in ('name', 'cty', 'twn', 'vil', 'addr', 'cvil', 'cap', 'haz', 'ind', 'out', 'weak') if k in p}
        feats.append({'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [g[0], g[1]]}, 'properties': q})
    json.dump({'type': 'FeatureCollection', 'source': '避難收容處所（既有圖資；已移除管理人姓名與電話）',
               'features': feats}, open(os.path.join(OUT, 'shelters.geojson'), 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
    print('避難收容所', len(feats))

KEYS = [k for k in ('fire', 'eoc', 'camp', 'police', 'siren', 'gw', 'lsw', 'lsst', 'bm') if k in F]
F['counts'] = {k: len(F[k]) for k in KEYS}
F['rejected'] = collections.Counter(r[0] for r in rej)
json.dump(F, open(os.path.join(OUT, 'facilities.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
with open(os.path.join(OUT, 'facilities_rejected.csv'), 'w', encoding='utf-8-sig', newline='') as fh:
    w = csv.writer(fh); w.writerow(['類別', '名稱', '經度', '緯度', '未採用原因']); w.writerows(rej)
print('採用', F['counts'], '未採用', dict(F['rejected']))
