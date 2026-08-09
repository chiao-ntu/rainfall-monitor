#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 terrain_zones.json 的四分類套進兩個前端檔案。

  taiwan_blank_map.html  → 改寫 TOWN_TIER（原七類併為四類）
  index.html             → 改寫 TOWN_ZONE（情境編輯器的地形群組來源）

四分類：山區 / 淺山區 / 沿海地區 / 平地
原七類的併法（使用者指示「併掉」）：
  山區            → 山區
  淺山            → 淺山區
  沿海／北海岸／東北角／恆春半島 → 沿海地區
  平地            → 平地
但**最終分類一律以 terrain_zones.json 的海拔＋臨海判定為準**，
上面的併法只用於「terrain_zones.json 缺該鄉鎮」時的退路（並會列出來）。

用法：
    python3 build_coastal.py
    python3 build_terrain_zones.py
    python3 apply_terrain_zones.py
"""
import io, json, os, re, sys

ZONES = 'terrain_zones.json'
MAP_FILE = 'taiwan_blank_map.html'
IDX_FILE = 'index.html'
VALID = ['山區', '淺山區', '沿海地區', '平地']
LEGACY_MERGE = {'山區': '山區', '淺山': '淺山區', '沿海': '沿海地區',
                '北海岸': '沿海地區', '東北角': '沿海地區', '恆春半島': '沿海地區',
                '平地': '平地'}


def read_legacy_tier(s):
    """讀 taiwan_blank_map.html 既有的 TOWN_TIER（供退路與比對用）。"""
    m = re.search(r'const TOWN_TIER\s*=\s*\{(.*?)\n\};', s, re.S)
    if not m:
        return {}, None
    body = m.group(1)
    out = {}
    for c, t, z in re.findall(r"'([^']+),([^']+)'\s*:\s*'([^']+)'", body):
        out[c + t] = z
    return out, m


def main():
    if not os.path.exists(ZONES):
        sys.exit(f'找不到 {ZONES}——請先執行 build_terrain_zones.py（需網路）')
    zs = json.load(io.open(ZONES, encoding='utf-8'))

    # 檢查資料完整性：zone 為 null 者不可套用（不猜）
    nulls = [k for k, v in zs.items() if not v.get('zone')]
    if nulls:
        print(f'★ {len(nulls)} 個鄉鎮 zone 為 null（海拔取不到），不會被寫入：')
        print('   ' + '、'.join(nulls[:15]))
        print('   請重跑 build_terrain_zones.py 補齊後再執行本腳本。')
    bad = [k for k, v in zs.items() if v.get('zone') and v['zone'] not in VALID]
    if bad:
        sys.exit(f'分類值不在四類之內：{bad[:5]}')

    stats = {}
    for v in zs.values():
        if v.get('zone'): stats[v['zone']] = stats.get(v['zone'], 0) + 1
    print(f'terrain_zones.json：{len(zs)} 筆，分類統計 {stats}')

    # ── 1. taiwan_blank_map.html 的 TOWN_TIER ──
    s = io.open(MAP_FILE, encoding='utf-8').read()
    legacy, m = read_legacy_tier(s)
    if not m:
        sys.exit(f'{MAP_FILE} 找不到 TOWN_TIER，請確認檔案版本')
    print(f'{MAP_FILE} 既有 TOWN_TIER：{len(legacy)} 筆')

    lines, used_legacy = [], []
    for key in sorted(zs.keys()):
        v = zs[key]
        z = v.get('zone')
        if not z:
            lz = legacy.get(key)
            z = LEGACY_MERGE.get(lz) if lz else None
            if z: used_legacy.append(f'{key}({lz}→{z})')
        if not z:
            continue
        lines.append(f"  '{v['county']},{v['town']}':'{z}',")
    if used_legacy:
        print(f'  以舊分類併入退路填補 {len(used_legacy)} 筆：' + '、'.join(used_legacy[:8]))

    new_block = 'const TOWN_TIER = {\n' + '\n'.join(lines) + '\n};'
    s2 = s[:m.start()] + new_block + s[m.end():]
    io.open(MAP_FILE, 'w', encoding='utf-8').write(s2)
    print(f'  已寫入 {len(lines)} 筆 → {MAP_FILE}')

    # ── 2. index.html 的 TOWN_ZONE（情境編輯器地形群組） ──
    idx = io.open(IDX_FILE, encoding='utf-8').read()
    zone_js = 'const TOWN_ZONE = {' + ','.join(
        f'"{k}":"{zs[k]["zone"]}"' for k in sorted(zs) if zs[k].get('zone')) + '};'
    if 'const TOWN_ZONE = {' in idx:
        idx = re.sub(r'const TOWN_ZONE = \{.*?\};', zone_js, idx, count=1, flags=re.S)
        print(f'  已更新 index.html 的 TOWN_ZONE')
    else:
        anchor = 'const SCN_TERRAIN'
        if anchor not in idx:
            sys.exit('index.html 找不到 SCN_TERRAIN，請確認檔案版本')
        idx = idx.replace(anchor, zone_js + '\n' + anchor, 1)
        print(f'  已插入 index.html 的 TOWN_ZONE')
    io.open(IDX_FILE, 'w', encoding='utf-8').write(idx)

    print('\n完成。請接著跑 verify_html.py 與前端測試確認未破壞其他功能。')


if __name__ == '__main__':
    main()
