# -*- coding: utf-8 -*-
"""T2.6 主循环（docs/03 §7）：replay_cursor 游标步进干跑（合成事件流，全离线）。

场景（48h，5min 步进 = 576 轮）：
- 车 A：T0 起每 10min 过站 6 次（末次 T0+50min）后**停摆**（线不停、单车死）；
  dead_vehicle_hours=1.0（B4 点 7 压缩）→ T0+110min 首轮判死移出（池 4→3，只扫一次）。
- 车 B/C/D：全程每 10min 过站，但 [24h, 25h) 全局停流 1h →
  guard NORMAL→STALE（lag≥30min，冻结）→ 恢复后 2 连新鲜回 NORMAL（B3 闩锁）；
  STALE 期间 B/C/D 超阈值也不得被扫（frozen 冻结语义，池保持 3）。
- 状态行：每轮含 池大小/max_ts/guard 状态（DoD）；max_ts 单调不减；
  死车审计 INSERT 进 dss.work_alert_log（只写 dss）。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from spike_w3.common import DSSConfig
from spike_w3.stream_pipeline import Event, EventStream, FreshnessGuard, VehiclePool

TZ = ZoneInfo("Asia/Shanghai")
T0 = datetime(2026, 9, 7, 0, 0, tzinfo=TZ)
STATIONS = ("10IS255", "12IS015", "14IS015", "19IS045")   # 全中线（非入口/出口/延续）
GAP_START, GAP_END = timedelta(hours=24), timedelta(hours=25)
END = timedelta(hours=48)


def _build_rows():
    """合成事件表（id 严格 ts 升序 → 无迟到路径，纯顺序消费）。

    返回 [(id, body_id, body_type, station, flag, ts)]。
    """
    rows = []
    rid = 0
    # 车 A：T0+10k min（k=0..5）后停摆
    for k in range(6):
        ts = T0 + timedelta(minutes=10 * k)
        rows.append((rid, "78202TAAA1", "78202A-", STATIONS[k % 4], "0", ts))
        rid += 1
    # 车 B/C/D：每 10min，跳过 [24h, 25h) 全局停流
    for body, off in (("78202TBBB1", 0), ("78202TCCC1", 1), ("78202TDDD1", 2)):
        k = 0
        while timedelta(minutes=10 * k) < END:
            dt = timedelta(minutes=10 * k)
            if GAP_START <= dt < GAP_END:
                k += 1
                continue
            rows.append((rid, body, "78202A-", STATIONS[(off + k) % 4], "0", T0 + dt))
            rid += 1
            k += 1
    rows.sort(key=lambda r: (r[5], r[0]))   # ts 升序（同 ts 按 id）
    return [(i, r[1], r[2], r[3], r[4], r[5]) for i, r in enumerate(rows, 1)]  # id 从 1 起（wm=0 起步）


class FakeStoreCursor:
    """按 WHERE 真实过滤的合成 DB 游标（consume / bootstrap SQL 生效；其余只读/审计 SQL 空结果）。"""

    def __init__(self, rows):
        self._rows = rows
        self.queries = []
        self._out = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if params is not None and len(params) == 2 and '"ID" > %s' in sql and '"DATE_EVT" <= %s' in sql:
            wm, now = params
            self._out = [r for r in self._rows if r[0] > wm and r[5] <= now]
        elif params is not None and len(params) == 2 and '"DATE_EVT" >= %s' in sql and '"DATE_EVT" <= %s' in sql:
            since, now = params
            self._out = [r for r in self._rows if since <= r[5] <= now]
        else:
            self._out = []
        return self

    def fetchall(self):
        return self._out


class FakeConn:
    def __init__(self, rows):
        self._cur = FakeStoreCursor(rows)
        self.commits = 0

    def cursor(self):
        return self._cur

    def commit(self):
        self.commits += 1


def _load_boot_module():
    import importlib.util
    import os
    import sys as _sys
    if "w3_boot" in _sys.modules:
        return _sys.modules["w3_boot"]
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "spike_w3", "01_boot_online.py")
    spec = importlib.util.spec_from_file_location("w3_boot", p)
    mod = importlib.util.module_from_spec(spec)
    _sys.modules["w3_boot"] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------- Step 2：test_main_loop_replay_mode（docs/03 §7 Step 2 DoD） ----------

def test_main_loop_replay_mode():
    mod = _load_boot_module()
    cfg = DSSConfig()                      # 默认值：stale_min=30, poll_interval_sec=300
    rows = _build_rows()
    conn = FakeConn(rows)
    pool = VehiclePool(dead_vehicle_hours=1.0)      # 阈值压缩（B4 点 7）
    stream = EventStream(conn, wm=0, skew_window_min=15)
    guard = FreshnessGuard(cfg)

    status = mod.run_replay(pool, stream, guard, conn, cfg,
                            start=T0, end=T0 + END, step_sec=300)

    # 576 轮（48h / 5min），无异常
    assert len(status) == 576
    # 状态行字段（DoD：每轮含 池大小 / max_ts / guard 状态）
    for st in status:
        assert {"now", "pool_size", "max_ts", "guard_state", "frozen", "dead_count"} <= set(st)
    # max_ts 单调不减
    assert all(a["max_ts"] <= b["max_ts"] for a, b in zip(status, status[1:]))

    # —— 单车死车（A）：T0+110min 首轮判死、只扫一次、此后不再回池 ——
    dead_idx = [i for i, st in enumerate(status) if st["dead_count"] > 0]
    assert dead_idx == [22]                          # 22 × 5min = T0+110min（50min 末事件 + 60min 阈值）
    assert status[22]["frozen"] is False             # 线不停（B/C/D 新鲜）→ 非冻结轮扫出
    assert "78202TAAA1" not in pool.snapshot()
    assert all(st["pool_size"] == 3 for st in status[23:])   # 死车不回池（A 无新事件）

    # —— 全局停流 [24h, 25h)：STALE 冻结 → 恢复 2 连新鲜回 NORMAL ——
    stale_rounds = [i for i, st in enumerate(status) if st["guard_state"] == "STALE"]
    # STALE 窗口 ⊂ [24h:30m, 25h) 之间（lag≥30min 起，恢复前）
    assert stale_rounds and all(288 < i < 300 for i in stale_rounds)
    # STALE 轮 frozen=True 且池保持 3（B/C/D 超阈值也不得被扫——冻结语义）
    assert all(status[i]["frozen"] is True and status[i]["pool_size"] == 3 for i in stale_rounds)
    # 恢复轮（25h:00 RECOVERY → 25h:05 NORMAL）
    assert status[300]["guard_state"] == "RECOVERY"
    assert status[301]["guard_state"] == "NORMAL" and status[301]["frozen"] is False
    assert status[-1]["guard_state"] == "NORMAL"

    # —— 终态：B/C/D 在池，站 = 末事件站 ——
    snap = pool.snapshot()
    assert set(snap) == {"78202TBBB1", "78202TCCC1", "78202TDDD1"}

    # —— 死车审计：INSERT 进 dss.work_alert_log（只写 dss）+ commit ——
    sqls = [q[0] for q in conn._cur.queries]
    assert any("INSERT INTO dss.work_alert_log" in s for s in sqls)
    assert conn.commits >= 1


# ---------- Step 2b：F-1 丢行自愈——VehiclePool.reset + run_replay 周期重建（2026-09-25 真库踩坑） ----------

def test_vehicle_pool_reset():
    """reset() = 全量重建归零（状态纯由事件流重建，B4 点③）；阈值参数保留。"""
    p = VehiclePool(dead_vehicle_hours=1.0)
    p.ingest(Event("78202TZ001", "78202A-", "10IS255", "0", T0, row_id=1))
    p.ingest(Event("78202TZ001", "78202A-", "K3IS140", "0", T0 + timedelta(minutes=10), row_id=2))  # 出站
    assert p.audit["exits"].get("78202TZ001") == 1
    p.reset()
    assert p.snapshot() == {}
    assert p.audit["exits"] == {} and p.audit["late_out_of_window"] == 0
    assert p.dead_vehicle_hours == 1.0          # 阈值保留（重建不换实例）


def _poison_rows():
    """B/C 正常车（每 10min × 4h，ID 1..48 ts 升序）+ 中毒行（ID 1000、ts=T0）：
    ETL 乱序写——事件 ts 很早但 ID 极大；round 1 消费它后 wm 跳到 1000，
    后续所有行 ID ≤ 48 < 1000 → 永不可拉（F-1 wm 中毒，真库 09-13 实测形态）。"""
    rows = []
    rid = 0
    for k in range(24):  # k=0..23 → T0..T0+230min
        for body, off in (("78202TBBB1", 0), ("78202TCCC1", 1)):
            rid += 1
            rows.append((rid, body, "78202A-", STATIONS[(off + k) % 4], "0",
                         T0 + timedelta(minutes=10 * k)))
    rows.append((1000, "78202TP001", "78202A-", STATIONS[0], "0", T0))
    return rows


def test_run_replay_rebuild_recovers_wm_poison():
    """wm 中毒 → 周期重建自愈（F-1 修复，2026-09-25）：
    rebuild_hours=2（压缩）→ T0+2h 全窗重建 → 池恢复；回放终态 = 终点纯 bootstrap 真值；
    对照无重建 → 池冻结在 round 1（丢失不可自愈）。
    """
    mod = _load_boot_module()
    cfg = DSSConfig()
    rows = _poison_rows()

    # —— 有重建（rebuild_hours=2）——
    conn = FakeConn(rows)
    pool = VehiclePool(dead_vehicle_hours=1.0)
    stream = EventStream(conn, wm=0, skew_window_min=15)
    guard = FreshnessGuard(cfg)
    status = mod.run_replay(pool, stream, guard, conn, cfg,
                            start=T0, end=T0 + timedelta(hours=4), step_sec=300,
                            rebuild_hours=2.0)
    assert len(status) == 48                     # 4h / 5min
    # 中毒后数据断流 → B3 护栏抓出 STALE（lag ≥ 30min，round 6 起）
    stale = [i for i, st in enumerate(status) if st["guard_state"] == "STALE"]
    assert stale and stale[0] == 6
    # 终态 = 终点纯 bootstrap 真值：B/C 末位 = k=23 事件；中毒车 P 在重建窗内被收养
    snap = pool.snapshot()
    assert snap["78202TBBB1"].station == STATIONS[3]   # (0+23)%4
    assert snap["78202TCCC1"].station == STATIONS[0]   # (1+23)%4
    assert "78202TP001" in snap

    # —— 对照：无重建 → 池冻结在 round 1（B 停在 k=0 站）——
    conn2 = FakeConn(rows)
    pool2 = VehiclePool(dead_vehicle_hours=1.0)
    stream2 = EventStream(conn2, wm=0, skew_window_min=15)
    mod.run_replay(pool2, stream2, FreshnessGuard(cfg), conn2, cfg,
                   start=T0, end=T0 + timedelta(hours=4), step_sec=300)
    assert pool2.snapshot()["78202TBBB1"].station == STATIONS[0]   # 冻结（中毒丢失）


# ---------- Step 2c：agy T2.6 审核修复（中等① main 回放分支锚点 / 低① _rebuild 影子池） ----------

def test_main_replay_branch_anchors_boot_to_start(monkeypatch):
    """agy T2.6 审核中等①：replay_cursor 模式下 main() 的 bootstrap 必须锚定回放起点
    start（水位 = 回放窗最大 ID）——锚定墙钟会使水位置顶全库当前最大 ID，回放消费全程空转。"""
    mod = _load_boot_module()
    rec = {}
    start_s, end_s = "2026-09-07T00:00", "2026-09-14T00:00"
    monkeypatch.setenv("DSS_REPLAY_START", start_s)
    monkeypatch.setenv("DSS_REPLAY_END", end_s)
    monkeypatch.delenv("DSS_PLAN_CSV", raising=False)

    class FakeConn:
        def close(self):
            pass

    class FakeES:
        def __init__(self, conn, wm, skew_window_min=15):
            self.wm = wm
            self.front = None

    def fake_boot(conn_, cfg_, now_, expected_silence=False, pool=None):
        rec["boot_now"] = now_
        return type("BR", (), {"bootstrap": type("BS", (), {"watermark": 5, "data_max_ts": T0})})()

    def fake_replay(pool_, stream_, guard_, conn_, cfg_, start=None, end=None, **kw):
        rec["replay"] = (start, end)
        return []

    monkeypatch.setattr(mod, "connect", lambda: FakeConn())
    monkeypatch.setattr(mod, "load_config", lambda: DSSConfig(clock_mode="replay_cursor"))
    monkeypatch.setattr(mod, "boot_sequence", fake_boot)
    monkeypatch.setattr(mod, "render_boot_report", lambda br: "ok")
    monkeypatch.setattr(mod, "EventStream", FakeES)
    monkeypatch.setattr(mod, "run_replay", fake_replay)
    mod.main()
    want = datetime.fromisoformat(start_s).astimezone()
    assert rec["boot_now"] == want          # 中等①：bootstrap 锚点 = 回放起点（非墙钟）
    assert rec["replay"] == (want, datetime.fromisoformat(end_s).astimezone())


def test_rebuild_shadow_pool_keeps_old_on_failure(monkeypatch):
    """agy T2.6 审核低①：_rebuild 先建影子池再原子置换——bootstrap 中途失败时保留旧池并抛异常，
    不留空池；成功时原子置换 + 水位只升不降 + front 对齐。"""
    mod = _load_boot_module()
    cfg = DSSConfig()
    pool = VehiclePool(dead_vehicle_hours=1.0)
    pool.ingest(Event("78202TAAA1", "78202A-", STATIONS[0], "0", T0, row_id=1))

    class FakeStream:
        def __init__(self):
            self.wm = 0
            self.front = None

        def set_watermark(self, w):
            if w > self.wm:
                self.wm = w

    stream = FakeStream()
    monkeypatch.setattr(mod, "bootstrap", lambda c, cf, p, n: (_ for _ in ()).throw(RuntimeError("DB down")))
    with pytest.raises(RuntimeError):
        mod._rebuild(None, cfg, pool, stream, T0)
    # 故障保旧：旧池状态原样保留（未 reset）
    assert pool.snapshot()["78202TAAA1"].station == STATIONS[0]
    # 成功路径：影子池建成后原子置换 + 水位只升不降 + front 对齐
    class R:
        watermark = 100
        data_max_ts = T0 + timedelta(hours=5)

    monkeypatch.setattr(mod, "bootstrap", lambda c, cf, p, n: R())
    mod._rebuild(None, cfg, pool, stream, T0)
    assert pool.snapshot() == {}            # 影子池（本 fake 未喂入）已置换为主池
    assert stream.wm == 100
    assert stream.front == T0 + timedelta(hours=5)
