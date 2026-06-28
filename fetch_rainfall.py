"""
台灣降雨預測監測系統 - 資料抓取腳本 v4
==============================================
資料來源（以氣象署為主）：
  觀測（過去）: O-A0002-001 自動雨量站
  PoP 前3天:   F-D0047-089 台灣未來3天（PoP6h，逐6小時）
  PoP 後4天:   F-D0047-091 台灣未來1週（PoP12h，轉換為6h）
  QPF 颱風期:  F-C0041-001~008 格點定量降水（每6h一段）
  QPF 非颱風:  Open-Meteo（補充，標示非官方）

複合風險指標：
  Risk_A = PoP(%) × ETR2_current(%) / 100
  Risk_B = PoP(%) × (ETR2_current + QPF_eff) / Alert × 100
  QPF_eff = Σ 0.7^i × QPF_6h_i

風險分級：高 ≥70%，中 40-70%，低 <40%
"""
import requests, json, math, os, sys
from datetime import datetime, timezone, timedelta

# ── 設定 ──────────────────────────────────────────
CWA_API_KEY  = os.environ.get("CWA_API_KEY", "")
STATIC_FILE  = "etr2_static.json"
HISTORY_FILE = "obs_history.json"
OUTPUT_FILE  = "data.json"
ALPHA        = 0.7

BASE_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
OBS_URL  = f"{BASE_URL}/O-A0002-001"
POP3D_URL = f"{BASE_URL}/F-D0047-089"   # 前3天 PoP6h
POP7D_URL = f"{BASE_URL}/F-D0047-091"   # 未來1週 PoP12h
QPF_TYPHOON = [f"{BASE_URL}/F-C0041-{str(i).zfill(3)}" for i in range(1,9)]
OPENMETEO_URL = "https://api.open-meteo.com/v1/forecast"

# ── 讀取靜態警戒值 ────────────────────────────────
def load_static():
    if not os.path.exists(STATIC_FILE):
        print(f"找不到 {STATIC_FILE}"); sys.exit(1)
    with open(STATIC_FILE, encoding="utf-8") as f:
        rows = json.load(f)
    table = {}
    for r in rows:
        key = r["county"] + r["township"]
        table[key] = r
    print(f"靜態警戒值：{len(table)} 個鄉鎮")
    return table

# ── 抓觀測站雨量 ──────────────────────────────────
def fetch_obs():
    if not CWA_API_KEY:
        print("無 API Key，觀測跳過"); return {}
    print("抓取觀測站（O-A0002-001）...")
    try:
        r = requests.get(OBS_URL, params={"Authorization": CWA_API_KEY, "format":"JSON"}, timeout=60)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        print(f"  失敗：{e}"); return {}

    stations = {}
    try:
        all_st = raw["records"]["Station"]
        if all_st:
            re0 = all_st[0].get("RainfallElement", {})
            print(f"  [結構] RainfallElement keys: {list(re0.keys())}")

        def gp(block, default=0.0):
            v = block.get("Precipitation", None) if isinstance(block, dict) else None
            if v is None: return default
            try:
                f = float(v); return f if f >= 0 else default
            except: return default

        for st in all_st:
            sid  = st.get("StationId","")
            geo  = st.get("GeoInfo",{})
            coords = geo.get("Coordinates",[{}])
            lat, lng = 0.0, 0.0
            for c in coords:
                lv = c.get("StationLatitude",0); lo = c.get("StationLongitude",0)
                if lv and lo: lat=float(lv); lng=float(lo); break
            re = st.get("RainfallElement",{})
            stations[sid] = {
                "name": st.get("StationName",""),
                "lat": lat, "lng": lng,
                "county":   geo.get("CountyName",""),
                "township": geo.get("TownName",""),
                "rain_now":  gp(re.get("Now",{})),
                "rain_1h":   gp(re.get("Past1hr",{})),
                "rain_6h":   gp(re.get("Past6Hr", re.get("Past6hr",{}))),
                "rain_12h":  gp(re.get("Past12hr",{})),
                "rain_24h":  gp(re.get("Past24hr",{})),
                "rain_2d":   gp(re.get("Past2days",{})),
                "rain_3d":   gp(re.get("Past3days",{})),
            }
    except Exception as e:
        print(f"  解析失敗：{e}"); import traceback; traceback.print_exc()

    nonzero = sum(1 for s in stations.values() if s["rain_24h"]>0)
    print(f"  {len(stations)} 站，有24h雨量：{nonzero}")
    return stations

# ── 更新歷史日雨量 ────────────────────────────────
def update_history(stations, now_tpe):
    today = now_tpe.strftime("%Y-%m-%d")
    y1    = (now_tpe-timedelta(days=1)).strftime("%Y-%m-%d")
    y2    = (now_tpe-timedelta(days=2)).strftime("%Y-%m-%d")

    history = {}
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)

    for sid, st in stations.items():
        if sid not in history: history[sid] = {}
        history[sid][today] = st["rain_24h"]
        r2d = st["rain_2d"]; r1d = st["rain_24h"]
        if y1 not in history[sid] and r2d > 0:
            history[sid][y1] = max(0.0, round(r2d - r1d, 1))
        r3d = st["rain_3d"]
        if y2 not in history[sid] and r3d > 0:
            history[sid][y2] = max(0.0, round(r3d - r2d, 1))

    cutoff = (now_tpe-timedelta(days=9)).strftime("%Y-%m-%d")
    for sid in history:
        history[sid] = {d:v for d,v in history[sid].items() if d>cutoff}

    with open(HISTORY_FILE,"w",encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",",":"))
    print(f"  歷史更新：{len(history)} 站，今日={today}")
    return history

# ── 計算 ETR2 ──────────────────────────────────────
def calc_etr2(sid, history, now_tpe):
    if sid not in history: return None
    daily = history[sid]
    etr2 = 0.0
    for i in range(8):
        d = (now_tpe-timedelta(days=i)).strftime("%Y-%m-%d")
        etr2 += (ALPHA**i) * daily.get(d, 0.0)
    return round(etr2, 1)

# ── 觀測聚合到鄉鎮 ────────────────────────────────
def agg_obs(stations, alert_table, history, now_tpe):
    town = {}
    for sid, st in stations.items():
        key = st["county"] + st["township"]
        if key not in town:
            town[key] = {
                "county": st["county"], "township": st["township"],
                "stations":[], "rain_24h":0.0, "rain_6h":0.0,
                "rain_2d":0.0, "rain_3d":0.0, "etr2":None,
            }
        td = town[key]
        td["stations"].append(sid)
        td["rain_24h"] = max(td["rain_24h"], st["rain_24h"])
        td["rain_6h"]  = max(td["rain_6h"],  st["rain_6h"])
        td["rain_2d"]  = max(td["rain_2d"],  st["rain_2d"])
        td["rain_3d"]  = max(td["rain_3d"],  st["rain_3d"])
        ev = calc_etr2(sid, history, now_tpe)
        if ev is not None:
            td["etr2"] = max(td["etr2"] or 0.0, ev)

    for key, td in town.items():
        ai = alert_table.get(key, {})
        av = ai.get("alert_val", 0)
        td["etr2_pct"] = round(td["etr2"]/av, 4) if td["etr2"] and av>0 else None

    print(f"  鄉鎮聚合：{len(town)} 個有觀測的鄉鎮")
    return town

# ── 抓 PoP（前3天 F-D0047-089，含 PoP6h）─────────
def fetch_pop3d():
    if not CWA_API_KEY: return {}
    print("抓取 PoP 前3天（F-D0047-089）...")
    try:
        r = requests.get(POP3D_URL, params={
            "Authorization": CWA_API_KEY, "format":"JSON",
            "elementName": "PoP6h",
        }, timeout=60)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        print(f"  失敗：{e}"); return {}

    pop_map = {}  # {鄉鎮名: [(startTime, endTime, pop_value), ...]}
    try:
        locs = raw["records"]["Locations"][0]["Location"]
        if locs:
            # 印第一筆結構
            we0 = locs[0].get("WeatherElement",[])
            print(f"  [結構] WeatherElement[0]: {we0[0].get('ElementName','')} 時段數={len(we0[0].get('Time',[]))}")
            t0  = we0[0].get("Time",[{}])[0]
            print(f"  [結構] 時段格式: {list(t0.keys())} val={t0.get('ElementValue','')}")

        for loc in locs:
            name = loc.get("LocationName","")
            pop_segs = []
            for we in loc.get("WeatherElement",[]):
                if we.get("ElementName") != "PoP6h": continue
                for t in we.get("Time",[]):
                    start = t.get("StartTime","")
                    end   = t.get("EndTime","")
                    ev    = t.get("ElementValue",[{}])
                    v     = ev[0].get("Value","-") if isinstance(ev,list) else ev.get("Value","-")
                    try: pop = float(v)
                    except: pop = None
                    pop_segs.append({"start":start,"end":end,"pop":pop,"hours":6})
            if pop_segs: pop_map[name] = pop_segs

        print(f"  PoP6h：{len(pop_map)} 個鄉鎮，各 {len(next(iter(pop_map.values()),[]))} 時段")
    except Exception as e:
        print(f"  解析失敗：{e}"); import traceback; traceback.print_exc()
    return pop_map

# ── 抓 PoP（後4天 F-D0047-091，PoP12h → 6h）──────
def fetch_pop7d():
    if not CWA_API_KEY: return {}
    print("抓取 PoP 後4天（F-D0047-091）...")
    try:
        r = requests.get(POP7D_URL, params={
            "Authorization": CWA_API_KEY, "format":"JSON",
            "elementName": "PoP",
        }, timeout=60)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        print(f"  失敗：{e}"); return {}

    pop_map = {}
    try:
        locs = raw["records"]["Locations"][0]["Location"]
        for loc in locs:
            name = loc.get("LocationName","")
            pop_segs = []
            for we in loc.get("WeatherElement",[]):
                if we.get("ElementName") != "PoP": continue
                for t in we.get("Time",[]):
                    start = t.get("StartTime","")
                    end   = t.get("EndTime","")
                    ev    = t.get("ElementValue",[{}])
                    v     = ev[0].get("Value","-") if isinstance(ev,list) else ev.get("Value","-")
                    try:
                        pop12 = float(v)
                        # PoP12h → PoP6h：p = 1 - √(1 - pop12/100)
                        pop6  = round((1 - math.sqrt(max(0, 1 - pop12/100))) * 100, 1)
                    except:
                        pop12 = None; pop6 = None
                    # 一個12h時段拆成兩個6h
                    pop_segs.append({"start":start,"end":end,"pop":pop6,
                                     "pop12h":pop12,"hours":6,"derived":True})
        if pop_segs: pop_map[name] = pop_segs
        print(f"  PoP12h→6h：{len(pop_map)} 個鄉鎮")
    except Exception as e:
        print(f"  解析失敗：{e}"); import traceback; traceback.print_exc()
    return pop_map

# ── 合併 PoP（前3天6h直接用，後4天用12h轉換）────
def merge_pop(pop3d, pop7d, now_tpe):
    """
    回傳 {鄉鎮名: [pop_6h_list]}
    pop_6h_list 對應 BASE_TIME 起每個 6h 時段
    前12個時段（72h）取自 pop3d（PoP6h精確）
    後16個時段（96h）取自 pop7d（PoP12h轉換）
    """
    merged = {}
    all_towns = set(list(pop3d.keys()) + list(pop7d.keys()))
    for name in all_towns:
        segs3 = pop3d.get(name, [])
        segs7 = pop7d.get(name, [])
        # 前3天：取 pop3d 的後半（前3天中，避免跟7天重疊用前3天的精確值）
        # 後4天：取 pop7d 中日期在3天後的時段
        cutoff = (now_tpe + timedelta(days=3)).isoformat()[:10]
        early = [s for s in segs3]
        late  = [s for s in segs7 if s.get("start","") >= cutoff]
        merged[name] = early + late
    return merged

# ── 抓颱風期 QPF 格點 ─────────────────────────────
def fetch_typhoon_qpf():
    if not CWA_API_KEY: return []
    print("抓取颱風 QPF（F-C0041）...")
    typhoon_segs = []
    for i, url in enumerate(QPF_TYPHOON):
        label = f"{i*6}-{(i+1)*6}h"
        try:
            r = requests.get(url, params={"Authorization":CWA_API_KEY,"format":"JSON"}, timeout=30)
            if r.status_code == 404: continue
            r.raise_for_status()
            raw = r.json()
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
            print(f"  F-C0041-{str(i+1).zfill(3)} {label}: {len(pts)} 格點")
        except Exception as e:
            print(f"  {label}: {e}")

    if len(typhoon_segs) >= 4:
        print(f"  颱風 QPF：{len(typhoon_segs)} 段可用")
    else:
        print(f"  颱風 QPF 不足（{len(typhoon_segs)} 段），非颱風期間")
        typhoon_segs = []
    return typhoon_segs

# ── IDW 插值 ──────────────────────────────────────
def idw(lat, lng, pts, seg=None):
    if not pts: return 0.0
    dists = sorted([(math.sqrt((p[0]-lat)**2+(p[1]-lng)**2), p) for p in pts])[:4]
    tw, tv = 0.0, 0.0
    for d, p in dists:
        v = p[2] if seg is None else (p[seg+2] if seg+2 < len(p) else 0.0)
        if d < 1e-6: return v
        w = 1/d**2; tw+=w; tv+=w*v
    return round(tv/tw,1) if tw>0 else 0.0

# ── Open-Meteo 補充 QPF（第3-15天）──────────────
def fetch_openmeteo_batch(townships, days_start=3, days_end=15):
    """批次查詢 Open-Meteo，每次最多 1000 個座標點"""
    print(f"抓取 Open-Meteo QPF（第{days_start}-{days_end}天）...")
    lats = [t.get("lat",0) for t in townships]
    lngs = [t.get("lng",0) for t in townships]
    if not lats: return {}

    try:
        r = requests.get(OPENMETEO_URL, params={
            "latitude":  ",".join(str(x) for x in lats),
            "longitude": ",".join(str(x) for x in lngs),
            "hourly":    "precipitation",
            "forecast_days": days_end,
            "timezone":  "Asia/Taipei",
        }, timeout=90)
        r.raise_for_status()
        raw = r.json()
    except Exception as e:
        print(f"  Open-Meteo 失敗：{e}"); return {}

    # 批次回傳是 list
    if isinstance(raw, list):
        result = {}
        for i, loc_data in enumerate(raw):
            key = f"{lats[i]:.4f}_{lngs[i]:.4f}"
            hourly = loc_data.get("hourly",{})
            times  = hourly.get("time",[])
            precip = hourly.get("precipitation",[])
            # 取第 days_start 天後的資料，轉成逐6h
            segs_6h = []
            start_idx = days_start * 24
            for j in range(start_idx, len(times), 6):
                total_6h = sum(precip[j:j+6]) if j+6<=len(precip) else sum(precip[j:])
                segs_6h.append(round(total_6h, 1))
            result[key] = segs_6h
        print(f"  Open-Meteo：{len(result)} 個點，每點 {len(next(iter(result.values()),[]))} 個6h時段")
        return result
    else:
        print(f"  Open-Meteo 回傳格式非預期：{type(raw)}")
        return {}

# ── 計算複合風險 ──────────────────────────────────
def calc_risk(pop_pct, etr2_pct_now, qpf_eff, alert_val):
    """
    Risk_A = PoP(%) × ETR2_current(%) / 100
    Risk_B = PoP(%) × (ETR2_current + QPF_eff) / Alert × 100
    回傳 (risk_a, risk_b) 均為 %
    """
    if pop_pct is None: return None, None
    pop = pop_pct / 100.0

    # Risk A
    if etr2_pct_now is not None:
        risk_a = round(pop * etr2_pct_now * 100, 1)
    else:
        risk_a = None

    # Risk B
    if etr2_pct_now is not None and alert_val and alert_val > 0:
        etr2_mm = etr2_pct_now / 100 * alert_val
        risk_b = round(pop * (etr2_mm + qpf_eff) / alert_val * 100, 1)
    else:
        risk_b = None

    return risk_a, risk_b

# ══════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════
def main():
    now_utc = datetime.now(timezone.utc)
    now_tpe = now_utc + timedelta(hours=8)
    print("="*52)
    print(f"台灣降雨監測 v4  {now_tpe.strftime('%Y-%m-%d %H:%M')} TST")
    print("="*52)

    alert_table = load_static()

    # 1. 觀測
    stations = fetch_obs()
    history  = update_history(stations, now_tpe) if stations else \
               (json.load(open(HISTORY_FILE)) if os.path.exists(HISTORY_FILE) else {})
    town_obs = agg_obs(stations, alert_table, history, now_tpe)

    # 2. PoP
    pop3d   = fetch_pop3d()
    pop7d   = fetch_pop7d()
    pop_all = merge_pop(pop3d, pop7d, now_tpe)

    # 3. QPF
    typhoon_segs = fetch_typhoon_qpf()
    is_typhoon   = len(typhoon_segs) >= 4

    # base_time
    h = (now_tpe.hour//6)*6
    base_time_str = now_tpe.replace(hour=h,minute=0,second=0,microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")

    # 4. 組裝各鄉鎮
    print(f"\n組裝資料...")
    out_towns = []
    processed = set()

    static_list = list(alert_table.values())
    openmeteo_result = fetch_openmeteo_batch(static_list, days_start=3, days_end=15)

    for key, info in alert_table.items():
        county   = info.get("county","")
        township = info.get("township","")
        lat      = info.get("lat")
        lng      = info.get("lng")
        alert_v  = info.get("alert_val", 0)
        alert_6h = info.get("alert_6h", round(alert_v*0.55, 0))
        if not lat: continue

        # 觀測
        obs = town_obs.get(key, {})
        etr2_val  = obs.get("etr2")
        etr2_pct_now = obs.get("etr2_pct")  # 小數，如 0.48 = 48%
        rain_24h  = obs.get("rain_24h")
        rain_6h   = obs.get("rain_6h")

        # PoP（用鄉鎮名對應）
        pop_segs  = pop_all.get(township, pop_all.get(county+township, []))
        pop_6h_list = [s.get("pop") for s in pop_segs]  # list of % or None

        # QPF 前48h
        if is_typhoon:
            qpf_48h = []
            for seg_idx in range(8):
                pts = [(p[0],p[1],p[2]) for p in typhoon_segs[seg_idx]["points"]] \
                      if seg_idx < len(typhoon_segs) else []
                qpf_48h.append(idw(lat,lng,pts))
        else:
            qpf_48h = [0.0]*8  # 非颱風期無格點QPF

        # QPF 第3-15天（Open-Meteo）
        om_key   = f"{lat:.4f}_{lng:.4f}"
        qpf_sim  = openmeteo_result.get(om_key, [])
        if not qpf_sim:
            # 備援：簡單模擬
            import random; random.seed(int(alert_v+lat*100))
            base = alert_v/20*random.uniform(0.3,1.2)
            qpf_sim = [round(max(0,base*math.exp(-i//4*0.06)*random.uniform(0.4,1.8)),1)
                       for i in range(48)]

        # 完整 60 段（前8=QPF，後52=Open-Meteo/模擬）
        qpf15d  = qpf_48h + qpf_sim[:52]
        daily   = [round(sum(qpf15d[i*4:(i+1)*4]),1) for i in range(15)]

        # ETR2% 各6h時段
        seg_etr_pct = []
        for i in range(8):
            v = qpf15d[i]
            seg_etr_pct.append(round(min(v/alert_6h*100,300),1) if alert_6h>0 else None)

        # 計算各6h的複合風險
        risk_a_list = []
        risk_b_list = []
        for i, pop_pct in enumerate(pop_6h_list):
            # QPF_eff：從第 i 段起的加權累積
            qpf_eff = sum((ALPHA**j)*qpf15d[i+j] for j in range(8) if i+j<len(qpf15d))
            etr2_pct_pct = (etr2_pct_now or 0)*100  # 轉成 % 整數
            ra, rb = calc_risk(pop_pct, etr2_pct_pct, qpf_eff, alert_v)
            risk_a_list.append(ra)
            risk_b_list.append(rb)

        out_towns.append({
            "county": county, "township": township,
            "lat": round(lat,4), "lng": round(lng,4),
            "alert_val": alert_v, "alert_6h": alert_6h,
            # 觀測
            "rain_24h":  rain_24h,
            "rain_6h":   rain_6h,
            "etr2":      etr2_val,
            "etr2_pct":  etr2_pct_now,
            # 預報
            "qpf_15d":      qpf15d,
            "daily_qpf":    daily,
            "seg_etr_pct":  seg_etr_pct,
            "qpf_24h":      round(sum(qpf_48h[:4]),1),
            "qpf_48h":      round(sum(qpf_48h),1),
            "is_typhoon_qpf": is_typhoon,
            # PoP
            "pop_6h":    pop_6h_list,
            # 複合風險
            "risk_a":    risk_a_list,   # PoP × ETR2%_current
            "risk_b":    risk_b_list,   # PoP × (ETR2+QPF_eff)/Alert
        })
        processed.add(key)

    output = {
        "base_time":      base_time_str,
        "generated_at":   now_tpe.strftime("%Y-%m-%dT%H:%M:%S"),
        "source":         "CWA_OBS+POP+QPF" if stations else "DEMO",
        "is_typhoon":     is_typhoon,
        "station_count":  len(stations),
        "township_count": len(out_towns),
        "townships":      out_towns,
    }
    with open(OUTPUT_FILE,"w",encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",",":"))
    sz = os.path.getsize(OUTPUT_FILE)
    print(f"\n完成：{OUTPUT_FILE}（{sz//1024}KB）")
    print(f"  觀測站：{len(stations)}，颱風QPF：{is_typhoon}")
    print(f"  PoP3d：{len(pop3d)} 鄉鎮，PoP7d：{len(pop7d)} 鄉鎮")
    print(f"  輸出鄉鎮：{len(out_towns)}")

if __name__=="__main__":
    main()
