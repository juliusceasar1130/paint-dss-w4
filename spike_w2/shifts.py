# -*- coding: utf-8 -*-
"""班次日历纯函数（TDD）：类型自学 + 班次窗/锚点构建 + ISO 周工具。

口径（BM 笔记 2026-09-19 拍板 + 细化，verbatim）：
- 班次窗 = 开班 + 工作时长 + 30min 交班尾：日班窗末统一 16:30（11h 05:00–16:30 /
  10h 06:00–16:30 / 8h 08:00–16:30）；中班 16:30 开（11h → 04:00 / 10h → 03:00 / 8h → 01:00 窗末）
- 归属窗：日班 = [04:45, 16:30]（提早开工 04:45–正式开班计日班的推广，8h 型日 04:45–08:00 同计日班）；
  中班 = [16:30, 窗末]；(中班窗末, 04:45) 无归属（低谷微隙，回放报告覆盖率）
- 第 5 小时锚点 = 工作开班 + 5h（日 11h→10:00 / 10h→11:00 / 8h→13:00；中班恒 21:30）；
  多锚点 = 开班 + {3,4,5,6}h
- 类型自学（回放窗无周计划表）：
  日班 = {05:00, 06:00, 08:00} 中最早的"15min 桶事件数 ≥ SURGE_MIN_15MIN"（低谷底 ~12% 峰值
       ≈ 7–8 条/15min，满 takt ≈ 40–60 条/15min，阈值 30 居中）；全天事件 < MIN_DAY_EVENTS → None
  中班 = 中班段 [D 16:30, D+1 04:45) 末条产品车事件相对 16:30 的分钟数：
       ≥630 → 11h（10h 型若 03:00 后炉区拖尾会被误判，审计表留痕）/ ≥510 → 10h / 其余 → 8h；
       < MID_MIN_FLOOR（300min，真实 8h 中班末事件 ≈ 480–540min）视为数据截断 → None
- 【回放专用】类型自学用全天事件（含锚点之后），属回放后视；生产（W3）不可直接沿用
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

CST = ZoneInfo("Asia/Shanghai")

DAY_TYPES = {"11h": time(5, 0), "10h": time(6, 0), "8h": time(8, 0)}
MID_END_TIMES = {"11h": time(4, 0), "10h": time(3, 0), "8h": time(1, 0)}
ATTR_DAY_START = time(4, 45)        # 日班归属窗起点（提早开工口径推广）
TANK_DAY_END = time(16, 30)         # 日班归属窗末（工作收班 16:00 + 30min 交班尾）
MID_START = time(16, 30)            # 中班固定开班（2026-09-19 二确）
ANCHOR_HOURS = (3, 4, 5, 6)
GATE_ANCHOR_H = 5
SURGE_MIN_15MIN = 30                # 开班判定 15min 桶阈值
MIN_DAY_EVENTS = 500                # 全天事件低于此 → 无日班（停产日）
MID_MIN_FLOOR = 300                 # 中班末事件距 16:30 低于此 → 数据截断


@dataclass(frozen=True)
class Shift:
    kind: str            # 'day' | 'mid'
    day: date            # 名义日（日班 = 当日；中班 = 开班日）
    work_start: datetime # 工作开班（锚点起算点）
    attr_start: datetime # 归属窗起点（实际下线量计数起点）
    end: datetime        # 归属窗末（窗末；预测「t+剩余 ≤ 班末」的班末，修订 10 重锚定）
    type: str            # '11h' | '10h' | '8h'


def detect_day_type(counts: dict, day_total: int):
    """counts: {'05:00': n, '06:00': n, '08:00': n}（各 15min 桶事件数）。
    返回 '11h'/'10h'/'8h'/None（停产日）。"""
    if day_total < MIN_DAY_EVENTS:
        return None
    for tt in (time(5, 0), time(6, 0), time(8, 0)):
        if counts.get(f"{tt:%H:%M}", 0) >= SURGE_MIN_15MIN:
            return {time(5, 0): "11h", time(6, 0): "10h", time(8, 0): "8h"}[tt]
    return "10h"  # 三桶都未达（渐进爬坡）→ 回退最常见型；审计表留痕


def detect_mid_type(last_min):
    """last_min: 中班段末条产品车事件距 D 16:30 的分钟数（None = 段内无事件）。"""
    if last_min is None or last_min < MID_MIN_FLOOR:
        return None
    if last_min >= 630:
        return "11h"
    if last_min >= 510:
        return "10h"
    return "8h"


def normalize_shift_type(val):
    """标准归一化班次类型：支持 11, 10, 8, '11', '10h', '休', None 等。
    返回 '11h' | '10h' | '8h' | None（休息/停产）。"""
    if val is None:
        return None
    s = str(val).strip().lower()
    if s in ("", "none", "休", "休息", "0", "false"):
        return None
    if s.endswith("h"):
        s = s[:-1]
    if s in ("11", "10", "8"):
        return f"{s}h"
    return None


def shift_windows(day: date, day_type=None, mid_type=None, schedule: dict = None) -> list:
    """该日的班次列表 [日班?, 中班?]（type None → 无该班次）。
    若提供 schedule 且 day in schedule，优先采用排班表；否则回退自学 day_type / mid_type。"""
    if schedule is not None and day in schedule:
        sched_entry = schedule[day]
        dt = normalize_shift_type(sched_entry.get("day"))
        mt = normalize_shift_type(sched_entry.get("mid"))
    else:
        dt = normalize_shift_type(day_type)
        mt = normalize_shift_type(mid_type)

    out = []
    if dt is not None:
        out.append(Shift(
            kind="day", day=day, type=dt,
            work_start=datetime.combine(day, DAY_TYPES[dt], tzinfo=CST),
            attr_start=datetime.combine(day, ATTR_DAY_START, tzinfo=CST),
            end=datetime.combine(day, TANK_DAY_END, tzinfo=CST)))
    if mt is not None:
        out.append(Shift(
            kind="mid", day=day, type=mt,
            work_start=datetime.combine(day, MID_START, tzinfo=CST),
            attr_start=datetime.combine(day, MID_START, tzinfo=CST),
            end=datetime.combine(day + timedelta(days=1), MID_END_TIMES[mt], tzinfo=CST)))
    return out


def anchors(shift: Shift) -> list:
    return [shift.work_start + timedelta(hours=h) for h in ANCHOR_HOURS]


def gate_anchor(shift: Shift) -> datetime:
    return shift.work_start + timedelta(hours=GATE_ANCHOR_H)


def is_complete(shift: Shift, replay_start: datetime, replay_end: datetime) -> bool:
    """完整 = 归属窗 ⊆ 回放窗（C2：不完整班次排除出门判并列表）。"""
    return shift.attr_start >= replay_start and shift.end <= replay_end


def iso_week_label(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_start(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1)


def week_learning_range(week_label: str) -> tuple:
    """该周之前的 8 个 ISO 周（滚动学习窗，D3）：[周一-8周, 周一-1天]。无未来泄漏。"""
    y, w = int(week_label[:4]), int(week_label[6:8])
    monday = date.fromisocalendar(y, w, 1)
    return monday - timedelta(weeks=8), monday - timedelta(days=1)
