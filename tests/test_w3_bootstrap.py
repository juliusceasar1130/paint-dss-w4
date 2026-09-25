# -*- coding: utf-8 -*-
"""T2.4 B4 bootstrap：120h 回放（同一状态机）+ cross_check + BootstrapReport。

全离线：FakeCursor（bootstrap 行批 + cross_check 参考批）。
契约（docs/03 §0）：bootstrap(conn, cfg, pool, now) -> BootstrapReport；
cross_check(pool, conn) -> tuple[int, float]（裸调用形态保持；hours/now 可选注入）。
B4 七点：回放窗 = now−bootstrap_hours；同一个 ingest（monkeypatch 计数钉死，不建第二套逻辑）；
无本地状态文件；交叉核对参考 = dim.dim_vehicle_profile（carbody_last_seen_at 窗内 +
末站 ∉ 4 出口 ∪ 5 出站延续站）；watermark = 回放窗最大行 ID = 增量流起点（F8）。
"""
import sys
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from spike_w3.common import DSSConfig
from spike_w3.stream_pipeline import (
    Event, VehiclePool, bootstrap, cross_check,
)

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 24, 23, 0, tzinfo=TZ)


def E(body_id, station, flag, ts, row_id=0, body_type="78202A-"):
    return Event(body_id, body_type, station, flag, ts, row_id)


def cfg():
    return DSSConfig()  # bootstrap_hours=120 / skew_window_min=15 默认


# ---------- 测试替身（同前：每 execute 弹一批） ----------

class FakeCursor:
    def __init__(self, batches=()):
        self._batches = list(batches)
        self.queries = []
        self._rows = []

    def execute(self, sql, params=None):
        # 内置占位符校验（agy 审核低②：防 SQL 参数失配再逃逸——2026-09-24 真库
        # IndexError 就是 FakeCursor 盲弹没抓到的）
        if params is not None:
            assert sql.count("%s") == len(params), (
                f"SQL 占位符数 {sql.count('%s')} != 参数数 {len(params)}: {sql[:80]}")
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


def make_stream_rows(n: int = 50_000):
    """合成 120h 事件流（ts 随 ID 严格升序 → 全顺序，无迟到子查询）。"""
    bodies = [f"78202A{i:05d}" for i in range(50)]
    stations = ["01IS205", "10IS255", "14IS015", "17IS200", "20IS010", "21IS010", "K3IS055"]
    t0 = NOW - timedelta(hours=119)
    rows = []
    for i in range(n):
        rows.append((
            1_000_000 + i,                       # ID 升序
            bodies[i % 50],
            "2N54Y-",
            stations[i % len(stations)],         # 顺序过站（含入口 R3 新周期）
            "0",
            t0 + timedelta(seconds=i * 8),       # 50000×8s ≈ 111h ⊂ 120h 窗
        ))
    return rows


# ---------- Step 1：同路径回放（5 万条 < 30s，走 pool.ingest） ----------

def test_bootstrap_replay_same_path(monkeypatch):
    rows = make_stream_rows(50_000)
    registry = [(b,) for b in sorted({r[1] for r in rows})]   # 参考集 = 全部 50 车（diff=0）
    cur = FakeCursor([rows, registry])
    pool = VehiclePool()

    calls = {"n": 0}
    real = VehiclePool.ingest

    def counting(self, ev):
        calls["n"] += 1
        real(self, ev)

    monkeypatch.setattr(VehiclePool, "ingest", counting)

    t = time.time()
    report = bootstrap(FakeConn(cur), cfg(), pool, NOW)
    elapsed = time.time() - t

    assert elapsed < 30                          # 量级检查
    assert len(pool.snapshot()) == 50            # 池非空
    assert report.data_max_ts == rows[-1][5]     # max ts = 流末尾
    assert report.watermark == rows[-1][0]       # 水位 = 回放窗最大行 ID（F8 增量流起点）
    assert calls["n"] == 50_000                  # 同一状态机：全部走 pool.ingest

    # bootstrap SQL 纪律：只读 + 时间窗双界 + ID 排序 + 参数 = (now−120h, now)
    sql, params = cur.queries[0]
    assert sql.lstrip().upper().startswith("SELECT")
    assert '"DATE_EVT" >= %s' in sql and '"DATE_EVT" <= %s' in sql
    assert 'ORDER BY "ID"' in sql
    assert sql.count("%s") == len(params) == 2   # 占位符数 = 参数数
    assert params[0] == NOW - timedelta(hours=120) and params[1] == NOW


# ---------- Step 2：cross_check 差集数字 + >1% 门槛 ----------

def test_cross_check():
    pool = VehiclePool()
    t0 = NOW - timedelta(hours=5)
    for body, station in (("A", "10IS255"), ("B", "14IS015"), ("C", "01IS205")):
        pool.ingest(E(f"78202A{body}01", station, "0", t0))
    # 参考集 = {A, B, D} → only_in_pool={C} only_in_ref={D} → diff=2, base=3
    cur = FakeCursor([[ (f"78202A{b}01",) for b in ("A", "B", "D") ]])
    diff, pct = cross_check(pool, FakeConn(cur), hours=120, now=NOW)
    assert diff == 2 and abs(pct - 2 / 3) < 1e-9
    assert pct > 0.01                            # >1% DoD 门槛（启动报告出 warning）

    # 零差
    cur2 = FakeCursor([[ (f"78202A{b}01",) for b in ("A", "B", "C") ]])
    assert cross_check(pool, FakeConn(cur2), hours=120, now=NOW) == (0, 0.0)

    # SQL 纪律：参考表 + 窗双界（B1 上界，2026-09-24 agy 审核中①）+ 9 站排除（4 出口 + 5 延续站）
    sql, params = cur.queries[0]
    assert "dim.dim_vehicle_profile" in sql
    assert "carbody_last_seen_at >= %s" in sql
    assert "carbody_last_seen_at <= %s" in sql   # 上界（replay_cursor 模式防未来污染）
    assert "NOT IN" in sql
    assert len(params) == 11                     # since + now + 9 站
    assert sql.count("%s") == len(params)        # 占位符数 = 参数数（2026-09-24 真库 IndexError 回归）
    assert params[0] == NOW - timedelta(hours=120) and params[1] == NOW


# ---------- Step 3：BootstrapReport 全字段 ----------

def test_bootstrap_report_fields():
    # 6 行 3 车：A(10IS255,flag=1) B(14IS015,flag=0) C(01IS205,flag=-)；参考集只 {A,B} → diff=1
    rows = [
        (11, "78202A1111", "2N54Y-", "10IS255", "1", NOW - timedelta(hours=2)),
        (12, "78202A1111", "2N54Y-", "10IS255", "1", NOW - timedelta(hours=1)),
        (13, "78202A2222", "2N54Y-", "14IS015", "0", NOW - timedelta(hours=2)),
        (14, "78202A2222", "2N54Y-", "14IS015", "0", NOW - timedelta(hours=1)),
        (15, "78202A3333", "2N54Y-", "01IS205", "-", NOW - timedelta(hours=2)),
        (16, "78202A3333", "2N54Y-", "01IS205", "-", NOW - timedelta(minutes=30)),
    ]
    cur = FakeCursor([rows, [("78202A1111",), ("78202A2222",)]])
    report = bootstrap(FakeConn(cur), cfg(), VehiclePool(), NOW)

    assert report.pool_size == 3
    # C 车 flag='-' 无效 → 收养默认 "0"（T2.1 R2 冻结语义：新车无效 flag 不复位、默认 0 + invalid 审计）
    assert report.flag_dist == {"1": 1, "0": 2}
    assert report.seg_dist == {"10": 1, "14": 1, "01": 1}   # 站前缀粗分
    assert report.data_max_ts == NOW - timedelta(minutes=30)
    assert report.watermark == 16
    assert report.config_echo["stale_min"] == 30            # 全部生效值
    assert report.config_echo["bootstrap_hours"] == 120
    assert report.cross_check_diff == 1
    assert abs(report.cross_check_pct - 1 / 3) < 1e-9


# ---------- Step 5：启动序列顺序（docs/03 T2.4 Step 5 定版） ----------

def _load_boot_module():
    import importlib.util
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "spike_w3", "01_boot_online.py")
    spec = importlib.util.spec_from_file_location("w3_boot", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["w3_boot"] = mod          # 注册（spec_from_file_location 不自动注册）
    spec.loader.exec_module(mod)
    return mod


def test_boot_sequence_order(monkeypatch):
    import sys as _sys
    mod = _load_boot_module() if "w3_boot" not in _sys.modules else _sys.modules["w3_boot"]
    calls = []
    fake_report = mod.BootstrapReport(
        pool_size=5, flag_dist={"0": 5}, seg_dist={"10": 5}, data_max_ts=NOW,
        watermark=999, config_echo={"stale_min": 30}, cross_check_diff=0, cross_check_pct=0.0)

    monkeypatch.setattr(mod, "load_week_plan", lambda cfg: calls.append("plan") or None)
    monkeypatch.setattr(mod, "check_dim_tables",
                        lambda conn: calls.append("dim") or {t: True for t in mod.DSS_DIM_TABLES})
    monkeypatch.setattr(mod, "derive_postexit_stations",
                        lambda conn: calls.append("derive") or frozenset(mod.POST_EXIT_STATIONS))
    monkeypatch.setattr(mod, "bootstrap",
                        lambda conn, cfg, pool, now: calls.append("bootstrap") or fake_report)

    class FakeGuard:
        def __init__(self, cfg):
            calls.append("guard_init")
            self.status, self.label, self.is_anomaly = "STALE", "数据陈旧", True

        def check(self, now, ts, expected_silence=False):
            calls.append("b3_check")
            return True

    monkeypatch.setattr(mod, "FreshnessGuard", FakeGuard)
    br = mod.boot_sequence(FakeConn(FakeCursor([])), cfg(), NOW)
    assert calls == ["plan", "dim", "derive", "bootstrap", "guard_init", "b3_check"]
    assert br.frozen is True and br.guard_label == "数据陈旧"
    assert "derive 一致" in br.postexit_topo and br.warning == ""

    # watermark=0（窗内无数据）→ 中止（F8：增量流不得从 0 起步）
    import dataclasses
    monkeypatch.setattr(mod, "bootstrap",
                        lambda conn, cfg, pool, now: dataclasses.replace(fake_report, watermark=0))
    try:
        import pytest
        with pytest.raises(RuntimeError):
            mod.boot_sequence(FakeConn(FakeCursor([])), cfg(), NOW)
    finally:
        _sys.modules.pop("w3_boot", None)
