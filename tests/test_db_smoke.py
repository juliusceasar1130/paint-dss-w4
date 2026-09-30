# -*- coding: utf-8 -*-
"""DB 集成冒烟：真实 DB 只读。钉死 tz / 引号 / 口径常量（数据为存量，精确值可断言）。
快照钉 2026-09-30 11:36 数据同步（全表 6,966,535 行）；数据再同步后需刷新为本条实测值。
DB 不可达时自动 skip（公网环境 DB 正常，正常路径不应触发）。"""
import socket
from datetime import datetime, timedelta, timezone

import pytest

import spike_w1.common as common


def _db_up() -> bool:
    try:
        socket.create_connection(("127.0.0.1", 5432), timeout=2).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="analytics_db 不可达")

CST = timezone(timedelta(hours=8))


def test_tz_and_window_bounds():
    conn = common.connect()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT min("DATE_EVT") FROM ods.carbody_history')
            lo = cur.fetchone()[0]
    finally:
        conn.close()
    assert lo == datetime(2026, 4, 14, 16, 36, 51, 210000, tzinfo=CST)


def test_flag_distribution_product_vehicles():
    conn = common.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT SUBSTRING("MDS_DATA" FROM 139 FOR 1), count(*) '
                'FROM ods.carbody_history WHERE "BODY_ID" LIKE \'78202%\' '
                'GROUP BY 1')
            got = dict(cur.fetchall())
    finally:
        conn.close()
    assert got == {"0": 5300200, "1": 459191, "2": 202703, "-": 4602, "3": 2}  # 2026-09-30 11:36 同步后存量快照（09-30 实测刷新；上一版 09-24 16:32）


def test_entry_exit_station_counts():
    conn = common.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT "RW_STATION_ID", count(*) FROM ods.carbody_history '
                'WHERE "BODY_ID" LIKE \'78202%%\' '   # 带参执行：% 必须 %% 转义
                '  AND "RW_STATION_ID" IN (%s, %s, %s, %s, %s, %s, %s) '
                'GROUP BY 1',
                common.ENTRY_STATIONS + common.EXIT_STATIONS)
            got = dict(cur.fetchall())
    finally:
        conn.close()
    assert got["01IS205"] >= 68000
    assert got["K3IS140"] >= 64000
    assert set(common.ENTRY_STATIONS + common.EXIT_STATIONS) <= set(got)
