#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QPESUMS 觀測雨量網格 探測腳本 —— 在能連 CWA 的環境執行
目的：O-A0038-001 現在回傳「溫度分布圖」JPG（錯的），找出正確的
      QPESUMS 1小時觀測雨量網格 dataid。

用法：
    export CWA_API_KEY="你的金鑰"
    python probe_qpesums.py > qpesums_probe.log 2>&1
把 qpesums_probe.log 貼回對話。
"""
import os, json, requests, re

KEY = os.environ.get("CWA_API_KEY", "")
if not KEY:
    print("請先設定 CWA_API_KEY"); raise SystemExit(1)

FILEAPI = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi"
DATASTORE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"

def desc_of(doc):
    """找 datasetDescription / ResourceDesc。"""
    out = []
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ('datasetDescription','DatasetDescription',
                         'ResourceDesc','ContentDescription') and isinstance(v, str):
                    out.append(f"{k}={v}")
                else: walk(v)
        elif isinstance(o, list):
            for it in o: walk(it)
    walk(doc)
    return " | ".join(out[:6])

def has_grid(doc):
    """判斷內容是否含大量格點數值（科學記號）。"""
    s = json.dumps(doc, ensure_ascii=False)
    nums = re.findall(r'-?\d+\.?\d*[Ee][+-]?\d+', s)
    return len(nums)

# ── A. 掃 O-A0038 系列（QPESUMS 觀測整合）──
print("="*70)
print("A. O-A0038 系列（QPESUMS 觀測整合網格）")
print("="*70)
for i in range(1, 12):
    did = f"O-A0038-{i:03d}"
    try:
        r = requests.get(f"{FILEAPI}/{did}",
                         params={'Authorization': KEY, 'downloadType':'WEB', 'format':'JSON'},
                         timeout=30)
        if r.status_code != 200:
            print(f"  {did}: HTTP {r.status_code}"); continue
        head = r.content[:8]
        if head[:2] == b'PK' or head[:3] == b'\xff\xd8\xff':
            print(f"  {did}: 二進位（zip/jpg），{len(r.content)}bytes"); continue
        try:
            doc = json.loads(r.content.decode('utf-8','replace'))
        except Exception:
            print(f"  {did}: 非JSON"); continue
        ng = has_grid(doc)
        print(f"  {did}: {desc_of(doc)}")
        print(f"         格點數值數={ng} {'★可能是網格數據' if ng>1000 else ''}")
        # 印 ProductURL 若有
        s = json.dumps(doc, ensure_ascii=False)
        for m in re.findall(r'https?://[^\s"]+\.(?:jpg|png|zip|gz|txt)', s)[:2]:
            print(f"         URL: {m[:90]}")
    except Exception as e:
        print(f"  {did}: 例外 {e}")

# ── B. 其他可能的 QPESUMS 雨量 dataid ──
print("\n" + "="*70)
print("B. 其他候選（QPESUMS 定量降雨相關）")
print("="*70)
cands = [
    "O-A0040-001",  # 可能的雨量網格
    "O-A0058-001",  # QPESUMS 系列
    "O-A0059-001",
    "O-A0060-001",
    "F-B0046-001",  # 未來1h雷達QPF（對照，已知可用）
]
for did in cands:
    try:
        r = requests.get(f"{FILEAPI}/{did}",
                         params={'Authorization': KEY, 'downloadType':'WEB', 'format':'JSON'},
                         timeout=30)
        if r.status_code != 200:
            print(f"  {did}: HTTP {r.status_code}"); continue
        head = r.content[:8]
        if head[:2] == b'PK' or head[:3] == b'\xff\xd8\xff':
            print(f"  {did}: 二進位，{len(r.content)}bytes"); continue
        doc = json.loads(r.content.decode('utf-8','replace'))
        ng = has_grid(doc)
        print(f"  {did}: {desc_of(doc)}")
        print(f"         格點數值數={ng} {'★網格數據' if ng>1000 else ''}")
    except Exception as e:
        print(f"  {did}: 例外 {e}")

print("\n" + "="*70)
print("C. 深入檢查 O-A0038-003 與 O-A0059-001（判斷是雨量還是溫度/回波）")
print("="*70)
for did in ["O-A0038-003", "O-A0059-001"]:
    try:
        r = requests.get(f"{FILEAPI}/{did}",
                         params={'Authorization': KEY, 'downloadType':'WEB', 'format':'JSON'},
                         timeout=40)
        if r.status_code != 200:
            print(f"  {did}: HTTP {r.status_code}"); continue
        doc = json.loads(r.content.decode('utf-8','replace'))
        print(f"\n  ── {did} ──")
        # 印完整頂層結構
        root = doc.get('cwaopendata', doc)
        ds = root.get('dataset') or root.get('Dataset') or {}
        # datasetInfo / parameterSet
        info = ds.get('datasetInfo') or ds.get('DatasetInfo') or {}
        print(f"    datasetInfo keys: {list(info.keys())}")
        print(f"    描述: {info.get('datasetDescription') or info.get('DatasetDescription') or '(無)'}")
        ps = info.get('parameterSet') or info.get('ParameterSet') or {}
        if ps:
            print(f"    parameterSet: {json.dumps(ps, ensure_ascii=False)[:300]}")
        # GeoInfo（舊式）
        geo = ds.get('GeoInfo') or {}
        if geo:
            print(f"    GeoInfo: {json.dumps(geo, ensure_ascii=False)[:300]}")
        res = ds.get('Resource') or {}
        if res:
            print(f"    Resource: {json.dumps(res, ensure_ascii=False)[:200]}")
        # 抓格點數值，看值域（判斷單位）
        s = json.dumps(doc, ensure_ascii=False)
        nums = [float(x) for x in re.findall(r'-?\d+\.?\d*[Ee][+-]?\d+', s)]
        if not nums:
            nums = [float(x) for x in re.findall(r'-?\d+\.\d+', s)][:5000]
        if nums:
            valid = [n for n in nums if n > -90]   # 排除 -99 無效值
            if valid:
                import statistics
                print(f"    數值統計：共{len(nums)}個，有效{len(valid)}個")
                print(f"      範圍 {min(valid):.1f} ~ {max(valid):.1f}，中位數 {statistics.median(valid):.1f}")
                print(f"      判斷：", end='')
                mx = max(valid)
                if mx <= 45: print("值域像『溫度℃』(0~45)")
                elif mx <= 75: print("值域像『雷達回波 dBZ』(0~75)")
                else: print("值域較大，可能是『雨量 mm』或其他")
    except Exception as e:
        print(f"  {did}: 例外 {e}")

print("\n" + "="*70)
print("探測完成。重點看：")
print("  哪個 dataid 的『描述』含『定量降水估計/觀測雨量』且『格點數值數 > 1000』")
print("  → 那個才是正確的 QPESUMS 觀測雨量網格，用它取代 O-A0038-001")
print("="*70)
