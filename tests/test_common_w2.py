# -*- coding: utf-8 -*-
"""spike_w2 基座冒烟：再导出完整性 + DB 连接 + dss 可读（W1 表在位）。"""
import pytest

from spike_w2 import common


def test_reexports():
    assert callable(common.connect)
    assert set(common.ENTRY_STATIONS) == {"01IS205", "1L360RB", "01IS045"}
    assert set(common.EXIT_STATIONS) == {"K3IS140", "1J440RB", "K2IS075", "K1IS135"}
    assert (common.FLAG_NORMAL, common.FLAG_REWORK, common.FLAG_BLACKTOP) == ("0", "1", "2")


def test_db_smoke():
    conn = common.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM dss.dim_remaining_time")
        assert cur.fetchone()[0] > 0          # W1 学习表在位（回归护栏：W2 只读它们）
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()
