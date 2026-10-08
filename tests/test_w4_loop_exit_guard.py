# -*- coding: utf-8 -*-
"""W4.4c (α) R3' 出环守卫 TDD（Doc14 v1.4 候选；agy-2 审核纠偏 1+2，2026-10-06 用户拍板 α）。

背景：raw 139 位出环后残留（全窗返修车 96% / 黑顶车 45%）→ 在线状态机把出环后主线事件
误判在环 → 分支 A 落 loop_dur L 兜底（41.5–51.3h）→ 班末结构性漏计（M9 G1 主因 25/29 车）。
修复 = 把离线净化窗语义（D1，已拍板）镜像回在线状态机：

- VehicleState +loop_exited（25IS010/25IS110/25IS210 出口置 True；R3 泛化再过入口站
  重置 False——返线头必经入口站；replay_car 切片轨迹信号合并；收养按事件初始化）；
- resolve_effective_flag + loop_exited 参数：R3'（139='1' 且 loop_exited → '0'）+
  R2 黑顶同守卫（139='2' 且 loop_exited → '0'——出炉后白名单防御穿透，agy 纠偏 1）；
  缺省 False = 旧语义（DUAL_RING=0 回滚 / 既有测试 / 静态 effective_flag 直通不受影响）；
- estimate_event_unified / predictor.rem_of / dashboard 单车 CLI 三消费点透传
  （dashboard 补 25IS 出口历史查询，agy 纠偏 2）。
"""
from datetime import datetime
import sys
import types
from zoneinfo import ZoneInfo

from spike_w2 import eta_v2
from spike_w3 import stream_pipeline as sp
from spike_w3.stream_pipeline import Event, VehiclePool, VehicleState

TZ = ZoneInfo("Asia/Shanghai")
WEEK = "2026-W37"
T0 = int(datetime(2026, 9, 9, 5, 0, tzinfo=TZ).timestamp())


def T(h, m=0):
    return datetime(2026, 9, 9, h, m, tzinfo=TZ)


def E(body_id, station, flag, ts, row_id=0, body_type="78202A-", flag_137="0"):
    return Event(body_id, body_type, station, flag, ts, row_id, flag_137)


def _tables(**kw):
    base = {"rem": {}, "loop_rem": {}, "loop_dur": {}, "takt": {},
            "fut_week": {}, "fut_all": {}, "cohort": {}, "lineprob": {},
            "quality": {}, "expected_delay": {}}
    base.update(kw)
    return base


# ============ eta_v2 常量 ============

def test_loop_exit_gateways_trio():
    # 镜像 sqlbase 净化窗 t_rw_end/t_bt_end 站集（Doc14 v1.4 边界 c：三站，非 37 全集）
    assert eta_v2.LOOP_EXIT_GATEWAYS == frozenset({'25IS010', '25IS110', '25IS210'})
    assert eta_v2.LOOP_EXIT_GATEWAYS <= eta_v2.UNIVERSAL_EXIT_STATIONS


# ============ resolve_effective_flag：R3' + R2 出环守卫 ============

def test_r3p_flag1_loop_exited_suppressed():
    # R3'：出环后 raw 139='1' 残留 → '0'（主线口径，不再直通 '1'）
    assert eta_v2.resolve_effective_flag('33IS450', '1', '1', True, loop_exited=True) == '0'
    assert eta_v2.resolve_effective_flag('33IS450', '0', '1', False, loop_exited=True) == '0'


def test_r3p_flag1_inloop_and_default_keeps():
    # 环内 raw 139='1' 不变；缺省 loop_exited=False = 旧语义（回滚路径零漂移）
    assert eta_v2.resolve_effective_flag('14IS120', '1', '1', True, loop_exited=False) == '1'
    assert eta_v2.resolve_effective_flag('14IS120', '1', '1', True) == '1'
    assert eta_v2.resolve_effective_flag('33IS450', '1', '1', True) == '1'


def test_r2_blacktop_loop_exited_suppressed():
    # agy 纠偏 1：出炉后白名单防御穿透（passed_oven=True 时 2.2/2.3 均不成立）→
    # 出环守卫是 '2' 位的最后防线（真库 45% 黑顶车出环后 raw '2' 残留）
    assert eta_v2.resolve_effective_flag('33IS450', '1', '2', True, loop_exited=True) == '0'
    assert eta_v2.resolve_effective_flag('33IS450', '1', '2', False, loop_exited=True) == '0'


def test_r2_blacktop_inloop_and_default_keeps():
    # 环内黑顶不变；缺省 = 旧语义（既有 test_eff_r23_post_oven_non_whitelist_pass 锁定）
    assert eta_v2.resolve_effective_flag('15IS120', '1', '2', True, loop_exited=False) == '2'
    assert eta_v2.resolve_effective_flag('15IS120', '1', '2', True) == '2'


def test_r1_gateway_priority_with_loop_exited():
    # R1 优先级不变：37 站网关恒 '0'（含 loop_exited=True）
    assert eta_v2.resolve_effective_flag('25IS010', '1', '1', True, loop_exited=True) == '0'
    assert eta_v2.resolve_effective_flag('K3IS140', '1', '2', True, loop_exited=True) == '0'


def test_eff_flag0_unaffected_by_loop_exited():
    assert eta_v2.resolve_effective_flag('33IS450', '1', '0', True, loop_exited=True) == '0'
    assert eta_v2.resolve_effective_flag('33IS450', '1', '-', True, loop_exited=True) == '0'


# ============ estimate_event_unified：分支选择透传 ============

GUARD_T = _tables(
    loop_rem={('33IS450', '1', 'YM33J-'): (184593, 184593, 190000, 100)},
    rem={('33IS450', 'YM33J-'): (5000, 5100, 5200, 100)},
)


def test_unified_exited_goes_branch_b_not_a():
    # 出环后 raw '1' 残留 @33IS450：loop_rem 行存在但不得消费（分支 B 主线口径）
    eta_b, used = eta_v2.estimate_event_unified(
        T0, '33IS450', '1', '1', 'YM33J-', WEEK, GUARD_T,
        mode='vehicle', passed_oven=True, loop_exited=True)
    assert (eta_b, used) == (T0 + 5000, False)   # base=rem p50 列；无 delay 行 → 0


def test_unified_default_flag1_branch_a_lock():
    # 缺省 = 旧语义：raw '1' → 分支 A（loop_rem 行消费）——回滚/既有行为锁定
    eta_a, _ = eta_v2.estimate_event_unified(
        T0, '33IS450', '1', '1', 'YM33J-', WEEK, GUARD_T,
        mode='vehicle', passed_oven=True)
    assert eta_a == T0 + 184593


def test_unified_blacktop_exited_branch_b():
    # 黑顶 '2' 出环残留 → 分支 B（同守卫）
    eta_b, _ = eta_v2.estimate_event_unified(
        T0, '33IS450', '1', '2', 'YM33J-', WEEK, GUARD_T,
        mode='vehicle', passed_oven=True, loop_exited=True)
    assert eta_b == T0 + 5000


# ============ VehicleState / VehiclePool：loop_exited 状态机 ============

def test_vehiclestate_loop_exited_default():
    st = VehicleState(body_type="2N54Y-", station="10IS255", flag="0",
                      last_event_ts=T(8), entry_ts=T(8), exits_seen=0, loop_count=0)
    assert st.loop_exited is False


def test_pool_25is_exit_sets_loop_exited():
    p = VehiclePool()
    B = "78202A0301"
    p.ingest(E(B, "01IS205", "0", T(6)))        # 入口入池（缺省 False）
    p.ingest(E(B, "14IS120", "1", T(7)))        # 打磨返修（环内，raw '1'）
    assert p.snapshot()[B].loop_exited is False
    p.ingest(E(B, "25IS010", "1", T(8)))        # 出环（位仍 '1'）
    assert p.snapshot()[B].loop_exited is True


def test_pool_mainline_residual_does_not_reset():
    # 出环后主线事件 raw 位残留 → 不得重置（若重置则守卫失效，修复自破）
    p = VehiclePool()
    B = "78202A0302"
    p.ingest(E(B, "01IS205", "0", T(6)))
    p.ingest(E(B, "14IS120", "1", T(7)))
    p.ingest(E(B, "25IS010", "1", T(8)))
    p.ingest(E(B, "33IS450", "1", T(9)))        # 主线残留位
    p.ingest(E(B, "33IS487", "1", T(10)))
    st = p.snapshot()[B]
    assert st.loop_exited is True and st.flag == "1"


def test_pool_entry_reentry_resets_loop_exited():
    # R3 泛化：返线头必经入口站 → 新周期（重置 False；真库实测窗内出环后再入环 = 0 辆）
    p = VehiclePool()
    B = "78202A0303"
    p.ingest(E(B, "01IS205", "0", T(6)))
    p.ingest(E(B, "14IS120", "1", T(7)))
    p.ingest(E(B, "25IS010", "1", T(8)))
    assert p.snapshot()[B].loop_exited is True
    p.ingest(E(B, "01IS205", "1", T(9)))        # 返工回线头
    st = p.snapshot()[B]
    assert st.loop_exited is False and st.loop_count == 0


def test_pool_adoption_at_25is():
    # 中途收养发生在 25IS 出口 → loop_exited=True（出环即收养）
    p = VehiclePool()
    B = "78202A0304"
    p.ingest(E(B, "25IS010", "1", T(8), flag_137="1"))
    st = p.snapshot()[B]
    assert st.loop_exited is True and st.flag == "1"


def test_pool_adoption_mainline_residual_default():
    # 主线中途收养 + 残留位 → 缺省 False（与现状同：无历史信号时保守保持旧行为）
    p = VehiclePool()
    B = "78202A0305"
    p.ingest(E(B, "33IS450", "1", T(8), flag_137="1"))
    assert p.snapshot()[B].loop_exited is False


def test_pool_blacktop_25is_then_mainline():
    # 黑顶同轨：出环置 True，主线残留 '2' 不重置
    p = VehiclePool()
    B = "78202A0306"
    p.ingest(E(B, "01IS205", "0", T(6)))
    p.ingest(E(B, "15IS065", "2", T(7), flag_137="1"))
    p.ingest(E(B, "17IS305", "2", T(8), flag_137="1"))   # 出炉
    p.ingest(E(B, "25IS110", "2", T(9), flag_137="1"))
    p.ingest(E(B, "33IS450", "2", T(10), flag_137="1"))
    st = p.snapshot()[B]
    assert st.loop_exited is True and st.passed_oven is True and st.flag == "2"


def test_pool_25is_idempotent_same_station():
    # 连续同站 25IS（R1 幂等）→ loop_exited 保持 True（更新须在幂等早退前）
    p = VehiclePool()
    B = "78202A0307"
    p.ingest(E(B, "25IS010", "1", T(8)))
    p.ingest(E(B, "25IS010", "1", T(8, 30)))
    assert p.snapshot()[B].loop_exited is True


# ============ replay_car：切片轨迹信号合并 ============

def test_replay_car_slice_with_25is_sets_true():
    # 在池车 + 含 25IS 事件的迟到切片 → loop_exited=True（轨迹信号合并）
    p = VehiclePool()
    B = "78202A0308"
    p.ingest(E(B, "01IS205", "0", T(6), row_id=1))
    p.ingest(E(B, "14IS120", "1", T(7), row_id=2))
    p.replay_car(B, [
        E(B, "14IS120", "1", T(7), row_id=2),
        E(B, "25IS010", "1", T(8), row_id=3),
    ])
    st = p.snapshot()[B]
    assert st.loop_exited is True and st.station == "25IS010"


def test_replay_car_slice_no_signal_keeps():
    # 切片无 25IS/入口信号 → 保持原值（无信号不清除）
    p = VehiclePool()
    B = "78202A0309"
    p.ingest(E(B, "01IS205", "0", T(6), row_id=1))
    p.ingest(E(B, "14IS120", "1", T(7), row_id=2))
    p.ingest(E(B, "25IS010", "1", T(8), row_id=3))
    assert p.snapshot()[B].loop_exited is True
    p.replay_car(B, [E(B, "33IS450", "1", T(9), row_id=4)])   # 仅主线残留事件
    assert p.snapshot()[B].loop_exited is True


def test_replay_car_adoption_slice_with_25is():
    # 不在池 + 切片含 25IS → 收养且 loop_exited=True
    p = VehiclePool()
    B = "78202A0310"
    p.replay_car(B, [
        E(B, "14IS120", "1", T(7), row_id=1),
        E(B, "25IS010", "1", T(8), row_id=2),
        E(B, "33IS450", "1", T(9), row_id=3),
    ])
    st = p.snapshot()[B]
    assert st is not None and st.loop_exited is True and st.station == "33IS450"


# ============ predictor.rem_of 透传 ============

def test_rem_of_loop_exited_branch_b(monkeypatch):
    from spike_w3 import predictor
    monkeypatch.setenv("DSS_DUAL_RING", "1")
    t = _tables(
        loop_rem={('33IS450', '1', '78202A-'): (184593, 184593, 190000, 100)},
        rem={('33IS450', '78202A-'): (5000, 5100, 5200, 100)},
    )
    v = VehicleState(body_type="78202A-", station="33IS450", flag="1",
                     last_event_ts=T(4), entry_ts=T(4), exits_seen=0, loop_count=0,
                     passed_oven=True, flag_137="1", loop_exited=True)
    # 分支 B：base=rem mean 列（shift）5100；33IS450 ∉ 37 站、无 delay 行 → +0
    assert predictor.rem_of(v, T(5), t, frozenset()) == 5100.0
    # 对照：loop_exited=False（环内）→ 分支 A mean 列 184593
    v2 = VehicleState(body_type="78202A-", station="33IS450", flag="1",
                      last_event_ts=T(4), entry_ts=T(4), exits_seen=0, loop_count=0,
                      passed_oven=True, flag_137="1")
    assert predictor.rem_of(v2, T(5), t, frozenset()) == 184593.0


# ============ dashboard 单车 CLI：25IS 出口查询 + 透传 ============

def _dashboard_fake_conn(latest, oven_cnt=0, exit_cnt=0, rem=(), rem_rows=()):
    class Cur:
        def __init__(self):
            self.queries = []

        def execute(self, sql, params=None):
            self.queries.append((sql, params))
            if '"DATE_EVT" <= %s' in sql and 'LIMIT 1' in sql:
                self._out = [latest] if latest else []
            elif "COUNT(*)" in sql and "16IS305" in sql:
                self._out = [(oven_cnt,)]
            elif "COUNT(*)" in sql and "25IS010" in sql:
                self._out = [(exit_cnt,)]
            elif "MIN(" in sql and "01IS205" in sql:
                # W4.4h：入口事件班次归属查询（缺省 = 日班带内时刻；开关 off 时不参与计算）
                self._out = [(datetime(2026, 9, 9, 6, 0),)]
            elif "FROM dss.dim_remaining_time" in sql:
                self._out = list(rem)
            else:
                self._out = []
            return self

        def fetchall(self):
            return self._out

        def fetchone(self):
            return self._out[0] if self._out else None

    class C:
        def __init__(self):
            self._cur = Cur()

        def cursor(self):
            return self._cur

    return C()


def _stub_replay(tables):
    stub = types.ModuleType("w2_replay_cli_eta")
    stub.load_tables = lambda conn: tables
    return stub


def test_dashboard_dual1_loop_exited_mainline(monkeypatch):
    from spike_w3 import dashboard
    monkeypatch.setenv("DSS_DUAL_RING", "1")
    t = _tables(
        loop_rem={('33IS450', '1', '78202A-'): (184593, 184593, 190000, 100)},
        rem={('33IS450', '78202A-'): (5000, 5100, 5200, 100)},
    )
    monkeypatch.setitem(sys.modules, "w2_replay_cli_eta", _stub_replay(t))
    ts = datetime(2026, 9, 9, 4, 30, tzinfo=TZ)
    latest = (ts, "33IS450", "78202A-", "1", "1")   # raw 139='1' + 137='1' 残留
    conn = _dashboard_fake_conn(latest, oven_cnt=1, exit_cnt=1,
                                rem=[("78202A-", 5000.0, 100)])
    r = dashboard.compute_eta(conn, "78202GT1", "K3IS140", datetime(2026, 9, 9, 5, 0, tzinfo=TZ))
    assert r["status"] == "eta"
    assert r["eff_flag"] == "0" and r["in_loop"] is False   # 出环守卫 → 主线口径
    assert r["eta_sec"] == 5000.0                            # 分支 B p50 列
    sqls = [q for q, _ in conn._cur.queries]
    assert any("25IS010" in s for s in sqls)                 # 25IS 出口轨迹查询执行
    monkeypatch.delitem(sys.modules, "w2_replay_cli_eta", raising=False)


def test_dashboard_dual1_not_exited_inloop(monkeypatch):
    from spike_w3 import dashboard
    monkeypatch.setenv("DSS_DUAL_RING", "1")
    t = _tables(
        loop_rem={('33IS450', '1', '78202A-'): (184593, 184593, 190000, 100)},
        rem={('33IS450', '78202A-'): (5000, 5100, 5200, 100)},
    )
    monkeypatch.setitem(sys.modules, "w2_replay_cli_eta", _stub_replay(t))
    ts = datetime(2026, 9, 9, 4, 30, tzinfo=TZ)
    latest = (ts, "33IS450", "78202A-", "1", "1")
    conn = _dashboard_fake_conn(latest, oven_cnt=1, exit_cnt=0)
    r = dashboard.compute_eta(conn, "78202GT2", "K3IS140", datetime(2026, 9, 9, 5, 0, tzinfo=TZ))
    assert r["status"] == "eta"
    assert r["eff_flag"] == "1" and r["in_loop"] is True     # 未出环 → 环内（旧行为）
    assert r["eta_sec"] == 184593.0
    monkeypatch.delitem(sys.modules, "w2_replay_cli_eta", raising=False)
