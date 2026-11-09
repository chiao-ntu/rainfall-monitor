"""重要公共設施（政府開放資料）→ facilities.json ＋ 未採用清單 facilities_rejected.csv
   ★ 不猜座標：每一點都要落在資料自己寫的縣市（露營場再加鄉鎮）界內（容許 1 公里），
     對不上、座標為 0 或在臺灣範圍外的一律不上圖，列入未採用清單供人工確認。
   來源（使用者 2026-11-09 下載）：
     救援單位點位.csv（消防局、大隊、分隊；座標欄名寫 TWD97 但內容是經緯度）
     應變中心點位.csv（各縣市災害應變中心）
     全台露營場資料.csv（含營業狀態、是否合法、是否在原民區）"""
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
F['counts'] = {k: len(F[k]) for k in ('fire', 'eoc', 'camp')}
F['rejected'] = collections.Counter(r[0] for r in rej)
json.dump(F, open(os.path.join(OUT, 'facilities.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
with open(os.path.join(OUT, 'facilities_rejected.csv'), 'w', encoding='utf-8-sig', newline='') as fh:
    w = csv.writer(fh); w.writerow(['類別', '名稱', '經度', '緯度', '未採用原因']); w.writerows(rej)
print('採用', F['counts'], '未採用', dict(F['rejected']))
