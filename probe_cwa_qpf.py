#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CWA QPF 資料源探測腳本 —— 請在「能連到 opendata.cwa.gov.tw 的環境」執行
（GitHub Actions 或你的本機都可，沙盒連不到 CWA 所以無法在對話中實測）

目的：找出 CWA 定量降水預報有哪些「可接的數據/圖」，供對接使用。
優先順序：網格數據 > 6h圖 > 3h圖 > 12h圖。

用法：
    export CWA_API_KEY="你的金鑰"
    python probe_cwa_qpf.py > cwa_probe.log 2>&1
然後把 cwa_probe.log 貼回來。
"""
import os, json, requests, struct

KEY = os.environ.get("CWA_API_KEY", "")
if not KEY:
    print("請先設定 CWA_API_KEY 環境變數"); raise SystemExit(1)

FILEAPI  = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi"
DATASTORE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"

def walk_uris(obj, out):
    """遞迴找出 JSON 裡所有的 uri / URL 欄位。"""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and ('http' in v.lower()) and \
               any(x in v.lower() for x in ['.png', '.zip', '.csv', '.json', '.grib', '.nc', '.gz']):
                out.append((k, v))
            else:
                walk_uris(v, out)
    elif isinstance(obj, list):
        for it in obj:
            walk_uris(it, out)

# ── A. 定量降水預報「網格數據」候選（最優先，數值格點）──
#   F-C0041-002 常見為定量降水預報網格；也掃描鄰近編號以防改版
print("="*70)
print("A. 定量降水預報 網格數據候選（fileapi）")
print("="*70)
grid_ids = [f'F-C0041-{i:03d}' for i in range(1, 17)]
for did in grid_ids:
    try:
        r = requests.get(f"{FILEAPI}/{did}",
                         params={'Authorization': KEY, 'downloadType': 'WEB', 'format': 'JSON'},
                         timeout=20)
        if r.status_code != 200:
            continue
        body = r.content
        head = body[:16]
        kind = ('ZIP' if head[:2]==b'PK' else
                'PNG' if head[:8]==b'\x89PNG\r\n\x1a\n' else
                'GZIP' if head[:2]==b'\x1f\x8b' else
                'JSON' if head[:1] in (b'{', b'[') else '其他')
        print(f"\n  {did}: HTTP200, {len(body)}bytes, 型別={kind}")
        if kind == 'JSON':
            try:
                doc = json.loads(body.decode('utf-8', 'replace'))
                # 印出頂層結構 + 找 uri
                if isinstance(doc, dict):
                    print(f"    頂層keys: {list(doc.keys())[:12]}")
                uris = []; walk_uris(doc, uris)
                for k, u in uris[:8]:
                    print(f"    [{k}] {u[:130]}")
                # 若有 dataset 結構，印一段內容樣本
                s = json.dumps(doc, ensure_ascii=False)[:500]
                print(f"    內容樣本: {s}")
            except Exception as e:
                print(f"    JSON 解析失敗: {e}")
        elif kind in ('ZIP', 'GZIP'):
            print(f"    → 壓縮檔（可能是網格數據），開頭bytes={head!r}")
    except Exception as e:
        print(f"  {did}: 例外 {e}")

# ── B. datastore API 版的 QPF（結構化 JSON，最理想）──
print("\n" + "="*70)
print("B. datastore 結構化 QPF（若存在，這是最好接的）")
print("="*70)
# 常見鄉鎮/網格預報 dataid（依 CWA 開放資料平台，可能需調整）
datastore_ids = ['F-C0041-001', 'F-D0047-091', 'F-B0045-001']
for did in datastore_ids:
    try:
        r = requests.get(f"{DATASTORE}/{did}",
                         params={'Authorization': KEY, 'format': 'JSON'},
                         timeout=20)
        print(f"\n  {did}: HTTP{r.status_code}, {len(r.content)}bytes")
        if r.status_code == 200:
            doc = json.loads(r.content.decode('utf-8', 'replace'))
            print(f"    success={doc.get('success')}")
            rec = doc.get('records', {})
            if isinstance(rec, dict):
                print(f"    records keys: {list(rec.keys())[:12]}")
            print(f"    樣本: {json.dumps(doc, ensure_ascii=False)[:400]}")
    except Exception as e:
        print(f"  {did}: 例外 {e}")

# ── C. 所有 PNG 圖候選 + 尺寸 + 時段窗（找 6h / 3h 圖）──
print("\n" + "="*70)
print("C. 定量降水預報 PNG 圖候選（找 6h / 3h / 12h 各版）")
print("="*70)
scan = [f'F-C0035-{i:03d}' for i in range(1, 31)] + \
       [f'F-C0041-{i:03d}' for i in range(1, 17)]
png_all = []
for did in scan:
    try:
        r = requests.get(f"{FILEAPI}/{did}",
                         params={'Authorization': KEY, 'downloadType': 'WEB', 'format': 'JSON'},
                         timeout=15)
        if r.status_code != 200: continue
        if r.content[:2] == b'PK': continue
        try:
            doc = json.loads(r.content.decode('utf-8', 'replace'))
        except Exception:
            continue
        uris = []; walk_uris(doc, uris)
        for k, u in uris:
            if '.png' in u.lower():
                png_all.append((did, u))
    except Exception:
        pass

print(f"共找到 {len(png_all)} 個 PNG uri：")
import re
for did, u in png_all:
    fn = u.rsplit('/', 1)[-1]
    m = re.search(r'_(\d{1,3})_(\d{1,3})', fn)
    win = f"{m.group(1)}-{m.group(2)}h" if m else "無窗標示"
    # 下載讀 IHDR 尺寸
    try:
        rr = requests.get(u, timeout=30)
        if rr.content[:8] == b'\x89PNG\r\n\x1a\n':
            w, h = struct.unpack('>II', rr.content[16:24])
            print(f"  {did}: {fn[:50]}  [{win}]  {w}×{h}")
        else:
            print(f"  {did}: {fn[:50]}  [{win}]  (非PNG)")
    except Exception as e:
        print(f"  {did}: {fn[:50]}  [{win}]  下載失敗 {e}")

print("\n" + "="*70)
print("探測完成。請把以上完整輸出貼回對話。")
print("重點看：A 有沒有 JSON/壓縮的網格數據、B 有沒有 datastore QPF、")
print("        C 有沒有 6h/3h 的圖（檔名時段窗 + 尺寸）。")
print("="*70)
