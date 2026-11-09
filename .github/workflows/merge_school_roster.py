"""教育部 115 學年各級學校名錄 → 併入 schools.json（公私立、地址、電話、網址、學校代碼）。
   國土測繪中心校地範圍沒有這些欄位；以「縣市＋校名核心＋學制」對應，重名再以鄉鎮區分。
   對不上的不硬配：留在 school_roster_unmatched.csv 供人工確認。"""
import os, re, sys, json, csv, glob, collections
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, 'opendata', '全臺各級學校清冊')
SCH = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, 'layers', 'schools.json')
FILES = {'e1_new.xlsx': '國小', 'j1_new.xlsx': '國中', 'high.xlsx': '高中職', 'u1_new.xlsx': '大專',
         'sp1_new.xlsx': '特教', 'aj_new.xlsx': '國中', 'ac.xlsx': '國小'}
norm = lambda s: str(s or '').replace('台', '臺').replace('　', '').replace(' ', '').strip()
CTYS = '臺北市 新北市 桃園市 臺中市 臺南市 高雄市 基隆市 新竹市 嘉義市 新竹縣 苗栗縣 彰化縣 南投縣 雲林縣 嘉義縣 屏東縣 宜蘭縣 花蓮縣 臺東縣 澎湖縣 金門縣 連江縣'.split()
TOWNS = json.load(open(os.path.join(HERE, 'town_polys.json'), encoding='utf-8')).keys()
TOWN_OF = collections.defaultdict(list)
for k in TOWNS: TOWN_OF[k[:3]].append(k[3:])
def split_town(addr_or_name):
    """「縣市＋鄉鎮」前綴（以官方鄉鎮名單比對，不靠字尾猜）→ (縣市, 鄉鎮, 其餘)。"""
    s = norm(addr_or_name)
    for c in CTYS:
        for pre in (c + '立', c):
            if s.startswith(pre):
                rest = s[len(pre):]
                for t in sorted(TOWN_OF.get(c, []), key=len, reverse=True):
                    if rest.startswith(t):
                        return c, t, rest[len(t):]
                return c, '', rest
    return '', '', s
# 學制：高中與高職要分開（內湖高中／內湖高工、豐原高中／豐原高商 同縣市同核心名）
LV = [(r'(國民中小學|國中小|國\(中\)小|國中\(小\))', '中小'), (r'(國民小學|國小|實驗小學|實小|小學)$', '小'),
      (r'(國民中學|國中)$', '中'),
      (r'(職業學校|高工|高商|高農|高家|工商|家商|農工|工家|商工|海事|護校|高職)', '職'),
      (r'(完全中學|高級中等學校|高級中學|高中|實驗學校)', '高'),
      (r'(大學|學院|專科學校)', '大'), (r'(特殊教育學校|啟[智聰明]學校)', '特')]
def level(n):
    n = re.sub(r'[（(](?!中|小).*?[)）]', '', norm(n))
    for pat, lv in LV:
        if re.search(pat, n): return lv
    return '?'
def core(n):
    c, t, rest = split_town(n)
    n = re.sub(r'^(國立|市立|縣立|私立|直轄市立|財團法人)', '', rest)
    n = re.sub(r'[（(].*?[)）]', '', n)
    n = re.sub(r'(實驗)?(國民中小學|國民小學|國民中學|國中小|國小|國中|小學|實小|高級中等學校|高級中學|高中|完全中學|'
               r'高級.{0,6}職業學校|職業學校|高工|高商|高農|高家|工商|家商|農工|商工|海事|大學|科技大學|學院|專科學校|特殊教育學校|中學)$', '', n)
    return n
rows = []
for fn, kind in FILES.items():
    p = os.path.join(SRC, fn)
    if not os.path.exists(p): continue
    df = pd.read_excel(p, header=None)
    hdr = next(i for i in range(6) if '學校名稱' in [str(x) for x in df.iloc[i].tolist()])
    df.columns = [str(x) for x in df.iloc[hdr]]; df = df.iloc[hdr + 1:]
    for r in df.to_dict('records'):
        n = norm(r.get('學校名稱'))
        if not n or n == 'nan': continue
        cty = re.sub(r'^\[\d+\]', '', norm(r.get('縣市名稱')))
        if cty not in CTYS:   # 大專名錄縣市欄位拆兩欄
            cty = next((c for c in CTYS if c in norm(r.get('nan')) or c in norm(r.get('地址'))), cty)
        addr = re.sub(r'^\[\d+\]', '', norm(r.get('地址')))
        _c, _t, _ = split_town(addr)
        rows.append({'code': norm(r.get('代碼')), 'n': n, 'pub': norm(r.get('公/私立')), 'cty': cty,
                     'tw': _t, 'a': addr, 'tel': norm(r.get('電話')),
                     'url': norm(r.get('網址')) if norm(r.get('網址')) not in ('', 'nan') else '', 'kind': kind})
KIND_LV = {'國小': '小', '國中': '中', '高中職': '高', '大專': '大', '特教': '特'}
def level_r(r):   # 名錄校名看不出學制（如「市立建國中學」）→ 用所屬名錄別
    lv = level(r['n']); return lv if lv != '?' else KIND_LV.get(r['kind'], '?')
idx = collections.defaultdict(list)
for r in rows: idx[(r['cty'], core(r['n']), level_r(r))].append(r)
# ★ 驗證：校地位置必須在名錄地址所屬鄉鎮 3 公里內，否則視為配錯（同名不同校、校區不同），不採用
from shapely.geometry import Point, Polygon
from shapely.ops import unary_union
_TP = json.load(open(os.path.join(HERE, 'town_polys.json'), encoding='utf-8'))
_G = {}
def _near(f, r):
    k = r['cty'] + r['tw']
    if k not in _TP: return True          # 名錄地址無鄉鎮可比 → 不擋
    if k not in _G: _G[k] = unary_union([Polygon(x).buffer(0) for x in _TP[k][4] if len(x) >= 3])
    return _G[k].distance(Point(*f['c'])) * 101 <= 3
far = []
S = json.load(open(SCH, encoding='utf-8'))
hit = amb = 0; used = set(); miss = []
for f in S['feats']:
    tw = f.get('tw') or ''
    _nc = split_town(f['n'])[0]
    key = (_nc or tw[:3], core(f['n']), level(f['n']))
    cand = idx.get(key, [])
    if len(cand) > 1:
        c2 = [c for c in cand if c['tw'] and tw.endswith(c['tw'])]
        cand = c2 if len(c2) == 1 else ([] if len(c2) != 1 else c2)
        if not cand: amb += 1
    if len(cand) == 1 and not _near(f, cand[0]):
        far.append((f['n'], cand[0]['n'], cand[0]['cty'] + cand[0]['tw'])); cand = []
    if len(cand) == 1:
        r = cand[0]; used.add(id(r)); hit += 1
        f.update({'code': r['code'], 'pub': r['pub'], 'a': r['a']})
        if r['tel']: f['tel'] = r['tel']
        if r['url']: f['url'] = r['url']
        f['nm'] = r['n']          # 教育部名錄校名（一般慣稱）
        # ★ 所在鄉鎮以名錄地址為準（官方登記地址）；代表點落在界線附近時判定不可靠
        if r['cty'] and r['tw']:
            f['tw'] = r['cty'] + r['tw']
    else:
        for k in ('code', 'pub', 'a', 'tel', 'url', 'nm'): f.pop(k, None)
        miss.append(f['n'])
S['roster'] = {'source': '教育部 115 學年度各級學校名錄', 'matched': hit, 'total': len(S['feats'])}
json.dump(S, open(SCH, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
un = [r for r in rows if id(r) not in used]
with open(os.path.join(HERE, 'school_roster_unmatched.csv'), 'w', encoding='utf-8-sig', newline='') as fh:
    w = csv.writer(fh); w.writerow(['來源', '代碼', '校名', '縣市', '地址', '說明'])
    for r in un: w.writerow(['教育部名錄', r['code'], r['n'], r['cty'], r['a'], '圖資無對應校地（多為澎湖、金門、馬祖，或校地圖資未收錄）'])
    for n in sorted(set(miss) - {x[0] for x in far}): w.writerow(['國土測繪校地', '', n, '', '', '名錄對不上（含非學校單位、分校或名稱差異）'])
    for a, b, t in far: w.writerow(['國土測繪校地', '', a, '', '', f'名稱對上「{b}」但名錄地址在{t}、相距 3 公里以上 → 不採用'])
print(f'校地 {len(S["feats"])} 筆中 {hit} 筆對上名錄（重名無法區分 {amb}、位置不符剔除 {len(far)}）；名錄 {len(rows)} 校未對上 {len(un)}')
print('未對上名錄（各縣市）', collections.Counter(r['cty'] for r in un).most_common(8))
