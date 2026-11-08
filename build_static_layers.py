"""國土測繪中心（NLSC）圖資 → 前端靜態圖層（2026-11-08）。

輸入：NLSC shapefile 目錄（村里界、鐵路、高鐵、捷運、輕軌、臺鐵／捷運／高鐵車站、機場、碼頭、各級學校範圍圖）
輸出（OUT 目錄）：
  villages/<縣市>.json  村里界（依縣市分檔，前端放大且縣市在畫面內才載入），名稱綁「縣市＋鄉鎮＋村里」
  villages/index.json  各縣市外框與檔名
  transport.json       鐵路／高鐵／捷運／輕軌（線）＋臺鐵／高鐵／捷運車站、機場、碼頭（點）
  schools.json         各級學校（範圍多邊形＋代表點）
座標：一律轉 WGS84（TWD97 與 WGS84 差異 < 1m），小數 5 位（約 1m）。
簡化：村里 12m、學校 5m、路線 20m（Douglas-Peucker，保拓撲）。
用法：python3 build_static_layers.py <NLSC 目錄> <輸出目錄>
"""
import os, sys, json, re, collections
import shapefile
from pyproj import Transformer, CRS
from shapely.geometry import shape, mapping, Polygon, MultiPolygon, LineString, MultiLineString
from shapely.ops import unary_union

SRC = sys.argv[1] if len(sys.argv) > 1 else '/home/claude/nlsc'
OUT = sys.argv[2] if len(sys.argv) > 2 else '/home/claude/layers'
os.makedirs(os.path.join(OUT, 'villages'), exist_ok=True)

def reader(name):
    p = os.path.join(SRC, name)
    for enc in ('utf-8', 'cp950'):
        try:
            r = shapefile.Reader(p, encoding=enc); r.record(0); return r
        except Exception:
            pass
    raise RuntimeError(name)

def to_wgs(name):
    prj = open(os.path.join(SRC, name[:-4] + '.prj'), encoding='utf-8', errors='ignore').read()
    if prj.startswith('GEOGCS'):
        return None
    if 'Penghu' in prj or 'zone_119' in prj or '119' in prj.split('PROJECTION')[0]:
        return Transformer.from_crs(3825, 4326, always_xy=True)
    return Transformer.from_crs(3826, 4326, always_xy=True)

def geom_of(sr, tf):
    g = shape(sr.shape.__geo_interface__)
    if tf is None:
        return g
    from shapely.ops import transform
    return transform(lambda x, y, z=None: tf.transform(x, y), g)

R5 = lambda v: round(v, 5)
def ring_out(r):
    return [[R5(x), R5(y)] for x, y in r.coords]
def poly_out(g):
    gs = g.geoms if isinstance(g, MultiPolygon) else [g]
    return [[ring_out(p.exterior)] + [ring_out(i) for i in p.interiors] for p in gs if not p.is_empty]
def line_out(g):
    gs = g.geoms if isinstance(g, MultiLineString) else [g]
    return [ring_out(l) for l in gs if not l.is_empty]

def norm_tai(s):
    return (s or '').replace('台', '臺').strip()

# ── 村里 ──
vr = reader('VILLAGE_NLSC_1150817.shp'); tf = to_wgs('VILLAGE_NLSC_1150817.shp')
by_cty = collections.defaultdict(list)
def add_village(rec, g):
    g = g.buffer(0) if not g.is_valid else g
    gs = g.simplify(0.00012, preserve_topology=True)
    by_cty[norm_tai(rec['COUNTYNAME'])].append({
        't': norm_tai(rec['TOWNNAME']), 'v': norm_tai(rec['VILLNAME']) or '（未編定村里）',
        'id': rec['VILLCODE'], 'p': poly_out(gs),
        'c': [R5(gs.representative_point().x), R5(gs.representative_point().y)]})
for sr in vr.iterShapeRecords():
    add_village(sr.record, geom_of(sr, tf))
# 三和村（瑪家鄉）補充檔：主檔無此村
sh = reader('Village_Sanhe.shp')
for sr in sh.iterShapeRecords():
    if not any(f['id'] == sr.record['VILLCODE'] for f in by_cty[norm_tai(sr.record['COUNTYNAME'])]):
        add_village(sr.record, geom_of(sr, to_wgs('Village_Sanhe.shp')))
idx = {}
for i, (cty, feats) in enumerate(sorted(by_cty.items())):
    fn = f'v{i:02d}.json'
    xs = [pt[0] for f in feats for poly in f['p'] for pt in poly[0]]
    ys = [pt[1] for f in feats for poly in f['p'] for pt in poly[0]]
    idx[cty] = {'file': fn, 'n': len(feats), 'bbox': [min(xs), min(ys), max(xs), max(ys)],
                'towns': sorted({f['t'] for f in feats})}
    json.dump({'county': cty, 'feats': feats}, open(os.path.join(OUT, 'villages', fn), 'w', encoding='utf-8'),
              ensure_ascii=False, separators=(',', ':'))
json.dump({'source': 'NLSC 村里界 1150817（含瑪家鄉三和村補充檔）', 'counties': idx},
          open(os.path.join(OUT, 'villages', 'index.json'), 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
print('村里', sum(v['n'] for v in idx.values()), '縣市', len(idx))

# ── 交通 ──
T = {'source': 'NLSC 交通圖資（鐵路/捷運/輕軌/碼頭/車站 1150409；高鐵/機場 1101208；臺鐵車站 1140206）'}

# ★ 2026-11-08 各線官方路線色（使用者指定「換成官方公告的各線顏色」）。
#   來源：各營運機構路線識別色，取自維基百科路線色模組（Module:Adjacent stations/*、
#   模板:臺中捷運顏色、模板:高雄捷運顏色；後兩者註明取自營運機構官網／捷運局指定色）。
#   查不到官方色的（阿里山林業鐵路、圖資未標路線名稱的軌道）不自編顏色，一律中性灰。
LINE_COLOR = {
    ('臺北捷運', '文湖線'): '#C48C31', ('臺北捷運', '淡水信義線'): '#E3002C',
    ('臺北捷運', '新北投線'): '#FD92A3', ('臺北捷運', '松山新店線'): '#008659',
    ('臺北捷運', '小碧潭線'): '#CFDB00', ('臺北捷運', '中和新蘆線'): '#F8B61C',
    ('臺北捷運', '板南線'): '#0070BD', ('臺北捷運', '貓空纜車'): '#77BC1F',
    ('新北捷運', '環狀線'): '#FFDB00', ('新北捷運', '三鶯線'): '#79BCE8',
    ('新北捷運', '安坑輕軌'): '#C3B091', ('新北捷運', '淡海輕軌綠山線'): '#E5554F',
    ('新北捷運', '淡海輕軌藍海線'): '#E5554F',
    ('臺灣桃園國際機場捷運', '機場捷運'): '#8246AF',
    ('臺中捷運', '綠線'): '#8EC31C',
    ('高雄捷運', '紅線'): '#E20B65', ('高雄捷運', '橘線'): '#FAA73F', ('高雄捷運', '環狀輕軌'): '#81C03A',
}
TRA_COLOR, HSR_COLOR, NO_OFFICIAL = '#090980', '#C35617', '#7a7a7a'

def _seg_records(name, namef, sysf=None, keep=lambda r: True):
    """逐段讀入（不合併），回傳 [(系統, 名稱, WGS 幾何)] 與略過統計。"""
    r = reader(name); tf = to_wgs(name); out = []; skipped = collections.Counter()
    for sr in r.iterShapeRecords():
        rec = sr.record
        nm = norm_tai(rec[namef]) if namef else ''
        if not keep(rec):
            skipped[nm or '（無名）'] += 1; continue
        out.append((norm_tai(rec[sysf]) if sysf else '', nm, geom_of(sr, tf)))
    return out, skipped

# ★ 圖資有一批「未標路線名稱」的軌道段（臺北 57、機場捷運 27、臺中 11、高雄 30、臺鐵 207 段）。
#   不猜：只有當該段 ≥50% 長度落在同系統某條具名路線 40m 內（＝與該線共線，例如雙軌的另一軌），
#   才歸入那條線並用它的顏色；其餘（離具名路線較遠的機廠、聯絡線、側線等）保留為未標名、中性灰。
ATTACH_BUF, ATTACH_FRAC = 0.0004, 0.5
def attach_unnamed(segs):
    named = collections.defaultdict(list)
    for sy, nm, g in segs:
        if nm: named[(sy, nm)].append(g)
    U = {k: unary_union(v).buffer(ATTACH_BUF) for k, v in named.items()}
    out, n_att, n_left = [], 0, 0
    for sy, nm, g in segs:
        if not nm and g.length > 0:
            best = max(((g.intersection(b).length / g.length, k) for k, b in U.items() if k[0] == sy),
                       default=(0, None))
            if best[0] >= ATTACH_FRAC:
                nm = best[1][1]; n_att += 1
            else:
                n_left += 1
        out.append((sy, nm, g))
    return out, n_att, n_left

def merge_lines(segs, color_of):
    m = collections.OrderedDict()
    for sy, nm, g in segs:
        k = (sy, nm)
        f = m.setdefault(k, {'n': nm, 's': sy, 'c': color_of(sy, nm), 'l': []})
        f['l'] += line_out(g.simplify(0.0002, preserve_topology=True))
    return list(m.values())

def tra_color(sy, nm):
    if nm.startswith('臺鐵'): return TRA_COLOR
    return NO_OFFICIAL      # 阿里山林業鐵路（林業保育署）、未標名段：查無官方路線色

status0 = lambda r: int(r['STATUS'] or 0) == 0
rail_s, sk_rail = _seg_records('RAIL_1150409.shp', 'RAILNAME', keep=status0)
rail_s, a1, l1 = attach_unnamed(rail_s)
hsr_s, _ = _seg_records('HSRAIL_1101208.shp', 'HSNAME')
mrt_s, sk_mrt = _seg_records('MRT_1150409.shp', 'MRTCODE', 'MRTSYS', keep=status0)
mrt_s, a2, l2 = attach_unnamed(mrt_s)
lrt_s, sk_lrt = _seg_records('LRT_1150409.shp', 'LRTCODE', 'LRTSYS', keep=status0)
lrt_s, a3, l3 = attach_unnamed(lrt_s)
T['rail'] = merge_lines(rail_s, tra_color)
T['hsr'] = merge_lines(hsr_s, lambda sy, nm: HSR_COLOR)
T['mrt'] = merge_lines(mrt_s, lambda sy, nm: LINE_COLOR.get((sy, nm), NO_OFFICIAL))
T['lrt'] = merge_lines(lrt_s, lambda sy, nm: LINE_COLOR.get((sy, nm), NO_OFFICIAL))
T['unnamed_attach'] = {'rail': [a1, l1], 'mrt': [a2, l2], 'lrt': [a3, l3]}
T['not_shown_status'] = {'rail': dict(sk_rail), 'mrt': dict(sk_mrt), 'lrt': dict(sk_lrt)}
_miss = sorted({(f['s'], f['n']) for f in T['mrt'] + T['lrt'] if f['n'] and (f['s'], f['n']) not in LINE_COLOR})
if _miss:
    raise SystemExit(f'!! 具名路線缺官方色（請補 LINE_COLOR，不可自編）：{_miss}')

def points(name, namef='MARKNAME1', addr=True):
    r = reader(name); tf = to_wgs(name); out = []
    for sr in r.iterShapeRecords():
        x, y = sr.shape.points[0]
        if tf: x, y = tf.transform(x, y)
        rec = sr.record
        f = {'n': norm_tai(rec[namef]), 'c': [R5(x), R5(y)]}
        if addr and 'ADDRESS' in rec.as_dict() and rec['ADDRESS']: f['a'] = norm_tai(rec['ADDRESS'])
        if 'TEL' in rec.as_dict() and rec['TEL']: f['tel'] = rec['TEL']
        out.append(f)
    return out
T['st_tra'] = points('MARK_臺鐵車站_1140206.shp')
T['st_hsr'] = points('MARK_高鐵車站_1101208.shp')
# 捷運：圖資是「出入口」＋「站體」點位 → 依站名合併成一站（座標取各點平均）
#   ★ 2026-11-08 修正：舊規則認不得「-出入口M4」「_G09_O05」「_O5,R10」這類後綴，
#     臺北車站多出 8 個「站」、古亭／大安等轉乘站各重複一次。
#   站體點的站碼（R05、BL12、Y11、A3、O5,R10、118…）是官方編碼，直接決定所屬路線；
#   沒有站碼的站（貓空纜車）才以「同系統路線 120m 內」判定。
EXIT_RE = re.compile(r'[-_]出入口\s*[A-Z]{0,2}\d*[A-Z]?$')
CODE_RE = re.compile(r'_([A-Z]{0,2}\d+[A-Za-z]?(?:[_,][A-Z]{0,2}\d+[A-Za-z]?)*)$')
SYS_PREFIX = [('桃園機場捷運', '臺灣桃園國際機場捷運'), ('臺北捷運', '臺北捷運'), ('新北捷運', '新北捷運'),
              ('臺中捷運', '臺中捷運'), ('高雄捷運', '高雄捷運'), ('貓空纜車', '臺北捷運')]
def code_line(sy, code):
    pre = re.match(r'[A-Z]*', code).group(0)
    if sy == '臺北捷運':
        if code == 'R22A': return '新北投線'
        if code == 'G03A': return '小碧潭線'
        return {'R': '淡水信義線', 'O': '中和新蘆線', 'BL': '板南線', 'G': '松山新店線', 'BR': '文湖線'}.get(pre)
    if sy == '新北捷運': return {'Y': '環狀線'}.get(pre)
    if sy == '臺灣桃園國際機場捷運': return {'A': '機場捷運'}.get(pre)
    if sy == '高雄捷運': return {'R': '紅線', 'RK': '紅線', 'O': '橘線', 'OT': '橘線'}.get(pre)
    if sy == '臺中捷運': return '綠線' if pre == '' else None
    return None
mrt_raw = points('MARK_捷運車站_1150409.shp')
grp = collections.OrderedDict(); sk_st = collections.Counter(); unk_code = collections.Counter()
for f in mrt_raw:
    if '興建中' in f['n']:     # ★ 與路線 STATUS≠0 不顯示一致（三鶯線 11 站）
        sk_st[re.sub(r'_[A-Z]{1,2}\d+.*$', '', f['n'])] += 1; continue
    nm = EXIT_RE.sub('', f['n']); codes = []
    m = CODE_RE.search(nm)
    if m:
        codes = re.split(r'[_,]', m.group(1)); nm = nm[:m.start()]
    sy, pre = next(((v, k) for k, v in SYS_PREFIX if nm.startswith(k)), ('', ''))
    g = grp.setdefault(nm, {'sys': sy, 'pre': pre, 'pts': [], 'ln': []})
    g['pts'].append(f['c'])
    for c in codes:
        ln = code_line(sy, c)
        if ln and ln not in g['ln']: g['ln'].append(ln)
        elif not ln: unk_code[f'{sy}:{c}'] += 1
if unk_code:
    raise SystemExit(f'!! 站碼對不到路線（請補 code_line，不可猜）：{dict(unk_code)}')
T['not_shown_status']['st_mrt'] = dict(sk_st)
_LN = collections.defaultdict(list)
for sy, nm, g in mrt_s + lrt_s:
    if nm: _LN[(sy, nm)].append(g)
_LN = {k: unary_union(v) for k, v in _LN.items()}
from shapely.geometry import Point
def lines_near(sy, x, y, tol=0.0012):
    p = Point(x, y)
    return [k[1] for d, k in sorted((g.distance(p), k) for k, g in _LN.items()
                                     if k[0] == sy and g.distance(p) <= tol)]
# 同名同址跨系統合併（臺北捷運景安站＋新北捷運景安站＝一個轉乘站）
st_out = []
for nm, g in grp.items():
    x = sum(p[0] for p in g['pts']) / len(g['pts']); y = sum(p[1] for p in g['pts']) / len(g['pts'])
    ln = g['ln'] or lines_near(g['sys'], x, y)
    base = nm[len(g['pre']):] if g['pre'] and g['pre'] != '貓空纜車' else nm
    hit = next((o for o in st_out if o['n'] == base and abs(o['c'][0] - x) < .003 and abs(o['c'][1] - y) < .003), None)
    if hit:
        hit['ln'] += [l for l in ln if l not in hit['ln']]
        if g['sys'] not in hit['sys']: hit['sys'].append(g['sys'])
        hit['exits'] += len(g['pts'])
        continue
    st_out.append({'n': base, 'c': [R5(x), R5(y)], 'exits': len(g['pts']), 'ln': ln, 'sys': [g['sys']]})
T['st_mrt'] = st_out
_noln = [o['n'] for o in st_out if not o['ln']]
if _noln:
    raise SystemExit(f'!! 捷運站無法判定所屬路線：{_noln}')
T['line_color'] = {nm: c for (sy, nm), c in LINE_COLOR.items()}
T['line_color'].update({'臺鐵': TRA_COLOR, '高鐵': HSR_COLOR})
def dedupe(fs):
    # 本島檔與離島檔有重疊（馬祖機場兩檔都有）：同名且相距 < 300m 視為同一筆
    out = []
    for f in fs:
        if any(o['n'] == f['n'] and abs(o['c'][0] - f['c'][0]) < .003 and abs(o['c'][1] - f['c'][1]) < .003 for o in out):
            continue
        out.append(f)
    return out
T['airport'] = dedupe(points('MARK_機場_121_1101208.shp') + points('MARK_機場_119_1101208.shp'))
T['port'] = dedupe(points('MARK_碼頭_121_1150409.shp') + points('MARK_碼頭_119_1120412.shp'))
json.dump(T, open(os.path.join(OUT, 'transport.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
print('交通', {k: len(v) for k, v in T.items() if isinstance(v, list)}, '未顯示（STATUS≠0）', T['not_shown_status'])

# ── 學校 ──
sr_ = reader('各級學校範圍圖_121_1150409.shp'); tf = to_wgs('各級學校範圍圖_121_1150409.shp')
S = []
for sr in sr_.iterShapeRecords():
    g = geom_of(sr, tf); g = g.buffer(0) if not g.is_valid else g
    gs = g.simplify(0.00005, preserve_topology=True)
    pt = gs.representative_point()
    S.append({'n': norm_tai(sr.record['BLOCKNAME']), 'c': [R5(pt.x), R5(pt.y)], 'p': poly_out(gs)})
json.dump({'source': 'NLSC 各級學校範圍圖 1150409', 'feats': S},
          open(os.path.join(OUT, 'schools.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
print('學校', len(S))
for root, _, fs in os.walk(OUT):
    for f in fs:
        p = os.path.join(root, f)
print('總大小 KB', sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(OUT) for f in fs) // 1024)
