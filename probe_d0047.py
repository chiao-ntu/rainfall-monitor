#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F-D0047 鄉鎮天氣預報 詳細探測腳本
────────────────────────────────────────────────
目的：確認 F-D0047-089（未來3天）與 F-D0047-091（未來1週）的
      WeatherElement 內容，找出降水量欄位與時間解析度，
      判斷哪一支適合用來計算「未來 6/12 小時 QPF 加總」。

執行方式：
    export CWA_KEY="你的授權碼"
    python3 probe_d0047.py > d0047_probe.log 2>&1

完成後把 d0047_probe.log 整份貼回對話。
"""
import os
import sys
import json
import requests

KEY = os.getenv("CWA_KEY", "")
if not KEY:
    print("[ERROR] 請先設定環境變數 CWA_KEY")
    sys.exit(1)

DATASTORE = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
SEP = "=" * 72


def fetch(did):
    r = requests.get(
        f"{DATASTORE}/{did}",
        params={"Authorization": KEY, "format": "JSON"},
        timeout=45,
    )
    if r.status_code != 200:
        print(f"  {did}: HTTP {r.status_code}")
        return None
    return json.loads(r.content.decode("utf-8", "replace"))


def probe_dataset(did, label):
    print("\n" + SEP)
    print(f"{label}（{did}）")
    print(SEP)

    doc = fetch(did)
    if not doc:
        return

    recs = doc.get("records", {})
    locs_wrap = recs.get("Locations", [])
    if not locs_wrap:
        print("  找不到 Locations")
        return

    wrap = locs_wrap[0]
    print(f"  資料集描述：{wrap.get('DatasetDescription')}")

    locations = wrap.get("Location", [])
    print(f"  縣市數：{len(locations)}")

    if not locations:
        return

    # ── 取第一個縣市，看它有幾個鄉鎮 ──
    first_county = locations[0]
    print(f"\n  第一個縣市：{first_county.get('LocationName')} "
          f"(Geocode={first_county.get('Geocode')})")

    # 注意：D0047 的結構可能是 Location[] 直接是鄉鎮，
    # 也可能縣市下還有一層。先確認 WeatherElement 在哪一層。
    we_list = first_county.get("WeatherElement", [])
    print(f"  WeatherElement 數量：{len(we_list)}")

    # ── 列出所有 WeatherElement 名稱 ──
    print("\n  ── 所有 WeatherElement ──")
    for we in we_list:
        name = we.get("ElementName", "?")
        times = we.get("Time", [])
        print(f"    ElementName = {name!r}   (Time 筆數={len(times)})")

    # ── 找降水相關的 element，展開細節 ──
    RAIN_KEYWORDS = ["降水", "雨", "Rain", "Precipitation", "PoP", "機率"]
    print("\n  ── 降水相關 Element 詳細 ──")
    found_any = False
    for we in we_list:
        name = we.get("ElementName", "")
        if not any(k in name for k in RAIN_KEYWORDS):
            continue
        found_any = True
        times = we.get("Time", [])
        print(f"\n    【{name}】Time 筆數={len(times)}")
        if not times:
            continue

        # 看前 4 筆時間，確認解析度
        print("      前 4 筆時間區間：")
        for t in times[:4]:
            st = t.get("StartTime") or t.get("DataTime") or "?"
            et = t.get("EndTime", "")
            # 值可能在 ElementValue 這個 list 裡
            ev = t.get("ElementValue", [])
            if isinstance(ev, list) and ev:
                val_repr = json.dumps(ev[0], ensure_ascii=False)
            else:
                val_repr = json.dumps(ev, ensure_ascii=False)[:120]
            if et:
                print(f"        {st} → {et}")
            else:
                print(f"        {st}")
            print(f"          值：{val_repr}")

        # 完整印出第一筆的結構
        print(f"      第一筆完整結構：")
        print(f"        {json.dumps(times[0], ensure_ascii=False, indent=10)[:600]}")

    if not found_any:
        print("    ⚠ 沒找到降水相關 Element，以下印出第一個 Element 的完整結構供判斷：")
        if we_list:
            we0 = we_list[0]
            print(f"      ElementName={we0.get('ElementName')}")
            t0 = we0.get("Time", [])
            if t0:
                print(f"      {json.dumps(t0[0], ensure_ascii=False, indent=8)[:800]}")

    # ── 確認鄉鎮層級在哪裡 ──
    print("\n  ── 行政區層級確認 ──")
    print(f"    Location[] 長度 = {len(locations)}")
    print(f"    前 5 個 LocationName：")
    for loc in locations[:5]:
        print(f"      {loc.get('LocationName')}  Geocode={loc.get('Geocode')}")

    # 如果 Location 只有 22 個（縣市數），代表鄉鎮在更深一層
    if len(locations) <= 25:
        print("\n    → Location[] 只有縣市層級，檢查是否有更深的鄉鎮層…")
        # 印出第一個縣市的所有 key
        print(f"      第一個縣市的 keys：{list(first_county.keys())}")


# ══════════════════════════════════════════════════
probe_dataset("F-D0047-089", "鄉鎮天氣預報 未來3天")
probe_dataset("F-D0047-091", "鄉鎮天氣預報 未來1週")

# ── 額外掃描 D0047 系列其他編號，找更細解析度的 ──
print("\n" + SEP)
print("額外：掃描 F-D0047 系列其他編號")
print(SEP)
for i in [1, 3, 5, 7, 9, 33, 61, 63, 65, 67, 69, 71, 73, 75, 77, 79, 81, 83, 85, 87, 90, 92, 93]:
    did = f"F-D0047-{i:03d}"
    try:
        r = requests.get(
            f"{DATASTORE}/{did}",
            params={"Authorization": KEY, "format": "JSON"},
            timeout=20,
        )
        if r.status_code != 200:
            continue
        doc = json.loads(r.content.decode("utf-8", "replace"))
        recs = doc.get("records", {})
        lw = recs.get("Locations", [])
        if lw:
            desc = lw[0].get("DatasetDescription", "?")
            n_loc = len(lw[0].get("Location", []))
            print(f"  {did}: {desc}  (Location數={n_loc})")
    except Exception:
        pass

print("\n" + SEP)
print("重點確認：")
print("""
  1. 降水量的 ElementName 實際叫什麼？（可能是「降雨量」「12小時降雨量」等）
  2. Time 的區間是逐 3 小時、6 小時還是 12 小時？
  3. ElementValue 裡降水量的欄位名稱與單位為何？
  4. Location[] 是縣市層級（22個）還是鄉鎮層級（368個）？
     若只有 22 個，鄉鎮資料在哪一層？
  5. 掃描結果中，有沒有解析度更細（逐3小時）的 D0047 資料集？
""")
print("探測結束。請將本 log 完整貼回對話。")
