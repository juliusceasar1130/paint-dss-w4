# -*- coding: utf-8 -*-
"""T3.1 滚动预测器（predictor.py）纯逻辑单测（docs/04 §0 冻结接口 + §3 步骤）。

全离线：不连库（a_of/predict 用 FakeConn/FakeCursor 固定行集）；
时区纪律同 T2：全 aware Asia/Shanghai，naive 抛 TypeError。
"""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from spike_w2 import eta_v2
from spike_w3.common import EXIT_STATIONS, Shift
from spike_w3.stream_pipeline import VehicleState

CST = ZoneInfo("Asia/Shanghai")
ALL = eta_v2.ALL
E = int(datetime(2026, 9, 8, 20, 0, 0, tzinfo=CST).timestamp())
BASE = datetime(2026, 9, 8, 20, 0, 0, tzinfo=CST)
ANCHOR = datetime(2026, 9, 8, 21, 30, 0, tzinfo=CST)


def _v(flag="0", station="14IS260", model="2N54Y-", ts=BASE):
    return VehicleState(
        body_type=model, station=station, flag=flag, last_event_ts=ts,
        entry_ts=ts - timedelta(hours=12), exits_seen=0, loop_count=0,
    )


def _tables():
    """合成表（手算基准，行尾 n ≥ MIN_N=30 才可用）：
    - 主分布 (14IS260, 2N54Y-) P50 = 5400s
    - 补偿 fut_all p1=0.10 / p2=0.05；环时长 L1=3600s / L2=1800s
      → flag=0 ETA = E + round(5400 + 0.10·3600 + 0.05·1800) = E + 5850
    - 环分布 flag=1 P50 = 7200s（无补偿）
    """
    return {
        "rem": {("14IS260", "2N54Y-"): (5400.0, 5500.0, 7200.0, 100)},
        "loop_rem": {("14IS260", "1", "2N54Y-"): (7200.0, 7300.0, 9000.0, 100)},
        "loop_dur": {("1", "2N54Y-"): (3600.0, 50), ("2", "2N54Y-"): (1800.0, 50)},
        "takt": {},
        "fut_week": {},
        "fut_all": {("14IS260", "2N54Y-"): (0.10, 0.05, 100)},
        "cohort": {},
        "lineprob": {},
        "quality": {},
    }


def test_rem_of_flag0_flag1_and_none():
    from spike_w3.predictor import rem_of

    tables = _tables()
    # flag=0：主分布 5400 + 补偿 0.10×3600 + 0.05×1800 = 5850s（与 eta_v2 手算一致）
    assert rem_of(_v(flag="0"), ANCHOR, tables, frozenset()) == 5850.0
    # flag=1：环分布 7200s，无补偿
    assert rem_of(_v(flag="1"), ANCHOR, tables, frozenset()) == 7200.0
    # comp_excl（(model, station) 豁免）→ 补偿 = 0
    excl = frozenset({("2N54Y-", "14IS260")})
    assert rem_of(_v(flag="0"), ANCHOR, tables, excl) == 5400.0
    # 站缺行 → None（no-ETA 桶）
    assert rem_of(_v(flag="0", station="99IS999"), ANCHOR, tables, frozenset()) is None
    assert rem_of(_v(flag="1", station="99IS999"), ANCHOR, tables, frozenset()) is None
    # tz 纪律：naive t 抛 TypeError
    try:
        rem_of(_v(flag="0"), ANCHOR.replace(tzinfo=None), tables, frozenset())
        raise AssertionError("naive t 未抛 TypeError")
    except TypeError:
        pass


# ---------- 测试替身（同 T2 模式：记录 (sql, params) 供断言） ----------

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


def _day_shift(attr, end):
    return Shift(kind="day", day=attr.date(), work_start=attr,
                 attr_start=attr, end=end, type="11h")


def _feed_pool():
    """合成池：直喂 5 车（入口 01IS205 → 末站事件）。t = 09-08 10:00，end = 16:30
    （end−t = 23400s）：
    - v1 2N54Y- @14IS260 rem 3600s（正常，计入 pred）
    - v2 IM186- @14IS260 rem 25000s（α=1 超窗；α=1.2 → 20833s 计入）
    - v3 @99IS999（站缺行 → no-ETA）
    - v4 2N54Y- @14IS260 last=t−7h（stale，仍计 pred）
    - v5 88888888888888（非产品假号 → 不入在制集）
    """
    from spike_w3.stream_pipeline import Event, VehiclePool

    t = datetime(2026, 9, 8, 10, 0, 0, tzinfo=CST)
    pool = VehiclePool()
    evs = [
        ("78202A0001", "2N54Y-", "01IS205", t - timedelta(hours=12), 1),
        ("78202A0001", "2N54Y-", "14IS260", t - timedelta(hours=1), 2),
        ("78202A0002", "IM186-", "01IS205", t - timedelta(hours=13), 3),
        ("78202A0002", "IM186-", "14IS260", t - timedelta(hours=2), 4),
        ("78202A0003", "2N54Y-", "01IS205", t - timedelta(hours=14), 5),
        ("78202A0003", "2N54Y-", "99IS999", t - timedelta(hours=3), 6),
        ("78202A0004", "2N54Y-", "01IS205", t - timedelta(hours=19), 7),
        ("78202A0004", "2N54Y-", "14IS260", t - timedelta(hours=7), 8),
        ("88888888888888", "2N54Y-", "01IS205", t - timedelta(hours=15), 9),
        ("88888888888888", "2N54Y-", "14IS260", t - timedelta(hours=4), 10),
    ]
    for body, model, station, ts, rid in evs:
        pool.ingest(Event(body, model, station, "0", ts, rid))
    tables = {
        "rem": {("14IS260", "2N54Y-"): (3600.0, 3700.0, 5000.0, 100),
                ("14IS260", "IM186-"): (25000.0, 26000.0, 30000.0, 100)},
        "loop_rem": {}, "loop_dur": {}, "takt": {},
        "fut_week": {}, "fut_all": {}, "cohort": {}, "lineprob": {}, "quality": {},
    }
    return pool, tables, t


def test_predict_alpha1_matches_manual():
    from spike_w3.common import DSSConfig
    from spike_w3.predictor import predict

    pool, tables, t = _feed_pool()
    shift = _day_shift(datetime(2026, 9, 8, 4, 45, tzinfo=CST),
                       datetime(2026, 9, 8, 16, 30, tzinfo=CST))
    conn = FakeConn(FakeCursor([[ (30,) ], [ (30,) ]]))  # A(t) = 30（两次 predict 各查一次）
    # α=1：pred = 30 + (v1 3600≤23400 ✓, v2 25000>23400 ✗, v4 stale 仍计 ✓) = 32
    r = predict(conn, pool, shift, t, tables, frozenset(), DSSConfig(), alpha=1.0)
    assert r["A"] == 30 and r["inproc"] == 4 and r["pred"] == 32
    assert r["no_eta"] == 1 and r["stale"] == 1
    assert r["shift_id"] == "2026-09-08_day" and r["t"] == t
    # by_vehicle：4 行（假号 88888 不出现）；v3 rem_sec=None 仍在明细；v4 stale 标
    rows = {x["body_id"]: x for x in r["by_vehicle"]}
    assert set(rows) == {"78202A0001", "78202A0002", "78202A0003", "78202A0004"}
    assert rows["78202A0001"]["rem_sec"] == 3600.0 and not rows["78202A0001"]["stale"]
    assert rows["78202A0002"]["rem_sec"] == 25000.0
    assert rows["78202A0003"]["rem_sec"] is None
    assert rows["78202A0004"]["stale"] is True and rows["78202A0004"]["rem_sec"] == 3600.0
    # α=1.2：v2 rem/1.2 = 20833.3 ≤ 23400 计入 → pred = 33
    r2 = predict(conn, pool, shift, t, tables, frozenset(), DSSConfig(), alpha=1.2)
    assert r2["pred"] == 33 and r2["inproc"] == 4  # α 只作用于 rem 比较式，不动 A/inproc


def test_a_of_sql():
    from spike_w3.predictor import a_of

    attr = datetime(2026, 9, 8, 4, 45, tzinfo=CST)
    end = datetime(2026, 9, 8, 16, 30, tzinfo=CST)
    shift = _day_shift(attr, end)
    t = datetime(2026, 9, 8, 9, 45, 0, tzinfo=CST)  # t 整点：闭区间含端原样传入
    cur = FakeCursor([[ (7,) ]])
    n = a_of(FakeConn(cur), shift, t)
    assert n == 7
    sql, params = cur.queries[0]
    # 闭区间 [attr_start, t] 两端含（与 W2 静态 02_replay_shift.py:260 同义；
    # 16:30:00 交接双计构造性一致，外部审低⑤）
    assert '"DATE_EVT" >= %s' in sql and '"DATE_EVT" <= %s' in sql
    # 窗内任一出口事件该车计一次（去重）；仅产品车（非 78202 前缀不计）
    assert 'COUNT(DISTINCT "BODY_ID")' in sql
    assert "LIKE '78202%%'" in sql
    # 4 出口站内联（代码常量非用户输入）；占位符 = 仅 2 个时间边界（= 参数数）
    # 站名是字符串值 → 单引号（Postgres 双引号 = 标识符，会报 column does not exist，
    # 2026-09-25 M9 真库首跑实测炸过）
    for st in EXIT_STATIONS:
        assert f"'{st}'" in sql
        assert f'"{st}"' not in sql
    assert sql.count("%s") == 2 and len(params) == 2
    assert params == (attr, t)  # 原样传入（无截断/无时区变换）
    # naive t 抛 TypeError
    try:
        a_of(FakeConn(FakeCursor([[(0,)]])), shift, t.replace(tzinfo=None))
        raise AssertionError("naive t 未抛 TypeError")
    except TypeError:
        pass


def test_predict_tz_and_edge():
    from spike_w3.common import DSSConfig
    from spike_w3.predictor import predict
    from spike_w3.stream_pipeline import VehiclePool

    pool, tables, t = _feed_pool()
    attr = datetime(2026, 9, 8, 4, 45, tzinfo=CST)
    end = datetime(2026, 9, 8, 16, 30, tzinfo=CST)
    shift = _day_shift(attr, end)
    cfg = DSSConfig()

    # naive t → TypeError
    try:
        predict(FakeConn(FakeCursor([[(30,)]])), pool, shift, t.replace(tzinfo=None),
                tables, frozenset(), cfg)
        raise AssertionError("naive t 未抛 TypeError")
    except TypeError:
        pass

    # 空池 → A-only（无在制、无 no-ETA/stale 明细）
    r = predict(FakeConn(FakeCursor([[(30,)]])), VehiclePool(), shift, t,
                tables, frozenset(), cfg)
    assert r["A"] == 30 and r["inproc"] == 0 and r["pred"] == 30
    assert r["no_eta"] == 0 and r["stale"] == 0 and r["by_vehicle"] == []

    # t = end（全量）：无剩余时间可命中 → pred = A(end)，不抛错
    r = predict(FakeConn(FakeCursor([[(41,)]])), pool, shift, end,
                tables, frozenset(), cfg)
    assert r["A"] == 41 and r["pred"] == 41

    # t < attr_start（无意义输入）→ ValueError
    try:
        predict(FakeConn(FakeCursor([[(30,)]])), pool, shift, attr - timedelta(hours=1),
                tables, frozenset(), cfg)
        raise AssertionError("t < attr_start 未抛 ValueError")
    except ValueError:
        pass
