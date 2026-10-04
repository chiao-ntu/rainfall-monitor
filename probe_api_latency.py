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
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

TPE = timezone(timedelta(hours=8))
OUT = 'api_latency.json'
KEEP_DAYS = 14

try:
    import requests
except ImportError:
    requests = None

# ★ 缺 requests 時必須大聲失敗。
#   先前各 probe 寫 `if not requests: return []`，於是在沒有 requests 的環境
#   （GitHub Actions 的 setup-python 是乾淨的 Python，requests 不在標準庫）
#   會印出「累積 0 筆」而沒有任何錯誤行 —— 看起來像「API 都抓不到」，
#   實際上是一行相依套件沒裝。診斷工具靜默產不出資料，比直接壞掉更糟。
def _require_requests():
    if requests is None:
        print('!! 缺少 requests 套件，無法量測。請先執行：'
              'pip install requests', file=sys.stderr)
        sys.exit(2)


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


ISSUE_KEYS = ('issueTime', 'IssueTime', 'update', 'Update', 'updateTime',
              'sent', 'Sent', 'datasetTime', 'DataTime', 'ObsTime', 'obsTime')


def _walk_times(o, acc):
    """遞迴收集所有疑似時刻字串，並記下它的鍵名。"""
    if isinstance(o, dict):
        for k, v in o.items():
            if isinstance(v, str) and len(v) >= 16:
                acc.append((k, v))
            else:
                _walk_times(v, acc)
    elif isinstance(o, list):
        for v in o[:50]:          # 預報陣列很長，取前段足夠
            _walk_times(v, acc)


def _issue_time(j, now):
    """取「發布／更新時刻」。

    ★ 兩條規則，缺一不可：
      1. 優先用明確的發布欄位（issueTime/update/ObsTime…）。整包取最大時刻
         會抓到最後一個預報時段 —— 那是未來，不是發布時刻。
      2. 一律排除未來時刻。資料不可能比現在還新；出現未來時刻就代表
         抓錯欄位，寧可回 None 也不要產生負延遲的假數據。
    """
    acc = []
    _walk_times(j, acc)
    named = [v for k, v in acc if k in ISSUE_KEYS]
    cands = []
    for v in (named or [v for _, v in acc]):
        d = _parse(v)
        if d is None:
            continue
        if d.tzinfo is None:
            d = d.replace(tzinfo=TPE)
        if d <= now:              # 排除未來
            cands.append(d)
    return max(cands) if cands else None


def probe_cwa(key):
    """CWA 自動氣象站：回報最新一筆觀測的時刻。"""
    out = []
    if not key:
        print('   （略過 CWA：未設定 CWA_API_KEY）')
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
            # ★ 預報類資料集絕對不能取「最大時刻」：那是最後一個預報時段
            #   （可能是 8 天後），不是發布時刻。實測 F-D0047 因此算出
            #   延遲 −10803 分，負延遲在物理上不可能，等於這筆沒有意義。
            #   先找明確的發布／更新欄位，找不到才退回掃描，且一律排除未來時刻。
            dt = _issue_time(j, _now())
            out.append({'src': name, 'data_time': dt.isoformat() if dt else None,
                        'last_modified': r.headers.get('Last-Modified') or ''})
        except Exception as e:
            out.append({'src': name, 'err': str(e)[:80]})
    return out


def probe_openmeteo():
    """Open-Meteo：偵測「最新一報何時換新」。

    ★ 為什麼不是讀時刻：Open-Meteo 的 forecast 端點不提供模式的
      初始時刻（run time），先前程式寫死 data_time=None 再靠
      Last-Modified 當代理，但那個標頭常常沒有 —— 於是整欄空白，
      量了幾天等於沒量。
    ★ 改成指紋法：每輪取同一個點的降水序列算 SHA1。指紋一變就代表
      新的一報進來了，換新的時刻即可推出各模式的更新節奏 ——
      這正是「各模式推出更新的時間不同」要回答的問題。
    """
    out = []
    models = ['ecmwf_ifs025', 'gfs_seamless', 'jma_seamless',
              'ecmwf_aifs025_single', 'gfs_graphcast025', 'icon_seamless']
    for m in models:
        try:
            r = requests.get('https://api.open-meteo.com/v1/forecast',
                             params={'latitude': 23.7, 'longitude': 121.0,
                                     'hourly': 'precipitation', 'forecast_days': 2,
                                     'models': m, 'timezone': 'Asia/Taipei'},
                             timeout=60)
            if r.status_code != 200:
                out.append({'src': f'OpenMeteo_{m}', 'err': f'HTTP {r.status_code}'})
                continue
            j = r.json()
            ser = (j.get('hourly') or {}).get('precipitation') or []
            fp = hashlib.sha1(json.dumps(ser).encode()).hexdigest()[:12]
            out.append({'src': f'OpenMeteo_{m}', 'data_time': None, 'fp': fp,
                        'last_modified': r.headers.get('Last-Modified') or '',
                        'age': r.headers.get('Age') or ''})
        except Exception as e:
            out.append({'src': f'OpenMeteo_{m}', 'err': str(e)[:80]})
    return out


def probe_swcb():
    """水保署土石流警戒／ETR2：回報最新更新時刻。"""
    out = []
    try:
        r = requests.get('https://246.ardswc.gov.tw/Data/JSON/Alert.json', timeout=60)
        if r.status_code != 200:
            out.append({'src': '水保署_警戒', 'err': f'HTTP {r.status_code}'})
            return out
        # ★ 先前只掃前 4000 字且限定「YYYY-MM-DD HH:MM」一種格式，
        #   對不上就整欄空白。改為掃全文、接受斜線與 T 分隔，
        #   仍然抓不到才退回 HTTP 標頭（Last-Modified / Date）。
        txt = r.text
        st = re.findall(r'\d{4}[-/]\d{2}[-/]\d{2}[T ]\d{2}:\d{2}(?::\d{2})?', txt)
        now = _now()
        cands = []
        for x in st:
            d = _parse(x)
            if d is None:
                continue
            if d.tzinfo is None:
                d = d.replace(tzinfo=TPE)
            if d <= now:
                cands.append(d)
        dt = max(cands) if cands else None
        lm = r.headers.get('Last-Modified') or ''
        if dt is None and lm:
            try:
                from email.utils import parsedate_to_datetime
                dt = parsedate_to_datetime(lm).astimezone(TPE)
            except Exception:
                dt = None
        out.append({'src': '水保署_警戒',
                    'data_time': dt.isoformat() if dt else None,
                    'last_modified': lm})
    except Exception as e:
        out.append({'src': '水保署_警戒', 'err': str(e)[:80]})
    return out


def collect():
    _require_requests()
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
    print(f"本輪取得 {len(rows)} 筆，累積 {len(log['samples'])} 筆"
          f"（保留 {KEEP_DAYS} 天）")
    # ★ 一筆都沒產生＝量測沒有真的執行，必須讓 workflow 紅燈。
    #   先前回 0（成功），於是連續幾天「跑得很順但什麼都沒收集到」。
    if not rows:
        print('!! 本輪沒有產生任何樣本 —— 量測實際上沒有執行，'
              '請檢查相依套件與網路', file=sys.stderr)
        return 1
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
        p90 = lags[int(len(lags) * 0.9)] if len(lags) >= 10 else mx
        print(f"{src:30s} {len(lags):>5} {med:>8.0f}分 {mx:>6.0f}分"
              f"  資料時刻 +{p90 + 3:.0f} 分")

    # ── Open-Meteo：沒有發布時刻可讀，改看「序列指紋何時換新」──
    fps = {}
    for s0 in log.get('samples', []):
        if not s0.get('fp'):
            continue
        fps.setdefault(s0['src'], []).append((s0.get('ts', ''), s0['fp']))
    if fps:
        print(f"\n{'（指紋法）來源':30s} {'樣本':>5} {'換新次數':>9}  最近換新")
        for src, rows in sorted(fps.items()):
            rows.sort()
            ch = [rows[i][0] for i in range(1, len(rows))
                  if rows[i][1] != rows[i - 1][1]]
            print(f"{src:30s} {len(rows):>5} {len(ch):>9}  "
                  + (ch[-1][:16].replace('T', ' ') if ch else '尚未觀察到'))
        print('  ※ 這些來源不提供發布時刻，改以降水序列指紋變化判斷新報進來的時間。')
        print('  ※ 至少要累積到「換新次數 ≥ 3」才看得出節奏。')
    return 0


def self_test():
    """不連網的自我測試：守住會產生假數據的兩個坑。"""
    from datetime import datetime
    now = datetime(2026, 10, 4, 18, 0, tzinfo=TPE)
    fails = []

    def ok(c, m):
        print(('OK  ' if c else '!!  ') + m)
        if not c:
            fails.append(m)

    # ① 預報資料集：整包最大時刻是「最後一個預報時段」（未來），不可當發布時刻
    fc = {'cwaopendata': {'issueTime': '2026-10-04T17:30:00',
                          'dataset': {'time': [{'startTime': '2026-10-12T06:00:00'},
                                               {'startTime': '2026-10-11T06:00:00'}]}}}
    d = _issue_time(fc, now)
    ok(d is not None and d.strftime('%Y-%m-%dT%H:%M') == '2026-10-04T17:30',
       f'①取 issueTime 而非最後預報時段（實得 {d}）')
    ok(d is not None and _lag(d, now) >= 0,
       f'①延遲不得為負（實得 {_lag(d, now) if d else None} 分）')

    # ② 全部都是未來時刻 → 寧可回 None，也不要產生負延遲的假數據
    fut = {'dataset': {'time': [{'startTime': '2026-10-12T06:00:00'}]}}
    ok(_issue_time(fut, now) is None, '②只有未來時刻時回 None，不產生負延遲')

    # ③ 觀測類：沒有 issueTime，退回掃描但仍排除未來
    obs = {'records': {'Station': [{'ObsTime': {'DateTime': '2026-10-04T17:50:00'}},
                                   {'ObsTime': {'DateTime': '2026-10-04T17:00:00'}}]}}
    d3 = _issue_time(obs, now)
    ok(d3 is not None and d3.strftime('%H:%M') == '17:50',
       f'③觀測類取最新一筆 ObsTime（實得 {d3}）')

    print('\n' + ('全部通過' if not fails else f'失敗 {len(fails)} 項'))
    return 1 if fails else 0


if __name__ == '__main__':
    if '--self-test' in sys.argv:
        sys.exit(self_test())
    sys.exit(report() if '--report' in sys.argv else collect())
