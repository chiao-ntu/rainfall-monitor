#!/usr/bin/env python3
"""臺北捷運出入口官方資料併入 transport.json 的捷運車站（st_mrt）。

來源（data.gov.tw，臺北大眾捷運公司）：
  131731 臺北捷運車站出入口資料服務      出口名稱、位置說明、樓梯／電扶梯／電梯數
  128428 臺北捷運車站出入口座標           各出口是否為無障礙用
  128414 臺北捷運車站出入口無障礙電梯、無障礙坡道 GPS 座標
         臺北捷運車站無障礙電梯位置         各站出口電梯、月臺電梯位置文字

對應規則（不猜測）：
  - 車站以官方出口名稱裡的站名對到 st_mrt 站名（台→臺、褔→福為官方資料錯字修正），
    並用站碼交叉檢查；站名對不到的整筆記入 mrt_exit_unmatched.csv，不併入。
  - 出口以（站, 出口編號）合併；資料服務的「0」＝單一出口。四份資料出口分法不同
    （例：明德站資料服務只有一筆單一出口，座標資料有 1～3 號出口），照各自官方內容並列，不互相推論。
  - 有資料服務的站只用這份官方出口資料；沒有的站（環狀線等）保留國土測繪原有出口位置文字，
    並補上座標資料的無障礙註記。
  - 原有 ex 欄位不動，新欄位 exo / elv / exd；可重複執行。

用法：python3 merge_mrt_exits.py <資料夾> <transport.json>
"""
import csv, io, json, re, sys, os, collections

TP_SYS = {'臺北捷運', '新北捷運'}
F_SVC = '臺北捷運車站出入口資料服務'
F_CRD = '臺北捷運車站出入口座標'
F_ACC = '臺北捷運車站出入口無障礙電梯、無障礙坡道GPS座標'
F_ELV = '臺北捷運車站無障礙電梯位置'


def rd(path):
    b = open(path, 'rb').read()
    for enc in ('utf-8-sig', 'cp950'):
        try:
            return list(csv.DictReader(io.StringIO(b.decode(enc))))
        except UnicodeDecodeError:
            pass
    raise SystemExit(f'無法判讀編碼：{path}')


def find(folder, stem):
    hits = [f for f in os.listdir(folder) if f.startswith(stem) and f.endswith('.csv')]
    if len(hits) != 1:
        raise SystemExit(f'{stem}：找到 {len(hits)} 個檔案 {hits}')
    return os.path.join(folder, hits[0])


def norm_name(s):
    s = s.strip().strip("'").replace('台', '臺').replace('褔', '福')
    return s[:-1] if s.endswith('站') else s


def norm_code(c):
    m = re.match(r'^([A-Z]+)0*(\d+)([A-Z]?)$', c.strip().strip("'"))
    return f'{m[1]}{int(m[2])}{m[3]}' if m else c.strip().strip("'")


def norm_exit(e):
    e = e.strip().strip("'")
    e = re.sub(r'^出口', '', e)
    if e in ('', '0', '單一出口', '—'):
        return '0'
    return e


def split_exit_name(name):
    """'頂埔站出口1' → ('頂埔', '1')；'台北車站M5' → ('臺北車', 'M5')；'中山國中站出口' → ('中山國中', '0')"""
    name = name.strip().strip("'")
    m = re.match(r'^(.+?站)(?:出口(\w*)|(M\d+))$', name)
    if not m:
        return None, None
    return norm_name(m[1]), norm_exit(m[2] if m[2] is not None else (m[3] or ''))


def ymd(s):
    s = (s or '').strip()
    return s if re.fullmatch(r'\d{8}', s) else ''


def main(folder, tpath):
    T = json.load(open(tpath, encoding='utf-8'))
    st = [s for s in T['st_mrt'] if set(s.get('sys', [])) & TP_SYS]
    by_name = {norm_name(s['n']): s for s in st}
    by_code = collections.defaultdict(set)
    for s in st:
        for c in s.get('code', []):
            by_code[norm_code(c)].add(norm_name(s['n']))

    rej = []
    exits = collections.defaultdict(dict)        # 站名 → 出口編號 → dict
    svc_st = set()
    dates = collections.defaultdict(set)

    def row(stn, eid):
        return exits[stn].setdefault(eid, {'i': eid})

    # 1) 出入口資料服務
    for r in rd(find(folder, F_SVC)):
        m = re.match(r"^'?\{'([^']*)'", r['ExitName'])
        zh = m[1] if m else ''
        stn, eid = split_exit_name(zh)
        codes = [norm_code(c) for c in r['StationID'].strip("'").split(',')]
        if stn not in by_name:
            rej.append((F_SVC, r['StationID'], zh, '站名不在國土測繪捷運車站圖資內'))
            continue
        cs = set().union(*[by_code.get(c, set()) for c in codes])
        if stn not in cs:
            # 站名明確、站碼不一致（例：小碧潭站資料寫 G01A，官方站碼 G03A）→ 以站名為準並留紀錄
            rej.append((F_SVC, r['StationID'], zh, f'站碼與圖資不符，已依站名併入「{by_name[stn]["n"]}」'))
        eid = norm_exit(r['ExitID']) if r['ExitID'].strip() else eid
        o = row(stn, eid)
        o['l'] = r['LocationDescription'].strip().strip("'")
        for k, col in (('t', 'Stair'), ('s', 'Escalator'), ('e', 'Elevator')):
            v = r[col].strip()
            if v.isdigit():
                o[k] = int(v)
        svc_st.add(stn)
        if ymd(r.get('UpdateTime')):
            dates['svc'].add(r['UpdateTime'].strip())

    # 2) 出入口座標：是否為無障礙用
    crd_path = find(folder, F_CRD)
    m = re.search(r'_(\d{7})', os.path.basename(crd_path))
    if m:  # 民國 1150804 → 20260804
        dates['crd'].add(f'{int(m[1][:3]) + 1911}{m[1][3:]}')
    for r in rd(crd_path):
        stn, eid = split_exit_name(r['出入口名稱'])
        if stn not in by_name:
            rej.append((F_CRD, '', r['出入口名稱'], '站名不在國土測繪捷運車站圖資內'))
            continue
        flag = r['是否為無障礙用'].strip()
        if flag not in ('是', '否'):
            rej.append((F_CRD, '', r['出入口名稱'], f'無障礙欄位值無法判讀：{flag}'))
            continue
        row(stn, eid)['w'] = 1 if flag == '是' else 0

    # 3) 無障礙電梯、坡道
    for r in rd(find(folder, F_ACC)):
        nm = r['出入口電梯/無障礙坡道名稱'].strip()
        m = re.match(r'^(.+?站)', nm)
        stn = norm_name(m[1]) if m else None
        if stn not in by_name:
            rej.append((F_ACC, '', nm, '站名不在國土測繪捷運車站圖資內'))
            continue
        kind = '電梯' if '電梯' in nm else ('坡道' if '坡道' in nm else '')
        eid = norm_exit(r['出入口編號'])
        if not kind:
            # 官方名稱未註明（例：「士林站出口」），只知道該出口有無障礙電梯或坡道其中之一
            kind = '電梯或坡道'
            rej.append((F_ACC, '', nm, '名稱未註明電梯或坡道，已標為「無障礙電梯或坡道」'))
        if eid not in exits[stn] and eid == '0' and len(exits[stn]) > 0:
            # 「單一出口」但其他資料把這站分成多個出口 → 掛在站層級，不指定到某個出口
            by_name[stn].setdefault('_acc_st', set()).add(kind)
            continue
        o = row(stn, eid)
        o['r'] = '、'.join(sorted(set(filter(None, (o.get('r', '').split('、') + [kind])))))

    # 4) 無障礙電梯位置（站層級文字）
    elv = {}
    for r in rd(find(folder, F_ELV)):
        stn = norm_name(r['車站名稱'])
        if stn not in by_name:
            rej.append((F_ELV, r['車站編號'], r['車站名稱'], '站名不在國土測繪捷運車站圖資內'))
            continue
        txt = r['電梯位置'].strip()
        if ymd(r.get('更新日期')):
            dates['elv'].add(r['更新日期'].strip())
        if txt:
            # 轉乘站兩條線各一列、內容常相同 → 去重
            elv.setdefault(stn, [])
            if txt not in elv[stn]:
                elv[stn].append(txt)

    # 寫回
    n_st = n_ex = 0
    order = lambda k: (0, 0, '') if k == '0' else ((1, int(re.match(r'\d+', k)[0]), k) if re.match(r'\d', k) else (2, 0, k))
    for s in T['st_mrt']:
        for k in ('exo', 'elv', 'exd', 'exa'):
            s.pop(k, None)
    for stn, s in by_name.items():
        ex = exits.get(stn)
        if ex:
            if stn not in svc_st:
                # 沒有資料服務的站：位置文字沿用國土測繪原有出口資料（同一出口編號才帶入）
                for e in s.get('ex', []):
                    eid = norm_exit(e[0])
                    if eid in ex and e[1] and 'l' not in ex[eid]:
                        ex[eid]['l'] = e[1]
                        ex[eid]['ls'] = 'nlsc'
            s['exo'] = [ex[k] for k in sorted(ex, key=order)]
            n_st += 1
            n_ex += len(ex)
        if stn in elv:
            s['elv'] = elv[stn]
        a = s.pop('_acc_st', None)
        if a:
            s['exa'] = '、'.join(sorted(a))
        d = sorted(dates['svc'] if stn in svc_st else dates['crd'])
        if ex and d:
            s['exd'] = d[-1]
    T.setdefault('source', {})
    if isinstance(T['source'], dict):
        T['source']['mrt_exit'] = '臺北大眾捷運公司 出入口資料服務／出入口座標／無障礙電梯坡道（data.gov.tw 131731、128428、128414）'
    json.dump(T, open(tpath, 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))

    out = os.path.join(os.path.dirname(os.path.abspath(tpath)), '..', 'mrt_exit_unmatched.csv')
    out = os.path.normpath(out)
    with open(out, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f)
        w.writerow(['來源檔', '站碼', '名稱', '說明'])
        w.writerows(rej)
    no = [s['n'] for s in by_name.values() if 'exo' not in s]
    print(f'併入 {n_st} 站、{n_ex} 個出口；未對上 {len(rej)} 筆 → {out}')
    print('無官方出口資料的站：', '、'.join(no))
    return T, rej


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
