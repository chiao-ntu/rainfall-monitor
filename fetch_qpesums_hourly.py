#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QPESUMS 每小時累積腳本（輕量，供 GitHub Actions 每小時執行）
抓 O-A0038-001 網格 1h 雨量 → 取各鄉鎮最近格點值 → 滾動寫入 qpesums_history.json
主腳本 fetch_rainfall.py 每 6h 讀取此歷史合成 24h 累積，補強無測站鄉鎮觀測。
"""
import os, json, requests
from datetime import datetime, timezone, timedelta

CWA_API_KEY  = os.environ.get('CWA_API_KEY', '')
BASE_URL     = "https://opendata.cwa.gov.tw/api/v1/rest/datastore"
QPESUMS_URL  = f"{BASE_URL}/O-A0038-001"
HIST_FILE    = "qpesums_history.json"
TOWNS_FILE   = "all_townships.json"
# 網格參數（與 fetch_rainfall.py 保持一致）
QP_LON0, QP_LAT0, QP_D, QP_NX, QP_NY = 118.0, 20.0, 0.0125, 441, 561
KEEP_HOURS   = 30   # 保留30小時（24h合成留餘裕）


def fetch_grid():
    r = requests.get(QPESUMS_URL, params={'Authorization': CWA_API_KEY,
                                          'downloadType': 'WEB', 'format': 'JSON'}, timeout=90)
    r.raise_for_status()
    raw = r.json()
    content = None
    try:
        content = raw['cwaopendata']['dataset']['contents']['content']
    except (KeyError, TypeError):
        pass
    if content is None:
        try:
            content = raw['records']['contents']['content']
        except (KeyError, TypeError):
            pass
    if not content:
        print(f"結構不符，頂層keys: {list(raw)[:5]}")
        return None
    vals = []
    for tok in str(content).replace('\n', ',').split(','):
        tok = tok.strip()
        if not tok:
            continue
        try:
            v = float(tok)
            vals.append(None if v < 0 else v)
        except ValueError:
            continue
    print(f"網格：{len(vals)} 值（期望 {QP_NX*QP_NY}）")
    return vals if len(vals) >= QP_NX*QP_NY*0.9 else None


def grid_at(vals, lat, lng):
    ix = round((lng - QP_LON0) / QP_D)
    iy = round((lat - QP_LAT0) / QP_D)
    if ix < 0 or ix >= QP_NX or iy < 0 or iy >= QP_NY:
        return None
    idx = iy * QP_NX + ix
    return vals[idx] if idx < len(vals) else None


def main():
    now = datetime.now(timezone.utc) + timedelta(hours=8)
    hour_key = now.strftime('%Y-%m-%dT%H')
    print(f"QPESUMS 每小時累積  {now.strftime('%Y-%m-%d %H:%M')} TST")

    if not CWA_API_KEY:
        print("無 CWA_API_KEY，跳過")
        return
    if not os.path.exists(TOWNS_FILE):
        print(f"找不到 {TOWNS_FILE}")
        return

    with open(TOWNS_FILE, encoding='utf-8') as f:
        towns = json.load(f)

    vals = fetch_grid()
    if not vals:
        print("網格抓取失敗，本次不更新")
        return

    hist = {}
    if os.path.exists(HIST_FILE):
        try:
            with open(HIST_FILE, encoding='utf-8') as f:
                hist = json.load(f)
        except Exception:
            hist = {}

    cutoff = (now - timedelta(hours=KEEP_HOURS)).strftime('%Y-%m-%dT%H')
    n_hit = 0
    for t in towns:
        lat, lng = t.get('lat'), t.get('lng')
        if not lat:
            continue
        key = f"{t['county']}{t['township']}"
        v = grid_at(vals, lat, lng)
        rec = hist.setdefault(key, {})
        rec[hour_key] = v
        # 滾動清理
        for hk in [k for k in rec if k < cutoff]:
            del rec[hk]
        if v is not None:
            n_hit += 1

    with open(HIST_FILE, 'w', encoding='utf-8') as f:
        json.dump(hist, f, ensure_ascii=False, separators=(',', ':'))
    print(f"完成：{n_hit}/{len(towns)} 鄉鎮有值 → {HIST_FILE}（{os.path.getsize(HIST_FILE)//1024}KB）")


if __name__ == '__main__':
    main()
