# -*- coding: utf-8 -*-
"""W4.3c W3 管道契约扩展单测（TDD RED；规格 = docs/15 §0.3 + 双审 agy 阻断项 2）。

Event +1 字段 flag_137（4 处 SQL 同步）/ VehicleState +2 字段（passed_oven、flag_137 进池）/
规则 O1（烘房出炉单调标记）/ O2（137 位随车：ingest/replay_car/收养取最新事件值，'-' 按 '0'，
replay_car 重算轨迹 OR）/ 合成状态机矩阵（烘房前假 2 静默 / 出炉后真 2 环分布 /
出网关粘滞 1 解冻 + 车型基线）。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

from spike_w2 import eta_v2
from spike_w3 import stream_pipeline as sp
from spike_w3.stream_pipeline import Event, VehiclePool, VehicleState

TZ = ZoneInfo("Asia/Shanghai")


def T(h, m=0):
    return datetime(2026, 9, 7, h, m, tzinfo=TZ)


def E(body_id, station, flag, ts, row_id=0, body_type="78202A-", flag_137="0"):
    return Event(body_id, body_type, station, flag, ts, row_id, flag_137)


# ---------- 契约字段 ----------

def test_event_flag137_field_default_and_raw():
    ev = E("B", "10IS255", "0", T(10))
    assert ev.flag_137 == "0"          # 缺省 '0'
    ev2 = E("B", "10IS255", "0", T(10), flag_137="1")
    assert ev2.flag_137 == "1"
    ev3 = E("B", "10IS255", "0", T(10), flag_137="-")
    assert ev3.flag_137 == "-"         # Event 层保留原值，池层归一化


def test_vehiclestate_new_fields_defaults():
    st = VehicleState(body_type="2N54Y-", station="10IS255", flag="0",
                      last_event_ts=T(10), entry_ts=T(10), exits_seen=0, loop_count=0)
    assert st.passed_oven is False
    assert st.flag_137 == "0"


def test_4_sqls_carry_137_bit():
    # 4 处 consume SQL（consume / car_log / reconcile / bootstrap）同步 137 位
    for sql in (sp._CONSUME_SQL, sp._CAR_LOG_SQL, sp._RECONCILE_SQL, sp._BOOTSTRAP_SQL):
        assert 'SUBSTRING("MDS_DATA" FROM 139 FOR 1)' in sql   # 既有 139 位不动
        assert 'SUBSTRING("MDS_DATA" FROM 137 FOR 1)' in sql   # W4.3c 新增


# ---------- 规则 O1：烘房出炉标记（幂等单调，永不复位） ----------

def test_oven_out_sets_passed_oven():
    p = VehiclePool()
    B = "78202A0301"
    p.ingest(E(B, "01IS205", "0", T(8)))
    p.ingest(E(B, "17IS305", "0", T(10)))       # 2 线烘房出口
    assert p.snapshot()[B].passed_oven is True
    p.ingest(E(B, "10IS255", "0", T(11)))       # 后续事件不复位
    assert p.snapshot()[B].passed_oven is True


def test_oven_out_before_pool_entry_monotonic():
    # 同一烘房出口站二次过站（幂等）+ 非烘房站车不受影响
    p = VehiclePool()
    A, B = "78202A0302", "78202A0303"
    p.ingest(E(A, "01IS205", "0", T(8)))
    p.ingest(E(A, "16IS305", "0", T(9)))
    p.ingest(E(A, "16IS305", "0", T(9, 30)))    # 连续同站（R1 幂等）
    assert p.snapshot()[A].passed_oven is True
    p.ingest(E(B, "01IS205", "0", T(8)))
    p.ingest(E(B, "10IS255", "0", T(9)))
    assert p.snapshot()[B].passed_oven is False


def test_adoption_at_oven_out_station():
    # 中途收养发生在烘房出口站 → 该车事件即在出炉点，passed_oven = True
    p = VehiclePool()
    B = "78202A0304"
    p.ingest(E(B, "18IS345", "0", T(10)))       # 3 线烘房出口（非入站站 → R4 收养）
    s = p.snapshot()[B]
    assert s.passed_oven is True and s.station == "18IS345"


def test_oven_out_20is010_not_counted():
    # Doc14 勘误：20IS010 = 2 线储存线入口，非烘房出口（常量集合外）
    assert "20IS010" not in eta_v2.OVEN_OUT_STATIONS
    p = VehiclePool()
    B = "78202A0305"
    p.ingest(E(B, "01IS205", "0", T(8)))
    p.ingest(E(B, "20IS010", "0", T(9)))
    assert p.snapshot()[B].passed_oven is False


# ---------- 规则 O2：137 位随车（最新事件值，'-' 按 '0'） ----------

def test_flag137_tracks_latest_event_in_pool():
    p = VehiclePool()
    B = "78202A0310"
    p.ingest(E(B, "01IS205", "0", T(8), flag_137="1"))
    assert p.snapshot()[B].flag_137 == "1"
    p.ingest(E(B, "10IS255", "0", T(9), flag_137="-"))      # '-' 按 '0'
    assert p.snapshot()[B].flag_137 == "0"
    p.ingest(E(B, "12IS015", "1", T(10), flag_137="1"))     # 最新事件为准
    assert p.snapshot()[B].flag_137 == "1"


def test_flag137_adoption_initial():
    p = VehiclePool()
    A, B = "78202A0311", "78202A0312"
    p.ingest(E(A, "14IS015", "0", T(10), flag_137="1"))     # 中途收养 137='1'
    assert p.snapshot()[A].flag_137 == "1"
    p.ingest(E(B, "14IS015", "0", T(10), flag_137="-"))     # 中途收养 137='-'
    assert p.snapshot()[B].flag_137 == "0"


def test_replay_car_flag137_latest_and_oven_or():
    # 在池车：重算切片无烘房事件 → passed_oven 已 True 不重置（单调 OR）；flag_137 = 切片最新事件值
    p = VehiclePool()
    B = "78202A0313"
    p.ingest(E(B, "01IS205", "0", T(8), flag_137="0"))
    p.ingest(E(B, "17IS305", "0", T(9), flag_137="1"))      # 出炉 + 137=1
    s0 = p.snapshot()[B]
    assert s0.passed_oven is True and s0.flag_137 == "1"
    p.replay_car(B, [
        E(B, "01IS205", "0", T(8), row_id=1, flag_137="1"),
        E(B, "10IS255", "0", T(9, 30), row_id=2, flag_137="0"),   # 最新事件 137='0'
        E(B, "12IS015", "0", T(10), row_id=3, flag_137="-"),      # '-' → '0'
    ])
    s = p.snapshot()[B]
    assert s.passed_oven is True        # 单调：切片无烘房事件不清除
    assert s.flag_137 == "0"            # 最新事件值（'-' 按 '0'）
    assert s.station == "12IS015"


def test_replay_car_adoption_oven_or_from_trajectory():
    # 不在池车收养（replay_car 路径）：passed_oven = 重算轨迹 OR；flag_137 = 最新事件值
    p = VehiclePool()
    B = "78202A0314"
    p.replay_car(B, [
        E(B, "01IS205", "0", T(8), row_id=1, flag_137="0"),
        E(B, "18IS345", "0", T(9), row_id=2, flag_137="1"),      # 3 线烘房出口
        E(B, "14IS015", "2", T(10), row_id=3, flag_137="1"),
    ])
    s = p.snapshot()[B]
    assert s.passed_oven is True
    assert s.flag_137 == "1"
    assert s.flag == "2" and s.station == "14IS015"


def test_new_fields_no_cross_vehicle_contamination():
    p = VehiclePool()
    A, B = "78202A0315", "78202A0316"
    p.ingest(E(A, "01IS205", "0", T(8), flag_137="1"))
    p.ingest(E(A, "17IS305", "0", T(9), flag_137="1"))
    p.ingest(E(B, "01IS205", "0", T(8)))
    a, b = p.snapshot()[A], p.snapshot()[B]
    assert a.passed_oven is True and a.flag_137 == "1"
    assert b.passed_oven is False and b.flag_137 == "0"


# ---------- 合成状态机矩阵（docs/15 W4.3c：池态 × eta_v2 状态机联动） ----------

def test_pre_oven_false_blacktop_silent_mainline():
    # 烘房前假 2（非白名单站）：池态 passed_oven=False → 状态机静默 '0' → 分支 B 主线 + delay
    p = VehiclePool()
    B = "78202A0320"
    p.ingest(E(B, "01IS205", "0", T(8), flag_137="1"))
    p.ingest(E(B, "15IS120", "2", T(10), flag_137="1"))         # 139=2 未出炉非白名单
    s = p.snapshot()[B]
    eff = eta_v2.resolve_effective_flag(s.station, s.flag_137, s.flag, s.passed_oven)
    assert eff == "0"

    # ETA 走主线 + delay（池态驱动 estimate_event_unified 分支 B）
    t = _tables(rem={("15IS120", "2N54Y-"): (9000, 9000, 10000, 100)},
                expected_delay={("15IS120", "2N54Y-"): (1800, 1200, 50, 50)})
    last = int(s.last_event_ts.timestamp())
    eta_v_unified, _ = eta_v2.estimate_event_unified(
        last, s.station, s.flag_137, s.flag, "2N54Y-", "2026-W34", t,
        mode="vehicle", passed_oven=s.passed_oven)
    assert eta_v_unified == last + 9000 + 1200      # 主线基线 + 净延误（非环分布）


def test_post_oven_true_blacktop_loop_distribution():
    # 出炉后真 2（白名单站）：eff '2' → 分支 A 环分布
    p = VehiclePool()
    B = "78202A0321"
    p.ingest(E(B, "01IS205", "0", T(8), flag_137="1"))
    p.ingest(E(B, "17IS305", "0", T(9), flag_137="1"))
    p.ingest(E(B, "24IS185", "2", T(10), flag_137="1"))         # 黑顶锚点（报交入口簇首发站）
    s = p.snapshot()[B]
    eff = eta_v2.resolve_effective_flag(s.station, s.flag_137, s.flag, s.passed_oven)
    assert eff == "2"
    t = _tables(loop_rem={("24IS185", "2", "2N54Y-"): (36000, 40000, 44000, 100)})
    last = int(s.last_event_ts.timestamp())
    eta_u, used = eta_v2.estimate_event_unified(
        last, s.station, s.flag_137, s.flag, "2N54Y-", "2026-W34", t,
        mode="vehicle", passed_oven=s.passed_oven)
    assert (eta_u, used) == (last + 36000, False)    # 环分布 P50，无叠加


def test_post_gateway_sticky1_unfreeze_model_baseline():
    # 出网关后粘滞 1：R1 强制解冻 '0' → 分支 B 车型专属基线（delay=0）
    # 注：站选 25IS010（37 出网关站之一且不在 W3 出池 4 站内——K2IS075/K3IS140 等
    # 出走廊站是 W3 池出口，会把车移出池，不适用）
    p = VehiclePool()
    B = "78202A0322"
    p.ingest(E(B, "01IS205", "0", T(8)))
    p.ingest(E(B, "17IS305", "0", T(9)))
    p.ingest(E(B, "25IS010", "1", T(10)))             # 出环站 + 139=1 粘滞（未过出走廊）
    s = p.snapshot()[B]
    eff = eta_v2.resolve_effective_flag(s.station, s.flag_137, s.flag, s.passed_oven)
    assert eff == "0"
    t = _tables(rem={("25IS010", "IE14.-"): (59400, 59400, 61000, 60)})
    last = int(s.last_event_ts.timestamp())
    eta_u, _ = eta_v2.estimate_event_unified(
        last, s.station, s.flag_137, s.flag, "IE14.-", "2026-W34", t,
        mode="vehicle", passed_oven=s.passed_oven)
    assert eta_u == last + 59400                     # 车型专属 16.5h（D14-5）


def _tables(**kw):
    base = {"rem": {}, "loop_rem": {}, "loop_dur": {}, "takt": {},
            "fut_week": {}, "fut_all": {}, "cohort": {}, "lineprob": {},
            "quality": {}, "expected_delay": {}}
    base.update(kw)
    return base
