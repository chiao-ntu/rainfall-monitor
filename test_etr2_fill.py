# -*- coding: utf-8 -*-
"""ETR2 缺口補值：往返測試（挖空已知序列，檢查能否還原）
---------------------------------------------------------------------
使用者兩度指出「留白沒有解決問題，觀測多少就是多少」。
缺口兩端都是官方 ETR2、中間有官方雨量、遞迴式是官方定義的 ——
三者齊備時中間值唯一決定，不是猜測。

最強的驗證是往返：用官方公式生成一條「真值」序列，把中間挖掉，
再補回來，比對是否還原。能還原，才證明補值邏輯與官方公式一致。
"""
import sys
import fetch_rainfall as F

D = F.ETR2_DECAY_6H
fails = []


def ok(cond, msg):
    print(('OK  ' if cond else '!!  ') + msg)
    if not cond:
        fails.append(msg)


def truth(e0, rains):
    """用官方遞迴式生成真值序列：E(s+1) = E(s)*D + R(s+1)"""
    out, e = [e0], e0
    for r in rains:
        e = e * D + r
        out.append(round(e, 1))
    return out


print(f"ETR2 每 6h 衰減係數 = 0.7^(1/4) = {D:.6f}\n")

# ── ① 往返：無雨時段，挖空中間 4 段 ──────────────────────────
rains = [0.0] * 6
T = truth(300.0, rains)
holed = list(T)
for i in range(2, 6):
    holed[i] = None
filled, mask, info = F.fill_etr2_series(holed, [None] + rains)
err = max(abs(filled[i] - T[i]) for i in range(len(T)))
ok(info['filled'] == 4, f"①挖空 4 段，補回 {info['filled']} 段")
ok(err < 0.2, f"①無雨情境還原誤差 {err:.3f} mm（應 <0.2）")
ok(mask[2:6] == [True] * 4 and not mask[0] and not mask[1] and not mask[6],
   "①補值標記只落在被挖空的位置")
ok(filled[0] == T[0] and filled[1] == T[1] and filled[6] == T[6],
   "①原有官方值原封不動")

# ── ② 往返：缺口中間有雨 ────────────────────────────────────
rains2 = [0.0, 12.0, 45.0, 30.0, 5.0, 0.0]
T2 = truth(200.0, rains2)
holed2 = list(T2)
for i in range(2, 6):
    holed2[i] = None
# 雨量序列與 ETR2 對齊：index k 的雨量屬於「第 k 段」
rain_in = [None] + rains2
filled2, mask2, info2 = F.fill_etr2_series(holed2, rain_in)
err2 = max(abs(filled2[i] - T2[i]) for i in range(len(T2)))
ok(err2 < 0.2, f"②有雨情境還原誤差 {err2:.3f} mm（應 <0.2）")
print(f"     真值 {T2}")
print(f"     補值 {[round(v,1) for v in filled2]}")

# ── ③ 雨量全缺 + 實際大漲 → 必須拒補 ────────────────────────
#    ②那段期間實際下了 92mm，若雨量資料也一併缺失，純衰減推到右錨點
#    會差一大截。此時殘差超標，應拒補而非硬補出一條假線。
filled3, mask3, info3 = F.fill_etr2_series(holed2, [None] * len(T2))
ok(info3['norain'] > 0, f"③雨量缺漏已記錄（{info3['norain']} 段）")
ok(len(info3['refused']) == 1, "③雨量全缺且實際大漲時，殘差超標必須拒補")
ok(all(filled3[i] is None for i in range(2, 6)), "③拒補時維持留白，不得硬補")
ok(not any(mask3), "③拒補時不得留下補值標記")
_a3, _b3, _r3, _s3 = info3['refused'][0]
print(f"     殘差 {_r3} mm / 容許 {0.25*_s3:.1f} mm → 拒補（寧可留白，不要假線）")

# ── ③b 雨量全缺但變化平緩 → 仍可補（純衰減） ────────────────
T3b = truth(180.0, [0.0, 0.0, 0.0])
h3b = [T3b[0], None, None, T3b[3]]
f3b, m3b, i3b = F.fill_etr2_series(h3b, [None] * 4)
err3b = max(abs(f3b[i] - T3b[i]) for i in range(len(T3b)))
ok(i3b['filled'] == 2 and err3b < 0.2,
   f"③b 無雨時段即使缺雨量資料仍可補（誤差 {err3b:.3f} mm）")

# ── ④ 殘差過大必須拒補（寧可留白，不要假線）────────────────
bad = [100.0, None, None, 400.0]
f4, m4, i4 = F.fill_etr2_series(bad, [None] * 4)
ok(f4[1] is None and f4[2] is None, "④殘差過大時拒補，維持留白")
ok(len(i4['refused']) == 1, "④拒補事件有記錄")
ok(not any(m4), "④拒補時不得留下補值標記")

# ── ⑤ 首尾之外不得外插 ──────────────────────────────────────
edge = [None, None, 50.0, 45.0, None, None]
f5, m5, i5 = F.fill_etr2_series(edge, [None] * 6)
ok(f5[0] is None and f5[1] is None and f5[4] is None and f5[5] is None,
   "⑤序列首尾之外不補（外插＝猜測）")
ok(i5['filled'] == 0, "⑤端點外缺口不計入補值")

# ── ⑥ 不得產生負值 ──────────────────────────────────────────
f6, m6, i6 = F.fill_etr2_series([10.0, None, None, 0.0], [None, 0.0, 0.0, 0.0])
ok(all(v is None or v >= 0 for v in f6), f"⑥補值不得為負（{f6}）")

# ── ⑦ 單點缺口（最常見）────────────────────────────────────
T7 = truth(150.0, [8.0, 0.0])
h7 = [T7[0], None, T7[2]]
f7, m7, i7 = F.fill_etr2_series(h7, [None, 8.0, 0.0])
ok(abs(f7[1] - T7[1]) < 0.2, f"⑦單點缺口還原（{f7[1]} vs 真值 {T7[1]}）")

# ── ⑧ 無缺口時不得更動任何值 ────────────────────────────────
T8 = truth(100.0, [1.0, 2.0, 3.0])
f8, m8, i8 = F.fill_etr2_series(T8, [None, 1.0, 2.0, 3.0])
ok(f8 == T8 and not any(m8) and i8['filled'] == 0, "⑧序列完整時不更動、不標記")

# ── ⑨ 實況比對：使用者 2026-10-09 截圖的臺北分署 ─────────────
#    缺口前 25.0%、缺口後 21.5%（12 小時＝2 段）
a, b = 25.0, 21.5
pure = a * D * D
f9, m9, i9 = F.fill_etr2_series([a, None, b], [None, None, None])
ok(i9['filled'] == 1, "⑨實況缺口可補")
ok(abs(f9[2] - b) < 1e-6, "⑨右端精確落回官方值")
print(f"     純衰減推得 {pure:.2f}%、官方 {b}%、殘差 {b - pure:+.2f} 點"
      f" → 中間段補為 {f9[1]}%")
ok(pure < f9[1] < b + (a - b), f"⑨補值 {f9[1]} 落在衰減值與兩端之間（物理合理）")

print()
if fails:
    print(f"失敗 {len(fails)} 項")
    for f in fails:
        print('  - ' + f)
    sys.exit(1)
print(f"全部通過（{9} 組情境）")
