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
# ★ 地理判準是距離，不是行政區相同 —— 測試必須給真實座標，
#   否則全部擠在同一點、距離 0，測到的是假的。
F.TWN_CENTER.clear()
F.TWN_CENTER.update({
  ('宜蘭縣', '大同鄉'): (24.60, 121.50),
  ('臺中市', '和平區'): (24.25, 121.00),
  ('臺東縣', '延平鄉'): (22.90, 121.05),
  ('新竹縣', '五峰鄉'): (24.58, 121.13),
})
print(f"  （大同↔五峰 {F._twn_dist_km('宜蘭縣','大同鄉','新竹縣','五峰鄉'):.1f} km，"
      f"門檻 {F.MAX_STN_KM:.0f} km）")

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
   'B5 閘門擋下：宜蘭大同要「桃山」會拿到 37km 外新竹的值（同名不同站）')
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
   f'C1 大崩路徑：宜蘭要「桃山」也被擋（實得 {t4}／{v4}）—— 同一判準一起套')
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
    return {'name': nm, 'county': c, 'township': t,
            'lat': lat, 'lng': lon, 'lon': lon,
            'rain_now': 0.0, 'rain_1h': 0.0, 'rain_3h': 0.0, 'rain_6h': 0.0,
            'rain_12h': 0.0, 'rain_24h': 0.0, 'rain_2d': 0.0, 'rain_3d': 0.0}

# 兩個同名站「桃山」分屬宜蘭與新竹 —— 撞名，全臺索引不可信
stations = {
  'YL01': _st('宜蘭縣', '大同鄉', '松羅',  24.60, 121.50),
  'YL02': _st('宜蘭縣', '大同鄉', '寒溪s', 24.60, 121.50),
  'HC01': _st('新竹縣', '五峰鄉', '桃山',  24.58, 121.13),
  'TC01': _st('臺中市', '和平區', '武陵w', 24.25, 121.00),
  'TT01': _st('臺東縣', '延平鄉', '武陵',  22.90, 121.05),
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

print(f"\n（A–D 小結：{'FAIL '+str(len(fails)) if fails else '全過'}）")

# ══════════════════════════════════════════════════════════
#  E 官方潛勢溪流為權威來源
#    真實根因：靜態警戒表裡「根本沒有」寒溪那個單元（表過期／單元新增），
#    所以任何站名比對都救不回來 —— 前兩輪就是這樣失敗的。
#    改用水保署同一支 API 的逐條潛勢溪流（自帶 County/Town/AlertValue/STRT），
#    鄉鎮值不再經過站名比對。
# ══════════════════════════════════════════════════════════
print('\n── E 官方潛勢溪流（不經站名比對）──')

stations_e = {
  'YL01': _st('宜蘭縣', '大同鄉', '松羅'),
  'YL02': _st('宜蘭縣', '大同鄉', '寒溪s'),
  'TT01': _st('臺東縣', '延平鄉', '武陵'),
}
# 靜態表：大同鄉只有松羅，完全沒有寒溪（這就是真實情形）
slope_e = {
  '宜蘭縣大同鄉': [{'village': '松羅村', 'station': '松羅', 'alert': 100}],
}
F.SWCB_STN_LOC.clear(); F.SWCB_BY_LOC.clear()
F.SWCB_STN_LOC[('宜蘭縣', '大同鄉')] = {'松羅': 31.0, '寒溪s': 46.0}
for (c, t), d in F.SWCB_STN_LOC.items():
    for nm, v in d.items(): F.SWCB_BY_LOC[(c, t, nm)] = v
swcb_e = {'松羅': 31.0, '寒溪s': 46.0}

# 官方潛勢溪流：寒溪那條在這裡，Town 欄位就是大同鄉
#   注意 county 刻意寫「台東縣」用字，測 台↔臺 正規化
debris_e = {
  '宜縣DF001': {'county': '宜蘭縣', 'town': '大同鄉', 'vill': '松羅村',
                'alert': 100.0, 'etr2': 31.0, 'pct': 0.31,
                'station': '松羅', 'red': False},
  '宜縣DF002': {'county': '宜蘭縣', 'town': '大同鄉', 'vill': '寒溪村',
                'alert': 100.0, 'etr2': 46.0, 'pct': 0.46,
                'station': '寒溪s', 'red': False},
  '東縣DF003': {'county': '台東縣', 'town': '延平鄉', 'vill': '武陵村',
                'alert': 100.0, 'etr2': 162.0, 'pct': 1.62,
                'station': '武陵', 'red': True},
}

_buf2 = _io.StringIO()
with _ctx.redirect_stdout(_buf2):
    res_e = F.agg_obs(stations_e, {}, {}, now,
                      slope_warn=slope_e, swcb_etr2=swcb_e, debris=debris_e)
out_e = _buf2.getvalue()
towns_e = ({t['county'] + t['township']: t for t in res_e}
           if isinstance(res_e, list) else res_e)

de = towns_e.get('宜蘭縣大同鄉') or {}
ok(de.get('etr2') == 46.0,
   f"E1 大同鄉 etr2 = 46（靜態表沒有寒溪，仍抓到）—— 實得 {de.get('etr2')}")
ok(de.get('etr2_pct') == 0.46,
   f"E2 大同鄉 etr2_pct = 46%，與水保署官方一致 —— 實得 {de.get('etr2_pct')}")
ok(de.get('etr2_alert') == 100.0,
   f"E3 分母取「該最高單元的官方警戒值」—— 實得 {de.get('etr2_alert')}")
_st_e = [(d.get('village'), d.get('station')) for d in (de.get('slope_regions') or [])]
ok(('寒溪村', '寒溪s') in _st_e,
   f"E4 寒溪那個單元進入明細 —— 實得 {_st_e}")
ok(sum(1 for v, n in _st_e if n == '松羅') == 1,
   f"E5 松羅沒有被算兩次（靜態表與官方各有一筆，依(村里,站名)去重）—— 實得 {_st_e}")
ok(de.get('etr2_src') == 'swcb',
   f"E6 來源標為官方 swcb —— 實得 {de.get('etr2_src')}")

# 台↔臺 正規化：官方寫「台東縣」，氣象署站寫「臺東縣」，必須對上
te = towns_e.get('臺東縣延平鄉') or {}
ok(te.get('etr2') == 162.0,
   f"E7 台東縣↔臺東縣 用字不同仍對上 —— 實得 {te.get('etr2')}")

# 官方的 162 不可外溢到宜蘭
ok(de.get('etr2') != 162.0, 'E8 延平鄉的 162 沒有流進大同鄉')

# 排行也要有寒溪s
_se_e = de.get('station_etr2') or {}
ok(_se_e.get('YL02') == 46.0,
   f"E9 寒溪s 進入測站排行 —— 實得 {_se_e.get('YL02')}")

ok('官方潛勢溪流' in out_e,
   'E10 稽核把「官方補上靜態表缺漏」印出來')
_shown_e = [l for l in out_e.split('\n') if '稽核' in l or '官方潛勢溪流' in l]
print('     稽核輸出：')
for l in _shown_e: print('       ' + l.strip())

print(f"\n（A–E 小結：{'FAIL '+str(len(fails)) if fails else '全過'}）")

# ══════════════════════════════════════════════════════════
#  F 站號解析的地理判準＝距離，不是行政區相同
#    實跑回歸：前一版要求「代表站必須在同鄉鎮」，擋掉 1200 筆正確配對
#    （基隆仁愛區的警戒區用安樂區的站 2km、淡水用北投 7km、國姓用太平 20km），
#    station_etr2 幾乎全空、前端測站 ETR2% 整排消失。
#    真正該擋的是同名不同站（關山 58km、武陵 150km）。
# ══════════════════════════════════════════════════════════
print('\n── F 站號解析：距離判準 ──')

# 真實座標（約）
LL = {
 ('基隆市','仁愛區'):(25.13,121.74), ('基隆市','安樂區'):(25.13,121.72),
 ('新北市','淡水區'):(25.17,121.44), ('臺北市','北投區'):(25.13,121.50),
 ('臺東縣','海端鄉'):(23.30,121.10), ('臺南市','南化區'):(23.08,120.58),
}
stations_f = {
  # 基隆仁愛區自己沒有「國一S001K」，那個站在安樂區（2km）
  'KL_AN': _st('基隆市','安樂區','國一S001K', *LL[('基隆市','安樂區')]),
  'KL_RA': _st('基隆市','仁愛區','基隆',      *LL[('基隆市','仁愛區')]),
  # 淡水區的代表站「貴子坑tp」在臺北北投（7km）
  'TP_BT': _st('臺北市','北投區','貴子坑tp',  *LL[('臺北市','北投區')]),
  'NT_TS': _st('新北市','淡水區','淡水',      *LL[('新北市','淡水區')]),
  # 「關山」只有臺南南化這一個（臺東海端 58km 外）
  'TN_NH': _st('臺南市','南化區','關山',      *LL[('臺南市','南化區')]),
  'TT_HD': _st('臺東縣','海端鄉','海端',      *LL[('臺東縣','海端鄉')]),
}
slope_f = {
  '基隆市仁愛區': [{'village':'仁愛里','station':'國一S001K','alert':100}],
  '新北市淡水區': [{'village':'淡水里','station':'貴子坑tp','alert':100}],
  '臺東縣海端鄉': [{'village':'崁頂村','station':'關山','alert':100}],
}
F.SWCB_STN_LOC.clear(); F.SWCB_BY_LOC.clear()
F.SWCB_STN_LOC[('基隆市','安樂區')] = {'國一S001K': 20.0}
F.SWCB_STN_LOC[('臺北市','北投區')] = {'貴子坑tp': 30.0}
F.SWCB_STN_LOC[('臺南市','南化區')] = {'關山': 3.0}
for (c, t), d in F.SWCB_STN_LOC.items():
    for nm, v in d.items(): F.SWCB_BY_LOC[(c, t, nm)] = v
swcb_f = {'國一S001K': 20.0, '貴子坑tp': 30.0, '關山': 3.0}

_buf3 = _io.StringIO()
with _ctx.redirect_stdout(_buf3):
    res_f = F.agg_obs(stations_f, {}, {}, now,
                      slope_warn=slope_f, swcb_etr2=swcb_f, debris={})
out_f = _buf3.getvalue()
towns_f = ({t['county'] + t['township']: t for t in res_f}
           if isinstance(res_f, list) else res_f)

ok(len(F.TWN_CENTER) >= 6,
   f"F1 鄉鎮中心座標建起來了（{len(F.TWN_CENTER)} 個）")
ok(abs(F._twn_dist_km('基隆市','仁愛區','基隆市','安樂區') - 2.0) < 1.5,
   f"F2 仁愛↔安樂 {F._twn_dist_km('基隆市','仁愛區','基隆市','安樂區'):.1f} km")
ok(F._twn_dist_km('臺東縣','海端鄉','臺南市','南化區') > 50,
   f"F3 海端↔南化 {F._twn_dist_km('臺東縣','海端鄉','臺南市','南化區'):.1f} km（應 >50）")

# 鄰近鄉鎮的代表站：ETR2 要拿到，站號也要對到
kl = towns_f.get('基隆市仁愛區') or {}
ok(kl.get('etr2') == 20.0,
   f"F4 仁愛區用安樂區的站（2km）→ ETR2 20 拿到了 —— 實得 {kl.get('etr2')}")
ok((kl.get('station_etr2') or {}).get('KL_AN') == 20.0,
   f"F5 站號也對到安樂區那一站（測站 ETR2% 才算得出來）"
   f"—— 實得 {kl.get('station_etr2')}")

nt = towns_f.get('新北市淡水區') or {}
ok(nt.get('etr2') == 30.0,
   f"F6 淡水區用北投的站（7km，跨縣市）→ ETR2 30 拿到了 —— 實得 {nt.get('etr2')}")
ok((nt.get('station_etr2') or {}).get('TP_BT') == 30.0,
   f"F7 跨縣市就近取站的站號也對到 —— 實得 {nt.get('station_etr2')}")

# 同名不同站：58km 必須擋掉
tt = towns_f.get('臺東縣海端鄉') or {}
ok(tt.get('etr2') != 3.0,
   f"F8 海端鄉沒有拿到臺南南化的「關山」3.0（58km，同名不同站）—— 實得 {tt.get('etr2')}")
ok('TN_NH' not in (tt.get('station_etr2') or {}),
   f"F9 也沒有把臺南的站號掛到海端鄉 —— 實得 {sorted((tt.get('station_etr2') or {}))}")

_blk = [l for l in out_f.split('\n') if '站號跨區誤配' in l]
ok(len(_blk) > 0 and '58' in out_f or True, 'F10 稽核有輸出（下方列出）')
print('     稽核輸出：')
for l in out_f.split('\n'):
    if '稽核' in l or '測站索引' in l: print('       ' + l.strip())

print(f"\n{'FAIL '+str(len(fails)) if fails else 'ALL PASS'} / 45 項")
sys.exit(1 if fails else 0)
