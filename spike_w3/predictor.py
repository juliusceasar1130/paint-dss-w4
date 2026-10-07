# -*- coding: utf-8 -*-
"""T3.1 滚动 ETA（docs/04 §0 冻结接口，模块级纯函数；TDD 纯度：无隐藏状态）。

- rem_of：单车模型剩余秒数 = eta_v2.estimate_event 的 ETA − last_event_ts（t 无关；
  week 标签用锚点 t 的 ISO 周，同 W2 静态）；查不到分布 → None（no-ETA 桶）。
- a_of / predict：T3.1 Step 3–7（见 docs/04 §3）。
- 时区纪律同 T2：全 aware Asia/Shanghai；naive 抛 TypeError。
"""
from __future__ import annotations

import importlib.util
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from spike_w2 import eta_v2
from spike_w3.common import (CST, DSSConfig, EXIT_STATIONS, Shift, detect_day_type,
                             detect_mid_type, iso_week_label)
from spike_w3.stream_pipeline import VehiclePool, VehicleState


def _require_aware(dt: datetime, what: str = "t") -> None:
    if dt.tzinfo is None:
        raise TypeError(f"{what} 必须 aware（Asia/Shanghai），收到 naive")


def rem_of(v: VehicleState, t: datetime, tables: dict,
           comp_excl: frozenset) -> Optional[float]:
    """单车模型剩余秒数（docs/04 §0）。

    W4.3d 双环门（DSS_DUAL_RING）：
      =1 → 统一引擎 estimate_event_unified(mode='shift' 列语义：rem/delay 消费 mean 列，
        与 W4.2e 重学表列语义一致；全状态机路径：effective_flag=None，吃原始 139 位 +
        flag_137 + passed_oven + 37 站出环网关 + loop_exited 出环守卫（W4.4c (α) Doc14 v1.4）；α 不内嵌）；
      =0 + DSS_USE_NET_DELAY=1 → doc10 retrofit（p×L → expected_delay）；=0 + ND=0 → 旧引擎。

    B10 留痕（cc 阻断项 2）：本 rem = base + delay 全口径（含净延误）；α 只在 predict()
    消费点对整 rem 缩一次（rem·α，单缩放点），不在估计器内二次缩放。"""
    _require_aware(t)
    _require_aware(v.last_event_ts, "v.last_event_ts")
    last = int(v.last_event_ts.timestamp())
    if eta_v2.dual_ring_enabled():
        eta, _used = eta_v2.estimate_event_unified(
            last, v.station, v.flag_137, v.flag, v.body_type,
            iso_week_label(t.date()), tables,
            mode='shift', passed_oven=v.passed_oven, comp_excl=comp_excl,
            loop_exited=v.loop_exited,
        )
    elif eta_v2.use_net_delay_enabled():
        eta, _used = eta_v2.estimate_event_net_delay(
            last, v.station, v.flag, v.body_type,
            iso_week_label(t.date()), tables, comp_excl,
        )
    else:
        eta, _used = eta_v2.estimate_event(
            last, v.station, v.flag, v.body_type,
            iso_week_label(t.date()), tables, comp_excl,
        )
    if eta is None:
        return None
    return float(eta - last)


_A_OF_SQL = (
    'SELECT COUNT(DISTINCT "BODY_ID") FROM ods.carbody_history '
    'WHERE "RW_STATION_ID" IN ({stations}) AND "BODY_ID" LIKE \'78202%%\' '
    'AND "DATE_EVT" >= %s AND "DATE_EVT" <= %s'
).format(stations=",".join(f"'{s}'" for s in EXIT_STATIONS))


def a_of(conn, shift: Shift, t: datetime) -> int:
    """A(t) = [shift.attr_start, t] 内下线产品车数（docs/04 §0）。

    闭区间 [attr_start, t] 两端含（与 W2 静态 02_replay_shift.py:260 同义：
    16:30:00 出口日班/中班双计，构造性一致）；4 出口站并集；COUNT(DISTINCT)
    = 窗内任一出口事件该车计一次（多次出口实测 0，无口径差）。
    有界 SQL（窗 ≤ ~12h，真库实测单次 ~12ms）；**不用池**（池 R3 出站即移除、
    无出站时刻戳；SQL = W2 静态 exit_arr 同源 → α=off 一致性由构造保证）。
    """
    _require_aware(t)
    if shift.attr_start.tzinfo is None:
        raise TypeError("shift.attr_start 必须 aware（Asia/Shanghai），收到 naive")
    cur = conn.cursor()
    cur.execute(_A_OF_SQL, (shift.attr_start, t))
    return int(cur.fetchall()[0][0])


PRODUCT_PREFIX = "78202"  # 产品车前缀（池契约无过滤；预测层过滤与 W2 静态 LIKE '78202%' 同口径）


def predict(conn, pool: VehiclePool, shift: Shift, t: datetime,
            tables: dict, comp_excl: frozenset, cfg: DSSConfig,
            alpha: float = 1.0) -> dict:
    """pred(t) = A(t) + Σ [t + rem_v/alpha ≤ shift.end]（docs/04 §0，W2-v3 口径 rem'=rem/α）。

    在制集 = pool.snapshot() ∩ 产品车（body_id 以 78202 起头；池构造已排除出站/延续站车）。
    L4：stale = (t − last_event_ts) > eta_stale_hours → 明细标 stale=True，**仍计 pred**
    （α=off 与静态一致性要求：静态无 stale 剔除）；no-ETA（查不到分布）不计 pred、明细可见。
    α 只作用于 rem 比较式，不缩放 A(t)。
    """
    _require_aware(t)
    if t < shift.attr_start:
        raise ValueError(f"t={t.isoformat()} 早于 attr_start={shift.attr_start.isoformat()}，无意义输入")
    a = a_of(conn, shift, t)
    by_vehicle = []
    no_eta = 0
    n_stale = 0
    hit = 0
    for body_id, v in pool.snapshot().items():
        if not body_id.startswith(PRODUCT_PREFIX):
            continue  # 非产品车（假号/项目车哨兵）不入在制集
        rem = rem_of(v, t, tables, comp_excl)
        is_stale = (t - v.last_event_ts).total_seconds() > cfg.eta_stale_hours * 3600
        if rem is None:
            no_eta += 1
        elif t + timedelta(seconds=rem / alpha) <= shift.end:
            hit += 1
        n_stale += is_stale
        by_vehicle.append({
            "body_id": body_id,
            "station": v.station,
            "flag": v.flag,
            "body_type": v.body_type,
            "rem_sec": rem,
            "stale": bool(is_stale),
        })
    return {
        "shift_id": f"{shift.day.isoformat()}_{shift.kind}",
        "t": t,
        "A": a,
        "inproc": len(by_vehicle),
        "pred": a + hit,
        "no_eta": no_eta,
        "stale": n_stale,
        "by_vehicle": by_vehicle,
    }


# ---------- T3.2 α 自适应（docs/04 §0） ----------

_W2_REPLAY_ALIAS = "w2_replay_shift"


def _w2_replay_shift():
    """importlib 只读引 spike_w2/02_replay_shift.py（数字开头模块不可正常 import）；
    仅用其模块级常量 SQL_DAYTYPE / SQL_MIDLAST（main 有 __main__ 守卫，不执行；W2 文件零改动）。"""
    m = sys.modules.get(_W2_REPLAY_ALIAS)
    if m is None:
        spec = importlib.util.spec_from_file_location(
            _W2_REPLAY_ALIAS,
            Path(__file__).resolve().parent.parent / "spike_w2" / "02_replay_shift.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules[_W2_REPLAY_ALIAS] = m
        spec.loader.exec_module(m)
    return m


_EXIT_HOURS_SQL = (
    'SELECT date_trunc(\'hour\', "DATE_EVT") AS h, count(*)::INT '
    'FROM ods.carbody_history '
    'WHERE "RW_STATION_ID" IN ({stations}) AND "BODY_ID" LIKE \'78202%%\' '
    'AND "DATE_EVT" >= %s AND "DATE_EVT" < %s GROUP BY 1 ORDER BY 1'
).format(stations=",".join(f"'{s}'" for s in EXIT_STATIONS))

_TYPE_HOURS = {"8h": 8, "10h": 10, "11h": 11}


def _as_date(x) -> date:
    return x.date() if isinstance(x, datetime) else x


def build_takt_baseline(conn, win_start: str, win_end: str,
                        min_samples: int) -> dict:
    """α 分母：历史 P50 逐小时出站速率 × 班次型（docs/04 §0，启动时 SQL 聚合、不新增表）。

    ① 学习窗逐日类型 = shifts.detect_day_type/detect_mid_type（复用 W2
    SQL_DAYTYPE/SQL_MIDLAST；对纯历史窗使用，无未来泄漏——T1.3 禁用针对在线因果兑底，
    不覆盖本场景，R4）；② 窗内产品车出口事件（4 站）按出口时刻归班：
    [05:00,16:00) 小时桶 → 当日日班型；其余小时桶（≥16 点 → 当日、≤4 点 → 前日）→ 中班型
    （边界小时 16/04 按闭区间约定归中班；类型 None 的天剔除）；③ 按 (kind,type)
    小时桶计数（每桶 = 1h，计数即速率）→ P50（statistics.median）；④ n_hours <
    min_samples → 回退最近班次型（同 kind、按工时距离排序，只用目标型直接数据；
    再不足 → 该型无键，tracker 侧 α 恒 1.0 + no_baseline 标注）。
    返回 {(kind, type): (p50_rate: float, n_hours: int, source: str)}。
    """
    m = _w2_replay_shift()
    win_lo = datetime.fromisoformat(win_start).replace(tzinfo=CST)
    win_hi = datetime.fromisoformat(win_end).replace(tzinfo=CST) + timedelta(days=1)

    cur = conn.cursor()
    cur.execute(m.SQL_DAYTYPE, (win_lo, win_hi))
    day_types = {
        _as_date(r[0]): detect_day_type(
            {"05:00": r[2], "06:00": r[3], "08:00": r[4]}, r[1])
        for r in cur.fetchall()
    }
    cur.execute(m.SQL_MIDLAST, (win_lo, win_hi))
    mid_types = {_as_date(r[0]): detect_mid_type(r[1]) for r in cur.fetchall()}

    cur.execute(_EXIT_HOURS_SQL, (win_lo, win_hi))
    buckets: dict[tuple, dict] = {}
    for h, n in cur.fetchall():
        if 5 <= h.hour <= 15:
            kind, day, ttype = "day", h.date(), day_types.get(h.date())
        else:  # hh ≥ 16 → 当日中班；hh ≤ 4 → 前日中班（凌晨拖尾属上一班中班）
            day = h.date() if h.hour >= 16 else h.date() - timedelta(days=1)
            kind, ttype = "mid", mid_types.get(day)
        if ttype is None:
            continue  # 类型 None 的天剔除（停产/无班）
        key = (kind, ttype)
        buckets.setdefault(key, {})
        buckets[key][h] = buckets[key].get(h, 0) + n

    direct: dict[tuple, tuple] = {}
    for key, hb in buckets.items():
        rates = [v for v in hb.values() if v > 0]
        if rates:
            direct[key] = (float(statistics.median(rates)), len(rates))

    result: dict[tuple, tuple] = {}
    for kind in ("day", "mid"):
        for ttype in ("8h", "10h", "11h"):
            key = (kind, ttype)
            if key in direct and direct[key][1] >= min_samples:
                result[key] = (direct[key][0], direct[key][1], "direct")
                continue
            for other in sorted((t for t in ("8h", "10h", "11h") if t != ttype),
                                key=lambda o: abs(_TYPE_HOURS[o] - _TYPE_HOURS[ttype])):
                okey = (kind, other)
                if okey in direct and direct[okey][1] >= min_samples:
                    result[key] = (direct[okey][0], direct[okey][1], f"fallback_{other}")
                    break
    return result


def obs_window(t: datetime, shift: Shift, window_hours: float):
    """观察窗 [max(t−window, attr_start), min(t, end−30min)]（docs/04 §0，B5 定稿）。

    t ≥ shift.end 或班次外（t < attr_start）→ None；窗长 <1h → None。
    尾 30min 剔除 = B5 定稿「收班前 30min 尾部」（窗末钉 end−30min）；生产 Phase 1
    停线剔除集 = 空集（T5 接入后再扩展）。
    返回 (start, end, hours)。
    """
    _require_aware(t)
    if shift.attr_start.tzinfo is None or shift.end.tzinfo is None:
        raise TypeError("shift.attr_start/end 必须 aware（Asia/Shanghai），收到 naive")
    if t < shift.attr_start or t >= shift.end:
        return None
    end = min(t, shift.end - timedelta(minutes=30))
    start = max(t - timedelta(hours=window_hours), shift.attr_start)
    hours = (end - start).total_seconds() / 3600
    if hours < 1.0:
        return None
    return (start, end, hours)


class AlphaTracker:
    """α 状态机（docs/04 §0；B5 定稿参数；纯状态，无 DB 依赖 → 单测可全合成驱动）。

    α>1 = 比历史快 → rem/α 缩短 → pred 增大。迟滞 0.05 + 2 周期 = 代码级常量
    （2026-09-23 复审 B1 修正，不入 env）。raw 即时 clamp [0.6,1.5]（外部审中③-1）；
    同向迟滞：(ema−committed) 同号且 |Δ|≥0.05 连续 2 周期 → committed=ema
    （+0.06/−0.06 振荡不提交，外部审中③-2）。
    """

    HYST_THRESHOLD = 0.05
    HYST_CYCLES = 2

    def __init__(self, baseline: dict, window_hours: float = 3.0,
                 ema_beta: float = 0.3, clamp_min: float = 0.6,
                 clamp_max: float = 1.5):
        self.baseline = baseline
        self.window_hours = window_hours
        self.ema_beta = ema_beta
        self.clamp_min = clamp_min
        self.clamp_max = clamp_max
        self.committed = 1.0
        self.ema: Optional[float] = None
        self.no_baseline = False
        self._key: Optional[tuple] = None
        self._dev_count = 0
        self._last_sign = 0

    def on_shift(self, shift: Shift) -> None:
        """班次切换（新 attr_start 到达）：committed=1.0、ema=None、迟滞计数清零、
        绑定 (kind, type) 基准（缺该型 → no_baseline，α 恒 1.0）。"""
        self._key = (shift.kind, shift.type)
        self.no_baseline = self._key not in self.baseline
        self.committed = 1.0
        self.ema = None
        self._dev_count = 0
        self._last_sign = 0

    def alpha(self) -> float:
        return self.committed

    def update(self, t: datetime, shift: Shift, exits_in_window: int,
               window_hours: float) -> float:
        """观察一次 → 返回 committed（t ≥ shift.end / 班次外 / 窗长 <1h → 无观察，
        状态静止；外部审低⑥）。"""
        _require_aware(t)
        if self._key != (shift.kind, shift.type) or self.no_baseline:
            return self.committed
        w = obs_window(t, shift, self.window_hours)
        if w is None:
            return self.committed
        p50, _n, _src = self.baseline[self._key]
        raw = (exits_in_window / window_hours) / p50
        raw_c = min(max(raw, self.clamp_min), self.clamp_max)
        self.ema = (raw_c if self.ema is None
                    else self.ema_beta * raw_c + (1 - self.ema_beta) * self.ema)
        dev = self.ema - self.committed
        sign = 0 if dev == 0 else (1 if dev > 0 else -1)
        in_band = sign != 0 and abs(dev) >= self.HYST_THRESHOLD
        self._dev_count = (self._dev_count + 1) if (in_band and sign == self._last_sign) \
            else (1 if in_band else 0)
        self._last_sign = sign
        if self._dev_count >= self.HYST_CYCLES:
            self.committed = self.ema  # 已 clamp 界内
            self._dev_count = 0
            self._last_sign = 0
        return self.committed
