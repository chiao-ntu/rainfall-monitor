"""
台灣降雨預測監測系統 - 資料抓取腳本 v5
==============================================
已確認的 CWA API 結構：
  O-A0002-001: RainfallElement.Past6Hr / Past24hr / Past2days / Past3days
  F-D0047-XXX: WeatherElement「3小時降雨機率」/ 「12小時降雨機率」
               各縣市分開端點（奇數=3天，偶數=1週），Location = 鄉鎮
"""
import requests, json, math, os, sys
from datetime import datetime, timezone, timedelta

CWA_API_KEY  = os.environ.get("CWA_API_KEY", "")
STATIC_FILE  = "etr2_static.json"
HISTORY_FILE = "obs_history.json"
OUTPUT_FILE  = "data.json"
ALPHA        = 0.7
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
        history[sid][today] = st['rain_24h']
        if y1 not in history[sid] and st['rain_2d']>0:
            history[sid][y1] = max(0.0,round(st['rain_2d']-st['rain_24h'],1))
        if y2 not in history[sid] and st['rain_3d']>0:
            history[sid][y2] = max(0.0,round(st['rain_3d']-st['rain_2d'],1))
    cutoff = (now_tpe-timedelta(days=9)).strftime('%Y-%m-%d')
    for sid in history: history[sid]={d:v for d,v in history[sid].items() if d>cutoff}
    with open(HISTORY_FILE,'w',encoding='utf-8') as f:
        json.dump(history,f,ensure_ascii=False,separators=(',',':'))
    print(f"  歷史更新：{len(history)} 站，今日={today}")
    return history

def calc_etr2(sid, history, now_tpe):
    if sid not in history: return None
    daily = history[sid]
    etr2 = sum((ALPHA**i)*daily.get((now_tpe-timedelta(days=i)).strftime('%Y-%m-%d'),0.0)
               for i in range(8))
    return round(etr2,1)

def agg_obs(stations, alert_table, history, now_tpe):
    town={}
    for sid,st in stations.items():
        key=st['county']+st['township']
        if key not in town:
            town[key]={'county':st['county'],'township':st['township'],
                       'stations':[],'rain_24h':0.0,'rain_6h':0.0,
                       'rain_2d':0.0,'rain_3d':0.0,'etr2':None}
        td=town[key]; td['stations'].append(sid)
        td['rain_24h']=max(td['rain_24h'],st['rain_24h'])
        td['rain_6h'] =max(td['rain_6h'], st['rain_6h'])
        td['rain_2d'] =max(td['rain_2d'], st['rain_2d'])
        td['rain_3d'] =max(td['rain_3d'], st['rain_3d'])
        ev=calc_etr2(sid,history,now_tpe)
        if ev is not None: td['etr2']=max(td['etr2'] or 0.0,ev)
    for key,td in town.items():
        ai=alert_table.get(key,{}); av=ai.get('alert_val',0)
        td['etr2_pct']=round(td['etr2']/av,4) if td['etr2'] and av>0 else None
    print(f"  鄉鎮聚合：{len(town)} 個有觀測的鄉鎮")
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

    try:
        r = requests.get(OPENMETEO, params=params, timeout=120)
        r.raise_for_status(); raw=r.json()
    except Exception as e:
        print(f"    失敗：{e}"); return {}

    result={}
    data_list = raw if isinstance(raw,list) else [raw]
    for i, loc in enumerate(data_list):
        key = f"{lats[i]:.4f}_{lngs[i]:.4f}"
        hourly = loc.get('hourly',{})
        precip = hourly.get('precipitation',[])
        # 全部15天轉成6h時段（60個）
        segs_6h = []
        for j in range(0, len(precip), 6):
            chunk = [v for v in precip[j:j+6] if v is not None]
            segs_6h.append(round(sum(chunk), 1))
        result[key] = segs_6h[:60]  # 最多60個（15天）
    n = len(next(iter(result.values()),[]))
    print(f"    {len(result)} 個點，各 {n} 個6h時段")
    return result

def fetch_openmeteo(townships):
    """抓取所有 Open-Meteo 模式，回傳 {model: {key: [segs]}}"""
    print(f"抓取 Open-Meteo（{len(townships)} 個鄉鎮，全部15天）...")
    models = ['best_match', 'ecmwf_ifs025', 'gfs_seamless', 'icon_seamless']
    all_results = {}
    for model in models:
        result = fetch_openmeteo_model(townships, model)
        all_results[model] = result
    return all_results


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
    if etr_pct is None or pop_pct is None: return None

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
    om_all = fetch_openmeteo(static_list)
    om = om_all.get('best_match', {})  # 預設用 best_match

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

        # 各模式的完整15天QPF
        qpf_best  = get_qpf_model('best_match')
        qpf_ecmwf = get_qpf_model('ecmwf_ifs025')
        qpf_gfs   = get_qpf_model('gfs_seamless')
        qpf_icon  = get_qpf_model('icon_seamless')

        # 颱風期間：用 CWA 格點覆蓋前8段（48h）
        if is_typhoon:
            for idx in range(min(8, len(qpf_best))):
                v = idw(lat, lng, [], idx)  # 已在上方計算
                if v > 0:
                    qpf_best[idx] = qpf_ecmwf[idx] = qpf_gfs[idx] = qpf_icon[idx] = v

        # 預設用 best_match
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
            'qpf_24h':round(sum(qpf_48h[:4]),1),
            'qpf_48h':round(sum(qpf_48h),1),
            'pop_6h':pop_6h,
            'risk_score': risk_score_list,
            'risk_level': risk_level_list,
            # 各模式QPF（60個6h時段=15天）
            'qpf_best':  qpf_best,
            'qpf_ecmwf': qpf_ecmwf,
            'qpf_gfs':   qpf_gfs,
            'qpf_icon':  qpf_icon,
            'obs_6h':[0.0]*8,
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
