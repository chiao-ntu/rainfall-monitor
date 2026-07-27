#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QPESUMS 每小時累積腳本（輕量，供 GitHub Actions 每小時執行）
抓 O-A0038-001 網格 1h 雨量 → 取各鄉鎮最近格點值 → 滾動寫入 qpesums_history.json
主腳本 fetch_rainfall.py 每 6h 讀取此歷史合成 24h 累積，補強無測站鄉鎮觀測。
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
HIST_FILE    = "qpesums_history.json"
TOWNS_FILE   = "all_townships.json"
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


def patch_radar_into_data(radar_vals, radar_dt):
    """只補寫 data.json 的雷達欄位（qpf_radar_1h / radar_qpf_time），其他欄位不動。
    這樣雷達可隨每小時腳本更新，不必等 6h 的主腳本。"""
    if not os.path.exists(DATA_FILE):
        print(f"    找不到 {DATA_FILE}，跳過雷達補寫（主腳本尚未產生）")
        return
    try:
        with open(DATA_FILE, encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        print(f"    讀取 {DATA_FILE} 失敗：{e}"); return
    n = 0
    for t in data.get('townships', []):
        key = f"{t.get('county','')}{t.get('township','')}"
        if key in radar_vals:
            t['qpf_radar_1h'] = radar_vals[key]
            n += 1
    data['radar_qpf_time'] = radar_dt
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, separators=(',', ':'))
    print(f"    已補寫 data.json：{n} 鄉鎮雷達值，時間 {radar_dt}")


def main():
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    hour_key = now.strftime('%Y-%m-%dT%H')
    print(f"QPESUMS 每小時累積  {now.strftime('%Y-%m-%d %H:%M')} TST")

    if not CWA_API_KEY:
        print("無 CWA_API_KEY，跳過")
        return
    if not os.path.exists(TOWNS_FILE):
        print(f"找不到 {TOWNS_FILE}")
        return

    with open(TOWNS_FILE, encoding='utf-8') as f:
        towns = json.load(f)

    vals = fetch_grid()
    if not vals:
        print("網格抓取失敗，本次不更新")
        return

    hist = {}
    if os.path.exists(HIST_FILE):
        try:
            with open(HIST_FILE, encoding='utf-8') as f:
                hist = json.load(f)
        except Exception:
            hist = {}

    cutoff = (now - timedelta(hours=KEEP_HOURS)).strftime('%Y-%m-%dT%H')
    n_hit = 0
    for t in towns:
        lat, lng = t.get('lat'), t.get('lng')
        if not lat:
            continue
        key = f"{t['county']}{t['township']}"
        v = grid_at(vals, lat, lng)
        rec = hist.setdefault(key, {})
        rec[hour_key] = v
        # 滾動清理
        for hk in [k for k in rec if k < cutoff]:
            del rec[hk]
        if v is not None:
            n_hit += 1

    with open(HIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(hist, f, ensure_ascii=False, separators=(',', ':'))
    print(f"完成：{n_hit}/{len(towns)} 鄉鎮有值 → {HIST_FILE}（{os.path.getsize(HIST_FILE)//1024}KB）")

    # F-B0046 未來1h雷達QPF：抓取後補寫進 data.json（每小時刷新，比主腳本6h即時）
    radar_vals, radar_dt = fetch_radar_qpf_1h(towns)
    if radar_vals:
        patch_radar_into_data(radar_vals, radar_dt)
    else:
        print("    雷達QPF 本次未取得，data.json 雷達欄位維持原值")


if __name__ == '__main__':
    main()
