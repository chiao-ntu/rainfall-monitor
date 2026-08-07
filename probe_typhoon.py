#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
颱風路徑資料探測 —— 在能連 opendata.cwa.gov.tw 的環境執行
目的：確認 CWA 有哪些颱風路徑資料可介接、格式為何（現在是否為颱風期不影響探測，
      無颱風時會顯示「目前無颱風」，但仍能看出資料集是否存在與欄位結構）。

用法：
    export CWA_API_KEY="你的金鑰"
    python probe_typhoon.py > typhoon_probe.log 2>&1
把 log 貼回對話。
"""
import os, json, requests

KEY = os.environ.get("CWA_API_KEY", "")
if not KEY:
    print("請先設定 CWA_API_KEY"); raise SystemExit(1)

DATASTORE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
FILEAPI   = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi"

def brief(o, depth=0, maxd=3):
    """摘要 JSON 結構（避免整包印出）。"""
    pad = "  " * depth
    if depth >= maxd:
        return pad + "..."
    if isinstance(o, dict):
        out = []
        for k, v in list(o.items())[:12]:
            if isinstance(v, (dict, list)):
                out.append(f"{pad}{k}:")
                out.append(brief(v, depth+1, maxd))
            else:
                s = str(v)[:70]
                out.append(f"{pad}{k} = {s}")
        return "\n".join(out)
    if isinstance(o, list):
        if not o: return pad + "(空陣列)"
        return f"{pad}[陣列 {len(o)} 筆，第1筆：]\n" + brief(o[0], depth+1, maxd)
    return pad + str(o)[:70]

# ── A. datastore：颱風消息與警報（含路徑點）──
print("="*70)
print("A. W-C0034 颱風消息與警報（datastore，含中心位置/強度/預測路徑）")
print("="*70)
for did in ["W-C0034-005", "W-C0034-002", "W-C0034-001"]:
    try:
        r = requests.get(f"{DATASTORE}/{did}",
                         params={'Authorization': KEY, 'format': 'JSON'}, timeout=40)
        print(f"\n  {did}: HTTP {r.status_code}, {len(r.content)} bytes")
        if r.status_code != 200:
            print(f"    內容前120字: {r.text[:120]}")
            continue
        doc = json.loads(r.content.decode('utf-8', 'replace'))
        print(f"    success = {doc.get('success')}")
        rec = doc.get('records', {})
        if isinstance(rec, dict):
            print(f"    records keys: {list(rec.keys())}")
            # 颱風清單
            tys = rec.get('tropicalCyclones', {}).get('tropicalCyclone') \
                  if isinstance(rec.get('tropicalCyclones'), dict) else None
            if tys is None:
                tys = rec.get('tropicalCyclone')
            if tys:
                print(f"    ★目前有 {len(tys)} 個颱風")
                print(brief(tys[0], 2, 4))
            else:
                print("    目前無颱風（但資料集存在，颱風期會有內容）")
                print(brief(rec, 2, 3))
    except Exception as e:
        print(f"  {did}: 例外 {e}")

# ── B. 颱風路徑圖（影像，可直接當外連或圖層）──
print("\n" + "="*70)
print("B. 颱風路徑相關影像/檔案（fileapi）")
print("="*70)
for did in ["W-C0034-003", "W-C0034-004", "F-C0041-001"]:
    try:
        r = requests.get(f"{FILEAPI}/{did}",
                         params={'Authorization': KEY, 'downloadType': 'WEB',
                                 'format': 'JSON'}, timeout=40)
        print(f"\n  {did}: HTTP {r.status_code}, {len(r.content)} bytes")
        if r.status_code != 200: continue
        head = r.content[:8]
        if head[:3] == b'\xff\xd8\xff' or head[:8] == b'\x89PNG\r\n\x1a\n':
            print("    → 直接回傳影像檔")
            continue
        doc = json.loads(r.content.decode('utf-8', 'replace'))
        import re
        for u in re.findall(r'https?://[^\s"]+\.(?:jpg|png|gif)', json.dumps(doc))[:3]:
            print(f"    影像URL: {u[:100]}")
        for d in re.findall(r'"(?:ResourceDesc|datasetDescription)"\s*:\s*"([^"]+)"',
                            json.dumps(doc, ensure_ascii=False))[:3]:
            print(f"    描述: {d}")
    except Exception as e:
        print(f"  {did}: 例外 {e}")

print("\n" + "="*70)
print("重點確認：")
print("  A 段 W-C0034-005 是否 success=true、有 tropicalCyclones 結構")
print("     （颱風期會含 中心經緯度/氣壓/風速/移動方向 + 預測路徑點）")
print("  B 段 是否有現成的颱風路徑圖影像URL")
print("="*70)
