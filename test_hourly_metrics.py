#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""驗證逐時序列判定：暖機不足回 None＋reason、缺格不硬算、動態調降級別正確。"""
import os, json, tempfile, shutil
from datetime import datetime, timedelta
os.environ.setdefault('CWA_API_KEY', 'dummy')
import fetch_rainfall as F

fails = []
def chk(label, got, exp):
    ok = got == exp
    if not ok: fails.append(label)
    print(f"  {'OK ' if ok else '!! '}{label}: {got!r}" + ("" if ok else f"  期望 {exp!r}"))

T0 = datetime(2026, 8, 1, 0)
def mkser(vals, gap_at=None, stn="六龜"):
    """vals = 時雨量序列（升冪）。gap_at = 要挖掉的索引（模擬漏跑）。"""
    hours, cwa = [], {}
    for i, v in enumerate(vals):
        if gap_at is not None and i == gap_at: continue
        h = (T0 + timedelta(hours=i)).strftime('%Y-%m-%dT%H')
        hours.append(h); cwa[h] = {stn: v}
    return {'hours': hours, 'cwa': cwa, 'swcb': {}, 'gap_count': 1 if gap_at is not None else 0,
            'updated': 'test'}

def mkmeta(n):
    return {'available': n > 0, 'hours': n, 'gap_count': 0, 'updated': 'test', 'reason': '',
            'warm': {'r2h': n >= 2, 'r3h': n >= 3, 'no_abate': n >= 2,
                     'release_2stage': n >= 6, 'release_1stage': n >= 12, 'self_etr2': n >= 168}}

print("=== 無序列（未部署 hourly 腳本）→ 全 None ＋ reason ===")
m = F.hourly_metrics(None, {'available': False, 'reason': '尚無 rain_hourly.json'}, ["六龜"])
chk("r3h", m['r3h'], None)
chk("rel_1stage", m['rel_1stage'], None)
chk("有 reason", bool(m['reason']), True)

print("\n=== 對不到站 → reason 說明，不硬給 0 ===")
s = mkser([5.0]*12); m = F.hourly_metrics(s, mkmeta(12), ["不存在的站"])
chk("r1h", m['r1h'], None)
chk("reason", m['reason'], '逐時序列中對不到代表站')

print("\n=== 正規化對站（明細表帶機關代碼）===")
s = mkser([5.0]*3, stn="六龜")
m = F.hourly_metrics(s, mkmeta(3), ["六龜f"])
chk("六龜f 對到 六龜", m['station'], "六龜")
chk("r3h = 15.0", m['r3h'], 15.0)

print("\n=== 暖機：僅3h序列 → 近3h可算、解除不可算 ===")
s = mkser([10.0, 20.0, 30.0])
m = F.hourly_metrics(s, mkmeta(3), ["六龜"])
chk("r1h", m['r1h'], 30.0)
chk("r2h", m['r2h'], 50.0)
chk("r3h", m['r3h'], 60.0)
chk("rel_2stage 資料不足", m['rel_2stage'], None)
chk("rel_1stage 資料不足", m['rel_1stage'], None)
chk("reason 有說明", 'rel_1stage' in m['reason'], True)

print("\n=== 缺格：12h但中間漏1小時 → 不硬算 ===")
s = mkser([1.0]*12, gap_at=5)
m = F.hourly_metrics(s, mkmeta(11), ["六龜"])
print(f"   序列長度 {len(s['hours'])}｜rel_1stage={m['rel_1stage']!r} rel_2stage={m['rel_2stage']!r}")
chk("近1h仍可算（最新小時完整）", m['r1h'], 1.0)

print("\n=== 解除標準 ===")
s = mkser([1.0]*12)   # 平均1mm
m = F.hourly_metrics(s, mkmeta(12), ["六龜"])
chk("連續12h平均1mm<10 → 一階段解除 True", m['rel_1stage'], True)
chk("連續6h平均1mm<4且最大1≤10 → 二階段 True", m['rel_2stage'], True)
s = mkser([1.0]*6 + [3.0, 3.0, 3.0, 3.0, 3.0, 12.0])   # 最後一小時12mm
m = F.hourly_metrics(s, mkmeta(12), ["六龜"])
chk("最大時雨12>10 → 二階段 False", m['rel_2stage'], False)
s = mkser([15.0]*12)
m = F.hourly_metrics(s, mkmeta(12), ["六龜"])
chk("連續12h平均15mm → 一階段 False", m['rel_1stage'], False)

print("\n=== 降雨無減緩（代理判斷）===")
chk("4→10mm 增強 → True", F.hourly_metrics(mkser([4.0, 10.0]), mkmeta(2), ["六龜"])['no_abate'], True)
chk("20→1mm 明顯減弱 → False", F.hourly_metrics(mkser([20.0, 1.0]), mkmeta(2), ["六龜"])['no_abate'], False)
chk("2→3mm 皆<4mm → False", F.hourly_metrics(mkser([2.0, 3.0]), mkmeta(2), ["六龜"])['no_abate'], False)

print("\n=== 再發布門檻 ===")
chk("時雨量45mm → 門檻1 True", F.hourly_metrics(mkser([5.0, 45.0]), mkmeta(2), ["六龜"])['reissue_th1'], True)
chk("連續2h各25mm → 門檻1 True", F.hourly_metrics(mkser([25.0, 25.0]), mkmeta(2), ["六龜"])['reissue_th1'], True)
chk("連續2h各15mm → 門檻1 False", F.hourly_metrics(mkser([15.0, 15.0]), mkmeta(2), ["六龜"])['reissue_th1'], False)
chk("24h累積240mm → 門檻2 True", F.hourly_metrics(mkser([10.0]*24), mkmeta(24), ["六龜"])['reissue_th2'], True)
chk("24h序列不足 → 門檻2 None", F.hourly_metrics(mkser([10.0]*12), mkmeta(12), ["六龜"])['reissue_th2'], None)

print("\n=== 動態調降（技術指引三-(三)-3）===")
A = F.apply_dynamic_adj
chk("原400 近3h=210 → 一級降100 → 300", A(400, 210, 50), (300, 1, 100))
chk("原450 近3h=210 → 一級降150 → 300", A(450, 210, 50), (300, 1, 150))
chk("原400 近3h=160 → 二級降50 → 350",  A(400, 160, 50), (350, 2, 50))
chk("原500 近3h=160 → 二級降100 → 400", A(500, 160, 50), (400, 2, 100))
chk("原400 近2h=110 → 三級降50 → 350",  A(400, 100, 110), (350, 3, 50))
chk("原450 近2h=110 → 三級維持不變",     A(450, 100, 110), (450, 3, 0))
chk("均未達 → 級別0(已判定無需調整)",     A(400, 50, 20), (400, 0, 0))
chk("序列不足 → 級別None(無法判定)",      A(400, None, None), (400, None, 0))
chk("一級優先於二級",                     A(400, 250, 150), (300, 1, 100))
chk("警戒值無效 → 原樣回",                A(0, 250, 150), (0, None, 0))

print("\n=== load_hourly_series 缺檔/壞檔 ===")
tmp = tempfile.mkdtemp(); cwd = os.getcwd(); os.chdir(tmp)
F.HOURLY_FILE = "rain_hourly.json"
ser, meta = F.load_hourly_series()
chk("缺檔 → ser None", ser, None)
chk("缺檔 → available False", meta['available'], False)
open("rain_hourly.json", "w").write("{壞掉")
ser, meta = F.load_hourly_series()
chk("壞檔 → ser None", ser, None)
chk("壞檔 → 有 reason", bool(meta['reason']), True)
json.dump(mkser([1.0]*20), open("rain_hourly.json", "w"))
ser, meta = F.load_hourly_series()
chk("正常檔 → available", meta['available'], True)
chk("20h → release_1stage 可用", meta['warm']['release_1stage'], True)
chk("20h → self_etr2 未達", meta['warm']['self_etr2'], False)
os.chdir(cwd); shutil.rmtree(tmp, ignore_errors=True)

print("\n全部通過" if not fails else f"\n失敗 {len(fails)} 項：{fails}")
raise SystemExit(1 if fails else 0)
