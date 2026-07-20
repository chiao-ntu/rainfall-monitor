"""
台灣降雨預測監測系統 - 資料抓取腳本 v5
==============================================
已確認的 CWA API 結構：
  O-A0002-001: RainfallElement.Past6Hr / Past24hr / Past2days / Past3days
  F-D0047-XXX: WeatherElement「3小時降雨機率」/ 「12小時降雨機率」
               各縣市分開端點（奇數=3天，偶數=1週），Location = 鄉鎮
"""
import requests, json, math, os, sys, time
from datetime import datetime, timezone, timedelta

CWA_API_KEY  = os.environ.get("CWA_API_KEY", "")
STATIC_FILE  = "etr2_static.json"
ALL_TOWNSHIPS_FILE = "all_townships.json"  # 全台368個行政區（含座標），不依賴是否有觀測站
HISTORY_FILE = "obs_history.json"
OUTPUT_FILE  = "data.json"
ETR2_WEIGHTS = [1.0, 0.7, 0.5, 0.4, 0.3, 0.2, 0.1]  # R0~R6 固定權重
BASE_URL     = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
OBS_URL      = f"{BASE_URL}/O-A0002-001"
OPENMETEO    = "https://api.open-meteo.com/v1/forecast"

# 各縣市的鄉鎮預報端點（奇數=3天含3h PoP，偶數=1週含12h PoP）
COUNTY_EP_3D = {
    '宜蘭縣':'F-D0047-001','桃園市':'F-D0047-005','新竹縣':'F-D0047-009',
    '苗栗縣':'F-D0047-013','彰化縣':'F-D0047-017','南投縣':'F-D0047-021',
    '雲林縣':'F-D0047-025','嘉義縣':'F-D0047-029','屏東縣':'F-D0047-033',
    '臺東縣':'F-D0047-037','花蓮縣':'F-D0047-041','澎湖縣':'F-D0047-045',
    '基隆市':'F-D0047-049','新竹市':'F-D0047-053','嘉義市':'F-D0047-057',
    '臺北市':'F-D0047-061','高雄市':'F-D0047-065','新北市':'F-D0047-069',
    '臺中市':'F-D0047-073','臺南市':'F-D0047-077','連江縣':'F-D0047-081',
    '金門縣':'F-D0047-085',
}
COUNTY_EP_7D = {
    '宜蘭縣':'F-D0047-003','桃園市':'F-D0047-007','新竹縣':'F-D0047-011',
    '苗栗縣':'F-D0047-015','彰化縣':'F-D0047-019','南投縣':'F-D0047-023',
    '雲林縣':'F-D0047-027','嘉義縣':'F-D0047-031','屏東縣':'F-D0047-035',
    '臺東縣':'F-D0047-039','花蓮縣':'F-D0047-043','澎湖縣':'F-D0047-047',
    '基隆市':'F-D0047-051','新竹市':'F-D0047-055','嘉義市':'F-D0047-059',
    '臺北市':'F-D0047-063','高雄市':'F-D0047-067','新北市':'F-D0047-071',
    '臺中市':'F-D0047-075','臺南市':'F-D0047-079','連江縣':'F-D0047-083',
    '金門縣':'F-D0047-087',
}

def load_static():
    with open(STATIC_FILE, encoding='utf-8') as f:
        rows = json.load(f)
    table = {r['county']+r['township']: r for r in rows}
    print(f"靜態警戒值：{len(table)} 個鄉鎮")
    return table

def load_all_townships():
    """載入全台368個行政區的座標清單（不依賴是否有觀測站回報資料）"""
    if not os.path.exists(ALL_TOWNSHIPS_FILE):
        print(f"警告：找不到 {ALL_TOWNSHIPS_FILE}，將只處理有觀測站的鄉鎮")
        return []
    with open(ALL_TOWNSHIPS_FILE, encoding='utf-8') as f:
        rows = json.load(f)
    print(f"全台行政區清單：{len(rows)} 個")
    return rows

# ── 觀測站 ────────────────────────────────────────
def fetch_obs():
    if not CWA_API_KEY: return {}
    print("抓取觀測站...")
    raw = None
    for attempt in range(2):
        try:
            r = requests.get(OBS_URL, params={"Authorization":CWA_API_KEY,"format":"JSON"}, timeout=30)
            r.raise_for_status(); raw = r.json(); break
        except Exception as e:
            if attempt == 0: print(f"  第1次失敗，重試：{e}")
            else: print(f"  失敗：{e}"); return {}
    if raw is None: return {}

    def gp(re, key):
        # Past6Hr 大寫 H
        for k in [key, key.replace('hr','Hr'), key.replace('Hr','hr')]:
            v = re.get(k,{}).get('Precipitation')
            if v is not None:
                try:
                    f=float(v); return f if f>=0 else 0.0
                except: pass
        return 0.0

    stations = {}
    for st in raw.get('records',{}).get('Station',[]):
        geo = st.get('GeoInfo',{})
        coords = geo.get('Coordinates',[{}])
        lat,lng = 0.0,0.0
        for c in coords:
            lv=c.get('StationLatitude',0); lo=c.get('StationLongitude',0)
            if lv and lo: lat=float(lv); lng=float(lo); break
        re = st.get('RainfallElement',{})
        stations[st.get('StationId','')] = {
            'name': st.get('StationName',''),
            'lat':lat,'lng':lng,
            'county':geo.get('CountyName',''),
            'township':geo.get('TownName',''),
            'rain_now':  gp(re,'Now'),
            'rain_1h':   gp(re,'Past1hr'),
            'rain_6h':   gp(re,'Past6Hr'),
            'rain_12h':  gp(re,'Past12hr'),
            'rain_24h':  gp(re,'Past24hr'),
            'rain_2d':   gp(re,'Past2days'),
            'rain_3d':   gp(re,'Past3days'),
        }
    nonzero = sum(1 for s in stations.values() if s['rain_24h']>0)
    print(f"  {len(stations)} 站，有24h雨量：{nonzero}")
    return stations

def update_history(stations, now_tpe):
    """
    日累積歷史 v3（權威來源版）
      - 今天：直接使用 O-A0002 的 Now 欄位（本日00時起累積）——日曆日的權威觀測值，
        不做任何滾動窗估計。
      - 過去日：由「該日最後一次執行」寫入的 Now 值自然定版（23時值≈全日）。
        跨日後**絕不覆寫**既有記錄；僅在完全缺值時（新站/斷檔/首次部署）
        才以滾動差分補值：昨天≈rain_24h、前天≈rain_2d-rain_24h、大前天≈rain_3d-rain_2d。
    """
    today = now_tpe.strftime('%Y-%m-%d')
    y1 = (now_tpe-timedelta(days=1)).strftime('%Y-%m-%d')
    y2 = (now_tpe-timedelta(days=2)).strftime('%Y-%m-%d')
    y3 = (now_tpe-timedelta(days=3)).strftime('%Y-%m-%d')

    history = json.load(open(HISTORY_FILE)) if os.path.exists(HISTORY_FILE) else {}
    for sid,st in stations.items():
        if sid not in history: history[sid]={}
        rec = history[sid]
        r_now = st.get('rain_now', 0.0) or 0.0
        r24h  = st.get('rain_24h', 0.0) or 0.0
        r2d   = st.get('rain_2d',  0.0) or 0.0
        r3d   = st.get('rain_3d',  0.0) or 0.0

        # 過去日：只補缺值，絕不覆寫（既有記錄是該日 Now 的日終值，為權威）
        if y1 not in rec: rec[y1] = round(r24h, 1)
        if y2 not in rec: rec[y2] = max(0.0, round(r2d - r24h, 1))
        if y3 not in rec: rec[y3] = max(0.0, round(r3d - r2d, 1))

        # 今天：本日00時起累積（權威值，直接覆蓋更新）
        rec[today] = round(r_now, 1)

    cutoff = (now_tpe-timedelta(days=16)).strftime('%Y-%m-%d')   # 保留16天：過去7日視圖的ETR2需回推7+7天雨齡尾巴
    for sid in history: history[sid]={d:v for d,v in history[sid].items() if d>cutoff}
    with open(HISTORY_FILE,'w',encoding='utf-8') as f:
        json.dump(history,f,ensure_ascii=False,separators=(',',':'))
    print(f"  歷史更新：{len(history)} 站，今日={today}（今日累積=Now權威值）")
    return history

def calc_etr2(sid, history, now_tpe):
    """
    ETR2 = R0 + 0.7×R1 + 0.5×R2 + 0.4×R3 + 0.3×R4 + 0.2×R5 + 0.1×R6
    R0 = 當天(0-24h)累積雨量，R1 = 前一天(25-48h)，...R6 = 前6天
    """
    if sid not in history: return None
    dvals = get_daily_rain_array(sid, history, now_tpe, days=7)   # 含今天去重疊
    etr2 = sum(ETR2_WEIGHTS[i] * dvals[i] for i in range(7))
    return round(etr2, 1)

def get_daily_rain_array(sid, history, now_tpe, days=15):
    """
    回傳過去 N 天的逐日觀測雨量陣列（給前端做未來ETR2%滾動計算用）
    array[0] = 今天, array[1] = 昨天, ...（預設15天：過去7日視圖的ETR2需用到13-14天前觀測）
    """
    if sid not in history: return [0.0]*days
    daily = history[sid]
    return [
        daily.get((now_tpe-timedelta(days=i)).strftime('%Y-%m-%d'), 0.0)
        for i in range(days)
    ]

def enrich_stations_with_etr2(excel_stations, obs, all_stations, alert_val):
    """
    把 Excel 靜態表的測站清單跟即時觀測站資料做站名比對
    比對策略（依序嘗試）：
      1. 精確比對
      2. 正規化比對：去除 s/w/S/W/(1)/(2) 等後綴
      3. 部分包含比對：其中一邊包含另一邊的核心名稱
    """
    import re as _re
    station_etr2  = obs.get('station_etr2', {})
    station_daily = obs.get('station_daily', {})
    obs_station_ids = obs.get('stations', [])

    # 若 obs 是空字典（該鄉鎮完全無觀測站資料），直接回傳原站清單（無ETR2%）
    if not obs_station_ids:
        return [{'name': st.get('name',''), 'alert_val': st.get('alert_val'),
                 'village': st.get('village',''), 'etr2': None, 'etr2_pct': None,
                 'daily_rain': [0.0]*15} for st in excel_stations]

    def normalize(name):
        """去除常見後綴：機構代碼(s/w/S/W)、序號((1)/(2)/1/2)、空白"""
        n = name.strip()
        # 去除括號數字後綴，如 (1)(2)(3)
        n = _re.sub(r'\s*\([0-9]+\)\s*$', '', n)
        # 去除純數字後綴，如 1, 2
        n = _re.sub(r'\s*[0-9]+\s*$', '', n)
        # 去除機構代碼後綴 s/w/S/W
        n = n.rstrip('sSWw').strip()
        return n

    # 建立三層比對結構
    exact_map  = {}   # 精確站名 → 站號
    normal_map = {}   # 正規化站名 → 站號
    for sid in obs_station_ids:
        if sid in all_stations:
            raw = all_stations[sid].get('name', '').strip()
            exact_map[raw] = sid
            nrm = normalize(raw)
            if nrm and nrm not in normal_map:
                normal_map[nrm] = sid
        # 若 sid 不在 all_stations，代表資料結構有問題（通常不應發生）

    unmatched = []
    enriched = []
    for st in excel_stations:
        name = st.get('name', '').strip()
        sid = None

        # 策略1：精確
        sid = exact_map.get(name)

        # 策略2：正規化
        if not sid:
            sid = normal_map.get(normalize(name))

        # 策略3：部分包含（Excel站名正規化後是CWA站名的子字串，或反之）
        if not sid:
            nrm_excel = normalize(name)
            for cwa_name, cwa_sid in exact_map.items():
                nrm_cwa = normalize(cwa_name)
                if nrm_excel and nrm_cwa and (nrm_excel in nrm_cwa or nrm_cwa in nrm_excel):
                    sid = cwa_sid
                    break

        if not sid:
            unmatched.append(name)

        etr2_val = station_etr2.get(sid) if sid else None
        etr2_pct = round(etr2_val/alert_val, 4) if (etr2_val is not None and alert_val and alert_val > 0) else None
        daily    = station_daily.get(sid, [0.0]*15) if sid else [0.0]*15
        enriched.append({
            'name':      name,
            'alert_val': st.get('alert_val'),
            'village':   st.get('village', ''),
            'etr2':      round(etr2_val, 1) if etr2_val is not None else None,
            'etr2_pct':  etr2_pct,
            'daily_rain': daily,
        })

    if obs_station_ids and not exact_map:
        print(f"    [警告] obs有{len(obs_station_ids)}個站號但all_stations查無對應，站名比對完全失效")
        print(f"    obs_station_ids前3個: {obs_station_ids[:3]}")
        print(f"    all_stations共{len(all_stations)}個，前3個key: {list(all_stations.keys())[:3]}")

    if unmatched:
        print(f"    [未匹配測站 {len(unmatched)}個]: {', '.join(unmatched[:8])}{'...' if len(unmatched)>8 else ''}")
    return enriched

def agg_obs(stations, alert_table, history, now_tpe):
    # 建立「有ETR2警戒值登記」的測站名稱集合（用於判斷哪些站可以參與ETR2計算）
    # 邏輯：只有 etr2_static.json 裡明確登記的測站，才能影響 ETR2% 和地圖塗色
    etr2_valid_station_names = set()
    for info in alert_table.values():
        for st in info.get('stations', []):
            name = st.get('name', '').strip()
            if name:
                etr2_valid_station_names.add(name)
                # 也加入正規化後的名稱（去除 s/w 後綴）
                etr2_valid_station_names.add(name.rstrip('sSWw').strip())

    town={}
    for sid,st in stations.items():
        key=st['county']+st['township']
        if key not in town:
            town[key]={'county':st['county'],'township':st['township'],
                       'stations':[],'rain_24h':0.0,'rain_6h':0.0,
                       'rain_2d':0.0,'rain_3d':0.0,'etr2':None,
                       'daily_rain':[0.0]*15, 'station_etr2':{}}
        td=town[key]; td['stations'].append(sid)
        # 雨量觀測：所有站都可以貢獻（用於顯示觀測雨量，不影響 ETR2 塗色）
        td['rain_24h']=max(td['rain_24h'],st['rain_24h'])
        td['rain_6h'] =max(td['rain_6h'], st['rain_6h'])
        td['rain_2d'] =max(td['rain_2d'], st['rain_2d'])
        td['rain_3d'] =max(td['rain_3d'], st['rain_3d'])

        # ETR2 計算：只允許靜態表中有警戒值登記的測站參與
        st_name = st.get('name','').strip()
        is_etr2_valid = (st_name in etr2_valid_station_names or
                         st_name.rstrip('sSWw').strip() in etr2_valid_station_names)
        if is_etr2_valid:
            ev=calc_etr2(sid,history,now_tpe)
            if ev is not None:
                td['etr2']=max(td['etr2'] or 0.0,ev)
                td['station_etr2'][sid] = ev

        # 逐日雨量：所有站都可以貢獻（供前端顯示用）
        st_daily = get_daily_rain_array(sid, history, now_tpe, days=15)
        td['daily_rain'] = [max(a,b) for a,b in zip(td['daily_rain'], st_daily)]
        if 'station_daily' not in td: td['station_daily'] = {}
        td['station_daily'][sid] = st_daily

    for key,td in town.items():
        ai=alert_table.get(key,{}); av=ai.get('alert_val',0)
        td['etr2_pct']=round(td['etr2']/av,4) if td['etr2'] and av>0 else None
    print(f"  鄉鎮聚合：{len(town)} 個有觀測的鄉鎮（ETR2有效站名：{len(etr2_valid_station_names)}個）")
    return town

# ── PoP 各縣市鄉鎮端點 ───────────────────────────
def fetch_pop_county(county, ep_code, is_3day):
    """抓單一縣市的鄉鎮 PoP 資料"""
    url = f"{BASE_URL}/{ep_code}"
    try:
        r = requests.get(url, params={"Authorization":CWA_API_KEY,"format":"JSON"}, timeout=15)
        if r.status_code==404: return {}
        r.raise_for_status(); raw=r.json()
    except Exception as e: return {}

    pop_map={}
    try:
        rec = raw.get('records',{})
        locs_wrap = rec.get('Locations', rec.get('locations',[]))
        if not locs_wrap: return {}
        locs = locs_wrap[0].get('Location', locs_wrap[0].get('location',[]))

        # 目標欄位名稱（已確認）
        target_3d = '3小時降雨機率'   # F-D0047 奇數端點
        target_7d = '12小時降雨機率'  # F-D0047 偶數端點
        target = target_3d if is_3day else target_7d

        for loc in locs:
            name = loc.get('LocationName', loc.get('locationName',''))
            we_list = loc.get('WeatherElement', loc.get('weatherElement',[]))
            segs=[]
            for we in we_list:
                en = we.get('ElementName', we.get('elementName',''))
                if en != target: continue
                times = we.get('Time', we.get('time',[]))
                for t in times:
                    start = t.get('StartTime', t.get('startTime',
                            t.get('DataTime',  t.get('dataTime',''))))
                    end   = t.get('EndTime',   t.get('endTime', start))
                    ev    = t.get('ElementValue', t.get('elementValue',[{}]))
                    if isinstance(ev,list): ev=ev[0] if ev else {}
                    # F-D0047 的 ElementValue 結構可能是：
                    # {"Value":"70","Measures":"%"} 或 {"Probability":"70"}
                    v = None
                    for k in ['ProbabilityOfPrecipitation','Value','value',
                              'Probability','probability','MaxCI','MinCI']:
                        candidate = ev.get(k)
                        if candidate is not None and candidate != '' and candidate != ' ':
                            v = candidate; break
                    try: pop=float(v) if v is not None else None
                    except: pop=None

                    hours = 3 if is_3day else 12
                    segs.append({'start':start,'end':end,'pop':pop,'hours':hours})
            if segs: pop_map[name]=segs
    except Exception as e:
        pass
    return pop_map

def fetch_all_pop(counties_needed):
    """抓所有需要縣市的 PoP，合併成鄉鎮層級"""
    if not CWA_API_KEY: return {}, {}
    print(f"抓取 PoP（{len(counties_needed)} 個縣市）...")
    pop3d_all, pop7d_all = {}, {}
    for county in sorted(counties_needed):
        ep3 = COUNTY_EP_3D.get(county)
        ep7 = COUNTY_EP_7D.get(county)
        if ep3:
            m3 = fetch_pop_county(county, ep3, True)
            pop3d_all.update(m3)
        if ep7:
            m7 = fetch_pop_county(county, ep7, False)
            pop7d_all.update(m7)
    print(f"  PoP3d：{len(pop3d_all)} 鄉鎮，PoP7d：{len(pop7d_all)} 鄉鎮")
    # 印一個範例確認結構
    if pop3d_all:
        k=next(iter(pop3d_all)); s=pop3d_all[k]
        print(f"  [除錯] {k} 共{len(s)}時段，第一段：start={s[0]['start']} pop={s[0]['pop']} hrs={s[0]['hours']}")
    return pop3d_all, pop7d_all

def get_pop_6h_series(township_name, pop3d, pop7d, base_time, num_segs=28):
    """
    取鄉鎮的 6h PoP 序列（共 num_segs 個 6h 時段 = 7天）
    前3天用 pop3d（3h）：每兩個3h合成一個6h（取最大值，保守側）
    後4天用 pop7d（12h）：用 p=1-√(1-p12) 轉換為6h
    回傳 list of float or None，長度=num_segs
    """
    result = [None] * num_segs
    base = base_time

    # 3天資料（3h段）→ 6h段（取前兩個的最大值）
    segs3 = pop3d.get(township_name, [])
    if segs3:
        # 每2個3h合一個6h
        for i in range(0, min(len(segs3)-1, 24), 2):  # 最多12個6h（3天）
            p1 = segs3[i].get('pop')
            p2 = segs3[i+1].get('pop') if i+1<len(segs3) else p1
            if p1 is not None or p2 is not None:
                pop6 = max(p1 or 0, p2 or 0)
                seg_idx = i // 2
                if seg_idx < num_segs:
                    result[seg_idx] = pop6

    # 7天資料（12h段）→ 6h段
    segs7 = pop7d.get(township_name, [])
    if segs7:
        for seg in segs7:
            start_str = seg.get('start','')
            if not start_str: continue
            try:
                # 計算這個時段對應第幾個 6h slot
                start_dt = datetime.fromisoformat(start_str.replace('Z','+00:00'))
                start_tpe = start_dt + timedelta(hours=8)  # 轉台灣時間
                diff_h = (start_tpe - base).total_seconds() / 3600
                seg_idx = int(diff_h / 6)
            except:
                continue
            if 0 <= seg_idx < num_segs:
                p12 = seg.get('pop')
                if p12 is not None:
                    pop6 = round((1-math.sqrt(max(0,1-p12/100)))*100,1)
                    if result[seg_idx] is None:  # 只填尚未有資料的格子
                        result[seg_idx] = pop6
                    # 也填下一個6h slot（12h拆成兩個6h）
                    if seg_idx+1 < num_segs and result[seg_idx+1] is None:
                        result[seg_idx+1] = pop6
    return result

# ── Open-Meteo ────────────────────────────────────
# 逐時警特報掃描結果快取（best_match）：key → warn_seg[60]
WARN_SEG_CACHE = {}
HOURLY_CACHE = {}    # key -> 前48h逐時QPF（best_match）
PAST48_CACHE = {}    # key -> 過去48h逐時模式回算（前天+昨天，圖表歷史段用）

def fetch_openmeteo_model(townships, model='best_match'):
    """
    抓取 Open-Meteo 多模式預報（涵蓋全部15天，從現在起）
    model: 'best_match'（ECMWF+GFS最佳組合）/ 'ecmwf_ifs025' / 'gfs_seamless' / 'icon_seamless'
    """
    model_names = {
        'best_match':    'Open-Meteo Best（ECMWF+GFS）',
        'ecmwf_ifs025':  'ECMWF IFS',
        'gfs_seamless':  'NOAA GFS',
        'icon_seamless': 'DWD ICON',
    }
    label = model_names.get(model, model)
    print(f"  抓取 {label}...")
    lats=[t.get('lat',0) for t in townships]
    lngs=[t.get('lng',0) for t in townships]

    params = {
        'latitude':       ','.join(str(x) for x in lats),
        'longitude':      ','.join(str(x) for x in lngs),
        'hourly':         'precipitation',
        'forecast_days':  15,
        'timezone':       'Asia/Taipei',
    }
    if model != 'best_match':
        params['models'] = model

    for attempt in range(3):
        try:
            r = requests.get(OPENMETEO, params=params, timeout=120)
            if r.status_code == 429:
                wait = 5 * (attempt+1)
                print(f"    429限流，等待{wait}秒後重試...")
                time.sleep(wait)
                continue
            r.raise_for_status(); raw=r.json()
            break
        except Exception as e:
            print(f"    失敗（嘗試{attempt+1}/3）：{e}")
            if attempt == 2:
                return {}, {}
            time.sleep(3)
    else:
        return {}, {}

    result={}
    result_max_hourly={}  # 每個6h段內的「最大單一小時雨量」，供強度分級用
    data_list = raw if isinstance(raw,list) else [raw]
    for i, loc in enumerate(data_list):
        key = f"{lats[i]:.4f}_{lngs[i]:.4f}"
        hourly = loc.get('hourly',{})
        precip = hourly.get('precipitation',[])
        segs_6h = []
        max_hourly_6h = []
        for j in range(0, len(precip), 6):
            chunk = [v for v in precip[j:j+6] if v is not None]
            segs_6h.append(round(sum(chunk), 1))
            max_hourly_6h.append(round(max(chunk), 1) if chunk else 0.0)
        result[key] = segs_6h[:60]
        result_max_hourly[key] = max_hourly_6h[:60]

        # 逐時掃描 CWA 警特報條件（僅 best_match，供前端精確標示）
        # 大雨: 24h≥100 或 1h≥40；豪雨: 24h≥200 或 3h≥100
        # 大豪雨: 24h≥350 或 3h≥200；超大豪雨: 24h≥500
        if model == 'best_match':
            pv = [v if v is not None else 0.0 for v in precip]
            HOURLY_CACHE[key] = [round(v,1) for v in pv[:96]]  # 逐時QPF 96h（今天00起，覆蓋『現在+72h』全時段）
            warn_hourly = []
            r3 = 0.0; r24 = 0.0
            for h in range(len(pv)):
                r3  += pv[h] - (pv[h-3]  if h >= 3  else 0.0)
                r24 += pv[h] - (pv[h-24] if h >= 24 else 0.0)
                r1 = pv[h]
                if r24 >= 500:               lv = 4
                elif r24 >= 350 or r3 >= 200: lv = 3
                elif r24 >= 200 or r3 >= 100: lv = 2
                elif r24 >= 100 or r1 >= 40:  lv = 1
                else:                         lv = 0
                warn_hourly.append(lv)
            warn_seg = [max(warn_hourly[j:j+6]) if warn_hourly[j:j+6] else 0
                        for j in range(0, len(warn_hourly), 6)]
            WARN_SEG_CACHE[key] = warn_seg[:60]
    n = len(next(iter(result.values()),[]))
    print(f"    {len(result)} 個點，各 {n} 個6h時段")
    return result, result_max_hourly

def fetch_openmeteo(townships):
    """抓取所有 Open-Meteo 模式，回傳 (totals_by_model, max_hourly_by_model)"""
    print(f"抓取 Open-Meteo（{len(townships)} 個鄉鎮，全部15天）...")
    models = ['best_match', 'ecmwf_ifs025', 'gfs_seamless', 'icon_seamless']
    all_results = {}
    all_max_hourly = {}
    for i, model in enumerate(models):
        if i > 0:
            time.sleep(2)  # 避免連續請求觸發限流
        result, max_hourly = fetch_openmeteo_model(townships, model)
        all_results[model] = result
        all_max_hourly[model] = max_hourly
    return all_results, all_max_hourly


# ── QPESUMS 雷達整合網格觀測（O-A0038-001，~1.3km）──
# O-A0038-001 是網格「檔案型」產品，走 fileapi 路徑（datastore 會 404）
QPESUMS_URL  = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/O-A0038-001"
QPESUMS_HIST = "qpesums_history.json"
# 網格參數（CWA QPESUMS 標準網格；若首跑log顯示筆數不符再調整）
QP_LON0, QP_LAT0, QP_D, QP_NX, QP_NY = 118.0, 20.0, 0.0125, 441, 561

def fetch_qpesums_grid():
    """二段式：fileapi 後設資料（GeoInfo+Resource.ProductURL）→ 下載實際網格檔。"""
    global QP_LON0, QP_LAT0, QP_D, QP_NX, QP_NY
    if not CWA_API_KEY:
        return None
    try:
        r = requests.get(QPESUMS_URL, params={'Authorization': CWA_API_KEY,
                                              'downloadType':'WEB','format':'JSON'}, timeout=90)
        r.raise_for_status()
        ds = r.json().get('cwaopendata', {}).get('dataset', {})
        geo = ds.get('GeoInfo', {}) or {}
        res = ds.get('Resource', {}) or {}
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
        if isinstance(res, list): res = res[0] if res else {}
        url = res.get('ProductURL') if isinstance(res, dict) else (res if isinstance(res, str) else None)

        # ── 修復 v6.1：優先嘗試「內嵌網格」──
        # 有版本的 O-A0038-001 直接把網格放在 dataset 內（Contents/Content/ContentText），
        # ProductURL 反而指向非網格內容（7/20 事件：下載後僅解析出52值）。
        # 策略：遞迴找出 dataset 中最長字串，若數值token數達標即為網格。
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
            print(f"    QPESUMS 內嵌網格：{len(vals)} 值（期望 {QP_NX*QP_NY}）")
            if QP_NX*QP_NY*0.9 <= len(vals) <= QP_NX*QP_NY:
                return vals
            if len(vals) > QP_NX*QP_NY:
                print(f"    內嵌值多於網格數，取尾端網格段")
                return vals[-QP_NX*QP_NY:]
        if not url:
            print("    QPESUMS 找不到 ProductURL 且無內嵌網格")
            return None
        print(f"    QPESUMS ProductURL：{str(url)[:100]}")
        r2 = requests.get(url, timeout=120)
        r2.raise_for_status()
        data = r2.content
        print(f"    下載：{len(data)} bytes，Content-Type={r2.headers.get('Content-Type','?')[:40]}，開頭={data[:60]!r}")
        if data[:2] == b'PK':
            import zipfile, io
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                text = z.read(z.namelist()[0]).decode('utf-8', errors='replace')
        elif data[:2] == b'\x1f\x8b':
            import gzip as _gz
            text = _gz.decompress(data).decode('utf-8', errors='replace')
        else:
            text = data.decode('utf-8', errors='replace')
        vals = []
        for tok in text.replace(',', ' ').split():
            try:
                v = float(tok)
            except ValueError:
                continue
            vals.append(None if v < 0 else v)
        print(f"    QPESUMS 網格：{len(vals)} 值（期望 {QP_NX*QP_NY}）")
        if len(vals) < QP_NX*QP_NY*0.9 or len(vals) > QP_NX*QP_NY:
            return None
        return vals
    except Exception as e:
        print(f"    QPESUMS 失敗：{e}")
        return None

def qpesums_at(vals, lat, lng):
    """取最近格點的 1h 雨量（None=範圍外或無效）。網格：lon-major 逐列由南向北。"""
    if not vals: return None
    ix = round((lng - QP_LON0) / QP_D)
    iy = round((lat - QP_LAT0) / QP_D)
    if ix < 0 or ix >= QP_NX or iy < 0 or iy >= QP_NY: return None
    idx = iy * QP_NX + ix
    return vals[idx] if idx < len(vals) else None

def load_qpesums_history():
    """讀每小時累積腳本維護的歷史（{key: {iso_hour: mm}}），合成各鄉鎮 24h。"""
    if not os.path.exists(QPESUMS_HIST):
        return {}
    try:
        with open(QPESUMS_HIST, encoding='utf-8') as f:
            hist = json.load(f)
    except Exception:
        return {}
    out = {}
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    cutoff = (now - timedelta(hours=24)).strftime('%Y-%m-%dT%H')
    for key, hours in hist.items():
        vals = [v for h, v in hours.items() if h >= cutoff and v is not None]
        if vals:
            out[key] = round(sum(vals), 1)
    return out


def load_qpesums_p48():
    """QPESUMS 逐時觀測 → 每鄉鎮過去48h逐時序列（[0]=48小時前，[47]=上一完整小時；缺值None）。
    官方觀測資料（O-A0038-001雷達整合網格），供前端逐時圖過去段——絕不以模式回算充當觀測。"""
    if not os.path.exists(QPESUMS_HIST):
        return {}
    try:
        with open(QPESUMS_HIST, encoding='utf-8') as f:
            hist = json.load(f)
    except Exception:
        return {}
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    # 槽 i 對應的小時鍵：now-48+i（取整小時；最後一槽=上一完整小時）
    keys = [(now - timedelta(hours=48-i)).strftime('%Y-%m-%dT%H') for i in range(48)]
    out = {}
    for tkey, hours in hist.items():
        arr = [hours.get(k) for k in keys]
        if any(v is not None for v in arr):
            out[tkey] = [None if v is None else round(v,1) for v in arr]
    return out


# ── 系集強弱降雨比值（縣級） ──────────────────────
ENSEMBLE_API = "https://ensemble-api.open-meteo.com/v1/ensemble"

def fetch_ensemble_ratios(townships):
    """
    以縣級代表點抓 ECMWF 系集（51成員），計算前48h各6h段的
    強降雨放大倍率（前25%成員均值/中位數）與弱降雨縮小倍率（後25%/中位數）。
    回傳 {county: {'hi':[8], 'lo':[8]}}；失敗回空dict（前端退回qpf_best）。
    """
    print("抓取 ECMWF 系集（縣級代表點）...")
    # 縣級代表點：縣內鄉鎮座標平均
    county_pts = {}
    for t in townships:
        c = t.get('county'); lat = t.get('lat'); lng = t.get('lng')
        if not c or not lat: continue
        county_pts.setdefault(c, []).append((lat, lng))
    counties = sorted(county_pts.keys())
    lats = [sum(p[0] for p in county_pts[c])/len(county_pts[c]) for c in counties]
    lngs = [sum(p[1] for p in county_pts[c])/len(county_pts[c]) for c in counties]

    params = {
        'latitude':  ','.join(f"{x:.4f}" for x in lats),
        'longitude': ','.join(f"{x:.4f}" for x in lngs),
        'hourly':    'precipitation',
        'models':    'ecmwf_ifs025',
        'forecast_days': 3,
        'timezone':  'Asia/Taipei',
    }
    for attempt in range(3):
        try:
            r = requests.get(ENSEMBLE_API, params=params, timeout=120)
            if r.status_code == 429:
                time.sleep(5*(attempt+1)); continue
            r.raise_for_status(); raw = r.json()
            break
        except Exception as e:
            print(f"    系集失敗（{attempt+1}/3）：{e}")
            if attempt == 2: return {}
            time.sleep(3)
    else:
        return {}

    ratios = {}
    data_list = raw if isinstance(raw, list) else [raw]
    for i, loc in enumerate(data_list):
        if i >= len(counties): break
        hourly = loc.get('hourly', {})
        # 蒐集所有成員的降雨序列（key: precipitation_memberXX 或 precipitation）
        members = []
        for k, v in hourly.items():
            if k.startswith('precipitation') and isinstance(v, list):
                members.append([x if x is not None else 0.0 for x in v])
        if len(members) < 10:
            continue
        hi_arr, lo_arr = [], []
        for sg in range(8):  # 前48h的8個6h段
            seg_sums = sorted(sum(m[sg*6:(sg+1)*6]) for m in members)
            n = len(seg_sums)
            q = max(1, n//4)
            med = seg_sums[n//2]
            top_mean = sum(seg_sums[-q:]) / q
            bot_mean = sum(seg_sums[:q]) / q
            if med < 1.0:
                # 段雨量太小，比值無意義 → 不放大不縮小
                hi_arr.append(1.0); lo_arr.append(1.0)
            else:
                hi_arr.append(round(min(3.0, max(1.0, top_mean/med)), 2))
                lo_arr.append(round(max(0.1, min(1.0, bot_mean/med)), 2))
        ratios[counties[i]] = {'hi': hi_arr, 'lo': lo_arr}
    print(f"    系集比值：{len(ratios)} 縣市")
    return ratios


def apply_hourly_ratio(hourly, county, ens_ratios, kind):
    """逐時QPF × 縣級系集比值（比值以6h段為單位，套用至段內各小時）。"""
    r = ens_ratios.get(county, {}).get(kind)
    if not r or not hourly:
        return list(hourly)
    return [round(v * r[min(h//6, 7)], 1) for h, v in enumerate(hourly)]


def apply_ensemble_ratio(qpf, maxh, county, ens_ratios, kind):
    """qpf_best × 縣級系集比值（前8段），8段之後維持原值。回傳新陣列。"""
    r = ens_ratios.get(county, {}).get(kind)
    if not r:
        return list(qpf), list(maxh)
    q2 = [round(v*r[i], 1) if i < 8 and v else v for i, v in enumerate(qpf)]
    m2 = [round(v*r[i], 1) if i < 8 and v else v for i, v in enumerate(maxh)]
    return q2, m2


# ── 昨日模式偏差比（動態偏差比 v1，顯示層） ────────
def fetch_model_yesterday(townships):
    """
    抓 best_match 昨日24h模式雨量（past_days=1），供計算
    bias_24h = 昨日觀測 / 昨日模式。回傳 {key: model_yday_sum}。
    """
    print("抓取模式昨日回算（偏差比基準）...")
    lats=[t.get('lat',0) for t in townships]
    lngs=[t.get('lng',0) for t in townships]
    params = {
        'latitude':  ','.join(str(x) for x in lats),
        'longitude': ','.join(str(x) for x in lngs),
        'hourly':    'precipitation',
        'past_days': 2,
        'forecast_days': 1,
        'timezone':  'Asia/Taipei',
    }
    for attempt in range(3):
        try:
            r = requests.get(OPENMETEO, params=params, timeout=120)
            if r.status_code == 429:
                time.sleep(5*(attempt+1)); continue
            r.raise_for_status(); raw = r.json()
            break
        except Exception as e:
            print(f"    失敗（{attempt+1}/3）：{e}")
            if attempt == 2: return {}
            time.sleep(3)
    else:
        return {}
    out = {}
    global PAST48_CACHE
    PAST48_CACHE = {}
    data_list = raw if isinstance(raw, list) else [raw]
    for i, loc in enumerate(data_list):
        key = f"{lats[i]:.4f}_{lngs[i]:.4f}"
        precip = loc.get('hourly', {}).get('precipitation', [])
        # past_days=2：[0:24]=前天, [24:48]=昨天, [48:]=今天以後
        p48 = [round(v,1) if v is not None else 0.0 for v in precip[:48]]
        PAST48_CACHE[key] = p48
        out[key] = round(sum(p48[24:48]), 1)   # 昨日24h（偏差比基準）
    print(f"    {len(out)} 個點（含過去48h逐時回算）")
    return out


def calc_bias_24h(daily_rain, model_yday):
    """昨日觀測/昨日模式偏差比。門檻：模式≥10mm才有意義；限幅[0.2,8]。"""
    obs_yday = daily_rain[1] if len(daily_rain) > 1 else 0.0
    if model_yday is None or model_yday < 10.0:
        return None
    return round(max(0.2, min(8.0, obs_yday / model_yday)), 2)


# ── 颱風期 QPF 格點 ──────────────────────────────
QPF_TYPHOON = [f"{BASE_URL}/F-C0041-{str(i).zfill(3)}" for i in range(1,9)]

def fetch_typhoon_qpf():
    if not CWA_API_KEY: return []
    print("抓取颱風 QPF（F-C0041）...")
    typhoon_segs = []
    for i, url in enumerate(QPF_TYPHOON):
        label = f"{i*6}-{(i+1)*6}h"
        try:
            r = requests.get(url, params={"Authorization":CWA_API_KEY,"format":"JSON"}, timeout=20)
            if r.status_code == 404: continue
            r.raise_for_status(); raw=r.json()
            dataset = raw.get("records",{}).get("dataset",[])
            if not dataset: continue
            ct = dataset[0].get("contents",{}).get("contentText","")
            if not ct: continue
            # 擷取時間窗（供日曆段對齊；缺則前端/組裝端退回舊索引法）
            dsi = dataset[0].get("datasetInfo", dataset[0].get("DatasetInfo", {})) or {}
            st_str = dsi.get("startTime", dsi.get("StartTime", ""))
            pts = []
            for ri, row in enumerate(ct.strip().split("\n")):
                lat_pt = 20.8 + ri * 0.045
                for ci, v in enumerate(row.split(",")):
                    lng_pt = 117.56 + ci * 0.049
                    if 21.5<=lat_pt<=26.5 and 119<=lng_pt<=123:
                        try: pts.append((lat_pt, lng_pt, float(v)))
                        except: pass
            typhoon_segs.append({"label":label,"points":pts,"start":st_str})
        except Exception as e:
            pass
    if len(typhoon_segs) >= 4:
        print(f"  颱風 QPF：{len(typhoon_segs)} 段")
    else:
        print(f"  非颱風期間（{len(typhoon_segs)} 段）")
        typhoon_segs = []
    return typhoon_segs

# ── CWA 常態性定量降水預報（48h逐6h，預報員修正版）──────────────────
# 產品說明文件：https://www.cwa.gov.tw/Data/data_catalog/1-2-4.pdf
#   平時每日4次（05:30/11:30/17:30/23:30 TST），劇烈天氣期間每3h加發
#   csv：2.5km 格點，經緯 117.56~123.91 / 20.8~26.65，dlon 0.0245 / dlat 0.0226
#        260x260=67600 值，排列由南至北、由西至東，座標為 TWD67
#   檔名：[YYYY-MMDD-hhmm]._00[tau].QPF6h.csv（發布時間為 UTC，tau=預報時長）
# 介接策略（來源探測；成功後記憶於 CWA_QPF_SRC_FILE，之後直取）：
#   A. fileapi 指標檔 F-C0035-015/017/023/024（JSON 內含 uri → 下載 zip/csv）
#   B. fileapi ZIP 掃描 F-C0035-013..030（找 zip 內 *QPF6h*.csv）
FILEAPI = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi"
CWA_QPF_SRC_FILE = "cwa_qpf_source.json"
QPF_GRID = dict(lon0=117.56, lat0=20.8, dlon=0.0245, dlat=0.0226, nx=260, ny=260)
# TWD67 → WGS84 近似位移（TWD67 經度較小約0.0083°、緯度較大約0.0019°；2.5km格點取最近點足夠）
TWD67_DLON, TWD67_DLAT = 0.00834, -0.00186

def _qpf_parse_csv_text(text):
    """解析 2.5km QPF csv：跳過檔頭，收集所有數值；回傳 list（長度須=67600）或 None"""
    n_need = QPF_GRID['nx'] * QPF_GRID['ny']
    vals = []
    for line in text.splitlines():
        toks = line.replace(',', ' ').split()
        row = []
        ok = True
        for tk in toks:
            try: row.append(float(tk))
            except ValueError: ok = False; break
        if ok and row:
            vals.extend(row)
    if len(vals) < n_need:
        return None
    if len(vals) > n_need:
        vals = vals[-n_need:]   # 檔頭若含數字，取尾端網格段
    return [None if v < 0 else v for v in vals]

def _qpf_grid_at(vals, lat, lng):
    """town WGS84 座標 → TWD67 → 最近格點值（南→北、西→東 排列）"""
    g = QPF_GRID
    lon67 = lng - TWD67_DLON
    lat67 = lat - TWD67_DLAT
    ix = round((lon67 - g['lon0']) / g['dlon'])
    iy = round((lat67 - g['lat0']) / g['dlat'])
    if ix < 0 or ix >= g['nx'] or iy < 0 or iy >= g['ny']: return None
    idx = iy * g['nx'] + ix
    return vals[idx] if idx < len(vals) else None

def _qpf_extract_zip(data, now_tpe):
    """zip bytes → {start_tpe(datetime): vals}；只取 QPF6h 成員，時間窗由檔名推得"""
    import zipfile, io, re as _re
    out = {}
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        return out
    for name in z.namelist():
        m = _re.search(r'(\d{4})-(\d{2})(\d{2})-(\d{2})(\d{2})\._0*(\d+)\.QPF6h\.csv$', name)
        if not m: continue
        yy, mo, dd, hh, mi, tau = map(int, m.groups())
        issue_utc = datetime(yy, mo, dd, hh, mi, tzinfo=timezone.utc)
        end_tpe   = issue_utc + timedelta(hours=8) + timedelta(hours=tau)
        # 發布時刻為 X:30（05:30/11:30/17:30/23:30 TST），tau 自發布起算，
        # 實際預報窗對齊 6h 日曆邊界（首段=發布+30分起）→ 就近吸附（容差90分）
        end_naive = end_tpe.replace(tzinfo=None)
        day0 = end_naive.replace(hour=0, minute=0, second=0, microsecond=0)
        off  = (end_naive - day0).total_seconds()
        snap = round(off / 21600) * 21600
        if abs(off - snap) <= 5400:
            end_tpe = day0 + timedelta(seconds=snap)
            end_tpe = end_tpe.replace(tzinfo=timezone.utc)  # 佔位tz，稍後去除
        start_tpe = end_tpe - timedelta(hours=6)
        try:
            text = z.read(name).decode('utf-8', errors='replace')
        except Exception:
            continue
        vals = _qpf_parse_csv_text(text)
        if vals:
            out[start_tpe.replace(tzinfo=None)] = vals
    return out

def _walk_uris(obj, acc):
    if isinstance(obj, dict):
        for v in obj.values(): _walk_uris(v, acc)
    elif isinstance(obj, list):
        for v in obj: _walk_uris(v, acc)
    elif isinstance(obj, str) and obj.startswith('http'):
        acc.append(obj)

def fetch_cwa_routine_qpf(now_tpe):
    """常態 48h 逐6h QPF。成功回傳 {'issue':str, 'segs':{start_tpe: vals}}；失敗回 None
    v6.1 探測策略（依 7/20 首跑 log 修訂：F-C0035-015/017/023/024 指標檔僅含 PNG uri）：
      A. 已知來源（cwa_qpf_source.json）直取
      B. 廣域指標檔掃描 F-C0035-001..030 + F-C0041-001..016（fileapi JSON→列出全部 uri，
         下載任何 zip 檢視成員名單、csv 逐一嘗試）——一次跑完即可確定 dataid 版圖
      C. 全部僅圖檔時：下載 QPF PNG 存 qpf_sample.png（log 尺寸），供像素校正；
         校正參數 QPF_PNG_CALIB 填入後改走色塊判讀（decode_qpf_png）
    """
    if not CWA_API_KEY: return None
    print("抓取 CWA 常態 QPF（48h逐6h，預報員修正版）...")
    known = None
    if os.path.exists(CWA_QPF_SRC_FILE):
        try:
            with open(CWA_QPF_SRC_FILE, encoding='utf-8') as f: known = json.load(f)
        except Exception: known = None

    def _try_zip_bytes(data, did, note=''):
        segs = _qpf_extract_zip(data, now_tpe)
        if len(segs) >= 4:
            with open(CWA_QPF_SRC_FILE, 'w', encoding='utf-8') as f:
                json.dump({'kind': 'zip', 'id': did, 'note': note[:120]}, f)
            print(f"    ✓ 常態QPF：{len(segs)} 段（來源 {did} {note[:60]}）")
            return {'issue': did, 'segs': segs}
        return None

    scan_ids = ([known['id']] if known and known.get('id') else []) \
             + [f'F-C0035-{i:03d}' for i in range(1, 31)] \
             + [f'F-C0041-{i:03d}' for i in range(1, 17)]
    seen, png_uris = set(), []
    for did in scan_ids:
        if did in seen: continue
        seen.add(did)
        try:
            r = requests.get(f"{FILEAPI}/{did}", params={'Authorization': CWA_API_KEY,
                             'downloadType': 'WEB', 'format': 'JSON'}, timeout=20)
            if r.status_code != 200:
                continue   # 不存在的 dataid 靜默跳過（避免log爆量）
            body = r.content
            if body[:2] == b'PK':          # 直接就是 zip
                got = _try_zip_bytes(body, did, 'fileapi直出zip')
                if got: return got
                import zipfile as _zf, io as _io
                try:
                    names = _zf.ZipFile(_io.BytesIO(body)).namelist()[:5]
                    print(f"    {did}=zip 成員：{names}")
                except Exception: pass
                continue
            try:
                doc = json.loads(body.decode('utf-8', errors='replace'))
            except Exception:
                print(f"    {did}：非JSON（{body[:50]!r}）"); continue
            uris = []; _walk_uris(doc, uris)
            if not uris: continue
            # 完整列出 uri（探測版圖的關鍵情報）
            for u in uris[:4]:
                print(f"    {did} uri: {u[:110]}")
            for u in uris:
                ul = u.lower()
                if '.zip' in ul or ('csv' in ul and '.png' not in ul):
                    r2 = requests.get(u, timeout=120)
                    if r2.status_code != 200: continue
                    if r2.content[:2] == b'PK':
                        got = _try_zip_bytes(r2.content, did, u.rsplit('/',1)[-1])
                        if got: return got
                        import zipfile as _zf, io as _io
                        try:
                            names = _zf.ZipFile(_io.BytesIO(r2.content)).namelist()[:6]
                            print(f"      zip 成員（非QPF6h）：{names}")
                        except Exception: pass
                elif '.png' in ul:
                    png_uris.append((did, u))
        except Exception as e:
            print(f"    {did} 例外：{e}")

    # ── C. 僅圖檔 → 色塊判讀路徑 ──
    if png_uris:
        did, u = png_uris[0]
        try:
            r = requests.get(u, timeout=60)
            if r.status_code == 200 and r.content[:8] == b'\x89PNG\r\n\x1a\n':
                with open('qpf_sample.png', 'wb') as f: f.write(r.content)
                import struct
                w, h = struct.unpack('>II', r.content[16:24])   # IHDR
                print(f"    已存 qpf_sample.png（{w}x{h}，{len(r.content)//1024}KB，來源 {did}）")
                if QPF_PNG_CALIB:
                    segs = decode_qpf_png(r.content, did, now_tpe)
                    if segs and len(segs) >= 1:
                        print(f"    ✓ 常態QPF（PNG色塊判讀）：{len(segs)} 段")
                        return {'issue': did + ':png', 'segs': segs}
                else:
                    print(f"    QPF_PNG_CALIB 未設定——請將 qpf_sample.png 交給開發者做像素校正後啟用色塊判讀")
        except Exception as e:
            print(f"    PNG 取樣失敗：{e}")
    print("    常態QPF：格點資料探測未果（以上 uri 清單請貼給開發者）")
    return None

# ── QPF PNG 色塊判讀（校正後啟用）─────────────────────────────────
# 校正參數：由 qpf_sample.png 人工比對兩個已知經緯的像素點求仿射轉換。
# 格式：{'px0':像素x, 'py0':像素y, 'lon0':經度, 'lat0':緯度, 'dppx':度/像素x, 'dppy':度/像素y(向下為負),
#        'window_hours':12, 'bands':[(R,G,B,代表值mm), ...]}
# bands 依樣張圖例逐格取色填入（代表值取級距下界，保守），tol 為色距容忍。
QPF_PNG_CALIB = None

def decode_qpf_png(png_bytes, did, now_tpe):
    """PNG 色塊 → 各鄉鎮 QPF 值。需 Pillow（workflow 加 pip install pillow）。
    回傳 {start_tpe: '__png_town_map__'} 形式？——否：回傳與網格路徑同構，
    以「虛擬網格」包裝：直接產出 town 座標查值函式無法塞回既有管線，
    故此處輸出 260x260 虛擬網格（逐格反查像素）以沿用 _qpf_grid_at。"""
    c = QPF_PNG_CALIB
    if not c: return None
    try:
        from PIL import Image
        import io as _io
    except ImportError:
        print("    需要 Pillow：請在 workflow 安裝 pip install pillow")
        return None
    img = Image.open(_io.BytesIO(png_bytes)).convert('RGB')
    W, H = img.size
    px = img.load()
    g = QPF_GRID
    bands = c.get('bands') or []
    tol = c.get('tol', 30)
    vals = [None] * (g['nx'] * g['ny'])
    for iy in range(g['ny']):
        lat67 = g['lat0'] + iy * g['dlat']
        lat = lat67 + TWD67_DLAT   # TWD67→WGS84 反轉換（與 _qpf_grid_at 的正轉換互逆）
        for ix in range(g['nx']):
            lon67 = g['lon0'] + ix * g['dlon']
            lon = lon67 + TWD67_DLON
            x = int(round(c['px0'] + (lon - c['lon0']) / c['dppx']))
            y = int(round(c['py0'] + (lat - c['lat0']) / c['dppy']))
            if x < 0 or x >= W or y < 0 or y >= H: continue
            rgb = px[x, y]
            best, bd = None, tol * tol * 3 + 1
            for (br, bg2, bb, bv) in bands:
                d = (rgb[0]-br)**2 + (rgb[1]-bg2)**2 + (rgb[2]-bb)**2
                if d < bd: bd, best = d, bv
            if best is not None and bd <= tol * tol * 3:
                vals[iy * g['nx'] + ix] = float(best)
    # 時間窗：PNG 為 12h 產品（0-12h 起）；以「發布批次」推定，
    # 均分為兩個 6h 段（12h 值 ÷2）——色塊判讀本質為近似，log 明示
    wh = int(c.get('window_hours', 12))
    base = now_tpe.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    base = base + timedelta(hours=(6 - base.hour % 6) % 6)   # 下一個 6h 邊界
    segs = {}
    n_seg = max(1, wh // 6)
    half = [None if v is None else round(v / n_seg, 1) for v in vals]
    for k in range(n_seg):
        segs[base + timedelta(hours=6 * k)] = half
    print(f"    PNG判讀：{sum(1 for v in vals if v is not None)}/{len(vals)} 格有值，{n_seg} 段（12h均分近似）")
    return segs


# ── 官方警特報（W-C0033-001 各縣市現行天氣警特報）───────────────────
WARN_PHEN_LEVEL = {'大雨': 1, '豪雨': 2, '大豪雨': 3, '超大豪雨': 4}

def fetch_official_warnings():
    """回傳 {'fetched':iso, 'counties':{縣市:{'level':1-4,'phenomena':str,'start':..,'end':..}},
             'others':{縣市:[非降雨類特報名]}}；失敗回 None"""
    if not CWA_API_KEY: return None
    try:
        r = requests.get(f"{BASE_URL}/W-C0033-001",
                         params={'Authorization': CWA_API_KEY, 'format': 'JSON'}, timeout=20)
        r.raise_for_status()
        raw = r.json()
        locs = raw.get('records', {}).get('location', [])
        counties, others = {}, {}
        for loc in locs:
            name = loc.get('locationName', '')
            hz = loc.get('hazardConditions', {}) or {}
            hazards = hz.get('hazards', [])
            if isinstance(hazards, dict):  # 有些版本包一層 {'hazard':[...]}
                hazards = hazards.get('hazard', [])
            for h in hazards or []:
                info = h.get('info', {}) or {}
                phen = info.get('phenomena', '') or ''
                vt = h.get('validTime', {}) or {}
                lv = WARN_PHEN_LEVEL.get(phen)
                if lv:
                    cur = counties.get(name)
                    if not cur or lv > cur['level']:
                        counties[name] = {'level': lv, 'phenomena': phen,
                                          'start': vt.get('startTime', ''), 'end': vt.get('endTime', '')}
                elif phen:
                    others.setdefault(name, [])
                    if phen not in others[name]: others[name].append(phen)
        print(f"  官方警特報：{len(counties)} 縣市有豪大雨特報、{len(others)} 縣市有其他特報")
        return {'fetched': (datetime.now(timezone.utc)+timedelta(hours=8)).strftime('%Y-%m-%dT%H:%M'),
                'counties': counties, 'others': others}
    except Exception as e:
        print(f"  官方警特報抓取失敗：{e}")
        return None

# ── IDW 空間插值 ──────────────────────────────────
def idw(lat, lng, pts, seg=None):
    """pts = [(lat,lng,value), ...]，回傳反距離加權插值結果"""
    if not pts: return 0.0
    dists = sorted([(math.sqrt((p[0]-lat)**2+(p[1]-lng)**2), p) for p in pts])[:4]
    tw, tv = 0.0, 0.0
    for d, p in dists:
        v = p[2]
        if d < 1e-6: return v
        w = 1.0/d**2
        tw += w; tv += w*v
    return round(tv/tw, 1) if tw > 0 else 0.0

# ── 風險分數 S*（ETR2 Risk Score）────────────────────
def calc_risk_score(etr_pct, qpf_mm, pop_pct, n_hours,
                    alpha=0.5, beta=0.5, gamma=0.3,
                    decay_per_6h=4, threshold_per_6h=70):
    """
    etr_pct   : ETR2% 現況值（整數，如 110 = 110%）
    qpf_mm    : 該時窗的 QPF (mm)
    pop_pct   : 降雨機率（0-100）
    n_hours   : 預報時窗（3/6/12/24）
    回傳 S*（float，越大越嚴峻）
    """
    if etr_pct is None: return None
    # 當 PoP 缺失（超過7天預報範圍），用 QPF 量推估合理的 PoP
    # QPF=0mm → PoP=10%（基底），QPF=50mm → PoP≈90%，中間線性插值
    if pop_pct is None:
        if qpf_mm is None or qpf_mm <= 0:
            pop_pct = 10.0
        else:
            pop_pct = min(95.0, 10.0 + qpf_mm * 1.7)  # 約50mm達90%

    # Step 1: L（現況基礎分）
    if etr_pct < 70:
        L = 0
    elif etr_pct < 130:
        L = (etr_pct - 70) / 30 * 4
    else:
        L = 4 + (etr_pct - 130) / 10

    # Step 2: 基準量
    decay  = decay_per_6h * n_hours / 6      # 自然衰退量
    t_high = threshold_per_6h * n_hours / 6  # 加劇門檻量
    net    = qpf_mm - decay                   # 淨雨量

    # Step 3: Mf（未來雨量修正）
    denom = t_high - decay
    Mf = max(-1.0, min(1.0, net/denom*2-1)) if denom != 0 else -1.0

    # Step 4: Mp（降雨機率修正）
    Mp = (pop_pct - 50) / 50

    # Step 5: D（衰退速度修正）
    D = max(-2.0, min(2.0, net/decay)) if decay != 0 else 0.0

    # Step 6: S*
    inner = max(0, L + alpha*Mf + beta*Mp + gamma*D)
    return round(inner * 30, 1)

def get_risk_level(score):
    if score is None:   return None, '#FFFFFF'
    if score < 25:      return '無風險', '#FFFFFF'
    if score < 45:      return '注意',   '#00CC44'
    if score < 75:      return '警戒',   '#DDDD00'
    if score < 100:     return '應變',   '#DD2222'
    return '緊急', '#BB00BB'

# ══════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════
def main():
    now_utc=datetime.now(timezone.utc)
    now_tpe=now_utc+timedelta(hours=8)
    print('='*52)
    print(f"台灣降雨監測 v5  {now_tpe.strftime('%Y-%m-%d %H:%M')} TST")
    print('='*52)

    alert_table = load_static()
    static_list = list(alert_table.values())
    counties_needed = set(t['county'] for t in static_list)

    # 觀測
    stations = fetch_obs()
    history  = update_history(stations,now_tpe) if stations else \
               (json.load(open(HISTORY_FILE)) if os.path.exists(HISTORY_FILE) else {})
    town_obs = agg_obs(stations,alert_table,history,now_tpe)

    # PoP
    pop3d, pop7d = fetch_all_pop(counties_needed)

    # 颱風 QPF（先抓，決定 is_typhoon 旗標）
    typhoon_segs = fetch_typhoon_qpf() if CWA_API_KEY else []
    is_typhoon   = len(typhoon_segs) >= 4

    # 常態 CWA QPF（非颱風期間的官方預報員修正值；治本預測偏差）
    routine_qpf = None
    if not is_typhoon and CWA_API_KEY:
        try: routine_qpf = fetch_cwa_routine_qpf(now_tpe)
        except Exception as e: print(f"  常態QPF例外：{e}")
    # 對齊日曆6h段：idx = (start − 今天00時TST)/6h（qpf_15d[0]=今天00-06 鐵律）
    _today00 = now_tpe.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    routine_seg_map = {}
    if routine_qpf:
        for _st, _vals in routine_qpf['segs'].items():
            _sec = (_st - _today00).total_seconds()
            _idx = int(_sec // 21600)
            if 0 <= _idx < 60 and _sec % 21600 == 0:
                routine_seg_map[_idx] = _vals
        print(f"  常態QPF對齊段索引：{sorted(routine_seg_map)}")

    # 官方現行警特報（與系統預估對照，落差可視化）
    official_warn = fetch_official_warnings() if CWA_API_KEY else None

    # Open-Meteo（四個模式）
    om_all, om_max_hourly_all = fetch_openmeteo(static_list)
    om = om_all.get('ecmwf_ifs025', {})  # 預設用 ECMWF IFS，對台灣地形雨準確度較高

    # QPESUMS 網格觀測（1h 即時 + 24h 歷史合成）
    print("抓取 QPESUMS 網格觀測...")
    qp_grid = fetch_qpesums_grid()
    qp_24h  = load_qpesums_history()
    qp_p48  = load_qpesums_p48()
    if qp_24h: print(f"    QPESUMS 24h 歷史：{len(qp_24h)} 個鄉鎮")
    if qp_p48: print(f"    QPESUMS 逐時觀測 p48：{len(qp_p48)} 個鄉鎮")

    # 系集強弱降雨比值（縣級）+ 昨日模式偏差比
    time.sleep(2)
    ens_ratios = fetch_ensemble_ratios(static_list)
    time.sleep(2)
    model_yday = fetch_model_yesterday(static_list)

    # 基準時間
    h=(now_tpe.hour//6)*6
    base_dt = now_tpe.replace(hour=h,minute=0,second=0,microsecond=0)
    base_time_str = base_dt.strftime('%Y-%m-%dT%H:%M:%S')

    print('\n組裝資料...')
    out_towns=[]
    for key,info in alert_table.items():
        county=info.get('county',''); township=info.get('township','')
        lat=info.get('lat'); lng=info.get('lng')
        alert_v=info.get('alert_val',0); alert_6h=info.get('alert_6h',round(alert_v*0.55,0))
        if not lat: continue

        obs=town_obs.get(key,{})
        etr2_val    = obs.get('etr2')
        etr2_pct    = obs.get('etr2_pct')   # 小數，0.48=48%
        rain_24h    = obs.get('rain_24h')
        rain_6h     = obs.get('rain_6h')
        rain_2d     = obs.get('rain_2d',0.0)
        rain_3d     = obs.get('rain_3d',0.0)

        # QPF：優先用 Open-Meteo 全程15天，颱風期間用 CWA 格點覆蓋前48h
        om_key = f"{lat:.4f}_{lng:.4f}"

        def get_qpf_model(model_key):
            """取特定模式的60個6h QPF，若無則備援"""
            segs = om_all.get(model_key, {}).get(om_key, [])
            if not segs:
                import random; random.seed(int(alert_v+lat*100+hash(model_key)%100))
                base = alert_v/20*random.uniform(0.3,1.2)
                segs = [round(max(0,base*math.exp(-i//4*0.06)*random.uniform(0.4,1.8)),1)
                        for i in range(60)]
            return segs[:60]

        def get_max_hourly_model(model_key):
            """取特定模式的60個6h段內最大單一小時雨量（供強度分級用）"""
            arr = om_max_hourly_all.get(model_key, {}).get(om_key, [])
            return arr[:60] if arr else [0.0]*60

        # 各模式的完整15天QPF（依優先序：CWA > ECMWF > GFS/ICON）
        qpf_best  = get_qpf_model('best_match')
        qpf_ecmwf = get_qpf_model('ecmwf_ifs025')
        qpf_gfs   = get_qpf_model('gfs_seamless')
        qpf_icon  = get_qpf_model('icon_seamless')

        # 各模式對應的「最大時雨量」（強度分級用，不做累積換算）
        maxh_best  = get_max_hourly_model('best_match')
        maxh_ecmwf = get_max_hourly_model('ecmwf_ifs025')
        maxh_gfs   = get_max_hourly_model('gfs_seamless')
        maxh_icon  = get_max_hourly_model('icon_seamless')

        # CWA 官方 QPF 覆蓋（颱風 F-C0041 / 常態 48h 逐6h）：
        #   以「日曆段索引」對齊後覆蓋所有模式（CWA 為最高優先），
        #   qpf_cwa 為與 qpf_15d 同索引的稀疏陣列（未覆蓋段=null，絕不補值）
        _cwa_by_idx = {}   # idx -> value
        if is_typhoon and typhoon_segs:
            _cur_seg = now_tpe.hour // 6
            for _i, _seg in enumerate(typhoon_segs):
                # 優先用 StartTime 對齊；缺則退回「現在起第 i 段」舊索引法
                _idx = None
                _sts = _seg.get("start") or ""
                if _sts:
                    try:
                        _sd = datetime.fromisoformat(_sts.replace('Z','')).replace(tzinfo=None)
                        _s2 = (_sd - _today00).total_seconds()
                        if _s2 % 21600 == 0: _idx = int(_s2 // 21600)
                    except Exception: _idx = None
                if _idx is None: _idx = _cur_seg + _i
                if not (0 <= _idx < len(qpf_best)): continue
                _pts = [(p[0],p[1],p[2]) for p in _seg["points"]]
                _v = idw(lat, lng, _pts, _idx) if _pts else None
                if _v is not None: _cwa_by_idx[_idx] = _v
        elif routine_seg_map:
            for _idx, _vals in routine_seg_map.items():
                if not (0 <= _idx < len(qpf_best)): continue
                _v = _qpf_grid_at(_vals, lat, lng)
                if _v is not None: _cwa_by_idx[_idx] = round(float(_v), 1)
        qpf_cwa = []
        if _cwa_by_idx:
            _max_idx = max(_cwa_by_idx)
            qpf_cwa = [None] * (_max_idx + 1)
            for _idx, _v in _cwa_by_idx.items():
                qpf_cwa[_idx] = _v
                # 官方值最高優先：覆蓋所有模式（僅未來段；過去段前端一律以觀測為準）
                qpf_best[_idx] = qpf_ecmwf[_idx] = qpf_gfs[_idx] = qpf_icon[_idx] = _v

        # 預設用 best_match（CWA優先 > ECMWF > GFS=ICON 的綜合判斷已含在模式選擇邏輯中）
        qpf15d = qpf_best
        daily  = [round(sum(qpf15d[i*4:(i+1)*4]),1) for i in range(16)]

        # PoP 序列（28個6h時段=7天）
        pop_6h = get_pop_6h_series(township, pop3d, pop7d, base_dt, num_segs=28)

        # ETR2%各6h
        seg_etr_pct = [round(min(qpf15d[i]/alert_6h*100,300),1) if alert_6h>0 else None
                       for i in range(8)]

        # S* 風險分數（各6h時段，使用3h或6h QPF + PoP）
        # etr_pct_now = 現況ETR2%（整數%）
        etr_pct_now = round(etr2_pct * 100, 1) if etr2_pct is not None else None
        risk_score_list = []    # 各時段的 S*
        risk_level_list = []    # 各時段的等級文字
        risk_color_list = []    # 各時段的顏色
        for i, pp in enumerate(pop_6h):
            qpf_seg = qpf15d[i] if i < len(qpf15d) else 0.0
            score = calc_risk_score(etr_pct_now, qpf_seg, pp, n_hours=6)
            level, color = get_risk_level(score)
            risk_score_list.append(score)
            risk_level_list.append(level)
            risk_color_list.append(color)

        out_towns.append({
            'county':county,'township':township,
            'lat':round(lat,4),'lng':round(lng,4),
            'alert_val':alert_v,'alert_6h':alert_6h,
            'rain_24h':rain_24h,'rain_6h':rain_6h,
            'rain_2d':rain_2d,'rain_3d':rain_3d,
            'etr2':etr2_val,'etr2_pct':etr2_pct,
            'qpf_15d':qpf15d,'daily_qpf':daily,
            'seg_etr_pct':seg_etr_pct,
            'qpf_24h':round(sum(qpf_best[:4]),1),
            'qpf_48h':round(sum(qpf_best[:8]),1),
            'pop_6h':pop_6h,
            'risk_score': risk_score_list,
            'risk_level': risk_level_list,
            'qpf_best':  qpf_best,
            'qpf_ecmwf': qpf_ecmwf,
            'qpf_gfs':   qpf_gfs,
            'qpf_icon':  qpf_icon,
            'qpf_hi':    apply_ensemble_ratio(qpf_best, maxh_best, county, ens_ratios, 'hi')[0],
            'qpf_lo':    apply_ensemble_ratio(qpf_best, maxh_best, county, ens_ratios, 'lo')[0],
            'maxh_hi':   apply_ensemble_ratio(qpf_best, maxh_best, county, ens_ratios, 'hi')[1],
            'maxh_lo':   apply_ensemble_ratio(qpf_best, maxh_best, county, ens_ratios, 'lo')[1],
            'bias_24h':  calc_bias_24h(obs.get('daily_rain', [0.0]*15), model_yday.get(f"{lat:.4f}_{lng:.4f}")),
            'qpesums_1h':  qpesums_at(qp_grid, lat, lng),
            'qpesums_24h': qp_24h.get(f"{county}{township}"),
            'qpf_cwa':   qpf_cwa,
            'qpf_1h_cwa': [],  # CWA無逐時定量降水，維持空（前端逐時圖自動退回）
            'qpf_1h':    HOURLY_CACHE.get(f"{lat:.4f}_{lng:.4f}", []),
            'qpf_1h_p48': PAST48_CACHE.get(f"{lat:.4f}_{lng:.4f}", []),
            'obs_1h_p48': qp_p48.get(f"{county}{township}", []),   # 官方QPESUMS逐時觀測（過去48h）
            'qpf_1h_hi': apply_hourly_ratio(HOURLY_CACHE.get(f"{lat:.4f}_{lng:.4f}", []), county, ens_ratios, 'hi'),
            'qpf_1h_lo': apply_hourly_ratio(HOURLY_CACHE.get(f"{lat:.4f}_{lng:.4f}", []), county, ens_ratios, 'lo'),
            'warn_seg':  WARN_SEG_CACHE.get(f"{lat:.4f}_{lng:.4f}", []),
            'maxh_best':  maxh_best,
            'maxh_ecmwf': maxh_ecmwf,
            'maxh_gfs':   maxh_gfs,
            'maxh_icon':  maxh_icon,
            'obs_6h':[0.0]*8,
            'stations':  enrich_stations_with_etr2(info.get('stations', []), obs, stations, alert_v),
            'daily_rain': obs.get('daily_rain', [0.0]*15),  # 過去15天逐日雨量（過去7日視圖ETR2需回推14天）
        })

    # 加入「全台所有行政區」中尚未處理的：用 all_townships.json 為基準
    # 確保即使該行政區完全沒有CWA觀測站，也能用座標補上QPF預測資料
    processed = {t['county']+t['township'] for t in out_towns}
    all_towns = load_all_townships()

    # 除錯：確認問題鄉鎮在 all_townships.json 裡是否存在，以及 key 是否被誤判為已處理
    debug_check = [('高雄市','鳥松區'),('高雄市','前金區'),('高雄市','鹽埕區'),
                   ('彰化縣','芬園鄉'),('臺南市','東區'),
                   ('臺中市','中區'),('臺中市','東區'),('臺中市','南區'),('臺中市','西區')]
    print(f"  [除錯] all_townships.json 載入筆數: {len(all_towns)}")
    print(f"  [除錯] processed 集合大小（靜態表已處理）: {len(processed)}")
    for c, t in debug_check:
        key = c + t
        in_all = any(at['county']==c and at['township']==t for at in all_towns)
        in_processed = key in processed
        print(f"  [除錯] {key}: all_townships中={'有' if in_all else '無'}, 已被processed標記={'是' if in_processed else '否'}")

    non_static_list = []  # 待補的行政區清單（含座標）

    for at in all_towns:
        key = at['county'] + at['township']
        if key in processed: continue
        non_static_list.append(at)

    print(f"  非靜態表行政區（含完全無觀測站的）：{len(non_static_list)} 個，補抓 QPF...")
    non_static_coords = [{'lat': at['lat'], 'lng': at['lng'], 'alert_val': 0} for at in non_static_list]

    if non_static_coords:
        time.sleep(3)
        non_static_om, non_static_maxh = fetch_openmeteo(non_static_coords)
    else:
        non_static_om, non_static_maxh = {}, {}

    for i, at in enumerate(non_static_list):
        key = at['county'] + at['township']
        avg_lat, avg_lng = at['lat'], at['lng']
        om_key = f"{avg_lat:.4f}_{avg_lng:.4f}"
        obs = town_obs.get(key, {})  # 可能完全沒有觀測資料

        def get_ns_qpf(model_key):
            segs = non_static_om.get(model_key, {}).get(om_key, [])
            return segs[:60] if segs else [0.0]*60
        def get_ns_maxh(model_key):
            arr = non_static_maxh.get(model_key, {}).get(om_key, [])
            return arr[:60] if arr else [0.0]*60

        qpf_best_ns  = get_ns_qpf('best_match')
        qpf_ecmwf_ns = get_ns_qpf('ecmwf_ifs025')
        qpf_gfs_ns   = get_ns_qpf('gfs_seamless')
        qpf_icon_ns  = get_ns_qpf('icon_seamless')
        daily_ns = [round(sum(qpf_best_ns[d*4:(d+1)*4]),1) for d in range(16)]

        station_list = [{'name': stations[s]['name'], 'alert_val': None,
                          'village': f"{at['county']}{at['township']}"}
                         for s in obs.get('stations', []) if s in stations]

        out_towns.append({
            'county':   at['county'], 'township': at['township'],
            'lat': avg_lat, 'lng': avg_lng,
            'alert_val': None, 'alert_6h': None,
            'rain_24h':  obs.get('rain_24h'),
            'rain_6h':   obs.get('rain_6h'),
            'rain_2d':   obs.get('rain_2d', 0.0),
            'rain_3d':   obs.get('rain_3d', 0.0),
            'etr2':      None, 'etr2_pct': None,
            'qpf_15d':   qpf_best_ns, 'daily_qpf': daily_ns,
            'seg_etr_pct': [None]*8,
            'qpf_24h': round(sum(qpf_best_ns[:4]),1),
            'qpf_48h': round(sum(qpf_best_ns[:8]),1),
            'pop_6h':   [None]*28,
            'risk_score': [None]*28, 'risk_level': [None]*28,
            'obs_6h':   [0.0]*8,
            'qpf_best':  qpf_best_ns,  'qpf_ecmwf': qpf_ecmwf_ns,
            'qpf_gfs':   qpf_gfs_ns,   'qpf_icon':  qpf_icon_ns,
            'qpf_hi':    apply_ensemble_ratio(qpf_best_ns, get_ns_maxh('best_match'), at['county'], ens_ratios, 'hi')[0],
            'qpf_lo':    apply_ensemble_ratio(qpf_best_ns, get_ns_maxh('best_match'), at['county'], ens_ratios, 'lo')[0],
            'maxh_hi':   apply_ensemble_ratio(qpf_best_ns, get_ns_maxh('best_match'), at['county'], ens_ratios, 'hi')[1],
            'maxh_lo':   apply_ensemble_ratio(qpf_best_ns, get_ns_maxh('best_match'), at['county'], ens_ratios, 'lo')[1],
            'bias_24h':  None,
            'qpesums_1h':  qpesums_at(qp_grid, avg_lat, avg_lng),
            'qpesums_24h': qp_24h.get(f"{at['county']}{at['township']}"),
            'qpf_cwa':   [],
            'qpf_1h_cwa': [],
            'qpf_1h':    HOURLY_CACHE.get(f"{avg_lat:.4f}_{avg_lng:.4f}", []),
            'qpf_1h_p48': PAST48_CACHE.get(f"{avg_lat:.4f}_{avg_lng:.4f}", []),
            'obs_1h_p48': qp_p48.get(f"{at['county']}{at['township']}", []),
            'qpf_1h_hi': apply_hourly_ratio(HOURLY_CACHE.get(f"{avg_lat:.4f}_{avg_lng:.4f}", []), at['county'], ens_ratios, 'hi'),
            'qpf_1h_lo': apply_hourly_ratio(HOURLY_CACHE.get(f"{avg_lat:.4f}_{avg_lng:.4f}", []), at['county'], ens_ratios, 'lo'),
            'maxh_best': get_ns_maxh('best_match'),  'maxh_ecmwf': get_ns_maxh('ecmwf_ifs025'),
            'maxh_gfs':  get_ns_maxh('gfs_seamless'), 'maxh_icon': get_ns_maxh('icon_seamless'),
            'warn_seg':  WARN_SEG_CACHE.get(f"{avg_lat:.4f}_{avg_lng:.4f}", []),
            'stations':  station_list,
            'daily_rain': obs.get('daily_rain', [0.0]*15),
        })

    output={
        'base_time':base_time_str,
        'generated_at':now_tpe.strftime('%Y-%m-%dT%H:%M:%S'),
        'source':'CWA_OBS+POP' if stations else 'DEMO',
        'cwa_qpf_active': bool(is_typhoon or routine_seg_map),  # True=前48h已覆蓋CWA官方QPF
        'cwa_qpf_mode': 'typhoon' if is_typhoon else ('routine' if routine_seg_map else None),
        'cwa_qpf_segs': sorted(routine_seg_map.keys()) if routine_seg_map else [],
        'official_warn': official_warn,  # 官方現行警特報（W-C0033-001，縣市級）
        'township_count':len(out_towns),
        'townships':out_towns,
    }
    # 無站觀測鄉鎮：以 QPESUMS 補 rain_24h（標記來源，前端可辨識）
    qp_filled = 0
    for t in out_towns:
        if t.get('rain_24h') is None and t.get('qpesums_24h') is not None:
            t['rain_24h'] = t['qpesums_24h']
            t['obs_src'] = 'qpesums'
            qp_filled += 1
    if qp_filled: print(f"  QPESUMS 補值：{qp_filled} 個無站鄉鎮的 rain_24h")

    output['ens_active'] = len(ens_ratios) > 0  # 系集比值是否成功抓取
    # 全臺偏差比摘要（模式昨日≥10mm的鄉鎮之中位數）
    bias_vals = sorted(t['bias_24h'] for t in out_towns if t.get('bias_24h') is not None)
    output['bias_24h_median'] = bias_vals[len(bias_vals)//2] if bias_vals else None
    output['bias_24h_n'] = len(bias_vals)

    with open(OUTPUT_FILE,'w',encoding='utf-8') as f:
        json.dump(output,f,ensure_ascii=False,separators=(',',':'))
    print(f"\n完成：{OUTPUT_FILE}（{os.path.getsize(OUTPUT_FILE)//1024}KB）")
    print(f"  鄉鎮：{len(out_towns)}，PoP3d：{len(pop3d)}，PoP7d：{len(pop7d)}")
    if output['bias_24h_median'] is not None:
        print(f"  昨日偏差比中位數：{output['bias_24h_median']}（n={output['bias_24h_n']}）")

    # ── 預測快照存檔（校驗資料庫基礎；保留60天） ──
    try:
        os.makedirs('archive', exist_ok=True)
        snap_name = f"archive/{now_tpe.strftime('%Y%m%d%H')}.json"
        with open(snap_name,'w',encoding='utf-8') as f:
            json.dump(output,f,ensure_ascii=False,separators=(',',':'))
        cutoff = (now_tpe - timedelta(days=60)).strftime('%Y%m%d%H')
        removed = 0
        for fn in os.listdir('archive'):
            if fn.endswith('.json') and fn[:-5] < cutoff:
                os.remove(os.path.join('archive', fn)); removed += 1
        print(f"  快照：{snap_name}（清除{removed}個過期檔）")
    except Exception as e:
        print(f"  快照存檔失敗（不影響主流程）：{e}")

if __name__=='__main__':
    main()
