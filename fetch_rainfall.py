"""
台灣降雨預測監測系統 - 資料抓取腳本 v3
=====================================================
資料來源：
  觀測（過去）: CWA O-A0002-001 自動雨量站
  預報（未來）: CWA F-C0034-007 QPF格點預報
ETR2 計算式：R_t = Σ(i=0~7) 0.7^i × R_i
  R_i = 過去第 i 個 24h 的累積雨量（R0=最近24h）
  需要維護 obs_history.json 累積8天歷史
"""
import requests, json, math, os, sys
from datetime import datetime, timezone, timedelta

# ── 設定 ──────────────────────────────────────────
CWA_API_KEY  = os.environ.get("CWA_API_KEY", "")
STATIC_FILE  = "etr2_static.json"   # 各鄉鎮警戒值
HISTORY_FILE = "obs_history.json"   # 過去8天各站日雨量（自動累積）
OUTPUT_FILE  = "data.json"          # 輸出給地圖
ALPHA        = 0.7                  # ETR2 加權係數

OBS_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001"
QPF_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0034-007"

# ── 讀取靜態警戒值表 ──────────────────────────────
def load_static():
    if not os.path.exists(STATIC_FILE):
        print(f"找不到 {STATIC_FILE}"); sys.exit(1)
    with open(STATIC_FILE, encoding="utf-8") as f:
        rows = json.load(f)
    # 建立 {縣市+鄉鎮 → {alert_val, alert_6h}} 查詢表
    table = {}
    for r in rows:
        key = r["county"] + r["township"]
        table[key] = {
            "alert_val": r.get("alert_val", 0),
            "alert_6h":  r.get("alert_6h",  0),
            "lat": r.get("lat"), "lng": r.get("lng"),
        }
    print(f"靜態警戒值表：{len(table)} 個鄉鎮")
    return table

# ── 抓觀測站即時雨量 ─────────────────────────────
def fetch_obs():
    if not CWA_API_KEY:
        print("未設定 CWA_API_KEY，觀測使用歷史快取"); return {}
    print("抓取 CWA 觀測站（O-A0002-001）...")
    try:
        resp = requests.get(OBS_URL, params={
            "Authorization": CWA_API_KEY,
            "format": "JSON",
        }, timeout=60)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        print(f"  觀測抓取失敗：{e}"); return {}

    stations = {}
    try:
        for st in raw["records"]["Station"]:
            sid   = st.get("StationId", "")
            sname = st.get("StationName", "")
            geo   = st.get("GeoInfo", {})
            coords = geo.get("Coordinates", [{}])
            lat = float(coords[0].get("StationLatitude",  0))
            lng = float(coords[0].get("StationLongitude", 0))
            county   = geo.get("CountyName", "")
            township = geo.get("TownName", "")
            we = st.get("WeatherElement", {})

            def safe_float(val, default=0.0):
                try: return float(val)
                except: return default

            stations[sid] = {
                "name":      sname,
                "lat":       lat,
                "lng":       lng,
                "county":    county,
                "township":  township,
                "rain_10m":  safe_float(we.get("Now",{}).get("Precipitation", 0)),
                "rain_1h":   safe_float(we.get("Past1hour",{}).get("Precipitation", 0)),
                "rain_6h":   safe_float(we.get("Past6hours",{}).get("Precipitation", 0)),
                "rain_12h":  safe_float(we.get("Past12hours",{}).get("Precipitation", 0)),
                "rain_24h":  safe_float(we.get("Past24hours",{}).get("Precipitation", 0)),
            }
    except Exception as e:
        print(f"  觀測解析失敗：{e}")
    print(f"  取得 {len(stations)} 個觀測站")
    return stations

# ── 更新歷史日雨量（obs_history.json）────────────
def update_history(stations, now_tpe):
    """
    每次執行時把今天的 rain_24h 存入歷史，保留最近8天
    格式：{站號: {日期字串: rain_24h, ...}}
    """
    today_str = now_tpe.strftime("%Y-%m-%d")

    # 讀取既有歷史
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, encoding="utf-8") as f:
            history = json.load(f)
    else:
        history = {}

    # 更新今日數值
    for sid, st in stations.items():
        if sid not in history:
            history[sid] = {}
        # 只在每天 05/11/17/23 時的第一次執行更新日雨量
        # 實際上每次都更新最新值（覆蓋同一天）
        history[sid][today_str] = st["rain_24h"]

    # 清理8天前的舊資料
    cutoff = (now_tpe - timedelta(days=9)).strftime("%Y-%m-%d")
    for sid in history:
        history[sid] = {d: v for d, v in history[sid].items() if d > cutoff}

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",",":"))
    print(f"  歷史檔案更新：{len(history)} 站 × 最近8天")
    return history

# ── 計算 ETR2 ──────────────────────────────────
def calc_etr2(station_id, history, now_tpe):
    """
    R_t = Σ α^i × R_i，i=0~7
    R_i = 過去第 i 天的日雨量（R0=今天/最近24h）
    """
    if station_id not in history:
        return None
    daily = history[station_id]
    etr2 = 0.0
    for i in range(8):
        date_i = (now_tpe - timedelta(days=i)).strftime("%Y-%m-%d")
        r_i = daily.get(date_i, 0.0)
        etr2 += (ALPHA ** i) * r_i
    return round(etr2, 1)

# ── 抓 QPF 格點預報 ───────────────────────────────
def fetch_qpf():
    if not CWA_API_KEY:
        print("未設定 CWA_API_KEY，QPF 使用模擬"); return None
    print("抓取 CWA QPF（F-C0034-007）...")
    try:
        resp = requests.get(QPF_URL, params={
            "Authorization": CWA_API_KEY,
            "format": "JSON",
        }, timeout=60)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        print(f"  QPF 失敗：{e}"); return None
    try:
        locations = raw["records"]["locations"][0]["location"]
    except Exception as e:
        print(f"  QPF 格式錯誤：{e}"); return None

    grid, base_time = [], None
    for loc in locations:
        lat, lon = float(loc.get("lat",0)), float(loc.get("lon",0))
        if not (21.5<=lat<=26.5 and 119<=lon<=123): continue
        qpf_6h = []
        for elem in loc.get("weatherElement",[]):
            if elem.get("elementName") != "Precipitation": continue
            times = elem.get("time",[])
            if not base_time and times:
                base_time = times[0].get("startTime","")
            for t in times[:8]:
                try: qpf_6h.append(round(float(t.get("elementValue",[{}])[0].get("value","0")),1))
                except: qpf_6h.append(0.0)
        if qpf_6h: grid.append({"lat":lat,"lng":lon,"qpf_6h":qpf_6h})
    print(f"  QPF：{len(grid)} 格點，基準：{base_time}")
    return {"base_time": base_time, "grid": grid}

# ── IDW 空間插值 ──────────────────────────────────
def idw(lat, lng, points, value_key, seg=None):
    """points = [{lat, lng, value_key}, ...]"""
    if not points: return 0.0
    dists = []
    for p in points:
        d = math.sqrt((p["lat"]-lat)**2 + (p["lng"]-lng)**2)
        dists.append((d, p))
    dists.sort(key=lambda x: x[0])
    nearest = dists[:4]
    tw, tv = 0.0, 0.0
    for d, p in nearest:
        if d < 1e-6:
            v = p[value_key][seg] if seg is not None else p[value_key]
            return v
        w = 1.0 / d**2
        v = p[value_key][seg] if seg is not None else p[value_key]
        tw += w; tv += w*v
    return round(tv/tw, 1) if tw > 0 else 0.0

# ── 觀測站聚合到鄉鎮 ──────────────────────────────
def aggregate_obs_to_township(stations, alert_table, history, now_tpe):
    """
    把觀測站資料聚合到鄉鎮層級
    同一鄉鎮可能有多個站，取最大值（保守原則）
    回傳：{縣市+鄉鎮: {rain_24h, rain_6h, etr2, etr2_pct, station_ids}}
    """
    town_data = {}
    for sid, st in stations.items():
        key = st["county"] + st["township"]
        if key not in town_data:
            town_data[key] = {
                "county":   st["county"],
                "township": st["township"],
                "stations": [],
                "rain_24h": 0.0,
                "rain_6h":  0.0,
                "rain_1h":  0.0,
                "etr2":     None,
            }
        # 取各站最大值（保守側）
        td = town_data[key]
        td["stations"].append(sid)
        td["rain_24h"] = max(td["rain_24h"], st["rain_24h"])
        td["rain_6h"]  = max(td["rain_6h"],  st["rain_6h"])
        td["rain_1h"]  = max(td["rain_1h"],  st["rain_1h"])

        # 計算該站的 ETR2
        etr2_val = calc_etr2(sid, history, now_tpe)
        if etr2_val is not None:
            if td["etr2"] is None:
                td["etr2"] = etr2_val
            else:
                td["etr2"] = max(td["etr2"], etr2_val)  # 取最大

    # 計算 ETR2%
    for key, td in town_data.items():
        alert_info = alert_table.get(key, {})
        alert_val  = alert_info.get("alert_val", 0)
        if td["etr2"] is not None and alert_val > 0:
            td["etr2_pct"] = round(td["etr2"] / alert_val, 4)
        else:
            td["etr2_pct"] = None

    print(f"  鄉鎮聚合：{len(town_data)} 個有觀測站的鄉鎮")
    return town_data

# ── 模擬第3-15天 ──────────────────────────────────
def sim_day3_15(alert_val, avg_6h_qpf):
    import random; random.seed(int(alert_val))
    res = []
    for i in range(52):  # 13天 × 4段
        d   = i // 4 + 2
        dec = math.exp(-d * 0.06)
        res.append(round(max(0.0, avg_6h_qpf*dec*random.uniform(0.4,1.8)), 1))
    return res

# ══════════════════════════════════════════════════
# 主程式
# ══════════════════════════════════════════════════
def main():
    now_utc = datetime.now(timezone.utc)
    now_tpe = now_utc + timedelta(hours=8)
    print("=" * 52)
    print(f"台灣降雨監測  {now_tpe.strftime('%Y-%m-%d %H:%M')} TST")
    print("=" * 52)

    # 1. 靜態警戒值
    alert_table = load_static()

    # 2. 抓觀測資料
    stations = fetch_obs()

    # 3. 更新歷史日雨量（ETR2 計算用）
    if stations:
        history = update_history(stations, now_tpe)
    else:
        history = json.load(open(HISTORY_FILE)) if os.path.exists(HISTORY_FILE) else {}

    # 4. 觀測聚合到鄉鎮
    town_obs = aggregate_obs_to_township(stations, alert_table, history, now_tpe)

    # 5. 抓 QPF
    qpf_res = fetch_qpf()
    grid    = qpf_res["grid"] if qpf_res else []

    # 6. 決定基準時間
    if qpf_res and qpf_res.get("base_time"):
        base_time_str = qpf_res["base_time"]
    else:
        h = (now_tpe.hour // 6) * 6
        base_time_str = now_tpe.replace(
            hour=h, minute=0, second=0, microsecond=0
        ).strftime("%Y-%m-%dT%H:%M:%S")

    # 7. 組裝各鄉鎮輸出資料
    print(f"\n組裝資料...")
    out_towns = []

    # 以觀測站有資料的鄉鎮為主，其餘補上靜態警戒值
    # 先從有觀測的鄉鎮建立清單，再補入靜態表有但觀測沒覆蓋的
    processed_keys = set()

    for key, obs in town_obs.items():
        alert_info = alert_table.get(key, {})
        alert_val  = alert_info.get("alert_val", 0)
        alert_6h   = alert_info.get("alert_6h",  0)

        # 鄉鎮座標：優先用靜態表的代表座標
        lat = alert_info.get("lat")
        lng = alert_info.get("lng")
        if not lat:  # 靜態表沒有 → 用觀測站平均
            st_list = [stations[s] for s in obs["stations"] if s in stations]
            if st_list:
                lat = sum(s["lat"] for s in st_list) / len(st_list)
                lng = sum(s["lng"] for s in st_list) / len(st_list)
        if not lat:
            continue

        # QPF 插值（前8個6h時段）
        if grid:
            qpf_48h = [idw(lat, lng, grid, "qpf_6h", i) for i in range(8)]
        else:
            qpf_48h = [0.0] * 8

        # 第3-15天模擬
        avg6h   = sum(qpf_48h) / 8 if any(qpf_48h) else 0.0
        qpf_sim = sim_day3_15(alert_val, avg6h)
        qpf15d  = qpf_48h + qpf_sim
        daily   = [round(sum(qpf15d[i*4:(i+1)*4]), 1) for i in range(15)]

        # 6h ETR2%（QPF 對 6h 警戒值的比）
        seg_etr_pct = []
        for i in range(8):
            if alert_6h > 0:
                seg_etr_pct.append(round(min(qpf48h_v/alert_6h*100, 300), 1)
                                   if (qpf48h_v := qpf_48h[i]) else 0.0)
            else:
                seg_etr_pct.append(None)

        # 過去 8 個 6h（用 rain_6h 近似，精確版需要歷史6h資料）
        obs_6h = [obs["rain_6h"]] + [0.0] * 7  # 暫時只有最近6h

        out_towns.append({
            "county":      obs["county"],
            "township":    obs["township"],
            "lat":         round(lat, 4),
            "lng":         round(lng, 4),
            "alert_val":   alert_val,
            "alert_6h":    alert_6h,
            # 觀測值（現況）
            "rain_24h":    obs["rain_24h"],   # 近24h累積觀測
            "rain_6h":     obs["rain_6h"],    # 近6h累積觀測
            "etr2":        obs["etr2"],        # 加權有效雨量
            "etr2_pct":    obs["etr2_pct"],   # 現況ETR2%（觀測）
            # 預報值（未來）
            "qpf_15d":     qpf15d,
            "daily_qpf":   daily,
            "seg_etr_pct": seg_etr_pct,
            "qpf_24h":     round(sum(qpf_48h[:4]), 1),
            "qpf_48h":     round(sum(qpf_48h), 1),
            "obs_6h":      obs_6h,
        })
        processed_keys.add(key)

    # 補入靜態表有但觀測沒覆蓋的鄉鎮（顯示白色，僅有QPF預報）
    for key, info in alert_table.items():
        if key in processed_keys: continue
        lat, lng = info.get("lat"), info.get("lng")
        if not lat: continue
        alert_val = info.get("alert_val", 0)
        alert_6h  = info.get("alert_6h", 0)
        if grid:
            qpf_48h = [idw(lat, lng, grid, "qpf_6h", i) for i in range(8)]
        else:
            qpf_48h = [0.0] * 8
        avg6h   = sum(qpf_48h) / 8 if any(qpf_48h) else 0.0
        qpf_sim = sim_day3_15(alert_val, avg6h)
        qpf15d  = qpf_48h + qpf_sim
        daily   = [round(sum(qpf15d[i*4:(i+1)*4]),1) for i in range(15)]
        out_towns.append({
            "county":      key[:3] if len(key)>=3 else "",
            "township":    info.get("township",""),
            "lat":         round(lat,4), "lng": round(lng,4),
            "alert_val":   alert_val, "alert_6h": alert_6h,
            "rain_24h":    None, "rain_6h": None,
            "etr2":        None, "etr2_pct": None,  # 無觀測 → 顯示白色
            "qpf_15d":     qpf15d, "daily_qpf": daily,
            "seg_etr_pct": [None]*8,
            "qpf_24h":     round(sum(qpf_48h[:4]),1),
            "qpf_48h":     round(sum(qpf_48h),1),
            "obs_6h":      [0.0]*8,
        })

    # 8. 輸出
    output = {
        "base_time":      base_time_str,
        "generated_at":   now_tpe.strftime("%Y-%m-%dT%H:%M:%S"),
        "source":         "CWA_OBS+QPF" if stations else "DEMO",
        "station_count":  len(stations),
        "township_count": len(out_towns),
        "townships":      out_towns,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, separators=(",",":"))

    sz = os.path.getsize(OUTPUT_FILE)
    print(f"\n完成：{OUTPUT_FILE}（{sz//1024}KB）")
    print(f"  觀測站：{len(stations)}")
    print(f"  有觀測的鄉鎮：{len(processed_keys)}")
    print(f"  總輸出鄉鎮：{len(out_towns)}")
    print(f"  資料來源：{'CWA即時' if stations else '模擬'}")

if __name__ == "__main__":
    main()
