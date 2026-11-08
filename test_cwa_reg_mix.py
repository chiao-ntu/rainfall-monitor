"""2026-11-08 使用者回報：官方圖花蓮無雨、系統讀出花蓮有雨。
   ① 對位自檢：官方圖若在畫布內平移，必須偵測到並修正；沒平移不可亂修。
   ② 混報：同一小時被不同報的兩張窗蓋到，只採較新那一報、不可重複累加。"""
import sys, os, importlib.util
from datetime import datetime, timedelta
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
spec = importlib.util.spec_from_file_location('fr', 'fetch_rainfall.py')
fr = importlib.util.module_from_spec(spec); spec.loader.exec_module(fr)
import numpy as np
from PIL import Image, ImageDraw
fails = 0
def ok(c, m):
    global fails
    print(('  OK ' if c else '  !! ') + m); fails += (not c)

H3 = fr._png_solve_homography(fr.CAPES_PX, fr.CAPES_LL)
W, H = fr.QPF_PNG_REF_SIZE
pts = fr._reg_boundary_lonlat()
ok(len(pts) > 2000, f'海岸線＋縣市界取樣點 {len(pts)}')

def synth(dx, dy, noise=True):
    """模擬官方圖：白底、深灰界線（平移 dx,dy）、幾塊彩色雨區與文字雜訊。"""
    im = Image.new('RGB', (W, H), (255, 255, 255)); d = ImageDraw.Draw(im)
    P = [fr._png_ll2px(H3, lo, la) for lo, la in pts]
    for x, y in P:
        d.point((round(x) + dx, round(y) + dy), fill=(60, 60, 60))
    d.ellipse((600, 900, 700, 1000), fill=(3, 200, 255)); d.ellipse((800, 500, 860, 560), fill=(255, 149, 0))
    if noise:
        rng = np.random.default_rng(1)
        for _ in range(400):
            x, y = rng.integers(0, W), rng.integers(0, H); d.text((int(x), int(y)), '雨', fill=(30, 30, 30))
    return np.asarray(im)

for (dx, dy) in [(0, 0), (14, -9), (-22, 5)]:
    arr = synth(dx, dy)
    H3b, info = fr._png_register(arr, H3)
    if (dx, dy) == (0, 0):
        ok(not info['applied'] and info['dx'] == 0 and info['dy'] == 0, f'未平移 → 不修正（現行吻合 {info["score0"]}）')
    else:
        ok(info['applied'] and (info['dx'], info['dy']) == (dx, dy),
           f'平移 ({dx},{dy}) → 偵測 ({info["dx"]},{info["dy"]})，現行 {info["score0"]}→最佳 {info["best"]}，已修正')
        x0, y0 = fr._png_ll2px(H3, 121.0, 23.5); x1, y1 = fr._png_ll2px(H3b, 121.0, 23.5)
        ok(abs((x1 - x0) - dx) < 0.01 and abs((y1 - y0) - dy) < 0.01, '修正後經緯度→像素一併平移')
# 沒有界線的圖（全白）：不可「修正」
H3c, info = fr._png_register(np.full((H, W, 3), 255, np.uint8), H3)
ok(not info['applied'], '圖上沒有界線 → 不修正')

# ② 混報
t0 = datetime(2026, 11, 8, 0, 0)
w23 = {'start': datetime(2026, 11, 9, 11), 'end': datetime(2026, 11, 9, 23), 'vals': {'X': 2.0}, 'issued': datetime(2026, 11, 8, 23)}
w17 = {'start': datetime(2026, 11, 9, 17), 'end': datetime(2026, 11, 10, 5), 'vals': {'X': 2.0}, 'issued': datetime(2026, 11, 8, 17)}
b, q = fr.cwa_split_windows('X', [w17, w23], t0, 12)
hi = float(fr.qpf_band_hi(2.0))
ok(abs(q[6] - hi / 12 * 6) < 1e-6, f'11/09 12~18 時只算一次（{q[6]}，應為 {hi/12*6}）')
ok(abs(q[7] - (hi / 12 * 5 + hi / 12 * 1)) < 1e-6, '11/09 18~24：18~23 用新報、23~24 用舊報，不重複')
print('ALL PASS' if not fails else f'{fails} FAIL'); sys.exit(1 if fails else 0)
