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
    njobs = len(doc['jobs'])
    name = doc.get('name') or '(無 name)'
    return True, f'OK　{name}　job {njobs} 個'


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
    print(f'\n共 {len(files)} 個檔，{bad} 個有問題')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
