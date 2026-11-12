"""水保署 API 守門（2026-11-12）：欄位衝突規則、API 停住偵測、大崩警戒值備援表。

測試資料用真實官方檔：
  fixtures/swcb_debris_rain_20260910.json  水保署開放資料平臺匯出的土石流參考雨量站 ETR2，
      經比對與本系統 2026-09-10 12 時存檔 153/153 逐位相同 → 是停住兩個月的舊快照，
      正好拿來驗證「同一份值一直出現時要判定停住」。
  fixtures/swcb_ls_alert_20261112.json      大崩警戒值（94 處，與即時 API 完全相同）。
"""
import os, sys, json, shutil, tempfile, copy
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import fetch_rainfall as FR

fails, n = [], 0
def ok(c, m):
    global n
    n += 1
    print(('OK  ' if c else '!!  ') + m)
    if not c: fails.append(m)

RAIN = json.load(open(os.path.join(HERE, 'fixtures/swcb_debris_rain_20260910.json'), encoding='utf-8'))
LS = json.load(open(os.path.join(HERE, 'fixtures/swcb_ls_alert_20261112.json'), encoding='utf-8'))

tmp = tempfile.mkdtemp()
for f in ('town_polys.json', 'landslide_warning_stations.json'):
    shutil.copy(os.path.join(HERE, f), tmp)
os.chdir(tmp)

# ── ① 同義欄位衝突規則 ──
p = FR._row_pick
ok(p({'DebrisNO': 'A1'}, 'DebrisNO', 'DebrisNo') == 'A1', '①只有一種拼法有值 → 照用')
ok(p({'DebrisNO': 'A1', 'DebrisNo': 'A1'}, 'DebrisNO', 'DebrisNo') == 'A1', '①兩種拼法同值 → 照用')
FR.ROW_PICK_CONFLICTS.clear()
ok(p({'DebrisNO': 'A1', 'DebrisNo': 'A2'}, 'DebrisNO', 'DebrisNo') is None
   and FR.ROW_PICK_CONFLICTS, '①兩種拼法不同值 → 不採用並記錄衝突（不替官方決定哪個對）')
FR.ROW_PICK_CONFLICTS.clear()
ok(p({'County': '台東縣', 'CountyName': '臺東縣'}, 'County', 'CountyName') == '台東縣', '①台／臺視為相同')
ok(p({'AlertValue': '450', 'alertvalue': 450.0}, 'AlertValue') == '450', '①"450" 與 450.0 視為相同')
ok(p({'STRT1': 0}, 'STRT1') == 0, '①0 是有效的 ETR2 值，不可當成空白')
ok(p({'debrisno': 'X'}, 'DebrisNo') == 'X', '①欄位名大小寫不分')
c = FR._row_coord
ok(c({'Lng': '0', 'Longitude': '121.5'}, ('Lng', 'Longitude')) == 121.5, '①座標：一個是 0、另一個有值 → 用有值的')
ok(c({'Lng': '121.4', 'Longitude': '121.5'}, ('Lng', 'Longitude')) is None, '①座標：兩個都有值且不同 → 不採用')
ok(c({'Longitude': '0', 'Lng': ''}, ('Lng', 'Longitude')) is None, '①座標：都是 0 或空白 → 沒有座標')
FR.ROW_PICK_CONFLICTS.clear()

# ── ② API 停住偵測 ──
t0 = datetime(2026, 11, 12, 7, 0)
sf = 'st_test.json'
FR._SWCB_STALE_CACHE.clear()
seq = []
for m in (0, 60, 120, 149, 150, 180):
    FR._SWCB_STALE_CACHE.clear()
    seq.append(FR.swcb_rain_is_stale(RAIN, state_file=sf, now=t0 + timedelta(minutes=m))[0])
ok(seq == [False, False, False, False, True, True],
   f'②同一份值：149 分鐘內正常、滿 150 分鐘判定停住（{seq}）')
st = json.load(open(sf, encoding='utf-8'))
ok(st['stale'] and st['stale_events'] and st['stale_events'][-1]['since'] == '2026-11-12T07:00',
   '②停住事件記入狀態檔（背景紀錄，含起始時間）')
moved = copy.deepcopy(RAIN); moved[0]['STRT1'] = str(float(moved[0]['STRT1']) * 0.99)
FR._SWCB_STALE_CACHE.clear()
ok(FR.swcb_rain_is_stale(moved, state_file=sf, now=t0 + timedelta(minutes=190))[0] is False,
   '②任何一站數值變動 → 立即恢復正常')
FR._SWCB_STALE_CACHE.clear()
ok(FR.swcb_rain_is_stale(RAIN, state_file='st_other.json', now=t0 + timedelta(minutes=600))[0] is False,
   '②兩條流程各用自己的狀態檔，互不干擾（避免同時寫同一檔的 push 衝突）')

# 實際抓取函式：停住時回 {}，走既有的失敗退路
class R:
    status_code = 200
    def __init__(self, d): self.content = json.dumps(d).encode()
FR.cwa_get = lambda url, **k: R(RAIN)
FR.SWCB_STATE_FILE = sf
FR._SWCB_STALE_CACHE.clear()
FR.swcb_rain_is_stale(RAIN, state_file=sf)                 # 狀態檔記下這份資料的指紋
_st = json.load(open(sf)); _st['first_seen'] = '2026-01-01T00:00'   # 假裝它已經這樣維持很久
json.dump(_st, open(sf, 'w'))
FR._SWCB_STALE_CACHE.clear()
ok(FR.fetch_swcb_etr2() == {} and FR.fetch_debris_alerts() == {},
   '②判定停住時兩支抓取函式都回空 → 退回以氣象署觀測自算，不把舊值當即時值')
os.remove(sf); FR._SWCB_STALE_CACHE.clear()
r1 = FR.fetch_swcb_etr2()
ok(len(r1) > 1000, f'②正常時照常解析（{len(r1)} 個站鍵）')

# ── ③ 大崩警戒值備援表 ──
ls_now = copy.deepcopy(LS)
FR._swcb_json = lambda url, label, **k: copy.deepcopy(ls_now)
v1 = FR.fetch_ls_alert_values()
bk = json.load(open('ls_alert_backup.json', encoding='utf-8'))
ok(len(v1) == 94 and bk['n'] == 94 and bk['values']['桃市LL004']['alert'] == 350,
   '③API 成功 → 備援表以官方值覆寫（94 處，桃市LL004＝350）')
import io, contextlib
buf = io.StringIO()
ls_now[0]['AlertValue'] = '999'
with contextlib.redirect_stdout(buf):
    FR.fetch_ls_alert_values()
log = buf.getvalue()
ok('[官方變動]' in log and '999' in log, '③官方警戒值改變 → 寫入執行紀錄')
ok('[靜態表落後]' in log and '桃市LL004' in log, '③靜態明細表落後官方（桃市LL004 300／350）→ 寫入執行紀錄')
FR._swcb_json = lambda url, label, **k: None
v3 = FR.fetch_ls_alert_values()
ok(len(v3) == 94 and all(x.get('src') == 'backup' for x in v3.values()) and v3[ls_now[0]['LSNo']]['alert'] == 999,
   '③API 失敗 → 用最近一次成功的官方值（標示來源為備援表）')
os.remove('ls_alert_backup.json')
ok(FR.fetch_ls_alert_values() == {}, '③API 失敗且沒有備援表 → 回空，由主流程退回靜態明細表')

# 座標規則：官方新欄位名、衝突、落在別的鄉鎮
row_in = {'County': '宜蘭縣', 'Town': '大同鄉', 'LSNo': 'T1', 'Name': 't', 'AlertValue': '800',
          'Longitude': '121.5', 'Latitude': '24.55', 'Lng': '0', 'Lat': '0'}
row_conf = dict(row_in, LSNo='T2', Lng='121.6', Lat='24.6')
row_out = dict(row_in, LSNo='T3', Longitude='120.3', Latitude='22.6')
FR._swcb_json = lambda url, label, **k: [row_in, row_conf, row_out]
v4 = FR.fetch_ls_alert_values()
ok(v4['T1']['lng'] == 121.5 and v4['T1']['lat'] == 24.55, '③官方欄位名 Longitude/Latitude 讀得到（舊欄位為 0 時用新欄位）')
ok(v4['T2']['lng'] is None, '③兩種欄位名的座標不同 → 該處座標不採用')
ok(v4['T3']['lng'] is None, '③座標不在所寫鄉鎮內 → 不採用')

# ── ④ 10 分鐘流程（fetch_qpesums_hourly）用同一套規則、自己的狀態檔 ──
import fetch_qpesums_hourly as QH
QH.requests.get = lambda url, **k: R(RAIN)
FR._SWCB_STALE_CACHE.clear()
q1 = QH.fetch_swcb_hourly()
ok(len(q1) > 1000 and os.path.exists('swcb_state_qpe.json'), f'④10 分鐘流程正常解析（{len(q1)} 個鍵），狀態寫在 swcb_state_qpe.json')
_st = json.load(open('swcb_state_qpe.json')); _st['first_seen'] = '2026-01-01T00:00'
json.dump(_st, open('swcb_state_qpe.json', 'w'))
FR._SWCB_STALE_CACHE.clear()
ok(QH.fetch_swcb_hourly() == {}, '④10 分鐘流程判定停住 → 回空，etr2_now.json 不會被舊值覆寫')

shutil.rmtree(tmp, ignore_errors=True)
print(f"\n{'FAIL ' + str(len(fails)) if fails else 'ALL PASS'} / {n} 項")
sys.exit(1 if fails else 0)
