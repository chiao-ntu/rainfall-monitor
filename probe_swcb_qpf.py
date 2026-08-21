#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
水保署 246 系統 API 探測 — 尋找 QPF（未來預報雨量）
────────────────────────────────────────────────
目的：確認水保署是否提供「未來預報雨量」，用於 ETR2% 未來 6/12 小時推估。
      已知 GetDebrisRainData.ashx 只給觀測值（STRT = 目前 ETR2）。

執行方式（不需金鑰）：
    python3 probe_swcb_qpf.py > swcb_qpf_probe.log 2>&1

完成後把 swcb_qpf_probe.log 整份貼回對話。
"""
import json
import requests

SEP = "=" * 72
TIMEOUT = 45

# 已知可用的端點（先確認欄位，看有沒有預報相關欄）
KNOWN = [
    ("https://246.ardswc.gov.tw/webService/GetDebrisRainData.ashx",
     "土石流參考雨量站雨量（已知：STRT=目前ETR2）"),
    ("https://246.ardswc.gov.tw/WebService/GetLSCountyTownAlertValueList.ashx",
     "縣市鄉鎮警戒值清單"),
    ("https://246.ardswc.gov.tw/webService/GetIDisasterInfo.ashx",
     "應變小組開設資訊"),
    ("https://ls.ardswc.gov.tw/api/LandslideAlertOpenData",
     "官方土石流/大崩警戒"),
]

# 推測可能存在的 QPF 相關端點（依水保署命名慣例）
GUESS = [
    "https://246.ardswc.gov.tw/webService/GetDebrisRainForecast.ashx",
    "https://246.ardswc.gov.tw/webService/GetRainForecast.ashx",
    "https://246.ardswc.gov.tw/webService/GetQPFData.ashx",
    "https://246.ardswc.gov.tw/webService/GetForecastRainData.ashx",
    "https://246.ardswc.gov.tw/webService/GetDebrisQPF.ashx",
    "https://246.ardswc.gov.tw/WebService/GetRainfallForecast.ashx",
    "https://246.ardswc.gov.tw/webService/GetPredictRainData.ashx",
    "https://246.ardswc.gov.tw/webService/GetWeatherForecast.ashx",
    "https://ls.ardswc.gov.tw/api/RainfallForecastOpenData",
    "https://ls.ardswc.gov.tw/api/QPFOpenData",
]

# 預報相關關鍵字（用來在回傳欄位中搜尋）
FORECAST_KEYS = [
    "forecast", "Forecast", "QPF", "qpf", "predict", "Predict",
    "future", "Future", "預報", "推估", "F1", "F3", "F6", "F12", "F24",
]


def show_fields(data, label):
    """列出資料的欄位，並標記可能的預報欄位"""
    if isinstance(data, list) and data:
        sample = data[0]
    elif isinstance(data, dict):
        sample = data
    else:
        print(f"    資料型別：{type(data).__name__}，無法解析欄位")
        return

    if not isinstance(sample, dict):
        print(f"    第一筆型別：{type(sample).__name__}")
        return

    keys = list(sample.keys())
    print(f"    欄位（共 {len(keys)} 個）：")
    for k in keys:
        v = sample.get(k)
        v_repr = str(v)[:50]
        # 標記可能的預報欄位
        mark = ""
        if any(fk in k for fk in FORECAST_KEYS):
            mark = "  ★ 可能是預報欄位"
        print(f"      {k} = {v_repr}{mark}")

    # 完整印出第一筆
    print(f"\n    第一筆完整內容：")
    print(f"      {json.dumps(sample, ensure_ascii=False, indent=6)[:1200]}")


def probe(url, label, is_guess=False):
    tag = "[推測]" if is_guess else "[已知]"
    try:
        r = requests.get(url, timeout=TIMEOUT)
        if r.status_code != 200:
            if not is_guess:
                print(f"\n  {tag} {label}")
                print(f"    {url}")
                print(f"    HTTP {r.status_code}")
            return False

        body = r.content
        print(f"\n  {tag} {label}")
        print(f"    {url}")
        print(f"    HTTP 200, {len(body)} bytes")

        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except Exception as e:
            print(f"    非 JSON（{e}），開頭：{body[:200]!r}")
            return False

        if isinstance(data, list):
            print(f"    型別：list，長度 = {len(data)}")
        elif isinstance(data, dict):
            print(f"    型別：dict，keys = {list(data.keys())[:15]}")

        show_fields(data, label)
        return True

    except Exception as e:
        if not is_guess:
            print(f"\n  {tag} {label}：例外 → {e}")
        return False


# ══════════════════════════════════════════════════
print(SEP)
print("A. 已知端點 — 確認有無預報欄位")
print(SEP)
for url, label in KNOWN:
    probe(url, label)

print("\n" + SEP)
print("B. 推測端點 — 尋找 QPF 專用 API")
print(SEP)
found = []
for url in GUESS:
    if probe(url, url.split("/")[-1], is_guess=True):
        found.append(url)

if not found:
    print("\n  ⚠ 所有推測端點皆無回應（404 或連線失敗）")
    print("  → 水保署可能沒有公開的 QPF API")

# ══════════════════════════════════════════════════
print("\n" + SEP)
print("C. 重點確認")
print(SEP)
print("""
  1. GetDebrisRainData.ashx 的欄位裡，有沒有標記 ★ 的預報欄位？
     （例如 F1/F3/F6/F24、Forecast、QPF 等）
  2. 有沒有任何欄位是「未來時段的雨量」而非「目前累積」？
  3. B 區有沒有任何推測端點回傳 200？
  4. 若都沒有 → 水保署不提供 QPF，需改用其他方案：
     (a) 只做「目前 ETR2%」，未來時段暫不實作
     (b) 用 CWA F-C0041 格點（僅颱風警報期間有資料，需建映射表）
     (c) 用 Open-Meteo（rainfall-monitor 現行做法，但非官方資料）
""")
print("探測結束。請將本 log 完整貼回對話。")
