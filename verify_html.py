#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""index.html 改動後的必驗清單（傳承文件開發鐵律）。
  1. JS 語法（抽出 <script> 後 node --check）
  2. 關鍵函式仍存在、且無重複定義
  3. </html> 存在
  4. 關鍵功能字串仍在
絕不 print index.html 全文（內嵌 GeoJSON 會爆輸出）。"""
import io, re, subprocess, sys

P = 'index.html'
s = io.open(P, encoding='utf-8').read()
fail = []

# --- 1. 抽 script 做語法檢查 ---
blocks = re.findall(r'<script[^>]*>(.*?)</script>', s, re.S)
js = '\n;\n'.join(blocks)
io.open('_extracted.js', 'w', encoding='utf-8').write(js)
r = subprocess.run(['node', '--check', '_extracted.js'], capture_output=True, text=True)
if r.returncode != 0:
    fail.append('JS 語法錯誤')
    print('!! JS syntax:\n', r.stderr[:2000])
else:
    print(f'OK  JS 語法（{len(blocks)} 個 script 區塊，{len(js)} 字元）')

# --- 2. 關鍵函式存在且不重複 ---
MUST = ['getAccum', 'setWin', 'onSlider', '_spanAccum', '_hourlyBars', '_futuHourly',
        'renderLayer', 'renderLegend', 'toggleTyphoonLayer', 'renderTyphoonLayer',
        'renderTyphoonLegend', '_tyKeyPoints', '_tyInterp', '_distToTaiwanKm', '_twLandPts',
        'updateTyphoonPanel', 'copyTyphoonPanel', 'downloadTyphoonCsv',
        'toggleDisasterLayer', 'renderDisasterLayer',
        'toggleDebrisLayer', 'renderDebrisLayer', 'updateDebrisPanel',
        'toggleLandslideLayer', 'renderLandslideLayer', 'updateLandslidePanel',
        'toggleLsbSection', 'calcEtr2AtSeg', '_etrDen', '_nowSeg', 'getQpfArr',
        'toggleTyphoonLegend', '_applyTyphoonLegendCollapse', '_summaryRain',
        '_inWarnScope', '_seaLineSegs', '_countiesInRadius', '_twGrid',
        'downloadRangeCsv', 'toggleMapPanel',
        'scnUndo', 'scnRedo', 'scnPushUndo', '_scnApplyState', '_scnRowFocus',
        'scnToggleFold', '_slopeEstJS', '_withEst', '_nowHourClamped', '_townZone']
for fn in MUST:
    n = len(re.findall(r'^\s*(?:async\s+)?function\s+' + re.escape(fn) + r'\s*\(', s, re.M))
    if n == 0:
        fail.append(f'缺函式 {fn}'); print(f'!! 缺函式 {fn}')
    elif n > 1:
        fail.append(f'重複定義 {fn}'); print(f'!! 重複定義 {fn} ×{n}')
print(f'OK  {len(MUST)} 個關鍵函式各恰好定義一次' if not fail else '')

# --- 3. 結構完整 ---
for tag in ['</html>', '</body>', '<div id="map"']:
    if tag not in s:
        fail.append(f'缺 {tag}'); print(f'!! 缺 {tag}')

# --- 4. 關鍵功能字串 ---
MUST_STR = ['TOWN_GEO', 'TYPHOON_TRACK', 'DEBRIS_ALERTS', 'typhoon-panel-body',
            'typhoon-legend-wrap', 'typhoon-legend-toggle', 'TW_WARN_EXCLUDE',
            'debris-panel-body', 'landslide-panel-body', 'typhoon-legend',
            'cust-ctrl', 'slS', 'slE', '颱風動態', 'ETR2']
for k in MUST_STR:
    if k not in s:
        fail.append(f'缺字串 {k}'); print(f'!! 缺字串 {k}')

# --- 5. 不該殘留的舊名 ---
stale = re.findall(r'🌀 颱風路徑', s)
if stale:
    fail.append('殘留舊名「🌀 颱風路徑」'); print('!! 殘留舊名 ×', len(stale))

# --- 6. 漏空格的宣告（node --check 抓不到：`const深 = {}` 會變成隱式全域）---
#   實際踩過：`const深 = {}` 語法合法，但宣告的是名為 const深 的變數，
#   後續引用 深 會 ReferenceError。只有執行期測試才會發現，故在此靜態掃描。
for m in re.finditer(r'\b(const|let|var)([^\sA-Za-z_$\(\[\{=/（\-])', js):
    line = js[:m.start()].count('\n') + 1
    fail.append(f'第{line}行 疑似漏空格宣告：{m.group(0)!r}')
    print(f'!! 疑似漏空格宣告（第{line}行，抽出的JS）：{m.group(0)!r}')

print(f'\n檔案 {len(s)//1024}KB、{s.count(chr(10))+1} 行')
print('=== 全部通過 ===' if not fail else f'=== 失敗 {len(fail)} 項：{fail} ===')
sys.exit(1 if fail else 0)
