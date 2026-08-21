#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CWA 定量降水預報（QPF）資料探測腳本
────────────────────────────────────────────────
目的：確認 F-C0041 系列的實際回傳格式，判斷是否可直接用於
      「鄉鎮級未來 6/12 小時 QPF 加總」，或需要另建格點→鄉鎮映射。

執行方式（在能連到 opendata.cwa.gov.tw 的環境）：
    export CWA_KEY="你的授權碼"
    python3 probe_qpf.py > qpf_probe.log 2>&1

完成後把 qpf_probe.log 整份貼回對話。
"""
import os
import sys
import json
import zipfile
import io
import requests

KEY = os.getenv("CWA_KEY", "")
if not KEY:
    print("[ERROR] 請先設定環境變數 CWA_KEY")
    sys.exit(1)

FILEAPI   = "https://opendata.cwa.gov.tw/fileapi/v1/opendataapi"
DATASTORE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"

SEP = "=" * 72


def detect_kind(body: bytes) -> str:
    """從檔頭 magic bytes 判斷內容型別"""
    head = body[:16]
    if head[:2] == b"PK":
        return "ZIP"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "PNG"
    if head[:2] == b"\x1f\x8b":
        return "GZIP"
    if head[:1] in (b"{", b"["):
        return "JSON"
    if head[:5] == b"<?xml":
        return "XML"
    return f"其他（開頭={head!r}）"


def walk_keys(obj, prefix="", depth=0, max_depth=4, out=None):
    """遞迴列出 JSON 結構的 key 路徑（限制深度避免爆量）"""
    if out is None:
        out = []
    if depth > max_depth:
        return out
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else k
            if isinstance(v, (dict, list)):
                out.append(f"{path}  <{type(v).__name__}>")
                walk_keys(v, path, depth + 1, max_depth, out)
            else:
                sample = str(v)[:60]
                out.append(f"{path} = {sample}")
    elif isinstance(obj, list):
        out.append(f"{prefix}[]  長度={len(obj)}")
        if obj:
            walk_keys(obj[0], f"{prefix}[0]", depth + 1, max_depth, out)
    return out


# ══════════════════════════════════════════════════════
# A. 掃描 F-C0041 系列（fileapi）
# ══════════════════════════════════════════════════════
print(SEP)
print("A. 掃描 F-C0041 系列（定量降水預報，fileapi）")
print(SEP)

for i in range(1, 13):
    did = f"F-C0041-{i:03d}"
    try:
        r = requests.get(
            f"{FILEAPI}/{did}",
            params={"Authorization": KEY, "downloadType": "WEB", "format": "JSON"},
            timeout=30,
        )
        if r.status_code != 200:
            print(f"\n  {did}: HTTP {r.status_code}（跳過）")
            continue

        body = r.content
        kind = detect_kind(body)
        print(f"\n  {did}: HTTP 200, {len(body)} bytes, 型別={kind}")

        if kind == "JSON":
            try:
                doc = json.loads(body.decode("utf-8", "replace"))
                print("    ── 結構 ──")
                for line in walk_keys(doc)[:40]:
                    print(f"      {line}")
            except Exception as e:
                print(f"    JSON 解析失敗：{e}")

        elif kind == "ZIP":
            try:
                zf = zipfile.ZipFile(io.BytesIO(body))
                print(f"    ZIP 內含 {len(zf.namelist())} 個檔案：")
                for name in zf.namelist()[:10]:
                    info = zf.getinfo(name)
                    print(f"      {name}  ({info.file_size} bytes)")
                # 試著看第一個檔案的開頭
                first = zf.namelist()[0]
                with zf.open(first) as f:
                    preview = f.read(600)
                print(f"    第一個檔案開頭 600 bytes：")
                print(f"      {preview[:600]!r}")
            except Exception as e:
                print(f"    ZIP 解析失敗：{e}")

    except Exception as e:
        print(f"\n  {did}: 例外 → {e}")


# ══════════════════════════════════════════════════════
# B. datastore 版（結構化 JSON，最好接）
# ══════════════════════════════════════════════════════
print("\n" + SEP)
print("B. datastore 結構化 QPF / 鄉鎮預報候選")
print(SEP)

datastore_targets = [
    ("F-C0041-001", "定量降水預報"),
    ("F-D0047-089", "鄉鎮天氣預報-臺灣未來3天"),
    ("F-D0047-091", "鄉鎮天氣預報-臺灣未來1週"),
    ("F-B0045-001", "鄉鎮天氣預報"),
]

for did, name in datastore_targets:
    try:
        r = requests.get(
            f"{DATASTORE}/{did}",
            params={"Authorization": KEY, "format": "JSON"},
            timeout=30,
        )
        print(f"\n  {did} ({name}): HTTP {r.status_code}, {len(r.content)} bytes")
        if r.status_code != 200:
            continue
        doc = json.loads(r.content.decode("utf-8", "replace"))
        print(f"    success = {doc.get('success')}")
        print("    ── 結構 ──")
        for line in walk_keys(doc.get("records", {}))[:35]:
            print(f"      {line}")
    except Exception as e:
        print(f"  {did}: 例外 → {e}")


# ══════════════════════════════════════════════════════
# C. 重點確認事項
# ══════════════════════════════════════════════════════
print("\n" + SEP)
print("C. 請在 log 中確認以下重點")
print(SEP)
print("""
  1. F-C0041 系列哪一支回傳 JSON（不是 ZIP/PNG）？
  2. 該 JSON 裡有沒有「鄉鎮名稱」或「行政區代碼」欄位？
     → 有：可直接對應鄉鎮，不用建格點映射表
     → 沒有：只有經緯度格點，需要另建映射
  3. 時間欄位是否含 0-6h / 6-12h 分段？時間解析度為何？
  4. 降水量的單位與欄位名稱是什麼？
  5. 若 F-C0041 都是 ZIP，B 區的 F-D0047 系列是否可用？
     （F-D0047 是鄉鎮級，含 3 小時降雨機率與降水量）
""")
print("探測結束。請將本 log 完整貼回對話。")
