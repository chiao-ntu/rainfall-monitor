#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FORMOSA 前端全檔靜態稽核
=========================================================================
用途：避免「使用者指出哪裡、就只修哪裡」。把這個專案實際發生過的失效型態
      寫成掃描規則，一次掃過整份 index.html，列出**所有**同類位置。

失效型態（每一條都對應一次實際事故）：
  [P] 平行實作      同一計算多份，日後逐漸分歧（本專案最主要的失效模式）
  [N] null 當成 0   Math.min(null,x)===0 / null>0===false / fill(0) / (v||0)
  [A] 聚合母體會變  max over「有值的子集」，覆蓋率一變，值就塌下去再跳回來
  [G] 幾何各寫一份  tooltip 自己寫死邊距，與圖實際的 pL/pR/格數對不上
  [T] 畫布文字重疊  同一列左右對齊的 fillText 互撞
  [C] 觀看者時鐘    Date.now() 當「過去第 h 小時」的原點

用法：python3 audit_frontend.py [index.html]
      結尾會給一行總結；CI 可用離開碼判定（有 FAIL 類即非 0）。
"""
import re, sys
from collections import OrderedDict

SRC = sys.argv[1] if len(sys.argv) > 1 else 'index.html'
text = open(SRC, encoding='utf-8').read()
lines = text.split('\n')

results = OrderedDict()


def section(code, title):
    results[code] = {'title': title, 'items': [], 'fail': 0}
    return results[code]


def item(code, status, msg, ln=None):
    """status: OK / FAIL / INFO"""
    results[code]['items'].append((status, msg, ln))
    if status == 'FAIL':
        results[code]['fail'] += 1


def count(pat):
    return len(re.findall(pat, text))


# ── [P] 核心概念的實作份數 ────────────────────────────────────────
section('P', '[P] 平行實作：同一概念應只有一份實作')
SINGLE = {
    '折線（可含 null）': r'function _strokeNullableSeries\(',
    '軸上限（忽略 null）': r'function _axisMaxOfNullable\(',
    '軸刻度整數化':       r'function _niceAxisMax\(',
    '左右軸標題排版':     r'function _drawAxisTitles\(',
    '聚合覆蓋率閘門':     r'function _gateByCoverage\(',
    '圖面幾何登記':       r'function _registerChartGeom\(',
    '幾何→索引換算':      r'function _geomIndexAt\(',
    'ETR2 逐時序列':      r'function _etr2HourlySeries\(',
    'ETR2 段值（權威）':  r'function calcEtr2AtSeg\(',
    'ETR2% 取值入口':     r'function _etrPctAt\(',
    '逐時長條':           r'function _hourlyBars\(',
    '觀測（時間鍵對齊）': r'function _obsAtHour\(',
}
for name, pat in SINGLE.items():
    n = count(pat)
    item('P', 'OK' if n == 1 else 'FAIL', f'{name}：{n} 份' + ('' if n == 1 else '（應為 1）'))

# 畫 ETR2 折線的圖，全部都必須走共用折線工具
stroke_use = count(r'_strokeNullableSeries\(') - 1
item('P', 'OK' if stroke_use >= 5 else 'FAIL',
     f'走共用折線工具的圖：{stroke_use} 張（鄉鎮逐時/逐日、測站、分署逐時/逐日，應 ≥5）')

# 手寫折線迴圈（會把 null 連成線）
HAND = [
    (r'etr\.forEach\(\(v,i\)=>\{\s*const x=pL\+i\*itemW[^}]*i===0\?ctx\.moveTo', '_drawTownChart'),
    (r'etrRows\[di\]\.forEach\(\(v,i\)=>\{\s*const x=pL\+i\*itemW[^}]*i===0\?ctx\.moveTo', '_drawDistrictChart'),
    (r'etr\.forEach\(\(ev,i\)=>\{[\s\S]{0,120}i===0\?ctx\.moveTo', '測站圖'),
]
for pat, who in HAND:
    item('P', 'FAIL' if re.search(pat, text) else 'OK',
         f'{who}：手寫折線迴圈' + ('又出現了' if re.search(pat, text) else '已收斂'))

# ── [N] null 當成 0 ──────────────────────────────────────────────
section('N', '[N] null 被當成 0（比留白更危險：看起來像真的是 0）')
NULL_PATTERNS = [
    (r'const ee=new Array\(n\)\.fill\(0\)', '_calcDistrictHourly 的 ETR 聚合 fill(0)'),
    (r'let mxR=0,mxE=0', '分署逐日／組體聚合 mxE=0'),
    (r'const axisMaxEtr\s*=Math\.max\(ceilTo10', 'ETR 軸上限直接用 ceilTo10（null 會壓扁軸）'),
    (r'y=pT\+ch\*\(1-v/axisMaxEtr\)[^;]*;\s*i===0\?', '折線以 v/axisMax 計算 y（null→0→貼地）'),
]
for pat, who in NULL_PATTERNS:
    item('N', 'FAIL' if re.search(pat, text) else 'OK',
         who + ('　← 復發' if re.search(pat, text) else '　已修'))

n_null_init = count(r'let mxR=0,mxE=null')
item('N', 'OK' if n_null_init >= 3 else 'FAIL',
     f'null 起始的分署聚合：{n_null_init} 處（分署逐日／組體／組體 tooltip，應 ≥3）')

# 缺資料不得當成「沒下雨的證據」
item('N', 'OK' if re.search(r'if\(nObs < 3 && today == null\) return false;', text) else 'FAIL',
     '_noRainEvidence：缺觀測時不得判定「沒雨」（會在融合時剔除高量模式）')

# ── [A] 聚合母體隨覆蓋率變動 ─────────────────────────────────────
section('A', '[A] 聚合母體會變：子集取最大必定低估真值')
gate_use = count(r'_gateByCoverage\(') - 1
item('A', 'OK' if gate_use >= 3 else 'FAIL',
     f'套用覆蓋率閘門的聚合：{gate_use} 處（分署逐時／逐日／組體，應 ≥3）')
item('A', 'OK' if re.search(r'const AGG_COV_MIN = [0-9.]+', text) else 'FAIL',
     '覆蓋率門檻為單一常數（AGG_COV_MIN）')
item('A', 'OK' if re.search(r'nTot>0 && nE < nTot\*AGG_COV_MIN', text) else 'FAIL',
     'tooltip 的後備聚合也套用同一門檻（否則線斷了、tooltip 卻有數字）')

# ── [G] tooltip 幾何 ────────────────────────────────────────────
section('G', '[G] tooltip 幾何必須取自圖本身')
reg = count(r'_registerChartGeom\(') - 1
item('G', 'OK' if reg >= 4 else 'FAIL', f'登記幾何的圖：{reg} 張（應 ≥4）')
hard = count(r'const pL=isZoom\?72:34, pR=isZoom\?72:34;')
item('G', 'OK' if hard == 0 else 'FAIL', f'tooltip 寫死邊距：{hard} 處（應 0）')
for kind in ('hourly', 'district-hourly'):
    item('G', 'OK' if f"_G.kind==='{kind}'" in text else 'FAIL',
         f"tooltip 有 '{kind}' 專屬分派")

# ── [T] 畫布文字重疊 ────────────────────────────────────────────
section('T', '[T] 左右軸標題同列不得重疊')
raw = count(r"ctx\.fillText\('左軸：[^']*',\s*pL\s*,\s*axTitleY\)")
item('T', 'OK' if raw == 0 else 'FAIL', f'未經量測直接畫的軸標題：{raw} 處（應 0）')
n_axtitle = count(r'_drawAxisTitles\(') - 1
item('T', 'OK' if n_axtitle >= 3 else 'FAIL',
     f'走共用排版的軸標題：{n_axtitle} 處（應 ≥3）')

# ── [F] 補值來源必須標示 ────────────────────────────────────────
section('F', '[F] 推算值與官方實測必須分得出來')
item('F', 'OK' if count(r'function _etr2IsFill\(') == 1 else 'FAIL',
     '_etr2IsFill 恰好一份')
item('F', 'OK' if count(r'function _etr2FillMaskHourly\(') == 0 else 'FAIL',
     '逐時遮罩沒有另一份平行實作（應由 _etr2HourlySeries 的 outFill 輸出）')
item('F', 'OK' if 'outFill' in text else 'FAIL',
     '_etr2HourlySeries 以輸出參數回傳補值遮罩（遮罩與序列同一趟算出）')
item('F', 'OK' if re.search(r'ctx\.setLineDash\(\[w \* 2\.2', text) else 'FAIL',
     '折線工具會把推算段畫成虛線')
n_fillarg = len(re.findall(r'_strokeNullableSeries\(ctx, [^;]*?,\s*(?:_etrFill|etrFill|\(fillRows\|\|\[\]\)\[d[i]?\])\)',
                           text, re.S))
item('F', 'OK' if n_fillarg >= 5 else 'FAIL',
     f'傳入補值遮罩的折線呼叫：{n_fillarg} 處（五張圖都要，應 ≥5）')
item('F', 'OK' if '虛線＝' in text else 'FAIL', '軸標題說明虛線語意')
item('F', 'OK' if '推算' in text else 'FAIL', 'tooltip 標示推算值')

# ── [U] 單位一致性 ──────────────────────────────────────────────
section('U', '[U] etr2_pct 全系統只能有一種單位')
item('U', 'OK' if count(r'function _normalizeEtrPct\(') == 1 else 'FAIL',
     '_normalizeEtrPct 恰好一份（由 ETR2/警戒值 現算，不靠數值大小猜單位）')
n_norm = count(r'_normalizeEtrPct\(\)') - 1
item('U', 'OK' if n_norm >= 3 else 'FAIL',
     f'正規化呼叫點：{n_norm} 處（內建資料、data.json、etr2_now 三處都要）')
item('U', 'OK' if not re.search(r'const _list = \(window\.TOWNSHIPS', text) else 'FAIL',
     '不得用 window.TOWNSHIPS（const 宣告不會掛上 window，會靜默跳過全部）')

# ── [C] 觀看者時鐘當原點 ────────────────────────────────────────
section('C', '[C] Date.now() 當 h 偏移原點（僅「距現在第 h 小時」語意才正確）')
funcs = [(i + 1, m.group(1)) for i, L in enumerate(lines)
         if (m := re.match(r'\s*function\s+([A-Za-z_$][\w$]*)', L))]


def owner(ln):
    best = ('?', 0)
    for s, nm in funcs:
        if s <= ln and s > best[1]:
            best = (nm, s)
    return best[0]


# 判準：該處的 h／v／_hourIdx 的語意是否為「距現在幾小時」。
#   是 → Date.now() 正是正確原點。
#   否（例如「距今日 00 時幾小時」）→ 必須用 SEG_EPOCH()，否則觀看者
#        時鐘一變，同一個索引指到不同時刻。
# 下列函式已於 2026-10-09 逐一讀過原始碼確認語意為「距現在」。
# 新增的位置不在名單內就會被標為未通過，必須先判定語意再決定是否加入。
UI_RELATIVE = {
    '_obsHourlyAt',      # h<0＝前 h 小時的實測，_obsAtHour 以實際時間鍵對齊
    '_threeHourAt',      # 風力／氣溫／浪高，h＝距現在
    '_tyAnimSetHour', '_tyAnimLoop', '_tyDrawAt',   # 颱風動畫，註解明示「距現在 h 小時」
    '_animApply', '_bgWait',                        # 動畫幀時戳
    'setWin', 'onSlider', 'segLabel',               # 時間視窗／滑桿，v＝距現在
    'buildFuture1hBtns', '_syncH1Label',            # +Nh 按鈕標籤
    'openTimePicker', 'buildSliderTicks', '_syncTimeNav',
    '_coverageWarn',     # 以 _hourIdx（距現在）標示目前選定時刻
    'drawEtrWaterfall',  # firstCross＝「+Nh 後達警戒」，標籤同時印出 +Nh
    '_windAtMs',         # _hourIdx 分支；無 _hourIdx 時已改走 SEG_EPOCH
}
for i, L in enumerate(lines):
    if re.search(r'Date\.now\(\)\s*\+\s*[A-Za-z_$][\w$]*\s*\*\s*3600e3', L):
        fn = owner(i + 1)
        item('C', 'OK' if fn in UI_RELATIVE else 'FAIL',
             f'{fn}' + ('（h＝距現在，原點正確）' if fn in UI_RELATIVE
                        else '　← 需確認 h 的基準是否為 SEG_EPOCH'), i + 1)

# ── 報表 ─────────────────────────────────────────────────────────
MARK = {'OK': '  OK  ', 'FAIL': '  !!  ', 'INFO': '  ··  '}
total_fail = 0
for code, sec in results.items():
    print(f'\n{"=" * 74}\n{sec["title"]}\n{"=" * 74}')
    for status, msg, ln in sec['items']:
        loc = f'L{ln}' if ln else ''
        print(f'{MARK[status]}{msg} {loc}')
    total_fail += sec['fail']

print(f'\n{"=" * 74}')
if total_fail:
    print(f'❌ 共 {total_fail} 項未通過')
    sys.exit(1)
print(f'✅ 全部 {sum(len(s["items"]) for s in results.values())} 項檢查通過')
