"""
台灣降雨預測資料抓取腳本 v2
- 前 48h：CWA QPF 格點預報（F-C0034-007）
- 第 3-15天：模擬資料（GFS 待接入）
- ETR2%：從 etr2_static.json 靜態表讀取
執行方式：python fetch_rainfall.py
環境變數：CWA_API_KEY（由 GitHub Actions Secrets 注入）
"""
import requests, json, math, os, sys
from datetime import datetime, timezone, timedelta

CWA_API_KEY = os.environ.get("CWA_API_KEY", "")
STATIC_FILE = "etr2_static.json"
OUTPUT_FILE = "data.json"
QPF_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0034-007"
OBS_URL = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0002-001"

def load_static():
    if not os.path.exists(STATIC_FILE):
        print(f"找不到 {STATIC_FILE}"); sys.exit(1)
    with open(STATIC_FILE, encoding="utf-8") as f:
        data = json.load(f)
    print(f"ETR2 靜態表：{len(data)} 個鄉鎮")
    return data

def fetch_cwa_qpf():
    if not CWA_API_KEY:
        print("未設定 CWA_API_KEY，QPF 使用模擬資料"); return None
    print("抓取 CWA QPF...")
    try:
        resp = requests.get(QPF_URL, params={"Authorization": CWA_API_KEY, "format": "JSON"}, timeout=60)
        resp.raise_for_status()
        raw = resp.json()
    except Exception as e:
        print(f"QPF 失敗：{e}"); return None
    try:
        locations = raw["records"]["locations"][0]["location"]
    except Exception as e:
        print(f"QPF 格式錯誤：{e}\n回傳：{str(raw)[:300]}"); return None
    grid, base_time = [], None
    for loc in locations:
        lat, lon = float(loc.get("lat",0)), float(loc.get("lon",0))
        if not (21.5<=lat<=26.5 and 119<=lon<=123): continue
        qpf_6h = []
        for elem in loc.get("weatherElement",[]):
            if elem.get("elementName") != "Precipitation": continue
            times = elem.get("time",[])
            if not base_time and times: base_time = times[0].get("startTime","")
            for t in times[:8]:
                try: qpf_6h.append(round(float(t.get("elementValue",[{}])[0].get("value","0")),1))
                except: qpf_6h.append(0.0)
        if qpf_6h: grid.append({"lat":lat,"lng":lon,"qpf_6h":qpf_6h})
    print(f"QPF：{len(grid)} 格點，基準：{base_time}")
    return {"base_time": base_time, "grid_points": grid}

def idw(lat, lng, grid, seg):
    if not grid: return 0.0
    dists = sorted([(math.sqrt((g["lat"]-lat)**2+(g["lng"]-lng)**2),g) for g in grid])[:4]
    tw, tv = 0.0, 0.0
    for d,g in dists:
        if d<1e-6: v=g["qpf_6h"]; return v[seg] if seg<len(v) else 0.0
        w=1/d**2; v=g["qpf_6h"][seg] if seg<len(g["qpf_6h"]) else 0.0
        tw+=w; tv+=w*v
    return round(tv/tw,1) if tw>0 else 0.0

def sim_day3_15(alert, avg6h):
    import random; random.seed(int(alert))
    res=[]
    for i in range(52):
        d=i//4+2; dec=math.exp(-d*0.06)
        res.append(round(max(0,avg6h*dec*random.uniform(0.4,1.8)),1))
    return res

def main():
    now_utc = datetime.now(timezone.utc)
    now_tpe = now_utc + timedelta(hours=8)
    print(f"執行時間：{now_tpe.strftime('%Y-%m-%d %H:%M')} TST")

    townships = load_static()
    qpf_res   = fetch_cwa_qpf()
    grid      = qpf_res["grid_points"] if qpf_res else []

    if qpf_res and qpf_res.get("base_time"):
        base_time_str = qpf_res["base_time"]
    else:
        h = (now_tpe.hour//6)*6
        base_time_str = now_tpe.replace(hour=h,minute=0,second=0,microsecond=0).strftime("%Y-%m-%dT%H:%M:%S")

    print(f"組裝 {len(townships)} 個鄉鎮...")
    out_towns = []
    for t in townships:
        lat, lng, alert = t["lat"], t["lng"], t["alert_val"]
        alert_6h = t.get("alert_6h", round(alert*0.55,0))

        if grid:
            qpf48 = [idw(lat,lng,grid,i) for i in range(8)]
        else:
            import random; random.seed(int(alert+lat*100))
            base=alert/20*random.uniform(0.3,1.2)
            qpf48=[round(max(0,base*(1+0.4*math.sin(i*1.1))*random.uniform(0.5,1.5)),1) for i in range(8)]

        avg6h   = sum(qpf48)/8 if qpf48 else 5.0
        qpf_sim = sim_day3_15(alert, avg6h)
        qpf15d  = qpf48 + qpf_sim
        daily   = [round(sum(qpf15d[i*4:(i+1)*4]),1) for i in range(15)]
        seg_etr = [round(min(qpf15d[i]/alert_6h*100,300),1) if alert_6h>0 else 0 for i in range(8)]
        q24     = round(sum(qpf48[:4]),1)
        q48     = round(sum(qpf48),1)
        etr2v   = round(q24*0.8,1)
        etr2p   = round(etr2v/alert,4) if alert>0 else 0.0

        out_towns.append({
            "county":t["county"],"township":t["township"],
            "lat":lat,"lng":lng,"alert_val":alert,"alert_6h":alert_6h,
            "qpf_15d":qpf15d,"daily_qpf":daily,"seg_etr_pct":seg_etr,
            "qpf_24h":q24,"qpf_48h":q48,
            "etr2":etr2v,"etr2_pct":etr2p,"obs_6h":[0.0]*8,
        })

    output = {
        "base_time":     base_time_str,
        "generated_at":  now_tpe.strftime("%Y-%m-%dT%H:%M:%S"),
        "source":        "CWA_QPF" if grid else "DEMO",
        "township_count":len(out_towns),
        "townships":     out_towns,
    }
    with open(OUTPUT_FILE,"w",encoding="utf-8") as f:
        json.dump(output,f,ensure_ascii=False,separators=(",",":"))
    print(f"完成：{OUTPUT_FILE}（{os.path.getsize(OUTPUT_FILE)//1024}KB）來源：{'QPF即時' if grid else '模擬'}")

if __name__=="__main__":
    main()
