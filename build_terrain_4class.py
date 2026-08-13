#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由既有七類特性分區建立四類地形對照表（山區／淺山區／沿海地區／平地）。

不使用海拔 API——依使用者指示，以既有人工分類為基礎做規則轉換：

基本映射：
    山區   → 山區
    淺山   → 淺山區
    沿海   → 沿海地區
    平地   → 平地
    北海岸   → 沿海地區（併入該縣市沿海）
    恆春半島 → 沿海地區（併入該縣市沿海）
    東北角   → 逐一指定（見 OVERRIDE，該類跨新北與基隆，無單一歸屬）

使用者逐一指定（OVERRIDE，優先於基本映射）：
    新北 瑞芳、貢寮、雙溪            → 淺山區
    基隆 中正、中山、信義、仁愛      → 沿海地區（臨海港區）
    基隆 安樂、暖暖、七堵            → 淺山區（內陸丘陵）
    宜蘭 頭城、礁溪                  → 淺山區（地區特性近淺山，非沿海）

輸出：terrain_zones_4class.json  {"縣市鄉鎮": "分類"}
      並同步寫入 index.html 的 TOWN_ZONE 與 taiwan_blank_map.html 的 TOWN_TIER。
"""
import io, json, re, sys

MAP_FILE = 'taiwan_blank_map.html'
IDX_FILE = 'index.html'
OUT_FILE = 'terrain_zones_4class.json'
VALID = ['山區', '淺山區', '沿海地區', '平地']

BASE = {'山區': '山區', '淺山': '淺山區', '沿海': '沿海地區', '平地': '平地',
        '北海岸': '沿海地區', '恆春半島': '沿海地區'}

OVERRIDE = {
    # 東北角（新北）→ 淺山區
    '新北市瑞芳區': '淺山區', '新北市貢寮區': '淺山區', '新北市雙溪區': '淺山區',
    # 東北角（基隆）→ 臨海港區歸沿海、內陸丘陵歸淺山
    '基隆市中正區': '沿海地區', '基隆市中山區': '沿海地區',
    '基隆市信義區': '沿海地區', '基隆市仁愛區': '沿海地區',
    '基隆市安樂區': '淺山區', '基隆市暖暖區': '淺山區', '基隆市七堵區': '淺山區',
    # 宜蘭（使用者指定改為淺山區）
    '宜蘭縣頭城鎮': '淺山區', '宜蘭縣礁溪鄉': '淺山區',
}


def read_tier(path=MAP_FILE):
    s = io.open(path, encoding='utf-8').read()
    m = re.search(r'const TOWN_TIER\s*=\s*\{(.*?)\n\};', s, re.S)
    if not m:
        sys.exit(f'{path} 找不到 TOWN_TIER')
    pairs = re.findall(r"'([^']+),([^']+)':'([^']+)'", m.group(1))
    return s, m, pairs


def main():
    s, m, pairs = read_tier()
    print(f'既有 TOWN_TIER：{len(pairs)} 筆')

    zone, unmapped, used_ov = {}, [], []
    for c, t, old in pairs:
        key = c + t
        if key in OVERRIDE:
            zone[key] = OVERRIDE[key]
            used_ov.append(f'{key}（{old}→{zone[key]}）')
        elif old in BASE:
            zone[key] = BASE[old]
        else:
            unmapped.append(f'{key}({old})')

    # ── 檢核：不得有未分類、不得有非法類別、OVERRIDE 必須全部命中 ──
    if unmapped:
        sys.exit('★ 有未映射的舊分類，請補 BASE：' + '、'.join(unmapped))
    bad = {k: v for k, v in zone.items() if v not in VALID}
    if bad:
        sys.exit(f'★ 非法分類值：{list(bad.items())[:5]}')
    missed = [k for k in OVERRIDE if k not in zone]
    if missed:
        sys.exit('★ OVERRIDE 有鄉鎮不存在於 TOWN_TIER（名稱可能不符）：' + '、'.join(missed))
    print(f'逐一指定生效 {len(used_ov)} 筆：')
    for u in used_ov:
        print('   ', u)

    stats = {}
    for v in zone.values():
        stats[v] = stats.get(v, 0) + 1
    print(f'\n四類統計：{stats}（合計 {sum(stats.values())}）')

    # 宜蘭全縣覆核（使用者特別指定的縣）
    print('\n宜蘭縣覆核：')
    for k in sorted(k for k in zone if k.startswith('宜蘭縣')):
        print(f'   {k[3:]:5s} → {zone[k]}')
    # 基隆全市覆核
    print('基隆市覆核：')
    for k in sorted(k for k in zone if k.startswith('基隆市')):
        print(f'   {k[3:]:5s} → {zone[k]}')

    json.dump(zone, io.open(OUT_FILE, 'w', encoding='utf-8'),
              ensure_ascii=False, indent=0, sort_keys=True)
    print(f'\n已寫出 {OUT_FILE}')

    # ── 寫入 taiwan_blank_map.html 的 TOWN_TIER（改為四類）──
    lines = []
    for c, t, _ in pairs:
        lines.append(f"  '{c},{t}':'{zone[c+t]}',")
    new_block = 'const TOWN_TIER = {\n' + '\n'.join(lines) + '\n};'
    io.open(MAP_FILE, 'w', encoding='utf-8').write(s[:m.start()] + new_block + s[m.end():])
    print(f'已更新 {MAP_FILE} 的 TOWN_TIER（{len(lines)} 筆，四類）')

    # ── 寫入 index.html 的 TOWN_ZONE ──
    idx = io.open(IDX_FILE, encoding='utf-8').read()
    js = 'const TOWN_ZONE = {' + ','.join(
        f'"{k}":"{zone[k]}"' for k in sorted(zone)) + '};'
    if 'const TOWN_ZONE = {' in idx:
        idx = re.sub(r'const TOWN_ZONE = \{.*?\};', js, idx, count=1, flags=re.S)
        print('已更新 index.html 的 TOWN_ZONE')
    else:
        sys.exit('index.html 找不到 TOWN_ZONE')
    # SCN_TERRAIN 改回四類（順序依使用者指定：山區、淺山區、平地、沿海地區）
    old_t = "const SCN_TERRAIN = ['山區', '平地'];"
    new_t = "const SCN_TERRAIN = ['山區', '淺山區', '平地', '沿海地區'];"
    if old_t in idx:
        idx = idx.replace(old_t, new_t)
        print('SCN_TERRAIN → 四類（山區／淺山區／平地／沿海地區）')
    elif new_t in idx:
        print('SCN_TERRAIN 已是四類')
    else:
        print('★ 未找到 SCN_TERRAIN 定義，請手動確認')
    io.open(IDX_FILE, 'w', encoding='utf-8').write(idx)
    print('\n完成。請執行 verify_html.py 與前端測試。')


if __name__ == '__main__':
    main()
