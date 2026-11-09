"""2026-11-09 定量降水預報圖判讀守門（使用者以 11/9 05:30 報四張官方圖比對：熱區不一致）。
   根因：① 控制點 W 錯 131 像素，整張扭曲；② 有效時間早算 3 小時；③ 鄉鎮取眾數，零星雨區與核心消失。
   這支測試用真正的官方圖（fixtures/qpf_20261109_0530_I.png）驗證：
   ① 對位自檢：正確控制點吻合 ≥0.9；給錯的控制點會自動重新對位；空白圖不採用
   ② 標題時間：讀出發布 05:30、有效 08:00~20:00
   ③ 判讀結果對得上官方圖上肉眼可見的熱區（舊控制點會失敗＝測得出這次的錯）
   ④ 混報：同一小時不同報只算一次"""
import sys, os, json, shutil, importlib.util
from datetime import datetime
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here); os.chdir(here)
spec = importlib.util.spec_from_file_location('fr', 'fetch_rainfall.py')
fr = importlib.util.module_from_spec(spec); spec.loader.exec_module(fr)
import numpy as np
from PIL import Image
fails = 0
def ok(c, m):
    global fails
    print(('  OK ' if c else '  !! ') + m); fails += (not c)

IMG = 'fixtures/qpf_20261109_0530_12.png'
raw = open(IMG, 'rb').read(); arr = np.asarray(Image.open(IMG).convert('RGB'))
towns = json.load(open('fixtures/towns_20261109.json'))['townships']
WRONG = {'N': (933, 202), 'E': (1102, 306), 'S': (687, 1423), 'W': (380, 860)}

# ① 對位
fr._REG_CAPES_RUN = None
H3, info = fr._png_register(arr)
ok(info['fit0'] >= 0.9 and not info['refit'], f"正確控制點：96% 界線落在官方黑線 2 像素內（實得 {info['fit0']}）")
fr._REG_CAPES_RUN = dict(WRONG)
H3, info = fr._png_register(arr)
ok(info['fit0'] < 0.3 and info['refit'] and info['fit'] >= 0.9, f"錯誤控制點 {info['fit0']} → 自動重新對位 {info.get('fit')}")
ok(all(abs(info['capes'][k][i] - fr.CAPES_PX[k][i]) <= 2 for k in 'NESW' for i in (0, 1)), '重新對位結果與校正值相差 ≤2 像素')
shifted = np.full_like(arr, 255); shifted[:, 18:] = arr[:, :-18]
fr._REG_CAPES_RUN = None
H3, info = fr._png_register(shifted)
ok(info['refit'] and info['fit'] >= 0.85, f"官方版面平移 18 像素 → 偵測並重新對位（{info['fit0']}→{info.get('fit')}）")
fr._REG_CAPES_RUN = None
H3, info = fr._png_register(np.full_like(arr, 255))
ok(H3 is None and not info['ok'], '圖上沒有界線（版面大改）→ 不採用，寧缺勿錯')
fr._REG_CAPES_RUN = None

# ② 標題時間
if shutil.which('tesseract'):
    tt = fr.cwa_png_title_times(raw)
    ok(tt and tt['issued'] == datetime(2026, 11, 9, 5, 30) and tt['start'] == datetime(2026, 11, 9, 8)
       and tt['end'] == datetime(2026, 11, 9, 20), f'標題：發布 05:30、有效 08:00~20:00（{tt}）')
else:
    print('  -- 未安裝 tesseract，略過標題時間（workflow 有安裝）')
ok(fr.CWA_VALID_OFFSET_H == 3, '規則推算：有效時間＝班次＋3h（05→08）')

# ③ 熱區
def decode(capes):
    fr.CAPES_PX, fr._REG_CAPES_RUN = capes, None
    reg = fr._png_register
    if capes is WRONG:   # 重現修正前：不跑自檢
        fr._png_register = lambda a, H=None: (fr._png_solve_homography(WRONG, fr.CAPES_LL), {'ok': True})
    r = fr.decode_qpf_png(raw, 'x', datetime(2026, 11, 9, 8, 25), towns, win_seg=0)
    fr._png_register = reg
    return next(iter(r.values())), dict(fr.QPF_LAST_Q12)
GOOD = dict(fr.CAPES_PX)
band, q = decode(GOOD)
EXPECT = [  # 官方圖上看得到的熱區／無雨區（以反投影定位到鄉鎮）
    ('新北市烏來區', lambda b, v: b >= 30, '黃色核心 30–40'),
    ('宜蘭縣大同鄉', lambda b, v: b >= 20, '綠色核心 20–30'),
    ('桃園市復興區', lambda b, v: b >= 15, '綠色 15–20'),
    ('宜蘭縣冬山鄉', lambda b, v: b >= 10, '藍色 10–15'),
    ('屏東縣三地門鄉', lambda b, v: b >= 10, '藍色 10–15（含綠點）'),
    ('臺東縣海端鄉', lambda b, v: b >= 1, '山區零星雨'),
    ('彰化縣彰化市', lambda b, v: b == 0 and v == 0, '無雨'),
    ('臺中市西屯區', lambda b, v: b == 0 and v == 0, '無雨'),
]
for k, f, why in EXPECT:
    ok(f(band.get(k), q.get(k)), f'{k}：{why}（色帶 {band.get(k)}、面積平均 {q.get(k)}）')
n_rain = sum(1 for v in q.values() if v and v > 0)
ok(n_rain >= 150, f'有雨鄉鎮數 {n_rain}（眾數法僅 132，零星山區雨被吃掉）')
band_w, q_w = decode(WRONG)
bad = [k for k, f, _ in EXPECT if not f(band_w.get(k), q_w.get(k))]
ok(len(bad) >= 2, f'舊控制點會被這組檢查抓到（不符 {len(bad)} 項：{"、".join(bad)}）')
fr.CAPES_PX = GOOD

# ④ 混報
from datetime import timedelta
t0 = datetime(2026, 11, 8, 0, 0)
w23 = {'start': datetime(2026, 11, 9, 11), 'end': datetime(2026, 11, 9, 23), 'vals': {'X': 2.0}, 'issued': datetime(2026, 11, 8, 23)}
w17 = {'start': datetime(2026, 11, 9, 17), 'end': datetime(2026, 11, 10, 5), 'vals': {'X': 2.0}, 'issued': datetime(2026, 11, 8, 17)}
b, qq = fr.cwa_split_windows('X', [w17, w23], t0, 12)
hi = float(fr.qpf_band_hi(2.0))
ok(abs(qq[6] - hi / 12 * 6) < 1e-6, f'11/09 12~18 時只算一次（{qq[6]}）')
ok(abs(qq[7] - hi / 12 * 6) < 1e-6, '11/09 18~24：18~23 新報、23~24 舊報，不重複')
w = dict(w23); w['q12'] = {'X': 3.3}
b, qq = fr.cwa_split_windows('X', [w], t0, 12)
ok(abs(sum(v for v in qq.values() if v) - 3.3) < 0.02, '可加量採鄉鎮面積平均（q12），12 小時加總＝3.3')

# ⑤ 發布到有效起點的空窗：05:30 報第一張 08:00 起，06~08 時由上一報（23:30 報 02~14 時）補
prev = [{'start': '2026-11-09T02:00:00', 'end': '2026-11-09T14:00:00', 'issued': '2026-11-08T23:00:00',
         'vals': {'X': 5.0}, 'q12': {'X': 6.0}},
        {'start': '2026-11-08T14:00:00', 'end': '2026-11-09T02:00:00', 'issued': '2026-11-08T11:00:00',
         'vals': {'X': 5.0}, 'q12': {'X': 6.0}}]
new_w = {'start': datetime(2026, 11, 9, 8), 'end': datetime(2026, 11, 9, 20), 'vals': {'X': 2.0},
         'q12': {'X': 2.4}, 'issued': datetime(2026, 11, 9, 5)}
gap = fr.cwa_gap_windows(prev, datetime(2026, 11, 9, 5), datetime(2026, 11, 9, 8), datetime(2026, 11, 9, 6))
ok(len(gap) == 1 and gap[0]['issued'] == datetime(2026, 11, 8, 23), '只取上一報、且結束晚於現在的窗（更舊、已過去的不取）')
b, qq = fr.cwa_split_windows('X', [new_w] + gap, datetime(2026, 11, 9), 8)
ok(abs(qq[1] - (6.0 / 12 * 2 + 2.4 / 12 * 4)) < 0.02, f'06~12 時：06~08 上一報＋08~12 新報（{qq[1]}）')
ok(abs(qq[2] - 2.4 / 12 * 6) < 0.02, '12~18 時：新報優先，上一報不重複加')
ok(fr.cwa_issued_of([{'issued': '2026-11-09T05:00:00'}, {'issued': '2026-11-08T23:00:00', 'gap_fill': True}])
   == '2026-11-09T05:00:00', '標示採用的報別時不把補空窗的上一報算進去')
print('ALL PASS' if not fails else f'{fails} FAIL'); sys.exit(1 if fails else 0)
