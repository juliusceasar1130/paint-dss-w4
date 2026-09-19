# -*- coding: utf-8 -*-
"""纯估计逻辑：三估计器对打（无 DB 依赖，单元测试覆盖）。

口径（BM 笔记 2026-09-19 六确）：
- 直接剩余分布（主候选）：flag∈{1,2} → 环分布 P50；flag=0 → 主分布 P50（干净后缀学出）
  + 前决策补偿 p(车型,站)×L(车型,环)（返工/黑顶独立相加）。
- Σ段中位数 / Σ段均值：沿车**实际经过**的窗口内路径求和（后见路径，纯诊断用途——
  验证中位数不可加 / 均值可加，非生产口径）。
- 回退链：车型行 n≥30 → 'ALL' 行 n≥30 → 无覆盖（None）。
表行形状（n 恒在行尾）：
  rem:      {(station, model): (p50, mean, p90, n)}
  loop_rem: {(station, flag, model): (p50, mean, p90, n)}
  fut:      {(station, model): (p1, p2, n)}
  loop_dur: {(flag, model): (l, n)}
  takt:     {(frm, to, model): (p50, mean, p90, n)}
"""
from dataclasses import dataclass
from typing import Optional, Sequence

ALL = "ALL"


@dataclass(frozen=True)
class Event:
    t: int            # epoch 秒（任意单调基准，全链路一致即可）
    station: str
    flag: str         # '0' | '1' | '2'


@dataclass(frozen=True)
class EventEta:
    t: int
    station: str
    flag: str
    remaining_actual: int        # exit_t - t（秒）
    eta_direct: Optional[int]    # 绝对 epoch 秒；None = 无覆盖
    eta_sum_median: Optional[int]
    eta_sum_mean: Optional[int]


def resolve(table: dict, key: tuple, model: str,
            stat_idx: int = 0, min_n: int = 30) -> Optional[float]:
    """两级回退取统计量：(key, model) → (key, 'ALL')；行尾 n < min_n 视为不可用。"""
    for m in (model, ALL):
        row = table.get(key + (m,))
        if row is not None and row[-1] >= min_n:
            return row[stat_idx]
    return None


def estimate_vehicle(events: Sequence[Event], exit_t: int,
                     tables: dict, model: str) -> list[EventEta]:
    """逐事件 ETA。events = 完整窗口内行程 [entry, ..., exit]（含下线点事件）。
    返回 len(events)-1 条（下线事件本身 remaining=0，无预测信息，不进样本）。
    Σ 估计器按整车 all-or-none：任一段无覆盖 → 该车两个 Σ 估计器全 None。
    """
    n = len(events)

    # --- Σ 估计器：沿实际路径的段级统计（后见路径，诊断用途） ---
    segs = []
    seg_ok = True
    for a, b in zip(events, events[1:]):
        p50 = resolve(tables["takt"], (a.station, b.station), model, stat_idx=0)
        mean = resolve(tables["takt"], (a.station, b.station), model, stat_idx=1)
        if p50 is None or mean is None:
            seg_ok = False
            break
        segs.append((p50, mean))
    if seg_ok:
        ssum_med = [0.0] * (n - 1)
        ssum_mean = [0.0] * (n - 1)
        for i in range(n - 2, -1, -1):          # 后缀和：ssum[i] = Σ segs[i:]
            prev_med = ssum_med[i + 1] if i + 1 < n - 1 else 0.0
            prev_mean = ssum_mean[i + 1] if i + 1 < n - 1 else 0.0
            ssum_med[i] = prev_med + segs[i][0]
            ssum_mean[i] = prev_mean + segs[i][1]
    else:
        ssum_med = ssum_mean = None

    out = []
    for i, ev in enumerate(events):
        if i == n - 1:
            continue                            # 下线事件本身不进样本
        if ev.flag in ("1", "2"):               # 环内：直接用环分布，不加补偿
            base = resolve(tables["loop_rem"], (ev.station, ev.flag), model)
            eta_direct = ev.t + int(round(base)) if base is not None else None
        else:                                   # flag=0：主分布 + 前决策补偿
            base = resolve(tables["rem"], (ev.station,), model)
            if base is None:
                eta_direct = None
            else:
                p1 = resolve(tables["fut"], (ev.station,), model, stat_idx=0) or 0.0
                p2 = resolve(tables["fut"], (ev.station,), model, stat_idx=1) or 0.0
                l1 = resolve(tables["loop_dur"], ("1",), model) or 0.0
                l2 = resolve(tables["loop_dur"], ("2",), model) or 0.0
                eta_direct = ev.t + int(round(base + p1 * l1 + p2 * l2))
        eta_med = int(round(ev.t + ssum_med[i])) if ssum_med is not None else None
        eta_mean = int(round(ev.t + ssum_mean[i])) if ssum_mean is not None else None
        out.append(EventEta(t=ev.t, station=ev.station, flag=ev.flag,
                            remaining_actual=exit_t - ev.t, eta_direct=eta_direct,
                            eta_sum_median=eta_med, eta_sum_mean=eta_mean))
    return out
