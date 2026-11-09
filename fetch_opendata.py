#!/usr/bin/env python3
"""下載 FORMOSA 圖層所需的官方開放資料到 opendata/raw/（在 GitHub Actions 上執行）。

為什麼要在 Actions 上跑：開發環境連不到政府網站與 TDX，GitHub 的機器可以。
下載後的原始檔 commit 進 repo，再由 build_*.py 轉成前端圖層；原始檔留存可稽核。

來源
  1. 政府資料開放平臺（data.gov.tw）：以資料集編號查 API 取得官方下載網址，原樣存檔。
  2. TDX 運輸資料流通服務（交通部）：各捷運／輕軌、臺鐵、高鐵的車站、出入口、站內設施。
     有 TDX_CLIENT_ID / TDX_CLIENT_SECRET（GitHub Secrets）就用金鑰；沒有則以未註冊身分呼叫
     （每日次數有限，超過時停止並記錄，不重試轟炸）。

輸出
  opendata/raw/datagov/<編號>/<檔名>
  opendata/raw/tdx/<名稱>.json
  opendata/raw/manifest.json   每一項的來源網址、狀態、筆數、下載時間（失敗也記錄原因）
"""
import json, os, re, sys, time, hashlib, datetime
import urllib.request, urllib.parse, urllib.error

OUT = os.path.join('opendata', 'raw')
UA = 'Mozilla/5.0 (FORMOSA rainfall monitor; +https://github.com/chiao-ntu/rainfall-monitor)'

# ── data.gov.tw 資料集（編號：用途）──────────────────────────────
DATAGOV = {
    '5958':   '各縣市警察局、分局、分駐（派出）所地址與座標（警政署）',
    '24419':  '警政署所屬機關暨其所屬單位地址與座標（警政署）',
    '34102':  '全國海嘯警報臺地址座標（警政署）',
    '168341': '健保特約醫療院所名冊（健保署；取醫院，地址需定位）',
    '128437': '桃園捷運車站出入口基本資料（桃園捷運公司）',
    '128416': '臺北捷運車站無障礙設施資料（臺北捷運公司）',
}

# ── TDX 軌道（系統代碼見 TDX 文件：TRTC KRTC TYMC TMRT KLRT NTDLRT NTMC NTALRT TRTCMG）──
METRO = ['TRTC', 'KRTC', 'TYMC', 'TMRT', 'KLRT', 'NTDLRT', 'NTMC', 'NTALRT', 'TRTCMG']
TDX_BASE = 'https://tdx.transportdata.tw/api/basic'
TDX_JOBS = []
for s in METRO:
    TDX_JOBS += [(f'metro_station_{s}',  f'/v2/Rail/Metro/Station/{s}'),
                 (f'metro_exit_{s}',     f'/v2/Rail/Metro/StationExit/{s}')]
TDX_JOBS += [('tra_station', '/v3/Rail/TRA/Station'),
             ('tra_exit', '/v3/Rail/TRA/StationExit'),
             ('thsr_station', '/v2/Rail/THSR/Station'),
             ('thsr_exit', '/v2/Rail/THSR/StationExit')]
# 站內設施（廁所、電梯、哺乳室等）放在最後：額度不足時優先保住出入口
for s in METRO:
    TDX_JOBS.append((f'metro_facility_{s}', f'/v2/Rail/Metro/StationFacility/{s}'))
TDX_JOBS += [('tra_facility', '/v3/Rail/TRA/StationFacility'),
             ('thsr_facility', '/v2/Rail/THSR/StationFacility'),
             ('air_airport', '/v2/Air/Airport')]


def now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y-%m-%d %H:%M')


def http(url, data=None, headers=None, timeout=90):
    h = {'User-Agent': UA, 'Accept': '*/*'}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read(), dict(r.headers)


def safe_name(s):
    s = re.sub(r'[\\/:*?"<>|\s]+', '_', s).strip('_')
    return s[:80] or 'file'


def find_urls(obj):
    """data.gov.tw API 回傳裡所有下載網址（欄位名可能是 resourceDownloadUrl / downloadURL）。"""
    out = []
    if isinstance(obj, dict):
        url = None
        for k, v in obj.items():
            if isinstance(v, str) and re.search(r'download', k, re.I) and v.startswith('http'):
                url = v
        if url:
            fmt = obj.get('resourceFormat') or obj.get('format') or ''
            desc = obj.get('resourceDescription') or obj.get('title') or ''
            out.append((url, fmt, desc))
        for v in obj.values():
            out += find_urls(v)
    elif isinstance(obj, list):
        for v in obj:
            out += find_urls(v)
    seen, uniq = set(), []
    for u in out:
        if u[0] not in seen:
            seen.add(u[0]); uniq.append(u)
    return uniq


def fetch_datagov(man):
    for did, why in DATAGOV.items():
        rec = {'source': 'data.gov.tw', 'id': did, 'purpose': why, 'files': [], 'time': now()}
        api = f'https://data.gov.tw/api/v2/rest/dataset/{did}'
        try:
            st, body, _ = http(api)
            meta = json.loads(body.decode('utf-8'))
            os.makedirs(os.path.join(OUT, 'datagov', did), exist_ok=True)
            json.dump(meta, open(os.path.join(OUT, 'datagov', did, '_meta.json'), 'w', encoding='utf-8'),
                      ensure_ascii=False, indent=1)
            urls = find_urls(meta)
            if not urls:
                rec['status'] = 'API 回應中找不到下載網址'
            for url, fmt, desc in urls:
                ext = (fmt or '').lower().strip('.') or (os.path.splitext(urllib.parse.urlparse(url).path)[1].strip('.') or 'bin')
                fn = safe_name(desc or did) + '.' + ext
                try:
                    st2, data, hd = http(url, timeout=180)
                    open(os.path.join(OUT, 'datagov', did, fn), 'wb').write(data)
                    rec['files'].append({'url': url, 'file': fn, 'bytes': len(data),
                                         'sha1': hashlib.sha1(data).hexdigest()[:12]})
                except Exception as e:
                    rec['files'].append({'url': url, 'error': f'{type(e).__name__}: {e}'[:200]})
            rec.setdefault('status', 'ok' if any('file' in f for f in rec['files']) else '下載失敗')
        except Exception as e:
            rec['status'] = f'{type(e).__name__}: {e}'[:200]
        print(f"[data.gov.tw {did}] {rec['status']}  {len([f for f in rec['files'] if 'file' in f])} 檔")
        man['datagov'][did] = rec


def tdx_token():
    cid, sec = os.environ.get('TDX_CLIENT_ID', '').strip(), os.environ.get('TDX_CLIENT_SECRET', '').strip()
    if not (cid and sec):
        return None
    body = urllib.parse.urlencode({'grant_type': 'client_credentials',
                                   'client_id': cid, 'client_secret': sec}).encode()
    st, data, _ = http('https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token',
                       data=body, headers={'Content-Type': 'application/x-www-form-urlencoded'})
    return json.loads(data)['access_token']


def fetch_tdx(man):
    try:
        tok = tdx_token()
    except Exception as e:
        tok = None
        man['tdx_auth'] = f'金鑰換取 token 失敗：{type(e).__name__}'
    man['tdx_auth'] = man.get('tdx_auth') or ('API 金鑰' if tok else '未註冊（次數有限）')
    print('TDX 身分：', man['tdx_auth'])
    hdr = {'Accept': 'application/json'}
    if tok:
        hdr['Authorization'] = f'Bearer {tok}'
    os.makedirs(os.path.join(OUT, 'tdx'), exist_ok=True)
    stop = None
    for name, path in TDX_JOBS:
        rec = {'source': 'TDX', 'path': path, 'time': now()}
        if stop:
            rec['status'] = f'未執行（{stop}）'
            man['tdx'][name] = rec
            continue
        url = TDX_BASE + path + '?$format=JSON'
        try:
            st, data, _ = http(url, headers=hdr)
            j = json.loads(data.decode('utf-8'))
            n = len(j) if isinstance(j, list) else len(next((v for v in j.values() if isinstance(v, list)), [])) if isinstance(j, dict) else 0
            json.dump(j, open(os.path.join(OUT, 'tdx', name + '.json'), 'w', encoding='utf-8'),
                      ensure_ascii=False, separators=(',', ':'))
            rec.update(status='ok', n=n)
        except urllib.error.HTTPError as e:
            rec['status'] = f'HTTP {e.code}'
            if e.code in (401, 403, 429):
                stop = f'HTTP {e.code}：額度用完或需要金鑰，後續停止'
        except Exception as e:
            rec['status'] = f'{type(e).__name__}: {e}'[:200]
        print(f"[TDX {name}] {rec['status']} {rec.get('n', '')}")
        man['tdx'][name] = rec
        time.sleep(1.2)       # 未註冊身分的頻率限制


def main():
    os.makedirs(OUT, exist_ok=True)
    mp = os.path.join(OUT, 'manifest.json')
    man = json.load(open(mp, encoding='utf-8')) if os.path.exists(mp) else {}
    man.setdefault('datagov', {}); man.setdefault('tdx', {})
    only = set(sys.argv[1:])
    if not only or 'datagov' in only:
        fetch_datagov(man)
    if not only or 'tdx' in only:
        fetch_tdx(man)
    man['updated'] = now()
    json.dump(man, open(mp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main()
