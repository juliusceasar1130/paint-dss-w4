# -*- coding: utf-8 -*-
"""T3.2 α 自适应（docs/04 §0 冻结接口：build_takt_baseline + AlphaTracker + obs_window）。

全离线：不连库（FakeConn 固定行集）；纯状态机可全合成驱动。
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from spike_w2 import shifts
from spike_w3.common import Shift

CST = ZoneInfo("Asia/Shanghai")


class FakeCursor:
    def __init__(self, batches=()):
        self._batches = list(batches)
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        return self

    def fetchall(self):
        return self._batches.pop(0) if self._batches else []


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


# 学习窗 04-14 → 04-15（2 天）：
# - 日型：两天均 11h（c05=30 ≥ 30）
# - 中型：04-14 = 8h（末事件 420min ∈ [300,510)）；04-15 = 10h（570min ∈ [510,630)）
# - 出口事件（date_trunc('hour'), count）：
#   日班 11h 04-14: 05h×5, 09h×10, 13h×7
#   日班 11h 04-15: 06h×8, 10h×12        → ('day','11h') 速率 [5,10,7,8,12] n=5 P50=8.0
#   中班 8h  04-14: 19h×1, 20h×1, 21h×1  → ('mid','8h')  n=3 <5 → 回退 10h
#   中班 10h 04-15: 17h..22h 各×1        → ('mid','10h') n=6  P50=1.0
DT14 = datetime(2026, 4, 14, 0, 0, tzinfo=CST)
DT15 = datetime(2026, 4, 15, 0, 0, tzinfo=CST)
DAYTYPE_ROWS = [
    (DT14, 5000, 30, 0, 0),   # (d, total, c05, c06, c08)
    (DT15, 5000, 30, 0, 0),
]
MIDLAST_ROWS = [
    (DT14, 420),              # (mid_day, last_min)
    (DT15, 570),
]
EXIT_ROWS = [
    (DT14 + timedelta(hours=h), n)
    for h, n in [(5, 5), (9, 10), (13, 7), (19, 1), (20, 1), (21, 1)]
] + [
    (DT15 + timedelta(hours=h), 1) for h in (17, 18, 19, 20, 21, 22)
] + [(DT15 + timedelta(hours=6), 8), (DT15 + timedelta(hours=10), 12)]


def _conn():
    return FakeConn(FakeCursor([DAYTYPE_ROWS, MIDLAST_ROWS, EXIT_ROWS]))


def test_build_takt_baseline_p50_and_fallback():
    from spike_w3.predictor import EXIT_STATIONS, _EXIT_HOURS_SQL, build_takt_baseline

    # SQL 形状：站名是字符串值 → 单引号（Postgres 双引号 = 标识符，
    # 2026-09-25 M9 真库首跑实测炸过）；占位符恰 2 个（时间边界）
    for st in EXIT_STATIONS:
        assert f"'{st}'" in _EXIT_HOURS_SQL and f'"{st}"' not in _EXIT_HOURS_SQL
    assert _EXIT_HOURS_SQL.count("%s") == 2

    # min_samples=5：
    #  ('day','11h') 直接：n=5 ≥5 → P50=8.0 source=direct
    #  ('mid','10h') 直接：n=6 ≥5 → P50=1.0 source=direct
    #  ('mid','8h')  n=3 <5 → 回退最近班次型 10h（工时距离 2）→ source=fallback_10h
    #  ('mid','11h') 无直接数据 → 回退 10h（工时距离 1 最近）
    #  ('day','8h')  n=0 → 10h 日 n=0 → 11h 日 n=5 → source=fallback_11h
    r = build_takt_baseline(_conn(), "2026-04-14", "2026-04-15", 5)
    assert r[("day", "11h")] == (8.0, 5, "direct")
    assert r[("mid", "10h")] == (1.0, 6, "direct")
    assert r[("mid", "8h")] == (1.0, 6, "fallback_10h")
    assert r[("mid", "11h")] == (1.0, 6, "fallback_10h")
    assert r[("day", "8h")] == (8.0, 5, "fallback_11h")
    assert r[("day", "10h")] == (8.0, 5, "fallback_11h")  # 无直接数据 → 最近 11h（距离 1）
    # 回退键存在（tracker 用 baseline.get 一行绑定），值 = 目标型的直接数据
    assert set(r) == {("day", t) for t in ("8h", "10h", "11h")} | \
                   {("mid", t) for t in ("8h", "10h", "11h")}

    # min_samples=10：全部 n<10 且回退目标亦不足 → 无基准（键缺失，tracker 侧 α 恒 1.0）
    r2 = build_takt_baseline(_conn(), "2026-04-14", "2026-04-15", 10)
    assert r2 == {}


def _mid_shift(day, type_="11h"):
    """中班 Shift（16:30 开，11h → 次日 03:30 收）。"""
    attr = datetime(day.year, day.month, day.day, 16, 30, tzinfo=CST)
    hours = int(type_.rstrip("h"))
    end = attr + timedelta(hours=hours)
    return Shift(kind="mid", day=day, work_start=attr,
                 attr_start=attr, end=end, type=type_)


def test_alpha_tracker_full_lifecycle():
    from spike_w3.predictor import AlphaTracker

    baseline = {("mid", "11h"): (10.0, 100, "direct")}
    shift = _mid_shift(date(2026, 9, 8))
    T = lambda h, m=0: datetime(2026, 9, 8, 16, 30, tzinfo=CST) + timedelta(hours=h, minutes=m)

    # (vi) on_shift 重置：committed=1.0、ema=None
    tr = AlphaTracker(baseline, window_hours=3.0, ema_beta=0.3)
    tr.on_shift(shift)
    assert tr.alpha() == 1.0 and tr.ema is None

    # (i) 窗长 <1h（t=17:00 → 窗 [16:30,17:00]=0.5h）→ 无观察：状态不动（ema 仍 None）
    assert tr.update(T(0, 30), shift, exits_in_window=1000, window_hours=0.5) == 1.0
    assert tr.ema is None and tr.alpha() == 1.0

    # (ii) 首观 ema=raw：t=19:30 窗 3h，exits=40 → raw=(40/3)/10=1.3333（界内）；首周期不提交
    assert tr.update(T(3, 0), shift, 40, 3.0) == 1.0
    assert abs(tr.ema - 1.333333) < 1e-4

    # (iii) |Δ|<0.05 → 不提交（全新 tracker：raw=1.0333 → dev=0.0333 < 0.05，连续 2 周期仍不提交）
    tr_iii = AlphaTracker(baseline, window_hours=3.0, ema_beta=0.3)
    tr_iii.on_shift(shift)
    assert tr_iii.update(T(3, 0), shift, 31, 3.0) == 1.0     # (31/3)/10 = 1.0333
    assert tr_iii.update(T(4, 0), shift, 31, 3.0) == 1.0
    assert tr_iii.alpha() == 1.0 and abs(tr_iii.ema - 1.033333) < 1e-4

    # (iv) |Δ|≥0.05 连续 2 周期 → 提交（全新 tracker 精确验证提交路径）：
    tr2 = AlphaTracker(baseline, window_hours=3.0, ema_beta=0.3)
    tr2.on_shift(shift)
    assert tr2.update(T(3, 0), shift, 40, 3.0) == 1.0         # dev +0.3333 计 1
    got = tr2.update(T(4, 0), shift, 40, 3.0)                 # 第 2 周期同号 → 提交
    assert abs(got - 1.333333) < 1e-4 and abs(tr2.alpha() - 1.333333) < 1e-4
    # 提交后 dev≈0 → 计数清零（下一周期不会立即再提交）

    # (v) clamp 上界：raw=2.0 → raw_clamped=1.5 → ema 1.5 → 2 周期提交 1.5
    tr3 = AlphaTracker(baseline, window_hours=3.0, ema_beta=0.3)
    tr3.on_shift(shift)
    tr3.update(T(3, 0), shift, 60, 3.0)                        # (60/3)/10 = 2.0 → 1.5
    got3 = tr3.update(T(4, 0), shift, 60, 3.0)
    assert abs(got3 - 1.5) < 1e-9
    # clamp 下界：raw=0.2 → 0.6
    tr4 = AlphaTracker(baseline, window_hours=3.0, ema_beta=0.3)
    tr4.on_shift(shift)
    tr4.update(T(3, 0), shift, 6, 3.0)                         # (6/3)/10 = 0.2 → 0.6
    got4 = tr4.update(T(4, 0), shift, 6, 3.0)
    assert abs(got4 - 0.6) < 1e-9

    # (v) 精确复核 (vi)：提交 1.5 后切班 → 重置 1.0
    tr3.on_shift(shift)
    assert tr3.alpha() == 1.0 and tr3.ema is None

    # (vii) 无基准型 → 恒 1.0 + no_baseline 标记
    tr5 = AlphaTracker({}, window_hours=3.0, ema_beta=0.3)
    tr5.on_shift(shift)
    assert tr5.no_baseline is True
    assert tr5.update(T(3, 0), shift, 40, 3.0) == 1.0
    assert tr5.ema is None and tr5.alpha() == 1.0

    # t ≥ end（班末后）→ 无观察状态静止（不动 ema/committed）
    tr6 = AlphaTracker(baseline, window_hours=3.0, ema_beta=0.3)
    tr6.on_shift(shift)
    assert tr6.update(T(3, 0), shift, 40, 3.0) == 1.0
    assert tr6.update(shift.end, shift, 40, 3.0) == 1.0
    assert tr6.update(shift.end + timedelta(hours=1), shift, 40, 3.0) == 1.0
    assert abs(tr6.ema - 1.333333) < 1e-4


def test_obs_window_edges():
    from spike_w3.predictor import obs_window

    # 日班 11h（attr_start=05:00, end=16:30）：t=08:00 → 起点裁剪 [05:00,08:00]=3h
    day = Shift(kind="day", day=date(2026, 9, 8),
                work_start=datetime(2026, 9, 8, 5, 0, tzinfo=CST),
                attr_start=datetime(2026, 9, 8, 5, 0, tzinfo=CST),
                end=datetime(2026, 9, 8, 16, 30, tzinfo=CST), type="11h")
    w = obs_window(datetime(2026, 9, 8, 8, 0, tzinfo=CST), day, 3.0)
    assert (w[0], w[1], w[2]) == (
        datetime(2026, 9, 8, 5, 0, tzinfo=CST), datetime(2026, 9, 8, 8, 0, tzinfo=CST), 3.0)
    # t=10:00 → 不裁剪 [07:00,10:00]=3h
    w = obs_window(datetime(2026, 9, 8, 10, 0, tzinfo=CST), day, 3.0)
    assert (w[0], w[1]) == (datetime(2026, 9, 8, 7, 0, tzinfo=CST),
                            datetime(2026, 9, 8, 10, 0, tzinfo=CST))

    # 中班 11h（16:30 → 次日 03:30）：t=21:30 → [18:30,21:30]=3h
    mid = _mid_shift(date(2026, 9, 8))
    w = obs_window(datetime(2026, 9, 8, 21, 30, tzinfo=CST), mid, 3.0)
    assert (w[0], w[1], w[2]) == (
        datetime(2026, 9, 8, 18, 30, tzinfo=CST), datetime(2026, 9, 8, 21, 30, tzinfo=CST), 3.0)

    # 尾 30min 剔除：end=04:00、t=03:50 → 窗末钉 03:30（窗 [00:50, 03:30] = 2h40m）
    mid40 = Shift(kind="mid", day=date(2026, 9, 8), work_start=mid.attr_start,
                  attr_start=mid.attr_start,
                  end=datetime(2026, 9, 9, 4, 0, tzinfo=CST), type="11h")
    w = obs_window(datetime(2026, 9, 9, 3, 50, tzinfo=CST), mid40, 3.0)
    assert w[1] == datetime(2026, 9, 9, 3, 30, tzinfo=CST) and abs(w[2] - 160 / 60) < 1e-9

    # t ≥ end（班末后）→ None
    assert obs_window(mid40.end, mid40, 3.0) is None
    assert obs_window(mid40.end + timedelta(hours=1), mid40, 3.0) is None
    # t < attr_start（班次外）→ None
    assert obs_window(mid.attr_start - timedelta(hours=1), mid, 3.0) is None
    # 窗长 <1h → None（t=17:00、attr_start=16:30 → 0.5h）
    assert obs_window(datetime(2026, 9, 8, 17, 0, tzinfo=CST), mid, 3.0) is None
