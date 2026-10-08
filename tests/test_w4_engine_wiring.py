# -*- coding: utf-8 -*-
"""W4.3d 引擎接线单测（TDD RED；规格 = docs/15 W4.3d + §0.5 env 契约 + 双审 B10/B11/cc 阻断 3/agy 建议 2）。

- DSSConfig +2 字段（dual_ring / use_net_delay，严格 1/0，非法值启动报错）
- eta_v2：门严格 1/0 + estimate_event_net_delay retrofit（DUAL_RING=0 + ND=1：旧引擎 p×L → expected_delay）
- estimate_for_replay：DUAL_RING=0 分支吃 ND 门
- predictor.rem_of：DUAL_RING=1 → 统一引擎全状态机路径（mode='shift'、raw 139 + 137 位 + passed_oven、
  effective_flag=None；rem = base+delay 含 delay，α 只作用消费点单次 B10）；=0 → 旧路径（ND=1 → retrofit）
- 02_replay_shift._eta_pair：DUAL_RING=1 → (unified 净化直通, 净延误关闭基线 expected_delay 强制 0)
  （direct_nocomp 重定义）；=0 → (旧引擎 comp_excl 保持, 旧引擎不豁免旧语义)
- 02_shadow_replay 静态参照侧 2 调用点 = 同一分发（源码钉死）
- dashboard.compute_eta：DUAL_RING=1 → 137 位 + passed_oven 轨迹派生 + 统一引擎（mode='vehicle'）；
  =0 → 旧路径零漂移
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from spike_w2 import eta_v2
from spike_w3 import common, predictor
from spike_w3.stream_pipeline import VehicleState

TZ = ZoneInfo("Asia/Shanghai")
W1 = "2026-W34"
WEEK_NOW = "2026-W37"   # rem_of 测试 as-of = 2026-09-09 的 ISO 周（fixture week 必须对齐）
T0 = int(datetime(2026, 9, 9, 5, 0, tzinfo=TZ).timestamp())


def _tables(**kw):
    base = {"rem": {}, "loop_rem": {}, "loop_dur": {}, "takt": {},
            "fut_week": {}, "fut_all": {}, "cohort": {}, "lineprob": {},
            "quality": {}, "expected_delay": {}}
    base.update(kw)
    return base


# ---------- DSSConfig +2 字段（§0.5 严格 1/0） ----------

def test_config_defaults(monkeypatch):
    monkeypatch.delenv("DSS_DUAL_RING", raising=False)
    monkeypatch.delenv("DSS_USE_NET_DELAY", raising=False)
    cfg = common.load_config()
    assert cfg.dual_ring == 1 and cfg.use_net_delay == 1


def test_config_strict_01(monkeypatch):
    monkeypatch.setenv("DSS_DUAL_RING", "0")
    monkeypatch.setenv("DSS_USE_NET_DELAY", "0")
    cfg = common.load_config()
    assert cfg.dual_ring == 0 and cfg.use_net_delay == 0


def test_config_invalid_raises(monkeypatch):
    monkeypatch.delenv("DSS_USE_NET_DELAY", raising=False)
    for bad in ("2", "true", "on", "1.0"):
        monkeypatch.setenv("DSS_DUAL_RING", bad)
        with pytest.raises(ValueError):
            common.load_config()
    monkeypatch.setenv("DSS_DUAL_RING", "1")
    monkeypatch.setenv("DSS_USE_NET_DELAY", "x")
    with pytest.raises(ValueError):
        common.load_config()


# ---------- eta_v2 门：严格 1/0 ----------

def test_dual_ring_env_strict(monkeypatch):
    monkeypatch.delenv("DSS_DUAL_RING", raising=False)
    assert eta_v2.dual_ring_enabled() is True            # 缺省 = 1
    monkeypatch.setenv("DSS_DUAL_RING", "0")
    assert eta_v2.dual_ring_enabled() is False
    monkeypatch.setenv("DSS_DUAL_RING", "1")
    assert eta_v2.dual_ring_enabled() is True
    for bad in ("2", "true", "yes"):
        monkeypatch.setenv("DSS_DUAL_RING", bad)
        with pytest.raises(ValueError):
            eta_v2.dual_ring_enabled()


def test_use_net_delay_env_strict(monkeypatch):
    monkeypatch.delenv("DSS_USE_NET_DELAY", raising=False)
    assert eta_v2.use_net_delay_enabled() is True
    monkeypatch.setenv("DSS_USE_NET_DELAY", "0")
    assert eta_v2.use_net_delay_enabled() is False
    monkeypatch.setenv("DSS_USE_NET_DELAY", "2")
    with pytest.raises(ValueError):
        eta_v2.use_net_delay_enabled()


# ---------- estimate_event_net_delay retrofit（doc10 语义，仅 DUAL_RING=0 生效） ----------

ND_T = _tables(
    rem={("s", "m"): (500, 520, 600, 50)},
    loop_dur={("1", "m"): (3600, 4000, 40, 4000), ("2", "m"): (1800, 1900, 20, 1800)},
    fut_week={("s", "m", W1): (0.2, 0.1, 200)},
    expected_delay={("s", "m"): (9000, 7200, 50, 50)},
)


def test_nd_flag0_base_plus_delay_not_pL():
    # 旧引擎 = 500 + 0.2*3600 + 0.1*1800 = 1220；retrofit = 500 + 7200（p50 列，W4.4b 修订 2026-10-06）
    eta_old, _ = eta_v2.estimate_event(0, "s", "0", "m", W1, ND_T)
    assert eta_old == round(500 + 0.2 * 3600 + 0.1 * 1800)
    eta_nd, used = eta_v2.estimate_event_net_delay(0, "s", "0", "m", W1, ND_T)
    assert eta_nd == round(500 + 7200) and used is False


def test_nd_compexcl_kills_delay():
    eta_nd, _ = eta_v2.estimate_event_net_delay(0, "s", "0", "m", W1, ND_T,
                                                comp_excl=frozenset({("m", "s")}))
    assert eta_nd == 500                    # 豁免位与旧 comp 同门：delay 强制 0


def test_nd_missing_delay_zero():
    t2 = _tables(rem={("s", "m"): (500, 520, 600, 50)},
                 fut_week={("s", "m", W1): (0.9, 0.9, 200)},
                 loop_dur={("1", "m"): (3600, 4000, 40, 4000)})
    eta_nd, _ = eta_v2.estimate_event_net_delay(0, "s", "0", "m", W1, t2)
    assert eta_nd == 500                    # expected_delay 缺 → delay=0（旧引擎该值 = 500+0.9*3600）


def test_nd_flag12_unchanged():
    tt = _tables(loop_rem={("s", "1", "m"): (36000, 40000, 44000, 100)})
    e_old = eta_v2.estimate_event(0, "s", "1", "m", W1, tt)
    e_nd = eta_v2.estimate_event_net_delay(0, "s", "1", "m", W1, tt)
    assert e_old == e_nd == (36000, False)   # 环分布路径不动（不加 delay）


# ---------- estimate_for_replay 分发：DUAL_RING=0 分支吃 ND 门 ----------

def test_for_replay_gate0_nd(monkeypatch):
    monkeypatch.setenv("DSS_DUAL_RING", "0")
    # ND=1 → retrofit（500+7200，p50 列 W4.4b 修订）；ND=0 → 纯旧（1220）
    monkeypatch.setenv("DSS_USE_NET_DELAY", "1")
    e1, _ = eta_v2.estimate_for_replay(0, "s", "0", "m", W1, ND_T)
    assert e1 == round(500 + 7200)
    monkeypatch.setenv("DSS_USE_NET_DELAY", "0")
    e0, _ = eta_v2.estimate_for_replay(0, "s", "0", "m", W1, ND_T)
    assert e0 == round(500 + 0.2 * 3600 + 0.1 * 1800)
    # DUAL_RING=1 → 统一引擎净化直通（shift 模式：base=mean 列 520 + delay p50 列 7200，W4.4b 修订）
    monkeypatch.setenv("DSS_DUAL_RING", "1")
    e2, _ = eta_v2.estimate_for_replay(0, "s", "0", "m", W1, ND_T)
    assert e2 == round(520 + 7200)


# ---------- predictor.rem_of 接线 ----------

def _v(st, flag, f137="0", oven=False):
    ts = datetime(2026, 9, 9, 4, 0, tzinfo=TZ)
    return VehicleState(body_type="78202A-", station=st, flag=flag,
                        last_event_ts=ts, entry_ts=ts, exits_seen=0, loop_count=0,
                        passed_oven=oven, flag_137=f137)


def test_rem_of_dual1_full_statemachine(monkeypatch):
    # 分支 A：出炉后黑顶（137=1, 139=2, 白名单站）→ loop_rem shift 消费 mean 列（row[1]）
    monkeypatch.setenv("DSS_DUAL_RING", "1")
    t = _tables(loop_rem={("24IS185", "2", "78202A-"): (36000, 40000, 44000, 100)})
    v = _v("24IS185", "2", f137="1", oven=True)
    assert predictor.rem_of(v, datetime(2026, 9, 9, 5, 0, tzinfo=TZ), t, frozenset()) == 40000.0


def test_rem_of_dual1_branchb_base_plus_delay(monkeypatch):
    # 分支 B：主线事件 → base(rem mean 列，shift 模式) + delay_p50（W4.4b 修订）；rem 含 delay（α 消费点单次 B10）
    monkeypatch.setenv("DSS_DUAL_RING", "1")
    t = _tables(rem={("10IS255", "78202A-"): (9000, 9200, 9500, 100)},
                expected_delay={("10IS255", "78202A-"): (1800, 1200, 50, 50)})
    v = _v("10IS255", "0")
    assert predictor.rem_of(v, datetime(2026, 9, 9, 5, 0, tzinfo=TZ), t, frozenset()) == 9200.0 + 1200.0


def test_rem_of_dual1_exit_gateway_delay0(monkeypatch):
    # 37 出网关站：delay 强制 0（Doc14 车型干净基线）
    monkeypatch.setenv("DSS_DUAL_RING", "1")
    t = _tables(rem={("25IS010", "78202A-"): (3000, 3100, 3200, 100)},
                expected_delay={("25IS010", "78202A-"): (99999, 99999, 50, 50)})
    v = _v("25IS010", "0")
    assert predictor.rem_of(v, datetime(2026, 9, 9, 5, 0, tzinfo=TZ), t, frozenset()) == 3100.0


def test_rem_of_dual0_old_and_nd(monkeypatch):
    # DUAL_RING=0：旧引擎（flag 2 → loop_rem P50 row[0]）
    monkeypatch.setenv("DSS_DUAL_RING", "0")
    monkeypatch.delenv("DSS_USE_NET_DELAY", raising=False)
    t = _tables(loop_rem={("24IS185", "2", "78202A-"): (36000, 40000, 44000, 100)})
    v = _v("24IS185", "2", f137="1", oven=True)
    assert predictor.rem_of(v, datetime(2026, 9, 9, 5, 0, tzinfo=TZ), t, frozenset()) == 36000.0
    # ND=1 → retrofit：flag 0 → base + delay_p50（旧 p×L 被替换；W4.4b 修订）
    t2 = _tables(rem={("10IS255", "78202A-"): (9000, 9200, 9500, 100)},
                 fut_week={("10IS255", "78202A-", WEEK_NOW): (0.9, 0.9, 200)},
                 loop_dur={("1", "78202A-"): (72000, 72000, 72000, 100)},
                 expected_delay={("10IS255", "78202A-"): (1800, 1200, 50, 50)})
    v2 = _v("10IS255", "0")
    monkeypatch.setenv("DSS_USE_NET_DELAY", "1")
    assert predictor.rem_of(v2, datetime(2026, 9, 9, 5, 0, tzinfo=TZ), t2, frozenset()) == 9000.0 + 1200.0
    monkeypatch.setenv("DSS_USE_NET_DELAY", "0")
    # 纯旧：9000 + 0.9*72000 = 73800
    assert predictor.rem_of(v2, datetime(2026, 9, 9, 5, 0, tzinfo=TZ), t2, frozenset()) == 9000.0 + 0.9 * 72000


def test_rem_of_missing_distribution_none(monkeypatch):
    monkeypatch.setenv("DSS_DUAL_RING", "1")
    assert predictor.rem_of(_v("10IS255", "0"),
                            datetime(2026, 9, 9, 5, 0, tzinfo=TZ), _tables(), frozenset()) is None


# ---------- 02_replay_shift._eta_pair（direct_nocomp 重定义） ----------

def _w2m():
    import importlib.util
    import sys
    from pathlib import Path
    key = "w2_replay_wiring_test"
    if key in sys.modules:
        return sys.modules[key]
    spec = importlib.util.spec_from_file_location(
        key, Path(__file__).resolve().parent.parent / "spike_w2" / "02_replay_shift.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[key] = m
    spec.loader.exec_module(m)
    return m


def test_eta_pair_dual1(monkeypatch):
    monkeypatch.setenv("DSS_DUAL_RING", "1")
    m = _w2m()
    t = _tables(rem={("s", "m"): (500, 520, 600, 50)},
                expected_delay={("s", "m"): (9000, 7200, 50, 50)})
    eta_d, eta_d0, _used = m._eta_pair(0, "s", "0", "m", W1, t, frozenset())
    assert eta_d == round(520 + 7200)          # 统一引擎 shift（base=mean 列 + delay p50 列，净化 flag 直通；W4.4b 修订）
    assert eta_d0 == 520                        # 净延误关闭基线：expected_delay 强制 0
    # 无 expected_delay 行时两臂相等（对比恒平 → 度量表净效应为 0，语义成立）
    eta_d2, eta_d02, _ = m._eta_pair(0, "s", "0", "m", W1, _tables(rem={("s", "m"): (500, 520, 600, 50)}),
                                     frozenset())
    assert eta_d2 == eta_d02


def test_eta_pair_dual0(monkeypatch):
    monkeypatch.setenv("DSS_DUAL_RING", "0")
    monkeypatch.setenv("DSS_USE_NET_DELAY", "0")
    m = _w2m()
    t = _tables(rem={("s", "m"): (500, 520, 600, 50)},
                fut_week={("s", "m", W1): (0.2, 0.1, 200)},
                loop_dur={("1", "m"): (3600, 4000, 40, 4000), ("2", "m"): (1800, 1900, 20, 1800)})
    eta_d, eta_d0, _used = m._eta_pair(0, "s", "0", "m", W1, t, frozenset({("m", "s")}))
    assert eta_d == 500                          # 旧引擎 ⑥b 豁免语义保持
    assert eta_d0 == round(500 + 0.2 * 3600 + 0.1 * 1800)   # 旧引擎不豁免（旧 ⑥b 对照）


# ---------- 02_shadow_replay 静态参照侧同引擎分发（源码钉死） ----------

def test_shadow_static_side_same_dispatch():
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "spike_w3" / "02_shadow_replay.py").read_text(
        encoding="utf-8")
    n_dispatch = src.count("eta_v2.estimate_for_replay(")
    n_direct = src.count("eta_v2.estimate_event(")
    assert n_dispatch >= 2, "静态参照侧 2 处调用点须走共享分发（cc 阻断项 3）"
    assert n_direct == 0, "静态参照侧不得再直接调旧引擎（G1 同引擎门）"


# ---------- dashboard.compute_eta：DUAL_RING=1 统一路径 / =0 旧路径零漂移 ----------

def _fake_conn(latest, oven_cnt=0, rem=()):
    class Cur:
        def __init__(self):
            self.queries = []

        def execute(self, sql, params=None):
            self.queries.append((sql, params))
            if '"DATE_EVT" <= %s' in sql and 'LIMIT 1' in sql:
                self._out = [latest] if latest else []
            elif "COUNT(*)" in sql and "16IS305" in sql:
                self._out = [(oven_cnt,)]
            elif "COUNT(*)" in sql and "25IS010" in sql:
                self._out = [(0,)]      # W4.4c (α)：25IS 出环轨迹查询（缺省 0 = loop_exited False，旧语义）
            elif "MIN(" in sql and "01IS205" in sql:
                # W4.4h：入口事件班次归属查询（缺省 = 日班带内时刻；开关 off 时不参与计算）
                self._out = [(datetime(2026, 9, 9, 6, 0),)]
            elif "FROM dss.dim_remaining_time" in sql:
                self._out = list(rem)
            else:
                self._out = []
            return self

        def fetchall(self):
            return self._out

        def fetchone(self):
            return self._out[0] if self._out else None

    class C:
        def __init__(self):
            self._cur = Cur()

        def cursor(self):
            return self._cur

    return C()


def test_compute_eta_dual1_unified(monkeypatch):
    from spike_w3 import dashboard
    from spike_w3.predictor import PRODUCT_PREFIX
    import sys
    import types
    monkeypatch.setenv("DSS_DUAL_RING", "1")
    # 预注入 W2 模块别名（compute_eta 懒加载走 sys.modules 首查）
    stub = types.ModuleType("w2_replay_cli_eta")

    def _load(conn):
        return _tables(
            rem={("10IS255", "78202A-"): (9000, 9200, 9500, 100)},
            expected_delay={("10IS255", "78202A-"): (1800, 1200, 50, 50)})
    stub.load_tables = _load
    monkeypatch.setitem(sys.modules, "w2_replay_cli_eta", stub)

    ts = datetime(2026, 9, 9, 4, 30, tzinfo=TZ)
    latest = (ts, "10IS255", "78202A-", "0", "0")
    conn = _fake_conn(latest, oven_cnt=0,
                      rem=[("78202A-", 9000.0, 100), ("2N", 9100.0, 100), ("ALL", 9300.0, 100)])
    r = dashboard.compute_eta(conn, f"{PRODUCT_PREFIX}T1", "K3IS140",
                              datetime(2026, 9, 9, 5, 0, tzinfo=TZ))
    assert r["status"] == "eta"
    # vehicle 模式：base = rem p50 列（9000）+ delay = p50 列（1200）
    assert r["eta_sec"] == 9000.0 + 1200.0
    assert r["eff_flag"] == "0" and r["in_loop"] is False
    assert r["source"] == "78202A-" and r["n"] == 100
    # 137 位 + passed_oven 轨迹派生 SQL 确实执行
    sqls = [q for q, _ in conn._cur.queries]
    assert any("FROM 137 FOR 1" in s for s in sqls)
    assert any("16IS305" in s for s in sqls)
    monkeypatch.delitem(sys.modules, "w2_replay_cli_eta", raising=False)


def test_compute_eta_dual1_pre_oven_blacktop_silent(monkeypatch):
    # 烘房前假 2（未出炉非白名单）：状态机静默 '0' → 分支 B（非环分布）
    from spike_w3 import dashboard
    import sys, types
    monkeypatch.setenv("DSS_DUAL_RING", "1")
    stub = types.ModuleType("w2_replay_cli_eta")
    stub.load_tables = lambda conn: _tables(
        rem={("15IS120", "78202A-"): (9000, 9200, 9500, 100)},
        expected_delay={("15IS120", "78202A-"): (1800, 1200, 50, 50)})
    monkeypatch.setitem(sys.modules, "w2_replay_cli_eta", stub)
    ts = datetime(2026, 9, 9, 4, 30, tzinfo=TZ)
    latest = (ts, "15IS120", "78202A-", "2", "1")     # 139=2 + 137=1 但未出炉
    conn = _fake_conn(latest, oven_cnt=0, rem=[("78202A-", 9000.0, 100), ("ALL", 9300.0, 100)])
    r = dashboard.compute_eta(conn, "78202T2", "K3IS140", datetime(2026, 9, 9, 5, 0, tzinfo=TZ))
    assert r["status"] == "eta" and r["eff_flag"] == "0" and r["in_loop"] is False
    assert r["eta_sec"] == 9000.0 + 1200.0
    monkeypatch.delitem(sys.modules, "w2_replay_cli_eta", raising=False)


def test_compute_eta_dual0_old_path_unchanged(monkeypatch):
    # DUAL_RING=0：旧路径零漂移（不查 137 位 oven 轨迹、不装载表；rem p50 旧口径）
    from spike_w3 import dashboard
    monkeypatch.setenv("DSS_DUAL_RING", "0")
    ts = datetime(2026, 9, 9, 4, 30, tzinfo=TZ)
    latest = (ts, "10IS255", "78202A-", "0", "0")
    conn = _fake_conn(latest, rem=[("78202A-", 9000.0, 100), ("ALL", 9300.0, 100)])
    r = dashboard.compute_eta(conn, "78202T3", "K3IS140", datetime(2026, 9, 9, 5, 0, tzinfo=TZ))
    assert r["status"] == "eta" and r["eta_sec"] == 9000.0
    assert "eff_flag" not in r                      # 旧路径响应形状不变
    sqls = [q for q, _ in conn._cur.queries]
    assert not any("16IS305" in s for s in sqls)    # 未出炉轨迹查询不执行
