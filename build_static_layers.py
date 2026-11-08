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
# ★ 2026-11-08 正式路線名稱與起訖（使用者指定寫清楚）。NLSC 圖資只有簡稱（新北投線、綠線…）。
#   名稱依各營運機構／捷運工程局：臺北捷運支線正式稱「支線」；臺中綠線＝烏日文心北屯線；
#   高雄紅線起訖為小港－岡山車站（南岡山站 2024 年已更名岡山高醫站）。
OFFICIAL = {
    ('臺北捷運', '文湖線'): ('文湖線', '動物園－南港展覽館'),
    ('臺北捷運', '淡水信義線'): ('淡水信義線', '淡水－象山'),
    ('臺北捷運', '新北投線'): ('新北投支線', '北投－新北投'),
    ('臺北捷運', '松山新店線'): ('松山新店線', '新店－松山'),
    ('臺北捷運', '小碧潭線'): ('小碧潭支線', '七張－小碧潭'),
    ('臺北捷運', '中和新蘆線'): ('中和新蘆線', '南勢角－迴龍／蘆洲'),
    ('臺北捷運', '板南線'): ('板南線', '頂埔－南港展覽館'),
    ('臺北捷運', '貓空纜車'): ('貓空纜車', '動物園－貓空'),
    ('新北捷運', '環狀線'): ('環狀線', '大坪林－新北產業園區'),
    ('新北捷運', '安坑輕軌'): ('安坑輕軌', '雙城－十四張'),
    ('新北捷運', '淡海輕軌綠山線'): ('淡海輕軌綠山線', '紅樹林－崁頂'),
    ('新北捷運', '淡海輕軌藍海線'): ('淡海輕軌藍海線', '濱海沙崙－淡水漁人碼頭'),
    ('臺灣桃園國際機場捷運', '機場捷運'): ('桃園機場捷運', '臺北車站－老街溪'),
    ('臺中捷運', '綠線'): ('烏日文心北屯線（綠線）', '北屯總站－高鐵臺中站'),
    ('高雄捷運', '紅線'): ('紅線', '小港－岡山車站'),
    ('高雄捷運', '橘線'): ('橘線', '哈瑪星－大寮'),
    ('高雄捷運', '環狀輕軌'): ('環狀輕軌', '環狀運行'),
}
def official_name(sy, nm):
    return OFFICIAL.get((sy, nm), (nm, ''))[0]

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
# ★ 2026-11-08 查核：未併入路線的未標名捷運軌道共 12 群，全部落在各營運機構的機廠位置
#   （位置＝該群中心點；依各系統機廠清單與 OSM 機廠標示核對）。以機廠名稱標示、用中性灰，
#   不冒充成載客路線。不在任何機廠 1.5km 內的未標名段會讓建置失敗，逼人工查核。
MRT_DEPOTS = [
    ('臺北捷運', 121.4794, 25.1342, '北投機廠'), ('臺北捷運', 121.4866, 25.1368, '北投機廠'),
    ('臺北捷運', 121.5856, 25.0002, '木柵機廠'), ('臺北捷運', 121.4432, 24.9910, '土城機廠'),
    ('臺北捷運', 121.4677, 25.0979, '蘆洲機廠'), ('臺北捷運', 121.5306, 24.9845, '環狀線南機廠'),
    ('臺灣桃園國際機場捷運', 121.2198, 25.0269, '青埔機廠'), ('臺灣桃園國際機場捷運', 121.2715, 25.0824, '蘆竹機廠'),
    ('臺中捷運', 120.7101, 24.1897, '北屯機廠'),
    ('高雄捷運', 120.3027, 22.7759, '北機廠'), ('高雄捷運', 120.3294, 22.5846, '南機廠'),
    ('高雄捷運', 120.3927, 22.6191, '大寮機廠'),
]
def _depot_of(sy, g, tol=0.015):
    c = g.centroid
    best = min(((abs(lo - c.x) + abs(la - c.y), nm) for s_, lo, la, nm in MRT_DEPOTS if s_ == sy),
               default=(9, None))
    return best[1] if best[0] <= tol else None

def attach_unnamed(segs, kind='mrt'):
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
                if kind == 'rail':
                    # 鐵路未標名段：依 100m 內最近的具名路線判定經營者（臺鐵站場／側線 vs 林鐵）
                    near = min(((g.distance(b), k[1]) for k, b in U.items()), default=(9, ''))
                    if near[0] <= 0.001 + ATTACH_BUF:
                        nm = '臺鐵站場／側線' if near[1].startswith('臺鐵') else \
                             ('阿里山林業鐵路（未標名段）' if near[1].startswith('阿里山') else '')
                else:
                    dep = _depot_of(sy, g)
                    if not dep:
                        raise SystemExit(f'!! 未標名捷運軌道不在已知機廠範圍：{sy} {g.centroid}')
                    nm = dep + '（機廠軌道）'
        out.append((sy, nm, g))
    if kind == 'rail':
        # 與已判定的站場段相連（150m 內）的未標名段，沿用同一經營者（同一個站場的其他股道）
        changed = True
        while changed:
            changed = False
            for i, (sy, nm, g) in enumerate(out):
                if nm: continue
                for sy2, nm2, g2 in out:
                    if nm2 in ('臺鐵站場／側線', '阿里山林業鐵路（未標名段）') and g.distance(g2) <= 0.0015:
                        out[i] = (sy, nm2, g); changed = True; break
    return out, n_att, n_left

def merge_lines(segs, color_of):
    m = collections.OrderedDict()
    for sy, nm, g in segs:
        k = (sy, nm)
        on, tm = OFFICIAL.get(k, (nm, ''))
        f = m.setdefault(k, {'n': on, 's': sy, 'c': color_of(sy, nm), 'l': []})
        if tm: f['term'] = tm
        f['l'] += line_out(g.simplify(0.0002, preserve_topology=True))
    return list(m.values())

def tra_color(sy, nm):
    if nm.startswith('臺鐵'): return TRA_COLOR
    return NO_OFFICIAL      # 阿里山林業鐵路（林業保育署）、未標名段：查無官方路線色

status0 = lambda r: int(r['STATUS'] or 0) == 0
rail_s, sk_rail = _seg_records('RAIL_1150409.shp', 'RAILNAME', keep=status0)
rail_s, a1, l1 = attach_unnamed(rail_s, 'rail')
if any(not nm for sy, nm, g in rail_s):
    raise SystemExit('!! 仍有無法判定經營者的未標名鐵路段，請人工查核')
hsr_s, _ = _seg_records('HSRAIL_1101208.shp', 'HSNAME')
mrt_s, sk_mrt = _seg_records('MRT_1150409.shp', 'MRTCODE', 'MRTSYS', keep=status0)
mrt_s, a2, l2 = attach_unnamed(mrt_s)
lrt_s, sk_lrt = _seg_records('LRT_1150409.shp', 'LRTCODE', 'LRTSYS', keep=status0)
lrt_s, a3, l3 = attach_unnamed(lrt_s)
T['rail'] = merge_lines(rail_s, tra_color)
T['hsr'] = merge_lines(hsr_s, lambda sy, nm: HSR_COLOR)
T['mrt'] = merge_lines(mrt_s, lambda sy, nm: LINE_COLOR.get((sy, nm), NO_OFFICIAL))
T['lrt'] = merge_lines(lrt_s, lambda sy, nm: LINE_COLOR.get((sy, nm), NO_OFFICIAL))
for f in T['mrt'] + T['lrt']:
    if f['n'].endswith('（機廠軌道）'): f['depot'] = 1
for f in T['rail']:
    if f['n'] == '臺鐵站場／側線': f['yard'] = 1
T['unnamed_attach'] = {'rail': [a1, l1], 'mrt': [a2, l2], 'lrt': [a3, l3]}
T['not_shown_status'] = {'rail': dict(sk_rail), 'mrt': dict(sk_mrt), 'lrt': dict(sk_lrt)}
_miss = sorted({(sy, nm) for sy, nm, g in mrt_s + lrt_s
                if nm and not nm.endswith('（機廠軌道）') and ((sy, nm) not in LINE_COLOR or (sy, nm) not in OFFICIAL)})
if _miss:
    raise SystemExit(f'!! 具名路線缺官方色或正式名稱（請補 LINE_COLOR／OFFICIAL，不可自編）：{_miss}')

def points(name, namef='MARKNAME1', addr=True):
    r = reader(name); tf = to_wgs(name); out = []
    for sr in r.iterShapeRecords():
        x, y = sr.shape.points[0]
        if tf: x, y = tf.transform(x, y)
        rec = sr.record
        f = {'n': norm_tai(rec[namef]).replace('\xa0', '').strip(), 'c': [R5(x), R5(y)]}
        if addr and 'ADDRESS' in rec.as_dict() and rec['ADDRESS']: f['a'] = norm_tai(rec['ADDRESS'])
        if 'TEL' in rec.as_dict() and rec['TEL']: f['tel'] = rec['TEL'].strip()
        if 'MARKTYPE1' in rec.as_dict(): f['_t'] = (rec['MARKTYPE1'] or '').strip()
        # ★ 2026-11-08 補齊圖資既有欄位：簡稱（MARKNAME2，與全名不同時）、圖資日期（MDATE／UPD_YYYYMM）
        d = rec.as_dict()
        n2 = norm_tai(d.get('MARKNAME2') or '').replace('\xa0', '').strip()
        if n2 and n2 != f['n'] and '出入口' not in n2: f['n2'] = n2
        ym = str(d.get('MDATE') or d.get('UPD_YYYYMM') or '').strip()
        if re.fullmatch(r'\d{6}', ym): f['d'] = ym
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
EXIT_LBL = re.compile(r'[-_](出入口\s*[A-Z]{0,2}\d*[A-Z]?)$')
for f in mrt_raw:
    if '興建中' in f['n']:     # ★ 與路線 STATUS≠0 不顯示一致（三鶯線 11 站）
        sk_st[re.sub(r'_[A-Z]{1,2}\d+.*$', '', f['n'])] += 1; continue
    em = EXIT_LBL.search(f['n'])
    nm = EXIT_RE.sub('', f['n']); codes = []
    m = CODE_RE.search(nm)
    if m:
        codes = re.split(r'[_,]', m.group(1)); nm = nm[:m.start()]
    sy, pre = next(((v, k) for k, v in SYS_PREFIX if nm.startswith(k)), ('', ''))
    g = grp.setdefault(nm, {'sys': sy, 'pre': pre, 'pts': [], 'ln': [], 'ex': [], 'a': '', 'tel': '', 'code': []})
    g['pts'].append(f['c'])
    if f.get('d') and f['d'] > g.get('d', ''): g['d'] = f['d']
    if em:      # ★ 出入口：官方 ADDRESS 欄寫的是「出入口所在位置」（路口／地標），即「幾號出口往哪」
        lbl = re.sub(r'\s+', '', em.group(1)).replace('出入口', '') or '—'
        g['ex'].append([lbl, f.get('a', '')])
        if f.get('tel') and not g['tel']: g['tel'] = f['tel']
    else:       # 站體點：站址、電話、站碼
        if f.get('a'): g['a'] = f['a']
        if f.get('tel'): g['tel'] = f['tel']
        g['code'] += [c for c in codes if c not in g['code']]
    for c in codes:
        ln = code_line(sy, c)
        if ln and official_name(sy, ln) not in g['ln']: g['ln'].append(official_name(sy, ln))
        elif not ln: unk_code[f'{sy}:{c}'] += 1
if unk_code:
    raise SystemExit(f'!! 站碼對不到路線（請補 code_line，不可猜）：{dict(unk_code)}')
T['not_shown_status']['st_mrt'] = dict(sk_st)
_LN = collections.defaultdict(list)
for sy, nm, g in mrt_s + lrt_s:
    if nm and not nm.endswith('（機廠軌道）'): _LN[(sy, nm)].append(g)
_LN = {k: unary_union(v) for k, v in _LN.items()}
from shapely.geometry import Point
def lines_near(sy, x, y, tol=0.0012):
    p = Point(x, y)
    return [official_name(*k) for d, k in sorted((g.distance(p), k) for k, g in _LN.items()
                                                 if k[0] == sy and g.distance(p) <= tol)]
def _exkey(e):
    m = re.match(r'([A-Z]*)(\d*)([A-Z]?)', e[0])
    return (m.group(1), int(m.group(2) or 0), m.group(3)) if m else (e[0], 0, '')
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
        hit['code'] += [c for c in g['code'] if c not in hit['code']]
        hit['ex'] += [e for e in g['ex'] if e not in hit['ex']]
        if not hit.get('a') and g['a']: hit['a'] = g['a']
        if not hit.get('tel') and g['tel']: hit['tel'] = g['tel']
        if g.get('d') and g['d'] > hit.get('d', ''): hit['d'] = g['d']
        continue
    o = {'n': base, 'c': [R5(x), R5(y)], 'ln': ln, 'sys': [g['sys']], 'code': g['code'], 'ex': g['ex']}
    if g.get('d'): o['d'] = g['d']
    if g['a']: o['a'] = g['a']
    if g['tel']: o['tel'] = g['tel']
    st_out.append(o)
for o in st_out:
    o['ex'] = sorted(o['ex'], key=_exkey)
T['st_mrt'] = st_out
_noln = [o['n'] for o in st_out if not o['ln']]
if _noln:
    raise SystemExit(f'!! 捷運站無法判定所屬路線：{_noln}')
T['line_color'] = {official_name(sy, nm): c for (sy, nm), c in LINE_COLOR.items()}
T['line_color'].update({'臺鐵': TRA_COLOR, '高鐵': HSR_COLOR})
T['line_term'] = {v[0]: v[1] for v in OFFICIAL.values()}

# ★ 臺鐵車站所屬路線：150m 內通過的具名臺鐵路線（站場／側線不算）
_RL = collections.defaultdict(list)
for sy, nm, g in rail_s:
    if nm and nm not in ('臺鐵站場／側線', '阿里山林業鐵路（未標名段）'): _RL[nm].append(g)
_RL = {k: unary_union(v) for k, v in _RL.items()}
_sk = [f['n'] for f in T['st_tra'] if '興建中' in f['n']]      # 與路線一致：興建中不顯示
T['st_tra'] = [f for f in T['st_tra'] if '興建中' not in f['n']]
T['not_shown_status']['st_tra'] = _sk
for f in T['st_tra']:
    if f['n'].startswith('阿里山林業鐵路'): f['op'] = 'forest'
    p = Point(*f['c'])
    f['ln'] = [k for d, k in sorted((g.distance(p), k) for k, g in _RL.items() if g.distance(p) <= 0.0015)]
    f.pop('_t', None)
for f in T['st_hsr']: f.pop('_t', None)
def dedupe(fs):
    # 本島檔與離島檔有重疊（馬祖機場兩檔都有）：同名且相距 < 300m 視為同一筆
    out = []
    for f in fs:
        if any(o['n'] == f['n'] and abs(o['c'][0] - f['c'][0]) < .003 and abs(o['c'][1] - f['c'][1]) < .003 for o in out):
            continue
        out.append(f)
    return out
T['airport'] = dedupe(points('MARK_機場_121_1101208.shp') + points('MARK_機場_119_1101208.shp'))
for f in T['airport']: f.pop('_t', None)
T['port'] = dedupe(points('MARK_碼頭_121_1150409.shp') + points('MARK_碼頭_119_1120412.shp'))
# ★ 港口類別：NLSC 地標分類碼（9960400 港口、400a 遊艇港／觀光碼頭、401 商港、402 漁港、403 工業專用港）
PORT_TYPE = {'9960400': '港口', '9960400a': '遊艇港／觀光碼頭', '9960401': '商港',
             '9960402': '漁港', '9960403': '工業專用港'}
_pt_unk = collections.Counter()
for f in T['port']:
    t = f.pop('_t', '')
    if t in PORT_TYPE: f['k'] = PORT_TYPE[t]
    else: _pt_unk[t] += 1
if _pt_unk:
    raise SystemExit(f'!! 港口分類碼未對照：{dict(_pt_unk)}')
json.dump(T, open(os.path.join(OUT, 'transport.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
print('交通', {k: len(v) for k, v in T.items() if isinstance(v, list)}, '未顯示（STATUS≠0）', T['not_shown_status'])

# ── 學校 ──
def school_kind(nm):
    for keys, k in ((('幼兒園', '幼稚園'), '幼兒園'), (('國民中小學',), '國中小'),
                    (('國民小學', '國小', '實驗小學'), '國小'),
                    (('國民中學', '國中'), '國中'),
                    (('完全中學', '高級中學', '高級中等', '高中', '高級職業', '高職', '高級工業', '高級商業',
                      '高級農業', '高級家事', '高級海事', '高工', '高商', '高農', '高家', '職業學校'), '高中職'),
                    (('特殊教育', '啟智', '啟聰', '啟明', '特教'), '特殊教育學校'),
                    (('大學', '學院', '專科學校'), '大專校院')):
        if any(x in nm for x in keys):
            return k
    return '其他'
sr_ = reader('各級學校範圍圖_121_1150409.shp'); tf = to_wgs('各級學校範圍圖_121_1150409.shp')
S = []
for sr in sr_.iterShapeRecords():
    g = geom_of(sr, tf); g = g.buffer(0) if not g.is_valid else g
    gs = g.simplify(0.00005, preserve_topology=True)
    pt = gs.representative_point()
    # 同校多筆範圍在圖資裡以「校名1、校名2」區分，顯示時去掉流水號
    nm = re.sub(r'(?<=[校學部院園區小中])\d+$', '', norm_tai(sr.record['BLOCKNAME']))
    # ★ 學制由正式校名判斷（圖資只有校名）；校地面積由範圍多邊形計算（TWD97 公尺座標）
    ha = shape(sr.shape.__geo_interface__).area / 1e4 if tf is not None else None
    S.append({'n': nm, 'c': [R5(pt.x), R5(pt.y)], 'p': poly_out(gs), 'k': school_kind(nm),
              **({'ha': round(ha, 2)} if ha else {})})
# ★ 2026-11-08 補齊：所在縣市鄉鎮（代表點落在哪個鄉鎮界內，與雨量聚合同一份 town_polys.json）、
#   圖資日期（YYYYMM）、同校多筆範圍（同名）的筆數與全校合計面積。
_TP = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'town_polys.json')
_tpolys = []
if os.path.exists(_TP):
    for k, v in json.load(open(_TP, encoding='utf-8')).items():
        try:
            _tpolys.append((k, v[:4], unary_union([Polygon(r).buffer(0) for r in v[4] if len(r) >= 3])))
        except Exception as e:
            raise SystemExit(f'!! 鄉鎮界 {k} 無法建立多邊形：{e}')
def _town_of(x, y):
    p = Point(x, y)
    for k, bb, g in _tpolys:
        if bb[0] <= x <= bb[2] and bb[1] <= y <= bb[3] and g.contains(p):
            return k
    # 代表點落在鄉鎮界外（海岸、界線簡化誤差）：取 1km 內最近的鄉鎮；再遠就不填，不猜
    best = min(((g.distance(p), k) for k, bb, g in _tpolys
                if bb[0] - .02 <= x <= bb[2] + .02 and bb[1] - .02 <= y <= bb[3] + .02), default=(9, None))
    return best[1] if best[0] <= 0.01 else None
_ym = [str(sr.record['YYYYMM'] or '').strip() for sr in reader('各級學校範圍圖_121_1150409.shp').iterShapeRecords()]
_cnt = collections.Counter(f['n'] for f in S); _tot = collections.defaultdict(float)
for f in S: _tot[f['n']] += f.get('ha') or 0
_no_town = 0
for f, ym in zip(S, _ym):
    tw = _town_of(*f['c'])
    if not tw:   # 界外且 1km 內無鄉鎮：校名本身以「縣市＋鄉鎮」開頭者（官方校名），採校名
        tw = next((k for k, bb, g in _tpolys if f['n'].startswith(k)), None)
    if tw: f['tw'] = tw
    else: _no_town += 1
    if re.fullmatch(r'\d{6}', ym): f['d'] = ym
    if _cnt[f['n']] > 1:
        f['np'] = _cnt[f['n']]; f['tot'] = round(_tot[f['n']], 2)
print('學校：鄉鎮定位', len(S) - _no_town, '/', len(S), '；多筆範圍校數', sum(1 for v in _cnt.values() if v > 1))
json.dump({'source': 'NLSC 各級學校範圍圖 1150409', 'feats': S},
          open(os.path.join(OUT, 'schools.json'), 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
print('學校', len(S))
for root, _, fs in os.walk(OUT):
    for f in fs:
        p = os.path.join(root, f)
print('總大小 KB', sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(OUT) for f in fs) // 1024)
