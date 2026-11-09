"""端到端：模擬氣象署 fileapi 回傳 11/9 05:30 報四張官方圖，跑完整常態 QPF 流程。
   驗證 ① 四窗有效時間＝08、20、08、20 時起（標題讀出）② 同一報 ③ 對位自檢通過
   ④ 06~08 時以上一報補（cwa_qpf_prev.json）⑤ 鄉鎮可加量＝面積平均（烏來 12h ≈23mm）"""
import os, sys, json, shutil, tempfile, importlib.util
from datetime import datetime, timedelta
SRC = os.path.dirname(os.path.abspath(__file__))
work = tempfile.mkdtemp(); os.chdir(work)
for f in ('fetch_rainfall.py', 'town_polys.json', 'all_townships.json', 'qpf_reg_pts.json'):
    shutil.copy(os.path.join(SRC, f), f)
os.environ['CWA_API_KEY'] = 'TEST'
spec = importlib.util.spec_from_file_location('fr', 'fetch_rainfall.py')
fr = importlib.util.module_from_spec(spec); spec.loader.exec_module(fr)
fr.CWA_API_KEY = 'TEST'
fails = 0
def ok(c, m):
    global fails
    print(('  OK ' if c else '  !! ') + m); fails += (not c)
IMG = {f'F-C0035-0{n}': os.path.join(SRC, f'fixtures/qpf_20261109_0530_{h}.png')
       for n, h in (('15', 12), ('17', 24), ('23', 36), ('24', 48))}
ROM = {'F-C0035-015': '(I)', 'F-C0035-017': '(II)', 'F-C0035-023': '(III)', 'F-C0035-024': '(IV)'}
class R:
    def __init__(s, code, content=b''): s.status_code, s.content = code, content
def fake_get(url, params=None, timeout=None, **k):
    did = url.rsplit('/', 1)[-1]
    if did in IMG:
        return R(200, json.dumps({'cwaopendata': {'Sent': '2026-11-09T05:18:00+08:00', 'Dataset': {'Resource': {
            'ResourceDesc': f'中文定量降水預報圖{ROM[did]}', 'MimeType': 'image/png',
            'ProductURL': f'https://x/Forecast/{did}.png'}}}}).encode())
    if url.endswith('.png'):
        return R(200, open(IMG[url.rsplit('/', 1)[-1][:-4]], 'rb').read())
    return R(404)
fr.cwa_get = fake_get
# 上一報（11/8 23:30 報）快取：第 I 張 02~14 時
json.dump({'windows': [{'start': '2026-11-09T02:00:00', 'end': '2026-11-09T14:00:00',
                        'issued': '2026-11-08T23:00:00', 'vals': {'新北市烏來區': 10.0}, 'q12': {'新北市烏來區': 12.0}}]},
          open('cwa_qpf_prev.json', 'w'))
now = datetime(2026, 11, 9, 6, 25)
res = fr.fetch_cwa_routine_qpf(now)
ok(res and res.get('png'), '常態 QPF 走 PNG 判讀路徑')
W = [w for w in res['windows'] if not w.get('gap_fill')]
ok([w['start'][5:16] for w in W] == ['11-09T08:00', '11-09T20:00', '11-10T08:00', '11-10T20:00'],
   f"有效時間＝官方標題（{[w['start'][5:16] for w in W]}）")
ok(all(w.get('time_src') == 'title' for w in W) or not shutil.which('tesseract'), '時間來源＝標題文字')
ok({w['issued'] for w in W} == {'2026-11-09T05:00:00'}, '四張同一報（05 時）')
ok(fr.cwa_issued_of(res['windows']) == '2026-11-09T05:00:00', '採用報別＝05 時（補空窗的上一報不算）')
ok(all(r.get('fit0', 0) >= 0.9 for r in fr.CWA_PNG_REG), f"對位自檢 {[r.get('fit0') for r in fr.CWA_PNG_REG]}")
gap = [w for w in res['windows'] if w.get('gap_fill')]
ok(len(gap) == 1 and gap[0]['issued'] == '2026-11-08T23:00:00', '06~08 時以上一報補空窗')
today00 = now.replace(hour=0, minute=0)
b, q = fr.cwa_split_windows('新北市烏來區', fr.CWA_PNG_WINDOWS, today00, 12)
ok(abs(q[1] - (12.0 / 12 * 2 + 23.32 / 12 * 4)) < 0.1, f'烏來 06~12 時＝上一報 2h＋新報 4h（{q.get(1)}）')
ok(abs(q[2] - 23.32 * 6 / 12) < 0.05, f'烏來 12~18 時＝新報第 I 張 6h（{q.get(2)}）')
prev = json.load(open('cwa_qpf_prev.json'))
ok({w['issued'] for w in prev['windows']} == {'2026-11-09T05:00:00'} and len(prev['windows']) == 4,
   '快取已換成本輪 05 時報四張（下一輪補空窗用）')
print('ALL PASS' if not fails else f'{fails} FAIL'); shutil.rmtree(work); sys.exit(1 if fails else 0)
