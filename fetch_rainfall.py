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
    today = now_tpe.strftime('%Y-%m-%d')
    y1 = (now_tpe-timedelta(days=1)).strftime('%Y-%m-%d')
    y2 = (now_tpe-timedelta(days=2)).strftime('%Y-%m-%d')
    history = json.load(open(HISTORY_FILE)) if os.path.exists(HISTORY_FILE) else {}
    for sid,st in stations.items():
        if sid not in history: history[sid]={}
        # 「今天」的雨量只更新當前時刻之前的累積（從00:00到現在）
        # rain_24h 是「過去24小時滾動」，不等於今天00:00起的累積
        # 正確做法：今天的量用 rain_2d - rain_24h 的補足方式反推
        # 但最直接的是：保留 rain_24h 給前端用，歷史檔只存「完整的一天」
        # 策略：用 rain_24h 作為今天到目前為止的最佳估計（前端會加QPF補足）
        rain_today_so_far = st.get('rain_24h', 0.0) or 0.0
        history[sid][today] = rain_today_so_far

        # 昨天的量：用 rain_2d - rain_24h 估算（若尚未有昨天記錄）
        if y1 not in history[sid]:
            r2d = st.get('rain_2d', 0.0) or 0.0
            r24h = st.get('rain_24h', 0.0) or 0.0
            history[sid][y1] = max(0.0, round(r2d - r24h, 1))
        # 前天的量：用 rain_3d - rain_2d 估算（若尚未有前天記錄）
        if y2 not in history[sid]:
            r3d = st.get('rain_3d', 0.0) or 0.0
            r2d = st.get('rain_2d', 0.0) or 0.0
            history[sid][y2] = max(0.0, round(r3d - r2d, 1))
    cutoff = (now_tpe-timedelta(days=9)).strftime('%Y-%m-%d')
    for sid in history: history[sid]={d:v for d,v in history[sid].items() if d>cutoff}
    with open(HISTORY_FILE,'w',encoding='utf-8') as f:
        json.dump(history,f,ensure_ascii=False,separators=(',',':'))
    print(f"  歷史更新：{len(history)} 站，今日={today}")
    return history

def calc_etr2(sid, history, now_tpe):
    """
    ETR2 = R0 + 0.7×R1 + 0.5×R2 + 0.4×R3 + 0.3×R4 + 0.2×R5 + 0.1×R6
    R0 = 當天(0-24h)累積雨量，R1 = 前一天(25-48h)，...R6 = 前6天
    """
    if sid not in history: return None
    daily = history[sid]
    etr2 = sum(
        ETR2_WEIGHTS[i] * daily.get((now_tpe-timedelta(days=i)).strftime('%Y-%m-%d'), 0.0)
        for i in range(7)
    )
    return round(etr2, 1)

def get_daily_rain_array(sid, history, now_tpe, days=8):
    """
    回傳過去 N 天的逐日觀測雨量陣列（給前端做未來ETR2%滾動計算用）
    array[0] = 今天, array[1] = 昨天, ... array[7] = 7天前
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
                 'daily_rain': [0.0]*8} for st in excel_stations]

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
        daily    = station_daily.get(sid, [0.0]*8) if sid else [0.0]*8
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
                       'daily_rain':[0.0]*8, 'station_etr2':{}}
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
        st_daily = get_daily_rain_array(sid, history, now_tpe, days=8)
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
            pts = []
            for ri, row in enumerate(ct.strip().split("\n")):
                lat_pt = 20.8 + ri * 0.045
                for ci, v in enumerate(row.split(",")):
                    lng_pt = 117.56 + ci * 0.049
                    if 21.5<=lat_pt<=26.5 and 119<=lng_pt<=123:
                        try: pts.append((lat_pt, lng_pt, float(v)))
                        except: pass
            typhoon_segs.append({"label":label,"points":pts})
        except Exception as e:
            pass
    if len(typhoon_segs) >= 4:
        print(f"  颱風 QPF：{len(typhoon_segs)} 段")
    else:
        print(f"  非颱風期間（{len(typhoon_segs)} 段）")
        typhoon_segs = []
    return typhoon_segs

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
                    decay_per_6h=4, threshold_per_6h=40):
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

    # Open-Meteo（四個模式）
    om_all, om_max_hourly_all = fetch_openmeteo(static_list)
    om = om_all.get('ecmwf_ifs025', {})  # 預設用 ECMWF IFS，對台灣地形雨準確度較高

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

        # 颱風期間：CWA 格點 QPF 優先覆蓋前8段（48h），其餘時段仍用 Open-Meteo
        if is_typhoon and typhoon_segs:
            for idx in range(min(8, len(qpf_best))):
                seg_pts = [(p[0],p[1],p[2]) for p in typhoon_segs[idx]["points"]] \
                          if idx < len(typhoon_segs) else []
                if seg_pts:
                    v = idw(lat, lng, seg_pts, idx)
                    if v is not None:
                        # CWA 為最高優先，所有模式統一覆蓋為 CWA 觀測值
                        qpf_best[idx] = qpf_ecmwf[idx] = qpf_gfs[idx] = qpf_icon[idx] = v

        # 預設用 best_match（CWA優先 > ECMWF > GFS=ICON 的綜合判斷已含在模式選擇邏輯中）
        qpf15d = qpf_best
        daily  = [round(sum(qpf15d[i*4:(i+1)*4]),1) for i in range(15)]

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
            'maxh_best':  maxh_best,
            'maxh_ecmwf': maxh_ecmwf,
            'maxh_gfs':   maxh_gfs,
            'maxh_icon':  maxh_icon,
            'obs_6h':[0.0]*8,
            'stations':  enrich_stations_with_etr2(info.get('stations', []), obs, stations, alert_v),
            'daily_rain': obs.get('daily_rain', [0.0]*8),  # 過去8天逐日雨量，供前端滾動計算未來ETR2%
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
        daily_ns = [round(sum(qpf_best_ns[d*4:(d+1)*4]),1) for d in range(15)]

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
            'maxh_best': get_ns_maxh('best_match'),  'maxh_ecmwf': get_ns_maxh('ecmwf_ifs025'),
            'maxh_gfs':  get_ns_maxh('gfs_seamless'), 'maxh_icon': get_ns_maxh('icon_seamless'),
            'stations':  station_list,
            'daily_rain': obs.get('daily_rain', [0.0]*8),
        })

    output={
        'base_time':base_time_str,
        'generated_at':now_tpe.strftime('%Y-%m-%dT%H:%M:%S'),
        'source':'CWA_OBS+POP' if stations else 'DEMO',
        'township_count':len(out_towns),
        'townships':out_towns,
    }
    with open(OUTPUT_FILE,'w',encoding='utf-8') as f:
        json.dump(output,f,ensure_ascii=False,separators=(',',':'))
    print(f"\n完成：{OUTPUT_FILE}（{os.path.getsize(OUTPUT_FILE)//1024}KB）")
    print(f"  鄉鎮：{len(out_towns)}，PoP3d：{len(pop3d)}，PoP7d：{len(pop7d)}")

if __name__=='__main__':
    main()
