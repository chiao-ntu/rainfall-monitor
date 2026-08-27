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
SLOPE_WARN_FILE = "slope_warning_stations.json"  # 官方坡地警戒區→代表站+警戒值（改法B對齊）
LS_WARN_FILE = "landslide_warning_stations.json"  # 官方大崩警戒區→代表站+警戒值（115年明細表）
# 水保署土石流參考雨量站 API：直接回官方 ETR2(STRT)，涵蓋氣象署抓不到的自建站
SWCB_RAIN_URL = "https://246.ardswc.gov.tw/webService/GetDebrisRainData.ashx"
# ── 官方警戒（雙軌架構的「現況」側，權威值）──────────────
#   現況紅/黃一律採官方發布值；系統只在「未來推估」側自行研判（明確標示推估）。
SWCB_ALERT_URL   = "https://ls.ardswc.gov.tw/api/LandslideAlertOpenData"          # D=土石流 L=大崩
SWCB_LSVAL_URL   = "https://246.ardswc.gov.tw/WebService/GetLSCountyTownAlertValueList.ashx"
SWCB_EOCINFO_URL = "https://246.ardswc.gov.tw/webService/GetIDisasterInfo.ashx"   # 應變小組開設
HEAVY_RAIN_COUNTY_TH = 150.0   # 「雨勢較大地區」縣市門檻：任一鄉鎮日累積達此值即納入
HOURLY_FILE = "rain_hourly.json"   # 由 fetch_qpesums_hourly.py 每小時寫入（本腳本只讀）
SWCB_STN_LOC = {}
# (縣市, 鄉鎮, 站名) → ETR2(mm)：同名站以地理位置區分
SWCB_BY_LOC = {}   # (縣市,鄉鎮) → {站名: ETR2}；由 fetch_swcb_etr2() 填充
# 代表站對站層級的可讀名稱（log 與前端共用語彙）
_LS_TIER_NAME = {'exact': '代表站精確', 'norm': '代表站正規化',
                 'near_town': '同鄉鎮相似站', 'near_county': '同縣市相似站',
                 'town': '退回鄉鎮值', 'none': '無值'}
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

def _stn_key(n):
    """站名正規化：去除尾端網站代碼字母（s水保/w水利/tp台北/sr/fr等）。"""
    import re as _re
    return _re.sub(r'[A-Za-z]+$', '', (n or '').strip()).strip()


def fetch_debris_alerts():
    """土石流警戒研判（依官方標準，以水保署 API 逐潛勢溪流計算）。
      紅色警戒：實際有效累積雨量(ETR2) ≥ 警戒值 → 強制/勸告撤離
      黃色警戒：預測雨量(ETR2＋未來QPF) ≥ 警戒值 → 疏散避難勸告
    本函式先算「紅色」與「達成率」；黃色需未來QPF，於主流程併入。
    回傳 {DebrisNO: {...}}；失敗回 {}。"""
    def num(v):
        try: return float(v)
        except: return None
    print("土石流警戒研判（水保署潛勢溪流）...")
    data = None
    for attempt in range(3):
        try:
            r = requests.get(SWCB_RAIN_URL, timeout=60)
            if r.status_code != 200:
                if attempt < 2: time.sleep(3); continue
                return {}
            data = json.loads(r.content.decode('utf-8', 'replace'))
            break
        except Exception as e:
            print(f"    失敗（{attempt+1}/3）：{e}")
            if attempt == 2: return {}
            time.sleep(3)
    if not data: return {}

    out = {}
    n_red = 0
    for row in data:
        no = row.get('DebrisNO')
        if not no: continue
        av = num(row.get('AlertValue'))
        if not av or av <= 0: continue
        # 兩支參考站取較高的 ETR2（保守，不漏報）
        cands = []
        for nk, vk in [('STName1','STRT1'), ('STName2','STRT2')]:
            v = num(row.get(vk))
            if v is not None: cands.append((v, (row.get(nk) or '').strip()))
        if not cands: continue
        etr2, stn = max(cands)
        pct = round(etr2/av, 4)
        red = etr2 >= av
        if red: n_red += 1
        out[no] = {
            'county': row.get('County',''), 'town': row.get('Town',''),
            'vill': row.get('Vill',''),
            'alert': av, 'etr2': round(etr2, 1), 'pct': pct,
            'station': stn, 'red': red,
        }
    print(f"    {len(out)} 條潛勢溪流｜達紅色警戒（ETR2≥警戒值）：{n_red} 條")
    return out


def fetch_swcb_etr2():
    """抓水保署土石流參考雨量站 API → 回傳「站名 → ETR2(mm)」對照表。

    ★★ STRT 語意：**直接就是有效累積雨量（毫米）**，非比值。
       實測佐證：曾誤將其乘上 AlertValue 換算，結果全臺 ETR2% 變成
       數千至上萬（宜蘭南澳 13163%），恰為放大約 350~450 倍（即警戒值倍數），
       證明原值本身即為毫米。**切勿再做任何比例換算。**
       （官方 API 文件某些範例的 STRT 呈現 0~1 小數，係該站當時雨量甚小，
         並非單位為比值——勿據此誤判。）
    副作用：同時填充模組級 SWCB_STN_LOC（(縣市,鄉鎮) → {站名: ETR2}），
      供大崩代表站找不到時「同鄉鎮／同縣市相似名」替代之用——這樣模糊比對
      有地理範圍約束，不會把「大武」誤配到隔縣的「大武山」。
      刻意用副作用而非改回傳值，以免動到 agg_obs 的呼叫介面。
    失敗回 {}（agg_obs 會退回以 CWA 觀測自算）。"""
    def num(v):
        try: return float(v)
        except: return None
    print("抓取水保署土石流參考雨量站 ETR2...")
    data = None
    for attempt in range(3):
        try:
            r = requests.get(SWCB_RAIN_URL, timeout=60)
            if r.status_code != 200:
                print(f"    HTTP {r.status_code}")
                if attempt < 2: time.sleep(3); continue
                return {}
            data = json.loads(r.content.decode('utf-8', 'replace'))
            break
        except Exception as e:
            print(f"    失敗（{attempt+1}/3）：{e}")
            if attempt == 2: return {}
            time.sleep(3)
    if not data:
        return {}
    st_val = {}
    SWCB_STN_LOC.clear()
    SWCB_BY_LOC.clear()
    _name_ids = {}
    for row in data:
        _cty = (row.get('County') or '').strip()
        _twn = (row.get('Town') or '').strip()
        for ik, nk, vk in [('STID1','STName1','STRT1'), ('STID2','STName2','STRT2')]:
            nm = (row.get(nk) or '').strip(); v = num(row.get(vk))
            sid = (row.get(ik) or '').strip()
            if v is None or (not nm and not sid): continue
            if sid: st_val[sid] = v             # ★ STID 為權威鍵（唯一識別）
            if nm:
                _name_ids.setdefault(nm, set()).add(sid or nm)
                if _cty and _twn:
                    SWCB_BY_LOC[(_cty, _twn, nm)] = v
                    SWCB_STN_LOC.setdefault((_cty, _twn), {})[nm] = v
    # ★ 站名鍵僅在該名稱全臺對應唯一 STID 時建立。
    #   實例：「武陵」有臺中和平 A0F010（33.7mm）與臺東延平 01S130（162mm）兩站，
    #   以站名為鍵會讓臺東的值覆蓋臺中，使和平區 ETR2% 由 10% 暴增至 46%。
    _n_amb = 0
    for nm, ids in _name_ids.items():
        if len(ids) > 1: _n_amb += 1
    for nm, ids in _name_ids.items():
        if len(ids) > 1: continue
        sid = next(iter(ids))
        if sid in st_val: st_val[nm] = st_val[sid]
    # ★ 正規化鍵僅在不撞名時建立（與 fetch_qpesums_hourly.py 同一規則）。
    #   全臺有 6 組站去尾字母後同名但屬不同機關、不同地點（武陵/武陵w、
    #   關山/關山w、南庄/南庄w、外大坪/外大坪w、寒溪/寒溪s、雙溪/雙溪tp）。
    #   舊版無條件 setdefault 會讓兩站塌成一鍵，對站時可能取到另一站的 ETR2。
    _owner = {}
    for nm in [k for k in _name_ids if len(_name_ids[k]) == 1 and k in st_val]:
        k = _stn_key(nm)
        if k == nm or not k: continue
        _owner.setdefault(k, set()).add(nm)
    _n_norm = 0
    for k, owners in _owner.items():
        if k in st_val: continue
        if len(owners) > 1: continue            # 撞名 → 不建立，寧可對不到也不對錯
        st_val[k] = st_val[next(iter(owners))]; _n_norm += 1
    print(f"    水保署ETR2：{len(data)} 條潛勢溪流、{len(st_val)} 個鍵"
          f"（STID＋唯一站名；正規化鍵 {_n_norm} 個，"
          f"同名多站 {_n_amb} 個改以 STID/地理區分）、"
          f"{len(SWCB_STN_LOC)} 個鄉鎮位置索引")
    return st_val


# ══════════════════════════════════════════════════════════
#  官方警戒（雙軌架構「現況」側）
#    現況紅/黃 = 水保署官方發布值（權威，含報別）
#    未來推估 = 系統自算（ETR2＋QPF，依技術指引門檻），一律標示為推估
# ══════════════════════════════════════════════════════════
def _swcb_json(url, label, tries=3, timeout=60):
    """水保署系列 API 通用取用（回 list/dict；失敗回 None）。"""
    for attempt in range(tries):
        try:
            r = requests.get(url, timeout=timeout)
            if r.status_code != 200:
                print(f"    {label} HTTP {r.status_code}")
                if attempt < tries - 1: time.sleep(3); continue
                return None
            return json.loads(r.content.decode('utf-8', 'replace'))
        except Exception as e:
            print(f"    {label} 失敗（{attempt+1}/{tries}）：{e}")
            if attempt == tries - 1: return None
            time.sleep(3)
    return None


def fetch_official_alerts():
    """水保署官方已發布之土石流／大規模崩塌紅黃警戒。
    來源 ls.ardswc.gov.tw/api/LandslideAlertOpenData
      AlertType D=土石流（DebrisNo）、L=大規模崩塌（LandslideID）
      AlertLevel y=黃色警戒、r=紅色警戒
    回傳 {'debris':{編號:{...}}, 'landslide':{編號:{...}}, 'report_id':..., 'updated':...}
    無警戒時 API 可能回空陣列或錯誤物件 → 回空結構（非失敗）。"""
    print("抓取官方土石流／大崩警戒（水保署 LandslideAlertOpenData）...")
    data = _swcb_json(SWCB_ALERT_URL, "官方警戒")
    out = {'debris': {}, 'landslide': {}, 'report_id': '', 'updated': '', 'ok': data is not None}
    if data is None:
        print("    取用失敗 → 現況警戒改以系統推估值代用（前端會標示為推估）")
        return out
    if isinstance(data, dict):
        # 無警戒時可能回 {"errorMessage": "目前無警戒"}
        if data.get('errorMessage'):
            print(f"    {data.get('errorMessage')}")
            return out
        data = [data]
    if not isinstance(data, list):
        print(f"    非預期結構（{type(data).__name__}）→ 視為無警戒")
        return out
    for row in data:
        if not isinstance(row, dict): continue
        lvl = (row.get('AlertLevel') or '').strip().lower()
        if lvl not in ('y', 'r'): continue
        rec = {
            'level': lvl,
            'county': (row.get('County') or '').strip(),
            'town':   (row.get('Town') or '').strip(),
            'vill':   (row.get('Vill') or '').strip() or None,
            'name':   (row.get('LandslideName') or '').strip() or None,
            'updated': (row.get('LastUpdateDate') or '').strip(),
            'report':  (row.get('ReportID') or '').strip(),
        }
        typ = (row.get('AlertType') or '').strip().upper()
        no_d = (row.get('DebrisNo') or '').strip()
        no_l = (row.get('LandslideID') or '').strip()
        if typ == 'D' and no_d and no_d != '-':
            out['debris'][no_d] = rec
        elif typ == 'L' and no_l and no_l != '-':
            out['landslide'][no_l] = rec
        if rec['report'] and not out['report_id']: out['report_id'] = rec['report']
        if rec['updated'] > out['updated']:        out['updated'] = rec['updated']
    dr = sum(1 for v in out['debris'].values()    if v['level'] == 'r')
    dy = sum(1 for v in out['debris'].values()    if v['level'] == 'y')
    lr = sum(1 for v in out['landslide'].values() if v['level'] == 'r')
    ly = sum(1 for v in out['landslide'].values() if v['level'] == 'y')
    print(f"    官方警戒｜土石流 紅{dr}/黃{dy} 條、大崩 紅{lr}/黃{ly} 處"
          f"（報別 {out['report_id'] or '—'}，更新 {out['updated'] or '—'}）")
    return out


def fetch_ls_alert_values():
    """大規模崩塌潛勢區官方警戒值（GetLSCountyTownAlertValueList.ashx）。
    逐潛勢區：LSNo / Name / AlertValue / Lng / Lat。新增潛勢區會自動出現，
    故本表優先於 landslide_warning_stations.json 的靜態 alert 值。
    回傳 {LSNo: {...}}；失敗回 {}。"""
    print("抓取大崩潛勢區官方警戒值...")
    data = _swcb_json(SWCB_LSVAL_URL, "大崩警戒值")
    if not isinstance(data, list):
        print("    取用失敗 → 改用 landslide_warning_stations.json 靜態警戒值")
        return {}
    out = {}
    for row in data:
        if not isinstance(row, dict): continue
        no = (row.get('LSNo') or '').strip()
        if not no: continue
        try: av = float(row.get('AlertValue'))
        except (TypeError, ValueError): continue
        if av <= 0: continue
        def _f(v):
            try: return float(v)
            except (TypeError, ValueError): return None
        out[no] = {
            'alert': av,
            'name':  (row.get('Name') or '').strip(),
            'county': (row.get('County') or '').strip(),
            'town':   (row.get('Town') or '').strip(),
            'lat': _f(row.get('Lat')), 'lng': _f(row.get('Lng')),
        }
    print(f"    大崩官方警戒值：{len(out)} 處潛勢區")
    return out


def _stn_key2(n):
    """強化版站名正規化：去尾端機關代碼字母、再去尾端 (數字) 或 空白數字，反覆到收斂。
    與 landslide_warning_stations.json 的 station_norm、fetch_qpesums_hourly.py 的
    _stn_key 規則一致（例：太平山(1)w→太平山、知本(5)→知本、太麻里 2→太麻里）。

    ★ 刻意與上面的 _stn_key() 分開：_stn_key() 是既有 ETR2 官方對齊在用的
      （v8.0 修正的 21 個 sr/tp 站），它只去尾端字母。若把 (數字) 也一併去掉，
      「集集(2)」「知本(5)」這類同名不同機關的獨立測站會塌成同一個鍵，
      可能改變已驗證的 ETR2 對齊結果。故新邏輯另立函式，不動舊路徑。
    """
    import re as _re, unicodedata as _ud
    s = _ud.normalize('NFKC', (n or '')).strip()
    prev = None
    while prev != s:
        prev = s
        s = _re.sub(r'[A-Za-z]+$', '', s).strip()
        s = _re.sub(r'\(\s*\d+\s*\)$', '', s).strip()
        s = _re.sub(r'\s+\d+$', '', s).strip()
    return s


def resolve_station_etr2(names, swcb_etr2, county='', town=''):
    """依序嘗試把「官方代表站名」對到即時 ETR2 值。

    決策目的是「給官方未來發布紅黃的建議」，所以寧可用同鄉鎮的鄰近站
    也不要退回鄉鎮聚合值——但模糊比對必須有地理約束，否則會跨區誤配
    （例：「大武」→隔縣「大武山」、「太平」→「太平山」）。

    層級（先到先用，回傳第一個命中）：
      1 exact   代表站原名精確命中
      2 norm    去機關代碼/序號後命中（太平山(1)w → 太平山）
      3 near_t  同鄉鎮內名稱相似（子字串或 difflib≥0.72）
      4 near_c  同縣市內名稱相似（同上，較寬鬆的地理範圍）
    回傳 (etr2, tier, matched_name)；全不中回 (None, '', '')。
    """
    import difflib
    cands = [n for n in names if n]
    # 1 精確
    for n in cands:
        if n in swcb_etr2: return swcb_etr2[n], 'exact', n
    # 2 正規化
    for n in cands:
        k = _stn_key2(n)
        if k and k in swcb_etr2: return swcb_etr2[k], 'norm', k

    def _best(pool):
        """pool = {站名: ETR2}；回傳最相似者（需通過門檻）。"""
        best, best_score, best_nm = None, 0.0, ''
        for n in cands:
            kn = _stn_key2(n)
            if not kn: continue
            for pn, pv in pool.items():
                kp = _stn_key2(pn)
                if not kp: continue
                if kn == kp:
                    return pv, 1.0, pn
                # 子字串：短名須≥2字，避免單字誤配
                if len(kn) >= 2 and len(kp) >= 2 and (kn in kp or kp in kn):
                    sc = 0.9
                else:
                    sc = difflib.SequenceMatcher(None, kn, kp).ratio()
                if sc > best_score:
                    best, best_score, best_nm = pv, sc, pn
        return (best, best_score, best_nm) if best_score >= 0.72 else (None, 0.0, '')

    # 3 同鄉鎮
    if county and town:
        v, sc, nm = _best(SWCB_STN_LOC.get((county, town), {}))
        if v is not None: return v, 'near_town', nm
    # 4 同縣市
    if county:
        pool = {}
        for (c, t), d in SWCB_STN_LOC.items():
            if c == county: pool.update(d)
        v, sc, nm = _best(pool)
        if v is not None: return v, 'near_county', nm
    return None, '', ''


def load_hourly_series():
    """讀 fetch_qpesums_hourly.py 寫的 rain_hourly.json（本腳本只讀不寫）。
    回傳 (series dict, meta dict)；缺檔／壞檔回 (None, meta)。"""
    meta = {'available': False, 'hours': 0, 'gap_count': None, 'updated': '',
            'warm': {}, 'reason': ''}
    if not os.path.exists(HOURLY_FILE):
        meta['reason'] = f'尚無 {HOURLY_FILE}（每小時腳本還沒跑過或未部署）'
        print(f"逐時序列：{meta['reason']}")
        return None, meta
    try:
        with open(HOURLY_FILE, encoding='utf-8') as f:
            ser = json.load(f)
        assert isinstance(ser, dict) and 'hours' in ser
    except Exception as e:
        meta['reason'] = f'讀取失敗：{e}'
        print(f"逐時序列：{meta['reason']}")
        return None, meta
    hrs = ser.get('hours') or []
    meta.update({'available': bool(hrs), 'hours': len(hrs),
                 'gap_count': ser.get('gap_count'), 'updated': ser.get('updated', '')})
    # 各項判定所需的最短序列長度——未達者一律回「資料不足」，不回「未達標」
    meta['warm'] = {'r2h': len(hrs) >= 2, 'r3h': len(hrs) >= 3,
                    'no_abate': len(hrs) >= 2,
                    'release_2stage': len(hrs) >= 6, 'release_1stage': len(hrs) >= 12,
                    'self_etr2': len(hrs) >= 168}
    print(f"逐時序列：{len(hrs)} 小時（缺格 {ser.get('gap_count')}，更新 {ser.get('updated','—')}）"
          f"｜可用判定：" + "、".join(k for k, v in meta['warm'].items() if v) or "（暖機中）")
    if len(hrs) < 12:
        print(f"  ⚠ 暖機中：解除判定需12h、自算ETR2需168h；未達前相關欄位回 None＋reason")
    return ser, meta


def hourly_metrics(ser, meta, station_names):
    """由逐時序列算出技術指引判定所需的量。station_names 為候選代表站名（依序試）。

    回傳 dict，欄位語意：
      r1h/r2h/r3h      近1/2/3小時累積雨量（mm）；None＝序列不足或對不到站
      no_abate         降雨無減緩趨勢（★代理判斷，見下）
      adj_level/adj_mm 警戒基準值動態調降級別與調降量（依原警戒值決定，見 apply_dynamic_adj）
      rel_2stage       符合二階段調降標準：連續6h平均<4mm 且最大時雨≤10mm
      rel_1stage       符合一階段解除標準：連續12h平均<10mm
      reissue_th1      再發布門檻1：時雨量≥40mm 或 連續2h每小時>20mm
      reissue_th2      再發布門檻2：24h累積≥200mm（豪雨標準）
      reason           不可判定時的原因字串（前端顯示「資料不足」而非「未達標」）

    ★「降雨無減緩趨勢」官方未給量化定義，此處以代理判斷：
        近1h≥4mm（＝官方雨場持續門檻）且 近1h ≥ 前1h×0.6
      前端必須標明為代理判斷，不可顯示成官方判定。
    """
    out = {k: None for k in ('r1h', 'r2h', 'r3h', 'no_abate', 'rel_2stage', 'rel_1stage',
                             'reissue_th1', 'reissue_th2')}
    out['reason'] = ''
    out['station'] = ''
    if not ser or not meta.get('available'):
        out['reason'] = meta.get('reason') or '逐時序列尚未建立'
        return out
    hrs = ser.get('hours') or []
    cwa = ser.get('cwa') or {}
    # 對站：精確 → 強化正規化
    keys = [n for n in station_names if n]
    latest = cwa.get(hrs[-1], {}) if hrs else {}
    stn = next((n for n in keys if n in latest), None)
    if stn is None:
        norm = {_stn_key2(k): k for k in latest}
        stn = next((norm[_stn_key2(n)] for n in keys if _stn_key2(n) in norm), None)
    if stn is None:
        out['reason'] = '逐時序列中對不到代表站'
        return out
    out['station'] = stn

    def val(h):
        v = (cwa.get(h) or {}).get(stn)
        return None if v is None else float(v)

    def window(n):
        """最近 n 小時的時雨量序列（時間升冪）；有任一小時缺格→回 None。"""
        need = hrs[-n:] if len(hrs) >= n else None
        if not need or len(need) < n: return None
        vs = [val(h) for h in need]
        return None if any(v is None for v in vs) else vs

    w1, w2, w3 = window(1), window(2), window(3)
    if w1: out['r1h'] = round(w1[-1], 1)
    if w2: out['r2h'] = round(sum(w2), 1)
    if w3: out['r3h'] = round(sum(w3), 1)

    # 降雨無減緩（代理判斷）
    if w2:
        cur, prev = w2[-1], w2[-2]
        out['no_abate'] = bool(cur >= 4.0 and cur >= prev * 0.6)
    # 解除標準
    w6, w12 = window(6), window(12)
    if w6:
        out['rel_2stage'] = bool(sum(w6) / 6.0 < 4.0 and max(w6) <= 10.0)
    if w12:
        out['rel_1stage'] = bool(sum(w12) / 12.0 < 10.0)
    # 再發布門檻
    if w1: out['reissue_th1'] = bool(w1[-1] >= 40.0)
    if w2 and not out['reissue_th1']:
        out['reissue_th1'] = bool(all(v > 20.0 for v in w2))
    w24 = window(24)
    if w24: out['reissue_th2'] = bool(sum(w24) >= 200.0)

    miss = [k for k in ('rel_2stage', 'rel_1stage') if out[k] is None]
    if miss: out['reason'] = f"序列不足或有缺格（{len(hrs)}h）：{'、'.join(miss)} 無法判定"
    return out


def apply_dynamic_adj(alert, r3h, r2h):
    """警戒基準值動態調整機制（技術指引三-(三)-3）。

      一級：近3h ≥200mm → 原值≤400 調降100；原值≥450 調降150
      二級：近3h ≥150mm → 原值≤400 調降 50；原值≥450 調降100
      三級：近2h ≥100mm → 原值≤400 調降 50；原值≥450 維持不變
    取最先成立者（一級優先）。回傳 (調整後警戒值, 級別, 調降量)。
    r3h/r2h 為 None（序列不足）→ 不調整並回級別 None，前端顯示「資料不足」。

    ★ 門檻為「大於等於」：依水保署系統註3之原文（3hr累積雨量>=200mm）。
      本函式原以嚴格大於實作，恰為 200.0mm 時官方會調降而系統不會；
      防災判定於邊界值應從寬，故對齊官方寫法。
    """
    if not alert or alert <= 0: return alert, None, 0
    if r3h is None and r2h is None: return alert, None, 0
    lo = alert <= 400
    if r3h is not None and r3h >= 200:
        d = 100 if lo else 150
        return max(0, alert - d), 1, d
    if r3h is not None and r3h >= 150:
        d = 50 if lo else 100
        return max(0, alert - d), 2, d
    if r2h is not None and r2h >= 100:
        d = 50 if lo else 0
        return max(0, alert - d), 3, d
    return alert, 0, 0      # 0 = 已判定且無需調整（區別於 None = 無法判定）


def slope_est(etr2, alert, qpf24, qpf_night=None):
    """依技術指引推估紅／黃（**推估側專用**；現況紅黃一律以官方發布為準）。

    官方黃色警戒標準（技術指引三-(二)）：
      警戒值≦350mm → 實際降雨量已達警戒值 30%，且該值加預測雨量 > 警戒值
      警戒值≧400mm → 實際降雨量已達警戒值 40%，且該值加預測雨量 > 警戒值
    官方紅色警戒標準：實際降雨量已達警戒基準值（另需近3h>30mm 且降雨無減緩
      ——此兩項屬「現況」判定，推估側無從得知未來的實測時雨量，故不套用）。
    入夜前示警（技術指引三-(四)）：已發布黃警地區，實際＋夜間預測雨量可能達警戒值。

    回傳 dict；alert 無效時所有判定回 None（不猜，不以 False 冒充「未達標」）。"""
    if not alert or alert <= 0 or etr2 is None:
        return {'pct': None, 'fc_etr2': None, 'fc_pct': None,
                'est_yellow_now': None, 'est_red_fc': None,
                'yellow_th': None, 'reached_th': None, 'night_warn': None}
    q24 = qpf24 or 0.0
    fc  = etr2 + q24
    th_ratio = 0.30 if alert <= 350 else 0.40      # 350<x<400 不存在（級距為50mm）
    reached_th = etr2 >= alert * th_ratio

    # 兩個語意不同、可同時成立的推估旗標——刻意不合併成單一「推估等級」：
    #   est_yellow_now：此刻已符合官方「黃色警戒發布標準」
    #                   （實際達警戒值 th_ratio，且實際＋預測 > 警戒值）
    #   est_red_fc    ：未來24h預測有效累積雨量達警戒值 → 可能達紅色警戒
    est_yellow_now = reached_th and fc > alert
    est_red_fc     = fc >= alert
    return {
        'pct':     round(etr2 / alert, 4),
        'fc_etr2': round(fc, 1),
        'fc_pct':  round(fc / alert, 4),
        'est_yellow_now': est_yellow_now,
        'est_red_fc':     est_red_fc,
        'yellow_th':  round(th_ratio, 2),
        'reached_th': reached_th,
        # 入夜前示警：實際＋夜間(19–06)預測雨量可能達警戒值（None＝無夜間QPF可用）
        'night_warn': None if qpf_night is None else (etr2 + qpf_night) >= alert,
    }


def load_ls_warn():
    """載入大崩警戒區明細（代表站＋靜態警戒值；未來推估側需要代表站）。
    回傳 zones list；缺檔回 []。"""
    if not os.path.exists(LS_WARN_FILE):
        print(f"警告：找不到 {LS_WARN_FILE}，大崩未來推估將停用（現況仍走官方警戒）")
        return []
    with open(LS_WARN_FILE, encoding='utf-8') as f:
        d = json.load(f)
    zones = d.get('zones', [])
    n = sum(z.get('n_zones', 1) for z in zones)
    print(f"大崩警戒區明細：{len(zones)} 列／{n} 處潛勢區（{d.get('source','')[:24]}…）")
    return zones


def load_slope_warn():
    """載入官方坡地警戒區明細（改法B：逐警戒區代表站+警戒值）。"""
    if not os.path.exists(SLOPE_WARN_FILE):
        print(f"警告：找不到 {SLOPE_WARN_FILE}，ETR2 聚合退回舊法（鎮內取最大）")
        return None
    with open(SLOPE_WARN_FILE, encoding='utf-8') as f:
        d = json.load(f)
    tw = d.get('townships', {})
    n_reg = sum(len(v) for v in tw.values())
    print(f"坡地警戒區明細：{len(tw)} 鄉鎮、{n_reg} 警戒區（官方代表站對齊）")
    return tw

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
            'rain_3h':   gp(re,'Past3hr'),   # 紅警「近3h>30mm」與動態調降門檻需要
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

def agg_obs(stations, alert_table, history, now_tpe, slope_warn=None, swcb_etr2=None):
    # 建立站名→sid 索引（供改法B用官方代表站名查即時ETR2）
    name2sid = {}
    for sid, st in stations.items():
        nm = st.get('name','').strip()
        if nm:
            name2sid.setdefault(nm, sid)
            name2sid.setdefault(nm.rstrip('sSWw').strip(), sid)  # 去後綴也建索引

    town={}
    for sid,st in stations.items():
        key=st['county']+st['township']
        if key not in town:
            town[key]={'county':st['county'],'township':st['township'],
                       'stations':[],'rain_24h':0.0,'rain_6h':0.0,
                       'rain_2d':0.0,'rain_3d':0.0,'etr2':None,
                       'daily_rain':[0.0]*15, 'station_etr2':{}}
        td=town[key]; td['stations'].append(sid)
        # 雨量觀測：所有站都可以貢獻（用於顯示觀測雨量）
        td['rain_24h']=max(td['rain_24h'],st['rain_24h'])
        td['rain_6h'] =max(td['rain_6h'], st['rain_6h'])
        td['rain_2d'] =max(td['rain_2d'], st['rain_2d'])
        td['rain_3d'] =max(td['rain_3d'], st['rain_3d'])
        # 逐日雨量：所有站都可以貢獻（供前端顯示）
        st_daily = get_daily_rain_array(sid, history, now_tpe, days=15)
        td['daily_rain'] = [max(a,b) for a,b in zip(td['daily_rain'], st_daily)]
        if 'station_daily' not in td: td['station_daily'] = {}
        td['station_daily'][sid] = st_daily

    # ── ETR2 計算（改法B：逐官方警戒區用指定代表站，鎮內取最高 ETR2%）──
    if slope_warn:
        n_aligned = 0; n_swcb = 0; n_cwa = 0
        for key, td in town.items():
            regions = slope_warn.get(key)
            if not regions:
                td['etr2'] = None; td['etr2_pct'] = None
                continue
            best_pct = None; best_etr2 = None; best_av = None
            detail = []; seen = set(); used_swcb = False; used_cwa = False
            for reg in regions:
                stn = (reg.get('station') or '').strip()
                if not stn: continue
                av = reg.get('alert', 0) or 0
                if isinstance(av, str):
                    import re as _re
                    _m = _re.search(r'\d+', av); av = int(_m.group()) if _m else 0
                # ① 官方值優先（水保署 API）
                ev = None; src = None
                if swcb_etr2:
                    # ★ 先以 (縣市, 鄉鎮, 站名) 精準對站：同名站由地理位置區分。
                    #   「武陵」在臺中和平(33.7mm)與臺東延平(162mm)各有一站，
                    #   僅用站名會取到錯的那個，使和平區 ETR2% 由 10% 暴增至 46%。
                    _c, _t = td['county'], td['township']
                    for _nm in (stn, reg.get('station_norm')):
                        if _nm and (_c, _t, _nm) in SWCB_BY_LOC:
                            ev = SWCB_BY_LOC[(_c, _t, _nm)]; break
                    if ev is None:
                        ev = swcb_etr2.get(stn)
                    if ev is None:
                        ev = swcb_etr2.get(reg.get('station_norm') or _stn_key(stn))
                    if ev is None:
                        ev = swcb_etr2.get(_stn_key(stn))
                    if ev is not None: src = 'swcb'; used_swcb = True
                # ② 備援：以 CWA 觀測自算（該官方指定站）
                if ev is None:
                    sid = name2sid.get(stn) or name2sid.get(_stn_key(stn))
                    if sid:
                        ev = calc_etr2(sid, history, now_tpe)
                        if ev is not None: src = 'cwa'; used_cwa = True
                if ev is None: continue
                # ★分母用「該單元的官方警戒值」（與官方警戒分析總表一致）
                pct = round(ev/av, 4) if av > 0 else None
                sig = (reg.get('village',''), stn)
                if sig not in seen:
                    seen.add(sig)
                    detail.append({'village': reg.get('village',''), 'station': stn,
                                   'alert': av, 'etr2': ev, 'etr2_pct': pct, 'src': src})
                if pct is not None and (best_pct is None or pct > best_pct):
                    best_pct = pct; best_etr2 = ev; best_av = av
            td['etr2'] = best_etr2
            td['etr2_pct'] = best_pct
            td['etr2_alert'] = best_av          # 該最高單元的官方警戒值（前端算%用）
            td['etr2_src'] = ('swcb' if (used_swcb and not used_cwa)
                              else 'mixed' if (used_swcb and used_cwa)
                              else 'cwa' if used_cwa else None)
            td['slope_regions'] = detail
            if best_pct is not None:
                n_aligned += 1
                if used_swcb: n_swcb += 1
                else: n_cwa += 1
        print(f"  鄉鎮聚合（逐官方警戒單元）：{n_aligned} 個鄉鎮有 ETR2%"
              f"（含水保署官方值 {n_swcb}、純CWA自算 {n_cwa}）")
    else:
        # 退回舊法：鎮內所有登記站取最大（相容無對照表時）
        etr2_valid = set()
        for info in alert_table.values():
            for st in info.get('stations', []):
                nm = st.get('name','').strip()
                if nm:
                    etr2_valid.add(nm); etr2_valid.add(nm.rstrip('sSWw').strip())
        for sid, st in stations.items():
            key = st['county']+st['township']
            if key not in town: continue
            nm = st.get('name','').strip()
            if nm in etr2_valid or nm.rstrip('sSWw').strip() in etr2_valid:
                ev = calc_etr2(sid, history, now_tpe)
                if ev is not None:
                    town[key]['etr2'] = max(town[key]['etr2'] or 0.0, ev)
                    town[key]['station_etr2'][sid] = ev
        for key, td in town.items():
            ai = alert_table.get(key, {}); av = ai.get('alert_val', 0)
            td['etr2_pct'] = round(td['etr2']/av, 4) if td['etr2'] and av > 0 else None
        print(f"  鄉鎮聚合（舊法退回）：{len(town)} 個鄉鎮")
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
# 逐時警特報掃描結果快取：{model: {key: warn_seg[64]}}（每模式獨立，前端依所選模式取用）
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
        'forecast_days':  16,
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
        result[key] = segs_6h[:64]
        result_max_hourly[key] = max_hourly_6h[:64]

        # 逐時掃描 CWA 警特報條件（每個模式都算，供前端依所選模式顯示對應等級）
        # 大雨: 24h≥100 或 1h≥40；豪雨: 24h≥200 或 3h≥100
        # 大豪雨: 24h≥350 或 3h≥200；超大豪雨: 24h≥500
        pv = [v if v is not None else 0.0 for v in precip]
        if model == 'best_match':
            HOURLY_CACHE[key] = [round(v,1) for v in pv[:96]]  # 逐時QPF 96h（今天00起）
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
        WARN_SEG_CACHE.setdefault(model, {})[key] = warn_seg[:64]
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


# ── F-B0046 未來1小時雷達定量降雨預報（~1.4km 格點，每10分鐘更新）──
#   走 fileapi（datastore 對此格點產品會 404，同 O-A0038/F-C0041）。
#   fileapi 直接回內含格點數值的 JSON（頂層 cwaopendata），不需二段式。
FB0046_URL = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi/F-B0046-001"

def fetch_radar_qpf_1h(townships):
    """抓 F-B0046 未來1h雷達QPF格點 → 取各鄉鎮最近格點值。
    回傳 (town_vals: {county+township: mm}, datetime_str)；失敗回 ({}, '')。"""
    print("抓取 F-B0046 未來1h雷達定量降雨預報...")
    for attempt in range(3):
        try:
            r = requests.get(FB0046_URL, params={'Authorization': CWA_API_KEY,
                             'downloadType': 'WEB', 'format': 'JSON'}, timeout=60)
            if r.status_code != 200:
                print(f"    HTTP {r.status_code}"); 
                if attempt < 2: time.sleep(3); continue
                return {}, ''
            doc = json.loads(r.content.decode('utf-8', 'replace'))
            break
        except Exception as e:
            print(f"    失敗（{attempt+1}/3）：{e}")
            if attempt == 2: return {}, ''
            time.sleep(3)
    else:
        return {}, ''
    # 解析格點
    try:
        # 相容大小寫（datastore 版多為小寫 dataset）
        root = doc.get('cwaopendata', doc)
        ds = root.get('dataset') or root.get('Dataset') or {}
        info = ds.get('datasetInfo') or ds.get('DatasetInfo') or {}
        ps = info.get('parameterSet') or info.get('ParameterSet') or {}
        lon0 = float(ps.get('StartPointLongitude', 118.0))
        lat0 = float(ps.get('StartPointLatitude', 20.0))
        res  = float(ps.get('GridResolution', 0.0125))
        nx   = int(ps.get('GridDimensionX', 441))
        ny   = int(ps.get('GridDimensionY', 561))
        dtstr = ps.get('DateTime', '')
        conts = ds.get('contents') or ds.get('Contents') or {}
        content = conts.get('content') or conts.get('Content') or ''
        if isinstance(content, dict):
            content = content.get('#text') or content.get('ContentText') or ''
        import re as _re
        nums = _re.findall(r'-?\d+\.?\d*[Ee][+-]?\d+', str(content))
        vals = [float(x) for x in nums]
        if len(vals) < nx*ny*0.5:
            print(f"    格點數異常：{len(vals)}（期望 {nx*ny}）"); return {}, dtstr
        # 實際左下角 = StartPoint - res/2（log 註明第一點為 117.975/19.975）
        gl0 = lon0 - res/2
        gb0 = lat0 - res/2
        town_vals = {}
        for t in townships:
            lat = t.get('lat'); lng = t.get('lng')
            if lat is None or lng is None: continue
            gx = round((lng - gl0) / res)
            gy = round((lat - gb0) / res)
            if 0 <= gx < nx and 0 <= gy < ny:
                v = vals[gy*nx + gx]
                town_vals[f"{t['county']}{t['township']}"] = round(v, 1) if v >= 0 else None
        n_rain = sum(1 for v in town_vals.values() if v and v > 0)
        print(f"    雷達QPF：{len(town_vals)} 鄉鎮取值，{n_rain} 個有雨（時間 {dtstr}）")
        return town_vals, dtstr
    except Exception as e:
        print(f"    解析失敗：{e}"); return {}, ''



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

def compute_warn_seg_from_hourly(hourly):
    """從逐時降雨陣列算 CWA 警特報逐段等級（與各模式主掃描同標準）。
    回傳 warn_seg[≤64]。供 hi/lo 等「由 best 逐時×比值」衍生的模式即時計算。"""
    if not hourly:
        return []
    pv = [v if v is not None else 0.0 for v in hourly]
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
    return [max(warn_hourly[j:j+6]) if warn_hourly[j:j+6] else 0
            for j in range(0, len(warn_hourly), 6)][:64]


def fetch_ensemble_ratios(townships):
    """
    以縣級代表點抓 ECMWF 系集（51成員），計算全預報期各6h段的
    強降雨放大倍率（前25%成員均值/中位數）與弱降雨縮小倍率（後25%/中位數）。
    回傳 {county: {'hi':[N], 'lo':[N]}}；失敗回空dict（前端退回qpf_best）。
    """
    print("抓取 ECMWF 系集（縣級代表點，全期）...")
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
        'forecast_days': 15,   # ECMWF 系集支援 15 天（全期強/弱降雨情境）
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
        # 段數：以最短成員長度為準（15天=60段；不足則有多少算多少）
        n_hours = min(len(m) for m in members)
        n_seg = min(60, n_hours // 6)
        hi_arr, lo_arr = [], []
        for sg in range(n_seg):
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
    print(f"    系集比值：{len(ratios)} 縣市（各 {n_seg if ratios else 0} 段，全期）")
    return ratios


def apply_hourly_ratio(hourly, county, ens_ratios, kind):
    """逐時QPF × 縣級系集比值（比值以6h段為單位，套用至段內各小時；全期）。"""
    r = ens_ratios.get(county, {}).get(kind)
    if not r or not hourly:
        return list(hourly)
    nseg = len(r)
    return [round(v * r[min(h//6, nseg-1)], 1) for h, v in enumerate(hourly)]


def apply_ensemble_ratio(qpf, maxh, county, ens_ratios, kind):
    """qpf_best × 縣級系集比值（全期各段）。超出比值長度的段維持原值。回傳新陣列。"""
    r = ens_ratios.get(county, {}).get(kind)
    if not r:
        return list(qpf), list(maxh)
    nseg = len(r)
    q2 = [round(v*r[i], 1) if i < nseg and v else v for i, v in enumerate(qpf)]
    m2 = [round(v*r[i], 1) if i < nseg and v else v for i, v in enumerate(maxh)]
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

def fetch_typhoon_track():
    """W-C0034-005：西北太平洋及南海活動中熱帶氣旋之過去軌跡與預報路徑。
    注意：CWA 此資料集欄位一律大寫開頭（TropicalCyclones/AnalysisData/Fix...）。
    回傳精簡結構陣列；無颱風或失敗回 []。"""
    def _f(v):
        try: return float(v)
        except: return None
    def _i(v):
        try: return int(float(v))
        except: return None
    def _radius(node):
        """Circle15ms/Circle25ms → 半徑(km)；象限半徑略過（畫圓即可）。"""
        if not isinstance(node, dict): return None
        return _i(node.get('Radius'))

    print("抓取颱風路徑（W-C0034-005）...")
    doc = None
    for attempt in range(3):
        try:
            r = requests.get(f"{BASE_URL}/W-C0034-005",
                             params={'Authorization': CWA_API_KEY, 'format': 'JSON'},
                             timeout=45)
            if r.status_code != 200:
                print(f"    HTTP {r.status_code}")
                if attempt < 2: time.sleep(3); continue
                return []
            doc = json.loads(r.content.decode('utf-8', 'replace'))
            break
        except Exception as e:
            print(f"    失敗（{attempt+1}/3）：{e}")
            if attempt == 2: return []
            time.sleep(3)
    if not doc: return []

    try:
        rec = doc.get('records', {}) or {}
        tcs = rec.get('TropicalCyclones') or {}
        lst = tcs.get('TropicalCyclone') or []
        if isinstance(lst, dict): lst = [lst]
        out = []
        for ty in lst:
            ana = (ty.get('AnalysisData') or {}).get('Fix') or []
            if isinstance(ana, dict): ana = [ana]
            fcs = (ty.get('ForecastData') or {}).get('Fix') or []
            if isinstance(fcs, dict): fcs = [fcs]
            past = []
            for f in ana:
                lng, lat = _f(f.get('CoordinateLongitude')), _f(f.get('CoordinateLatitude'))
                if lng is None or lat is None: continue
                past.append({
                    't': f.get('DateTime',''), 'lng': lng, 'lat': lat,
                    'ws': _i(f.get('MaxWindSpeed')), 'gust': _i(f.get('MaxGustSpeed')),
                    'p': _i(f.get('Pressure')),
                    'r15': _radius(f.get('Circle15ms')), 'r25': _radius(f.get('Circle25ms')),
                })
            fut = []
            for f in fcs:
                lng, lat = _f(f.get('CoordinateLongitude')), _f(f.get('CoordinateLatitude'))
                if lng is None or lat is None: continue
                fut.append({
                    'init': f.get('InitialTime',''), 'fh': _i(f.get('ForecastHour')),
                    'lng': lng, 'lat': lat,
                    'ws': _i(f.get('MaxWindSpeed')), 'gust': _i(f.get('MaxGustSpeed')),
                    'p': _i(f.get('Pressure')),
                    'mspd': _i(f.get('MovingSpeed')), 'mdir': f.get('MovingDirection',''),
                    'r15': _radius(f.get('Circle15ms')), 'r25': _radius(f.get('Circle25ms')),
                    'r70': _i(f.get('Radius70PercentProbability')),
                })
            if not past and not fut: continue
            cur = past[-1] if past else None
            out.append({
                'name_en': ty.get('TyphoonName',''),
                'name_zh': ty.get('CwaTyphoonName',''),
                'ty_no': ty.get('CwaTyNo',''), 'td_no': ty.get('CwaTdNo',''),
                'year': ty.get('Year',''),
                'current': cur, 'past': past, 'forecast': fut,
            })
        if out:
            for t in out:
                c = t.get('current') or {}
                print(f"    {t['name_zh']}({t['name_en']}) 編號{t['ty_no']}："
                      f"現在 {c.get('lat')}N/{c.get('lng')}E 風速{c.get('ws')}m/s "
                      f"氣壓{c.get('p')}hPa｜過去{len(t['past'])}點、預報{len(t['forecast'])}點")
        else:
            print("    目前無活動中熱帶氣旋")
        return out
    except Exception as e:
        print(f"    解析失敗：{e}")
        return []


def fetch_typhoon_warning():
    """W-C0034-001：颱風警報單（官方原文）。
    已實測結構（2026-08-08 白海豚 海警第6報）：
      records.info[] 每筆＝一份警報單
        effective / onset / expires          發布、生效、失效時間
        headline                             「海上颱風警報」/「陸上颱風警報」…
        description.section[]                {title,value} ← 官方原文段落
            命名與位置／強度與半徑／移速與預測／颱風動態／警戒區域及事項
            （條件出現：大雨特報／強風特報／注意事項）
        description['typhoon-info'][0].section[]
            警報報數／警報類別(SEA|LAND)／颱風編號／颱風資訊{analysis,prediction}
        parameter[]  alert_title / severity_level / alert_color / website_color
        area[]       areaDesc + polygon（警戒海域/陸域）
    警報單文字**一律照抄不改寫**；系統自算的時間點另外標示。
    無警報或失敗回 []。"""
    print("抓取颱風警報單（W-C0034-001）...")
    doc = None
    for attempt in range(3):
        try:
            r = requests.get(f"{BASE_URL}/W-C0034-001",
                             params={'Authorization': CWA_API_KEY, 'format': 'JSON'},
                             timeout=45)
            if r.status_code != 200:
                print(f"    HTTP {r.status_code}")
                if attempt < 2: time.sleep(3); continue
                return []
            doc = json.loads(r.content.decode('utf-8', 'replace'))
            break
        except Exception as e:
            print(f"    失敗（{attempt+1}/3）：{e}")
            if attempt == 2: return []
            time.sleep(3)
    if not doc: return []

    def _sections(node):
        """{title,value} 陣列 → [{'title':..,'value':..}]（保序、去空）。"""
        if isinstance(node, dict): node = [node]
        if not isinstance(node, list): return []
        out = []
        for s in node:
            if not isinstance(s, dict): continue
            ti = (s.get('title') or s.get('Title') or '').strip()
            va = s.get('value') if 'value' in s else s.get('Value')
            if isinstance(va, (dict, list)): continue      # 颱風資訊那種結構化節點另外處理
            va = (va or '').strip()
            if ti and va: out.append({'title': ti, 'value': va})
        return out

    def _struct(node):
        """從 typhoon-info 的 section 撈出結構化的 analysis / prediction。"""
        if isinstance(node, dict): node = [node]
        if not isinstance(node, list): return {}
        got = {}
        for s in node:
            if not isinstance(s, dict): continue
            for key in ('analysis', 'Analysis', 'prediction', 'Prediction'):
                if isinstance(s.get(key), dict):
                    got[key.lower()] = s[key]
        return got

    try:
        rec = doc.get('records', {}) or {}
        infos = rec.get('info') or rec.get('Info') or []
        if isinstance(infos, dict): infos = [infos]
        out = []
        for inf in infos:
            if not isinstance(inf, dict): continue
            desc = inf.get('description') or {}
            nm_zh, nm_en = '', ''
            if isinstance(desc, str):        # 極少數情形整段是純文字
                secs, tyinfo, struct = [{'title': '警報內容', 'value': desc.strip()}], [], {}
            else:
                secs   = _sections(desc.get('section') or desc.get('Section'))
                tinode = desc.get('typhoon-info') or desc.get('typhoonInfo') or []
                if isinstance(tinode, dict): tinode = [tinode]
                tsec, struct = [], {}
                for ti in tinode:
                    if not isinstance(ti, dict): continue
                    tsec += _sections(ti.get('section') or ti.get('Section'))
                    struct.update(_struct(ti.get('section') or ti.get('Section')))
                    nm_zh = nm_zh or (ti.get('cwa_typhoon_name') or '').strip()
                    nm_en = nm_en or (ti.get('typhoon_name') or '').strip()
                tyinfo = tsec
            params = {}
            pl = inf.get('parameter') or []
            if isinstance(pl, dict): pl = [pl]
            for p in pl:
                if isinstance(p, dict) and p.get('valueName'):
                    params[str(p['valueName']).strip()] = str(p.get('value', '')).strip()
            areas = []
            al = inf.get('area') or []
            if isinstance(al, dict): al = [al]
            for a in al:
                if isinstance(a, dict) and (a.get('areaDesc') or '').strip():
                    areas.append((a['areaDesc']).strip())   # polygon 刻意不帶（前端不畫警戒海域）
            meta = {s['title']: s['value'] for s in tyinfo}
            out.append({
                'effective': (inf.get('effective') or '').strip(),
                'onset':     (inf.get('onset') or '').strip(),
                'expires':   (inf.get('expires') or '').strip(),
                'headline':  (inf.get('headline') or '').strip(),
                'sender':    (inf.get('senderName') or '').strip(),
                'severity_level': params.get('severity_level', ''),
                'alert_color':    params.get('alert_color', ''),
                'name_zh': nm_zh, 'name_en': nm_en,
                'report_no': meta.get('警報報數', ''),
                'warn_kind': meta.get('警報類別', ''),      # SEA / LAND
                'ty_no':     meta.get('颱風編號', ''),
                'sections':  secs,                          # ← 官方原文，前端照抄
                'analysis':   struct.get('analysis'),
                'prediction': struct.get('prediction'),
                'areas': areas,
                'params': params,
            })
        if out:
            for o in out:
                print(f"    {o['headline']}第{o['report_no'] or '?'}報"
                      f"（{o['warn_kind'] or '?'}）發布 {o['effective'][:16]}"
                      f"｜段落 {len(o['sections'])} 個｜警戒區 {len(o['areas'])} 處")
        else:
            print("    目前無颱風警報")
        return out
    except Exception as e:
        print(f"    解析失敗：{e}")
        return []


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
      C. 全部僅圖檔時：下載 QPF PNG 存 qpf_sample.png，以內建四海岬單應性校正判讀
         （decode_qpf_png，樣張色表已內建）
    """
    if not CWA_API_KEY: return None
    towns = load_all_townships()   # PNG 判讀路徑需要鄉鎮座標
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
    _scan_ok, _scan_fail = 0, 0
    for did in scan_ids:
        if did in seen: continue
        seen.add(did)
        try:
            r = requests.get(f"{FILEAPI}/{did}", params={'Authorization': CWA_API_KEY,
                             'downloadType': 'WEB', 'format': 'JSON'}, timeout=20)
            if r.status_code != 200:
                _scan_fail += 1
                continue   # 不存在的 dataid 靜默跳過（避免log爆量）
            _scan_ok += 1
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
                    # 擷取 ResourceDesc（含羅馬數字 (I)(II)(III)(IV) → 定序時段）
                    _rdesc = ''
                    def _find_rdesc(o):
                        if isinstance(o, dict):
                            for k, v in o.items():
                                if k == 'ResourceDesc' and isinstance(v, str): return v
                                r = _find_rdesc(v)
                                if r: return r
                        elif isinstance(o, list):
                            for it in o:
                                r = _find_rdesc(it)
                                if r: return r
                        return ''
                    _rdesc = _find_rdesc(doc)
                    png_uris.append((did, u, _rdesc))
        except Exception as e:
            print(f"    {did} 例外：{e}")

    print(f"    掃描 {len(seen)} 個 dataid：{_scan_ok} 個有回應、{_scan_fail} 個不存在/失敗")
    # ── C. 僅圖檔 → 色塊判讀路徑 ──
    #   收集所有 PNG 候選，優先定量降水預報主圖；下載 ref-size 者逐一判讀，
    #   依各自時間窗（檔名 _HH_HH）合併成連續 6h 段序列（覆蓋越多窗越好）。
    print(f"    探測完成：共收集到 {len(png_uris)} 個 PNG uri"
          + (f"（dataid：{sorted(set(d for d,_,_ in png_uris))[:10]}）" if png_uris else "（完全沒有 PNG——CWA此時段可能未發布定量降水預報圖，或探測範圍/API有問題）"))
    if png_uris:
        import struct
        def _png_score(u):
            ul = u.lower(); s = 0
            if 'chfcstprecip' in ul: s += 10
            if 'qpf' in ul: s += 6
            if 'precip' in ul: s += 3
            if 'thumb' in ul or 'small' in ul or 'icon' in ul: s -= 8
            return -s
        png_uris.sort(key=lambda du: _png_score(du[1]))
        # 診斷：列出所有 PNG 候選及其檔名窗（讓 log 揭露 CWA 到底出了哪些時段的圖）
        import re as _re2
        print(f"    共 {len(png_uris)} 個 PNG 候選：")
        for _did, _u, _rd in png_uris[:20]:
            _fn = _u.rsplit('/',1)[-1]
            _wm = _re2.search(r'_(\d{1,3})_(\d{1,3})(?:\.png)?$', _fn)
            _win = f"{_wm.group(1)}-{_wm.group(2)}h" if _wm else (_rd or "無窗標示")
            print(f"      {_did}: {_fn[:40]} [{_win}]")
        merged = {}          # start_tpe -> {town_key: val}
        used_src = []
        _cwa_windows = []    # 每張圖的真實起訖時段（供前端按鈕標籤）
        _saved_sample = False
        for did, u, rdesc in png_uris[:16]:
            try:
                r = requests.get(u, timeout=60)
                if r.status_code != 200 or r.content[:8] != b'\x89PNG\r\n\x1a\n':
                    continue
                w, h = struct.unpack('>II', r.content[16:24])
                fn = u.rsplit('/',1)[-1]
                print(f"    PNG候選 {did}：{w}×{h}（{len(r.content)//1024}KB）{fn[:50]} [{rdesc}]")
                if not _saved_sample:
                    with open('qpf_sample.png', 'wb') as f: f.write(r.content)
                    _saved_sample = True
                if (w, h) != QPF_PNG_REF_SIZE:
                    continue
                # 依 ResourceDesc 羅馬數字定序（逐12h）：(I)=第1個12h窗…(IV)=第4個
                #   CWA定量降水預報發布班次：05/11/17/23時，圖涵蓋「發布時刻起」逐12h。
                #   以發布時刻為錨（非今天00:00），每張圖固定12h，前端按鈕跟著真實時段走。
                _roman = {'(I)':0, '(II)':1, '(III)':2, '(IV)':3}
                _worder = None
                for _rk, _rv in _roman.items():
                    if rdesc and _rk in rdesc:
                        _worder = _rv; break
                # 發布時刻：今日最近且 ≤ now 的 05/11/17/23 時
                _issue = None
                if _worder is not None:
                    _cands = []
                    for _dh in (-1, 0):
                        _d = now_tpe.replace(minute=0, second=0, microsecond=0, tzinfo=None) + timedelta(days=_dh)
                        for _hh in (5, 11, 17, 23):
                            _cands.append(_d.replace(hour=_hh))
                    _issue = max([c for c in _cands if c <= now_tpe.replace(tzinfo=None)], default=None)
                # 該圖真實起訖（發布 + order*12h ~ +12h）
                _win_start = _issue + timedelta(hours=12*_worder) if (_issue and _worder is not None) else None
                # 轉段索引（今天00:00起算、吸附6h邊界）供內部陣列定位
                _wseg = None
                if _win_start is not None:
                    _day0 = _win_start.replace(hour=0, minute=0, second=0, microsecond=0)
                    _off = (_win_start - _day0).total_seconds()
                    _wseg = int((_day0 - now_tpe.replace(hour=0,minute=0,second=0,microsecond=0,tzinfo=None)).total_seconds()//21600) \
                            + round(_off/21600)
                    if _wseg < 0: _wseg = None   # 落在過去則丟棄
                segs = decode_qpf_png(r.content, did, now_tpe, towns, fname=fn,
                                      win_seg=_wseg, win_nseg=2)
                if segs and _win_start is not None:
                    _cwa_windows.append({
                        'start': _win_start.strftime('%Y-%m-%dT%H:%M:%S'),
                        'end': (_win_start + timedelta(hours=12)).strftime('%Y-%m-%dT%H:%M:%S'),
                        'seg': _wseg, 'order': _worder,
                    })
                if segs:
                    for st, tv in segs.items():
                        if st not in merged:      # 先到先得（同窗不重複；不同窗互補）
                            merged[st] = tv
                    used_src.append(f"{did}:{fn[:24]}")
            except Exception as e:
                print(f"    PNG候選 {did} 失敗：{e}")
        if merged:
            print(f"    ✓ 常態QPF（PNG單應性判讀）：合併 {len(merged)} 段"
                  f"（近似，僅級距；來源 {len(used_src)} 張圖）")
            with open(CWA_QPF_SRC_FILE, 'w', encoding='utf-8') as f:
                json.dump({'kind': 'png', 'sources': used_src}, f, ensure_ascii=False)
            return {'issue': 'png:' + ','.join(used_src)[:80], 'segs': merged, 'png': True,
                    'windows': sorted(_cwa_windows, key=lambda w: w['start'])}
        if _saved_sample:
            print(f"    有PNG但無 {QPF_PNG_REF_SIZE[0]}×{QPF_PNG_REF_SIZE[1]} 主圖——已存 qpf_sample.png，"
                  f"若為改版式請提供以重新定位四海岬")
    print("    常態QPF：格點資料探測未果（以上 uri 清單請貼給開發者）")
    return None

# ── QPF PNG 色塊判讀（單應性校正，樣張已內建 F-C0035 定量降水預報 II 色表）──
# 校正基準：2026/07/20 樣張 QPF_ChFcstPrecip_12_24.png（1245×1500）四海岬像素定位，
#   單應性（透視變形已修正，非軸對齊——實測仿射殘差達27px、單應性收斂至0）。
#   H3 由 CAPES 四點解得；不同尺寸/版式的圖需重新定位（見 QPF_PNG_H3 尺寸檢查）。
# 色表：右側圖例17級實測色（級距下界為代表值，保守）。
CAPES_PX = {  # 樣張像素座標（1245×1500）
    'N': (933, 202),  'E': (1102, 306),  'S': (687, 1423),  'W': (380, 860),
}
CAPES_LL = {
    'N': (121.5366, 25.2977),  'E': (122.0017, 25.0074),
    'S': (120.8585, 21.8968),  'W': (120.0358, 23.1008),
}
QPF_PNG_REF_SIZE = (1245, 1500)
# 17級色表 (R,G,B,代表值mm)：<0.5 不著色；級距用「下界」代表（≥110→110、90-110→90…）
QPF_PNG_BANDS = [
    (253,201,255, 300), (251,0,255, 200), (201,0,204, 150), (150,0,153, 130),
    (153,0,0, 110),     (204,0,0, 90),    (255,0,0, 70),     (255,149,0, 50),
    (255,200,0, 40),    (255,251,3, 30),  (57,255,3, 20),    (5,153,2, 15),
    (3,99,255, 10),     (5,155,255, 5),   (3,200,255, 2),    (156,252,255, 1),
    (194,194,194, 0.5),
]
QPF_PNG_TOL = 42          # 色距容忍（√(42²×3)≈73）
QPF_PNG_WINDOW_HOURS = 12 # 定量降水預報(II) 為 12h 有效時段

def _png_solve_homography(px_map, ll_map):
    """四點 DLT 解 lon/lat→pixel 單應性（回 3×3 list）"""
    import numpy as _np
    A = []
    for k in ['N','E','S','W']:
        x, y = px_map[k]; lon, lat = ll_map[k]
        A.append([lon,lat,1,0,0,0,-x*lon,-x*lat,-x])
        A.append([0,0,0,lon,lat,1,-y*lon,-y*lat,-y])
    _, _, vt = _np.linalg.svd(_np.array(A))
    h = vt[-1].reshape(3,3)
    return (h / h[2,2]).tolist()

def _png_ll2px(H3, lon, lat):
    w = H3[2][0]*lon + H3[2][1]*lat + H3[2][2]
    return ((H3[0][0]*lon+H3[0][1]*lat+H3[0][2])/w,
            (H3[1][0]*lon+H3[1][1]*lat+H3[1][2])/w)

def decode_qpf_png(png_bytes, did, now_tpe, towns, fname='', win_seg=None, win_nseg=2):
    """CWA 定量降水預報 PNG → 各鄉鎮 QPF 值（單應性 + 質心多點取樣多數決）。
    需 Pillow。回傳 {start_tpe: {town_key: val}}（鄉鎮字典，非網格）；失敗回 None。
    ⚠色塊判讀為近似（僅級距、無精確值），12h 圖均分兩個 6h 段。"""
    try:
        from PIL import Image
        import numpy as _np, io as _io
    except ImportError:
        print("    需要 Pillow/numpy：workflow 請加 pip install pillow numpy")
        return None
    img = Image.open(_io.BytesIO(png_bytes)).convert('RGB')
    W, H = img.size
    arr = _np.asarray(img)
    # 尺寸須與樣張一致才能套用內建像素座標（否則需重新定位）
    if (W, H) != QPF_PNG_REF_SIZE:
        print(f"    PNG尺寸 {W}×{H} ≠ 校正基準 {QPF_PNG_REF_SIZE}——無法套用內建四點，略過")
        print(f"    （若為新版式，請提供新樣張重新定位四海岬像素）")
        return None
    H3 = _png_solve_homography(CAPES_PX, CAPES_LL)
    bands = QPF_PNG_BANDS
    tol2 = QPF_PNG_TOL * QPF_PNG_TOL * 3

    def classify_px(xi, yi):
        """單像素→級距值；純色才回值，等值線/文字/過渡回 None，白底回 0.0"""
        if xi < 0 or xi >= W or yi < 0 or yi >= H: return None
        r, g, b = int(arr[yi,xi,0]), int(arr[yi,xi,1]), int(arr[yi,xi,2])
        mx, mn = max(r,g,b), min(r,g,b)
        if mx > 235 and mn > 225: return 0.0          # 白底＝無雨
        if mx < 55: return None                        # 黑等值線/邊界
        if (mx-mn) < 22 and 55 <= mx < 230: return None  # 低飽和灰（文字/格線）
        best, bd = None, tol2 + 1
        for (br,bg,bb,bv) in bands:
            d = (r-br)**2 + (g-bg)**2 + (b-bb)**2
            if d < bd: bd, best = d, bv
        return best if bd <= tol2 else None            # 非純色（過渡）→ 丟棄

    # 鄉鎮取樣密度：以像素空間半徑 R 內的密集網格取直方圖。
    # R 依緯度換算（1鄉鎮尺度約 0.03~0.08°，取影像上約 8~14px 半徑掃描）。
    def town_hist(lon, lat):
        cx, cy = _png_ll2px(H3, lon, lat)
        R = 11                       # 像素半徑（涵蓋鄉鎮質心鄰域，避開跨鄉鎮太遠）
        step = 2
        votes = {}
        for dy in range(-R, R+1, step):
            for dx in range(-R, R+1, step):
                if dx*dx + dy*dy > R*R: continue      # 圓形鄰域
                v = classify_px(int(round(cx))+dx, int(round(cy))+dy)
                if v is not None: votes[v] = votes.get(v, 0) + 1
        return votes

    # 級距值由小到大（用於限制「跳級」幅度）
    _BAND_VALS = sorted({bv for (_r, _g, _b, bv) in bands})

    def aggregate(votes, hi_frac=0.35, hi_min=8):
        """眾數為主；更高級距要「佔比足夠且票數足夠」才升級（防漏報但不誤報）。
        兩道防線（對付白底/邊界反鋸齒誤配到淺色高級距，如 #FDC9FF≥300）：
          ① 眾數為 0（幾乎無雨）→ 需極強證據（佔比≥0.55 且票數≥14）才升級
          ② 一般情況 → 升級最多跨 2 個級距，避免少數雜訊像素把值拉到最高帶
        """
        if not votes: return None
        tot = sum(votes.values())
        mode = max(votes.items(), key=lambda kv: (kv[1], kv[0]))[0]
        if mode == 0.0:
            cand = [v for v, c in votes.items()
                    if v > 0 and c / tot >= 0.55 and c >= 14]
            return max(cand) if cand else 0.0
        try:
            mi = _BAND_VALS.index(mode)
        except ValueError:
            mi = 0
        max_val = _BAND_VALS[min(mi + 2, len(_BAND_VALS) - 1)]
        hi = [v for v, c in votes.items()
              if v > mode and v <= max_val and c / tot >= hi_frac and c >= hi_min]
        return max(hi) if hi else mode

    town_vals = {}
    n_hit = 0
    for t in towns:
        lat, lng = t.get('lat'), t.get('lng')
        if not lat: continue
        key = f"{t['county']}{t['township']}"
        val = aggregate(town_hist(lng, lat))
        if val is None: continue
        town_vals[key] = val
        if val > 0: n_hit += 1
    print(f"    PNG判讀（單應性+密集鄰域）：{n_hit}/{len(towns)} 鄉鎮有雨值")

    # ★明確窗位（呼叫端依 ResourceDesc 羅馬數字 (I)(II)(III)(IV) 指定）：
    #   定量降水預報圖逐12h、共4張=48h。win_seg=該圖起始段索引（今天00:00起算）。
    #   win_nseg=該圖涵蓋幾段（12h=2段）。色階為類別，套用到窗內每個6h子段。
    if win_seg is not None:
        _base00 = now_tpe.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
        seg_vals = dict(town_vals)
        segs = {}
        for k in range(win_nseg):
            segs[_base00 + timedelta(hours=6*(win_seg + k))] = seg_vals
        print(f"    明確窗位：段 {win_seg}~{win_seg+win_nseg-1}"
              f"（{_base00 + timedelta(hours=6*win_seg):%m/%d %H:%M} 起 {win_nseg} 段）")
        return segs

    # 時間窗：檔名如 ...Precip_12_24 表示「發布起 +12h~+24h」。
    #   解析 _HH_HH → 相對發布時刻的起訖小時；發布時刻取最近的 CWA 發布班次
    #   （05:30/11:30/17:30/23:30 TST）。無法解析則退回「下一個6h邊界起、12h窗」。
    import re as _re
    m = _re.search(r'_(\d{1,3})_(\d{1,3})(?:\.png)?$', fname or '')
    win_h = QPF_PNG_WINDOW_HOURS
    if m:
        h0, h1 = int(m.group(1)), int(m.group(2))
        # 接受 6~48h 的任何合理窗（6的倍數）；涵蓋 0_12 / 12_24 / 24_48 等各版式
        if h1 > h0 and (h1 - h0) % 6 == 0 and (h1 - h0) <= 48 and h1 <= 72:
            win_h = h1 - h0
            # 發布班次：取今日最近且 ≤ now 的 05:30/11:30/17:30/23:30
            _cands = []
            for _dh in (-1, 0):
                _d = now_tpe.replace(minute=30, second=0, microsecond=0, tzinfo=None) + timedelta(days=_dh)
                for _hh in (5, 11, 17, 23):
                    _cands.append(_d.replace(hour=_hh))
            _issue = max([c for c in _cands if c <= now_tpe.replace(tzinfo=None)], default=None)
            if _issue is not None:
                _win_start = _issue + timedelta(hours=h0)
                # 吸附 6h 日曆邊界（發布在 :30，+偶數h 仍偏移30分）
                _day0 = _win_start.replace(hour=0, minute=0, second=0, microsecond=0)
                _off = (_win_start - _day0).total_seconds()
                _snap = round(_off / 21600) * 21600
                base = _day0 + timedelta(seconds=_snap)
                n_seg = max(1, win_h // 6)
                # 色階為「類別」而非可加量：色帶直接套用到窗內每個 6h 子段
                #   （不除以段數——除法會破壞色帶身分，害前端對不到官方色）
                seg_vals = dict(town_vals)
                segs = {}
                for k in range(n_seg):
                    segs[base + timedelta(hours=6*k)] = seg_vals
                print(f"    時間窗（檔名 {h0}-{h1}h，{win_h}h窗）："
                      f"{base.strftime('%m/%d %H:%M')} 起 {n_seg} 段（色階類別）")
                return segs
        else:
            print(f"    檔名窗 {h0}-{h1}h 不合理（差={h1-h0}），退回預設窗")
    # 退回：下一個 6h 邊界起、12h 窗（色階為類別，直接套用不除段）
    base = now_tpe.replace(minute=0, second=0, microsecond=0, tzinfo=None)
    base = base + timedelta(hours=(6 - base.hour % 6) % 6)
    n_seg = max(1, win_h // 6)
    seg_vals = dict(town_vals)
    segs = {}
    for k in range(n_seg):
        segs[base + timedelta(hours=6*k)] = seg_vals
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
    slope_warn = load_slope_warn()
    ls_warn    = load_ls_warn()      # 大崩警戒區（代表站；未來推估側用）
    # 讀上一輪 data.json 的官方 ETR2 → 供前端「這期 vs 前期」趨勢箭頭（同一資料源，可比）
    prev_etr2 = {}
    try:
        if os.path.exists('data.json'):
            with open('data.json', encoding='utf-8') as _f:
                _old = json.load(_f)
            for _t in _old.get('townships', []):
                _e = _t.get('etr2')
                if _e is not None:
                    prev_etr2[f"{_t.get('county','')}{_t.get('township','')}"] = _e
            print(f"  前期 ETR2（上一輪 data.json）：{len(prev_etr2)} 個鄉鎮")
    except Exception as _e:
        print(f"  讀取前期 ETR2 失敗（不影響）：{_e}")
    swcb_etr2 = fetch_swcb_etr2()   # 站名→官方ETR2 對照
    hourly_ser, hourly_meta = load_hourly_series()   # 逐時序列（暖機未滿時自動回「資料不足」）
    time.sleep(1)
    static_list = list(alert_table.values())
    counties_needed = set(t['county'] for t in static_list)

    # 觀測
    stations = fetch_obs()
    history  = update_history(stations,now_tpe) if stations else \
               (json.load(open(HISTORY_FILE)) if os.path.exists(HISTORY_FILE) else {})
    town_obs = agg_obs(stations,alert_table,history,now_tpe,slope_warn,swcb_etr2)

    # PoP
    pop3d, pop7d = fetch_all_pop(counties_needed)

    # 颱風 QPF（先抓，決定 is_typhoon 旗標）
    typhoon_segs = fetch_typhoon_qpf() if CWA_API_KEY else []
    typhoon_track = fetch_typhoon_track() if CWA_API_KEY else []
    typhoon_warn  = fetch_typhoon_warning() if CWA_API_KEY else []
    debris_alerts = fetch_debris_alerts()
    # 雙軌：現況紅黃走官方發布值、未來推估自算
    official_alerts = fetch_official_alerts()
    ls_alert_vals   = fetch_ls_alert_values()
    is_typhoon   = len(typhoon_segs) >= 4

    # 常態 CWA QPF（官方預報員修正值；治本預測偏差）——任何時候都跑，
    # 讓「CWA 模式」隨時有官方定量降水判讀可看（颱風期另有 F-C0041 精確格點，
    # 兩者段索引不重疊時互補；重疊段以颱風精確值優先，見組裝端）。
    routine_qpf = None
    if CWA_API_KEY:
        try: routine_qpf = fetch_cwa_routine_qpf(now_tpe)
        except Exception as e: print(f"  常態QPF例外：{e}")
    # 對齊日曆6h段：idx = (start − 今天00時TST)/6h（qpf_15d[0]=今天00-06 鐵律）
    _today00 = now_tpe.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    routine_seg_map = {}
    routine_is_png = bool(routine_qpf and routine_qpf.get('png'))
    if routine_qpf:
        for _st, _vals in routine_qpf['segs'].items():
            _sec = (_st - _today00).total_seconds()
            _idx = int(_sec // 21600)
            if 0 <= _idx < 60 and _sec % 21600 == 0:
                routine_seg_map[_idx] = _vals
        print(f"  常態QPF對齊段索引：{sorted(routine_seg_map)}"
              f"{'（PNG鄉鎮字典，近似）' if routine_is_png else ''}")

    # 官方現行警特報（與系統預估對照，落差可視化）
    official_warn = fetch_official_warnings() if CWA_API_KEY else None

    # Open-Meteo（四個模式）
    om_all, om_max_hourly_all = fetch_openmeteo(static_list)
    om = om_all.get('ecmwf_ifs025', {})  # 預設用 ECMWF IFS，對台灣地形雨準確度較高

    # QPESUMS 網格觀測（1h 即時 + 24h 歷史合成）
    # QPESUMS（O-A0038）已停用：CWA 該 dataid 現回傳溫度圖而非雨量網格。
    #   無測站鄉鎮改以雨量站聚合＋模式為準，不再耗時下載無用影像。
    print("QPESUMS 網格觀測：已停用（CWA 未提供雨量網格）")
    qp_grid = {}
    qp_24h  = load_qpesums_history()
    qp_p48  = load_qpesums_p48()
    if qp_24h: print(f"    QPESUMS 24h 歷史：{len(qp_24h)} 個鄉鎮")
    if qp_p48: print(f"    QPESUMS 逐時觀測 p48：{len(qp_p48)} 個鄉鎮")

    # F-B0046 未來1h雷達QPF（高解析銜接，過去觀測與未來6h段之間）
    # F-B0046 未來1h雷達QPF：完全由每小時腳本寫入獨立的 radar.json，主腳本不碰。
    #   前端載入時併入 radar.json——兩個 workflow 各寫各檔，永不在 data.json 上撞車。
    radar_qpf, radar_dt = {}, ''

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
        etr2_src    = obs.get('etr2_src')          # 'swcb'/'mixed'/'cwa'
        etr2_alert  = obs.get('etr2_alert')        # 最高單元的官方警戒值（前端算%分母）
        etr2_prev   = prev_etr2.get(key)           # 上一輪官方 ETR2（趨勢比較基準）
        slope_regions = obs.get('slope_regions')   # 各警戒區明細
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
                        for i in range(64)]
            return segs[:64]

        def get_max_hourly_model(model_key):
            """取特定模式的60個6h段內最大單一小時雨量（供強度分級用）"""
            arr = om_max_hourly_all.get(model_key, {}).get(om_key, [])
            return arr[:64] if arr else [0.0]*64

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

        # CWA 官方 QPF 覆蓋：
        #   (A) 颱風 F-C0041＝精確格點數值 → 覆蓋各模式（真實數值，有意義）。
        #   (B) 常態 PNG＝定量降水預報圖「色階類別」→ 僅供 CWA 模式著色，
        #       絕不轉數字、絕不覆蓋任何模式（色塊判讀本質是類別，硬轉數字會失真，
        #       且會污染 best/ecmwf/hi/lo——這正是先前數據異常的主因）。
        #   qpf_cwa：CWA 模式著色用陣列（PNG 段=色階代表值；颱風段=精確值；未覆蓋=null）。
        _cwa_by_idx = {}     # idx -> value（CWA模式著色用）
        # (A) 常態 PNG 色階（僅存 qpf_cwa，不動任何模式）
        if routine_seg_map and routine_is_png:
            _tkey = f"{county}{township}"
            for _idx, _vals in routine_seg_map.items():
                if not (0 <= _idx < len(qpf_best)): continue
                _v = _vals.get(_tkey)
                if _v is not None:
                    _cwa_by_idx[_idx] = round(float(_v), 1)   # 色階代表值（僅著色）
        elif routine_seg_map and not routine_is_png:
            # 常態格點（非PNG，真實數值）→ 可覆蓋模式（與颱風同性質）
            for _idx, _vals in routine_seg_map.items():
                if not (0 <= _idx < len(qpf_best)): continue
                _v = _qpf_grid_at(_vals, lat, lng)
                if _v is not None:
                    _cwa_by_idx[_idx] = round(float(_v), 1)
                    qpf_best[_idx] = qpf_ecmwf[_idx] = qpf_gfs[_idx] = qpf_icon[_idx] = _cwa_by_idx[_idx]
        # (B) 颱風 F-C0041 精確格點（真實數值，覆蓋模式）
        if is_typhoon and typhoon_segs:
            _cur_seg = now_tpe.hour // 6
            for _i, _seg in enumerate(typhoon_segs):
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
                if _v is not None:
                    _cwa_by_idx[_idx] = _v
                    qpf_best[_idx] = qpf_ecmwf[_idx] = qpf_gfs[_idx] = qpf_icon[_idx] = _v
        qpf_cwa = []
        if _cwa_by_idx:
            _max_idx = max(_cwa_by_idx)
            qpf_cwa = [None] * (_max_idx + 1)
            for _idx, _v in _cwa_by_idx.items():
                qpf_cwa[_idx] = _v

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
            'etr2_src':etr2_src,'etr2_alert':etr2_alert,'etr2_prev':etr2_prev,
            'slope_regions':slope_regions,
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
            # 每模式獨立警特報（前端依所選模式取用；hi/lo 由 best 逐時×系集比值即時算）
            'warn_seg':       WARN_SEG_CACHE.get('best_match', {}).get(f"{lat:.4f}_{lng:.4f}", []),
            'warn_seg_ecmwf': WARN_SEG_CACHE.get('ecmwf_ifs025', {}).get(f"{lat:.4f}_{lng:.4f}", []),
            'warn_seg_gfs':   WARN_SEG_CACHE.get('gfs_seamless', {}).get(f"{lat:.4f}_{lng:.4f}", []),
            'warn_seg_icon':  WARN_SEG_CACHE.get('icon_seamless', {}).get(f"{lat:.4f}_{lng:.4f}", []),
            'warn_seg_hi':    compute_warn_seg_from_hourly(apply_hourly_ratio(HOURLY_CACHE.get(f"{lat:.4f}_{lng:.4f}", []), county, ens_ratios, 'hi')),
            'warn_seg_lo':    compute_warn_seg_from_hourly(apply_hourly_ratio(HOURLY_CACHE.get(f"{lat:.4f}_{lng:.4f}", []), county, ens_ratios, 'lo')),
            'qpf_radar_1h': radar_qpf.get(f"{county}{township}"),   # F-B0046 未來1h雷達QPF(mm)
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
            return segs[:64] if segs else [0.0]*64
        def get_ns_maxh(model_key):
            arr = non_static_maxh.get(model_key, {}).get(om_key, [])
            return arr[:64] if arr else [0.0]*64

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
            'warn_seg':       WARN_SEG_CACHE.get('best_match', {}).get(f"{avg_lat:.4f}_{avg_lng:.4f}", []),
            'warn_seg_ecmwf': WARN_SEG_CACHE.get('ecmwf_ifs025', {}).get(f"{avg_lat:.4f}_{avg_lng:.4f}", []),
            'warn_seg_gfs':   WARN_SEG_CACHE.get('gfs_seamless', {}).get(f"{avg_lat:.4f}_{avg_lng:.4f}", []),
            'warn_seg_icon':  WARN_SEG_CACHE.get('icon_seamless', {}).get(f"{avg_lat:.4f}_{avg_lng:.4f}", []),
            'warn_seg_hi':    compute_warn_seg_from_hourly(apply_hourly_ratio(HOURLY_CACHE.get(f"{avg_lat:.4f}_{avg_lng:.4f}", []), at['county'], ens_ratios, 'hi')),
            'warn_seg_lo':    compute_warn_seg_from_hourly(apply_hourly_ratio(HOURLY_CACHE.get(f"{avg_lat:.4f}_{avg_lng:.4f}", []), at['county'], ens_ratios, 'lo')),
            'qpf_radar_1h': radar_qpf.get(f"{at['county']}{at['township']}"),   # F-B0046 未來1h雷達QPF(mm)
            'stations':  station_list,
            'daily_rain': obs.get('daily_rain', [0.0]*15),
        })

    # ════════════════════════════════════════════════════════
    #  警戒研判（雙軌）
    #    現況紅/黃 ＝ 水保署官方發布值（權威；欄位 off_level / off_report）
    #    未來推估   ＝ 系統自算（ETR2＋QPF，依技術指引門檻；欄位皆冠 est_）
    #  兩者刻意分欄，前端不得把推估值顯示成官方警戒。
    # ════════════════════════════════════════════════════════
    # 逐鄉鎮 QPF 查表：未來24h、以及夜間(今日19時→明日06時)窗
    _night_a = now_tpe.replace(hour=19, minute=0, second=0, microsecond=0)
    if now_tpe.hour >= 19:                     # 已過19時 → 指今晚剩餘至明晨06時
        _night_a = now_tpe
    _night_b = (_night_a + timedelta(days=1)).replace(hour=6, minute=0, second=0, microsecond=0)
    if _night_a.hour < 19 and now_tpe.hour < 6:   # 凌晨執行 → 夜間窗指「現在→今晨06時」
        _night_a, _night_b = now_tpe, now_tpe.replace(hour=6, minute=0, second=0, microsecond=0)

    _tq, _tqn = {}, {}
    for _t in out_towns:
        _k = _t['county'] + _t['township']
        if _t.get('qpf_24h') is not None: _tq[_k] = _t['qpf_24h']
        # 夜間窗 QPF：加總與 [_night_a,_night_b) 重疊的 6h 段（段起點 = base_dt + 6h*i）
        _arr = _t.get('qpf_best') or []
        _sum, _hit = 0.0, 0
        for _i, _v in enumerate(_arr[:12]):
            if _v is None: continue
            _s = base_dt + timedelta(hours=6 * _i)
            _e = _s + timedelta(hours=6)
            if _e <= _night_a or _s >= _night_b: continue
            _ov = (min(_e, _night_b) - max(_s, _night_a)).total_seconds() / 21600.0
            if _ov > 0: _sum += _v * _ov; _hit += 1
        _tqn[_k] = round(_sum, 1) if _hit else None
    print(f"  夜間窗（入夜前示警用）：{_night_a.strftime('%m/%d %H:%M')} → "
          f"{_night_b.strftime('%m/%d %H:%M')}，{sum(1 for v in _tqn.values() if v)} 個鄉鎮有夜間QPF")

    _off_d = official_alerts.get('debris', {})
    _off_l = official_alerts.get('landslide', {})
    _off_ok = official_alerts.get('ok', False)

    # ── 土石流（逐潛勢溪流）──────────────────────────
    if debris_alerts:
        _n_est_y = _n_est_r = _n_night = _n_adj = 0
        for _no, _d in debris_alerts.items():
            _key = _d['county'] + _d['town']
            _q24 = _tq.get(_key)
            _qn  = _tqn.get(_key)
            _d['qpf24'] = None if _q24 is None else round(_q24, 1)
            _d['qpf_night'] = _qn
            # 逐時量（近1/2/3h、無減緩、解除、再發布門檻）
            _hm = hourly_metrics(hourly_ser, hourly_meta,
                                 [_d.get('station'), _stn_key2(_d.get('station') or '')])
            _d.update({k: _hm[k] for k in
                       ('r1h', 'r2h', 'r3h', 'no_abate', 'rel_2stage', 'rel_1stage',
                        'reissue_th1', 'reissue_th2')})
            _d['hourly_reason'] = _hm['reason']
            _d['hourly_station'] = _hm['station']
            # 動態調降（雨場期間以調整後值為研判基準）
            _adj, _lvl, _dmm = apply_dynamic_adj(_d.get('alert'), _hm['r3h'], _hm['r2h'])
            _d['alert_adj'] = _adj if _lvl else _d.get('alert')
            _d['adj_level'] = _lvl
            _d['adj_mm'] = _dmm
            if _lvl: _n_adj += 1
            # 推估以調整後警戒值為基準（官方語意：雨場結束前皆用調整後值）
            _est = slope_est(_d.get('etr2'), _d['alert_adj'], _q24, _qn)
            _d.update({k: _est[k] for k in
                       ('fc_etr2', 'fc_pct', 'est_yellow_now', 'est_red_fc',
                        'yellow_th', 'reached_th', 'night_warn')})
            # 官方現況（權威）
            _o = _off_d.get(_no)
            _d['off_level']  = _o['level'] if _o else (None if not _off_ok else '')
            _d['off_report'] = _o['report'] if _o else ''
            _d['off_updated'] = _o['updated'] if _o else ''
            # 相容舊前端欄位：red/yellow 改為「官方現況」語意
            _d['red']    = (_d['off_level'] == 'r') if _off_ok else None
            _d['yellow'] = (_d['off_level'] == 'y') if _off_ok else None
            if _est['est_yellow_now']: _n_est_y += 1
            if _est['est_red_fc']:     _n_est_r += 1
            if _est['night_warn']:     _n_night += 1
        _or = sum(1 for _d in debris_alerts.values() if _d.get('off_level') == 'r')
        _oy = sum(1 for _d in debris_alerts.values() if _d.get('off_level') == 'y')
        print(f"  土石流｜官方現況 紅{_or}/黃{_oy} 條"
              f"（{'官方值' if _off_ok else '★官方取用失敗，現況為 None'}）")
        print(f"        ｜推估 符合黃警發布標準 {_n_est_y} 條、未來24h可能達紅 {_n_est_r} 條、"
              f"入夜前示警 {_n_night} 條、動態調降 {_n_adj} 條")

    # ── 大規模崩塌（逐警戒區）────────────────────────
    #   警戒值優先用官方 API（會自動含新增潛勢區），缺才退回靜態明細表。
    landslide_alerts = {}
    if ls_warn or ls_alert_vals:
        _claimed = set()
        _rows = []
        for _z in ls_warn:
            for _i in _z.get('ids', []):
                _rows.append((_i, _z)); _claimed.add(_i)
        # 未顯式標編號的列：以 (縣市,鄉鎮,村里) 認領官方清單中尚未被認領的潛勢區
        _by_loc = {}
        for _no, _v in ls_alert_vals.items():
            _by_loc.setdefault((_v['county'], _v['town']), []).append(_no)
        for _z in ls_warn:
            if _z.get('ids'): continue
            _cand = [n for n in _by_loc.get((_z['county'], _z['town']), []) if n not in _claimed]
            for _no in _cand[:_z.get('n_zones', 1)]:
                _rows.append((_no, _z)); _claimed.add(_no)
        # 官方清單有、但明細表沒對到的（例如新增 6 處）→ 仍納入，代表站留空
        for _no in ls_alert_vals:
            if _no not in _claimed: _rows.append((_no, None))

        for _no, _z in _rows:
            _v = ls_alert_vals.get(_no) or {}
            _alert = _v.get('alert') or (_z.get('alert') if _z else None)
            _cty = _v.get('county') or (_z.get('county') if _z else '')
            _twn = _v.get('town')   or (_z.get('town')   if _z else '')
            # ETR2：官方代表站優先，依序 精確→正規化→同鄉鎮相似→同縣市相似，
            #        全不中才退回鄉鎮 ETR2（etr2_src 標明來源層級，前端可辨識可信度）
            _etr, _src, _stn = None, '', ''
            if _z:
                _etr, _tier, _stn = resolve_station_etr2(
                    [_z.get('station1'), _z.get('station2'),
                     _z.get('station1_norm'), _z.get('station2_norm')],
                    swcb_etr2, _cty, _twn)
                if _etr is not None: _src = _tier
            if _etr is None:
                _t = next((t for t in out_towns
                           if t['county'] == _cty and t['township'] == _twn), None)
                if _t and _t.get('etr2') is not None:
                    _etr, _src, _stn = _t['etr2'], 'town', f"{_cty}{_twn}(鄉鎮值)"
            _key = _cty + _twn
            _q24, _qn = _tq.get(_key), _tqn.get(_key)
            _hm = hourly_metrics(hourly_ser, hourly_meta,
                                 [_stn, _stn_key2(_stn or '')] +
                                 ([_z.get('station1'), _z.get('station2')] if _z else []))
            _adj, _lvl, _dmm = apply_dynamic_adj(_alert, _hm['r3h'], _hm['r2h'])
            _alert_adj = _adj if _lvl else _alert
            _est = slope_est(_etr, _alert_adj, _q24, _qn)
            _o = _off_l.get(_no)
            landslide_alerts[_no] = {
                'county': _cty, 'town': _twn,
                'village': (_z.get('village') if _z else ''),
                'name': _v.get('name', ''),
                'lat': _v.get('lat'), 'lng': _v.get('lng'),
                'alert': _alert,
                'alert_adj': _alert_adj, 'adj_level': _lvl, 'adj_mm': _dmm,
                'alert_src': 'api' if _v.get('alert') else ('table' if _alert else None),
                'etr2': None if _etr is None else round(_etr, 1),
                'etr2_src': _src, 'etr2_src_name': _LS_TIER_NAME.get(_src, _src),
                'station': _stn,
                'forest_bureau': bool(_z.get('forest_bureau')) if _z else None,
                'qpf24': None if _q24 is None else round(_q24, 1),
                'qpf_night': _qn,
                'off_level':   _o['level'] if _o else (None if not _off_ok else ''),
                'off_report':  _o['report'] if _o else '',
                'off_updated': _o['updated'] if _o else '',
                'hourly_reason': _hm['reason'], 'hourly_station': _hm['station'],
                **{k: _hm[k] for k in ('r1h', 'r2h', 'r3h', 'no_abate',
                                       'rel_2stage', 'rel_1stage',
                                       'reissue_th1', 'reissue_th2')},
                **{k: _est[k] for k in ('pct', 'fc_etr2', 'fc_pct', 'est_yellow_now',
                                        'est_red_fc', 'yellow_th', 'reached_th', 'night_warn')},
            }
        _lr = sum(1 for v in landslide_alerts.values() if v['off_level'] == 'r')
        _ly = sum(1 for v in landslide_alerts.values() if v['off_level'] == 'y')
        _tiers = {}
        for v in landslide_alerts.values():
            _tiers[v['etr2_src'] or 'none'] = _tiers.get(v['etr2_src'] or 'none', 0) + 1
        print(f"  大規模崩塌｜{len(landslide_alerts)} 處警戒區"
              f"（警戒值來源 API {sum(1 for v in landslide_alerts.values() if v['alert_src']=='api')}"
              f"／靜態表 {sum(1 for v in landslide_alerts.values() if v['alert_src']=='table')}）")
        print(f"        ｜官方現況 紅{_lr}/黃{_ly} 處")
        print(f"        ｜ETR2 來源：" + "、".join(
            f"{_LS_TIER_NAME.get(k, k)} {n}處" for k, n in sorted(_tiers.items(), key=lambda x: -x[1])))
        print(f"        ｜推估 符合黃警發布標準 "
              f"{sum(1 for v in landslide_alerts.values() if v['est_yellow_now'])} 處、"
              f"未來24h可能達紅 {sum(1 for v in landslide_alerts.values() if v['est_red_fc'])} 處")

    # ── 雨勢較大地區（縣市級，日累積≥150mm）──────────
    #   颱風面板用；區分「觀測」與「預測」，不混為一談。
    heavy_counties = {}
    for _t in out_towns:
        _c = _t['county']
        # daily_rain[0] ＝今天（見 get_daily_rain_array 註解），非 [-1]
        _dr = _t.get('daily_rain') or []
        # daily_qpf[i] ＝自 base_time 起第 i 個滾動24h 區塊（非日曆日，故以窗序標示）
        _dq = _t.get('daily_qpf') or []
        _obs_today = _dr[0] if _dr else None
        _cur = heavy_counties.setdefault(_c, {'obs_max': None, 'obs_town': '',
                                              'fc_max': None, 'fc_town': '', 'fc_win': None})
        if _obs_today is not None and (_cur['obs_max'] is None or _obs_today > _cur['obs_max']):
            _cur['obs_max'], _cur['obs_town'] = round(_obs_today, 1), _t['township']
        for _i, _v in enumerate(_dq[:3]):
            if _v is None: continue
            if _cur['fc_max'] is None or _v > _cur['fc_max']:
                _cur['fc_max'], _cur['fc_town'] = round(_v, 1), _t['township']
                _cur['fc_win'] = _i          # 0＝未來24h、1＝24–48h、2＝48–72h
    heavy_rain_counties = {c: d for c, d in heavy_counties.items()
                           if (d['obs_max'] or 0) >= HEAVY_RAIN_COUNTY_TH
                           or (d['fc_max'] or 0) >= HEAVY_RAIN_COUNTY_TH}
    print(f"  雨勢較大地區（日累積≥{HEAVY_RAIN_COUNTY_TH:.0f}mm）："
          f"{len(heavy_rain_counties)} 縣市 "
          f"{'、'.join(sorted(heavy_rain_counties)) if heavy_rain_counties else '（無）'}")

    output={

        'base_time':base_time_str,
        'generated_at':now_tpe.strftime('%Y-%m-%dT%H:%M:%S'),
        'source':'CWA_OBS+POP' if stations else 'DEMO',
        'cwa_qpf_active': bool(is_typhoon or routine_seg_map),  # True=前48h已覆蓋CWA官方QPF
        'radar_qpf_time': radar_dt,   # F-B0046 未來1h雷達QPF 發布時間（空=本次未取得）
        'typhoon_track': typhoon_track,  # W-C0034-005 颱風過去軌跡＋預報路徑（無颱風＝[]）
        'typhoon_warn': typhoon_warn,    # W-C0034-001 官方警報單原文（無警報＝[]）
        # 雙軌警戒：off_* ＝官方發布（權威）、est_* ＝系統推估（前端須標示）
        'debris_alerts': debris_alerts,        # 土石流逐潛勢溪流
        'landslide_alerts': landslide_alerts,  # 大規模崩塌逐警戒區
        'official_alert_meta': {              # 官方警戒取用狀態（前端據此決定是否顯示「官方」字樣）
            'ok': official_alerts.get('ok', False),
            'report_id': official_alerts.get('report_id', ''),
            'updated': official_alerts.get('updated', ''),
            'src': SWCB_ALERT_URL,
        },
        'heavy_rain_counties': heavy_rain_counties,  # 雨勢較大地區（縣市級，日累積≥150mm）
        'heavy_rain_th': HEAVY_RAIN_COUNTY_TH,
        # 逐時序列暖機狀態：前端據此把未達長度的判定顯示為「資料不足」而非「未達標」
        'hourly_meta': hourly_meta,
        # 模式：typhoon(精確格點) / typhoon+routine_png(颱風段精確+其餘段圖判讀) /
        #       routine(格點) / routine_png(全圖判讀近似)
        'cwa_qpf_mode': (
            ('typhoon+routine_png' if (routine_seg_map and routine_is_png) else 'typhoon')
            if is_typhoon else
            ('routine_png' if routine_is_png else 'routine') if routine_seg_map else None),
        'cwa_qpf_segs': sorted(routine_seg_map.keys()) if routine_seg_map else [],
        'cwa_qpf_windows': (routine_qpf.get('windows') if routine_qpf else []) or [],
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
