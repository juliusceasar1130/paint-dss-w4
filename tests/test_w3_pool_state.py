# -*- coding: utf-8 -*-
"""T2.1 池状态机核心：R1–R7 全单测（合成事件流，全离线）。

测试夹具（docs/03 §2 定版）：所有合成 ts 一律 aware（Asia/Shanghai）。
每规则一个测试，规则表 = docs/03 §2（R1 同站幂等 / R2 flag 继承 / R3 多次进出 /
R4 中途收养排出站 / R5 BODY_TYPE 直取 / R6 loop 计数 / R7 tz 强制）。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from spike_w3.common import POST_EXIT_STATIONS
from spike_w3.stream_pipeline import Event, VehiclePool

TZ = ZoneInfo("Asia/Shanghai")


def E(body_id, station, flag, ts, row_id=0, body_type="78202A-"):
    # row_id/body_type 默认值（T2.2 水位断言时用真值）
    return Event(body_id, body_type, station, flag, ts, row_id)


t0 = datetime(2026, 9, 7, 0, 0, tzinfo=TZ)
MIN = timedelta(minutes=1)


@pytest.fixture
def p():
    return VehiclePool()


# ---------- R7：tz 强制 ----------

def test_ingest_rejects_naive_ts(p):
    naive = datetime(2026, 9, 7, 0, 0)  # 无 tzinfo
    with pytest.raises(TypeError):
        p.ingest(E("78202A0009", "01IS205", "0", naive))
    assert "78202A0009" not in p.snapshot()


# ---------- 生命周期主干（入口入池 / 过站更新 / 首出站移除） ----------

def test_lifecycle(p):
    p.ingest(E("78202A0001", "01IS205", "0", t0))          # 进站
    p.ingest(E("78202A0001", "10IS255", "0", t0 + MIN))    # 过站
    assert p.snapshot()["78202A0001"].station == "10IS255"
    p.ingest(E("78202A0001", "K3IS140", "0", t0 + timedelta(hours=1)))  # 首出站
    assert "78202A0001" not in p.snapshot() and p.audit["exits"]["78202A0001"] == 1


# ---------- R1：连续同站幂等（禁时间窗丢弃） ----------

def test_same_station_idempotent(p):
    B = "78202A0003"
    p.ingest(E(B, "10IS255", "0", t0))
    p.ingest(E(B, "10IS255", "0", t0 + timedelta(seconds=5)))  # 同站 5 秒后再扫
    s = p.snapshot()[B]
    # 位置不变、loop_count 不加、last_event_ts 刷新、事件未被丢弃（审计计数 +1）
    assert s.station == "10IS255" and s.last_event_ts == t0 + timedelta(seconds=5)
    assert s.loop_count == 0 and p.audit["same_station_dups"][B] == 1


# ---------- R2：M5 无效 flag 继承（不得复位 0） ----------

def test_flag_inherit(p):
    B = "78202A0004"
    t = t0
    # 0 → 1 → '-' → '3' → 0：'-'/'3' 时 flag 保持 '1'，下一个有效 '0' 才复位
    for f, expect in [("0", "0"), ("1", "1"), ("-", "1"), ("3", "1"), ("0", "0")]:
        p.ingest(E(B, "10IS255", f, t))
        t += MIN
        assert p.snapshot()[B].flag == expect
    assert p.audit["invalid_flags"][B] == 2  # '-' 与 '3' 各计一次


# ---------- R3：M6 多次进出（首出移除 / 二次入站新周期 / 多余出站审计） ----------

def test_multi_entry_exit(p):
    B = "78202A0005"
    p.ingest(E(B, "01IS205", "0", t0))                      # 进站 #1
    p.ingest(E(B, "K3IS140", "0", t0 + timedelta(hours=1)))  # 出站 #1 → 池移除
    assert B not in p.snapshot() and p.audit["exits"][B] == 1
    p.ingest(E(B, "01IS205", "1", t0 + timedelta(hours=2)))  # 进站 #2 → 新周期重新入池
    s = p.snapshot()[B]
    assert s.entry_ts == t0 + timedelta(hours=2) and s.loop_count == 0 and s.flag == "1"
    p.ingest(E(B, "1J440RB", "0", t0 + timedelta(hours=3)))  # 出站 #2（周期 2 首次出站）→ 移除
    assert B not in p.snapshot() and p.audit["exits"][B] == 2
    p.ingest(E(B, "K1IS135", "0", t0 + timedelta(hours=4)))  # 出站 #3：不在池多余出站 → 仅审计不动池
    assert B not in p.snapshot() and p.audit["exits"][B] == 3
    # R3 泛化：在池车再过入站站（返工回线头）= 同样新周期（entry_ts 重置 / loop 归 0 / flag 取该事件有效值）
    D = "78202A0008"
    p.ingest(E(D, "20IS010", "0", t0))                        # 中途收养（entry_ts=t0）
    p.ingest(E(D, "01IS205", "1", t0 + timedelta(hours=2)))   # 在池 + 入站事件 → 新周期
    s = p.snapshot()[D]
    assert s.entry_ts == t0 + timedelta(hours=2) and s.loop_count == 0 and s.flag == "1" and s.station == "01IS205"


# ---------- R6：loop_count（非连续重复过站 +1，连续段不算） ----------

def test_loop_count(p):
    B = "78202A0010"
    p.ingest(E(B, "10IS255", "0", t0))            # A
    p.ingest(E(B, "14IS015", "0", t0 + MIN))      # B
    p.ingest(E(B, "10IS255", "1", t0 + 2 * MIN))  # A 非连续重过 → loop+1
    assert p.snapshot()[B].loop_count == 1
    C = "78202A0011"
    p.ingest(E(C, "10IS255", "0", t0))            # A
    p.ingest(E(C, "10IS255", "0", t0 + MIN))      # A 连续同站（R1，不算 loop）
    p.ingest(E(C, "14IS015", "0", t0 + 2 * MIN))  # B
    p.ingest(E(C, "10IS255", "1", t0 + 3 * MIN))  # A 非连续重过 → loop=1（连续段不重复计）
    assert p.snapshot()[C].loop_count == 1


# ---------- R4：中途收养（排出站，F6） ----------

def test_midline_adoption(p):
    B = "78202A0006"
    p.ingest(E(B, "20IS010", "1", t0))   # 不在池、非入口非出站站 → 收养（entry_ts=该事件 ts 近似）
    s = p.snapshot()[B]
    assert s.station == "20IS010" and s.flag == "1" and s.entry_ts == t0
    C = "78202A0007"
    p.ingest(E(C, "K3IS140", "0", t0 + MIN))  # 不在池 + 出站站 → 不收养、仅审计（F6：禁出站事件收养）
    assert C not in p.snapshot() and p.audit["exits"][C] == 1


# ---------- R5：BODY_TYPE 直取透传（禁前缀推导） ----------

def test_body_type_passthrough(p):
    p.ingest(E("78202A0012", "01IS205", "0", t0, body_type="78202B"))  # 列值透传
    assert p.snapshot()["78202A0012"].body_type == "78202B"
    p.ingest(E("78202A0012", "10IS255", "0", t0 + MIN, body_type=""))  # 空值：保持原值、不炸
    assert p.snapshot()["78202A0012"].body_type == "78202B"


# ---------- Step 8：last_event_by_station（B7 依赖）+ 快照回读 ----------

def test_last_event_by_station(p):
    p.ingest(E("78202A0013", "01IS205", "0", t0))
    p.ingest(E("78202A0014", "01IS205", "0", t0 + MIN))
    p.ingest(E("78202A0013", "10IS255", "0", t0 + 2 * MIN))
    m = p.last_event_by_station()
    assert m["01IS205"] == t0 + MIN and m["10IS255"] == t0 + 2 * MIN
    # 站内级留存（B7，2026-09-24 代码审核）：车驶离后该站时间戳保留（0014 驶离 01IS205）
    p.ingest(E("78202A0014", "14IS015", "0", t0 + 3 * MIN))
    assert p.last_event_by_station()["01IS205"] == t0 + MIN
    # 不在池的出站事件（仅审计）也更新站级 ts（K2IS075 类关键站）
    p.ingest(E("78202A9999", "K2IS075", "0", t0 + 4 * MIN))
    assert p.last_event_by_station()["K2IS075"] == t0 + 4 * MIN
    assert VehiclePool().last_event_by_station() == {}  # 从未有事件 = 空 dict


def test_snapshot_roundtrip(p):
    p.ingest(E("78202A0015", "01IS205", "0", t0))
    p.ingest(E("78202A0015", "10IS255", "1", t0 + MIN))
    snap = p.snapshot()
    assert snap["78202A0015"].station == "10IS255" and snap["78202A0015"].flag == "1"
    assert snap["78202A0015"].entry_ts == t0 and snap["78202A0015"].last_event_ts == t0 + MIN


# ---------- 无效 flag 组合边界（2026-09-24 代码审核补：四条路径全覆盖） ----------

def test_flag_inherit_combos(p):
    # ① 不在池 + 入口 + 无效 flag：默认 '0' + 计数
    A = "78202A0020"
    p.ingest(E(A, "01IS205", "-", t0))
    s = p.snapshot()[A]
    assert s.flag == "0" and p.audit["invalid_flags"][A] == 1
    # ② 在池 + 变站 + 无效 flag：跨站继承（非仅同站路径）
    p.ingest(E(A, "10IS255", "1", t0 + MIN))
    p.ingest(E(A, "14IS015", "3", t0 + 2 * MIN))
    s = p.snapshot()[A]
    assert s.flag == "1" and s.station == "14IS015" and p.audit["invalid_flags"][A] == 2
    # ③ 在池 + 出站 + 无效 flag：池移除仍审计 invalid_flags
    p.ingest(E(A, "K3IS140", "-", t0 + 3 * MIN))
    assert A not in p.snapshot() and p.audit["exits"][A] == 1 and p.audit["invalid_flags"][A] == 3
    # ④ 收养后立刻同站重复：R1 幂等 + 计数（flag 有效则取事件值）
    B = "78202A0021"
    p.ingest(E(B, "20IS010", "0", t0))
    p.ingest(E(B, "20IS010", "1", t0 + MIN))
    s = p.snapshot()[B]
    assert s.flag == "1" and s.loop_count == 0 and p.audit["same_station_dups"][B] == 1


# ---------- R5 边界：DB 列 BODY_TYPE = NULL（None）兜底空串 ----------

def test_body_type_none_guard(p):
    p.ingest(E("78202A0022", "20IS010", "0", t0, body_type=None))
    assert p.snapshot()["78202A0022"].body_type == ""


# ---------- R4 双保险：POST_EXIT_STATIONS 第二出界 + 动态历史锁（2026-09-24 幽灵车踩坑） ----------

def T(h, m=0):
    return datetime(2026, 9, 7, h, m, tzinfo=TZ)


def test_postexit_not_adopted(p):
    # 不在池 + 过 5 个出站延续站之一 → 不收养、静默（不加任何 audit 计数）
    for st in POST_EXIT_STATIONS:
        p.ingest(E("78202A0201", st, "0", T(10, 0)))
    assert p.snapshot() == {}
    assert p.audit["exits"] == {} and p.audit["invalid_flags"] == {}
    assert p.audit["same_station_dups"] == {} and p.audit["late_out_of_window"] == 0


def test_postexit_in_pool_removed(p):
    # 在池 + 过出站延续站 → 池移除（防穿透 + 漏扫出站站清理）；站级 ts 保留
    B = "78202A0202"
    p.ingest(E(B, "10IS255", "0", T(10, 0)))
    assert B in p.snapshot()
    p.ingest(E(B, "K3IS230", "0", T(10, 30)))   # K3IS140 漏扫、K3IS230 直接出现
    assert B not in p.snapshot()
    assert "K3IS230" in p.last_event_by_station()


def test_replay_postexit_merge(p):
    # replay：最新事件在出站延续站 → 不在池不收养；在池池移除
    A, C = "78202A0203", "78202A0204"
    p.replay_car(A, [
        E(A, "10IS255", "0", T(10, 0), row_id=1),
        E(A, "09IS360", "0", T(11, 0), row_id=2),
    ])
    assert A not in p.snapshot()
    p.ingest(E(C, "10IS255", "0", T(9, 0)))
    p.replay_car(C, [
        E(C, "01IS205", "0", T(8, 0), row_id=1),
        E(C, "10IS255", "0", T(9, 0), row_id=2),
        E(C, "K2IS165", "0", T(10, 0), row_id=3),
    ])
    assert C not in p.snapshot()


def test_dynamic_exit_lock(p):
    # 动态历史锁：已出站车（exits>0）+ 中线首见（非入口站）→ 拒绝收养；重过入口站 → 允许收养
    B = "78202A0205"
    p.ingest(E(B, "01IS205", "0", T(8, 0)))
    p.ingest(E(B, "K3IS140", "0", T(12, 0)))        # 出站 → exits=1
    p.ingest(E(B, "10IS255", "0", T(13, 0)))        # 中线首见 → 锁拒绝
    assert B not in p.snapshot()
    p.ingest(E(B, "01IS205", "0", T(15, 0)))        # 返线头 → 入口站豁免，收养
    s = p.snapshot()[B]
    assert s.station == "01IS205" and s.entry_ts == T(15, 0) and s.exits_seen == 1


def test_replay_dynamic_lock(p):
    # replay：不在池 + 生命周期已出站 + 切片最新在中线站 → 不收养（锁贯穿重算/合并）
    B = "78202A0206"
    p.ingest(E(B, "01IS205", "0", T(8, 0)))
    p.ingest(E(B, "K3IS140", "0", T(12, 0)))
    p.replay_car(B, [
        E(B, "10IS255", "0", T(13, 0), row_id=1),
        E(B, "14IS015", "0", T(14, 0), row_id=2),
    ])
    assert B not in p.snapshot()


def test_replay_adoption_then_ingest(p):
    # replay 收养后新顺序事件到来 → 不 KeyError（_visited 初始化回归，2026-09-24 干跑踩坑）
    B = "78202A0207"
    p.replay_car(B, [
        E(B, "10IS255", "0", T(10, 0), row_id=1),
        E(B, "14IS015", "0", T(10, 30), row_id=2),
    ])
    assert p.snapshot()[B].station == "14IS015"
    p.ingest(E(B, "14IS015", "0", T(11, 0)))     # 收养后同站顺序事件 → R1
    p.ingest(E(B, "20IS010", "0", T(11, 30)))    # 过站 → _visited 直接索引路径
    s = p.snapshot()[B]
    assert s.station == "20IS010" and p.audit["same_station_dups"][B] == 1
