# -*- coding: utf-8 -*-
"""T2.5 死车机制（docs/03 §6 定版：M7 三点 + B3 联动）。

判定基准 = 自**最近事件**无新事件 ≥ dead_vehicle_hours（不是总行程）；
移出活跃池 → DeadCar 报表；不计班次预测（T3 只读活跃池）；
新事件自动复活 = R4 收养（天然成立，测试钉死）；
审计写 dss.work_alert_log（DDL 本 task 建；编排层 record_dead_audit——
冻结契约 dead_sweep(self, now, frozen) 无 conn，DB 持久化不混入纯状态机）。

阈值压缩（B4 点 7「测试可压缩阈值」）：VehiclePool(dead_vehicle_hours=1.0)。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from spike_w3.stream_pipeline import (
    DeadCar, Event, VehiclePool, record_dead_audit,
)

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 24, 23, 0, tzinfo=TZ)
NAIVE = datetime(2026, 9, 24, 23, 0)  # R7 反例


def E(body_id, station, flag, ts, row_id, body_type="78202A-"):
    return Event(body_id, body_type, station, flag, ts, row_id)


class FakeCursor:
    def __init__(self):
        self.queries = []

    def execute(self, sql, params=None):
        # 占位符校验（agy 审核低②纪律，防 SQL 参数失配逃逸）
        if params is not None:
            assert sql.count("%s") == len(params), f"占位符 {sql.count('%s')} != 参数 {len(params)}"
        self.queries.append((sql, params))
        return self

    def fetchall(self):
        return []


class FakeConn:
    def __init__(self):
        self._cur = FakeCursor()

    def cursor(self):
        return self._cur


# ---------- Step 1：判定 + 移出池 + DeadCar 字段 ----------

def test_dead_sweep():
    pool = VehiclePool(dead_vehicle_hours=1.0)      # 阈值压缩（B4 点 7）
    t_dead = NOW - timedelta(hours=1, minutes=6)    # = 1.1h 前 → 超 1h 阈值
    t_live = NOW - timedelta(minutes=54)            # < 1h → 不动
    pool.ingest(E("78202D001", "10IS255", "0", t_dead, 1))
    pool.ingest(E("78202D002", "14IS015", "2", t_live, 2))

    dead = pool.dead_sweep(NOW, frozen=False)
    assert len(dead) == 1
    d = dead[0]
    assert d.body_id == "78202D001"
    assert d.station == "10IS255" and d.flag == "0"
    assert d.since == t_dead                        # since = 最近事件 ts
    assert abs(d.hours - 1.1) < 1e-9                # hours ≈ 121 的压缩版

    snap = pool.snapshot()
    assert "78202D001" not in snap                  # 移出活跃池（T3 只读活跃池 → 不计预测）
    assert "78202D002" in snap                      # 未超阈值不动
    assert set(pool._visited) == set(snap)          # _visited 不变式：set/del 成对

    # 幂等：再扫一遍 = 空（已移出）
    assert pool.dead_sweep(NOW) == []
    # R7：naive now → TypeError
    with pytest.raises(TypeError):
        pool.dead_sweep(NAIVE)


# ---------- Step 2：死车复活 = R4 收养（不改代码只钉行为） ----------

def test_dead_revive():
    pool = VehiclePool(dead_vehicle_hours=1.0)
    pool.ingest(E("78202D001", "10IS255", "0", NOW - timedelta(hours=2), 1))
    dead = pool.dead_sweep(NOW)
    assert len(dead) == 1 and "78202D001" not in pool.snapshot()

    # 新事件到来（过站信号恢复）→ 中途收养，entry_ts = 新事件 ts
    t_new = NOW - timedelta(minutes=5)
    pool.ingest(E("78202D001", "12IS015", "1", t_new, 2))
    st = pool.snapshot()["78202D001"]
    assert st.entry_ts == t_new                     # 复活 = 新周期
    assert st.station == "12IS015" and st.flag == "1"


# ---------- Step 3：frozen=True（B3 STALE）→ 空操作、无移出 ----------

def test_dead_frozen_when_stale():
    pool = VehiclePool(dead_vehicle_hours=1.0)
    pool.ingest(E("78202D001", "10IS255", "0", NOW - timedelta(hours=2), 1))
    dead = pool.dead_sweep(NOW, frozen=True)
    assert dead == []
    assert "78202D001" in pool.snapshot()           # 冻结期间死车判定挂起（不丢数据）


# ---------- Step 4（补）：死车移除保留 _seen_row_ids（与出站路径一致，防迟到重算审计双计） ----------

def test_dead_sweep_replay_no_audit_double_count():
    """死车移除后迟到重算（replay_car 同 row_id 切片）不得重复计审计。

    出站站/延续站移除路径均保留 _seen_row_ids（生命周期去重集）；死车移除若 pop，
    后续 skew 迟到触发 per-car 重算时 seen 为空 → 旧 row_id 的 dup/invalid 重复计数。
    """
    pool = VehiclePool(dead_vehicle_hours=1.0)
    t0 = NOW - timedelta(hours=2)
    evs = [E("78202D001", "10IS255", "0", t0, 1),
           E("78202D001", "10IS255", "0", t0 + timedelta(minutes=1), 2),   # 同站 dup = 1
           E("78202D001", "12IS015", "1", t0 + timedelta(minutes=2), 3)]
    for ev in evs:
        pool.ingest(ev)
    assert pool.audit["same_station_dups"]["78202D001"] == 1
    assert len(pool.dead_sweep(NOW)) == 1

    # 迟到窗内新事件触发 per-car 重算（切片 = 同 3 事件 + 1 新事件）
    evs2 = evs + [E("78202D001", "14IS015", "1", NOW - timedelta(minutes=30), 4)]
    pool.replay_car("78202D001", evs2)
    # 去重集保留 → 旧 row_id 不重计；r4 异站无 dup → 计数保持 1
    assert pool.audit["same_station_dups"]["78202D001"] == 1
    # 复活（R4 收养）且状态 = 重算结果
    st = pool.snapshot()["78202D001"]
    assert st.station == "14IS015"


# ---------- Step 4：审计 SQL（dss 只写，防误写 ods） ----------

def test_dead_audit_sql():
    t_since = NOW - timedelta(hours=2)
    dead = [DeadCar(body_id="78202D001", station="10IS255", flag="0",
                    since=t_since, hours=2.0)]
    conn = FakeConn()
    n = record_dead_audit(conn, dead, NOW)
    assert n == 1

    sqls = [q[0] for q in conn._cur.queries]
    # DDL：只建 dss schema 的可选审计表（幂等）
    assert any("CREATE TABLE IF NOT EXISTS dss.work_alert_log" in s for s in sqls)
    # INSERT：type='dead_car' 字面量、只写 dss
    ins = [s for s in sqls if s.lstrip().upper().startswith("INSERT")]
    assert len(ins) == 1
    assert "dss.work_alert_log" in ins[0]
    assert "'dead_car'" in ins[0]
    for schema in ("ods.", "dim.", "fct.", "mart.", "meta."):
        assert schema not in ins[0], f"审计 SQL 不得写 {schema}（只读原则）"
    # 参数 = (body_id, station, flag, since, detected_at)
    _, params = conn._cur.queries[1]
    assert params == ("78202D001", "10IS255", "0", t_since, NOW)

    # 空列表 = 不写行（仍幂等，无 INSERT）
    conn2 = FakeConn()
    assert record_dead_audit(conn2, [], NOW) == 0
    assert all(not s.lstrip().upper().startswith("INSERT") for s, _ in conn2._cur.queries)
