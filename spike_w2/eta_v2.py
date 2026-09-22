# -*- coding: utf-8 -*-
"""W2 ETA 纯逻辑：W1 直接估计器 + ① 线别 cohort 混合 + ② 周度 p + ⑥ 补偿豁免 + ⑤ 质量标记（TDD）。

W1 表行形状（不变，见 spike_w1/eta.py 头注）：
  rem:      {(station, model): (p50, mean, p90, n)}
  loop_rem: {(station, flag, model): (p50, mean, p90, n)}
  loop_dur: {(flag, model): (l, n)}
  takt:     {(frm, to, model): (p50, mean, p90, n)}
W2 表行形状（新增，Task 3 学出）：
  fut_week: {(station, model, week): (p1, p2, n)}   # 滚动 8 周学习（week = "YYYY-Www"）
  fut_all:  {(station, model): (p1, p2, n)}         # 全窗 04-14→08-15（⑥a 前环人群净化）
  cohort:   {(station, cohort, model): (p50, mean, p90, n)}   # cohort ∈ ("08", "09")
  lineprob: {(model, week): (w08, n)}               # 滚动 8 周线概率；week=WEEK_ALL 为全窗
  quality:  {(model, station): mark}                # mark ∈ ok/low_n/mixed/both
"""
from typing import Optional

ALL = "ALL"
WEEK_ALL = "ALL"
P_WEEK_MIN_N = 50          # 周度 p 行 n≥50 才用，否则回退 fut_all（周窗小样本不稳）
COHORT_MIN_N = 30          # cohort 表行 n≥30 才用（与主分布同门槛）
LINEPROB_MIN_N = 100       # 线概率行 n≥100 才用（分派比小样本噪声大）
MIN_N = 30                 # 主/环分布行 n 门槛（与 W1 resolve 一致）


def resolve_main(table: dict, station: str, model: str, min_n: int = MIN_N) -> Optional[float]:
    """W1 主分布两级回退：(station, model) → (station, 'ALL')；行尾 n < min_n 视为不可用。"""
    for m in (model, ALL):
        row = table.get((station, m))
        if row is not None and row[-1] >= min_n:
            return row[0]
    return None


def _row_n(table: dict, key: tuple, min_n: int):
    row = table.get(key)
    if row is not None and row[-1] >= min_n:
        return row
    return None


def resolve_cohort_p50(cohort: dict, station: str, model: str,
                       lineprob: dict, week: str) -> Optional[float]:
    """① 线别 cohort 混合：P50 = w08·P50_08 + w09·P50_09。
    各 cohort P50 走 model→ALL 回退（n≥COHORT_MIN_N）；线概率走 (model, week) → (model, WEEK_ALL)
    → (ALL, week) → (ALL, WEEK_ALL)（n≥LINEPROB_MIN_N）。任一缺失 → None（调用方回退 W1 主分布）。
    非后见：线概率用过去 8 周学出（Task 3），不是该车自己的实际走线。
    """
    if not cohort:
        return None

    def c50(coh):
        for m in (model, ALL):
            row = _row_n(cohort, (station, coh, m), COHORT_MIN_N)
            if row is not None:
                return row[0]
        return None

    p50_08, p50_09 = c50("08"), c50("09")
    if p50_08 is None or p50_09 is None:
        return None
    w08 = None
    for m in (model, ALL):
        for wk in (week, WEEK_ALL):
            row = _row_n(lineprob, (m, wk), LINEPROB_MIN_N)
            if row is not None:
                w08 = row[0]
                break
        if w08 is not None:
            break
    if w08 is None:
        return None
    return w08 * p50_08 + (1.0 - w08) * p50_09


def resolve_p(fut_week: dict, fut_all: dict, station: str, model: str,
              week: str) -> tuple:
    """② 周度 p：(station, model, week) n≥P_WEEK_MIN_N → 用；否则 fut_all (station, model)
    （model→ALL 回退，行 n≥30，与 W1 resolve 默认门槛一致）；全缺 → (0.0, 0.0)。"""
    if fut_week:
        for m in (model, ALL):
            row = _row_n(fut_week, (station, m, week), P_WEEK_MIN_N)
            if row is not None:
                return row[0], row[1]
    if fut_all:
        for m in (model, ALL):
            row = _row_n(fut_all, (station, m), MIN_N)
            if row is not None:
                return row[0], row[1]
    return 0.0, 0.0


def estimate_event(t: int, station: str, flag: str, model: str, week: str,
                   tables: dict, comp_excl: frozenset = frozenset()
                   ) -> tuple:
    """单事件 ETA（W1 直接估计器 v2）。返回 (eta 绝对秒|None, used_cohort bool)。

    flag ∈ {1,2} → 环分布 P50（model→ALL，n≥30）；不加补偿（已在环内），不走 cohort。
    flag = 0 → 主分布 P50：① 先试 cohort 混合（缺 → W1 rem 表）
              + 补偿 p1·L1 + p2·L2（② 周度 p；⑥b (model,station) ∈ comp_excl → 补偿 = 0）。
    L1/L2 走 loop_dur model→ALL（n≥30），缺 → 0。
    """
    if flag in ("1", "2"):
        base = None
        for m in (model, ALL):
            row = tables["loop_rem"].get((station, flag, m))
            if row is not None and row[-1] >= MIN_N:
                base = row[0]
                break
        if base is None:
            return None, False
        return t + int(round(base)), False

    used_cohort = False
    base = resolve_cohort_p50(tables.get("cohort"), station, model,
                              tables.get("lineprob"), week)
    if base is not None:
        used_cohort = True
    if base is None:
        base = resolve_main(tables["rem"], station, model)
    if base is None:
        return None, used_cohort

    p1 = p2 = 0.0
    if (model, station) not in comp_excl:
        p1, p2 = resolve_p(tables.get("fut_week"), tables.get("fut_all"),
                           station, model, week)

    def ld(flagc):
        for m in (model, ALL):
            row = tables["loop_dur"].get((flagc, m))
            if row is not None and row[-1] >= MIN_N:
                return row[0]
        return 0.0

    eta = t + int(round(base + p1 * ld("1") + p2 * ld("2")))
    return eta, used_cohort


def quality_mark(quality: dict, model: str, station: str) -> str:
    """⑤ 质量标记：未标记默认 'ok'。mark ∈ ok/low_n/mixed/both。"""
    return quality.get((model, station), "ok")
