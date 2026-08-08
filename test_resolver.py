#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證 resolve_station_etr2() 的四層對站，重點在「不得跨區誤配」。"""
import os, json
os.environ.setdefault('CWA_API_KEY', 'dummy')
import fetch_rainfall as F

fails = []
def chk(label, got, exp):
    ok = got == exp
    if not ok: fails.append(label)
    print(f"  {'OK ' if ok else '!! '}{label}: {got!r}" + ("" if ok else f"  期望 {exp!r}"))

# 模擬水保署回傳：站名→ETR2，並建立鄉鎮位置索引
SW = {"六龜": 400.0, "林試六龜中": 380.0, "新發": 350.0, "新發國小s": 360.0,
      "大武": 100.0, "大武山": 900.0, "神山w": 200.0, "太平山": 50.0,
      "太平國小w": 240.0, "梨山": 120.0, "梨山部落s": 130.0}
F.SWCB_STN_LOC.clear()
F.SWCB_STN_LOC[("高雄市", "六龜區")] = {"六龜": 400.0, "林試六龜中": 380.0,
                                       "新發": 350.0, "新發國小s": 360.0}
F.SWCB_STN_LOC[("臺東縣", "大武鄉")] = {"大武": 100.0}
F.SWCB_STN_LOC[("屏東縣", "霧臺鄉")] = {"神山w": 200.0, "大武山": 900.0}
F.SWCB_STN_LOC[("宜蘭縣", "大同鄉")] = {"太平山": 50.0}
F.SWCB_STN_LOC[("花蓮縣", "卓溪鄉")] = {"太平國小w": 240.0}
F.SWCB_STN_LOC[("臺中市", "和平區")] = {"梨山": 120.0, "梨山部落s": 130.0}
R = F.resolve_station_etr2

print("=== 第1層 精確 ===")
chk("六龜", R(["六龜"], SW, "高雄市", "六龜區")[:2], (400.0, 'exact'))
chk("新發國小s", R(["新發國小s"], SW, "高雄市", "六龜區")[:2], (360.0, 'exact'))

print("\n=== 第2層 正規化（明細表帶機關代碼/序號）===")
chk("太平山(1)w → 太平山", R(["太平山(1)w"], SW, "宜蘭縣", "大同鄉")[:2], (50.0, 'norm'))
chk("神山w（原名在表中）", R(["神山w"], SW, "屏東縣", "霧臺鄉")[:2], (200.0, 'exact'))

print("\n=== 第3層 同鄉鎮相似（本次要新增的能力）===")
v, tier, nm = R(["六龜f"], SW, "高雄市", "六龜區")
chk("六龜f → 命中", (tier, nm), ('norm', '六龜'))
v, tier, nm = R(["竹林s"], SW, "高雄市", "六龜區")   # 表中無竹林 → 同鄉鎮找相似
print(f"   竹林s → tier={tier!r} matched={nm!r} val={v!r}（無足夠相似者時應回 None）")

print("\n=== ★ 不得跨區誤配 ===")
# 臺東大武鄉的「大武」不該配到屏東霧臺鄉的「大武山」
v, tier, nm = R(["大武"], SW, "臺東縣", "大武鄉")
chk("臺東大武 → 精確命中自己的站", (v, tier, nm), (100.0, 'exact', '大武'))
# 一個臺東的站名不存在時，不該跑去屏東抓「大武山」
v, tier, nm = R(["大武溪"], SW, "臺東縣", "大武鄉")
ok = (nm != '大武山')
if not ok: fails.append("跨縣誤配大武山")
print(f"  {'OK ' if ok else '!! '}臺東「大武溪」未配到屏東「大武山」：matched={nm!r} tier={tier!r}")
# 宜蘭「太平山」不該配到花蓮卓溪的「太平國小」
v, tier, nm = R(["太平山莊"], SW, "宜蘭縣", "大同鄉")
ok = (nm != '太平國小w')
if not ok: fails.append("跨縣誤配太平國小")
print(f"  {'OK ' if ok else '!! '}宜蘭「太平山莊」未配到花蓮「太平國小」：matched={nm!r} tier={tier!r}")

print("\n=== 單字名不做子字串配對（避免亂配）===")
v, tier, nm = R(["山"], SW, "屏東縣", "霧臺鄉")
ok = tier in ('', 'near_town', 'near_county') and nm != '大武山' or v is None
print(f"   單字「山」→ tier={tier!r} matched={nm!r}（不應以子字串配到大武山）")
if nm == '大武山': fails.append("單字誤配")

print("\n=== 全不中 ===")
chk("完全陌生的站名+無縣市", R(["完全不存在的站"], SW)[:2], (None, ''))
chk("空清單", R([], SW, "高雄市", "六龜區")[:2], (None, ''))
chk("None 元素安全", R([None, ''], SW, "高雄市", "六龜區")[:2], (None, ''))

print("\n=== 用真實 94 處代表站清單跑一遍，看層級分布 ===")
tbl = json.load(open('landslide_warning_stations.json'))
# 以土石流代表站清單當作可得的即時站池（近似真實情況）
sl = json.load(open('/mnt/user-data/uploads/slope_warning_stations.json'))['townships']
pool, loc = {}, {}
for k, rows in sl.items():
    for r in rows:
        pool[r['station']] = 100.0
        pool.setdefault(r.get('station_norm', ''), 100.0)
F.SWCB_STN_LOC.clear()
for k, rows in sl.items():
    # k 形如「臺東縣大武鄉」——切出縣市（3字）與鄉鎮
    cty, twn = k[:3], k[3:]
    F.SWCB_STN_LOC.setdefault((cty, twn), {})
    for r in rows:
        F.SWCB_STN_LOC[(cty, twn)][r['station']] = 100.0
tiers = {}
still_none = []
for z in tbl['zones']:
    v, tier, nm = R([z['station1'], z['station2'],
                     z['station1_norm'], z['station2_norm']], pool, z['county'], z['town'])
    key = tier or 'none'
    tiers[key] = tiers.get(key, 0) + z['n_zones']
    if v is None: still_none.append(f"{z['county']}{z['town']}{z['village']}")
print("   層級分布（依潛勢區數）:", {F._LS_TIER_NAME.get(k, k): n for k, n in tiers.items()})
print(f"   仍需退回鄉鎮值：{len(still_none)} 列")
for s in still_none[:10]: print("     -", s)

print("\n全部通過" if not fails else f"\n失敗 {len(fails)} 項：{fails}")
raise SystemExit(1 if fails else 0)
