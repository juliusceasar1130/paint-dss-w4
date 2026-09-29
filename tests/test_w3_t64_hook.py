# -*- coding: utf-8 -*-
"""T6.4（docs/07 v1.2 §3）：主循环挂接 —— alert_engine.deb_state() 纯读（拍板⑥）
+ dashboard.make_t6_hook 闭包工厂（帧 ctx → 快照 + 控制台；引擎/α 透传）。

口径：
- deb_state(now) = {scope_key: {current_level, pending_level, pending_min}}，
  纯读（调用前后 _deb 深度不变）。
- hook 每轮：pv 一次（_pool_view 或注入 pv_fn）→ 引擎 evaluate（STALE → []）→
  tracker（on 模式）→ build_frame_ctx → write_snapshot → sink(render_frame)。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from spike_w3.alert_engine import AlertEngine, Debouncer
from spike_w3.common import DSSConfig
from spike_w3.dashboard import make_t6_hook
from spike_w3.predictor import AlphaTracker
from spike_w3.plan_loader import PlanRow, WeeklyPlan

TZ = ZoneInfo("Asia/Shanghai")
D = date(2026, 9, 9)
CFG = DSSConfig()


def _plan():
    rows = {D: PlanRow(date=D, day_h=11, mid_h=11, total=956, entry=None,
                       note="", source="file", source_file="t.csv")}
    return WeeklyPlan(rows=rows)


def _engine_plan():
    return {D: {"total": 956, "day": "11h", "mid": "11h"}}


class RawGuard:
    """镜像 stream_pipeline.FreshnessGuard 的属性协议（.status 字符串 + diagnose）。"""
    def __init__(self, mode="NORMAL"):
        self.status = mode
        self.label = "数据新鲜"
        self.lag_minutes = 1.0
        self.is_anomaly = False
        self._diag_calls = 0

    def diagnose(self, conn, now):
        self._diag_calls += 1
        return {"s1_sync_job": {"started_at": None, "status": "success", "lag_min": 1.0},
                "s2_watermark": {"etl_max_id": 1, "table_max_id": 1, "gap": 0},
                "s3_max_event_ts": None, "s3_lag_min": 1.0,
                "s4_rb_max_ts": None, "s4_lag_min": 1.0,
                "event_fresh": True, "etl_healthy": True, "verdict": "新鲜"}


def _eng_adapter(g):
    from spike_w3.dashboard import EngineGuardAdapter
    return EngineGuardAdapter(g)


def _pool3(t):
    from spike_w3.stream_pipeline import VehicleState
    def vs(bid, stn, flag):
        return VehicleState(body_type="78202A", station=stn, flag=flag,
                            last_event_ts=t - timedelta(minutes=10),
                            entry_ts=t - timedelta(hours=2),
                            exits_seen=0, loop_count=0)
    class P:
        def snapshot(self):
            return {"78202T1": vs("78202T1", "10IS255", "0"),
                    "78202T2": vs("78202T2", "12IS015", "1")}
    return P()


def _pv_canned():
    def pv_fn(t_):
        return {"by_vehicle": [
            {"body_id": "78202T1", "station": "10IS255", "flag": "0",
             "rem_sec": 1800.0, "stale": False},
            {"body_id": "78202T2", "station": "12IS015", "flag": "1",
             "rem_sec": 86400.0, "stale": False}]}
    return pv_fn


class AConn:
    """a_of SQL 假连接：COUNT → canned A 值。"""
    def __init__(self, a_map):
        self.a_map = a_map
        self.queries = []

    def cursor(self):
        return self

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        return self

    def fetchall(self):
        if self.queries:
            sql = self.queries[-1][0]
            params = self.queries[-1][1]
            if "COUNT" in sql.upper() and len(params) == 2:
                # a_of(s, t): A(s.attr_start → t) 按 (kind, day) 路由
                for (kind, day), v in self.a_map.items():
                    return [(v,)]
        return [(0,)]


# ---------------------------------------------------------------------------
# deb_state（拍板⑥：纯读 + 零语义变化）
# ---------------------------------------------------------------------------

def test_deb_state_pure_read():
    eng = AlertEngine(_engine_plan(), CFG, {})
    t0 = datetime(2026, 9, 9, 10, 0, tzinfo=TZ)
    deb = Debouncer()
    deb.current = "red"
    deb.pending = "yellow"
    deb.pending_since = t0 - timedelta(minutes=5)
    eng._deb["2026-09-09_day"] = deb
    before = (deb.current, deb.pending, deb.pending_since)
    st = eng.deb_state(t0)
    assert st == {"2026-09-09_day": {"current_level": "red",
                                     "pending_level": "yellow",
                                     "pending_min": pytest.approx(5.0)}}
    assert (deb.current, deb.pending, deb.pending_since) == before   # 零语义变化


def test_deb_state_empty_engine():
    eng = AlertEngine(_engine_plan(), CFG, {})
    t0 = datetime(2026, 9, 9, 10, 0, tzinfo=TZ)
    assert eng.deb_state(t0) == {}


# ---------------------------------------------------------------------------
# make_t6_hook
# ---------------------------------------------------------------------------

def _st(t, pool_size=2):
    return {"now": t, "pool_size": pool_size,
            "max_ts": t - timedelta(minutes=1), "guard_state": "NORMAL",
            "frozen": False, "dead_count": 0}


def test_hook_snapshot_and_frame(tmp_path):
    plan = _plan()
    t = datetime(2026, 9, 9, 5, 0, tzinfo=TZ)
    gf = RawGuard()
    conn = AConn({("day", D): 87, ("mid", D): 0})
    frames = []
    hook = make_t6_hook(plan, CFG, gf, engine=None, mode="replay",
                        run_tag="t6_4", snapshot_dir=tmp_path,
                        sink=frames.append, pv_fn=_pv_canned(),
                        a_map={("day", D): 87, ("mid", D): 0})
    ctx = hook(t, _pool3(t), conn, CFG, _st(t))
    assert ctx["pred"]["shift"]["A"] == 87
    snaps = list((tmp_path / "t6_4").glob("snap_*.json"))
    assert len(snaps) == 1
    import json
    obj = json.loads(snaps[0].read_text(encoding="utf-8"))
    assert obj["run_tag"] == "t6_4" and obj["mode"] == "replay"
    assert len(frames) == 1 and frames[0].startswith("[DSS] ")


def test_hook_engine_debounce_confirm(tmp_path):
    """基线即确认（首档）→ 转红 pending（未确认）→ 10min 持续后确认转换。"""
    plan = _plan()
    gf = RawGuard()
    eng = AlertEngine(_engine_plan(), CFG, {"10IS255": "W53"}, guard=_eng_adapter(gf))
    a_map = {("day", D): 470, ("mid", D): 0}       # 首档 +1.5% → 绿
    conn = AConn(a_map)
    t1 = datetime(2026, 9, 9, 9, 0, tzinfo=TZ)
    hook = make_t6_hook(plan, CFG, gf, engine=eng, mode="replay",
                        run_tag="deb", snapshot_dir=tmp_path, sink=None,
                        pv_fn=_pv_canned(), a_map=a_map)
    ctx1 = hook(t1, _pool3(t1), conn, CFG, _st(t1))
    st1 = ctx1["alert_state"]["2026-09-09_day"]
    assert st1["current_level"] == "green" and st1["pending_level"] is None
    assert len(ctx1["alerts"]) == 1 and ctx1["alerts"][0]["prev_level"] is None  # 基线

    a_map[("day", D)] = 100                         # 转红（101/478 = −79%）
    t2 = t1 + timedelta(minutes=30)
    ctx2 = hook(t2, _pool3(t2), conn, CFG, _st(t2))
    st2 = ctx2["alert_state"]["2026-09-09_day"]
    assert ctx2["alerts"] == []                     # 未满 10min 不确认
    assert st2["current_level"] == "green" and st2["pending_level"] == "red"

    t3 = t2 + timedelta(minutes=10)
    ctx3 = hook(t3, _pool3(t3), conn, CFG, _st(t3))
    st3 = ctx3["alert_state"]["2026-09-09_day"]
    assert st3["current_level"] == "red"            # 10min 满 → 确认转换
    al = ctx3["alerts"][0]
    assert al["level"] == "red" and al["prev_level"] == "green"
    assert al["sid"] == "2026-09-09_day" and al["scope"] == "shift"


def test_hook_stale_guard_no_alerts(tmp_path):
    """STALE 冻结：引擎零告警、去抖不推进。"""
    plan = _plan()
    gf = RawGuard(mode="STALE")
    eng = AlertEngine(_engine_plan(), CFG, {}, guard=_eng_adapter(gf))
    conn = AConn({("day", D): 100})
    t = datetime(2026, 9, 9, 10, 0, tzinfo=TZ)
    hook = make_t6_hook(plan, CFG, gf, engine=eng, mode="replay",
                        run_tag="stale", snapshot_dir=tmp_path, sink=None,
                        pv_fn=_pv_canned(), a_map={("day", D): 100})
    ctx = hook(t, _pool3(t), conn, CFG, _st(t))
    assert ctx["alerts"] == []
    assert ctx["alert_state"] == {}               # STALE：去抖零推进、无 deb 键


def test_hook_alpha_off_tracker_untouched(tmp_path):
    """α off：ctx.alpha=off/1.0；tracker 不更新（spy update 零调用）。"""
    plan = _plan()
    gf = RawGuard()
    trk = AlphaTracker(baseline={})
    called = []
    orig = trk.update

    def spy(*a, **k):
        called.append(a)
        return orig(*a, **k)
    trk.update = spy
    conn = AConn({("day", D): 87})
    t = datetime(2026, 9, 9, 5, 0, tzinfo=TZ)
    hook = make_t6_hook(plan, CFG, gf, engine=None, mode="wall",
                        run_tag="alpha", snapshot_dir=tmp_path, sink=None,
                        pv_fn=_pv_canned(), a_map={("day", D): 87},
                        tracker=trk, exit_count_fn=lambda c, a, b: 0)
    ctx = hook(t, _pool3(t), conn, CFG, _st(t))
    assert ctx["alpha"] == {"mode": "off", "value": 1.0}
    assert called == []


def test_hook_alpha_on_update_called(tmp_path):
    """α on：tracker.alpha() 进 ctx + update 每轮（观察窗内）。"""
    cfg_on = DSSConfig(alpha_mode="on")
    plan = _plan()
    gf = RawGuard()
    trk = AlphaTracker(baseline={})
    calls = []

    def fake_update(t_, s_, n_, h_):
        calls.append((t_, s_.kind, n_, h_))
    trk.update = fake_update
    conn = AConn({("day", D): 87, ("mid", D): 0})
    t = datetime(2026, 9, 9, 10, 0, tzinfo=TZ)      # 日班观察窗内
    hook = make_t6_hook(plan, cfg_on, gf, engine=None, mode="wall",
                        run_tag="alphaon", snapshot_dir=tmp_path, sink=None,
                        pv_fn=_pv_canned(), a_map={("day", D): 87, ("mid", D): 0},
                        tracker=trk, exit_count_fn=lambda c, a, b: 12)
    ctx = hook(t, _pool3(t), conn, cfg_on, _st(t))
    assert ctx["alpha"]["mode"] == "on"
    assert len(calls) == 1 and calls[0][2] == 12     # 出口数透传


def test_hook_wall_diagnose_called(tmp_path):
    plan = _plan()
    gf = RawGuard()
    conn = AConn({("day", D): 87})
    t = datetime(2026, 9, 9, 5, 0, tzinfo=TZ)
    hook = make_t6_hook(plan, CFG, gf, engine=None, mode="wall",
                        run_tag="w", snapshot_dir=tmp_path, sink=None,
                        pv_fn=_pv_canned(), a_map={("day", D): 87})
    hook(t, _pool3(t), conn, CFG, _st(t))
    assert gf._diag_calls == 1                        # wall 模式诊断
    gf2 = RawGuard()
    hook3 = make_t6_hook(plan, CFG, gf2, engine=None, mode="replay",
                         run_tag="w3", snapshot_dir=tmp_path, sink=None,
                         pv_fn=_pv_canned(), a_map={("day", D): 87})
    hook3(t, _pool3(t), conn, CFG, _st(t))
    assert gf2._diag_calls == 0                       # replay 模式零诊断


def test_main_wires_console_hook_static():
    """main() 源码钉：cfg.console 门 + round_hook 传参（replay 与 wall 两分支）。"""
    from pathlib import Path
    import os
    src = Path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "spike_w3", "01_boot_online.py")).read_text(encoding="utf-8")
    i_main = src.index("def main()")
    body = src[i_main:]
    assert "cfg.console" in body
    assert "build_t6_components" in body
    n_hook = body.count("round_hook=")
    assert n_hook >= 2                               # replay + wall 两分支
