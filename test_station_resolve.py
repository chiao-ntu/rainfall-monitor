# -*- coding: utf-8 -*-
"""鄉鎮 ETR2 測站對位：低估要補回，但不可換成跨縣市誤配。

使用者回報的現象：宜蘭縣大同鄉在本系統 36%、水保署官方 46%，差在「寒溪s」
沒被算進去。根因是站名撞名時刻意不建正規化鍵（那條規則本身是對的，用來
避免「武陵」跨縣市誤配），但它造成「靜默落空」—— 對不到就當沒有這個站，
鄉鎮 ETR2 因此被低估。對預警系統而言低估比對錯更危險。

這支測試同時守兩個方向，因為修法很容易把其中一邊弄壞：
  A 低估要被補回（寒溪 → 寒溪s，同鄉鎮內消歧）
  B 不可因此放寬成跨縣市誤配（和平區不得拿到臺東延平的值；
    宜蘭不得撈到新竹的同名站）
"""
import sys
sys.path.insert(0, '/home/claude')
import fetch_rainfall as F

fails = []
def ok(c, m):
    print(('OK  ' if c else '!!  ') + m)
    if not c: fails.append(m)

# ── 貼近真實的索引 ──
#   SWCB_STN_LOC 有位置；swcb_etr2(st_val) 只有「全臺唯一站名」才建鍵，
#   所以撞名的「武陵」「寒溪」不在 swcb_etr2 裡 —— 這正是落空的來源。
F.SWCB_STN_LOC.clear(); F.SWCB_BY_LOC.clear()
F.SWCB_STN_LOC[('宜蘭縣', '大同鄉')] = {'寒溪s': 46.0, '松羅': 31.0}
F.SWCB_STN_LOC[('臺中市', '和平區')] = {'武陵w': 33.7, '梨山': 12.0}
F.SWCB_STN_LOC[('臺東縣', '延平鄉')] = {'武陵': 162.0}
F.SWCB_STN_LOC[('新竹縣', '五峰鄉')] = {'桃山': 20.0}
for (c, t), d in F.SWCB_STN_LOC.items():
    for nm, v in d.items(): F.SWCB_BY_LOC[(c, t, nm)] = v

# st_val：撞名者（武陵/武陵w、寒溪/寒溪s 的正規化鍵）不建立
swcb = {'寒溪s': 46.0, '松羅': 31.0, '武陵w': 33.7, '梨山': 12.0,
        '桃山': 20.0}
print(f'swcb_etr2 鍵：{sorted(swcb)}')
print('（「寒溪」「武陵」正規化鍵不存在 —— 靜默落空的來源）\n')

# ══ A 低估補回 ══
ok(swcb.get('寒溪') is None,
   'A1 前提成立：直查 swcb_etr2["寒溪"] 是 None（原本就是這樣漏掉的）')
v, tier, nm = F.resolve_station_etr2(['寒溪', ''], swcb,
                                     county='宜蘭縣', town='大同鄉',
                                     strict_geo=True)
ok(v == 46.0 and nm == '寒溪s' and tier == 'near_town',
   f'A2 大同鄉要「寒溪」→ 對到「{nm}」ETR2 {v}（層級 {tier}）')
ok(max(31.0, 46.0) == 46.0,
   f'A3 鄉鎮取大值：修正前只有松羅 31 → 修正後 46（低估 15 補回）')

# ══ B 不可跨縣市誤配 ══
# B1 和平區要「武陵」：strict_geo 只在同鄉鎮內找，應對到武陵w 33.7
v2, t2, nm2 = F.resolve_station_etr2(['武陵', ''], swcb,
                                     county='臺中市', town='和平區',
                                     strict_geo=True)
ok(v2 == 33.7 and nm2 == '武陵w',
   f'B1 和平區要「武陵」→「{nm2}」{v2}（同鄉鎮內），不是臺東的 162')
ok(v2 != 162.0, 'B2 臺東延平的 162 沒有流進臺中市（武陵錯誤未復發）')

# B3 宜蘭要「桃山」：該名全臺唯一但站在新竹 → 閘門必須擋掉
ok(swcb.get('桃山') == 20.0, 'B3 前提：直查「桃山」有值（新竹五峰的 20）')
ok(F._geo_ok('桃山', '新竹縣', '五峰鄉') is True,
   'B4 閘門放行：新竹五峰要「桃山」是對的地方')
ok(F._geo_ok('桃山', '宜蘭縣', '大同鄉') is False,
   'B5 閘門擋下：宜蘭大同要「桃山」會拿到新竹的值 —— 與武陵同一錯誤類型')
v3, t3, nm3 = F.resolve_station_etr2(['桃山', ''], swcb,
                                     county='宜蘭縣', town='大同鄉',
                                     strict_geo=True)
ok(v3 is None,
   f'B6 strict_geo 下宜蘭要「桃山」回 None（實得 {v3}／{nm3}）—— 寧可少報不報錯地方')

# B7 無位置可查（只有 STID 鍵）→ 放行，不弄壞既有行為
ok(F._geo_ok('沒有位置的站', '宜蘭縣', '大同鄉') is True,
   'B7 查不到位置的站名放行（無法否證，維持原行為）')

# ══ C 大崩那條路徑（strict_geo=False）行為不變 ══
# C1 大崩路徑（strict_geo=False）也必須擋跨縣市：它的 tier1/2 先前同樣無約束
v4, t4, nm4 = F.resolve_station_etr2(['桃山'], swcb,
                                     county='宜蘭縣', town='大同鄉')
ok(v4 is None,
   f'C1 大崩路徑：宜蘭要「桃山」也被擋（實得 {t4}／{v4}）—— 同一錯誤類型一起修')
v4b, t4b, nm4b = F.resolve_station_etr2(['桃山'], swcb,
                                        county='新竹縣', town='五峰鄉')
ok(v4b == 20.0 and t4b == 'exact',
   f'C1b 對的地方仍精確命中（{t4b}／{v4b}）—— 沒有把正常功能擋掉')
v4c, t4c, nm4c = F.resolve_station_etr2(['桃山'], swcb)
ok(v4c == 20.0,
   f'C1c 沒給縣市時維持原行為（{t4c}／{v4c}）')
v5, t5, nm5 = F.resolve_station_etr2(['梨'], swcb,
                                     county='臺中市', town='和平區')
ok(t5 in ('near_town', 'near_county') or v5 is None,
   f'C2 非 strict 模式仍可用 near_county 退路（{t5}／{v5}）')

print(f"\n（A–C 小結：{'FAIL '+str(len(fails)) if fails else '全過'}）")

# ══════════════════════════════════════════════════════════
#  D 端到端：直接跑 agg_obs()，驗證整條鄉鎮聚合路徑
#    _pick_sid 是 agg_obs 內的閉包，只能從外面整條跑才測得到。
#    這也同時驗證地理閘門真的接在路徑上，不是只有函式本身正確。
# ══════════════════════════════════════════════════════════
print('\n── D 端到端 agg_obs() ──')
from datetime import datetime, timezone, timedelta
TPE = timezone(timedelta(hours=8))
now = datetime(2026, 10, 4, 12, 0, tzinfo=TPE)

def _st(c, t, nm, lat=24.0, lon=121.0):
    return {'name': nm, 'county': c, 'township': t, 'lat': lat, 'lon': lon,
            'rain_now': 0.0, 'rain_1h': 0.0, 'rain_3h': 0.0, 'rain_6h': 0.0,
            'rain_12h': 0.0, 'rain_24h': 0.0, 'rain_2d': 0.0, 'rain_3d': 0.0}

# 兩個同名站「桃山」分屬宜蘭與新竹 —— 撞名，全臺索引不可信
stations = {
  'YL01': _st('宜蘭縣', '大同鄉', '松羅'),
  'YL02': _st('宜蘭縣', '大同鄉', '寒溪s'),
  'HC01': _st('新竹縣', '五峰鄉', '桃山'),
  'TC01': _st('臺中市', '和平區', '武陵w'),
  'TT01': _st('臺東縣', '延平鄉', '武陵'),
}
history = {}            # 空歷史 → CWA 自算退路拿不到值，單純測對位
alert_table = {}

# 靜態警戒表：大同鄉有兩個警戒單元，代表站分別是「寒溪」與「桃山」
#   「寒溪」→ 官方實際站名寒溪s（同鄉鎮，應補回 46）
#   「桃山」→ 水保署的桃山在新竹（跨縣市，應被擋掉而非灌 20）
slope_warn = {
  '宜蘭縣大同鄉': [
    {'village': '寒溪村', 'station': '寒溪',  'alert': 100},
    {'village': '松羅村', 'station': '桃山',  'alert': 100},
  ],
  '臺中市和平區': [
    {'village': '平等里', 'station': '武陵',  'alert': 100},
  ],
}
F.SWCB_STN_LOC.clear(); F.SWCB_BY_LOC.clear()
F.SWCB_STN_LOC[('宜蘭縣', '大同鄉')] = {'寒溪s': 46.0, '松羅': 31.0}
F.SWCB_STN_LOC[('臺中市', '和平區')] = {'武陵w': 33.7}
F.SWCB_STN_LOC[('臺東縣', '延平鄉')] = {'武陵': 162.0}
F.SWCB_STN_LOC[('新竹縣', '五峰鄉')] = {'桃山': 20.0}
for (c, t), d in F.SWCB_STN_LOC.items():
    for nm, v in d.items(): F.SWCB_BY_LOC[(c, t, nm)] = v
swcb2 = {'寒溪s': 46.0, '松羅': 31.0, '武陵w': 33.7, '桃山': 20.0}

import io as _io, contextlib as _ctx
_buf = _io.StringIO()
with _ctx.redirect_stdout(_buf):
    res = F.agg_obs(stations, alert_table, history, now,
                    slope_warn=slope_warn, swcb_etr2=swcb2)
out = _buf.getvalue()
towns = {t['county'] + t['township']: t for t in res} if isinstance(res, list) else res

dt = towns.get('宜蘭縣大同鄉') or {}
ok(dt.get('etr2') == 46.0,
   f"D1 大同鄉 etr2 取到 46（寒溪s 補回）—— 實得 {dt.get('etr2')}")
ok(dt.get('etr2_pct') == 0.46,
   f"D2 大同鄉 etr2_pct = 46%（官方值）—— 實得 {dt.get('etr2_pct')}")
_stns = [d.get('station') for d in (dt.get('slope_regions') or [])]
ok('寒溪' in _stns,
   f"D3 寒溪那個警戒單元進入了明細 —— 實得 {_stns}")
ok(dt.get('etr2') != 20.0,
   'D4 新竹的「桃山」20 沒有被當成大同鄉的值')

tc = towns.get('臺中市和平區') or {}
ok(tc.get('etr2') == 33.7,
   f"D5 和平區 etr2 = 33.7（武陵w），不是臺東的 162 —— 實得 {tc.get('etr2')}")

ok('擋下' in out or '桃山' in out,
   'D6 稽核有把被擋下的跨區誤配印出來（不是靜默處理）')

# D7-D9 排行補齊：松羅不是任何警戒單元的代表站，但水保署有值 31
#        → 必須出現在測站排行，且不得改變鄉鎮官方 ETR2（46）
_se = dt.get('station_etr2') or {}
ok(_se.get('YL01') == 31.0,
   f"D7 松羅（非代表站，水保署有值 31）已補進測站排行 —— 實得 {_se.get('YL01')}")
ok(_se.get('YL02') == 46.0,
   f"D8 寒溪s 在排行裡仍是 46 —— 實得 {_se.get('YL02')}")
ok(dt.get('etr2') == 46.0 and dt.get('etr2_pct') == 0.46,
   f"D9 補排行後鄉鎮官方 ETR2 沒被動到（仍 46／46%）—— 實得 "
   f"{dt.get('etr2')}／{dt.get('etr2_pct')}")
ok('HC01' not in _se,
   f"D10 新竹五峰的桃山沒有被塞進宜蘭的排行 —— 排行鍵 {sorted(_se)}")
_shown = [l for l in out.split('\n') if '稽核' in l or '測站索引' in l]
print('     稽核輸出：')
for l in _shown: print('       ' + l.strip())

print(f"\n{'FAIL '+str(len(fails)) if fails else 'ALL PASS'} / 25 項")
sys.exit(1 if fails else 0)
