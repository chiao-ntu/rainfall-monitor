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

print('\n全部通過' if not fails else f'\n失敗 {len(fails)} 項：{fails}')
sys.exit(1 if fails else 0)
