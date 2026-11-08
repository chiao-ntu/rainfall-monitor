"""
trigger_formosa.py
──────────────────
由外部排程（Render Cron Job）準時觸發 FORMOSA 的 GitHub Actions。

為什麼需要：
  GitHub 的 schedule（cron）在尖峰時段會被延後或直接略過。
  2026-11-05～11-08 commit 紀錄實測：設定「每小時」的主排程實際 3.5～8.5 小時
  才跑一次；設定「每 10 分鐘」的雷達／ETR2 實際 3～5 小時一次。
  結果：11/08 19:43 系統顯示的仍是氣象署 11 時那一報的定量降水預報，
  官方網站已是 17 時報 → 兩邊對不上。
  workflow_dispatch（API 觸發）不受排程節流影響，所以改由這支準時觸發；
  各 workflow 的 schedule 保留當後備。

Render Cron Job 設定：
  Schedule       3,13,23,33,43,53 * * * *     （每 10 分鐘，與原排程同分鐘）
  Build Command  pip install requests
  Command        python trigger_formosa.py
  環境變數       GH_TOKEN   fine-grained PAT：只授權 rainfall-monitor 這一個 repo，
                            權限只給 Actions: Read and write（其餘一律不給）
                 GH_REPO    chiao-ntu/rainfall-monitor（可省略，預設即此）
                 GH_BRANCH  main（可省略）

每次執行做的事（時間一律以臺灣時間判斷，Render 用 UTC 不影響）：
  每 10 分鐘   update_qpesums.yml   雷達 1h、逐時序列、ETR2 官方現值、颱風
  每小時 :23   update_rainfall.yml  主排程（觀測、各模式、氣象署定量降水預報）
               probe_latency.yml    API 延遲量測
  發布班次 :43 update_rainfall.yml  氣象署定量降水預報發布時次（02/05/08/11/14/17/20/23）
                                    補抓一次，避免官方晚送時要再等一小時
  每次         看門狗：查最近一次「成功」的主排程／雷達排程，
               超過門檻就以非 0 結束 → Render 會把這次標成失敗並通知。

安全：token 只從環境變數讀，任何輸出都不印 token 內容。
"""
import os
import sys
import time
from datetime import datetime, timezone, timedelta

import requests

GH_TOKEN = os.getenv("GH_TOKEN", "").strip()
GH_REPO = os.getenv("GH_REPO", "chiao-ntu/rainfall-monitor").strip()
GH_BRANCH = os.getenv("GH_BRANCH", "main").strip()

TPE = timezone(timedelta(hours=8))
API = "https://api.github.com"

WF_RAIN = "update_rainfall.yml"
WF_QPE = "update_qpesums.yml"
WF_LAT = "probe_latency.yml"

# 氣象署定量降水預報發布時次（常態 05/11/17/23，加強作業另加 02/08/14/20）
CWA_ISSUE_HOURS = {2, 5, 8, 11, 14, 17, 20, 23}

# 看門狗門檻（分鐘）：主排程每小時一次、執行最長 50 分 → 超過 150 分沒有成功就是出事
WATCH_LIMIT_MIN = {WF_RAIN: 150, WF_QPE: 45}


def plan(now_tpe):
    """依臺灣時間決定這一輪要觸發哪些 workflow（純函式，方便測試）。"""
    m, h = now_tpe.minute, now_tpe.hour
    wfs = [WF_QPE]
    if 20 <= m < 30:
        wfs += [WF_RAIN, WF_LAT]
    elif 40 <= m < 50 and h in CWA_ISSUE_HOURS:
        wfs.append(WF_RAIN)
    return wfs


def _headers():
    return {
        "Authorization": f"Bearer {GH_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def dispatch(wf):
    """回傳 True＝成功。5xx／逾時重試一次；4xx 直接印出 GitHub 的原因。"""
    url = f"{API}/repos/{GH_REPO}/actions/workflows/{wf}/dispatches"
    for attempt in (1, 2):
        try:
            r = requests.post(url, headers=_headers(), json={"ref": GH_BRANCH}, timeout=15)
        except requests.exceptions.RequestException as e:
            print(f"  [{wf}] 連線失敗（第 {attempt} 次）：{type(e).__name__}")
            time.sleep(5)
            continue
        if r.status_code == 204:
            print(f"  [{wf}] 觸發成功")
            return True
        why = r.text[:300].replace("\n", " ")
        if r.status_code == 401:
            print(f"  [{wf}] 401：GH_TOKEN 無效或已過期")
            return False
        if r.status_code == 403:
            print(f"  [{wf}] 403：token 權限不足（需 Actions: Read and write）→ {why}")
            return False
        if r.status_code == 404:
            print(f"  [{wf}] 404：找不到 repo 或 workflow 檔（GH_REPO={GH_REPO}），"
                  f"或 token 沒授權這個 repo → {why}")
            return False
        if r.status_code == 422:
            print(f"  [{wf}] 422：分支不存在、workflow 沒有 workflow_dispatch，"
                  f"或 workflow 檔解析失敗（例如 cron 寫錯）→ {why}")
            return False
        print(f"  [{wf}] HTTP {r.status_code}（第 {attempt} 次）→ {why}")
        if r.status_code < 500:
            return False
        time.sleep(5)
    return False


def last_success_age_min(wf, now_utc):
    """最近一次成功執行距今幾分鐘；查不到回傳 None（不判定）。"""
    url = f"{API}/repos/{GH_REPO}/actions/workflows/{wf}/runs"
    try:
        r = requests.get(url, headers=_headers(), timeout=15,
                         params={"status": "success", "branch": GH_BRANCH, "per_page": 1})
        if r.status_code != 200:
            print(f"  [看門狗] {wf} 查詢失敗 HTTP {r.status_code}")
            return None
        runs = r.json().get("workflow_runs") or []
        if not runs:
            return None
        t = datetime.strptime(runs[0]["updated_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return (now_utc - t).total_seconds() / 60
    except Exception as e:
        print(f"  [看門狗] {wf} 查詢例外：{type(e).__name__}")
        return None


# ★ 2026-11-08 補跑：上線第一晚看到主排程 7 小時沒成功，但它只排在每小時 :23，
#   要等到下一個 :23 才會被觸發。改為：只要距上次成功超過 CATCHUP_MIN、
#   且目前沒有排隊／執行中的同一支，就立刻補觸發，不等時刻表。
CATCHUP_MIN = {WF_RAIN: 70, WF_QPE: 20}


def recent_runs(wf, n=5):
    """最近 n 次執行（任何狀態）。查不到回傳 None。"""
    url = f"{API}/repos/{GH_REPO}/actions/workflows/{wf}/runs"
    try:
        r = requests.get(url, headers=_headers(), timeout=15,
                         params={"branch": GH_BRANCH, "per_page": n})
        if r.status_code != 200:
            return None
        return r.json().get("workflow_runs") or []
    except Exception:
        return None


def _run_txt(run):
    t = datetime.strptime(run["created_at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(TPE)
    ev = {"schedule": "GitHub排程", "workflow_dispatch": "外部觸發"}.get(run.get("event"), run.get("event"))
    st = run.get("conclusion") or run.get("status")
    st = {"success": "成功", "failure": "失敗", "cancelled": "取消", "in_progress": "執行中",
          "queued": "排隊中", "timed_out": "逾時中止"}.get(st, st)
    return f"{t:%m/%d %H:%M} {ev} {st}"


def main():
    if not GH_TOKEN:
        print("[ERROR] 缺少 GH_TOKEN 環境變數")
        return 1
    now_utc = datetime.now(timezone.utc)
    now_tpe = now_utc.astimezone(TPE)
    wfs = plan(now_tpe)
    print(f"[{now_tpe:%m/%d %H:%M} TST] {GH_REPO}@{GH_BRANCH} 觸發：{', '.join(wfs)}")
    failed = [wf for wf in wfs if not dispatch(wf)]

    late = []
    for wf, lim in WATCH_LIMIT_MIN.items():
        age = last_success_age_min(wf, now_utc)
        runs = recent_runs(wf) or []
        busy = any(r.get("status") in ("queued", "in_progress", "waiting", "pending") for r in runs)
        if runs:
            print(f"  [看門狗] {wf} 最近幾次：" + "；".join(_run_txt(r) for r in runs[:3]))
        if age is None:
            continue
        flag = "逾時" if age > lim else "正常"
        print(f"  [看門狗] {wf} 最近一次成功距今 {age:.0f} 分（門檻 {lim}）{flag}")
        # 補跑：太久沒成功、這輪沒排到、也沒有正在跑的 → 立刻觸發
        if age > CATCHUP_MIN[wf] and wf not in wfs and not busy:
            print(f"  [補跑] {wf} 已 {age:.0f} 分未成功且無執行中 → 立即觸發")
            if not dispatch(wf):
                failed.append(wf)
        if age > lim:
            late.append(wf)

    if failed:
        print(f"[ERROR] 觸發失敗：{', '.join(failed)}")
    # 逾時警報每小時只報一次（:20–:29 那一輪），避免每 10 分鐘寄一封失敗通知；
    #   觸發失敗（token、權限、workflow 檔壞掉）則每次都報。
    alert_late = late and 20 <= now_tpe.minute < 30
    if late:
        print(f"[{'ERROR' if alert_late else 'WARN'}] 已逾時未成功更新：{', '.join(late)}"
              " → 看上面「最近幾次」的狀態；若是「失敗」，到 GitHub Actions 點進該次看錯誤")
    return 1 if (failed or alert_late) else 0


if __name__ == "__main__":
    sys.exit(main())
