#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
颱風路徑資料探測 v2 —— 修正欄位名（TropicalCyclones 大寫開頭）
用法：
    export CWA_API_KEY="你的金鑰"
    python probe_typhoon2.py > typhoon_probe2.log 2>&1
"""
import os, json, requests

KEY = os.environ.get("CWA_API_KEY", "")
if not KEY:
    print("請先設定 CWA_API_KEY"); raise SystemExit(1)

DATASTORE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"

print("=" * 70)
print("W-C0034-005 颱風路徑（過去/現在/預報）完整結構")
print("=" * 70)
try:
    r = requests.get(f"{DATASTORE}/W-C0034-005",
                     params={'Authorization': KEY, 'format': 'JSON'}, timeout=40)
    print(f"HTTP {r.status_code}, {len(r.content)} bytes")
    doc = json.loads(r.content.decode('utf-8', 'replace'))
    rec = doc.get('records', {})
    print(f"records keys: {list(rec.keys())}")

    tcs = rec.get('TropicalCyclones') or rec.get('tropicalCyclones') or {}
    if isinstance(tcs, dict):
        print(f"TropicalCyclones keys: {list(tcs.keys())}")
        lst = tcs.get('tropicalCyclone') or tcs.get('TropicalCyclone') or []
    else:
        lst = tcs
    if isinstance(lst, dict):
        lst = [lst]

    print(f"\n★活動中熱帶氣旋數: {len(lst)}")

    for i, ty in enumerate(lst[:2]):
        print("\n" + "─" * 60)
        print(f"颱風 #{i+1} 頂層欄位: {list(ty.keys())}")
        for k in ('year', 'typhoonName', 'cwaTyphoonName', 'cwaTdNo', 'typhoonNo'):
            if k in ty:
                print(f"  {k} = {ty[k]}")

        ana = ty.get('analysisData') or {}
        fixes = ana.get('fix') or []
        if isinstance(fixes, dict):
            fixes = [fixes]
        print(f"\n  【analysisData】過去/現在定位 {len(fixes)} 筆")
        if fixes:
            print(f"    單筆欄位: {list(fixes[0].keys())}")
            print("    最新一筆:")
            print(json.dumps(fixes[-1], ensure_ascii=False, indent=6)[:900])

        fc = ty.get('forecastData') or {}
        fcs = fc.get('fix') or []
        if isinstance(fcs, dict):
            fcs = [fcs]
        print(f"\n  【forecastData】預報路徑 {len(fcs)} 筆")
        if fcs:
            print(f"    單筆欄位: {list(fcs[0].keys())}")
            print("    第1筆:")
            print(json.dumps(fcs[0], ensure_ascii=False, indent=6)[:900])

    if not lst:
        print("目前無活動中熱帶氣旋。原始結構前 1500 字：")
        print(json.dumps(rec, ensure_ascii=False)[:1500])

except Exception:
    import traceback
    traceback.print_exc()

print("\n" + "=" * 70)
print("重點：analysisData=過去軌跡、forecastData=預測路徑")
print("      需確認 座標欄位名 / 暴風圈半徑 / 時間格式")
print("=" * 70)
