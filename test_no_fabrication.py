# -*- coding: utf-8 -*-
# 不得編造預報 —— 抓不到就是沒有
# =====================================================================
# 2026-10-11 事故：使用者回報「gem 像包牌、到處都在下雨，今天幾乎沒雨」。
# 根本原因是 get_qpf_model 的退路：
#     抓不到 → random.seed(警戒值+緯度) → base = 警戒值/20 × U(0.3,1.2)
#              每段 = base × exp(-i//4×0.06) × U(0.4,1.8)
# 實測後果（由當天 07:30 的 data.json 量到，存為 fixture）：
#   · ICON/KMA/CMA 本輪未排程（B 組輪抓，設計如此），159 個有警戒值的
#     鄉鎮**全部**拿到偽造值；GEM 抓取 3/3 逾時，同樣偽造。
#   · 偽造模式 16 天全臺總量 12.4 萬 mm，真實模式 0～3,827 mm。
#   · 偽造值與警戒值正相關 r=+0.36~+0.50（真實模式為負），
#     最大值/警戒值 0.100~0.103，緊貼公式上限 0.108。
#   · 融合因此在 132 個鄉鎮報 ≥10mm，實際無雨。
#
# 本測試有兩個部分，缺一不可：
#   ① 原始碼層：資料路徑不得有亂數、不得以常數陣列充當缺資料。
#   ② 偵測器層：拿**當天真實的偽造資料**當輸入，偵測器必須判為不通過。
#      只測「修好的程式會通過」是空轉 —— 要先證明它抓得到才算數。
import json
import os
import re
import sys

src = open('fetch_rainfall.py', encoding='utf-8').read()
sf = []
ok = lambda c, m: None if c else sf.append(m)


def _code_only(s):
    """去掉 # 註解與三引號字串（規則不能被說明文字觸發）"""
    out, i, n = [], 0, len(s)
    while i < n:
        c = s[i]
        if c in ('"', "'"):
            q3 = s[i:i + 3]
            if q3 in ('"""', "'''"):
                j = s.find(q3, i + 3)
                i = n if j < 0 else j + 3
                continue
            j = i + 1
            while j < n and s[j] != c:
                j += 2 if s[j] == chr(92) else 1
            out.append(s[i:j + 1]); i = j + 1; continue
        if c == '#':
            j = s.find(chr(10), i)
            i = n if j < 0 else j
            continue
        out.append(c); i += 1
    return ''.join(out)


CODE = _code_only(src)

# ── ① 原始碼層 ───────────────────────────────────────────
ok('import random' not in CODE and 'random.uniform' not in CODE,
   '資料路徑仍有亂數（預報與觀測一律不得編造）')
ok('alert_v/20' not in CODE.replace(' ', '') or
   'alert_v/20*random' not in CODE.replace(' ', ''),
   '仍以警戒值推導雨量（警戒值是地質門檻，與會不會下雨無關）')
ok('return []' in CODE and 'def get_qpf_model' in src,
   'get_qpf_model 抓不到時必須回空陣列')
ok(not re.search(r'return\s+segs\[:64\]\s+if\s+segs\s+else\s+\[0\.0\]\s*\*\s*64', CODE),
   '仍以 [0.0]*64 充當缺資料（「沒資料」被寫成「確定不下雨」）')
ok('_QPF_MISS' in CODE,
   '缺資料必須計數並印出（沉默的退路是這次事故的放大器）')
ok('def cwa_overlay' in src and CODE.count('cwa_overlay(') >= 3,
   'CWA 疊合必須是共用的單一實作，靜態與非靜態表鄉鎮都要用')
ok(not re.search(r"'qpf_cwa_q'\s*:\s*\[\]", CODE),
   '仍有鄉鎮把 qpf_cwa_q 寫死成空陣列（官方預報對那些鄉鎮完全沒作用）')
ok('PATTERN_MIN_OBCOV' in CODE,
   '缺觀測涵蓋率門檻（資料最少的模式會免疫於包牌篩選）')
ok("m == 'blend'" in CODE,
   '融合不得是自己的成分（自我遞迴）')


# ── ② 偵測器：對真實的偽造資料必須判為不通過 ─────────────
def corr(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    sx = (sum((x - mx) ** 2 for x in xs) / n) ** .5
    sy = (sum((y - my) ** 2 for y in ys) / n) ** .5
    if sx == 0 or sy == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (n * sx * sy)


#  偽造公式的決定性特徵：值 ≤ 警戒值×0.108，且與警戒值正相關。
#  真實模式沒有任何理由與「當地的崩塌警戒門檻」相關。
#  三個條件同時成立才判偽造（單用相關係數會誤殺：山區警戒值高、
#  地形雨也多，真實模式在濕日本來就可能正相關）：
#    ① 與警戒值正相關 r > 0.25（n=159 時約 3.2σ）
#    ② **每一個**鄉鎮的最大值都 ≤ 警戒值×0.108 —— 這是偽造公式的硬上限，
#       真實模式在濕日一定會有鄉鎮突破
#    ③ 普遍有雨（ratio 中位數 > 0.01）—— 否則乾日全為 0 會誤判
FAKE_CEIL = 0.1081
CORR_MAX = 0.25
MED_MIN = 0.01


def scan(towns):
    """回傳 {模式: (相關係數, 最大值/警戒值, 是否判定為偽造)}"""
    out = {}
    fields = sorted({k for t in towns for k in t if k.startswith('qpf_')
                     and k not in ('qpf_cwa', 'qpf_cwa_q', 'qpf_hi', 'qpf_lo',
                                   'qpf_15d', 'qpf_1h', 'qpf_24h', 'qpf_48h')})
    for f in fields:
        av, mn, rat = [], [], []
        for t in towns:
            a = t.get(f) or []
            v = t.get('alert_val') or 0
            if v <= 0 or len(a) < 8:
                continue
            vals = [x for x in a[:8] if x is not None]
            if not vals:
                continue
            av.append(v); mn.append(sum(vals) / len(vals))
            rat.append(max(x for x in a if x is not None) / v)
        if len(av) < 20:
            continue
        r = corr(av, mn)
        rs = sorted(rat)
        med = rs[len(rs) // 2]
        capped = all(x <= FAKE_CEIL for x in rat)
        out[f] = (round(r, 3), round(max(rat), 4),
                  r > CORR_MAX and capped and med > MED_MIN)
    return out


FIX = 'fixture_fabricated_20261011.json'
if os.path.exists(FIX):
    bad = scan(json.load(open(FIX, encoding='utf-8'))['townships'])
    caught = sorted(k for k, v in bad.items() if v[2])
    ok(set(caught) >= {'qpf_gem', 'qpf_icon', 'qpf_kma', 'qpf_cma'},
       f'偵測器對當天真實的偽造資料沒抓到全部四個模式，只抓到 {caught}')
    clean = sorted(k for k, v in bad.items() if not v[2])
    ok(all(k not in clean for k in ('qpf_gem', 'qpf_icon', 'qpf_kma', 'qpf_cma')),
       '偵測器把偽造模式判成乾淨的')
    ok({'qpf_best', 'qpf_ecmwf', 'qpf_gfs'} <= set(clean),
       f'偵測器把真實模式誤判為偽造（誤殺）：{[k for k in ("qpf_best","qpf_ecmwf","qpf_gfs") if k not in clean]}')
    print(f'   偵測器對 {FIX}：')
    for k, (r, mx, isf) in sorted(bad.items(), key=lambda x: -x[1][0]):
        print(f'     {k:<12} 相關 {r:+.3f}　最大/警戒 {mx:.4f}　'
              + ('← 判定偽造' if isf else '乾淨'))
else:
    sf.append(f'缺少 fixture {FIX}（沒有真實輸入就無法證明偵測器有效）')

if sf:
    print(f'❌ test_no_fabrication 失敗 {len(sf)} 項')
    for x in sf:
        print('   - ' + x)
    sys.exit(1)
print('✅ test_no_fabrication 全數通過（12 項）')
