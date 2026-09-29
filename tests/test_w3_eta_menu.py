# -*- coding: utf-8 -*-
"""T6.3（docs/07 v1.2 §2.3）：工位 ETA 菜单（v1 = 仅 4 下线点；独立连库只读）。

口径钉死（v1.2 + r2 F-R2-01）：
- 已出站判定 = 库内最近事件 ∈ EXIT_STATIONS ∪ POST_EXIT_STATIONS（或历史已有下线点事件）。
- 残差 n≥30 纪律：车型行 n≥30 优先；否则 ALL；双缺/n<30 → 明确报错不估。
- 目标 ∉ 4 下线点 → 明确报错「中间站 ETA v1 未开放（Phase 3：takt 图最短路）」。
- 返修车（flag 1/2）= 环内站残差直查 + 标注 + dim_loop_remaining 参考（可缺省）。
- 非产品车 / 车不存在 → 明确报错（非产品车零库查询）。
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from spike_w3.dashboard import compute_eta, main as eta_main
from spike_w3.common import EXIT_STATIONS, POST_EXIT_STATIONS

TZ = ZoneInfo("Asia/Shanghai")
ASOF = datetime(2026, 9, 9, 5, 0, tzinfo=TZ)
EXIT1 = "K3IS140"
POST1 = "09IS360"


class FakeEtaCursor:
    def __init__(self, sc):
        self.sc = sc
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if params is not None and '"BODY_ID" = %s AND "DATE_EVT" <= %s' in sql:
            self._out = [self.sc["latest"]] if self.sc.get("latest") else []
        elif "MAX(\"DATE_EVT\")" in sql:
            self._out = [(self.sc.get("exit_ts"),)]
        elif "FROM dss.dim_remaining_time" in sql:
            self._out = self.sc.get("rem", [])
        elif "FROM dss.dim_loop_remaining" in sql:
            self._out = self.sc.get("loop", [])
        else:
            self._out = []
        return self

    def fetchall(self):
        return self._out


class FakeEtaConn:
    def __init__(self, sc):
        self.sc = sc
        self._cur = FakeEtaCursor(sc)

    def cursor(self):
        return self._cur


def _sc(**kw):
    base = {"latest": (datetime(2026, 9, 9, 4, 30, tzinfo=TZ), "10IS255",
                       "78202A", "0"),
            "rem": [("78202A", 5000.0, 120), ("ALL", 5200.0, 3000)]}
    base.update(kw)
    return base


# ---------------------------------------------------------------------------

def test_eta_ok_body_type_preferred():
    r = compute_eta(FakeEtaConn(_sc()), "78202T1", EXIT1, ASOF)
    assert r["status"] == "eta"
    assert r["eta_sec"] == 5000.0 and r["source"] == "78202A" and r["n"] == 120
    assert r["station_now"] == "10IS255" and r["flag"] == "0"
    assert r["eta_label"] == "1h 23m 20s"
    assert r["in_loop"] is False


def test_eta_fallback_all():
    r = compute_eta(FakeEtaConn(_sc(rem=[("ALL", 5200.0, 3000)])),
                    "78202T1", EXIT1, ASOF)
    assert r["source"] == "ALL" and r["eta_sec"] == 5200.0


def test_eta_low_n_fallback():
    """车型 n=20 < 30 → ALL（n≥30 纪律）。"""
    r = compute_eta(FakeEtaConn(_sc(rem=[("78202A", 5000.0, 20),
                                         ("ALL", 5200.0, 3000)])),
                    "78202T1", EXIT1, ASOF)
    assert r["source"] == "ALL"


def test_eta_no_rows_error():
    r = compute_eta(FakeEtaConn(_sc(rem=[])), "78202T1", EXIT1, ASOF)
    assert r["status"] == "error" and "残差样本不足" in r["error"]


def test_eta_all_low_n_error():
    r = compute_eta(FakeEtaConn(_sc(rem=[("ALL", 5000.0, 20)])),
                    "78202T1", EXIT1, ASOF)
    assert r["status"] == "error" and "n<30" in r["error"]


def test_eta_intermediate_station_error():
    """目标 ∉ 4 下线点 → 明确报错不猜（v1 范围收缩，F-BLK-01 整改）。"""
    r = compute_eta(FakeEtaConn(_sc()), "78202T1", "12IS015", ASOF)
    assert r["status"] == "error"
    assert "中间站 ETA v1 未开放" in r["error"] and "Phase 3" in r["error"]


def test_eta_exited_at_exit_station():
    sc = _sc(latest=(datetime(2026, 9, 9, 3, 0, tzinfo=TZ), EXIT1, "78202A", "0"))
    r = compute_eta(FakeEtaConn(sc), "78202T1", EXIT1, ASOF)
    assert r["status"] == "exited"
    assert r["exit_ts"] == datetime(2026, 9, 9, 3, 0, tzinfo=TZ).isoformat()


def test_eta_exited_at_post_exit_station():
    """最新事件 = 出站延续站（r2 实测 76% 出站车形态）→ 已出站，出口时刻 = 下线点 MAX。"""
    sc = _sc(latest=(datetime(2026, 9, 9, 3, 5, tzinfo=TZ), POST1, "78202A", "0"),
             exit_ts=datetime(2026, 9, 9, 3, 0, tzinfo=TZ))
    r = compute_eta(FakeEtaConn(sc), "78202T1", EXIT1, ASOF)
    assert r["status"] == "exited"
    assert r["exit_ts"] == datetime(2026, 9, 9, 3, 0, tzinfo=TZ).isoformat()


def test_eta_exited_any_of_four_exits():
    """任一 4 下线点皆算已出站（目标站无关）。"""
    for e in EXIT_STATIONS:
        sc = _sc(latest=(ASOF, e, "78202A", "0"))
        r = compute_eta(FakeEtaConn(sc), "78202T1", EXIT1, ASOF)
        assert r["status"] == "exited", e
    for p in POST_EXIT_STATIONS:
        sc = _sc(latest=(ASOF, p, "78202A", "0"), exit_ts=ASOF)
        r = compute_eta(FakeEtaConn(sc), "78202T1", EXIT1, ASOF)
        assert r["status"] == "exited", p


def test_eta_rework_annotation_with_loop_ref():
    sc = _sc(latest=(ASOF, "12IS575", "2N54Y-", "1"),
             rem=[("2N54Y-", 4000.0, 200), ("ALL", 4100.0, 1000)],
             loop=[(30967.0, 271)])
    r = compute_eta(FakeEtaConn(sc), "78202T2", EXIT1, ASOF)
    assert r["status"] == "eta" and r["in_loop"] is True
    assert "返修" in r["loop_note"]
    assert r["loop_back_sec"] == 30967.0


def test_eta_rework_no_loop_ref():
    """loop 参考行缺失 → 标注保留、参考值 None（可缺则省）。"""
    sc = _sc(latest=(ASOF, "12IS575", "2N54Y-", "2"),
             rem=[("ALL", 4100.0, 1000)], loop=[])
    r = compute_eta(FakeEtaConn(sc), "78202T3", EXIT1, ASOF)
    assert r["in_loop"] is True and r["loop_back_sec"] is None
    assert "返修" in r["loop_note"]


def test_eta_not_found():
    r = compute_eta(FakeEtaConn(_sc(latest=None)), "78202T9", EXIT1, ASOF)
    assert r["status"] == "error" and ("不存在" in r["error"] or "无事件" in r["error"])


def test_eta_not_product_zero_queries():
    conn = FakeEtaConn(_sc())
    r = compute_eta(conn, "ABC123", EXIT1, ASOF)
    assert r["status"] == "error" and "非产品车" in r["error"]
    assert conn._cur.queries == []          # 零库查询


def test_eta_naive_rejected():
    with pytest.raises(TypeError):
        compute_eta(FakeEtaConn(_sc()), "78202T1", EXIT1,
                    datetime(2026, 9, 9, 5, 0))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def test_cli_main_eta_success(capsys):
    sc = _sc()
    rc = eta_main(["--eta", "78202T1", EXIT1,
                   "--as-of", ASOF.isoformat()], _connect=lambda: FakeEtaConn(sc))
    assert rc == 0
    out = capsys.readouterr().out
    assert "78202T1" in out and "1h 23m 20s" in out


def test_cli_main_eta_error(capsys):
    sc = _sc(latest=(ASOF, POST1, "78202A", "0"), exit_ts=ASOF)
    rc = eta_main(["--eta", "78202T1", EXIT1,
                   "--as-of", ASOF.isoformat()], _connect=lambda: FakeEtaConn(sc))
    assert rc == 0                          # exited 也是正常答复
    out = capsys.readouterr().out
    assert "出站" in out


def test_cli_main_error_exit2(capsys):
    rc = eta_main(["--eta", "ABC123", EXIT1,
                   "--as-of", ASOF.isoformat()], _connect=lambda: FakeEtaConn(_sc()))
    assert rc == 2
    assert "非产品车" in capsys.readouterr().out


def test_cli_requires_eta_arg(capsys):
    rc = eta_main([], _connect=lambda: FakeEtaConn(_sc()))
    assert rc == 2
