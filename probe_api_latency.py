#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""量測各資料來源的「發布延遲」，用資料決定排程時間，而不是憑猜。

為什麼要量而不是查文件：
  各家文件寫的是「更新頻率」，不是「幾點幾分真的拿得到」。而我們真正需要
  知道的是：在某個時刻去抓，拿到的資料是幾點的？延遲多久？
  只要連續量幾天，就能看出每個來源的實際可用時刻，排程就能貼著它排。

用法（在能連到各 API 的環境，例如 GitHub Actions 裡）：
    CWA_API_KEY=xxx python3 probe_api_latency.py          # 量一次並附加到紀錄
    python3 probe_api_latency.py --report                 # 讀紀錄，輸出建議排程

產出 api_latency.json：
  {"samples":[{"ts":"抓取時刻","src":"來源","data_time":"資料時刻",
               "lag_min":延遲分鐘}]}
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

TPE = timezone(timedelta(hours=8))
OUT = 'api_latency.json'
KEEP_DAYS = 14

try:
    import requests
except ImportError:
    requests = None


def _now():
    return datetime.now(TPE)


def _lag(data_dt, fetched):
    """資料時刻 → 抓取時刻的分鐘數。"""
    if data_dt is None:
        return None
    if data_dt.tzinfo is None:
        data_dt = data_dt.replace(tzinfo=TPE)
    return round((fetched - data_dt).total_seconds() / 60, 1)


def _parse(s):
    if not s:
        return None
    s = str(s).strip().replace('/', '-').replace(' ', 'T')
    for fmt in ('%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S',
                '%Y-%m-%dT%H:%M', '%Y-%m-%dT%H'):
        try:
            return datetime.strptime(s[:len(datetime.now().strftime(fmt))], fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def probe_cwa(key):
    """CWA 自動氣象站：回報最新一筆觀測的時刻。"""
    out = []
    if not (requests and key):
        return out
    base = 'https://opendata.cwa.gov.tw/api/v1/rest/datastore'
    targets = [
        ('CWA_自動氣象站_O-A0001', f'{base}/O-A0001-001'),
        ('CWA_自動雨量站_O-A0002', f'{base}/O-A0002-001'),
        ('CWA_鄉鎮預報_F-D0047', f'{base}/F-D0047-091'),
    ]
    for name, url in targets:
        try:
            r = requests.get(url, params={'Authorization': key, 'format': 'JSON'},
                             timeout=60)
            if r.status_code != 200:
                out.append({'src': name, 'err': f'HTTP {r.status_code}'})
                continue
            j = r.json()
            # 觀測類：找最新的 ObsTime；預報類：找 issueTime / startTime
            txt = json.dumps(j, ensure_ascii=False)
            import re
            stamps = re.findall(r'\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}', txt)
            dt = max((_parse(x) for x in stamps if _parse(x)), default=None) \
                if stamps else None
            out.append({'src': name, 'data_time': dt.isoformat() if dt else None})
        except Exception as e:
            out.append({'src': name, 'err': str(e)[:80]})
    return out


def probe_openmeteo():
    """Open-Meteo：各模式最新一報的初始時刻（run time）。"""
    out = []
    if not requests:
        return out
    models = ['ecmwf_ifs025', 'gfs_seamless', 'jma_seamless',
              'ecmwf_aifs025_single', 'gfs_graphcast025', 'icon_seamless']
    for m in models:
        try:
            r = requests.get('https://api.open-meteo.com/v1/forecast',
                             params={'latitude': 23.7, 'longitude': 121.0,
                                     'hourly': 'precipitation', 'forecast_days': 1,
                                     'models': m, 'timezone': 'Asia/Taipei'},
                             timeout=60)
            if r.status_code != 200:
                out.append({'src': f'OpenMeteo_{m}', 'err': f'HTTP {r.status_code}'})
                continue
            j = r.json()
            # Open-Meteo 不直接給 run time，用回應標頭的 Last-Modified 當代理
            lm = r.headers.get('Last-Modified') or ''
            out.append({'src': f'OpenMeteo_{m}',
                        'data_time': None, 'last_modified': lm,
                        'gen_ms': j.get('generationtime_ms')})
        except Exception as e:
            out.append({'src': f'OpenMeteo_{m}', 'err': str(e)[:80]})
    return out


def probe_swcb():
    """水保署土石流警戒／ETR2：回報最新更新時刻。"""
    out = []
    if not requests:
        return out
    try:
        r = requests.get('https://246.ardswc.gov.tw/Data/JSON/Alert.json', timeout=60)
        if r.status_code == 200:
            txt = r.text[:4000]
            import re
            st = re.findall(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}', txt)
            dt = max((_parse(x) for x in st if _parse(x)), default=None) if st else None
            out.append({'src': '水保署_警戒', 'data_time': dt.isoformat() if dt else None})
        else:
            out.append({'src': '水保署_警戒', 'err': f'HTTP {r.status_code}'})
    except Exception as e:
        out.append({'src': '水保署_警戒', 'err': str(e)[:80]})
    return out


def collect():
    now = _now()
    rows = []
    rows += probe_cwa(os.environ.get('CWA_API_KEY', ''))
    rows += probe_openmeteo()
    rows += probe_swcb()
    for r in rows:
        r['ts'] = now.isoformat()
        dt = _parse(r.get('data_time'))
        r['lag_min'] = _lag(dt, now)

    log = {'samples': []}
    if os.path.exists(OUT):
        try:
            with open(OUT, encoding='utf-8') as f:
                log = json.load(f) or log
        except Exception:
            pass
    log.setdefault('samples', [])
    log['samples'] += rows
    cut = (now - timedelta(days=KEEP_DAYS)).isoformat()
    log['samples'] = [s for s in log['samples'] if s.get('ts', '') >= cut]
    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, separators=(',', ':'))

    print(f"量測時刻 {now.strftime('%Y-%m-%d %H:%M')} TST")
    for r in rows:
        if r.get('err'):
            print(f"  !! {r['src']:30s} {r['err']}")
        else:
            lag = r.get('lag_min')
            print(f"  {r['src']:30s} 資料時刻 {r.get('data_time') or '—'}"
                  + (f"　延遲 {lag:.0f} 分" if lag is not None else "")
                  + (f"　Last-Modified {r['last_modified']}"
                     if r.get('last_modified') else ""))
    print(f"累積 {len(log['samples'])} 筆（保留 {KEEP_DAYS} 天）")
    return 0


def report():
    if not os.path.exists(OUT):
        print(f"找不到 {OUT}，請先跑幾輪量測。")
        return 2
    with open(OUT, encoding='utf-8') as f:
        log = json.load(f)
    by = {}
    for s in log.get('samples', []):
        if s.get('lag_min') is None:
            continue
        by.setdefault(s['src'], []).append(s)
    if not by:
        print("尚無可用樣本（可能都抓不到資料時刻）。")
        return 2
    print(f"{'來源':30s} {'樣本':>5} {'延遲中位':>9} {'最大':>7}  建議抓取時刻")
    for src, rows in sorted(by.items()):
        lags = sorted(r['lag_min'] for r in rows)
        med = lags[len(lags) // 2]
        mx = lags[-1]
        # 建議：資料時刻 + 延遲上限（取 90 百分位）再加 3 分鐘緩衝
        p90 = lags[int(len(lags) * 0.9)] if len(lags) >= 10 else mx
        print(f"{src:30s} {len(lags):>5} {med:>8.0f}分 {mx:>6.0f}分"
              f"  資料時刻 +{p90 + 3:.0f} 分")
    print("\n★ 「建議抓取時刻」＝資料時刻 ＋ 90 百分位延遲 ＋ 3 分緩衝。")
    print("  排程排在這個點之後，才不會抓到上一輪的舊資料。")
    return 0


if __name__ == '__main__':
    sys.exit(report() if '--report' in sys.argv else collect())
