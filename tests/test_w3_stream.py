# -*- coding: utf-8 -*-
"""T2.2 增量消费：EventStream（ID 水位 + :now 上界 + skew 两档）/ replay_car 合并 / reconcile。

全离线：FakeCursor 测试替身（execute 返回预置行 + 记录 SQL 文本/参数供断言）。
SQL 断言钉死：`WHERE "ID" > %s` + `DATE_EVT <= %s` 上界（一轮 B1 阻断回归）+
`"BODY_TYPE"` 列（F5 回归）+ `ORDER BY "ID"` + flag 取 `SUBSTRING(MDS_DATA FROM 139 FOR 1)`。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from spike_w3.stream_pipeline import Event, EventStream, VehiclePool

TZ = ZoneInfo("Asia/Shanghai")
MIN = timedelta(minutes=1)


def E(body_id, station, flag, ts, row_id=0, body_type="78202A-"):
    return Event(body_id, body_type, station, flag, ts, row_id)


t0 = datetime(2026, 9, 7, 0, 0, tzinfo=TZ)


# ---------- 测试替身（docs/03 T2.2 Step 1） ----------

class FakeCursor:
    """execute(sql, params) 依次弹出预置行批；记录 (sql, params) 供断言。"""

    def __init__(self, batches=()):
        self._batches = list(batches)
        self.queries = []
        self._rows = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        self._rows = self._batches.pop(0) if self._batches else []
        return self

    def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor

    def close(self):
        pass


# ---------- Step 1：水位 + SQL 纪律 ----------

def test_consume_watermark_sql():
    cur = FakeCursor([
        [
            (101, "78202A0001", "2N54Y-", "01IS205", "0", t0),
            (102, "78202A0001", "2N54Y-", "10IS255", "0", t0 + MIN),
            (103, "78202A0002", "2N54Y-", "K3IS140", "0", t0 + 2 * MIN),
        ]  # 单查询单批：一次拉取三行
    ])
    stream = EventStream(FakeConn(cur), wm=100)
    pool = VehiclePool()
    events = stream.consume(now=t0 + 3 * MIN, pool=pool)

    # SQL 纪律（B1 阻断回归 + F5 回归，逐条断言）
    sql, params = cur.queries[0]
    assert '"ID" > %s' in sql
    assert '"DATE_EVT" <= %s' in sql          # 缺上界 = 一轮 B1 阻断（replay 首轮拉穿全表）
    assert '"BODY_TYPE"' in sql               # 缺列 = F5 回归（R5 直取口径）
    assert 'ORDER BY "ID"' in sql
    assert 'SUBSTRING("MDS_DATA" FROM 139 FOR 1)' in sql
    assert params == (100, t0 + 3 * MIN)      # wm 起点 + :now 上界

    # 水位推进 = max(row_id)；事件原样（row_id/body_type 透传）
    assert stream.watermark == 103
    assert [e.row_id for e in events] == [101, 102, 103]
    assert events[0].body_type == "2N54Y-" and events[0].flag == "0"

    # 顺序事件已喂 pool（首轮无 front 基准 = 全部顺序）
    assert pool.snapshot()["78202A0001"].station == "10IS255"
    assert "78202A0002" not in pool.snapshot() and pool.audit["exits"]["78202A0002"] == 1


# ---------- Step 1b：轮内乱序 ts 排序分发（T2.6 真库回放性能踩坑修，2026-09-25） ----------

def test_consume_round_internal_order_ts_sorted():
    """轮内 ID/ts 逆序（真实数据 ~52%）：consume 按 (ts, ID) 序分发 →
    front 单调推进、刚够格行全部顺序 ingest → 零按车迟到子查询
    （真库 7 天回放踩坑：ID 序分发把刚够格行误判窗内迟到 → ~20 万次 per-car 子查询 → 11h）。
    真迟到（ts < front − 窗）仍走 replay_car 兑底（不受本修影响，Step 2 用例钉死）。"""
    B = "78202A0201"
    p = VehiclePool()
    now = T(11, 0)
    # 本轮拉取（DB 按 ID 序返回）：ID 501 ts=10:55 / 502 ts=10:45 / 503 ts=10:40
    # —— ts 沿 ID 序递减；front=None（冷启动），三行都是本轮刚够格（ts 均在 now−15min 内）
    cur = FakeCursor([
        [
            (501, B, "2N54Y-", "14IS015", "0", T(10, 55)),
            (502, B, "2N54Y-", "12IS015", "0", T(10, 45)),
            (503, B, "2N54Y-", "10IS255", "0", T(10, 40)),
        ],
    ])
    stream = EventStream(FakeConn(cur), wm=500)
    events = stream.consume(now=now, pool=p)
    # 零按车迟到重算子查询（仅 consume 一条 SQL）
    assert len(cur.queries) == 1, [q[0][:80] for q in cur.queries]
    # 状态 = ts 序结果：10IS255(10:40) → 12IS015(10:45) → 14IS015(10:55)
    s = p.snapshot()[B]
    assert s.station == "14IS015" and s.last_event_ts == T(10, 55)
    assert stream.watermark == 503 and stream.front == T(10, 55)
    # 返回 = 本轮全部事件（全顺序），ts 序
    assert [e.row_id for e in events] == [503, 502, 501]


# ---------- Step 2：skew 两档分流（顺序 / 窗内迟到 → replay / 更旧 → 审计） ----------

def T(h, m=0):
    return datetime(2026, 9, 7, h, m, tzinfo=TZ)


def test_consume_normal_and_late_split():
    p = VehiclePool()
    B, C = "78202A0101", "78202A0102"
    # 先前顺序事件：B 10:30 进站、10:50 过 10IS255 → front=10:50
    p.ingest(E(B, "01IS205", "0", T(10, 30)))
    p.ingest(E(B, "10IS255", "0", T(10, 50)))
    now = T(10, 58)
    # 本轮拉取（单批，ID 序）：301 顺序 10:55 / 302 窗内迟到 10:40（=front−15min 边界）/ 303 窗外更旧 09:00
    cur = FakeCursor([
        [
            (301, B, "2N54Y-", "20IS010", "0", T(10, 55)),
            (302, B, "2N54Y-", "14IS015", "1", T(10, 40)),
            (303, C, "2N54Y-", "10IS255", "0", T(9, 0)),
        ],
        # 按车日志切片（since=entry_ts=10:30 ~ now），DATE_EVT, ID 序（含已顺序消费的 301）
        [
            (302, B, "2N54Y-", "14IS015", "1", T(10, 40)),
            (301, B, "2N54Y-", "20IS010", "0", T(10, 55)),
        ],
    ])
    stream = EventStream(FakeConn(cur), wm=300)
    stream.front = T(10, 50)
    events = stream.consume(now=now, pool=p)

    # 顺序 → ingest + front 推进；返回 = 仅顺序事件
    assert [e.row_id for e in events] == [301]
    assert stream.front == T(10, 55)
    assert stream.watermark == 303  # 水位推进 = 拉取的全部行（迟到/更旧行也算）
    # 迟到 → 按车重算：SQL 纪律（since=entry_ts 全周期重算）
    sql, params = cur.queries[1]
    assert '"BODY_ID" = %s' in sql and 'BETWEEN' in sql and 'ORDER BY "DATE_EVT","ID"' in sql
    assert params == (B, T(10, 30), now)
    # 合并结果：station = 重算最新（20IS010@10:55）；entry_ts / exits_seen 不动
    s = p.snapshot()[B]
    assert s.station == "20IS010" and s.entry_ts == T(10, 30) and s.exits_seen == 0
    # 更旧 → 只计数不动池（C 未收养、无额外查询）
    assert p.audit["late_out_of_window"] == 1
    assert C not in p.snapshot() and len(cur.queries) == 2


# ---------- Step 3：replay_car 合并规则（F7 定版三用例） ----------

def test_replay_car_merge_rule():
    # ① 迟到有效 flag 修正最新点继承：A0@10:00、C'-'@11:00 已顺序消费 → flag=0；
    #    B1@10:30 迟到 → 重算 [A0, B1, C'-] → flag=1、entry_ts 不动
    B = "78202A0110"
    p = VehiclePool()
    p.ingest(E(B, "01IS205", "0", T(10, 0)))
    p.ingest(E(B, "10IS255", "-", T(11, 0)))
    assert p.snapshot()[B].flag == "0"
    p.replay_car(B, [
        E(B, "01IS205", "0", T(10, 0), row_id=1),
        E(B, "14IS015", "1", T(10, 30), row_id=2),
        E(B, "10IS255", "-", T(11, 0), row_id=3),
    ])
    s = p.snapshot()[B]
    assert s.flag == "1" and s.station == "10IS255" and s.entry_ts == T(10, 0)

    # ② 迟到出站事件 + 车不在池 → 不收养仅审计
    D = "78202A0111"
    p.replay_car(D, [
        E(D, "10IS255", "0", T(10, 0), row_id=1),
        E(D, "K3IS140", "0", T(12, 0), row_id=2),
    ])
    assert D not in p.snapshot()

    # ③ 迟到窗内事件不动 exits_seen / entry_ts（有前周期历史的车）
    Cc = "78202A0112"
    p.ingest(E(Cc, "01IS205", "0", T(8, 0)))
    p.ingest(E(Cc, "K1IS135", "0", T(10, 0)))   # 出站 #1 → 移除，exits=1
    p.ingest(E(Cc, "01IS205", "0", T(11, 0)))   # 进站 #2 → 重新入池，exits_seen=1
    assert p.snapshot()[Cc].exits_seen == 1
    p.replay_car(Cc, [
        E(Cc, "01IS205", "0", T(11, 0), row_id=1),
        E(Cc, "20IS010", "1", T(12, 0), row_id=2),
    ])
    s = p.snapshot()[Cc]
    assert s.entry_ts == T(11, 0) and s.exits_seen == 1 and s.station == "20IS010" and s.flag == "1"


# ---------- Step 3b：幂等（同一迟到事件重算两次 → 状态不变、审计不重复，row_id 去重） ----------

def test_replay_car_idempotent():
    B = "78202A0113"
    p = VehiclePool()
    p.ingest(E(B, "01IS205", "0", T(10, 0), row_id=10))
    p.ingest(E(B, "10IS255", "1", T(10, 20), row_id=11))
    slice_events = [
        E(B, "01IS205", "0", T(10, 0), row_id=10),    # 已顺序消费（seen）
        E(B, "10IS255", "1", T(10, 20), row_id=11),   # 已顺序消费（seen）
        E(B, "10IS255", "1", T(10, 21), row_id=12),   # 新：连续同站重复（R1）
        E(B, "20IS010", "-", T(10, 30), row_id=13),   # 新：无效 flag（R2）
    ]
    p.replay_car(B, slice_events)
    # 重算：flag 0→1→1→继承=1；station 20IS010；loop 0；新 dups=1（r12）、新 invalid=1（r13）
    after1 = (p.snapshot()[B].station, p.snapshot()[B].flag, p.snapshot()[B].loop_count,
              p.audit["same_station_dups"].get(B, 0), p.audit["invalid_flags"].get(B, 0))
    assert after1 == ("20IS010", "1", 0, 1, 1)
    # 同一切片重算两次 → 状态不变、审计计数不重复
    p.replay_car(B, slice_events)
    after2 = (p.snapshot()[B].station, p.snapshot()[B].flag, p.snapshot()[B].loop_count,
              p.audit["same_station_dups"].get(B, 0), p.audit["invalid_flags"].get(B, 0))
    assert after2 == after1


# ---------- Step 4：每日全量核对（运行池 1 漂移 + 1 缺失 + 1 多出 → 数字精确） ----------

def test_reconcile_report():
    from spike_w3.stream_pipeline import reconcile
    now = datetime(2026, 9, 7, 16, 0, tzinfo=TZ)
    A, B, X, Y = "78202A0120", "78202A0121", "78202A0122", "78202A0123"
    # 运行池（live）：A 位置滞后 / B 一致 / X 仅在 live
    live = VehiclePool()
    live.ingest(E(A, "01IS205", "0", T(9, 0)))
    live.ingest(E(A, "10IS255", "0", T(14, 0)))
    live.ingest(E(B, "01IS205", "0", T(9, 0)))
    live.ingest(E(B, "20IS010", "0", T(13, 0)))
    live.ingest(E(X, "01IS205", "0", T(9, 0)))
    live.ingest(E(X, "10IS255", "0", T(9, 30)))
    # 7 日干净池事件源（reconcile 查询预置行）：A 真实位置 14IS015 / B 一致 / Y 仅在 fresh
    cur = FakeCursor([
        [
            (401, A, "2N54Y-", "01IS205", "0", T(9, 0)),
            (402, A, "2N54Y-", "10IS255", "0", T(12, 0)),
            (403, A, "2N54Y-", "14IS015", "0", T(14, 30)),
            (404, B, "2N54Y-", "01IS205", "0", T(9, 0)),
            (405, B, "2N54Y-", "20IS010", "0", T(13, 0)),
            (406, Y, "2N54Y-", "01IS205", "0", T(15, 0)),
        ]
    ])
    rep = reconcile(FakeConn(cur), now, live, days=7)
    assert rep.pool_size == 3
    assert rep.only_in_live == 1      # X
    assert rep.only_in_fresh == 1     # Y
    assert rep.state_mismatches == 1  # A（live 10IS255 vs fresh 14IS015）
    # SQL 纪律：7 日窗口 + 产品车过滤 + 按 ts 序喂
    sql, params = cur.queries[0]
    assert '"DATE_EVT" >= %s' in sql and "LIKE '78202%" in sql and 'ORDER BY "DATE_EVT","ID"' in sql
    assert params == (now - timedelta(days=7),)


# ---------- Step 5：derive_postexit_stations 启动闸门（出站延续站数据驱动重推） ----------

def test_derive_postexit_stations():
    from spike_w3.common import EXIT_STATIONS
    from spike_w3.stream_pipeline import derive_postexit_stations
    cur = FakeCursor([
        [
            ("09IS360", 72117, 72092),   # rate 0.9997 → 命中
            ("K3IS230", 68489, 68100),   # rate 0.994  → 命中
            ("10IS255", 90000, 100),     # rate 0.001  → 不命中（线内站）
            ("K1IS225", 10, 10),         # rate 1.0 但 total < 30 → 不命中（小样本排除）
        ]
    ])
    result = derive_postexit_stations(FakeConn(cur), min_rate=0.95)
    assert result == frozenset({"09IS360", "K3IS230"})
    # SQL 纪律：产品车过滤 + 4 出站站前置判定 + 小样本阈值参数（min_rate 比较在 Python 侧）
    sql, params = cur.queries[0]
    assert "LIKE '78202%" in sql and 'FILTER (WHERE' in sql and 'IN (%s' in sql
    assert tuple(params) == tuple(EXIT_STATIONS) + (30,)
