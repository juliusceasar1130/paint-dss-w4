# -*- coding: utf-8 -*-
"""T2.3 B3 新鲜度护栏：FreshnessGuard（三态机 + 30min latch + 四信号诊断 + 休息日降级）。

全离线：check() 纯计算（注入 now / max_event_ts）；diagnose() 走 FakeCursor（5 批预置行）。
STALE 冻结语义（B3 定稿，DoD 注释项）：冻结对象 = 告警 + 死车 sweep，不冻结池消费/计算。
断流判定口径（BM 2026-09-22）：事件停 + ETL 停 = 断流；事件停 + ETL 正常 = 移交模式 A。
"""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from spike_w3.common import DSSConfig
from spike_w3.stream_pipeline import FreshnessGuard

TZ = ZoneInfo("Asia/Shanghai")
MIN = timedelta(minutes=1)
NOW = datetime(2026, 9, 24, 23, 0, tzinfo=TZ)


def cfg():
    return DSSConfig()  # stale_min=30 / lag_min=15 默认值


# ---------- 测试替身（同 test_w3_stream 模式：每 execute 弹一批） ----------

class FakeCursor:
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


# ---------- Step 1：三态机 + latch + log ----------

def test_freshness_normal_to_stale():
    g = FreshnessGuard(cfg())
    assert g.status == "NORMAL"

    # lag 29min（< 30 阈值）→ NORMAL，不冻结
    assert g.check(NOW, NOW - 29 * MIN) is False
    assert g.status == "NORMAL"

    # now 推进 2min → lag 31min（≥ 30）→ STALE，冻结
    now1 = NOW + 2 * MIN
    assert g.check(now1, NOW - 29 * MIN) is True
    assert g.status == "STALE"

    # 恢复事件（max_event_ts = now）→ RECOVERY（过渡态，不冻结）
    now2 = now1 + 5 * MIN
    assert g.check(now2, now2) is False
    assert g.status == "RECOVERY"

    # 再次 check 仍新鲜 → NORMAL；log 记录三次转换
    now3 = now2 + 5 * MIN
    assert g.check(now3, now3) is False
    assert g.status == "NORMAL"
    transitions = [t for _, t in g.log]
    assert transitions == ["NORMAL->STALE", "STALE->RECOVERY", "RECOVERY->NORMAL"]


# ---------- Step 2：STALE 冻结语义 ----------

def test_stale_freezes():
    g = FreshnessGuard(cfg())
    assert g.check(NOW, NOW - 31 * MIN) is True          # STALE → 冻结
    assert g.check(NOW + MIN, NOW + MIN) is False        # RECOVERY → 不冻结
    assert g.check(NOW + 2 * MIN, NOW + 2 * MIN) is False  # NORMAL → 不冻结
    # STALE 中再遇陈旧 → 保持 STALE 继续冻结（不重复记 log）
    g2 = FreshnessGuard(cfg())
    g2.check(NOW, NOW - 31 * MIN)
    g2.check(NOW + MIN, NOW - 31 * MIN)
    assert g2.status == "STALE" and len(g2.log) == 1


# ---------- Step 3：休息日降级（P2） ----------

def test_rest_day_downgrade():
    # expected_silence=True：仍 STALE（告警冻结不变）但 label/异常性降级
    g1 = FreshnessGuard(cfg())
    assert g1.check(NOW, NOW - 2 * timedelta(hours=1), expected_silence=True) is True
    assert g1.status == "STALE"
    assert g1.label == "无数据属预期"
    assert g1.is_anomaly is False

    # expected_silence=False 同 lag → 正常陈旧异常
    g2 = FreshnessGuard(cfg())
    assert g2.check(NOW, NOW - 2 * timedelta(hours=1)) is True
    assert g2.status == "STALE"
    assert g2.label == "数据陈旧"
    assert g2.is_anomaly is True

    # NORMAL 态 label 一致
    g3 = FreshnessGuard(cfg())
    g3.check(NOW, NOW - MIN)
    assert g3.label == "数据新鲜" and g3.is_anomaly is False


# ---------- Step 4：四信号诊断（断流 vs 移交模式 A） ----------

def test_diagnose_four_signals():
    # 场景 A：事件停 + ETL 停 → 断流
    #   S1 失败 / S2 水位缺口 5 / S3 停 2h / S4 停 2h
    curA = FakeCursor([
        [(NOW - 5 * MIN, "failed")],                    # S1 sync_job_log
        [("100", NOW - timedelta(hours=1))],            # S2a refresh_watermark
        [(105,)],                                        # S2b 全表 max_id（gap=5）
        [(NOW - timedelta(hours=2),)],               # S3 max DATE_EVT
        [(NOW - timedelta(hours=2),)],               # S4 rb_position_data 最新
    ])
    d = FreshnessGuard(cfg()).diagnose(FakeConn(curA), NOW)
    assert d["event_fresh"] is False
    assert d["etl_healthy"] is False
    assert d["verdict"] == "断流"
    assert d["s2_watermark"]["gap"] == 5
    assert d["s3_lag_min"] >= 120

    # 场景 B：事件停 + ETL 正常 → 移交模式 A（真停线，不是链路问题）
    curB = FakeCursor([
        [(NOW - 2 * MIN, "success")],                   # S1 成功且新鲜
        [("100", NOW - MIN)],                           # S2a
        [(100,)],                                        # S2b gap=0
        [(NOW - timedelta(hours=2),)],               # S3 事件停 2h
        [(NOW - 3 * MIN,)],                          # S4 独立快照新鲜
    ])
    d = FreshnessGuard(cfg()).diagnose(FakeConn(curB), NOW)
    assert d["event_fresh"] is False
    assert d["etl_healthy"] is True
    assert d["verdict"] == "移交模式A"

    # 场景 C：事件新鲜 → 新鲜（ETL 信号仅作诊断，不推翻事件层事实）
    curC = FakeCursor([
        [(NOW - 2 * MIN, "success")],
        [("100", NOW - MIN)],
        [(100,)],
        [(NOW - 5 * MIN,)],                           # S3 新鲜
        [(NOW - 3 * MIN,)],
    ])
    d = FreshnessGuard(cfg()).diagnose(FakeConn(curC), NOW)
    assert d["event_fresh"] is True
    assert d["verdict"] == "新鲜"


# ---------- SQL 纪律：只读 + 表名钉死 ----------

def test_diagnose_sql():
    cur = FakeCursor([[(NOW, "success")], [("1", NOW)], [(1,)], [(NOW,)], [(NOW,)]])
    FreshnessGuard(cfg()).diagnose(FakeConn(cur), NOW)
    sqls = [q[0] for q in cur.queries]
    assert len(sqls) == 5
    assert "meta.sync_job_log" in sqls[0]
    assert "meta.refresh_watermark" in sqls[1]
    assert "ods.carbody_history" in sqls[2] and "MAX(\"ID\")" in sqls[2]
    assert "MAX(\"DATE_EVT\")" in sqls[3]
    assert "ods.rb_position_data" in sqls[4]
    for s in sqls:
        assert s.lstrip().upper().startswith("SELECT")   # 只读


# ---------- 时区纪律（R7） ----------

def test_freshness_requires_aware():
    import pytest
    g = FreshnessGuard(cfg())
    naive = datetime(2026, 9, 24, 23, 0)
    with pytest.raises(TypeError):
        g.check(naive, naive)
    with pytest.raises(TypeError):
        g.diagnose(FakeConn(FakeCursor([])), naive)
