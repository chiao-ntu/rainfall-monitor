"""二次災害高風險區：第三時窗是 6 小時（2026-09 列表起），介面與判斷不可殘留 12 小時。

2026-11-26 使用者回報畫面仍寫 1h/3h/12h：判斷邏輯早已是 6 小時，
但面板說明文字與「過去日以日累積對照」的註腳仍比對舊標記 12h†（實際標記是 6h†，註腳永遠不出現）。
"""
import os, re, sys
src = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'index.html'), encoding='utf-8').read()
fails, n = [], 0
def ok(c, m):
    global n
    n += 1
    print(('OK  ' if c else '!!  ') + m)
    if not c: fails.append(m)
a = src.index('function _disasterHits()'); b = src.index('function _disasterRows()')
blk = src[a:b + 2000]
ok(not re.search(r"1h/3h/12h|12h†|t12h", blk), '二次災害面板與判斷不再出現 12h 字樣或標記')
ok("m.win==='6h†'" in blk and "win:'6h†'" in blk, '過去日註腳比對的標記與實際產生的標記一致（6h†）')
ok('1h/3h/6h' in blk, '面板說明為 1h/3h/6h')
m = re.search(r'const DISASTER_ZONES = (\[.*?\]);', src)
Z = __import__('json').loads(m.group(1))
ok(len(Z) == 42 and all(set(('t1h', 't3h', 't6h')) <= set(z) and 't12h' not in z for z in Z),
   f'42 處皆為 1／3／6 小時注意值（{len(Z)} 處）')
print(f"\n{'FAIL ' + str(len(fails)) if fails else 'ALL PASS'} / {n} 項")
sys.exit(1 if fails else 0)
