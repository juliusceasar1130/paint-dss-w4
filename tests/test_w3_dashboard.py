# -*- coding: utf-8 -*-
"""T6-lite（docs/07 v1.2/v1.3）：控制台 + 5min 快照 + 工位 ETA 菜单。

T6.0：DSSConfig 三 env / active_shifts_at 动态班次解析（round 1 F-BLK-02 整改：
attr_start <= t < end，跨日中班天然覆盖，禁用 is_complete 静态过滤）/
run_replay·run_wall_clock round_hook 参数（默认 None 零影响）。

T6.1（§2.1/2.2）：build_frame_ctx + render_frame 纯函数。口径钉死：
- pred.shift.target = split_target 分摊班次目标（同引擎/M9 门；956/11h+11h → 478）。
- pred.day_total = 仅中班时段填充（A 窗 [D 04:45, 中班窗末)，target = 全天 total）。
- 锚点 = work_start+{3,4,5,6}h；pre 首锚点(3h)前 shift.grade=None（观测进度）。
- 池三分 scope_end：活跃班次 end；班外 = 下一班次 end；无下一班次 = 有 rem 全瓶颈。
- replay 模式 diagnose 零调用（显示层拦截，F-MED-05）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from spike_w3.common import DSSConfig, load_config
from spike_w3.dashboard import active_shifts_at, build_frame_ctx, render_frame
from spike_w3.plan_loader import PlanRow, WeeklyPlan
from spike_w3.stream_pipeline import VehicleState

TZ = ZoneInfo("Asia/Shanghai")
D = date(2026, 9, 9)          # KW37 周三 11h/11h（dry-run 窗，docs/07 拍板⑤）
T = datetime(2026, 9, 9, 5, 0, tzinfo=TZ)
CFG = DSSConfig()


# ===========================================================================
# T6.0
# ===========================================================================

def _mkplan(days: dict) -> WeeklyPlan:
    rows = {d: PlanRow(date=d, day_h=vh[0], mid_h=vh[1], total=vh[2],
                       entry=None, note="", source="file", source_file="t.csv")
            for d, vh in days.items()}
    return WeeklyPlan(rows=rows)


def test_config_t6_env_defaults(monkeypatch):
    for k in ("DSS_CONSOLE", "DSS_RUN_TAG", "DSS_SNAPSHOT_DIR"):
        monkeypatch.delenv(k, raising=False)
    cfg = load_config()
    assert cfg.console is True
    assert cfg.run_tag == ""
    assert cfg.snapshot_dir == "output/snapshots"


def test_config_t6_env_values(monkeypatch):
    monkeypatch.setenv("DSS_CONSOLE", "0")
    monkeypatch.setenv("DSS_RUN_TAG", "kw37_0909")
    monkeypatch.setenv("DSS_SNAPSHOT_DIR", "snap_tmp")
    cfg = load_config()
    assert cfg.console is False
    assert cfg.run_tag == "kw37_0909"
    assert cfg.snapshot_dir == "snap_tmp"
    monkeypatch.setenv("DSS_CONSOLE", "1")
    assert load_config().console is True


def test_config_t6_env_bad_value(monkeypatch):
    monkeypatch.setenv("DSS_CONSOLE", "banana")
    with pytest.raises(ValueError, match="DSS_CONSOLE"):
        load_config()


@pytest.fixture()
def plan_0909():
    return _mkplan({D: (11, 11, 956), date(2026, 9, 10): (11, 8, 900)})


def test_active_shifts_full_day_traversal(plan_0909):
    t = datetime(2026, 9, 9, 0, 0, tzinfo=TZ)
    seen = []
    while t < datetime(2026, 9, 10, 0, 0, tzinfo=TZ):
        seen.append([s.kind for s in active_shifts_at(plan_0909, t)])
        t += timedelta(minutes=5)
    # 00:00–04:40 班外（57 步）；04:45–16:25 日班（141 步）；16:30–23:55 中班（90 步）
    assert seen[:57] == [[] for _ in range(57)]
    assert seen[57:57 + 141] == [["day"] for _ in range(141)]
    assert seen[57 + 141:] == [["mid"] for _ in range(90)]
    assert len(seen) == 288


def test_active_shifts_boundary_1630_single(plan_0909):
    t = datetime(2026, 9, 9, 16, 30, 0, tzinfo=TZ)
    acts = active_shifts_at(plan_0909, t)
    assert len(acts) == 1 and acts[0].kind == "mid"


def test_active_shifts_cross_day_mid(plan_0909):
    t = datetime(2026, 9, 10, 3, 59, tzinfo=TZ)
    acts = active_shifts_at(plan_0909, t)
    assert len(acts) == 1 and acts[0].kind == "mid" and acts[0].day == D
    t2 = datetime(2026, 9, 10, 4, 0, tzinfo=TZ)
    acts2 = [s for s in active_shifts_at(plan_0909, t2)
             if s.kind == "mid" and s.day == D]
    assert acts2 == []


def test_active_shifts_missing_day_no_crash():
    p = _mkplan({D: (11, 11, 956)})
    t = datetime(2026, 9, 12, 10, 0, tzinfo=TZ)
    assert active_shifts_at(p, t) == []
    assert active_shifts_at(None, t) == []


def test_active_shifts_naive_rejected():
    with pytest.raises(TypeError):
        active_shifts_at(_mkplan({D: (11, 11, 956)}), datetime(2026, 9, 9, 10, 0))


def test_active_shifts_both_rest_empty():
    p = _mkplan({D: (None, None, 0)})
    t = datetime(2026, 9, 9, 10, 0, tzinfo=TZ)
    assert active_shifts_at(p, t) == []


def _load_boot():
    from test_w3_mainloop import _load_boot_module
    return _load_boot_module()


def test_run_replay_hook_none_unchanged():
    from test_w3_mainloop import FakeConn, T0 as MT0, _build_rows
    from spike_w3.stream_pipeline import EventStream, FreshnessGuard, VehiclePool
    mod = _load_boot()
    cfg = DSSConfig()
    results = []
    for _ in range(2):
        conn = FakeConn(_build_rows())
        pool = VehiclePool(dead_vehicle_hours=1.0)
        stream = EventStream(conn, wm=0, skew_window_min=15)
        guard = FreshnessGuard(cfg)
        results.append(mod.run_replay(pool, stream, guard, conn, cfg,
                                      start=MT0, end=MT0 + timedelta(hours=4),
                                      step_sec=300))
    a, b = results
    assert len(a) == len(b) == 48
    for sa, sb in zip(a, b):
        assert {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in sa.items()} == \
               {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in sb.items()}


def test_run_replay_hook_called_each_round():
    from test_w3_mainloop import FakeConn, T0 as MT0, _build_rows
    from spike_w3.stream_pipeline import EventStream, FreshnessGuard, VehiclePool
    mod = _load_boot()
    cfg = DSSConfig()
    conn = FakeConn(_build_rows())
    pool = VehiclePool(dead_vehicle_hours=1.0)
    stream = EventStream(conn, wm=0, skew_window_min=15)
    guard = FreshnessGuard(cfg)
    calls = []

    def hook(cur, pool_, conn_, cfg_, st):
        calls.append((cur, pool_, conn_, cfg_, st))

    mod.run_replay(pool, stream, guard, conn, cfg,
                   start=MT0, end=MT0 + timedelta(hours=2), step_sec=300,
                   round_hook=hook)
    assert len(calls) == 24
    assert calls[0][0] == MT0
    for prev, cur in zip(calls, calls[1:]):
        assert (cur[0] - prev[0]) == timedelta(seconds=300)
    for cur, pool_, conn_, cfg_, st in calls:
        assert pool_ is pool and conn_ is conn and cfg_ is cfg
        assert {"now", "pool_size", "max_ts", "guard_state", "frozen", "dead_count"} <= set(st)


def test_run_wall_clock_hook_param_static():
    import re
    from pathlib import Path
    import os
    src = Path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "spike_w3", "01_boot_online.py")).read_text(encoding="utf-8")
    m = re.search(r"def run_wall_clock\(.*?\):", src, re.S)
    assert m and "round_hook=None" in m.group(0)
    body = src[m.end():src.index("def load_rest_days")]
    i_one = body.index("run_one_round(pool, stream, guard, conn, now")
    i_hook = body.index("round_hook(now, pool, conn, cfg, st)")
    assert i_one < i_hook


# ===========================================================================
# T6.1：build_frame_ctx + render_frame
# ===========================================================================

def _plan() -> WeeklyPlan:
    rows = {D: PlanRow(date=D, day_h=11, mid_h=11, total=956, entry=None,
                       note="", source="file", source_file="t.csv")}
    return WeeklyPlan(rows=rows)


def _vs(body_id, station, flag, last_dt):
    return VehicleState(body_type="78202A", station=station, flag=flag,
                        last_event_ts=last_dt, entry_ts=last_dt - timedelta(hours=2),
                        exits_seen=0, loop_count=0)


def _pool3(t):
    class P:
        def snapshot(self):
            return {"78202T1": _vs("78202T1", "10IS255", "0", t - timedelta(minutes=30)),
                    "78202T2": _vs("78202T2", "12IS015", "1", t - timedelta(minutes=10)),
                    "78202T3": _vs("78202T3", "14IS015", "2", t - timedelta(minutes=5))}
    return P()


def _rem_fn(rem_map):
    def pred_fn(t_):
        return {"by_vehicle": [
            {"body_id": "78202T1", "station": "10IS255", "flag": "0",
             "rem_sec": rem_map.get("T1"), "stale": False},
            {"body_id": "78202T2", "station": "12IS015", "flag": "1",
             "rem_sec": rem_map.get("T2"), "stale": False},
            {"body_id": "78202T3", "station": "14IS015", "flag": "2",
             "rem_sec": rem_map.get("T3"), "stale": False}]}
    return pred_fn


class FakeGuard:
    def __init__(self, diagnose_val=None):
        self.status = "NORMAL"
        self.label = "数据新鲜"
        self.lag_minutes = 2.0
        self.is_anomaly = False
        self.diagnose_val = diagnose_val
        self.diagnose_calls = 0

    def diagnose(self, conn, now):
        self.diagnose_calls += 1
        return self.diagnose_val


def _diag():
    return {"s1_sync_job": {"started_at": None, "status": "success", "lag_min": 1.0},
            "s2_watermark": {"etl_max_id": 100, "table_max_id": 100, "gap": 0},
            "s3_max_event_ts": None, "s3_lag_min": 2.0,
            "s4_rb_max_ts": None, "s4_lag_min": 1.0,
            "event_fresh": True, "etl_healthy": True, "verdict": "新鲜"}


def _ctx(t, pred_fn, a_map, plan=None, mode="wall", guard=None, **kw):
    plan = plan or _plan()
    guard = guard or FakeGuard(diagnose_val=_diag())

    def a_fn(s, t_):
        return a_map.get((s.kind, s.day), 0)

    ctx = build_frame_ctx(_pool3(t), None, guard, plan, CFG, t, pred_fn, a_fn,
                          mode=mode, max_ts=t - timedelta(minutes=2), **kw)
    return ctx, guard


def test_frame_ctx_day_pre_anchor():
    """05:00 日班首锚点(08:00)前：grade None + pre + anchor_in_min 180。"""
    pf = _rem_fn({"T1": 1800, "T2": 86400, "T3": None})
    ctx, guard = _ctx(T, pf, {("day", D): 87})
    assert guard.diagnose_calls == 1
    pr = ctx["pred"]["shift"]
    assert pr["shift_sid"] == "2026-09-09_day"
    assert pr["A"] == 87 and pr["pred"] == 88
    assert pr["target"] == 478                          # split_target(11,11,956)
    assert pr["grade"] is None and pr["anchor_state"] == "pre"
    assert pr["anchor_in_min"] == 180                   # 05:00 → 08:00(3h 锚点)
    assert pr["delta"] == pytest.approx(88 / 478 - 1)
    assert ctx["pred"]["day_total"] == {
        "A_all": None, "pred": None, "target": 956, "delta": None, "grade": None}
    p = ctx["pool"]
    assert p["size"] == 3 and p["inproc_n"] == 1 and p["bottleneck_n"] == 1
    assert p["no_eta_n"] == 1 and p["flag_dist"] == {"0": 1, "1": 1, "2": 1}
    assert ctx["alpha"] == {"mode": "off", "value": 1.0}
    assert ctx["week"] == "2026-W37" and ctx["weekday"] == "周三"
    assert ctx["mode"] == "wall" and ctx["alerts"] == []


def test_frame_ctx_mid_daytotal_filled():
    """18:00 中班时段：day_total 全量填充（A 窗 04:45 起，target 956）。"""
    t = datetime(2026, 9, 9, 18, 0, tzinfo=TZ)
    pf = _rem_fn({"T1": 1800, "T2": 86400, "T3": None})
    ctx, _ = _ctx(t, pf, {("mid", D): 12, ("day", D): 500})
    pr = ctx["pred"]["shift"]
    assert pr["shift_sid"] == "2026-09-09_mid" and pr["target"] == 478
    assert pr["A"] == 12 and pr["pred"] == 13
    assert pr["anchor_state"] == "pre" and pr["anchor_in_min"] == 90   # 19:30 首锚点
    assert pr["grade"] is None                                        # pre 不上色
    dt = ctx["pred"]["day_total"]
    assert dt["A_all"] == 500 and dt["pred"] == 501
    assert dt["target"] == 956
    assert dt["delta"] == pytest.approx(501 / 956 - 1)
    assert dt["grade"] == "red"                                       # −47.6% ≤ −10%


def test_frame_ctx_post_anchor_grade():
    """10:00 日班 post（3h 锚点 08:00 已过）：grade 生效 + 下一锚点 11:00（60min）。"""
    t = datetime(2026, 9, 9, 10, 0, tzinfo=TZ)
    pf = _rem_fn({"T1": 1800, "T2": 86400, "T3": None})
    ctx, _ = _ctx(t, pf, {("day", D): 100})
    pr = ctx["pred"]["shift"]
    assert pr["pred"] == 101 and pr["target"] == 478
    assert pr["anchor_state"] == "post" and pr["anchor_in_min"] == 60
    assert pr["grade"] == "red"                                       # −78.9%
    # 6h 锚点（11:00）已过 → anchor_in_min = None
    t2 = datetime(2026, 9, 9, 12, 0, tzinfo=TZ)
    ctx2, _ = _ctx(t2, pf, {("day", D): 300})
    assert ctx2["pred"]["shift"]["anchor_state"] == "post"
    assert ctx2["pred"]["shift"]["anchor_in_min"] is None


def test_frame_ctx_out_of_shift():
    """02:00 班外：shift 整对象 null；day_total 仅 target 预填；池三分 scope = 下一班次 end。"""
    t = datetime(2026, 9, 9, 2, 0, tzinfo=TZ)
    pf = _rem_fn({"T1": 3600, "T2": 86400, "T3": None})
    ctx, _ = _ctx(t, pf, {("day", D): 0})
    assert ctx["pred"]["shift"] is None
    assert ctx["pred"]["day_total"] == {
        "A_all": None, "pred": None, "target": 956, "delta": None, "grade": None}
    p = ctx["pool"]
    assert (p["inproc_n"], p["bottleneck_n"], p["no_eta_n"]) == (1, 1, 1)


def test_frame_ctx_no_plan_day():
    """无计划日：shift None、day_total.target None（不猜数）。"""
    p = WeeklyPlan(rows={})
    t = datetime(2026, 9, 9, 10, 0, tzinfo=TZ)
    pf = _rem_fn({"T1": 1800, "T2": 86400, "T3": None})
    ctx, _ = _ctx(t, pf, {("day", D): 0}, plan=p)
    assert ctx["pred"]["shift"] is None
    assert ctx["pred"]["day_total"]["target"] is None
    assert ctx["pool"]["size"] == 3


def test_frame_ctx_replay_no_diagnose():
    """replay 模式：diagnose 零调用 + note 拦截（F-MED-05）。"""
    pf = _rem_fn({"T1": 1800, "T2": 86400, "T3": None})
    ctx, guard = _ctx(T, pf, {("day", D): 87}, mode="replay")
    assert guard.diagnose_calls == 0
    g = ctx["guard"]
    assert g["diagnose"] is None
    assert "replay" in g["diagnose_note"] and "时间穿透" in g["diagnose_note"]
    assert ctx["mode"] == "replay"


def test_frame_ctx_alpha_on_and_alerts_passthrough():
    """α on 模式值透传 + alerts/alert_state 透传。"""
    from types import SimpleNamespace
    pf = _rem_fn({"T1": 1800, "T2": 86400, "T3": None})
    al = {"scope_key": "2026-09-09_day", "level": "red", "t": T.isoformat()}
    ctx, _ = _ctx(T, pf, {("day", D): 1}, alpha_mode="on", alpha_value=1.02,
                  alerts=[al], alert_state={"2026-09-09_day":
                                            {"current_level": "yellow",
                                             "pending_level": "red",
                                             "pending_min": 5.0}})
    assert ctx["alpha"] == {"mode": "on", "value": 1.02}
    assert ctx["alerts"] == [al]
    assert ctx["alert_state"]["2026-09-09_day"]["pending_level"] == "red"


def test_render_frame_prefix_and_delta_tiers():
    """帧首行 [DSS] 前缀（G1 判据）；pre 不上红黄绿；post grade 生效；replay 诊断行。"""
    pf = _rem_fn({"T1": 1800, "T2": 86400, "T3": None})
    ctx_pre, _ = _ctx(T, pf, {("day", D): 87})
    out_pre = render_frame(ctx_pre)
    first = out_pre.splitlines()[0]
    assert first.startswith("[DSS] ") and "2026-09-09 05:00" in first
    assert "α off" in first
    assert "观测进度" in out_pre
    delta_line = [l for l in out_pre.splitlines() if "Δ=" in l][0]
    assert not any(w in delta_line for w in ("红", "黄", "绿"))
    assert "S2✓" in out_pre and "新鲜" in out_pre

    ctx_post, _ = _ctx(datetime(2026, 9, 9, 10, 0, tzinfo=TZ), pf, {("day", D): 100})
    out_post = render_frame(ctx_post)
    delta_line2 = [l for l in out_post.splitlines() if "Δ=" in l][0]
    assert "红" in delta_line2

    ctx_rep, _ = _ctx(T, pf, {("day", D): 87}, mode="replay")
    out_rep = render_frame(ctx_rep)
    assert "ETL 诊断不适用·时间穿透" in out_rep and "S2✓" not in out_rep


def test_render_frame_out_of_shift_and_sections():
    """班外行（下一班次 04:45）+ 瓶颈 flag 分层 + 瓶颈段 top 行。"""
    t = datetime(2026, 9, 9, 2, 0, tzinfo=TZ)
    pf = _rem_fn({"T1": 3600, "T2": 86400, "T3": None})
    ctx, _ = _ctx(t, pf, {("day", D): 0})
    out = render_frame(ctx)
    lines = out.splitlines()
    shift_line = [l for l in lines if "班外" in l][0]
    assert "04:45" in shift_line and "日班" in shift_line
    pool_line = [l for l in lines if l.strip().startswith("池")][0]
    assert "返修 1" in pool_line and "黑顶 0" in pool_line
    top_line = [l for l in lines if "瓶颈段" in l][0]
    assert "12IS015×1" in top_line


def test_frame_ctx_pool_split_no_next_shift():
    """无计划 + 无下一班次：有 rem 全瓶颈（不猜 scope）。"""
    p = WeeklyPlan(rows={})
    t = datetime(2026, 9, 9, 2, 0, tzinfo=TZ)
    pf = _rem_fn({"T1": 3600, "T2": 86400, "T3": None})
    ctx, _ = _ctx(t, pf, {("day", D): 0}, plan=p)
    p3 = ctx["pool"]
    assert (p3["inproc_n"], p3["bottleneck_n"], p3["no_eta_n"]) == (0, 2, 1)
