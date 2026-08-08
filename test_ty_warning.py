#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""離線驗證 fetch_typhoon_warning()：用 2026-08-08 白海豚海警第6報的真實結構。
沙盒連不到 CWA，故以 monkeypatch 注入實測 payload。"""
import json, types, os
os.environ.setdefault('CWA_API_KEY', 'dummy')
import fetch_rainfall as F

REAL = {"success": "true", "records": {"info": [{
    "language": "zh-TW", "category": "Met", "event": "颱風",
    "responseType": "Monitor", "urgency": "Future", "severity": "Moderate",
    "certainty": "Likely",
    "eventCode": {"valueName": "profile:CAP-TWP:Event:1.0", "value": "typhoon"},
    "effective": "2026-08-08T05:30:00+08:00",
    "onset":     "2026-08-08T05:30:00+08:00",
    "expires":   "2026-08-08T09:30:00+08:00",
    "senderName": "中央氣象署",
    "headline": "海上颱風警報",
    "description": {
        "section": [
            {"title": "命名與位置", "value": "中度颱風 白海豚（國際命名 DOLPHIN）8日5時的中心位置在北緯 26.8 度，東經 126.5 度，即在臺北的東北東方約 540 公里之處。"},
            {"title": "強度與半徑", "value": "中心氣壓 958 百帕，近中心最大風速每秒 38 公尺（約每小時 137 公里），相當於 13 級風，瞬間最大陣風每秒 48 公尺（約每小時 173 公里），相當於 15 級風，七級風暴風半徑 280 公里，十級風暴風半徑 90 公里。"},
            {"title": "移速與預測", "value": "以每小時8轉14公里速度，向西北西進行，預測9日5時的中心位置在北緯 27.3 度，東經 123.8 度，即在臺北的東北方約 340 公里之處。"},
            {"title": "颱風動態", "value": "根據最新資料顯示，第13號颱風過去3小時強度略為減弱，目前中心在臺北東北東方海面，向西北西移動，對臺灣北部海面及臺灣東北部海面將構成威脅。"},
            {"title": "警戒區域及事項", "value": "海上：臺灣北部海面、臺灣東北部海面航行及作業船隻應嚴加戒備。"},
            {"title": "大雨特報", "value": "颱風外圍環流影響，易有短延時強降雨…"},
            {"title": "強風特報", "value": "8日至9日颱風外圍環流影響，基隆市、臺北市…請注意。"},
            {"title": "注意事項", "value": "＊今(8日)、明(9日)兩天基隆北海岸…請避免前往海邊活動。"},
        ],
        "typhoon-info": [{
            "section": [
                {"title": "警報報數", "value": "6"},
                {"title": "警報類別", "value": "SEA"},
                {"title": "颱風編號", "value": "13"},
                {"title": "颱風資訊",
                 "analysis": {"time": "2026-08-07T21:00:00+00:00", "position": "26.80,126.50",
                              "gust": {"value": "48", "unit": "m/s"},
                              "pressure": {"value": "958", "unit": "hPa"},
                              "scale": [{"value": "中度颱風", "lang": "zh-TW"},
                                        {"value": "TYPHOON", "lang": "en"}],
                              "max_winds": {"value": "38", "unit": "m/s"},
                              "radius_of_15mps": {"value": "280", "unit": "km"}},
                 "prediction": {"time": "2026-08-08T21:00:00+00:00", "position": "27.30,123.80",
                                "gust": {"value": "45", "unit": "m/s"},
                                "pressure": {"value": "965", "unit": "hPa"},
                                "max_winds": {"value": "35", "unit": "m/s"},
                                "radius_of_15mps": {"value": "250", "unit": "km"}}},
            ],
            "typhoon_name": "DOLPHIN", "cwa_typhoon_name": "白海豚",
        }],
    },
    "web": "https://www.cwa.gov.tw/V8/C/P/Warning/FIFOWS.html",
    "parameter": [
        {"valueName": "alert_title", "value": "颱風警報"},
        {"valueName": "severity_level", "value": "海上颱風警報"},
        {"valueName": "alert_color", "value": "橙色"},
        {"valueName": "website_color", "value": "255,128,0"},
    ],
    "area": [
        {"areaDesc": "臺灣北部海面",   "polygon": "25.0,122.0 25.0,121.9"},
        {"areaDesc": "臺灣東北部海面", "polygon": "24.5,122.0 24.5,121.8"},
    ],
}]}}


class FakeResp:
    def __init__(self, obj): self.status_code = 200; self.content = json.dumps(obj).encode()


def run(payload, label):
    F.requests = types.SimpleNamespace(get=lambda *a, **k: FakeResp(payload))
    out = F.fetch_typhoon_warning()
    print(f"--- {label}: {len(out)} 筆 ---")
    return out


ok = True
w = run(REAL, "實測 payload")
assert len(w) == 1, "應解出 1 份警報單"
o = w[0]
checks = [
    ("發布時間", o['effective'], "2026-08-08T05:30:00+08:00"),
    ("headline", o['headline'], "海上颱風警報"),
    ("報數",     o['report_no'], "6"),
    ("警報類別", o['warn_kind'], "SEA"),
    ("颱風編號", o['ty_no'], "13"),
    ("中文名",   o['name_zh'], "白海豚"),
    ("英文名",   o['name_en'], "DOLPHIN"),
    ("警戒等級", o['severity_level'], "海上颱風警報"),
    ("警報色",   o['alert_color'], "橙色"),
    ("段落數",   len(o['sections']), 8),
    ("警戒區數", len(o['areas']), 2),
    ("首段標題", o['sections'][0]['title'], "命名與位置"),
]
for name, got, exp in checks:
    flag = "OK " if got == exp else "!! "
    if got != exp: ok = False
    print(f"  {flag}{name}: {got!r}" + ("" if got == exp else f"  期望 {exp!r}"))

# 「颱風資訊」是結構化節點，不該混進原文段落
assert all(s['title'] != '颱風資訊' for s in o['sections']), "結構化節點不該進 sections"
assert o['analysis'] and o['analysis']['position'] == "26.80,126.50", "analysis 未取到"
assert o['prediction'] and o['prediction']['position'] == "27.30,123.80", "prediction 未取到"
print(f"  OK analysis/prediction 已取到（280km→{o['analysis']['radius_of_15mps']['value']}km）")

# 邊界情形
assert run({"records": {}}, "無警報") == [], "無警報應回 []"
assert run({"records": {"info": {}}}, "info為dict空物件") == [], "空物件應回 []"
one = run({"records": {"info": {"headline": "陸上颱風警報", "effective": "2026-08-09T15:30:00+08:00",
                                "description": "整段純文字的情形"}}}, "info為單一dict＋純文字description")
assert len(one) == 1 and one[0]['sections'][0]['value'] == "整段純文字的情形", "純文字 fallback 失效"
assert one[0]['name_zh'] == '' and one[0]['report_no'] == '', "缺欄位應為空字串而非爆掉"
print("  OK 邊界情形（無警報／空物件／純文字／缺欄位）全部安全")

print("\n全部通過" if ok else "\n有欄位不符，請看上面 !! 標記")
