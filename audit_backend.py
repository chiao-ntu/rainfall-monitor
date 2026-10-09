#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FORMOSA 後端靜態稽核
=========================================================================
對應的事故（2026-10-09）：圖上時雨量統計正常，但同時段 ETR2 完全空白。

根本原因是一個**可以一般化的失效型態**：
    多來源寫入時，把「部分失敗」記錄成「成功」。

它有兩種形狀，本專案兩種都出現過：
  (A) 寫入空容器：某來源失敗仍寫 {}，下游分不出「查過沒有」與「沒查到」
  (B) 整包覆寫：本輪只抓到部分欄位，卻覆蓋掉前一輪已抓到的完整資料
再加上「該小時已存在就整個跳過」，就會讓缺口永久化 ——
腳本每 10 分鐘跑一次、一小時有 6 次機會，卻只用掉第 1 次。

本稽核把這些寫成規則，避免同類問題在其他來源重演。

用法：python3 audit_backend.py
"""
import re
import sys

def _code_only(src):
    """只留程式碼：去掉 # 註解與三引號字串。

    ★ 稽核規則若對註解生效，就會被「說明這個錯誤的註解」觸發 ——
      實測：本稽核第一版把自己寫的事故說明判成未通過。
      規則必須只看會執行的那部分。
    """
    TQ = ('"' * 3, "'" * 3)
    out, i, n = [], 0, len(src)
    while i < n:
        c = src[i]
        if c in ('"', "'"):
            q3 = src[i:i + 3]
            if q3 in TQ:
                j = src.find(q3, i + 3)
                i = n if j < 0 else j + 3
                continue
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == chr(92) else 1
            out.append(src[i:j + 1])
            i = j + 1
            continue
        if c == '#':
            j = src.find(chr(10), i)
            i = n if j < 0 else j
            continue
        out.append(c)
        i += 1
    return ''.join(out)


FILES = {
    'fetch_qpesums_hourly.py': open('fetch_qpesums_hourly.py', encoding='utf-8').read(),
    'fetch_rainfall.py': open('fetch_rainfall.py', encoding='utf-8').read(),
}
Q = _code_only(FILES['fetch_qpesums_hourly.py'])
R = _code_only(FILES['fetch_rainfall.py'])

results = []


def chk(code, cond, msg, note=''):
    results.append((code, bool(cond), msg, note))


# ── [A] 不得寫入空容器 ───────────────────────────────────────────
chk('A', "ser['swcb'][hour_key] = swcb\n" not in Q.replace('                ', ''),
    '水保署結果不得無條件寫入（失敗時會是空字典）',
    '空字典會被下游當成「這小時沒有 ETR2」')
chk('A', "ser['swcb'].pop(hour_key, None)" in Q,
    '抓不到時把鍵移除，讓下一輪重試',
    '留空字典＝永久放棄；移除＝下一輪還會再試')
chk('A', "ser['cwa'].pop(hour_key, None)" in Q,
    'CWA 抓不到時同樣移除鍵（兩個來源一視同仁）')

# ── [B] 多來源必須逐一判定，不可整包 ─────────────────────────────
chk('B', 'if not cwa and not swcb' not in Q,
    '不得用「兩個來源都失敗才算失敗」判定',
    '單一來源失敗時會被當成成功寫入')
chk('B', 'need_cwa' in Q and 'need_swcb' in Q,
    '每個來源各自判定是否需要抓取')
chk('B', re.search(r'if not snap and not hist\[.hours.\]\.get\(hkey\)', Q) is not None,
    'env/wind 歷史：本輪無值但該小時已有值時，不得直接 return 丟棄')

# ── [C] 同一小時不得整包覆寫 ─────────────────────────────────────
n_overwrite = len(re.findall(r"hist\['hours'\]\[hkey\] = snap", Q))
n_merge = len(re.findall(r"_m = dict\(_prev\.get\(_k\) or \{\}\); _m\.update\(_v\)", Q))
chk('C', n_merge >= n_overwrite and n_overwrite > 0,
    f'每個 hist[hours][hkey] 寫入前都先逐欄合併（寫入 {n_overwrite} 處、合併 {n_merge} 處）',
    '整包覆寫會讓部分失敗的那一輪洗掉前一輪的完整資料')

# ── [D] 缺口必須可自癒 ───────────────────────────────────────────
chk('D', '已存在 → 不覆寫（同小時重跑）' not in Q,
    '不得因「該小時已存在」就整個跳過',
    '一小時有 6 次執行機會，不該只用第 1 次')
chk('D', '兩個來源都已寫入 → 不重抓' in Q,
    '只有兩個來源都齊全時才略過（而非只看小時是否存在）')

# ── [E] 缺口要用資料補，不是用畫法掩蓋 ───────────────────────────
chk('E', 'def backfill_swcb_calc' in Q,
    '有「以官方公式自算 ETR2」的補缺機制',
    'API 只給現在的值；過去的小時只能由官方時雨量依官方公式算出')
chk('E', 'backfill_swcb_calc(ser, now_tpe)' in Q,
    '補缺機制確實被呼叫')
chk('E', "_calc_h = _ser.get('swcb_calc')" in R,
    'fetch_rainfall 會讀取自算值（否則算了也沒人用）')
chk('E', 'etr2_hist_fill' not in R,
    '不再輸出「推算值」標記（資料路徑已無推算值）',
    '虛線＋註解是用呈現手法掩蓋缺口，不是修正')

# ── [F] 自算必須可驗證、且不得以 0 充數 ─────────────────────────
chk('F', '_DAY_COV_MIN' in Q,
    '自算有每日覆蓋率門檻（缺報的小時不當成 0）')
chk('F', '自算 vs 官方' in Q,
    '每輪印出「自算 vs 官方」比對，能看出對站或權重是否出問題')
chk('F', 'KEEP_SERIES_HOURS = 168' in Q,
    '時雨量保留 168 小時（＝官方 7 日權重所需）')

# ── [G] 權重與常數必須單一來源 ───────────────────────────────────
wq = re.search(r'ETR2_WEIGHTS = (\[[^\]]+\])', Q)
wr = re.search(r'ETR2_WEIGHTS = (\[[^\]]+\])', R)
chk('G', wq and wr and wq.group(1) == wr.group(1),
    f'兩支腳本的 ETR2 權重一致（{wq.group(1) if wq else "?"}）',
    '權重分歧會讓自算值與後端換算互相矛盾')

# ── [H] 修剪必須涵蓋所有 bucket ─────────────────────────────────
chk('H', "for bucket in ('cwa', 'swcb', 'swcb_calc')" in Q,
    '序列修剪涵蓋全部三個 bucket',
    '漏掉一個會讓檔案無限長大')

# ── 報表 ─────────────────────────────────────────────────────────
TITLE = {
    'A': '[A] 不得寫入空容器（「查過沒有」vs「沒查到」必須分得開）',
    'B': '[B] 多來源必須逐一判定成敗',
    'C': '[C] 同一時刻不得整包覆寫（部分失敗會洗掉完整資料）',
    'D': '[D] 缺口必須可自癒（每 10 分鐘都要有機會補上）',
    'E': '[E] 缺口要用資料補，不是用畫法掩蓋',
    'F': '[F] 自算必須可驗證、不得以 0 充數',
    'G': '[G] 常數單一來源',
    'H': '[H] 修剪涵蓋所有 bucket',
}
fail = 0
cur = None
for code, good, msg, note in results:
    if code != cur:
        cur = code
        print(f'\n{"=" * 72}\n{TITLE[code]}\n{"=" * 72}')
    print(f'  {"OK  " if good else "!!  "}{msg}')
    if note and not good:
        print(f'        → {note}')
    if not good:
        fail += 1

print(f'\n{"=" * 72}')
if fail:
    print(f'❌ {fail} 項未通過')
    sys.exit(1)
print(f'✅ 全部 {len(results)} 項檢查通過')
