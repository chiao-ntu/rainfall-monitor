#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
雷達1h QPF 每小時更新腳本（輕量，供 GitHub Actions 每小時執行）
抓 F-B0046（未來1h雷達定量降雨預報，~1.4km格點）→ 取各鄉鎮最近格點值 → 寫 radar.json
前端載入時併入。與主腳本 fetch_rainfall.py（6h）分寫不同檔，避免競態。
（原 QPESUMS 觀測補值 O-A0038 已於 2026-07 停用：CWA 該 dataid 改回傳溫度圖，
  opendata 未提供校正後 QPE 雨量網格；無測站鄉鎮改以雨量站聚合＋模式為準。）
"""
import os, json, requests, time, re
from datetime import datetime, timezone, timedelta

CWA_API_KEY  = os.environ.get('CWA_API_KEY', '')
BASE_URL     = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
# 網格「檔案型」產品走 fileapi（datastore 會 404）
QPESUMS_URL  = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/O-A0038-001"
# F-B0046 未來1h雷達QPF（每10分更新，走 fileapi）——併入本每小時腳本以提升即時性
FB0046_URL   = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-B0046-001"
DATA_FILE    = "data.json"
RADAR_FILE   = "radar.json"   # 雷達獨立檔（避免與主腳本搶寫 data.json）
HIST_FILE    = "qpesums_history.json"
TOWNS_FILE   = "all_townships.json"
# ── 逐時雨量快照（解鎖近2h/近3h、降雨無減緩、動態調降、解除/再發布判定）──
#   本腳本每小時第12分執行，是全系統唯一的逐時序列來源。
#   主腳本 fetch_rainfall.py 只讀不寫；兩支各寫各檔，永不搶寫。
HOURLY_FILE   = "rain_hourly.json"
OBS_URL       = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001"
SWCB_RAIN_URL = "https://246.ardswc.gov.tw/webService/GetDebrisRainData.ashx"
KEEP_SERIES_HOURS = 168   # 保留7日＝官方有效累積雨量的前期降雨時段（α=0.7、7日）
# 網格參數（與 fetch_rainfall.py 保持一致）
QP_LON0, QP_LAT0, QP_D, QP_NX, QP_NY = 118.0, 20.0, 0.0125, 441, 561
KEEP_HOURS   = 50   # 保留50小時（過去48h逐時觀測+24h合成餘裕）


def fetch_grid():
    """
    二段式抓取：
    1. fileapi 取後設資料（dataset.GeoInfo 網格定義 + dataset.Resource.ProductURL）
    2. 下載 ProductURL 實際網格檔（自動判斷 zip/gzip/純文字）
    回傳 values(list) 或 None；同時動態更新網格參數。
    """
    global QP_LON0, QP_LAT0, QP_D, QP_NX, QP_NY
    r = requests.get(QPESUMS_URL, params={'Authorization': CWA_API_KEY,
                                          'downloadType': 'WEB', 'format': 'JSON'}, timeout=90)
    r.raise_for_status()
    raw = r.json()
    ds = raw.get('cwaopendata', {}).get('dataset', {})
    geo = ds.get('GeoInfo', {}) or {}
    res = ds.get('Resource', {}) or {}
    print(f"ObsTime: {json.dumps(ds.get('ObsTime',''), ensure_ascii=False)[:120]}")
    print(f"GeoInfo: {json.dumps(geo, ensure_ascii=False)[:400]}")
    print(f"Resource: {json.dumps(res, ensure_ascii=False)[:300]}")

    # 動態網格參數（GeoInfo 欄位命名有多種變體，逐一嘗試）
    def _num(d, *names):
        for n in names:
            v = d.get(n)
            if v is not None:
                try: return float(v)
                except (ValueError, TypeError): pass
        return None
    lon0 = _num(geo, 'BottomLeftLongitude', 'LowerLeftLongitude', 'MinLongitude')
    lat0 = _num(geo, 'BottomLeftLatitude',  'LowerLeftLatitude',  'MinLatitude')
    dres = _num(geo, 'GridResolution', 'Resolution', 'CellSize')
    nx   = _num(geo, 'GridDimensionX', 'NumberOfColumns', 'Columns', 'Nx')
    ny   = _num(geo, 'GridDimensionY', 'NumberOfRows', 'Rows', 'Ny')
    if lon0 is not None: QP_LON0 = lon0
    if lat0 is not None: QP_LAT0 = lat0
    if dres is not None and dres > 0: QP_D = dres
    if nx: QP_NX = int(nx)
    if ny: QP_NY = int(ny)
    print(f"網格參數: lon0={QP_LON0} lat0={QP_LAT0} d={QP_D} {QP_NX}x{QP_NY}")

    # ── 修復 v6.1：優先嘗試「內嵌網格」（同 fetch_rainfall.py，7/20 ProductURL 事件）──
    def _longest_str(o, best=''):
        if isinstance(o, dict):
            for v in o.values(): best = _longest_str(v, best)
        elif isinstance(o, list):
            for v in o: best = _longest_str(v, best)
        elif isinstance(o, str) and len(o) > len(best):
            best = o
        return best
    blob = _longest_str(ds)
    if blob and len(blob) > 100000:
        vals = []
        for tok in blob.replace(',', ' ').split():
            try: v = float(tok)
            except ValueError: continue
            vals.append(None if v < 0 else v)
        print(f"內嵌網格：{len(vals)} 值（期望 {QP_NX*QP_NY}）")
        if QP_NX*QP_NY*0.9 <= len(vals) <= QP_NX*QP_NY:
            return vals
        if len(vals) > QP_NX*QP_NY:
            return vals[-QP_NX*QP_NY:]

    # ProductURL（可能是 dict / list / 直接字串）
    if isinstance(res, list):
        res = res[0] if res else {}
    url = None
    if isinstance(res, dict):
        url = res.get('ProductURL') or res.get('productUrl') or res.get('uri') or res.get('URI')
    elif isinstance(res, str):
        url = res
    if not url:
        print("找不到 ProductURL")
        return None
    print(f"下載: {url}")

    r2 = requests.get(url, timeout=120)
    r2.raise_for_status()
    data = r2.content
    text = None
    if data[:2] == b'PK':          # zip
        import zipfile, io
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = z.namelist()[0]
            print(f"zip 內容: {z.namelist()[:3]}")
            text = z.read(name).decode('utf-8', errors='replace')
    elif data[:2] == b'\x1f\x8b':  # gzip
        import gzip as _gz
        text = _gz.decompress(data).decode('utf-8', errors='replace')
    else:
        text = data.decode('utf-8', errors='replace')
    print(f"內容開頭: {text[:200]!r}")

    # 解析：抓出所有數值（逗號/空白/換行分隔皆可；跳過非數值行）
    vals = []
    for tok in text.replace(',', ' ').split():
        try:
            v = float(tok)
        except ValueError:
            continue
        vals.append(None if v < 0 else v)
    print(f"網格：{len(vals)} 值（期望 {QP_NX*QP_NY}）")
    if len(vals) < QP_NX*QP_NY*0.9:
        return None
    # 若解析出的值比預期多（檔案含座標欄），只取尾端網格段長度
    if len(vals) > QP_NX*QP_NY:
        print(f"  值多於網格數，可能含座標欄——保守起見放棄本次（貼log給開發者調整）")
        return None
    return vals

def grid_at(vals, lat, lng):
    ix = round((lng - QP_LON0) / QP_D)
    iy = round((lat - QP_LAT0) / QP_D)
    if ix < 0 or ix >= QP_NX or iy < 0 or iy >= QP_NY:
        return None
    idx = iy * QP_NX + ix
    return vals[idx] if idx < len(vals) else None


def fetch_radar_qpf_1h(townships):
    """抓 F-B0046 未來1h雷達QPF格點 → 取各鄉鎮最近格點值。
    回傳 (town_vals: {county+township: mm}, datetime_str)；失敗回 ({}, '')。"""
    print("抓取 F-B0046 未來1h雷達定量降雨預報...")
    doc = None
    for attempt in range(3):
        try:
            r = requests.get(FB0046_URL, params={'Authorization': CWA_API_KEY,
                             'downloadType': 'WEB', 'format': 'JSON'}, timeout=60)
            if r.status_code != 200:
                print(f"    HTTP {r.status_code}")
                if attempt < 2: time.sleep(3); continue
                return {}, ''
            doc = json.loads(r.content.decode('utf-8', 'replace'))
            break
        except Exception as e:
            print(f"    失敗（{attempt+1}/3）：{e}")
            if attempt == 2: return {}, ''
            time.sleep(3)
    if doc is None:
        return {}, ''
    try:
        root = doc.get('cwaopendata', doc)
        ds = root.get('dataset') or root.get('Dataset') or {}
        info = ds.get('datasetInfo') or ds.get('DatasetInfo') or {}
        ps = info.get('parameterSet') or info.get('ParameterSet') or {}
        lon0 = float(ps.get('StartPointLongitude', 118.0))
        lat0 = float(ps.get('StartPointLatitude', 20.0))
        res  = float(ps.get('GridResolution', 0.0125))
        nx   = int(ps.get('GridDimensionX', 441))
        ny   = int(ps.get('GridDimensionY', 561))
        dtstr = ps.get('DateTime', '')
        conts = ds.get('contents') or ds.get('Contents') or {}
        content = conts.get('content') or conts.get('Content') or ''
        if isinstance(content, dict):
            content = content.get('#text') or content.get('ContentText') or ''
        nums = re.findall(r'-?\d+\.?\d*[Ee][+-]?\d+', str(content))
        vals = [float(x) for x in nums]
        if len(vals) < nx*ny*0.5:
            print(f"    格點數異常：{len(vals)}（期望 {nx*ny}）"); return {}, dtstr
        gl0 = lon0 - res/2
        gb0 = lat0 - res/2
        town_vals = {}
        for t in townships:
            lat = t.get('lat'); lng = t.get('lng')
            if lat is None or lng is None: continue
            gx = round((lng - gl0) / res)
            gy = round((lat - gb0) / res)
            if 0 <= gx < nx and 0 <= gy < ny:
                v = vals[gy*nx + gx]
                town_vals[f"{t['county']}{t['township']}"] = round(v, 1) if v >= 0 else None
        n_rain = sum(1 for v in town_vals.values() if v and v > 0)
        print(f"    雷達QPF：{len(town_vals)} 鄉鎮取值，{n_rain} 個有雨（時間 {dtstr}）")
        return town_vals, dtstr
    except Exception as e:
        print(f"    解析失敗：{e}"); return {}, ''


# ══════════════════════════════════════════════════════════
#  逐時雨量快照
#    必須同時抓 CWA 與水保署兩個來源：官方代表站有一批是自建站
#    （s水保/w水利/f林保/sr石門/fr翡翠），氣象署 opendata 沒有它們的即時雨量。
#    只抓 CWA 會讓那批站的時序永遠是洞，解除判定就會失真。
# ══════════════════════════════════════════════════════════
def _stn_key(n):
    """站名正規化：去尾端機關代碼字母、再去尾端 (數字) 或 空白數字。
    與 fetch_rainfall.py / landslide_warning_stations.json 保持一致。"""
    import unicodedata
    s = unicodedata.normalize('NFKC', (n or '')).strip()
    prev = None
    while prev != s:
        prev = s
        s = re.sub(r'[A-Za-z]+$', '', s).strip()
        s = re.sub(r'\(\s*\d+\s*\)$', '', s).strip()
        s = re.sub(r'\s+\d+$', '', s).strip()
    return s


def fetch_cwa_hourly():
    """CWA O-A0002：取各站 Past1hr（與 Now/3h/24h 供交叉檢查）。
    回傳 {站名: {'r1':mm,'r3':mm,'r24':mm,'now':mm}}；失敗回 {}。"""
    print("抓取 CWA 觀測站時雨量（O-A0002）...")
    for attempt in range(3):
        try:
            r = requests.get(OBS_URL, params={'Authorization': CWA_API_KEY, 'format': 'JSON'},
                             timeout=90)
            if r.status_code != 200:
                print(f"    HTTP {r.status_code}")
                if attempt < 2: time.sleep(3); continue
                return {}
            doc = json.loads(r.content.decode('utf-8', 'replace'))
            break
        except Exception as e:
            print(f"    失敗（{attempt+1}/3）：{e}")
            if attempt == 2: return {}
            time.sleep(3)
    # ★ O-A0002 的 RainfallElement 值是**嵌套**在 'Precipitation' 下，且鍵名大小寫不一致
    #   （Past6Hr 大寫 H、Past24hr 小寫 h）。早期版本寫 float(el.get(k)) 直接對 dict
    #   取 float → 每一站都是 None → cwa 桶全空 → r1h/r2h/r3h 永遠算不出來。
    #   本函式與 fetch_rainfall.py 的 gp() 行為必須一致。
    def gp(el, key):
        for k in (key, key.replace('hr', 'Hr'), key.replace('Hr', 'hr')):
            node = el.get(k)
            if isinstance(node, dict):
                v = node.get('Precipitation')
            else:
                v = node          # 萬一哪天 CWA 改成直接給值也吃得下
            if v is None:
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            # CWA 缺值為 -998/-999；'T' 微量等非數值已在上面被跳過
            return None if f < -90 else max(0.0, f)
        return None
    out = {}
    try:
        for st in doc.get('records', {}).get('Station', []) or []:
            nm = (st.get('StationName') or '').strip()
            if not nm: continue
            el = st.get('RainfallElement', {}) or {}
            out[nm] = {'r1': gp(el, 'Past1hr'), 'r3': gp(el, 'Past3hr'),
                       'r24': gp(el, 'Past24hr'), 'now': gp(el, 'Now')}
    except Exception as e:
        print(f"    解析失敗：{e}"); return {}
    n_val = sum(1 for v in out.values() if v['r1'] is not None)
    n1 = sum(1 for v in out.values() if v['r1'])
    print(f"    {len(out)} 站，有時雨量值：{n_val} 站，時雨量>0：{n1} 站")
    if out and n_val == 0:
        print("    ★ 警告：所有站的 Past1hr 都取不到值——欄位結構可能改變，請貼 log 檢查")
    return out


def fetch_swcb_hourly():
    """水保署土石流參考雨量站 API：取 STRT（官方有效累積雨量 ETR2）。
    此 API 只給累積量、不給時雨量，故 ETR2 逐時序列由本快照的差分推得。
    回傳 {站名: ETR2}；失敗回 {}。"""
    print("抓取水保署參考雨量站 ETR2...")
    for attempt in range(3):
        try:
            r = requests.get(SWCB_RAIN_URL, timeout=60)
            if r.status_code != 200:
                print(f"    HTTP {r.status_code}")
                if attempt < 2: time.sleep(3); continue
                return {}
            data = json.loads(r.content.decode('utf-8', 'replace'))
            break
        except Exception as e:
            print(f"    失敗（{attempt+1}/3）：{e}")
            if attempt == 2: return {}
            time.sleep(3)
    if not isinstance(data, list): return {}
    out = {}
    for row in data:
        if not isinstance(row, dict): continue
        for nk, vk in (('STName1', 'STRT1'), ('STName2', 'STRT2')):
            nm = (row.get(nk) or '').strip()
            try: v = float(row.get(vk))
            except (TypeError, ValueError): continue
            if not nm: continue
            out[nm] = v
            out.setdefault(_stn_key(nm), v)
    print(f"    {len(out)} 個站名鍵（含正規化鍵）")
    return out


def update_hourly_series(now_tpe):
    """把本小時的 CWA 時雨量與水保署 ETR2 併入 rain_hourly.json 滾動序列。

    結構（刻意用「時戳→站→值」而非「站→時戳」，缺跑的整個小時一眼可辨）：
      {"updated": "2026-08-08T14:12",
       "hours": ["2026-08-08T13", ...],            ← 升冪，最多 168 筆
       "cwa":  {"2026-08-08T13": {"六龜": 12.5, ...}},   ← Past1hr
       "swcb": {"2026-08-08T13": {"六龜": 431.2, ...}}}  ← STRT(ETR2)

    絕不覆寫既有小時（該小時已寫過就跳過），避免同小時重跑污染序列。
    """
    hour_key = now_tpe.strftime('%Y-%m-%dT%H')
    ser = {'updated': '', 'hours': [], 'cwa': {}, 'swcb': {}}
    if os.path.exists(HOURLY_FILE):
        try:
            with open(HOURLY_FILE, encoding='utf-8') as f:
                old = json.load(f)
            if isinstance(old, dict) and 'hours' in old:
                ser = old
                ser.setdefault('cwa', {}); ser.setdefault('swcb', {})
        except Exception as e:
            print(f"    既有 {HOURLY_FILE} 讀取失敗，重建序列：{e}")

    if hour_key in ser.get('hours', []):
        print(f"    {hour_key} 已存在 → 不覆寫（同小時重跑）")
    else:
        cwa  = fetch_cwa_hourly()
        swcb = fetch_swcb_hourly()
        if not cwa and not swcb:
            print("    兩個來源都失敗 → 本小時不寫入（序列留空格，判定端會回『資料不足』）")
            return
        ser['cwa'][hour_key]  = {k: v['r1'] for k, v in cwa.items() if v.get('r1') is not None}
        ser['swcb'][hour_key] = swcb
        ser['hours'] = sorted(set(ser['hours']) | {hour_key})

    # 修剪：只留最近 KEEP_SERIES_HOURS 小時
    cutoff = (now_tpe - timedelta(hours=KEEP_SERIES_HOURS)).strftime('%Y-%m-%dT%H')
    keep = [h for h in ser['hours'] if h >= cutoff]
    dropped = len(ser['hours']) - len(keep)
    ser['hours'] = keep
    for bucket in ('cwa', 'swcb'):
        ser[bucket] = {h: v for h, v in ser[bucket].items() if h in keep}
    ser['updated'] = now_tpe.strftime('%Y-%m-%dT%H:%M')
    ser['keep_hours'] = KEEP_SERIES_HOURS

    # 完整度：判定端據此決定「資料不足」而非「未達標」
    span = len(keep)
    expected = min(span, KEEP_SERIES_HOURS)
    gaps = []
    if keep:
        t = datetime.strptime(keep[0], '%Y-%m-%dT%H')
        end = datetime.strptime(keep[-1], '%Y-%m-%dT%H')
        have = set(keep)
        while t <= end:
            k = t.strftime('%Y-%m-%dT%H')
            if k not in have: gaps.append(k)
            t += timedelta(hours=1)
    ser['gaps'] = gaps[-48:]          # 只留最近的缺格清單，避免無限長
    ser['gap_count'] = len(gaps)

    with open(HOURLY_FILE, 'w', encoding='utf-8') as f:
        json.dump(ser, f, ensure_ascii=False, separators=(',', ':'))
    sz = os.path.getsize(HOURLY_FILE) // 1024
    print(f"    已寫 {HOURLY_FILE}：{span} 小時序列（清除{dropped}筆過期），"
          f"缺格 {len(gaps)} 小時，{sz}KB")
    if span < 12:
        print(f"    ⚠ 暖機中（{span}/12h）：解除判定需12h、自算ETR2需168h，"
              f"未達前判定端會回『資料不足』而非『未達解除標準』")


def patch_radar_into_data(radar_vals, radar_dt):
    """寫獨立的 radar.json（只有本每小時腳本寫、主腳本不碰）。
    前端載入時併入——兩個 workflow 各寫各檔，永不在 data.json 上撞車。"""
    out = {
        'radar_qpf_time': radar_dt,
        'townships': radar_vals,   # {county+township: mm}
    }
    with open(RADAR_FILE, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, separators=(',', ':'))
    print(f"    已寫 {RADAR_FILE}：{len(radar_vals)} 鄉鎮雷達值，時間 {radar_dt}")


def main():
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    hour_key = now.strftime('%Y-%m-%dT%H')
    print(f"雷達1h QPF 每小時更新  {now.strftime('%Y-%m-%d %H:%M')} TST")

    if not CWA_API_KEY:
        print("無 CWA_API_KEY，跳過")
        return
    if not os.path.exists(TOWNS_FILE):
        print(f"找不到 {TOWNS_FILE}")
        return

    with open(TOWNS_FILE, encoding='utf-8') as f:
        towns = json.load(f)

    # ── F-B0046 未來1h雷達QPF（每10分更新）→ 寫獨立 radar.json ──
    #   註：QPESUMS 觀測網格（O-A0038）已於 2026-07 停用——CWA 該 dataid 現回傳
    #   溫度圖而非雨量網格，且 opendata 未提供校正後的 QPE 雨量文字網格
    #   （O-A0059 僅未校正回波 dBZ，Z-R 換算誤差達2倍以上，不宜當補值）。
    #   無測站鄉鎮改以雨量站聚合＋模式預測為準，不再嘗試 QPESUMS 補值。
    radar_vals, radar_dt = fetch_radar_qpf_1h(towns)
    if radar_vals:
        patch_radar_into_data(radar_vals, radar_dt)
    else:
        print("    雷達QPF 本次未取得，radar.json 維持原值")

    # ── 逐時雨量快照（獨立於雷達成敗；雷達掛掉不該讓序列斷格）──
    print("逐時雨量快照...")
    try:
        update_hourly_series(now)
    except Exception as e:
        print(f"    逐時快照失敗（不影響 radar.json）：{e}")


if __name__ == '__main__':
    main()
