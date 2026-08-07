#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
土石流／大規模崩塌 SHP → 精簡 GeoJSON（TWD97 TM2 → WGS84）
供前端按需載入（方案B：幾何獨立檔，不塞進 data.json）。

產出：
  debris_geo.json  土石流：溪流線 + 影響範圍 + 集水區（以 Debrisno 為鍵）
  landslide_geo.json 大規模崩塌94處：潛勢範圍 + 影響範圍（以 lslno 為鍵）
"""
import shapefile, json, glob, os
from pyproj import Transformer

SRC = "/home/claude/gis"
# TWD97 TM2（中央經線121°、假東距250000、尺度0.9999）→ WGS84
TF = Transformer.from_crs("EPSG:3826", "EPSG:4326", always_xy=True)

def to_wgs(pts):
    """[(x,y)...] → [[lng,lat]...]（6位小數≈0.1m，足夠且省空間）"""
    if not pts: return []
    xs, ys = zip(*[(p[0], p[1]) for p in pts])
    lngs, lats = TF.transform(xs, ys)
    return [[round(a, 6), round(b, 6)] for a, b in zip(lngs, lats)]

def simplify(pts, tol=0.00012):
    """Douglas-Peucker 簡化（tol≈13m）：大幅縮檔且視覺無感。"""
    if len(pts) <= 2: return pts
    def dp(p, s, e):
        dmax, idx = 0, 0
        x1, y1 = p[s]; x2, y2 = p[e]
        dx, dy = x2-x1, y2-y1
        den = (dx*dx + dy*dy) ** 0.5 or 1e-12
        for i in range(s+1, e):
            x0, y0 = p[i]
            d = abs(dy*x0 - dx*y0 + x2*y1 - y2*x1) / den
            if d > dmax: dmax, idx = d, i
        if dmax > tol:
            return dp(p, s, idx)[:-1] + dp(p, idx, e)
        return [p[s], p[e]]
    try:
        return dp(pts, 0, len(pts)-1)
    except RecursionError:
        return pts[::max(1, len(pts)//200)]

def rings(shape, simp=True):
    """shapefile 幾何 → GeoJSON 座標（處理多部分 parts）"""
    pts = to_wgs(shape.points)
    parts = list(shape.parts) + [len(pts)]
    out = []
    for i in range(len(parts)-1):
        seg = pts[parts[i]:parts[i+1]]
        if len(seg) < 2: continue
        out.append(simplify(seg) if simp else seg)
    return out

def load(dirname, encoding='utf-8'):
    p = glob.glob(f"{SRC}/{dirname}/*.shp")
    if not p: raise FileNotFoundError(dirname)
    # 部分 dbf 中文欄位被截斷（末字元斷在多位元組中間）→ 寬容解碼
    r = shapefile.Reader(p[0], encoding=encoding, encodingErrors='replace')
    flds = [f[0] for f in r.fields[1:]]
    return r, flds

def rec2dict(flds, rec, keep):
    d = {}
    for k in keep:
        if k in flds:
            v = rec[flds.index(k)]
            if isinstance(v, str): v = v.strip()
            d[k] = v
    return d

# ── 1. 土石流：溪流線（主體）──
print("處理 土石流潛勢溪流…")
r, f = load("debrisstream1753_20260126_twd97")
streams = {}
for sh, rec in zip(r.shapes(), r.iterRecords()):
    no = rec[f.index('Debrisno')]
    info = rec2dict(f, rec, ['County01','Town01','Vill01','Name','Mark','Roadname',
                             'TRes_Class','Risk','Length','Type','Basin'])
    streams[no] = {'i': info, 'l': rings(sh)}
print(f"  {len(streams)} 條")

# ── 2. 土石流：影響範圍（面）──
print("處理 土石流影響範圍…")
r, f = load("debris1753_20260126_twd97")
affect = {}
for sh, rec in zip(r.shapes(), r.iterRecords()):
    no = rec[f.index('Debrisno')]
    affect.setdefault(no, []).extend(rings(sh))
print(f"  {len(affect)} 條有影響範圍")

# ── 3. 土石流：集水區（面，較大幅簡化）──
print("處理 土石流集水區…")
r, f = load("watershed1753_20260102_twd97_UTF8")
ws = {}
for sh, rec in zip(r.shapes(), r.iterRecords()):
    no = rec[f.index('Debrisno')]
    ws.setdefault(no, []).extend(rings(sh))
print(f"  {len(ws)} 條有集水區")

debris = {'streams': streams, 'affect': affect, 'watershed': ws}
with open('/home/claude/debris_geo.json', 'w', encoding='utf-8') as fp:
    json.dump(debris, fp, ensure_ascii=False, separators=(',', ':'))
sz = os.path.getsize('/home/claude/debris_geo.json')
print(f"→ debris_geo.json {sz//1024} KB")

# ── 4. 大規模崩塌：潛勢範圍 + 影響範圍 ──
print("\n處理 大規模崩塌 94 處…")
r, f = load("115年_94處警戒發布區潛勢範圍_TWD97_UTF8")
ls = {}
for sh, rec in zip(r.shapes(), r.iterRecords()):
    no = rec[f.index('lslno')]
    info = rec2dict(f, rec, ['County01','Town01','Vill01','Name','Mark','Type',
                             'Dw_count','TRes_Class','Risk','Basin','P_area'])
    ls[no] = {'i': info, 'p': rings(sh)}
print(f"  潛勢範圍 {len(ls)} 處")

r, f = load("115年_94處警戒發布區影響範圍_TWD97_UTF8")
n_aff = 0
for sh, rec in zip(r.shapes(), r.iterRecords()):
    no = rec[f.index('Islno')]
    if no in ls:
        ls[no]['a'] = rings(sh)
        ls[no]['i']['Total_Res'] = rec[f.index('Total_Res')]
        ls[no]['i']['Address'] = (rec[f.index('Address')] or '')[:80]
        n_aff += 1
print(f"  影響範圍 {n_aff} 處對上")

with open('/home/claude/landslide_geo.json', 'w', encoding='utf-8') as fp:
    json.dump(ls, fp, ensure_ascii=False, separators=(',', ':'))
sz2 = os.path.getsize('/home/claude/landslide_geo.json')
print(f"→ landslide_geo.json {sz2//1024} KB")
print(f"\n合計 {(sz+sz2)//1024} KB（原始 SHP 8.4MB）")
