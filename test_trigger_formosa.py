"""trigger_formosa.py：觸發時刻規劃、錯誤處理、看門狗、token 不外洩。"""
import io, os, sys, contextlib
from datetime import datetime, timezone, timedelta
os.environ['GH_TOKEN'] = 'ghp_SECRET_SHOULD_NOT_PRINT_1234'
import importlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import trigger_formosa as T
importlib.reload(T)
TPE = timezone(timedelta(hours=8))
fails = 0
def ok(c, m):
    global fails
    print(('  OK ' if c else '  !! ') + m); fails += (not c)

P = lambda h, mi: T.plan(datetime(2026, 11, 8, h, mi, tzinfo=TPE))
ok(P(19, 3) == [T.WF_QPE], ':03 只有雷達')
ok(P(19, 23) == [T.WF_QPE, T.WF_RAIN, T.WF_LAT], ':23 雷達＋主排程＋延遲量測')
ok(P(17, 43) == [T.WF_QPE, T.WF_RAIN], '17:43 發布班次補抓主排程')
ok(P(18, 43) == [T.WF_QPE], '18:43 非發布班次不補抓')
ok(P(2, 43) == [T.WF_QPE, T.WF_RAIN], '02:43 加強作業班次也補抓')
# 一天內：主排程每小時至少一次、雷達 144 次
n_rain = n_qpe = 0
for h in range(24):
    for mi in (3, 13, 23, 33, 43, 53):
        p = P(h, mi); n_rain += p.count(T.WF_RAIN); n_qpe += p.count(T.WF_QPE)
ok(n_qpe == 144 and n_rain == 32, f'一天觸發次數：雷達 {n_qpe}、主排程 {n_rain}（24＋8 班次補抓）')

class R:
    def __init__(s, code, text='', js=None): s.status_code, s.text, s._js = code, text, js
    def json(s): return s._js
calls = []
def fake_post(url, headers=None, json=None, timeout=None):
    calls.append(url); return fake_post.resp.pop(0)
T.requests.post = fake_post
T.time.sleep = lambda s: None
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    fake_post.resp = [R(204)]; a = T.dispatch('x.yml')
    fake_post.resp = [R(502, 'bad gw'), R(204)]; b = T.dispatch('x.yml')
    fake_post.resp = [R(422, '{"message":"Workflow does not have workflow_dispatch trigger"}')]; c = T.dispatch('x.yml')
    fake_post.resp = [R(403, 'Resource not accessible')]; d = T.dispatch('x.yml')
out = buf.getvalue()
ok(a and b and not c and not d, '204 成功、5xx 重試後成功、422/403 失敗')
ok('workflow_dispatch trigger' in out, '422 會印出 GitHub 回傳的原因')
ok('SECRET' not in out, 'token 不出現在輸出')

now = datetime(2026, 11, 8, 11, 47, tzinfo=timezone.utc)
def fake_get(url, headers=None, timeout=None, params=None):
    t = (now - timedelta(minutes=fake_get.age[url.split('/')[-2]])).strftime('%Y-%m-%dT%H:%M:%SZ')
    return R(200, js={'workflow_runs': [{'updated_at': t}]})
T.requests.get = fake_get
fake_get.age = {T.WF_RAIN: 252, T.WF_QPE: 10}
ok(round(T.last_success_age_min(T.WF_RAIN, now)) == 252, '看門狗：讀最近一次成功時間')
class FDT(datetime):
    @classmethod
    def now(cls, tz=None): return now
T.datetime = FDT
fake_post.resp = [R(204)] * 5
buf = io.StringIO()
with contextlib.redirect_stdout(buf): rc = T.main()
ok(rc == 1 and '逾時未成功更新：update_rainfall.yml' in buf.getvalue(), '主排程 252 分未成功 → 非 0 結束')
fake_get.age = {T.WF_RAIN: 40, T.WF_QPE: 10}
fake_post.resp = [R(204)] * 5
with contextlib.redirect_stdout(io.StringIO()): rc = T.main()
ok(rc == 0, '都在門檻內 → 0')
print('ALL PASS' if not fails else f'{fails} FAIL'); sys.exit(1 if fails else 0)
