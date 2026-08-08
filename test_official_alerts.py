#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""離線驗證 fetch_official_alerts() / fetch_ls_alert_values()（依 API 文件範例結構）。"""
import json, types, os
os.environ.setdefault('CWA_API_KEY', 'dummy')
import fetch_rainfall as F

ALERTS = [
    {"AlertType": "D", "DebrisNo": "嘉縣DF077", "LandslideID": "-", "LandslideName": None,
     "County": "嘉義縣", "Town": "阿里山鄉", "Vill": "山美村",
     "AlertLevel": "y", "LastUpdateDate": "2026-08-08 00:25", "ReportID": "115E-10-0"},
    {"AlertType": "D", "DebrisNo": "高市DF053", "LandslideID": "-", "LandslideName": None,
     "County": "高雄市", "Town": "六龜區", "Vill": "中興里",
     "AlertLevel": "r", "LastUpdateDate": "2026-08-08 06:30", "ReportID": "115E-11-0"},
    {"AlertType": "L", "DebrisNo": "-", "LandslideID": "屏縣LL001", "LandslideName": "屏東縣-霧臺鄉-T001(佳暮)",
     "County": "屏東縣", "Town": "霧臺鄉", "Vill": None,
     "AlertLevel": "y", "LastUpdateDate": "2026-08-08 06:30", "ReportID": "115E-11-0"},
    # 雜訊：等級空白／編號為 '-'／非 dict → 應被安全略過
    {"AlertType": "D", "DebrisNo": "投縣DF001", "AlertLevel": "", "County": "南投縣", "Town": "信義鄉"},
    {"AlertType": "L", "LandslideID": "-", "AlertLevel": "r", "County": "南投縣", "Town": "仁愛鄉"},
    "壞資料",
]

LSVALS = [
    {"County": "宜蘭縣", "Town": "大同鄉", "LSNo": "宜縣LL001",
     "Name": "宜蘭縣-大同鄉-T002(蘭台)", "AlertValue": 800,
     "Lng": 121.5211107, "Lat": 24.53138419},
    {"County": "高雄市", "Town": "六龜區", "LSNo": "高市LL011",
     "Name": "高雄市-六龜區-T003(新發)", "AlertValue": 250,
     "Lng": 120.65, "Lat": 23.05},
    {"County": "x", "Town": "y", "LSNo": "", "AlertValue": 500},       # 無編號 → 略過
    {"County": "x", "Town": "y", "LSNo": "壞縣LL999", "AlertValue": 0},  # 值<=0 → 略過
    {"County": "x", "Town": "y", "LSNo": "壞縣LL998", "AlertValue": None},
]


class FakeResp:
    def __init__(self, obj, code=200):
        self.status_code = code; self.content = json.dumps(obj).encode()


def patch(obj, code=200):
    F.requests = types.SimpleNamespace(get=lambda *a, **k: FakeResp(obj, code))


print("=== fetch_official_alerts ===")
patch(ALERTS)
a = F.fetch_official_alerts()
assert a['ok'] is True
assert set(a['debris']) == {"嘉縣DF077", "高市DF053"}, a['debris'].keys()
assert set(a['landslide']) == {"屏縣LL001"}, a['landslide'].keys()
assert a['debris']['高市DF053']['level'] == 'r'
assert a['debris']['嘉縣DF077']['level'] == 'y'
assert a['landslide']['屏縣LL001']['name'] == "屏東縣-霧臺鄉-T001(佳暮)"
assert a['landslide']['屏縣LL001']['vill'] is None
assert a['updated'] == "2026-08-08 06:30", a['updated']
print("  OK 紅黃分流、雜訊略過、報別/更新時間正確")

patch({"errorMessage": "目前無警戒"})
a2 = F.fetch_official_alerts()
assert a2['ok'] is True and not a2['debris'] and not a2['landslide']
print("  OK 無警戒（errorMessage）視為成功且空")

patch([])
a3 = F.fetch_official_alerts()
assert a3['ok'] is True and not a3['debris']
print("  OK 空陣列視為成功且空")

patch({}, code=500)
a4 = F.fetch_official_alerts()
assert a4['ok'] is False, "HTTP 500 必須標記 ok=False（前端要能區分『無警戒』與『取用失敗』）"
print("  OK HTTP 失敗標記 ok=False，不會被誤讀成『無警戒』")

print("=== fetch_ls_alert_values ===")
patch(LSVALS)
v = F.fetch_ls_alert_values()
assert set(v) == {"宜縣LL001", "高市LL011"}, v.keys()
assert v['宜縣LL001']['alert'] == 800.0
assert abs(v['宜縣LL001']['lat'] - 24.53138419) < 1e-9
print("  OK 警戒值/座標正確，無編號與非正值已略過")

# 與手抄明細表交叉核對
tbl = json.load(open('landslide_warning_stations.json'))
idx = {}
for z in tbl['zones']:
    for i in z['ids']:
        idx[i] = z['alert']
for no, d in v.items():
    if no in idx:
        m = "OK " if idx[no] == d['alert'] else "!! "
        print(f"  {m}{no}: 明細表 {idx[no]} vs API {d['alert']:.0f}")
        assert idx[no] == d['alert'], f"{no} 警戒值不一致"

patch({}, code=404)
assert F.fetch_ls_alert_values() == {}, "失敗應回空 dict（呼叫端退回靜態表）"
print("  OK 失敗回空 dict")

print("\n全部通過")
