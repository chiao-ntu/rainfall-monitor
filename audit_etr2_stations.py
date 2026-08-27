#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全臺鄉鎮 ETR2 對站稽核 —— 找出「接錯站」的鄉鎮。

背景：水保署 API 中，同一個站名可能對應多個不同的測站（STID 不同），
      例如「武陵」在臺中和平（A0F010）與臺東延平（01S130）各有一站。
      若以站名為鍵，值會互相覆蓋，導致某些鄉鎮的 ETR2 取到別處的數值，
      警戒判定因而失真（實例：和平區官網 37%、系統顯示 48%）。

本腳本做三件事：
  1. 直接抓 API，用 (縣市, 鄉鎮, 站名) 精準對站，算出每個鄉鎮的 ETR2%。
  2. 與「僅用站名對站」的舊法比較，列出兩者不一致的鄉鎮 —— 即高風險清單。
  3. 輸出 audit_etr2.csv 供人工與官網逐一核對。

用法（需可連水保署，於本機執行）：
    python3 audit_etr2_stations.py
"""
import csv, io, json, os, sys
try:
    import requests
except ImportError:
    sys.exit('需要 requests：pip install requests')

API = "https://246.ardswc.gov.tw/webService/GetDebrisRainData.ashx"
SLOPE = "slope_warning_stations.json"
OUT_CSV = "audit_etr2.csv"


def fetch():
    r = requests.get(API, timeout=90)
    r.raise_for_status()
    return json.loads(r.content.decode('utf-8', 'replace'))


def main():
    if not os.path.exists(SLOPE):
        sys.exit(f'找不到 {SLOPE}')
    tw = json.load(io.open(SLOPE, encoding='utf-8'))['townships']
    rows = fetch()
    print(f'API 回傳 {len(rows)} 筆潛勢溪流')

    by_loc = {}          # (縣市,鄉鎮,站名) -> (值, STID)
    by_name = {}         # 站名 -> (值, STID)   ← 舊法：會互相覆蓋
    name_ids = {}        # 站名 -> {STID}
    for row in rows:
        cty = (row.get('County') or '').strip()
        twn = (row.get('Town') or '').strip()
        for ik, nk, vk in (('STID1', 'STName1', 'STRT1'),
                           ('STID2', 'STName2', 'STRT2')):
            nm = (row.get(nk) or '').strip()
            sid = (row.get(ik) or '').strip()
            try:
                v = float(row.get(vk))
            except (TypeError, ValueError):
                continue
            if not nm:
                continue
            name_ids.setdefault(nm, set()).add(sid)
            by_name[nm] = (v, sid)
            if cty and twn:
                by_loc[(cty, twn, nm)] = (v, sid)

    amb = {n: ids for n, ids in name_ids.items() if len(ids) > 1}
    print(f'★ 同名對應多個 STID 的站：{len(amb)} 個')
    for n, ids in sorted(amb.items()):
        print(f'    {n}  →  {sorted(ids)}')

    # 逐鄉鎮以兩種方式計算，找出差異
    out, risky = [], []
    for town, regs in sorted(tw.items()):
        cty, twn = town[:3], town[3:]
        best_geo = best_nm = None
        for r in regs:
            stn = (r.get('station') or '').strip()
            av = r.get('alert') or 0
            if not stn or not av:
                continue
            g = by_loc.get((cty, twn, stn)) or by_loc.get((cty, twn, r.get('station_norm') or ''))
            n = by_name.get(stn) or by_name.get(r.get('station_norm') or '')
            if g:
                p = g[0] / av
                if best_geo is None or p > best_geo[0]:
                    best_geo = (p, g[0], av, stn, g[1])
            if n:
                p = n[0] / av
                if best_nm is None or p > best_nm[0]:
                    best_nm = (p, n[0], av, stn, n[1])
        gp = round(best_geo[0] * 100, 1) if best_geo else None
        np_ = round(best_nm[0] * 100, 1) if best_nm else None
        mismatch = (gp is not None and np_ is not None and abs(gp - np_) > 1.0)
        out.append({
            '鄉鎮': town,
            '地理對站ETR2%': gp, '地理對站ETR2mm': best_geo[1] if best_geo else None,
            '地理對站站名': best_geo[3] if best_geo else '',
            '地理對站STID': best_geo[4] if best_geo else '',
            '警戒值': best_geo[2] if best_geo else (best_nm[2] if best_nm else None),
            '僅站名ETR2%': np_,
            '僅站名STID': best_nm[4] if best_nm else '',
            '兩法不一致': 'Y' if mismatch else '',
        })
        if mismatch:
            risky.append((town, gp, np_, best_geo[3], best_geo[4], best_nm[4]))

    with io.open(OUT_CSV, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        w.writeheader()
        w.writerows(out)

    print(f'\n★★ 兩種對站法結果不一致的鄉鎮：{len(risky)} 個'
          '（舊法在這些鄉鎮會取到錯的站）')
    print(f"{'鄉鎮':16s}{'正確%':>8s}{'舊法%':>8s}  站名 / 正確STID / 舊法STID")
    for t, gp, np_, stn, gid, nid in sorted(risky, key=lambda x: -abs((x[1] or 0) - (x[2] or 0))):
        print(f'{t:16s}{gp:8.1f}{np_:8.1f}  {stn} / {gid} / {nid}')

    n_no = sum(1 for r in out if r['地理對站ETR2%'] is None)
    print(f'\n已寫出 {OUT_CSV}（{len(out)} 鄉鎮，其中 {n_no} 個對不到任何站）')

    # ── 摘要 JSON：供 workflow 直接顯示，不必下載 CSV ──
    summary = {
        'checked_at': __import__('datetime').datetime.now().isoformat(timespec='seconds'),
        'n_streams': len(rows),
        'n_townships': len(out),
        'n_unmatched': n_no,
        'ambiguous_names': {n: sorted(ids) for n, ids in sorted(amb.items())},
        'mismatched': [
            {'township': t, 'correct_pct': gp, 'oldway_pct': np_,
             'station': stn, 'correct_stid': gid, 'oldway_stid': nid}
            for t, gp, np_, stn, gid, nid in
            sorted(risky, key=lambda x: -abs((x[1] or 0) - (x[2] or 0)))
        ],
        # 供與官網逐一核對：ETR2% 由高至低前 40 名
        'top40': sorted(
            [{'township': r['鄉鎮'], 'pct': r['地理對站ETR2%'],
              'mm': r['地理對站ETR2mm'], 'alert': r['警戒值'],
              'station': r['地理對站站名'], 'stid': r['地理對站STID']}
             for r in out if r['地理對站ETR2%'] is not None],
            key=lambda x: -x['pct'])[:40],
    }
    with io.open('audit_etr2_summary.json', 'w', encoding='utf-8') as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print('已寫出 audit_etr2_summary.json')

    # ── 供人工核對的前 40 名（直接印在 log，最方便）──
    print(f"\n{'鄉鎮':16s}{'ETR2%':>7s}{'ETR2mm':>8s}{'警戒值':>7s}  代表站 / STID")
    for r in summary['top40']:
        print(f"{r['township']:16s}{r['pct']:7.1f}{(r['mm'] or 0):8.1f}"
              f"{(r['alert'] or 0):7.0f}  {r['station']} / {r['stid']}")
    print('\n請以上列與水保署官網逐一核對。')


if __name__ == '__main__':
    main()
