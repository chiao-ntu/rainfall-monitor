"""
台灣降雨預測資料抓取腳本
用途：抓取 CWA QPF（前48h）+ GFS（第3-15天），產出 data.json 供地圖使用
執行方式：python fetch_rainfall.py
需要安裝：pip install requests cfgrib eccodes numpy scipy

作者：水保署降雨監測系統
"""

import requests, json, math, os, sys
from datetime import datetime, timedelta, timezone

# ═══════════════════════════════════════════════════════
#  設定區（請填入你的 API Key）
# ═══════════════════════════════════════════════════════
CWA_API_KEY = "YOUR_CWA_API_KEY_HERE"   # 填入你的氣象署 API Key
OUTPUT_FILE = "data.json"                 # 輸出到跟 HTML 同一資料夾

# ═══════════════════════════════════════════════════════
#  鄉鎮市區靜態資料（ETR2% 警戒值 + 座標）
#  實際部署時從 Excel/資料庫讀取，此處為示範
# ═══════════════════════════════════════════════════════
TOWNSHIPS = [
    {"county":"屏東縣","township":"來義鄉","lat":22.698,"lng":120.643,"alert_val":450},
    {"county":"屏東縣","township":"春日鄉","lat":22.607,"lng":120.638,"alert_val":420},
    {"county":"屏東縣","township":"泰武鄉","lat":22.659,"lng":120.666,"alert_val":430},
    {"county":"屏東縣","township":"獅子鄉","lat":22.525,"lng":120.680,"alert_val":480},
    {"county":"屏東縣","township":"三地門鄉","lat":22.727,"lng":120.636,"alert_val":700},
    {"county":"屏東縣","township":"霧臺鄉","lat":22.692,"lng":120.789,"alert_val":780},
    {"county":"高雄市","township":"桃源區","lat":23.162,"lng":120.812,"alert_val":720},
    {"county":"高雄市","township":"那瑪夏區","lat":23.268,"lng":120.697,"alert_val":700},
    {"county":"高雄市","township":"茂林區","lat":22.894,"lng":120.693,"alert_val":650},
    {"county":"南投縣","township":"仁愛鄉","lat":24.000,"lng":121.072,"alert_val":780},
    {"county":"南投縣","township":"信義鄉","lat":23.680,"lng":120.854,"alert_val":700},
    {"county":"南投縣","township":"竹山鎮","lat":23.748,"lng":120.669,"alert_val":480},
    {"county":"花蓮縣","township":"秀林鄉","lat":24.100,"lng":121.449,"alert_val":820},
    {"county":"花蓮縣","township":"卓溪鄉","lat":23.568,"lng":121.338,"alert_val":750},
    {"county":"宜蘭縣","township":"南澳鄉","lat":24.453,"lng":121.827,"alert_val":750},
    {"county":"宜蘭縣","township":"蘇澳鎮","lat":24.598,"lng":121.841,"alert_val":650},
    {"county":"台東縣","township":"延平鄉","lat":23.038,"lng":121.063,"alert_val":650},
    {"county":"台東縣","township":"海端鄉","lat":23.137,"lng":121.024,"alert_val":680},
    {"county":"嘉義縣","township":"阿里山鄉","lat":23.508,"lng":120.805,"alert_val":700},
    {"county":"新北市","township":"烏來區","lat":24.863,"lng":121.549,"alert_val":720},
    # ... 其餘鄉鎮請從 Excel 補充
]

# ═══════════════════════════════════════════════════════
#  Step 1：抓取 CWA QPF（前48h，每6小時）
# ═══════════════════════════════════════════════════════
def fetch_cwa_qpf():
    """
    抓取氣象署定量降水格點預報
    回傳：{base_time, grid_data}
    grid_data = list of 8 個 6h 時段的格點矩陣
    """
    print("📡 正在抓取 CWA QPF 資料...")

    # F-C0034-007：非颱風期間的格點定量降水預報
    url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0034-007"
    params = {
        "Authorization": CWA_API_KEY,
        "format": "JSON",
    }

    try:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  ⚠ CWA API 呼叫失敗：{e}")
        print("  → 使用模擬資料繼續...")
        return None

    # 解析回傳格式
    # CWA QPF 回傳結構：data['records']['locations']['location']
    # 每個 location 有 lat/lng 和各時段的 'Precipitation' 值
    try:
        records = data.get('records', {})
        locations = records.get('locations', [{}])[0].get('location', [])

        grid_points = []
        base_time = None

        for loc in locations:
            lat = float(loc.get('lat', 0))
            lng = float(loc.get('lon', 0))
            weather_elem = loc.get('weatherElement', [])

            precip_6h = []
            for elem in weather_elem:
                if elem.get('elementName') == 'Precipitation':
                    times = elem.get('time', [])
                    if not base_time and times:
                        base_time = times[0].get('startTime', '')
                    for t in times[:8]:  # 取前8個6h時段
                        val = t.get('elementValue', [{}])[0].get('value', '0')
                        try:
                            precip_6h.append(float(val))
                        except:
                            precip_6h.append(0.0)

            if precip_6h:
                grid_points.append({'lat': lat, 'lng': lng, 'qpf_6h': precip_6h})

        print(f"  ✓ 取得 {len(grid_points)} 個格點，基準時間：{base_time}")
        return {'base_time': base_time, 'grid_points': grid_points}

    except Exception as e:
        print(f"  ⚠ 資料解析失敗：{e}")
        return None


# ═══════════════════════════════════════════════════════
#  Step 2：抓取 GFS（第3-15天，逐日）
# ═══════════════════════════════════════════════════════
def fetch_gfs_forecast(base_dt):
    """
    從 NOAA NOMADS 下載 GFS 格點資料（第3-15天）
    GFS 發布時間：00z, 06z, 12z, 18z
    預報時效：到 384h（16天）
    """
    print("📡 正在抓取 GFS 資料（第3-15天）...")

    # 找最近的 GFS 發布時間（往前取整到 6h）
    now_utc = datetime.now(timezone.utc)
    run_hour = (now_utc.hour // 6) * 6
    run_dt = now_utc.replace(hour=run_hour, minute=0, second=0, microsecond=0)

    # NOAA NOMADS GFS 0.25度格點資料
    # 使用 OpenDAP/HTTP 下載特定變數
    date_str = run_dt.strftime('%Y%m%d')
    cycle = f"{run_hour:02d}"

    # APCP（累積降水量，mm）：從第72h到第360h（每24h一個時段）
    # 使用 NOAA Operational Model Archive (NOMADS)
    nomads_base = f"https://nomads.ncep.noaa.gov/cgi-bin/filter_gfs_0p25.pl"

    daily_grids = []  # 第3-15天，共13天的日雨量格點資料

    for day in range(3, 16):  # 第3天到第15天
        fhr_start = day * 24
        fhr_end   = fhr_start + 24

        # 下載 APCP（累積降水）：需要兩個時刻相減得到日雨量
        # 為了效率，這裡用 filter_gfs 只下載台灣範圍
        params = {
            'file': f'gfs.t{cycle}z.pgrb2.0p25.f{fhr_end:03d}',
            'var_APCP': 'on',
            'subregion': '',
            'leftlon': '118', 'rightlon': '123',
            'toplat': '27',   'bottomlat': '21',
            'dir': f'/gfs.{date_str}/{cycle}/atmos',
        }

        try:
            url = f"{nomads_base}?" + "&".join(f"{k}={v}" for k,v in params.items())
            resp = requests.get(url, timeout=60, stream=True)
            if resp.status_code == 200:
                # 儲存 GRIB2 暫存檔
                grib_path = f'/tmp/gfs_day{day}.grb2'
                with open(grib_path, 'wb') as f:
                    for chunk in resp.iter_content(8192):
                        f.write(chunk)
                daily_grids.append({'day': day, 'path': grib_path})
                print(f"  ✓ GFS 第{day}天下載完成")
            else:
                print(f"  ⚠ GFS 第{day}天下載失敗 (HTTP {resp.status_code})")
        except Exception as e:
            print(f"  ⚠ GFS 第{day}天：{e}")

    return daily_grids


def parse_gfs_grib(grib_paths):
    """解析 GRIB2 檔案，提取各格點的日雨量"""
    try:
        import cfgrib
        import numpy as np
    except ImportError:
        print("  ⚠ cfgrib 未安裝，跳過 GFS 解析")
        print("  → 請執行：pip install cfgrib eccodes")
        return {}

    result = {}  # {day: [(lat,lng,mm), ...]}
    for item in grib_paths:
        try:
            ds = cfgrib.open_dataset(item['path'],
                                     backend_kwargs={'indexing_time': 'valid_time'})
            apcp = ds['tp']  # 累積降水（mm）
            lats = ds.latitude.values
            lngs = ds.longitude.values
            vals = apcp.values

            pts = []
            for i, lat in enumerate(lats):
                for j, lng in enumerate(lngs):
                    lng_adj = lng if lng <= 180 else lng - 360
                    if 21 <= lat <= 27 and 118 <= lng_adj <= 123:
                        pts.append((float(lat), float(lng_adj), float(vals[i,j])))
            result[item['day']] = pts
        except Exception as e:
            print(f"  ⚠ GRIB 解析失敗 day{item['day']}: {e}")

    return result


# ═══════════════════════════════════════════════════════
#  Step 3：空間插值（格點 → 鄉鎮代表點）
# ═══════════════════════════════════════════════════════
def interpolate_to_township(lat, lng, grid_points, value_key='qpf_6h', seg_idx=None):
    """
    IDW（反距離加權）插值：找最近4個格點加權平均
    """
    if not grid_points:
        return 0.0

    # 計算所有格點距離
    dists = []
    for gp in grid_points:
        dlat = gp['lat'] - lat
        dlng = gp['lng'] - lng
        dist = math.sqrt(dlat**2 + dlng**2)
        dists.append((dist, gp))

    # 取最近4個格點
    dists.sort(key=lambda x: x[0])
    nearest = dists[:4]

    # IDW 加權
    total_w = 0.0
    total_v = 0.0
    for dist, gp in nearest:
        if dist < 1e-6:  # 剛好在格點上
            val = gp[value_key][seg_idx] if seg_idx is not None else sum(gp[value_key])
            return val
        w = 1.0 / (dist ** 2)
        v = gp[value_key][seg_idx] if seg_idx is not None else sum(gp[value_key])
        total_w += w
        total_v += w * v

    return total_v / total_w if total_w > 0 else 0.0


# ═══════════════════════════════════════════════════════
#  Step 4：模擬資料（CWA/GFS 抓取失敗時的備援）
# ═══════════════════════════════════════════════════════
def generate_demo_data(township, base_dt):
    """產生模擬資料（備援用）"""
    import random
    random.seed(hash(township['township']) % 10000)

    alert = township['alert_val']
    base = alert / 15 * random.uniform(0.5, 1.5)

    qpf_15d = []
    for i in range(60):
        decay = math.exp(-i * 0.03)
        v = max(0, base * decay * random.uniform(0.3, 2.0))
        qpf_15d.append(round(v, 1))

    daily = [round(sum(qpf_15d[i*4:(i+1)*4]), 1) for i in range(15)]
    etr2_6h = alert * 0.55
    seg_etr = [round(min(qpf_15d[i]/etr2_6h*100, 200), 1) for i in range(8)]

    return {
        'qpf_15d': qpf_15d,
        'daily_qpf': daily,
        'seg_etr_pct': seg_etr,
        'qpf_24h': sum(qpf_15d[:4]),
        'qpf_48h': sum(qpf_15d[:8]),
        'obs_6h': [round(v * random.uniform(0.5, 1.5), 1) for v in qpf_15d[:8]],
        'etr2': round(sum(qpf_15d[:4]) * 0.8, 1),
        'etr2_pct': round(sum(qpf_15d[:4]) * 0.8 / alert, 3),
    }


# ═══════════════════════════════════════════════════════
#  主程式
# ═══════════════════════════════════════════════════════
def main():
    print("=" * 50)
    print("台灣降雨預測資料抓取腳本")
    print(f"執行時間：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    now = datetime.now()
    base_time_str = now.strftime('%Y-%m-%dT%H:00:00')

    # ── 抓 CWA QPF（前48h）
    cwa_result = None
    if CWA_API_KEY != "YOUR_CWA_API_KEY_HERE":
        cwa_result = fetch_cwa_qpf()
        if cwa_result and cwa_result.get('base_time'):
            base_time_str = cwa_result['base_time']
    else:
        print("⚠  請先填入 CWA_API_KEY，目前使用模擬資料")

    # ── 抓 GFS（第3-15天）
    # 注意：GFS GRIB 下載較慢（每天約 1-3MB），正式使用再啟用
    gfs_data = {}
    # base_dt = datetime.strptime(base_time_str[:16], '%Y-%m-%dT%H:%M')
    # grib_paths = fetch_gfs_forecast(base_dt)
    # gfs_data = parse_gfs_grib(grib_paths)
    print("ℹ  GFS 下載暫時略過（需安裝 cfgrib 並確認網路），使用模擬資料補充第3-15天")

    # ── 組裝各鄉鎮資料
    print(f"\n🔄 組裝 {len(TOWNSHIPS)} 個鄉鎮資料...")
    output_townships = []

    for t in TOWNSHIPS:
        td = dict(t)

        if cwa_result and cwa_result.get('grid_points'):
            # 用 IDW 插值取各 6h 時段的 QPF
            gp = cwa_result['grid_points']
            qpf_6h_cwa = [
                round(interpolate_to_township(t['lat'], t['lng'], gp, 'qpf_6h', i), 1)
                for i in range(8)
            ]
            # 第3-15天用 GFS 或模擬
            demo = generate_demo_data(t, now)
            qpf_15d = qpf_6h_cwa + demo['qpf_15d'][8:]

            etr2_6h = t['alert_val'] * 0.55
            td.update({
                'qpf_15d'   : qpf_15d,
                'daily_qpf' : [round(sum(qpf_15d[i*4:(i+1)*4]),1) for i in range(15)],
                'seg_etr_pct': [round(min(qpf_15d[i]/etr2_6h*100,200),1) for i in range(8)],
                'qpf_24h'   : round(sum(qpf_6h_cwa[:4]),1),
                'qpf_48h'   : round(sum(qpf_6h_cwa),1),
                'obs_6h'    : [0]*8,   # 需接 CWA 觀測 API
                'etr2'      : round(sum(qpf_6h_cwa[:4])*0.8, 1),
                'etr2_pct'  : round(sum(qpf_6h_cwa[:4])*0.8/t['alert_val'], 3),
            })
        else:
            # 全部用模擬資料
            td.update(generate_demo_data(t, now))

        output_townships.append(td)
        print(f"  ✓ {t['county']} {t['township']}：24h={td.get('qpf_24h',0)}mm")

    # ── 輸出 data.json
    output = {
        'base_time'  : base_time_str,
        'generated_at': datetime.now().isoformat(),
        'source'     : 'CWA_QPF+GFS_DEMO' if not cwa_result else 'CWA_QPF+GFS',
        'townships'  : output_townships,
    }

    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完成！輸出至 {OUTPUT_FILE}（{os.path.getsize(OUTPUT_FILE)//1024} KB）")
    print(f"   基準時間：{base_time_str}")
    print(f"   鄉鎮數量：{len(output_townships)}")
    print("\n📋 下一步：")
    print("   1. 確認 taiwan_rainfall_v3.html 和 data.json 在同一資料夾")
    print("   2. 用瀏覽器開啟 taiwan_rainfall_v3.html")
    print("   3. 設定排程每 6 小時自動執行本腳本（見下方說明）")
    print("\n⏰ 設定自動排程（Windows）：")
    print("   工作排程器 → 新增工作 → 觸發程序：每6小時")
    print(f"   動作：python {os.path.abspath(OUTPUT_FILE.replace('.json','.py'))}")


if __name__ == '__main__':
    main()
