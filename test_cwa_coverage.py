# -*- coding: utf-8 -*-
# CWA 官方預報必須覆蓋全部鄉鎮；樣本不足不得等於通過篩選
# =====================================================================
# 使用者回報：「cwa 的最終裁量機制好像沒有啟動」。查到兩個獨立原因：
#   ① PNG 判讀讀了全部 368 個鄉鎮，但輸出時 209 個非靜態表鄉鎮的
#      qpf_cwa／qpf_cwa_q／band_segs 被寫死成 [] —— 官方預報對 57%
#      的地圖根本沒有進入融合。兩份各自實作，只改了其中一份。
#   ② 反包牌篩選（誤報率、ETS）要 fc>=8／ob>=5 才會判；達不到就放行。
#      gem 7 天內只有 1 天有資料（抓取逾時），fc/ob 遠低於門檻 →
#      免疫於所有篩選，還因為小樣本上的偏差比漂亮而拿到最高權重 1.2。
#      「資料愈少愈不會被擋、還愈受信任」是顛倒的。
import importlib.util
import json
import os
import sys
from datetime import datetime

spec = importlib.util.spec_from_file_location('fr', 'fetch_rainfall.py')
fr = importlib.util.module_from_spec(spec)
sys.modules['fr'] = fr
spec.loader.exec_module(fr)

sf = []
ok = lambda c, m: None if c else sf.append(m)

# ── ① CWA 疊合：0 是資訊，不是缺值 ───────────────────────
now = datetime(2026, 10, 11, 7, 30)
t00 = now.replace(hour=0, minute=0, second=0, microsecond=0)
#  PNG 判讀結果：A 鎮無雨（0.0）、B 鎮 5mm 帶、C 鎮沒讀到（不在字典裡）
seg_map = {1: {'甲縣A鎮': 0.0, '甲縣B鎮': 5.0},
           2: {'甲縣A鎮': 0.0, '甲縣B鎮': 5.0}}
span = {1: 2, 2: 2}
A = fr.cwa_overlay('甲縣A鎮', 24.0, 121.0, 64, seg_map, True, span, None, now, t00)
B = fr.cwa_overlay('甲縣B鎮', 24.0, 121.0, 64, seg_map, True, span, None, now, t00)
C = fr.cwa_overlay('甲縣C鎮', 24.0, 121.0, 64, seg_map, True, span, None, now, t00)

ok(A[1][1] == 0.0 and A[1][2] == 0.0,
   f'CWA 判讀為「無雨」必須以 0.0 進入融合，實得 {A[1][1:3]}'
   + '（當成缺值的話，官方說不會下雨這件事就完全沒有份量）')
ok(A[3] == [1, 2], f'無雨的段也必須列為色帶段，實得 {A[3]}')
ok(A[2] == [], '色帶段不得列為真值段（色帶是類別，不可取代模式加權）')
#  5mm 帶的上界是 10，窗寬 2 段 → 可加量 5.0／段
ok(B[1][1] == 5.0, f'色帶可加量應為上界÷窗段數＝10/2=5.0，實得 {B[1][1]}')
ok(B[0][1] == 5.0, f'著色用的值應為色帶下界 5.0，實得 {B[0][1]}')
ok(C[0] == [] and C[1] == [],
   'PNG 讀不到的鄉鎮必須留空（「讀到且無雨」與「讀不到」不可混為一談）')

# ── ② 兩條分支必須走同一份實作 ───────────────────────────
src = open('fetch_rainfall.py', encoding='utf-8').read()
ok(src.count('cwa_overlay(') >= 3,
   'cwa_overlay 沒有被兩條鄉鎮分支共用（兩份實作遲早分歧）')
ok("'qpf_cwa_q': qpf_cwa_q_ns" in src,
   '非靜態表鄉鎮仍未輸出 CWA 疊合')

# ── ③ 樣本不足不得等於通過 ───────────────────────────────
FIX = 'fixture_fabricated_20261011.json'
if os.path.exists(FIX):
    D = json.load(open(FIX, encoding='utf-8'))
    before = D['adaptive_blend']
    after = fr.build_adaptive_blend(D['model_skill'], None, D['pattern_skill'])
    # 修正前 gem 在三個地形拿到最高權重；修正後不得出現在任何地形
    z_before = [z for z, v in before.items() if 'gem' in (v.get('models') or {})]
    z_after = [z for z, v in after.items() if 'gem' in (v.get('models') or {})]
    ok(len(z_before) >= 3, f'fixture 應重現問題（gem 原本在 {z_before}）')
    ok(not z_after, f'gem 觀測涵蓋不足仍被納入：{z_after}')
    for z, v in after.items():
        ms = v.get('models') or {}
        ok('blend' not in ms, f'{z}：融合把自己列為成分（自我遞迴）')
        ok(len(ms) >= 2, f'{z}：只剩 {len(ms)} 個成員，那不是融合')
        # 每個被採用的模式，觀測涵蓋率都要過關
        P = D['pattern_skill'].get(z) or {}
        obmax = max([(P.get(m) or {}).get('ob') or 0 for m in P] or [0])
        for m in ms:
            _ob = (P.get(m) or {}).get('ob') or 0
            _thin = (obmax >= fr.PATTERN_MIN_OB
                     and _ob < obmax * fr.PATTERN_MIN_OBCOV)
            if not _thin:
                continue
            #  涵蓋不足時的契約：非核心模式一律不納入；
            #  核心模式保留，但必須是中性權重 0.6 —— 不可拿小樣本上
            #  算出來的漂亮分數去換高權重（gem 當初就是這樣拿到 1.2 的）。
            ok(m in fr.ADAPT_CORE,
               f'{z}/{m}：非核心模式在觀測涵蓋 {_ob}/{obmax} 不足時仍被採用')
            ok(ms[m] == 0.6,
               f'{z}/{m}：涵蓋不足的核心模式權重應為中性 0.6，實得 {ms[m]}')
    print('   逐地形採用模式（修正後）：')
    for z in sorted(after):
        ms = after[z]['models']
        print(f'     {z:<6}' + '、'.join(f'{m}×{w}' for m, w in
                                        sorted(ms.items(), key=lambda x: -x[1])))
else:
    sf.append(f'缺少 fixture {FIX}')

if sf:
    print(f'❌ test_cwa_coverage 失敗 {len(sf)} 項')
    for x in sf:
        print('   - ' + x)
    sys.exit(1)
print('✅ test_cwa_coverage 全數通過')
print('   註：jma 的觀測涵蓋同樣偏低（1/19～4/78，抓取常失敗），'
      '依既有設計以核心模式的中性權重 0.6 保留 —— 這是刻意的，但'
      '代表 JMA 的抓取穩定度需要另外處理。')
