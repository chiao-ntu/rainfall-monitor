#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證站名撞名不再污染 ETR2（臺中和平區 破百 vs 官網個位數 之根因）。

全臺有 6 組代表站「去尾英文字母後同名、實為不同機關的不同站」：
  武陵/武陵w、關山/關山w、南庄/南庄w、外大坪/外大坪w、寒溪/寒溪s、雙溪/雙溪tp
舊版無條件建立正規化鍵，使兩站塌成同一鍵，對站時可能取到另一站的值。
"""
import io, json, os, sys, types
os.environ.setdefault('CWA_API_KEY', 'dummy')
import fetch_qpesums_hourly as H

fails = []
def chk(label, got, exp):
    ok = got == exp
    if not ok: fails.append(label)
    print(f"  {'OK ' if ok else '!! '}{label}: {got!r}" + ('' if ok else f"  期望 {exp!r}"))


class R:
    def __init__(s, o): s.status_code = 200; s.content = json.dumps(o).encode()


# ★ STRT 為 0~1 的達成比值，非毫米：ETR2(mm) = STRT × AlertValue
# 兩個「武陵」：氣象署武陵 比值0.10（350×0.10=35mm）、水利署武陵w 比值0.95
DATA = [
    {"County": "臺中市", "Town": "和平區", "AlertValue": 350,
     "STName1": "武陵",  "STRT1": 0.10, "STName2": "德基", "STRT2": 0.05},
    {"County": "宜蘭縣", "Town": "大同鄉", "AlertValue": 400,
     "STName1": "武陵w", "STRT1": 0.95, "STName2": "土場", "STRT2": 0.05},
    # 不撞名者：正規化鍵應正常建立
    {"County": "南投縣", "Town": "仁愛鄉", "AlertValue": 400,
     "STName1": "廬山國小s", "STRT1": 0.11, "STName2": "奧萬大", "STRT2": 0.075},
]

H.requests = types.SimpleNamespace(get=lambda *a, **k: R(DATA))
out = H.fetch_swcb_hourly()

print('=== STRT 語意：比值×警戒值 = 毫米（依官方 API 文件範例）===')
DOC = [{"County":"新北市","Town":"五股區","DebrisNO":"新北DF021","AlertValue":500,
        "STName1":"社子","STRT1":0.05882449,"STName2":"關渡","STRT2":0.6567714}]
H.requests = types.SimpleNamespace(get=lambda *a, **k: R(DOC))
doc = H.fetch_swcb_hourly()
chk('社子 = 0.0588×500', doc.get('社子'), 29.4)
chk('關渡 = 0.6568×500', doc.get('關渡'), 328.4)
print(f"  （若誤當毫米，關渡達成率將為 {0.6567714/500*100:.4f}% —— 明顯荒謬）")
H.requests = types.SimpleNamespace(get=lambda *a, **k: R(DATA))
out = H.fetch_swcb_hourly()

print('=== 撞名站不得建立正規化鍵 ===')
chk('武陵 = 0.10×350', out.get('武陵'), 35.0)
chk('武陵w = 0.95×400', out.get('武陵w'), 380.0)
# ★ 關鍵：正規化鍵「武陵」若被 武陵w 覆蓋，和平區就會拿到 380 而非 5
chk('★正規化鍵未被另一站污染', out.get('武陵'), 35.0)

print('\n=== 不撞名者仍建立正規化鍵（對站能力不受損）===')
chk('廬山國小s = 0.11×400', out.get('廬山國小s'), 44.0)
chk('廬山國小（正規化）可對到', out.get('廬山國小'), 44.0)
chk('奧萬大 = 0.075×400', out.get('奧萬大'), 30.0)

print('\n=== 聚合後的鄉鎮值 ===')
# 用真實警戒表驗證和平區
slope = '/mnt/user-data/uploads/slope_warning_stations.json'
if os.path.exists(slope):
    import shutil, tempfile
    tmp = tempfile.mkdtemp(); cwd = os.getcwd()
    shutil.copy(slope, tmp); os.chdir(tmp)
    H.SLOPE_WARN_FILE = 'slope_warning_stations.json'; H.ETR2_FILE = 'etr2_now.json'
    from datetime import datetime
    # 和平區 11 個單元，只給武陵有值（5mm / 警戒值350 = 1.4%）
    H.write_etr2_now({'武陵': 35.0, '武陵w': 380.0}, datetime(2026, 8, 20, 10, 0))
    d = json.load(io.open('etr2_now.json', encoding='utf-8'))
    hp = d['townships'].get('臺中市和平區')
    print('  臺中市和平區:', json.dumps(hp, ensure_ascii=False) if hp else '（無）')
    if hp:
        chk('★和平區取到武陵的 35mm 而非武陵w 的 380mm', hp['etr2'], 35.0)
        pct = hp['pct'] * 100
        print(f"  ETR2% = {pct:.1f}%（若誤用武陵w 會是 {380/hp['alert']*100:.0f}%）")
        if pct > 50: fails.append(f'和平區 ETR2% 仍異常偏高（{pct:.1f}%）')
        else: print('  OK  百分比回到合理範圍（不再破百）')
    os.chdir(cwd); shutil.rmtree(tmp, ignore_errors=True)
else:
    print('  （找不到 slope_warning_stations.json，略過聚合驗證）')

print('\n全部通過' if not fails else f'\n失敗 {len(fails)} 項：{fails}')
sys.exit(1 if fails else 0)
