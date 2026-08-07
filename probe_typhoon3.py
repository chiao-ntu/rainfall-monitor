#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
颱風路徑資料探測 v3 —— 不猜欄位名，直接遞迴印出真實結構
用法：
    export CWA_API_KEY="你的金鑰"
    python probe_typhoon3.py > typhoon_probe3.log 2>&1
"""
import os, json, requests

KEY = os.environ.get("CWA_API_KEY", "")
if not KEY:
    print("請先設定 CWA_API_KEY"); raise SystemExit(1)

r = requests.get("https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0034-005",
                 params={'Authorization': KEY, 'format': 'JSON'}, timeout=40)
print(f"HTTP {r.status_code}, {len(r.content)} bytes")
doc = json.loads(r.content.decode('utf-8', 'replace'))

tc = doc['records']['TropicalCyclones']['TropicalCyclone']
if isinstance(tc, dict):
    tc = [tc]
print(f"活動中熱帶氣旋: {len(tc)}\n")

ty = tc[0]
for k in ('Year', 'TyphoonName', 'CwaTyphoonName', 'CwaTdNo', 'CwaTyNo'):
    print(f"{k} = {ty.get(k)}")

# ── 直接看 AnalysisData / ForecastData 的真實內容 ──
for section in ('AnalysisData', 'ForecastData'):
    print("\n" + "=" * 70)
    print(f"【{section}】")
    print("=" * 70)
    node = ty.get(section)
    if node is None:
        print("  (不存在)")
        continue
    print(f"型別: {type(node).__name__}")
    if isinstance(node, dict):
        print(f"keys: {list(node.keys())}")
        for k, v in node.items():
            if isinstance(v, list):
                print(f"\n  ★ {k}: 陣列 {len(v)} 筆")
                if v:
                    print(f"    第1筆欄位: {list(v[0].keys()) if isinstance(v[0], dict) else type(v[0])}")
                    print("    第1筆完整內容:")
                    print(json.dumps(v[0], ensure_ascii=False, indent=6))
                    if len(v) > 1:
                        print("    最後1筆完整內容:")
                        print(json.dumps(v[-1], ensure_ascii=False, indent=6))
            elif isinstance(v, dict):
                print(f"\n  {k}: dict, keys={list(v.keys())}")
                print(json.dumps(v, ensure_ascii=False, indent=6)[:1200])
            else:
                print(f"  {k} = {v}")
    elif isinstance(node, list):
        print(f"陣列 {len(node)} 筆")
        if node:
            print(json.dumps(node[0], ensure_ascii=False, indent=4)[:1200])

# ── 保險：整包原始 JSON 前 3000 字 ──
print("\n" + "=" * 70)
print("原始 JSON（前 3000 字）")
print("=" * 70)
print(json.dumps(ty, ensure_ascii=False)[:3000])
