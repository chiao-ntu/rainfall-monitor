"""繪圖地圖（taiwan_blank_map.html）的地形顯示必須與 index.html 一致（2026-11-26 使用者指定）。

先前兩頁各自調整：index 改成雙層暈渲（下層加強對比、上層色彩增值陰影），
繪圖地圖仍是單層半透明，畫面偏灰、立體感弱。這支測試把會影響外觀的參數逐一對照，
任何一頁單獨改動都會在這裡失敗。
"""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
idx = open(os.path.join(HERE, 'index.html'), encoding='utf-8').read()
blk = open(os.path.join(HERE, 'taiwan_blank_map.html'), encoding='utf-8').read()

fails, n = [], 0
def ok(c, m):
    global n
    n += 1
    print(('OK  ' if c else '!!  ') + m)
    if not c: fails.append(m)

def css(src, cls):
    m = re.search(r'\.' + re.escape(cls) + r'\{([^}]*)\}', src)
    return m.group(1).strip() if m else None

for cls in ('terrain-relief', 'terrain-relief-top'):
    a, b = css(idx, cls), css(blk, cls)
    ok(a is not None and a == b, f'.{cls} 濾鏡相同（index：{a}／繪圖地圖：{b}）')

def const(src, name):
    m = re.search(r'const ' + name + r'\s*=\s*([\d.]+)', src)
    return float(m.group(1)) if m else None
ok(const(idx, 'TERR_TOP_OP') == const(blk, 'TERR_TOP_OP') is not None,
   f'上層陰影不透明度相同（{const(idx, "TERR_TOP_OP")}／{const(blk, "TERR_TOP_OP")}）')

def relief(src):
    m = re.search(r'const TERRAIN_RELIEF = (\{.*?\});', src)
    return m.group(1) if m else None
ok(relief(idx) is not None and relief(idx) == relief(blk), '使用同一張暈渲圖與同一組範圍參數')

for src, lab in ((idx, 'index'), (blk, '繪圖地圖')):
    ok("style.mixBlendMode = 'multiply'" in src and re.search(r"terrainTopPane'\)[\s\S]{0,200}zIndex = 397", src)
       and re.search(r"terrainPane'\)\.style\.zIndex = 392", src),
       f'{lab}：雙層暈渲（下層 392、上層 397 色彩增值）')
ok(re.search(r"className: 'terrain-relief'", blk) and re.search(r"opacity: 1, pane: 'terrainPane'", blk),
   '繪圖地圖：下層暈渲全不透明並套用加強對比濾鏡（同 index）')
ok("(_terrainOn ? 1.0 : 0.78)" in blk and "(_terrainOn ? '#FFFFFF'" in blk and "_terrainOn ? '#8e8e8e'" in blk,
   '繪圖地圖：地形開啟時色塊不透明、未塗色處純白、邊框 #8e8e8e（同 index）')
ok(re.search(r"const _op = 1\.0;", blk) is not None, '繪圖地圖：渲染模式不再以半透明透出地形（同 index）')

print(f"\n{'FAIL ' + str(len(fails)) if fails else 'ALL PASS'} / {n} 項")
sys.exit(1 if fails else 0)
