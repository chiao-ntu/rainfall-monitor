#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證站名撞名不再污染 ETR2（臺中和平區 破百 vs 官網個位數 之根因）。

全臺有 6 組代表站「去尾英文字母後同名、實為不同機關的不同站」：
  武陵/武陵w、關山/關山w、南庄/南庄w、外大坪/外大坪w、寒溪/寒溪s、雙溪/雙溪tp
舊版無條件建立正規化鍵，使兩站塌成同一鍵，對站時可能取到另一站的值。
"""
import io, json, os, sys, types
os.environ.setdefault('CWA_API_KEY', 'dummy')
import fetch_qpesums_hourly as H

fails = []
def chk(label, got, exp):
    ok = got == exp
    if not ok: fails.append(label)
    print(f"  {'OK ' if ok else '!! '}{label}: {got!r}" + ('' if ok else f"  期望 {exp!r}"))


class R:
    def __init__(s, o): s.status_code = 200; s.content = json.dumps(o).encode()


# ★ STRT 直接就是毫米
# 兩個「武陵」：氣象署武陵 35mm、水利署武陵w 380mm
DATA = [
    {"County": "臺中市", "Town": "和平區", "AlertValue": 350,
     "STName1": "武陵",  "STRT1": 35.0, "STName2": "德基", "STRT2": 12.0},
    {"County": "宜蘭縣", "Town": "大同鄉", "AlertValue": 400,
     "STName1": "武陵w", "STRT1": 380.0, "STName2": "土場", "STRT2": 20.0},
    # 不撞名者：正規化鍵應正常建立
    {"County": "南投縣", "Town": "仁愛鄉", "AlertValue": 400,
     "STName1": "廬山國小s", "STRT1": 44.0, "STName2": "奧萬大", "STRT2": 30.0},
]

H.requests = types.SimpleNamespace(get=lambda *a, **k: R(DATA))
out = H.fetch_swcb_hourly()

slope = '/mnt/user-data/uploads/slope_warning_stations.json'
print('=== 真實資料：同名不同站必須以地理位置區分 ===')
# ★ 使用者於 2026-08-26 自 API 實抓的原始資料（節錄）
REAL = [
 {"County":"臺中市","Town":"和平區","Vill":"平等里","DebrisNO":"中市DF037","AlertValue":350,
  "STID1":"A0F010","STName1":"武陵","STRT1":33.726505,
  "STID2":"C0F9Z0","STName2":"雪山東峰","STRT2":82.583374},
 {"County":"臺中市","Town":"和平區","Vill":"平等里","DebrisNO":"中市DF038","AlertValue":350,
  "STID1":"A0F010","STName1":"武陵","STRT1":33.726505,
  "STID2":"C0F9Z0","STName2":"雪山東峰","STRT2":82.583374},
 {"County":"臺東縣","Town":"延平鄉","Vill":"武陵村","DebrisNO":"東縣DF028","AlertValue":450,
  "STID1":"01S130","STName1":"武陵","STRT1":162,
  "STID2":"C0SA40","STName2":"瑞和","STRT2":0},
]
H.requests = types.SimpleNamespace(get=lambda *a, **k: R(REAL))
rl = H.fetch_swcb_hourly()
chk('STRT 即毫米，不做換算（臺中武陵 STID）', rl.get('A0F010'), 33.726505)
chk('臺東武陵 STID 各自獨立', rl.get('01S130'), 162.0)
chk('★同名多站 → 不建立站名鍵（避免覆蓋）', rl.get('武陵'), None)
chk('唯一站名仍可用站名對到', rl.get('雪山東峰'), 82.583374)
chk('地理索引：臺中和平的武陵', H.SWCB_BY_LOC.get(('臺中市','和平區','武陵')), 33.726505)
chk('地理索引：臺東延平的武陵', H.SWCB_BY_LOC.get(('臺東縣','延平鄉','武陵')), 162.0)

print('\n=== 聚合：和平區必須取 33.7 而非 162 ===')
if os.path.exists(slope):
    import shutil, tempfile
    from datetime import datetime
    tmp3 = tempfile.mkdtemp(); cwd3 = os.getcwd()
    shutil.copy(slope, tmp3); os.chdir(tmp3)
    H.SLOPE_WARN_FILE = 'slope_warning_stations.json'; H.ETR2_FILE = 'etr2_now.json'
    H.write_etr2_now(rl, datetime(2026, 8, 26, 12, 0))
    dd = json.load(io.open('etr2_now.json', encoding='utf-8'))
    hp3 = dd['townships'].get('臺中市和平區')
    print('  和平區:', json.dumps(hp3, ensure_ascii=False) if hp3 else '（無）')
    if hp3:
        chk('★和平區 ETR2 取臺中武陵 33.7', round(hp3['etr2'], 1), 33.7)
        pct3 = hp3['pct'] * 100
        print(f"  ETR2% = {pct3:.1f}%（誤用臺東 162 會是 {162/350*100:.0f}%）")
        if pct3 > 30: fails.append(f'和平區 ETR2% 仍偏高（{pct3:.1f}%）')
        else: print('  OK  與官網量級一致')
    os.chdir(cwd3); shutil.rmtree(tmp3, ignore_errors=True)
H.requests = types.SimpleNamespace(get=lambda *a, **k: R(DATA))
out = H.fetch_swcb_hourly()

print('=== 撞名站不得建立正規化鍵 ===')
chk('武陵 35mm', out.get('武陵'), 35.0)
chk('武陵w 380mm', out.get('武陵w'), 380.0)
# ★ 關鍵：正規化鍵「武陵」若被 武陵w 覆蓋，和平區就會拿到 380 而非 5
chk('★正規化鍵未被另一站污染', out.get('武陵'), 35.0)

print('\n=== 不撞名者仍建立正規化鍵（對站能力不受損）===')
chk('廬山國小s 44mm', out.get('廬山國小s'), 44.0)
chk('廬山國小（正規化）可對到', out.get('廬山國小'), 44.0)
chk('奧萬大 30mm', out.get('奧萬大'), 30.0)

print('\n=== 聚合後的鄉鎮值 ===')
# 用真實警戒表驗證和平區
if os.path.exists(slope):
    import shutil, tempfile
    tmp = tempfile.mkdtemp(); cwd = os.getcwd()
    shutil.copy(slope, tmp); os.chdir(tmp)
    H.SLOPE_WARN_FILE = 'slope_warning_stations.json'; H.ETR2_FILE = 'etr2_now.json'
    from datetime import datetime
    # 和平區 11 個單元，只給武陵有值（5mm / 警戒值350 = 1.4%）
    H.write_etr2_now({'武陵': 35.0, '武陵w': 380.0}, datetime(2026, 8, 20, 10, 0))
    d = json.load(io.open('etr2_now.json', encoding='utf-8'))
    hp = d['townships'].get('臺中市和平區')
    print('  臺中市和平區:', json.dumps(hp, ensure_ascii=False) if hp else '（無）')
    if hp:
        chk('★和平區取到武陵的 35mm 而非武陵w 的 380mm', hp['etr2'], 35.0)
        pct = hp['pct'] * 100
        print(f"  ETR2% = {pct:.1f}%（若誤用武陵w 會是 {380/hp['alert']*100:.0f}%）")
        if pct > 50: fails.append(f'和平區 ETR2% 仍異常偏高（{pct:.1f}%）')
        else: print('  OK  百分比回到合理範圍（不再破百）')
    os.chdir(cwd); shutil.rmtree(tmp, ignore_errors=True)
else:
    print('  （找不到 slope_warning_stations.json，略過聚合驗證）')

print('\n=== 量級防呆：單位錯誤時拒寫檔 ===')
if os.path.exists(slope):
    import shutil, tempfile
    from datetime import datetime
    tmp2 = tempfile.mkdtemp(); cwd2 = os.getcwd()
    shutil.copy(slope, tmp2); os.chdir(tmp2)
    H.SLOPE_WARN_FILE = 'slope_warning_stations.json'; H.ETR2_FILE = 'etr2_now.json'
    # 先寫一份正常的
    H.write_etr2_now({'武陵': 35.0}, datetime(2026, 8, 20, 10, 0))
    ok_exists = os.path.exists('etr2_now.json')
    before = io.open('etr2_now.json', encoding='utf-8').read() if ok_exists else ''
    # 再餵入「誤乘警戒值」的錯誤資料（全臺破萬%）
    bad = {}
    tw = json.load(io.open('slope_warning_stations.json', encoding='utf-8'))['townships']
    for regs in tw.values():
        for r in regs:
            bad[r['station']] = r['alert'] * 100.0        # 模擬放大 100 倍
    H.write_etr2_now(bad, datetime(2026, 8, 20, 11, 0))
    after = io.open('etr2_now.json', encoding='utf-8').read() if os.path.exists('etr2_now.json') else ''
    chk('★異常量級時不覆寫檔案（保留前一份）', after, before)
    os.chdir(cwd2); shutil.rmtree(tmp2, ignore_errors=True)


print('\n=== 稽核實測：5 個高風險鄉鎮必須取到正確的站 ===')
# ★ 2026-08-27 全臺稽核實測結果（30 個同名多站、5 個鄉鎮舊法接錯）
#   下列資料重現該 5 例的關鍵結構，確保地理對站能正確區分。
AUDIT = [
 # 關山：三個 STID，關山鎮與海端鄉各有自己的「關山」單元
 {"County":"臺東縣","Town":"關山鎮","AlertValue":500,
  "STID1":"C1O880","STName1":"關山","STRT1":139.0,
  "STID2":"81S570","STName2":"月眉國小s","STRT2":139.0},
 {"County":"臺東縣","Town":"海端鄉","AlertValue":450,
  "STID1":"C0S890","STName1":"關山","STRT1":153.9,
  "STID2":"81S900","STName2":"加拿國小s","STRT2":153.9},
 {"County":"高雄市","Town":"六龜區","AlertValue":250,
  "STID1":"01O760","STName1":"關山","STRT1":254.0,      # ← 舊法會用這個覆蓋上面兩個
  "STID2":"C1V340","STName2":"大津","STRT2":386.8},
 # 大坑：臺中北屯/潭子 與 花蓮壽豐 同名
 {"County":"臺中市","Town":"北屯區","AlertValue":500,
  "STID1":"C0F970","STName1":"大坑","STRT1":89.5,
  "STID2":"C0F970","STName2":"大坑","STRT2":89.5},
 {"County":"花蓮縣","Town":"壽豐鄉","AlertValue":400,
  "STID1":"C0T9E0","STName1":"大坑","STRT1":152.5,      # ← 舊法會用這個覆蓋臺中
  "STID2":"C0T870","STName2":"鯉魚潭","STRT2":224.6},
]
H.requests = types.SimpleNamespace(get=lambda *a, **k: R(AUDIT))
au = H.fetch_swcb_hourly()
chk('關山對應多 STID → 不建站名鍵', au.get('關山'), None)
chk('大坑對應多 STID → 不建站名鍵', au.get('大坑'), None)
chk('地理索引：關山鎮的關山', H.SWCB_BY_LOC.get(('臺東縣','關山鎮','關山')), 139.0)
chk('地理索引：海端鄉的關山', H.SWCB_BY_LOC.get(('臺東縣','海端鄉','關山')), 153.9)
chk('地理索引：六龜區的關山', H.SWCB_BY_LOC.get(('高雄市','六龜區','關山')), 254.0)
chk('地理索引：北屯區的大坑', H.SWCB_BY_LOC.get(('臺中市','北屯區','大坑')), 89.5)
chk('地理索引：壽豐鄉的大坑', H.SWCB_BY_LOC.get(('花蓮縣','壽豐鄉','大坑')), 152.5)

if os.path.exists(slope):
    import shutil, tempfile
    from datetime import datetime
    tmp4 = tempfile.mkdtemp(); cwd4 = os.getcwd()
    shutil.copy(slope, tmp4); os.chdir(tmp4)
    H.SLOPE_WARN_FILE = 'slope_warning_stations.json'; H.ETR2_FILE = 'etr2_now.json'
    H.write_etr2_now(au, datetime(2026, 8, 27, 12, 0))
    dd4 = json.load(io.open('etr2_now.json', encoding='utf-8'))['townships']
    for town, want_pct, want_mm in [('臺東縣關山鎮', 27.8, 139.0),
                                    ('臺東縣海端鄉', 34.2, 153.9),
                                    ('臺中市北屯區', 17.9, 89.5)]:
        r4 = dd4.get(town)
        got = round(r4['pct']*100, 1) if r4 else None
        ok = got is not None and abs(got - want_pct) < 0.6
        if not ok: fails.append(f'{town} ETR2% {got} ≠ 稽核值 {want_pct}')
        print(f"  {'OK ' if ok else '!! '}{town}: {got}%（稽核 {want_pct}%）"
              + (f" 站={r4['station']}" if r4 else ''))
    os.chdir(cwd4); shutil.rmtree(tmp4, ignore_errors=True)


print('\n=== 已知同名站清單須與實測一致 ===')
amb_file = '/home/claude/ambiguous_stations.json'
if not os.path.exists(amb_file):
    amb_file = 'ambiguous_stations.json'
if os.path.exists(amb_file):
    ref = json.load(io.open(amb_file, encoding='utf-8'))
    names = ref['ambiguous_names']
    print(f"  已知同名多站：{len(names)} 個（2026-08-27 全臺稽核實測）")
    chk('清單筆數為 30', len(names), 30)
    chk('關山對應 3 個 STID', len(names.get('關山', [])), 3)
    chk('武陵含臺中 A0F010', 'A0F010' in names.get('武陵', []), True)
    chk('武陵含臺東 01S130', '01S130' in names.get('武陵', []), True)
    # 已知接錯站的鄉鎮：虛增幅度需與紀錄一致
    for m in ref['_known_mismatched_townships']:
        gap = round(m['oldway_pct'] - m['correct_pct'], 1)
        print(f"  {m['township']}：正確 {m['correct_pct']}%、"
              f"舊法 {m['oldway_pct']}%（虛增 {gap}%）站={m['station']}")
        if gap <= 0:
            fails.append(f"{m['township']} 舊法未虛增，紀錄可能有誤")
    chk('已知受影響鄉鎮為 5 個', len(ref['_known_mismatched_townships']), 5)
else:
    print('  （找不到 ambiguous_stations.json，略過）')


print('\n=== 風力預報擷取（F-D0047）===')
# ★ 不以 ElementName 比對（實際字串未經證實），改認 WindSpeed/BeaufortScale 值鍵
import fetch_rainfall as FR
SAMPLE = {"records":{"Locations":[{"Location":[{
  "LocationName":"仁愛鄉",
  "WeatherElement":[
    {"ElementName":"溫度","Time":[
      {"DataTime":"2026-08-30T12:00:00+08:00","ElementValue":[{"Temperature":"22"}]}]},
    {"ElementName":"風速","Time":[
      {"DataTime":"2026-08-30T12:00:00+08:00",
       "ElementValue":[{"WindSpeed":"12","BeaufortScale":"6"}]},
      {"DataTime":"2026-08-30T15:00:00+08:00",
       "ElementValue":[{"WindSpeed":"18","BeaufortScale":"8"}]}]}]}]}]}}
class _R:
    status_code = 200
    def json(self): return SAMPLE
    def raise_for_status(self): pass
FR.requests = types.SimpleNamespace(get=lambda *a, **k: _R())
FR.WIND_FCST.clear()
FR.fetch_pop_county('南投縣', 'F-D0047-055', True)
w = FR.WIND_FCST.get('南投縣', {}).get('仁愛鄉', [])
chk('抓到兩段風力', len(w), 2)
chk('風速正確', [x['ws'] for x in w], [12.0, 18.0])
chk('蒲福風級正確', [x['bf'] for x in w], [6, 8])
chk('★溫度未被誤當風速', all(x['ws'] in (12.0, 18.0) for x in w), True)
chk('★逐3小時已補區間結束時間', w[0]['end'] != w[0]['start'], True)
print(f"  區間：{w[0]['start'][11:16]} → {w[0]['end'][11:16]}")


print('\n=== 離島風力：">= N" 格式與多時間點（真實檔案）===')
# ★ 使用者實抓的連江縣 F-D0047-081：風大時官方以 ">= 11" 表示，
#   且逐 3 小時資料只填 DataTime（StartTime 為空字串）。
#   這兩點先前各自造成：整筆丟棄、32 段併成 1 段。
_xml = 'F-D0047-081.xml'
if os.path.exists(_xml):
    import re as _re, xml.etree.ElementTree as _ET
    _raw = _re.sub(r'\sxmlns="[^"]+"', '', io.open(_xml, encoding='utf-8').read(), count=1)
    _root = _ET.fromstring(_raw)
    _locs = []
    for _loc in _root.iter('Location'):
        _wl = []
        for _we in _loc.findall('WeatherElement'):
            _ts = []
            for _t in _we.findall('Time'):
                _ev = _t.find('ElementValue')
                _evd = {c.tag: (c.text or '') for c in _ev} if _ev is not None else {}
                _ts.append({'DataTime': (_t.findtext('DataTime') or ''),
                            'StartTime': (_t.findtext('StartTime') or ''),
                            'EndTime': (_t.findtext('EndTime') or ''),
                            'ElementValue': [_evd]})
            _wl.append({'ElementName': _we.findtext('ElementName') or '', 'Time': _ts})
        _locs.append({'LocationName': _loc.findtext('LocationName') or '',
                      'WeatherElement': _wl})
    _SAMPLE = {'records': {'Locations': [{'Location': _locs}]}}
    class _RR:
        status_code = 200
        def json(self): return _SAMPLE
        def raise_for_status(self): pass
    FR.requests = types.SimpleNamespace(get=lambda *a, **k: _RR())
    FR.WIND_FCST.clear()
    FR.fetch_pop_county('連江縣', 'F-D0047-081', True)
    _w = FR.WIND_FCST.get('連江縣', {})
    chk('★連江縣四鄉皆有風力', sorted(_w.keys()),
        ['北竿鄉', '南竿鄉', '東引鄉', '莒光鄉'])
    _n = len(_w.get('南竿鄉', []))
    print(f"  南竿鄉 {_n} 段（檔案含 32 個時間點）")
    chk('★多時間點未被去重誤併', _n >= 30, True)
    chk('★">= 11" 解析為 11.0', _w['南竿鄉'][0]['ws'], 11.0)
    chk('★">= 6" 解析為 6 級', _w['南竿鄉'][0]['bf'], 6)
    chk('區間結束時間已補', _w['南竿鄉'][0]['end'] != _w['南竿鄉'][0]['start'], True)
else:
    print('  （找不到 F-D0047-081.xml，略過）')


print('\n=== 風力須涵蓋全臺 22 縣市（含無坡地警戒的離島）===')
# ★ 金門、澎湖、連江沒有坡地警戒站。若 counties_needed 沿用 alert_table 的
#   縣市集合，這三縣市的 PoP 與風力會完全不抓 —— 實測即為離島無風力的主因。
_src = io.open('fetch_rainfall.py', encoding='utf-8').read()
chk('端點表含 22 縣市', len(FR.COUNTY_EP_3D), 22)
for _c in ('金門縣', '澎湖縣', '連江縣'):
    chk(f'{_c} 有 3 天端點', bool(FR.COUNTY_EP_3D.get(_c)), True)
    chk(f'{_c} 有 7 天端點', bool(FR.COUNTY_EP_7D.get(_c)), True)
chk('連江 3D 端點為 081（與實測檔案相符）', FR.COUNTY_EP_3D['連江縣'], 'F-D0047-081')
chk('連江 7D 端點為 083（與實測檔案相符）', FR.COUNTY_EP_7D['連江縣'], 'F-D0047-083')
chk('★counties_needed 以端點表為底（不受坡地警戒縣市限制）',
    'counties_needed = set(COUNTY_EP_3D.keys())' in _src, True)
_bad = [c for c in FR.COUNTY_EP_3D
        if int(FR.COUNTY_EP_7D[c].split('-')[-1]) - int(FR.COUNTY_EP_3D[c].split('-')[-1]) != 2]
chk('3D/7D 端點編號規則一致', _bad, [])


print('\n=== 全臺打包檔：一次下載取代 44 次呼叫 ===')
# ★ 逐縣市呼叫時，任一縣市失敗該縣市即全無資料（離島尤其常見）。
#   打包檔只有一次成敗，可靠得多；解析共用 _extract_pop_wind，兩路徑不會分歧。
_zipf = 'F-D0047-093.zip'
if os.path.exists(_zipf):
    _raw = open(_zipf, 'rb').read()
    class _ZR:
        status_code = 200
        content = _raw
    FR.requests = types.SimpleNamespace(get=lambda *a, **k: _ZR())
    FR.WIND_FCST.clear()
    _res = FR.fetch_all_pop_bundle(set(FR.COUNTY_EP_3D.keys()))
    chk('打包路徑成功回傳', bool(_res), True)
    if _res:
        _p3, _p7 = _res
        _wn = sum(len(v) for v in FR.WIND_FCST.values())
        print(f"  PoP3d {len(_p3)} 鄉鎮、PoP7d {len(_p7)}、風力 {_wn} 鄉鎮")
        chk('★風力涵蓋全臺 368 鄉鎮', _wn, 368)
        chk('風力涵蓋 22 縣市', len(FR.WIND_FCST), 22)
        # ★ 改以「縣市+鄉鎮」為鍵後，368 個鄉鎮全數到齊（不再被同名覆蓋）
        _comp = [k for k in _p3 if any(k.startswith(c) for c in FR.COUNTY_EP_3D)]
        chk('★PoP 複合鍵涵蓋 368 鄉鎮', len(_comp), 368)
        # 同名鄉鎮必須各自取到自己的值
        from datetime import datetime as _dt
        _base = _dt(2026, 8, 28, 18, 0, 0)
        for _twn, _n in (('東區', 4), ('中正區', 2), ('北區', 3)):
            _vals = {}
            for _c in FR.COUNTY_EP_3D:
                if (_c + _twn) in _p3:
                    _vals[_c] = tuple(FR.get_pop_6h_series(_twn, _p3, _p7, _base,
                                                           county=_c)[:4])
            chk(f'{_twn} 出現在 {_n} 個縣市', len(_vals), _n)
            if len(_vals) > 1:
                print(f"  {_twn}：" + "、".join(f"{c}{list(v)}" for c, v in
                                                list(_vals.items())[:2]))
                if len(set(_vals.values())) == 1:
                    print(f"  （{_twn} 各縣市值恰好相同，無法據此判定，略過）")
                else:
                    print(f"  OK  {_twn} 各縣市取到不同值（未互相覆蓋）")
        chk('保留純鄉鎮名鍵以相容', ('東區' in _p3), True)
        for _c, _n in (('連江縣', 4), ('金門縣', 6), ('澎湖縣', 6)):
            chk(f'★{_c} 有風力（{_n} 鄉鎮）', len(FR.WIND_FCST.get(_c, {})), _n)
        # 逐時段數：打包檔為逐 3 小時
        _first = next(iter(FR.WIND_FCST['連江縣'].values()))
        chk('每鄉鎮有多個時段', len(_first) > 10, True)
    chk('解析共用同一函式（不分歧）',
        '_extract_pop_wind(raw, county, is_3day)' in
        io.open('fetch_rainfall.py', encoding='utf-8').read(), True)
    chk('打包失敗會退回逐縣市',
        'bundled = fetch_all_pop_bundle' in
        io.open('fetch_rainfall.py', encoding='utf-8').read(), True)
else:
    print('  （找不到 F-D0047-093.zip，略過）')


print('\n=== 波浪預報模式（F-A0020-001）===')
# ★ 改用波浪模式格點資料：0.1°格點、逐時、含浪高/浪向/週期。
#   先前 F-D0047-095/096 與 F-A0085-00x 皆非沿海預報（404 或寒害指數）。
_src4 = io.open('fetch_rainfall.py', encoding='utf-8').read()
chk('採用波浪模式端點', FR.WAVE_EP, 'F-A0020-001')
chk('限制時間步控制體積', FR.WAVE_STEPS, 24)
chk('讀沿海鄉鎮清單', FR.COASTAL_TOWNS_FILE, 'coastal_towns.json')
chk('★經 ProductURL 下載（直接要 ZIP 會 500）',
    'url = _resolve_product_url(WAVE_EP)' in _src4, True)
# ★ 檔案型產品必須走 fileapi：datastore 對這類 dataid 回 404（實測 2026-08-31）
chk('metadata 先試 fileapi', "f\"{FILEAPI}/{dataid}\"" in _src4, True)
chk('帶 downloadType=WEB', "'downloadType': 'WEB'" in _src4, True)
chk('備援也走 fileapi 不走 datastore',
    'r = cwa_get(f"{FILEAPI}/{WAVE_EP}"' in _src4, True)
chk('FILEAPI 於檔首定義', _src4.index('FILEAPI      =') < _src4.index('def _resolve_product_url'), True)

# 端到端：metadata 全 404 時，仍能由 fileapi 直接取得 zip
_wz = '/mnt/user-data/uploads/F-A0020-001.zip'
if os.path.exists(_wz) and os.path.exists('coastal_towns.json'):
    _raw = open(_wz, 'rb').read()
    def _mk4(url, **kw):
        class _R:
            status_code = 404 if kw.get('params', {}).get('format') == 'JSON' else 200
            content = _raw
            def json(self): raise ValueError('not json')
        return _R()
    FR.requests = types.SimpleNamespace(get=_mk4)
    _save = FR.WAVE_STEPS; FR.WAVE_STEPS = 4
    _w4 = FR.fetch_wave_forecast()
    FR.WAVE_STEPS = _save
    print(f"  端到端：{len(_w4)} 個沿海鄉鎮")
    chk('★metadata 404 時仍取得浪高', len(_w4) >= 80, True)
    if _w4:
        _s4 = next(iter(_w4.values()))[0]
        chk('含浪高值', _s4['wave'] is not None, True)
        chk('含浪向', _s4['dir'] is not None, True)
        chk('含週期', _s4['period'] is not None, True)
chk('打包預報同樣經 ProductURL',
    "_burl = _resolve_product_url('F-D0047-093')" in _src4, True)

# ProductURL 解析器：對兩種 metadata 結構都須有效
for _name, _meta, _want in [
    ('ProductURL 大寫',
     {"cwaopendata":{"Resources":{"Resource":{"ProductURL":"https://x/a.zip"}}}}, 'https://x/a.zip'),
    ('uri 小寫',
     {"cwaopendata":{"dataset":{"resource":{"uri":"https://y/b.zip"}}}}, 'https://y/b.zip'),
]:
    class _MR:
        status_code = 200
        def __init__(self, m): self._m = m
        def json(self): return self._m
    FR.requests = types.SimpleNamespace(get=lambda *a, _m=_meta, **k: _MR(_m))
    chk(f'解析 {_name}', FR._resolve_product_url('X'), _want)


print('\n=== 颱風格點 QPF（F-C0041）解析修正 ===')
# ★ 舊寫法有兩個 bug：用 records.dataset（實際是 cwaopendata.Dataset）、
#   依換行切列（實際是 130×130 攤平的 16,900 個逗號分隔值）。
#   兩者都會讓颱風期間拿不到格點 —— 而那正是最需要精細預報的時候。
_qf = [f'/mnt/user-data/uploads/F-C0041-{n:03d}.json' for n in range(1, 9)]
if all(os.path.exists(f) for f in _qf):
    _qd = [json.load(io.open(f, encoding='utf-8')) for f in _qf]
    _qit = iter(_qd)
    def _mkq(*a, **k):
        class _R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self): return next(_qit)
        return _R()
    FR.requests = types.SimpleNamespace(get=_mkq)
    _segs = FR.fetch_typhoon_qpf()
    chk('★解析出 8 段（48 小時）', len(_segs), 8)
    if _segs:
        _p = _segs[0]['points']
        chk('臺灣範圍格點數合理', 8000 < len(_p) < 11000, True)
        chk('首段有起始時間', _segs[0]['start'][:4], '2026')
        _mx = max(_p, key=lambda x: x[2])
        print(f"  首段最大 {_mx[2]:.1f}mm @ {_mx[0]:.2f}N {_mx[1]:.2f}E")
        chk('數值合理（非全零）', _mx[2] > 10, True)
        chk('緯度在臺灣範圍', 21.5 <= _mx[0] <= 26.5, True)

print('\n=== 預報員研判雨量區間（F-C0034）===')
_t24 = '/mnt/user-data/uploads/24hPrecipTable.xml'
_tal = '/mnt/user-data/uploads/AllPrecipTable.xml'
if os.path.exists(_t24) and os.path.exists(_tal):
    _tb = {'F-C0034-006': open(_t24, 'rb').read(),
           'F-C0034-007': open(_tal, 'rb').read()}
    _st = {'ep': None}
    def _mkt(url, **k):
        class _R:
            status_code = 200
            def json(self):
                _st['ep'] = url.rsplit('/', 1)[-1]
                return {"cwaopendata": {"Dataset": {"Resource":
                        {"ProductURL": "https://x/" + _st['ep']}}}}
            @property
            def content(self): return _tb.get(_st['ep'], b'')
        return _R()
    FR.requests = types.SimpleNamespace(get=_mkt)
    _fp = FR.fetch_forecaster_precip()
    chk('取得 24h 與總雨量兩份', sorted(_fp.keys()), ['24h', 'total'])
    if '24h' in _fp:
        _a = _fp['24h']['areas']
        chk('地區數', len(_a), 24)
        chk('★區分平地與山區', 'mountain' in _a.get('臺北市', {}), True)
        chk('基隆僅平地', list(_a.get('基隆市', {}).keys()), ['flat'])
        _pt = _a.get('屏東縣', {}).get('mountain', {})
        print(f"  屏東山區 24h {_pt.get('lo')}-{_pt.get('hi')}mm、"
              f"總雨量 {_fp['total']['areas']['屏東縣']['mountain']['hi']}mm")
        chk('屏東山區有數值', _pt.get('hi', 0) > 0, True)
        chk('總雨量高於24h',
            _fp['total']['areas']['屏東縣']['mountain']['hi'] >= _pt.get('hi', 0), True)
    chk('★不限颱風（豪雨事件亦發布）', '豪雨事件' in _fp['24h']['title'], True)
    _src5 = io.open('fetch_rainfall.py', encoding='utf-8').read()
    chk('已處理 xsi 命名空間前綴', '帶 xsi: 命名空間前綴' in _src5, True)
    chk('輸出含 forecaster_precip', "'forecaster_precip': fc_precip" in _src5, True)
    chk('★載明不用於 ETR2 判定', '不用於 ETR2 警戒判定' in _src5, True)


print('\n=== 抗故障：殘缺資料不得覆蓋好資料 ===')
# ★ 實測 2026-09-01：CWA 開放資料大規模連線失敗，該輪 ETR2 由 156 掉到 0，
#   但 data.json 仍照常寫出，等於用殘缺資料覆蓋可用資料 ——
#   對防災系統而言比「不更新」更危險。
_src6 = io.open('fetch_rainfall.py', encoding='utf-8').read()
chk('★寫檔前有健全性檢查', '中止寫檔：資料明顯退化' in _src6, True)
chk('與前一輪比對 ETR2 鄉鎮數', "_pe >= 50 and _cur_etr2 < _pe * 0.3" in _src6, True)
chk('觀測全失效時中止', '本輪無任何 ETR2 觀測' in _src6, True)
chk('說明比不更新更危險', '比「不更新」更危險' in _src6, True)

print('\n=== 抗故障：CWA 請求節流 ===')
chk('提供節流函式', 'def cwa_get(url, **kw):' in _src6, True)
chk('僅對 CWA 主機節流', "'opendata.cwa.gov.tw' in url" in _src6, True)
chk('使用 Session 重用連線', 'requests.Session()' in _src6, True)
chk('節流間隔已設定', '_CWA_MIN_GAP  = 0.35' in _src6, True)
chk('尊重測試替換的 requests', "getattr(requests, '__name__', '') == 'requests'" in _src6, True)
_n_cwa = _src6.count('cwa_get(')
_n_raw = _src6.count('requests.get(')
print(f'  呼叫點：cwa_get {_n_cwa-1} 處、原生 requests.get {_n_raw} 處')
chk('★呼叫點已全面改用節流版', _n_raw, 1)

print('\n全部通過' if not fails else f'\n失敗 {len(fails)} 項：{fails}')
sys.exit(1 if fails else 0)
