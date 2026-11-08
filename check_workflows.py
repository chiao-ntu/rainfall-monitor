#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""檢查 .github/workflows/ 裡的檔案是不是合法的 GitHub Actions workflow。

為什麼需要這支：
  同樣是 .yml，用途完全不同，放錯地方 GitHub 會直接報
  "Invalid workflow file"，而且錯誤訊息（Unexpected value 'stages' /
  A sequence was not expected）看不出根因是「這個檔根本不該在這裡」。
  實際發生過三次：GitLab 的設定檔、以及兩個「要貼進既有 workflow 的片段」
  被放進了 workflows 資料夾。

三種 .yml 的去處：
  ① GitHub workflow（有 name/on/jobs）→ .github/workflows/
  ② GitLab CI（有 stages/pages 等 GitLab 關鍵字）→ repo 根目錄，檔名 .gitlab-ci.yml
  ③ 步驟片段（最外層就是 `- name:`）→ 不是檔案，是要貼進別的 workflow 的內容

用法：
    python3 check_workflows.py           # 檢查 .github/workflows/
    python3 check_workflows.py <目錄>
回傳碼 0＝全部合法，1＝有問題。
"""
import os
import sys

try:
    import yaml
except ImportError:
    print('!! 需要 pyyaml：pip install pyyaml', file=sys.stderr)
    sys.exit(2)

GITLAB_KEYS = {'stages', 'pages', 'image', 'before_script', 'variables',
               'include', 'default', 'workflow'}

# ★★★ 2026-10-22：cron 欄位檢查。
#   事故：weather-bot 的 check_weather.yml 寫成 cron: "300 * * * *"，
#   分鐘欄位只能 0–59。GitHub 因此**整個檔案解析失敗**，後果遠大於
#   「排程不準」——連 workflow_dispatch（手動／API 觸發）都回 422，
#   Render 每 10 分鐘打過來也全部失敗，bot 從那行被加進去就全停了。
#   而本支腳本當時只驗結構與放錯位置，這個值通得過，所以沒擋住。
#
#   欄位範圍依 GitHub Actions 文件（POSIX cron，5 欄）。
CRON_FIELDS = (
    ('分鐘', 0, 59, None),
    ('小時', 0, 23, None),
    ('日', 1, 31, None),
    ('月', 1, 12, ('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN',
                   'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC')),
    ('星期', 0, 6, ('SUN', 'MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT')),
)


def _cron_atom(atom, lo, hi, names):
    """驗單一值（可為數字或三字母名稱）。回傳錯誤字串或 None。"""
    a = atom.strip()
    if not a:
        return '有空值'
    if names and a.upper() in names:
        return None
    if not a.lstrip('-').isdigit():
        return f'「{a}」不是數字'
    v = int(a)
    if not (lo <= v <= hi):
        return f'「{a}」超出範圍（應為 {lo}–{hi}）'
    return None


def check_cron(expr):
    """驗 GitHub Actions 的 cron 字串。回傳 (ok, 說明)。"""
    if not isinstance(expr, str):
        return False, f'cron 不是字串（{type(expr).__name__}）'
    e = expr.strip()
    if e.startswith('@'):
        #   GitHub Actions 不支援 @daily / @hourly 這類簡寫
        return False, f'「{e}」GitHub Actions 不支援 @ 簡寫，須寫成 5 欄 cron'
    parts = e.split()
    if len(parts) != 5:
        return False, (f'「{e}」有 {len(parts)} 欄，cron 必須正好 5 欄'
                       '（分 時 日 月 星期）')
    for part, (fname, lo, hi, names) in zip(parts, CRON_FIELDS):
        body, _, step = part.partition('/')
        if _ and (not step.isdigit() or int(step) < 1):
            return False, f'{fname}欄「{part}」的間隔值不合法'
        for rng in body.split(','):
            if rng == '*':
                continue
            if '-' in rng.lstrip('-') or (rng.count('-') == 1
                                          and not rng.startswith('-')):
                a, _d, b = rng.partition('-')
                for one in (a, b):
                    err = _cron_atom(one, lo, hi, names)
                    if err:
                        return False, f'{fname}欄{err}'
                continue
            err = _cron_atom(rng, lo, hi, names)
            if err:
                return False, f'{fname}欄{err}'
    #   GitHub 最短間隔 5 分鐘；語法合法但實際不會照跑，屬提醒不是錯誤
    m = parts[0]
    warn = ''
    if m.startswith('*/') and m[2:].isdigit() and int(m[2:]) < 5:
        warn = '（提醒：GitHub 排程最短間隔 5 分鐘，比這更密不會照跑）'
    return True, 'OK' + warn


def _crons_in(doc):
    """取出 on.schedule 底下所有 cron 字串。"""
    on = doc.get('on')
    if on is None:
        on = doc.get(True)        # 裸的 on: 被 YAML 1.1 解析成 True
    if not isinstance(on, dict):
        return []
    sch = on.get('schedule')
    if not isinstance(sch, list):
        return []
    return [item.get('cron') for item in sch
            if isinstance(item, dict) and 'cron' in item]


# ★★★ 2026-11-08：累積型檔案沒有 commit ＝ 每一輪都從空檔開始。
#   事故（兩件同類）：
#     ‧ forecast_log.json（fetch_rainfall.py 寫）不在 update_rainfall.yml 的 git add 清單
#       → CWA 官方 QPF 一天都沒被校驗過（預報存檔永遠是空的）。
#     ‧ etr2_history.json（fetch_qpesums_hourly.py 寫）不在 update_qpesums.yml 的清單
#       → 官方 ETR2 逐段歷史每 10 分鐘歸零一次。
#   先前 obs_history／model_skill 也出過一樣的事，靠人記不住，改成機器檢查：
#   workflow 執行的 .py 裡，凡是「先讀回（os.path.exists(常數)）再寫出（open(常數,'w')）」
#   的 *_FILE 常數，就是累積型，該 workflow 必須 git add 它。
import re as _re


def _accum_files(py_path):
    try:
        src = open(py_path, encoding='utf-8').read()
    except Exception:
        return {}
    consts = dict(_re.findall(r'^([A-Z][A-Z0-9_]*FILE)\s*=\s*[\'"]([^\'"]+)[\'"]', src, _re.M))
    out = {}
    for c, fn in consts.items():
        reads = _re.search(rf'os\.path\.exists\(\s*{c}\s*\)', src)
        writes = _re.search(rf"open\(\s*{c}\s*,\s*['\"]w", src)
        if reads and writes:
            out[c] = fn
    return out


#   不必 commit 的例外（理由寫清楚，不可只為了讓檢查通過而加）
PERSIST_EXEMPT = {
    'data.json',            # 主輸出，另有專門的 git add（不是累積型，但每輪都會 add）
    'cwa_qpf_source.json',  # 探測快取：沒有就重新掃描，不影響正確性
}


def check_persist(raw, wf_dir):
    """回傳缺 commit 的說明清單（空＝通過）。只檢查本 workflow 會執行的 .py。"""
    runs = set(_re.findall(r'python3?\s+([\w./-]+\.py)', raw))
    added = set(_re.findall(r'git\s+add\s+(?:-[A-Za-z]+\s+)*([\w./-]+)', raw))
    roots = [os.path.normpath(os.path.join(wf_dir, '..', '..')), wf_dir, os.getcwd()]
    miss = []
    for py in sorted(runs):
        path = next((os.path.join(r, py) for r in roots if os.path.exists(os.path.join(r, py))), None)
        if not path:
            continue
        for c, fn in sorted(_accum_files(path).items()):
            if fn in PERSIST_EXEMPT or fn in added:
                continue
            miss.append(f'{py} 會累積寫入 {fn}（{c}），但本 workflow 沒有 git add → 每輪從空檔開始')
    return miss


def check_one(path):
    """回傳 (ok, 說明)。"""
    with open(path, encoding='utf-8') as f:
        raw = f.read()
    try:
        doc = yaml.safe_load(raw)
    except Exception as e:
        return False, f'YAML 解析失敗：{str(e)[:120]}'

    if isinstance(doc, list):
        return False, ('最外層是序列（開頭就是 `- name:`）＝這是「步驟片段」，'
                       '不是 workflow。請把內容貼進既有 workflow 的 steps:，'
                       '並把這個檔移出 workflows 資料夾')
    if not isinstance(doc, dict):
        return False, '內容不是對應表，不是合法 workflow'

    keys = set(doc.keys())
    # PyYAML 會把裸的 on: 解析成 True（YAML 1.1 的布林）
    has_on = ('on' in keys) or (True in keys)
    if 'jobs' not in keys:
        gl = keys & GITLAB_KEYS
        if gl and not has_on:
            return False, (f'看起來是 GitLab CI 設定（含 {sorted(gl)}）。'
                           'GitHub 不認得，請移到 repo 根目錄並命名為 .gitlab-ci.yml，'
                           '或刪除')
        return False, '缺少必要的 jobs:，不是合法的 GitHub workflow'
    if not has_on:
        return False, '缺少 on:（觸發條件），workflow 永遠不會執行'
    if not isinstance(doc.get('jobs'), dict) or not doc['jobs']:
        return False, 'jobs: 是空的'
    #  ★ cron 一壞，整個 workflow 解析失敗 —— 連手動觸發都會 422，
    #    所以這裡視為不合格，不是警告。
    for expr in _crons_in(doc):
        ok, why = check_cron(expr)
        if not ok:
            return False, f'cron 不合法：{why}　→ 整個 workflow 會無法解析，' \
                          '連 workflow_dispatch 都會回 422'
    #  ★ 累積型檔案必須 commit（見 check_persist）
    miss = check_persist(raw, os.path.dirname(os.path.abspath(path)))
    if miss:
        return False, '；'.join(miss)
    njobs = len(doc['jobs'])
    name = doc.get('name') or '(無 name)'
    _c = _crons_in(doc)
    _cn = f'　cron {len(_c)} 條' if _c else ''
    return True, f'OK　{name}　job {njobs} 個{_cn}'


def _on_of(doc):
    on = doc.get('on', doc.get(True)) if isinstance(doc, dict) else None
    if isinstance(on, str):
        return {on: None}
    if isinstance(on, list):
        return {k: None for k in on}
    return on if isinstance(on, dict) else {}


# ★★★ 2026-11-08：跨檔檢查。
#   事故一：publish_to_public.yml 的 workflow_run 填「資料更新」，實際名稱是
#     「降雨監測主排程」→ 名稱不符 GitHub 不報錯，只是永遠不觸發。
#   事故二：GitHub 排程在尖峰會被延後／略過（實測每小時排程 3.5～8.5 小時才跑一次），
#     改由外部（Render Cron → trigger_formosa.py）以 workflow_dispatch 觸發；
#     有 schedule 的 workflow 若少了 workflow_dispatch，外部觸發會回 422。
def check_cross(docs):
    """docs: {檔名: doc}。回傳問題清單（空＝通過）。"""
    names = {d.get('name') for d in docs.values() if isinstance(d, dict) and d.get('name')}
    issues = []
    for fn, doc in sorted(docs.items()):
        if not isinstance(doc, dict):
            continue
        on = _on_of(doc)
        wr = on.get('workflow_run') if isinstance(on, dict) else None
        if isinstance(wr, dict):
            for w in wr.get('workflows') or []:
                if w not in names:
                    issues.append(f'{fn}：workflow_run 指向「{w}」，但沒有任何 workflow 叫這個名字'
                                  f'（現有：{"、".join(sorted(names))}）→ 永遠不會觸發')
        if 'schedule' in on and 'workflow_dispatch' not in on:
            issues.append(f'{fn}：有 schedule 卻沒有 workflow_dispatch → 外部排程無法觸發，'
                          '只能靠會被 GitHub 延後的 cron')
    return issues


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else '.github/workflows'
    if not os.path.isdir(d):
        print(f'找不到目錄：{d}')
        return 1
    files = sorted(f for f in os.listdir(d)
                   if f.endswith(('.yml', '.yaml')))
    if not files:
        print(f'{d} 裡沒有 .yml/.yaml')
        return 0
    bad = 0
    for f in files:
        ok, msg = check_one(os.path.join(d, f))
        print(f'{"  " if ok else "!!"} {f:34s} {msg}')
        if not ok:
            bad += 1
    docs = {}
    for f in files:
        try:
            with open(os.path.join(d, f), encoding='utf-8') as fh:
                docs[f] = yaml.safe_load(fh)
        except Exception:
            pass
    for msg in check_cross(docs):
        print(f'!! {msg}')
        bad += 1
    print(f'\n共 {len(files)} 個檔，{bad} 個有問題')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
