# -*- coding: utf-8 -*-
"""預報校驗指標的鑑別力比較
=========================================================================
問題：現在主圖用 CSI，但各模式分數都很低且擠在一起，看不出差異，
      也看不出 FORMOSA 存在的必要。

作法：造一組「已知技術高低」的模式，看哪個指標能把它們分開、
      而且排序與真實技術一致。指標的價值就是鑑別力 ——
      分不開模式的指標，拿來做主圖沒有意義。

資料生成貼近臺灣日雨量：約 7 成鄉鎮日無雨，其餘對數常態、具重尾。
模式誤差用對數空間的相關係數控制技術高低（rho 越高越準），
另加系統性偏差，模擬真實模式的高估／低估傾向。
"""
import math
import random

random.seed(20261009)

N_TOWN, N_DAY = 159, 120
RAIN_THRESHOLD = 1.0
HIT_TOL, MISS_TOL = 0.30, 0.60


# ── 真值：臺灣型日雨量 ────────────────────────────────────────────
def gen_truth():
    out = []
    for _ in range(N_DAY):
        wet_frac = random.choice([0.05, 0.10, 0.20, 0.45, 0.70])   # 乾日~鋒面日
        day = []
        for _ in range(N_TOWN):
            if random.random() > wet_frac:
                day.append(0.0)
            else:
                v = math.exp(random.gauss(2.6, 1.15))              # 中位數 ~13mm
                day.append(round(min(v, 900.0), 1))
        out.append(day)
    return out


# ── 模式：以對數空間相關度控制技術 ───────────────────────────────
def gen_model(truth, rho, bias, wet_bias=0.0):
    """rho：與真值的對數相關（技術）；bias：乘法偏差；wet_bias：亂報傾向"""
    out = []
    s = math.sqrt(max(0.0, 1 - rho * rho)) * 1.15
    for day in truth:
        row = []
        for o in day:
            if o < RAIN_THRESHOLD:
                # 無雨日：以 wet_bias 機率亂報
                row.append(round(math.exp(random.gauss(2.0, 1.0)), 1)
                           if random.random() < wet_bias else 0.0)
                continue
            lo = math.log(max(o, 0.1))
            lm = rho * lo + (1 - rho) * 2.6 + random.gauss(0, s) + math.log(bias)
            v = math.exp(lm)
            # 報不到的機率隨技術下降而上升
            row.append(0.0 if random.random() < (1 - rho) * 0.35 else round(v, 1))
        out.append(row)
    return out


# ── 兩套列聯表 ───────────────────────────────────────────────────
def tab_tolerance(truth, model):
    """現行 CSI 用的判定：命中需「兩邊有雨且誤差 ≤30%」"""
    c = {'hit': 0, 'miss': 0, 'false': 0, 'correct_neg': 0}
    for dt, dm in zip(truth, model):
        for o, m in zip(dt, dm):
            orn, mrn = o >= RAIN_THRESHOLD, m >= RAIN_THRESHOLD
            if not orn and not mrn: c['correct_neg'] += 1; continue
            if mrn and not orn:     c['false'] += 1; continue
            if orn and not mrn:     c['miss'] += 1; continue
            err = abs(m - o) / max(o, 1e-9)
            if err <= HIT_TOL:      c['hit'] += 1
            elif err <= MISS_TOL:   c['false' if m > o else 'miss'] += 1
            else:                   c['miss' if m < o else 'false'] += 1
    return c


def tab_threshold(truth, model, thr):
    """標準判定：兩邊是否跨過門檻"""
    c = {'hit': 0, 'miss': 0, 'false': 0, 'correct_neg': 0}
    for dt, dm in zip(truth, model):
        for o, m in zip(dt, dm):
            o2, m2 = o >= thr, m >= thr
            k = 'hit' if (o2 and m2) else 'false' if m2 else 'miss' if o2 else 'correct_neg'
            c[k] += 1
    return c


# ── 指標 ─────────────────────────────────────────────────────────
def csi(c):
    d = c['hit'] + c['miss'] + c['false']
    return c['hit'] / d if d else None


def pod(c):
    d = c['hit'] + c['miss']
    return c['hit'] / d if d else None


def far(c):
    d = c['hit'] + c['false']
    return c['false'] / d if d else None


def bias(c):
    d = c['hit'] + c['miss']
    return (c['hit'] + c['false']) / d if d else None


def ets(c):
    h, m, f = c['hit'], c['miss'], c['false']
    n = h + m + f + c['correct_neg']
    if not n: return None
    hr = (h + f) * (h + m) / n
    den = h + m + f - hr
    return (h - hr) / den if abs(den) > 1e-9 else None


def sedi(c):
    h, m, f, cn = c['hit'], c['miss'], c['false'], c['correct_neg']
    if not (h + m) or not (f + cn): return None
    H, F = h / (h + m), f / (f + cn)
    if not (0 < H < 1) or not (0 < F < 1): return None
    num = math.log(F) - math.log(H) - math.log(1 - F) + math.log(1 - H)
    den = math.log(F) + math.log(H) + math.log(1 - F) + math.log(1 - H)
    return num / den if abs(den) > 1e-9 else None


def mae(truth, model):
    s = n = 0
    for dt, dm in zip(truth, model):
        for o, m in zip(dt, dm):
            s += abs(m - o); n += 1
    return s / n if n else None


# ── 建立「已知技術高低」的模式群 ─────────────────────────────────
truth = gen_truth()
SPEC = [                       # (名稱, rho＝真實技術, 乘法偏差, 亂報傾向)
    ('模式A 最佳', 0.82, 1.00, 0.015),
    ('模式B 良好', 0.74, 1.15, 0.020),
    ('模式C 中等', 0.66, 0.85, 0.025),
    ('模式D 普通', 0.58, 1.30, 0.035),
    ('模式E 偏弱', 0.50, 0.70, 0.045),
    ('模式F 最弱', 0.40, 1.60, 0.070),
]
models = {nm: gen_model(truth, rho, b, w) for nm, rho, b, w in SPEC}
TRUE_RANK = {nm: i for i, (nm, *_) in enumerate(SPEC)}      # 0＝最準

# FORMOSA：量軌道＝成員平均；警戒軌道＝85 分位數
member_names = [nm for nm, *_ in SPEC]


def blend(mode='mean'):
    out = []
    for d in range(N_DAY):
        row = []
        for t in range(N_TOWN):
            vs = sorted(models[nm][d][t] for nm in member_names)
            if mode == 'mean':
                row.append(sum(vs) / len(vs))
            else:
                k = min(len(vs) - 1, int(math.ceil(0.85 * len(vs)) - 1))
                row.append(vs[k])
        out.append(row)
    return out


models['FORMOSA 量'] = blend('mean')
models['FORMOSA 警戒'] = blend('warn')

ALL = member_names + ['FORMOSA 量', 'FORMOSA 警戒']

# ── 計算所有指標 ─────────────────────────────────────────────────
rows = {}
for nm in ALL:
    mdl = models[nm]
    tt = tab_tolerance(truth, mdl)
    t1 = tab_threshold(truth, mdl, 1)
    t10 = tab_threshold(truth, mdl, 10)
    t50 = tab_threshold(truth, mdl, 50)
    t80 = tab_threshold(truth, mdl, 80)
    rows[nm] = {
        'CSI(現行±30%)': csi(tt),
        'CSI(≥1mm門檻)': csi(t1),
        'ETS(≥10mm)': ets(t10),
        'ETS(≥50mm)': ets(t50),
        'SEDI(≥50mm)': sedi(t50),
        'SEDI(≥80mm)': sedi(t80),
        'POD(≥50mm)': pod(t50),
        'FAR(≥50mm)': far(t50),
        '偏差比(≥50mm)': bias(t50),
        'MAE': mae(truth, mdl),
    }

METRICS = list(rows[ALL[0]].keys())

print('=' * 94)
print('各指標分數（模式依「真實技術」由高到低排列）')
print('=' * 94)
hdr = f'{"模式":<14}' + ''.join(f'{m:>15}' for m in METRICS[:6])
print(hdr)
for nm in ALL:
    line = f'{nm:<14}'
    for m in METRICS[:6]:
        v = rows[nm][m]
        line += f'{(f"{v:.3f}" if v is not None else "—"):>15}'
    print(line)
print()
hdr2 = f'{"模式":<14}' + ''.join(f'{m:>15}' for m in METRICS[6:])
print(hdr2)
for nm in ALL:
    line = f'{nm:<14}'
    for m in METRICS[6:]:
        v = rows[nm][m]
        line += f'{(f"{v:.3f}" if v is not None else "—"):>15}'
    print(line)

# ── 鑑別力：分數散佈 + 排序正確性 ───────────────────────────────
print()
print('=' * 94)
print('鑑別力評比（只看 6 個成員模式，FORMOSA 不列入排序檢驗）')
print('=' * 94)
print(f'{"指標":<18}{"分數範圍":>22}{"相對散佈":>12}{"排序正確":>12}{"判定":>10}')
print('-' * 94)


def spearman(a, b):
    """兩個排名的等級相關"""
    n = len(a)
    d2 = sum((a[i] - b[i]) ** 2 for i in range(n))
    return 1 - 6 * d2 / (n * (n * n - 1))


summary = []
for m in METRICS:
    vals = [(nm, rows[nm][m]) for nm in member_names if rows[nm][m] is not None]
    if len(vals) < 4:
        print(f'{m:<18}{"資料不足":>22}')
        continue
    lo = min(v for _, v in vals); hi = max(v for _, v in vals)
    mean = sum(v for _, v in vals) / len(vals)
    # 相對散佈：全距 ÷ 平均（越大越能分開模式）
    spread = (hi - lo) / abs(mean) if mean else 0
    lower_better = m.startswith('FAR') or m == 'MAE'
    order = sorted(vals, key=lambda kv: kv[1], reverse=not lower_better)
    got = {nm: i for i, (nm, _) in enumerate(order)}
    rho = spearman([TRUE_RANK[nm] for nm, _ in vals], [got[nm] for nm, _ in vals])
    verdict = ('好' if spread >= 0.5 and rho >= 0.9 else
               '可' if spread >= 0.25 and rho >= 0.8 else '差')
    summary.append((m, spread, rho, verdict))
    print(f'{m:<18}{f"{lo:.3f} ~ {hi:.3f}":>22}{spread:>11.0%}{rho:>+12.2f}{verdict:>10}')

print()
print('※ 相對散佈＝(最高−最低)÷平均。太小代表所有模式分數擠在一起，主圖看不出差異。')
print('※ 排序正確＝與「真實技術高低」的等級相關，+1.00 為完全一致。')

# ── FORMOSA 的必要性：每日最佳模式會換人嗎 ──────────────────────
print()
print('=' * 94)
print('FORMOSA 存在的理由：最佳單一模式是否固定？')
print('=' * 94)
best_count = {nm: 0 for nm in member_names}
rank_of = {nm: [] for nm in ALL}
for d in range(N_DAY):
    day_t = [truth[d]]
    sc = {}
    for nm in ALL:
        c = tab_threshold(day_t, [models[nm][d]], 50)
        e = ets(c)
        sc[nm] = e if e is not None else -9
    order = sorted(ALL, key=lambda n: -sc[n])
    for i, nm in enumerate(order):
        rank_of[nm].append(i + 1)
    bm = max(member_names, key=lambda n: sc[n])
    best_count[bm] += 1

print(f'{"模式":<14}{"當日最佳次數":>14}{"平均名次":>12}{"最差名次":>12}{"進前三比例":>14}')
print('-' * 94)
for nm in ALL:
    r = rank_of[nm]
    avg = sum(r) / len(r)
    top3 = sum(1 for x in r if x <= 3) / len(r)
    bc = best_count.get(nm, None)
    print(f'{nm:<14}{(str(bc) if bc is not None else "—"):>14}'
          f'{avg:>12.2f}{max(r):>12}{top3:>13.0%}')

print()
print('※ 若「當日最佳」每天換人，事前就無從挑出該信哪一個 —— 這正是融合的理由。')
print('※ 融合的價值不在「單日最強」，而在「平均名次最前、最差名次不難看」。')


# ═════════════════════════════════════════════════════════════════
#  第二組：真實情境 —— 模式彼此接近
#  第一組的技術差距（rho 0.40~0.82）遠大於實際的 NWP 模式。
#  真實的 ECMWF/GFS/JMA 差距小得多，主圖要能分開的正是這種情況。
# ═════════════════════════════════════════════════════════════════
print()
print('=' * 94)
print('第二組：真實情境（模式技術接近，rho 0.70~0.80）')
print('=' * 94)
random.seed(777)
truth2 = gen_truth()
SPEC2 = [
    ('模式A', 0.80, 1.00, 0.020),
    ('模式B', 0.78, 1.10, 0.022),
    ('模式C', 0.76, 0.92, 0.024),
    ('模式D', 0.74, 1.18, 0.026),
    ('模式E', 0.72, 0.88, 0.030),
    ('模式F', 0.70, 1.25, 0.034),
]
m2 = {nm: gen_model(truth2, rho, b, w) for nm, rho, b, w in SPEC2}
TRUE2 = {nm: i for i, (nm, *_) in enumerate(SPEC2)}
mem2 = [nm for nm, *_ in SPEC2]


def blend2(mode):
    out = []
    for d in range(N_DAY):
        row = []
        for t in range(N_TOWN):
            vs = sorted(m2[nm][d][t] for nm in mem2)
            if mode == 'mean':
                row.append(sum(vs) / len(vs))
            else:
                k = min(len(vs) - 1, int(math.ceil(0.85 * len(vs)) - 1))
                row.append(vs[k])
        out.append(row)
    return out


m2['FORMOSA 量'] = blend2('mean')
m2['FORMOSA 警戒'] = blend2('warn')
ALL2 = mem2 + ['FORMOSA 量', 'FORMOSA 警戒']

rows2 = {}
for nm in ALL2:
    mdl = m2[nm]
    rows2[nm] = {
        'CSI(現行±30%)': csi(tab_tolerance(truth2, mdl)),
        'CSI(≥1mm門檻)': csi(tab_threshold(truth2, mdl, 1)),
        'ETS(≥10mm)': ets(tab_threshold(truth2, mdl, 10)),
        'ETS(≥50mm)': ets(tab_threshold(truth2, mdl, 50)),
        'SEDI(≥50mm)': sedi(tab_threshold(truth2, mdl, 50)),
        'SEDI(≥80mm)': sedi(tab_threshold(truth2, mdl, 80)),
        'FAR(≥50mm)': far(tab_threshold(truth2, mdl, 50)),
        '偏差比(≥50mm)': bias(tab_threshold(truth2, mdl, 50)),
        'MAE': mae(truth2, mdl),
    }
M2 = list(rows2[ALL2[0]].keys())
print(f'{"模式":<14}' + ''.join(f'{m:>15}' for m in M2[:6]))
for nm in ALL2:
    print(f'{nm:<14}' + ''.join(
        f'{(f"{rows2[nm][m]:.3f}" if rows2[nm][m] is not None else "—"):>15}' for m in M2[:6]))

print()
print(f'{"指標":<18}{"分數範圍":>22}{"相對散佈":>12}{"排序正確":>12}{"判定":>10}')
print('-' * 94)
for m in M2:
    vals = [(nm, rows2[nm][m]) for nm in mem2 if rows2[nm][m] is not None]
    if len(vals) < 4: continue
    lo = min(v for _, v in vals); hi = max(v for _, v in vals)
    mean = sum(v for _, v in vals) / len(vals)
    spread = (hi - lo) / abs(mean) if mean else 0
    lower_better = m.startswith('FAR') or m == 'MAE'
    order = sorted(vals, key=lambda kv: kv[1], reverse=not lower_better)
    got = {nm: i for i, (nm, _) in enumerate(order)}
    rho = spearman([TRUE2[nm] for nm, _ in vals], [got[nm] for nm, _ in vals])
    verdict = ('好' if spread >= 0.25 and rho >= 0.8 else
               '可' if spread >= 0.12 and rho >= 0.6 else '差')
    print(f'{m:<18}{f"{lo:.3f} ~ {hi:.3f}":>22}{spread:>11.0%}{rho:>+12.2f}{verdict:>10}')

# FORMOSA 相對最佳單一模式的優勢
print()
print('FORMOSA 相對「最佳單一模式」的優勢（真實情境）：')
for m in M2:
    lower_better = m.startswith('FAR') or m == 'MAE'
    vals = [(nm, rows2[nm][m]) for nm in mem2 if rows2[nm][m] is not None]
    if not vals: continue
    bestv = (min if lower_better else max)(v for _, v in vals)
    bn = [nm for nm, v in vals if v == bestv][0]
    fv = rows2['FORMOSA 量'][m]
    if fv is None: continue
    better = (fv < bestv) if lower_better else (fv > bestv)
    d = (fv - bestv) / abs(bestv) * 100 if bestv else 0
    print(f'   {m:<16} 最佳單一={bestv:.3f}（{bn}）　FORMOSA 量={fv:.3f}'
          f'　{"勝" if better else "負"} {d:+.1f}%')
