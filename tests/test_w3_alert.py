# -*- coding: utf-8 -*-
"""T4 缺口告警（docs/05 v3；2026-09-25 拍板：①A 激活窗 ②A H 占比 ④阈值 ⑤JSON/MD ⑥union 时界）。

全离线（合成池/假 cursor/假时钟），除 DDL 与段映射 bootstrap 走真库
（DB 不可达自动 skip，同 test_db_smoke 模式）。
步骤：T4.0 DDL → T4.1 split_target/grade/Debouncer → T4.2 build_station_section
→ T4.3 AlertEngine → T4.4 输出形态。
"""
import importlib.util
import socket
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import spike_w3.common as w3c


def _db_up() -> bool:
    try:
        socket.create_connection(("127.0.0.1", 5432), timeout=2).close()
        return True
    except OSError:
        return False


db_only = pytest.mark.skipif(not _db_up(), reason="analytics_db 不可达")

CST = timezone(timedelta(hours=8))


def _load_alert():
    p = Path(__file__).resolve().parent.parent / "spike_w3" / "alert_engine.py"
    spec = importlib.util.spec_from_file_location("w3_alert_engine", p)
    m = importlib.util.module_from_spec(spec)
    import sys as _sys
    _sys.modules["w3_alert_engine"] = m   # 必须注册（dataclass 延迟注解按模块名反查）
    spec.loader.exec_module(m)
    return m


# ---------- T4.0 DDL（真库形状断言，dss 可写只建表） ----------

@db_only
def test_ensure_work_shift_alert_ddl_shape():
    m = _load_alert()
    conn = w3c.connect()
    try:
        m.ensure_work_shift_alert(conn)
        m.ensure_work_shift_alert(conn)  # 幂等：二次执行不报错
        cur = conn.cursor()
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_schema='dss' AND table_name='work_shift_alert'"
            " ORDER BY ordinal_position")
        rows = cur.fetchall()
    finally:
        conn.close()
    cols = {name: typ for name, typ in rows}
    assert list(cols) == [
        "id", "scope", "sid", "scope_date", "ts", "prev_level", "level",
        "delta", "gap_n", "bottleneck_n", "rework_n", "blacktop_n",
        "sections", "created_at",
    ], f"列清单/顺序不符: {list(cols)}"
    assert cols["id"] == "bigint"
    assert cols["ts"] == "timestamp with time zone"
    assert cols["scope_date"] == "date"
    assert cols["delta"] == "double precision"
    assert cols["gap_n"] == "integer"
    assert cols["bottleneck_n"] == "integer"
    assert cols["rework_n"] == "integer"
    assert cols["blacktop_n"] == "integer"
    assert cols["sections"] == "jsonb"
    assert cols["level"] == "text"


@db_only
def test_ensure_dim_station_section_ddl_shape():
    m = _load_alert()
    conn = w3c.connect()
    try:
        m.ensure_dim_station_section(conn)
        m.ensure_dim_station_section(conn)  # 幂等
        cur = conn.cursor()
        cur.execute(
            "SELECT column_name, data_type FROM information_schema.columns"
            " WHERE table_schema='dss' AND table_name='dim_station_section'"
            " ORDER BY ordinal_position")
        rows = cur.fetchall()
    finally:
        conn.close()
    cols = {name: typ for name, typ in rows}
    assert list(cols) == [
        "station", "prefix_section", "area_mode", "n", "section", "status",
        "updated_at",
    ], f"列清单/顺序不符: {list(cols)}"
    assert cols["n"] == "integer"
    assert cols["updated_at"] == "timestamp with time zone"


# ---------- T4.1 split_target（docs/08 M2 通用公式，H 占比 + largest-remainder） ----------

def test_split_half_11_11():
    m = _load_alert()
    assert m.split_target(11, 11, 900) == (450, 450)


def test_split_11_10_largest_remainder():
    """11/21 = 0.52381 → 471.428/428.571 → floor 471/428 余 1 → 日班取余 472/428。"""
    m = _load_alert()
    assert m.split_target(11, 10, 900) == (472, 428)


def test_split_11_8():
    """11/19 = 0.57895 → 521.05/378.95 → 522/378（余 1 给日班）。"""
    m = _load_alert()
    assert m.split_target(11, 8, 900) == (522, 378)


def test_split_8_8():
    m = _load_alert()
    assert m.split_target(8, 8, 861) == (431, 430)  # 430.5 → 430/430 余 1 给日班


def test_split_day_rest_only():
    """中班休（h_mid None）：日班独占全天目标（占比 1.0）。"""
    m = _load_alert()
    assert m.split_target(11, None, 900) == (900, 0)
    assert m.split_target(None, 11, 900) == (0, 900)


def test_split_both_rest():
    m = _load_alert()
    assert m.split_target(None, None, 900) == (0, 0)
    assert m.split_target(0, 0, 900) == (0, 0)


def test_split_total_zero():
    m = _load_alert()
    assert m.split_target(11, 11, 0) == (0, 0)


def test_split_sum_invariant():
    """凑整不变式：任意 h/total 下 日+中 == total（或双休全 0）。"""
    m = _load_alert()
    for h_d, h_m, total in [(11, 11, 861), (11, 10, 862), (10, 8, 863),
                            (11, 8, 864), (8, 10, 865), (1, 99, 7), (99, 1, 7)]:
        d, mid = m.split_target(h_d, h_m, total)
        assert d + mid == total, f"{h_d}/{h_m}/{total} → {d}+{mid} != {total}"
        assert d >= 0 and mid >= 0


# ---------- T4.1 grade（拍板 C：Δ ≤ −10% 红；−10% < Δ ≤ −5% 黄；其余绿） ----------

def _cfg(red=-0.10, yellow=-0.05):
    return w3c.DSSConfig(alert_red=red, alert_yellow=yellow)


def test_grade_boundaries():
    m = _load_alert()
    g = m.grade
    assert g(-0.10, _cfg()) == "red"        # 含边界
    assert g(-0.05, _cfg()) == "yellow"     # 含边界
    assert g(-0.0499, _cfg()) == "green"
    assert g(-0.0999, _cfg()) == "yellow"
    assert g(-0.5, _cfg()) == "red"
    assert g(0.0, _cfg()) == "green"
    assert g(0.5, _cfg()) == "green"


def test_grade_custom_thresholds():
    m = _load_alert()
    assert m.grade(-0.20, _cfg(red=-0.20, yellow=-0.10)) == "red"
    assert m.grade(-0.15, _cfg(red=-0.20, yellow=-0.10)) == "yellow"


# ---------- T4.1 Debouncer（物理时差判据，口径 6；5min 步 = 3 轮采样确认） ----------

T0 = datetime(2026, 9, 12, 16, 30, tzinfo=CST)


def _dbouncer(m, minutes=10):
    return m.Debouncer(debounce_min=minutes)


def test_debounce_first_level_immediate():
    m = _load_alert()
    d = _dbouncer(m)
    assert d.feed(T0, "yellow") == (None, "yellow")   # 首档即确认（基线留痕）
    assert d.feed(T0 + timedelta(minutes=10), "yellow") is None  # 同档不连发


def test_debounce_hold_10min_confirms():
    m = _load_alert()
    d = _dbouncer(m)
    d.feed(T0, "green")
    assert d.feed(T0 + timedelta(minutes=5), "red") is None     # 起算
    assert d.feed(T0 + timedelta(minutes=10), "red") is None    # 5min 持续
    assert d.feed(T0 + timedelta(minutes=15), "red") == ("green", "red")  # 15min ≥10 确认


def test_debounce_boundary_inclusive():
    """恰好 10min（t − pending_since ≥ 10min 含等号）→ 确认。"""
    m = _load_alert()
    d = _dbouncer(m)
    d.feed(T0, "green")
    d.feed(T0 + timedelta(minutes=20), "yellow")
    assert d.feed(T0 + timedelta(minutes=30), "yellow") == ("green", "yellow")


def test_debounce_flashback_cancels():
    m = _load_alert()
    d = _dbouncer(m)
    d.feed(T0, "green")
    d.feed(T0 + timedelta(minutes=5), "red")
    d.feed(T0 + timedelta(minutes=9), "red")
    assert d.feed(T0 + timedelta(minutes=9, seconds=30), "green") is None  # 闪回清零
    # 闪回后重新起算：+10 起 pending red → +20 恰好 10min 确认
    d.feed(T0 + timedelta(minutes=10), "red")
    assert d.feed(T0 + timedelta(minutes=20), "red") == ("green", "red")


def test_debounce_same_level_no_repeat():
    m = _load_alert()
    d = _dbouncer(m)
    d.feed(T0, "green")
    d.feed(T0 + timedelta(minutes=5), "red")
    assert d.feed(T0 + timedelta(minutes=15), "red") == ("green", "red")  # 10min 确认
    assert d.feed(T0 + timedelta(minutes=16), "red") is None              # 同档不连发
    assert d.feed(T0 + timedelta(minutes=25), "red") is None


def test_debounce_downgrade_reentry():
    m = _load_alert()
    d = _dbouncer(m)
    d.feed(T0, "red")                                   # 首档红
    d.feed(T0 + timedelta(minutes=5), "green")
    d.feed(T0 + timedelta(minutes=10), "green")
    assert d.feed(T0 + timedelta(minutes=15), "green") == ("red", "green")  # 降级确认
    d.feed(T0 + timedelta(minutes=20), "yellow")        # 再升档
    d.feed(T0 + timedelta(minutes=25), "yellow")
    assert d.feed(T0 + timedelta(minutes=30), "yellow") == ("green", "yellow")  # 重入新转换


def test_debounce_third_level_resets():
    m = _load_alert()
    d = _dbouncer(m)
    d.feed(T0, "green")
    d.feed(T0 + timedelta(minutes=5), "red")
    d.feed(T0 + timedelta(minutes=8), "yellow")         # 第三档 → pending 换 yellow 重起算
    d.feed(T0 + timedelta(minutes=13), "yellow")
    assert d.feed(T0 + timedelta(minutes=18), "yellow") == ("green", "yellow")


def test_debounce_symmetric_down_to_up():
    """双向对称：红→黄（降级）与绿→黄（升级）同判据。"""
    m = _load_alert()
    d = _dbouncer(m)
    d.feed(T0, "red")
    d.feed(T0 + timedelta(minutes=5), "yellow")
    d.feed(T0 + timedelta(minutes=10), "yellow")
    assert d.feed(T0 + timedelta(minutes=15), "yellow") == ("red", "yellow")


def test_debounce_requires_aware():
    m = _load_alert()
    d = _dbouncer(m)
    with pytest.raises(TypeError):
        d.feed(datetime(2026, 9, 12, 16, 30), "green")  # naive


def test_debounce_custom_minutes():
    m = _load_alert()
    d = m.Debouncer(debounce_min=2)
    d.feed(T0, "green")
    d.feed(T0 + timedelta(minutes=1), "red")
    assert d.feed(T0 + timedelta(minutes=3), "red") == ("green", "red")
# 本文件由 conftest 无关；直接追加 import 与用例（同文件共享 _load_alert/db_only/CST）。
import pytest  # noqa: F401


class FakeCursor2:
    """假 cursor：按查询次序返回预置结果集（build_station_section 恰好 2 个查询）。"""

    def __init__(self, results):
        self._results = list(results)
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append(sql)

    def fetchall(self):
        return self._results.pop(0)

    def close(self):
        pass


class FakeConn2:
    def __init__(self, results):
        self._results = results
        self._cur = FakeCursor2(self._results)

    def cursor(self):
        return self._cur


# 真库查询序：① 站清单（DISTINCT 7d 窗） ② profile 众数配对
STATIONS_7 = [("03IS170",), ("09IS465",), ("XXIS999",), ("K1IS135",)]


def test_t42_prefix_rule_direct():
    """03 前缀 → 电泳；无 profile 证据 → 直接用前缀段（status auto）。"""
    m = _load_alert()
    conn = FakeConn2([STATIONS_7, []])  # profile 无行
    rows = {r["station"]: r for r in m.build_station_section(conn)}
    assert rows["03IS170"]["section"] == "电泳"
    assert rows["03IS170"]["prefix_section"] == "电泳"
    assert rows["03IS170"]["status"] == "auto"


def test_t42_profile_overrides_prefix():
    """profile 众数（n≥3）修正前缀：09 前缀=PVC 但众数 area=面漆烘房 → 顶涂。"""
    m = _load_alert()
    prof = [("09IS465", "面漆烘房", 5), ("09IS465", "PVC入口储存线", 2)]
    conn = FakeConn2([STATIONS_7, prof])
    rows = {r["station"]: r for r in m.build_station_section(conn)}
    assert rows["09IS465"]["area_mode"] == "面漆烘房"
    assert rows["09IS465"]["n"] == 5
    assert rows["09IS465"]["section"] == "顶涂"       # 修正前缀段
    assert rows["09IS465"]["prefix_section"] == "PVC预装密封"
    assert rows["09IS465"]["status"] == "auto"


def test_t42_profile_below_threshold_falls_back():
    """profile n<min_n_profile（默认 3）→ 不信众数，回退前缀；无前置 → 待确认。"""
    m = _load_alert()
    prof = [("09IS465", "面漆烘房", 2)]   # n=2 < 3
    conn = FakeConn2([STATIONS_7, prof])
    rows = {r["station"]: r for r in m.build_station_section(conn)}
    assert rows["09IS465"]["section"] == "PVC预装密封"  # 回退前缀
    assert rows["XXIS999"]["section"] == "待确认"
    assert rows["XXIS999"]["status"] == "待确认"


def test_t42_no_evidence_pending():
    """前缀无码 + profile 无证据 → 待确认（用户补）。"""
    m = _load_alert()
    conn = FakeConn2([STATIONS_7, [("XXIS999", "某个陌生区域", 10)]])
    rows = {r["station"]: r for r in m.build_station_section(conn)}
    assert rows["XXIS999"]["section"] == "待确认"     # 关键词不命中 → 无证据


def test_t42_min_n_profile_param():
    """min_n_profile 可调（n=3 恰好达标）。"""
    m = _load_alert()
    prof = [("09IS465", "面漆烘房", 3)]
    conn = FakeConn2([STATIONS_7, prof])
    rows = {r["station"]: r for r in m.build_station_section(conn)}
    assert rows["09IS465"]["section"] == "顶涂"


@pytest.mark.parametrize("area,expect", [
    ("一线预处理储存线", "前处理"),
    ("电泳烘房2.1", "电泳"),
    ("PVC入口储存线", "PVC预装密封"),
    ("4.5米打磨前储存线", "打磨"),
    ("L1面漆储存线", "顶涂"),
    ("L2分色线", "顶涂"),
    ("清漆烘房", "顶涂"),
    ("色选线", "顶涂"),
    ("注蜡F26_1道", "打蜡"),
    ("黑车顶F30烘房", "黑顶"),
    ("报交2.2", "交付-针孔修补-审核"),
    ("一线点修补返回线", "交付-针孔修补-审核"),
    ("审核工位A", "交付-针孔修补-审核"),
])
def test_t42_keyword_map(area, expect):
    m = _load_alert()
    assert m.kw_section(area) == expect


def test_t42_keyword_no_match_none():
    m = _load_alert()
    assert m.kw_section("完全陌生的区域名") is None


@db_only
def test_t42_real_db_full_table():
    """真库 259 站全表：覆盖/词汇/头部站抽查。"""
    m = _load_alert()
    conn = w3c.connect()
    try:
        rows = m.build_station_section(conn)
    finally:
        conn.close()
    assert len(rows) >= 250, f"站数不足: {len(rows)}"
    rows = {r["station"]: r for r in rows}
    vocab = set(m.SECTION_VOCAB) | {"待确认"}
    for st, r in rows.items():
        assert r["section"] in vocab, f"{st}: {r['section']} 不在八段词汇+待确认"
        assert set(r) >= {"station", "prefix_section", "area_mode", "n", "section", "status"}
    # 头部在制站抽查（2026-09-25 实测 profile 众数）
    assert rows["03IS170"]["section"] == "电泳"
    assert rows["17IS200"]["section"] == "顶涂"
    assert rows["33IS025"]["section"] == "打蜡"
    assert rows["21IS010"]["section"] == "顶涂"
    # 两出界接口站在表内（归属呈用户确认，不特判）
    assert "09IS360" in rows and "1J440RB" in rows
    # 覆盖：auto 占比 ≥60%（两级映射实测 ~67%，agy 轮 2 复核 162/259）
    auto = sum(1 for r in rows.values() if r["status"] == "auto")
    assert auto >= 0.6 * len(rows), f"auto 占比过低: {auto}/{len(rows)}"


# ---------- T4.3 AlertEngine ----------
from datetime import date as _date

from spike_w3.common import CST, Shift

D = _date(2026, 9, 12)
DAY_SHIFT = Shift(
    kind="day", day=D, type="11h",
    work_start=datetime(2026, 9, 12, 5, 0, tzinfo=CST),
    attr_start=datetime(2026, 9, 12, 4, 45, tzinfo=CST),
    end=datetime(2026, 9, 12, 16, 30, tzinfo=CST),
)
MID_SHIFT = Shift(
    kind="mid", day=D, type="11h",
    work_start=datetime(2026, 9, 12, 16, 30, tzinfo=CST),
    attr_start=datetime(2026, 9, 12, 16, 30, tzinfo=CST),
    end=datetime(2026, 9, 13, 4, 0, tzinfo=CST),
)

PLAN_11_11 = {D: {"total": 900, "day": "11h", "mid": "11h"}}   # 拆分 450/450
PLAN_11_10 = {D: {"total": 900, "day": "11h", "mid": "10h"}}   # 拆分 472/428
SECTION_MAP = {"A1": "顶涂", "B1": "打磨"}


def _bv(rem_min, station="A1", flag="0", n_offset=0):
    return {"body_id": f"78202{n_offset:05d}", "station": station, "flag": flag,
            "body_type": "YM33J", "rem_sec": rem_min * 60.0, "stale": False}


def _fake_pred_factory(hit_rems=(30,) * 200, bottleneck_rems=(1200,) * 90,
                       no_eta_n=10, wrong_pred=9999, wrong_A=12345):
    """假 pred_fn(shift, t)：默认 200 在制（rem 30min ≤ 任一窗末）+ 90 瓶颈（rem 20h）
    + 10 no-ETA。r['pred']/r['A'] 故意给错值（引擎必须自算，F9）。"""
    def pred_fn(shift, t):
        bv = [_bv(r) for r in hit_rems] + [_bv(r) for r in bottleneck_rems]
        bv += [{"body_id": f"78202N{i}", "station": "A1", "flag": "0",
                "body_type": "YM33J", "rem_sec": None, "stale": False}
               for i in range(no_eta_n)]
        return {"shift_id": "x", "t": t, "A": wrong_A, "inproc": len(bv),
                "pred": wrong_pred, "no_eta": no_eta_n, "stale": 0,
                "by_vehicle": bv}
    return pred_fn


def _a_fn_map(day_A=300, mid_A=200):
    def a_fn(shift, t):
        return day_A if shift.kind == "day" else mid_A
    return a_fn


def _mk_engine(m, plan=PLAN_11_11, guard=None):
    return m.AlertEngine(plan=plan, cfg=w3c.DSSConfig(),
                         section_map=SECTION_MAP, guard=guard)


class FakeGuard:
    def __init__(self, status="FRESH"):
        self._s = status

    def status(self):
        return self._s


def _t(h, mi=0, day=12):
    return datetime(2026, 9, day, h, mi, tzinfo=CST)


# ---------- T4.3 AlertEngine ----------

def test_evaluate_mid_period_scopes():
    """21:30（中班 5h 锚点）：mid + day_total 双 scope 基线红。
    mid: A=200 + 200 在制 = 400 vs 450 → Δ=−0.111 红。
    day_total: A_all=300 + 200 在制（end_later=04:00 全 ≤）= 500 vs 900 → Δ=−0.444 红。"""
    m = _load_alert()
    eng = _mk_engine(m)
    alerts = eng.evaluate(_t(21, 30), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                          _fake_pred_factory(), _a_fn_map(day_A=300, mid_A=200))
    got = {a.sid: a for a in alerts}
    assert set(got) == {"2026-09-12_mid", "2026-09-12"}, f"scope 集不符: {set(got)}"
    mid, tot = got["2026-09-12_mid"], got["2026-09-12"]
    assert mid.scope == "shift" and mid.prev_level is None and mid.level == "red"
    assert abs(mid.delta - (400 - 450) / 450) < 1e-12
    assert mid.gap_n == 50
    assert tot.scope == "day_total" and tot.prev_level is None and tot.level == "red"
    assert abs(tot.delta - (500 - 900) / 900) < 1e-12
    assert tot.gap_n == 400


def test_evaluate_day_period_no_day_total():
    """10:00（日班 5h 锚点）：只有 day scope，无 day_total（F10 激活窗）。"""
    m = _load_alert()
    eng = _mk_engine(m)
    alerts = eng.evaluate(_t(10, 0), [DAY_SHIFT, MID_SHIFT], [],
                          _fake_pred_factory(), _a_fn_map(day_A=100, mid_A=0))
    assert [a.sid for a in alerts] == ["2026-09-12_day"]
    a = alerts[0]
    # A=100 + 200 在制（16:30 前）= 300 vs 450 → Δ=−0.333 红基线
    assert a.level == "red" and abs(a.delta - (300 - 450) / 450) < 1e-12


def test_evaluate_split_11_10_targets():
    """11/10 拆分 472/428（largest-remainder）。mid pred 400 vs 428 → Δ=−0.0654 黄。"""
    m = _load_alert()
    eng = _mk_engine(m, plan=PLAN_11_10)
    alerts = eng.evaluate(_t(21, 30), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                          _fake_pred_factory(), _a_fn_map(day_A=300, mid_A=200))
    mid = {a.sid: a for a in alerts}["2026-09-12_mid"]
    assert abs(mid.delta - (400 - 428) / 428) < 1e-12
    assert mid.level == "yellow"


def test_evaluate_rest_day_no_scope():
    m = _load_alert()
    eng = _mk_engine(m, plan={D: {"total": 0, "day": None, "mid": None}})
    assert eng.evaluate(_t(21, 30), [], [], _fake_pred_factory(), _a_fn_map()) == []
    eng2 = _mk_engine(m, plan={})   # 缺排班日（docs/08 安全网）
    assert eng2.evaluate(_t(21, 30), [], [], _fake_pred_factory(), _a_fn_map()) == []


def test_evaluate_bottleneck_breakdown():
    """瓶颈分层：flag 1/2/0/− + 段映射（未知站 → 待确认）。11 台全瓶颈。"""
    m = _load_alert()
    bv = ([_bv(1200, station="A1", flag="1") for _ in range(3)]
          + [_bv(1200, station="A1", flag="2") for _ in range(2)]
          + [_bv(1200, station="B1", flag="0") for _ in range(5)]
          + [_bv(1200, station="C1", flag="-")])

    def pred_fn(shift, t):
        return {"shift_id": "x", "t": t, "A": 0, "inproc": 11, "pred": 0,
                "no_eta": 0, "stale": 0, "by_vehicle": bv}
    eng = _mk_engine(m)
    alerts = eng.evaluate(_t(21, 30), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                          pred_fn, _a_fn_map(day_A=0, mid_A=0))
    mid = {a.sid: a for a in alerts}["2026-09-12_mid"]
    assert mid.bottleneck_n == 11
    assert mid.rework_n == 3 and mid.blacktop_n == 2   # 主线 = 11−3−2 = 6
    assert mid.sections == {"顶涂": 5, "打磨": 5, "待确认": 1}
    assert mid.level == "red" and mid.gap_n == 450     # A=0、在制 0 → pred 0 vs 450


def test_evaluate_engine_ignores_pred_fn_wrong_fields():
    """引擎 pred 自算（by_vehicle + a_fn）：r['pred']/r['A'] 错值不传染（F9）。"""
    m = _load_alert()
    eng = _mk_engine(m)
    alerts = eng.evaluate(_t(21, 30), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                          _fake_pred_factory(wrong_pred=9999, wrong_A=12345),
                          _a_fn_map(day_A=300, mid_A=200))
    mid = {a.sid: a for a in alerts}["2026-09-12_mid"]
    assert mid.gap_n == 50   # pred=400，非 9999/12345 传染值


def test_evaluate_stale_guard_freeze():
    """guard 非 FRESH → 空返回且 debouncer 不推进（恢复后重新基线）。"""
    m = _load_alert()
    g = FakeGuard("STALE")
    eng = _mk_engine(m, guard=g)
    assert eng.evaluate(_t(16, 30), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                        _fake_pred_factory(), _a_fn_map()) == []
    g._s = "FRESH"
    alerts = eng.evaluate(_t(16, 35), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                          _fake_pred_factory(), _a_fn_map())
    assert len(alerts) == 2 and all(a.prev_level is None for a in alerts)


def test_evaluate_debounce_confirms_at_10min():
    """16:30 绿基线 → 17:00/17:05 红未确认 → 17:10 确认红（3 轮采样）。"""
    m = _load_alert()
    eng = _mk_engine(m)
    pf_green = _fake_pred_factory(hit_rems=(30,) * 400)   # mid 600 vs 450 → 绿
    pf_red = _fake_pred_factory(hit_rems=())              # mid 200 vs 450 → 红
    a0 = eng.evaluate(_t(16, 30), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                      pf_green, _a_fn_map(day_A=300, mid_A=200))
    assert [a.level for a in a0 if a.sid == "2026-09-12_mid"] == ["green"]
    a1 = eng.evaluate(_t(17, 0), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                      pf_red, _a_fn_map(day_A=300, mid_A=200))
    assert [a for a in a1 if a.sid == "2026-09-12_mid"] == []
    a2 = eng.evaluate(_t(17, 5), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                      pf_red, _a_fn_map(day_A=300, mid_A=200))
    assert [a for a in a2 if a.sid == "2026-09-12_mid"] == []
    a3 = eng.evaluate(_t(17, 10), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                      pf_red, _a_fn_map(day_A=300, mid_A=200))
    mid3 = [a for a in a3 if a.sid == "2026-09-12_mid"]
    assert len(mid3) == 1 and mid3[0].prev_level == "green" and mid3[0].level == "red"
    a4 = eng.evaluate(_t(17, 15), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                      pf_red, _a_fn_map(day_A=300, mid_A=200))
    assert [a for a in a4 if a.sid == "2026-09-12_mid"] == []   # 同档不连发


def test_evaluate_downgrade_confirms():
    """红基线 → 10min 持续绿 → 确认降级（red→green）。"""
    m = _load_alert()
    eng = _mk_engine(m)
    pf_red = _fake_pred_factory(hit_rems=())
    pf_green = _fake_pred_factory(hit_rems=(30,) * 400)
    a0 = eng.evaluate(_t(16, 30), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                      pf_red, _a_fn_map(day_A=300, mid_A=200))
    assert [a.level for a in a0 if a.sid == "2026-09-12_mid"] == ["red"]
    a1 = eng.evaluate(_t(17, 0), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                      pf_green, _a_fn_map(day_A=300, mid_A=200))
    assert [a for a in a1 if a.sid == "2026-09-12_mid"] == []
    a2 = eng.evaluate(_t(17, 10), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                      pf_green, _a_fn_map(day_A=300, mid_A=200))
    mid2 = [a for a in a2 if a.sid == "2026-09-12_mid"]
    assert len(mid2) == 1 and mid2[0].prev_level == "red" and mid2[0].level == "green"


def test_evaluate_flashback_cancels():
    """16:30 绿 → 17:00/17:05 红 pending → 17:09:30 闪回绿清零 → 17:10 无转换。"""
    m = _load_alert()
    eng = _mk_engine(m)
    pf_green = _fake_pred_factory(hit_rems=(30,) * 400)
    pf_red = _fake_pred_factory(hit_rems=())
    eng.evaluate(_t(16, 30), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                 pf_green, _a_fn_map(day_A=300, mid_A=200))
    for tt in (_t(17, 0), _t(17, 5)):
        res = eng.evaluate(tt, [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                           pf_red, _a_fn_map(day_A=300, mid_A=200))
        assert [a for a in res if a.sid == "2026-09-12_mid"] == []
    res = eng.evaluate(datetime(2026, 9, 12, 17, 9, 30, tzinfo=CST),
                       [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                       pf_green, _a_fn_map(day_A=300, mid_A=200))
    assert [a for a in res if a.sid == "2026-09-12_mid"] == []   # 闪回取消
    res2 = eng.evaluate(_t(17, 10), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                        pf_green, _a_fn_map(day_A=300, mid_A=200))
    assert [a for a in res2 if a.sid == "2026-09-12_mid"] == []  # 绿 = current


def test_evaluate_scope_day_isolation():
    """不同 sid 的 debouncer 互不串扰：day 基线与 mid/day_total 基线独立。"""
    m = _load_alert()
    eng = _mk_engine(m)
    a_day = eng.evaluate(_t(10, 0), [DAY_SHIFT, MID_SHIFT], [],
                         _fake_pred_factory(hit_rems=(30,) * 500),   # 日 600 vs 450 绿
                         _a_fn_map(day_A=100, mid_A=0))
    assert [a.sid for a in a_day] == ["2026-09-12_day"] and a_day[0].level == "green"
    a_mid = eng.evaluate(_t(21, 30), [DAY_SHIFT, MID_SHIFT], [DAY_SHIFT],
                         _fake_pred_factory(hit_rems=(30,) * 200),
                         _a_fn_map(day_A=300, mid_A=200))
    assert {a.sid for a in a_mid} == {"2026-09-12_mid", "2026-09-12"}
    assert all(a.prev_level is None for a in a_mid)


def test_evaluate_requires_aware():
    m = _load_alert()
    eng = _mk_engine(m)
    with pytest.raises(TypeError):
        eng.evaluate(datetime(2026, 9, 12, 21, 30), [DAY_SHIFT, MID_SHIFT],
                     [DAY_SHIFT], _fake_pred_factory(), _a_fn_map())


# ---------- T4.4 输出形态 / hook / 门判 ----------
from datetime import datetime as _dt44

# ---------- T4.4 render_alert_line（docs/05 §2.3） ----------

def _mk_alert(m, **kw):
    base = dict(scope="shift", sid="2026-09-12_mid",
                t=_dt44(2026, 9, 12, 21, 30, tzinfo=CST),
                prev_level="green", level="red", delta=-0.117, gap_n=56,
                bottleneck_n=636, rework_n=109, blacktop_n=107,
                sections={"顶涂": 210, "打磨": 180, "前处理": 90, "电泳": 5})
    base.update(kw)
    return m.Alert(**base)


def test_render_shift_line():
    m = _load_alert()
    line = m.render_alert_line(_mk_alert(m))
    # 主线 = 636−109−107 = 420（构造性一致）；段 Top-3 + …
    assert line == ("09-12_mid Δ -11.7% red | 缺口 56 台 | 瓶颈 636 台"
                    "（返修 109 / 黑顶 107 / 主线 420）: 顶涂 210 · 打磨 180 · 前处理 90 …"), line


def test_render_day_total_line_and_naming():
    m = _load_alert()
    a = _mk_alert(m, scope="day_total", sid="2026-09-12", level="yellow",
                  delta=-0.06, gap_n=120, bottleneck_n=100, rework_n=5,
                  blacktop_n=3, sections={"顶涂": 60, "打磨": 40})
    line = m.render_alert_line(a)
    assert line.startswith("09-12_全天 Δ -6.0% yellow"), line
    assert "（返修 5 / 黑顶 3 / 主线 92）: 顶涂 60 · 打磨 40" in line  # 恰 2 段无 …


def test_render_dual_track_hint():
    """shift 红 且 全天绿 → 拆分偏置标注；全天红 → 不标。"""
    m = _load_alert()
    a = _mk_alert(m)
    assert m.render_alert_line(a, day_total_level="green").endswith(
        " [拆分目标偏置（全天主视图绿，见 F8）]")
    assert m.render_alert_line(a, day_total_level="red") \
        == m.render_alert_line(a, day_total_level=None)
    # 绿档不标
    ag = _mk_alert(m, level="green", delta=0.01, prev_level="red")
    assert "偏置" not in m.render_alert_line(ag, day_total_level="green")


def test_render_sections_top3_sorted():
    m = _load_alert()
    a = _mk_alert(m, sections={"a": 1, "b": 2, "c": 3, "d": 4, "e": 5})
    line = m.render_alert_line(a)
    assert "e 5 · d 4 · c 3 …" in line


# ---------- T4.4 02 run_rolling alert_hook 参数（默认 None = 生产零影响） ----------

def test_run_rolling_alert_hook_default_none():
    import inspect
    m9 = _load_m9()
    sig = inspect.signature(m9.run_rolling)
    p = sig.parameters["alert_hook"]
    assert p.default is None


def _load_m9():
    p = Path(__file__).resolve().parent.parent / "spike_w3" / "02_shadow_replay.py"
    spec = importlib.util.spec_from_file_location("w3_shadow_replay", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------- T4.4 03 门判纯函数（合成 alerts/engine_view 驱动） ----------

def _load_backtest():
    p = Path(__file__).resolve().parent.parent / "spike_w3" / "03_alert_backtest.py"
    spec = importlib.util.spec_from_file_location("w3_alert_backtest", p)
    mod = importlib.util.module_from_spec(spec)
    import sys as _sys
    _sys.modules["w3_alert_backtest"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_gate_g1_median_and_zero_red():
    m = _load_backtest()
    # 7 天 21:30：|d5h − dfinal| pp；21:30 level
    rows = [(f"d{i}", a, b, lv) for i, (a, b, lv) in enumerate([
        (0.01, 0.015, "green"), (0.02, 0.025, "green"), (0.03, 0.03, "green"),
        (0.04, 0.045, "green"), (0.00, 0.01, "green"), (0.02, 0.01, "green"),
        (0.01, 0.02, "green")])]   # |差| pp = [.5,.5,0,.5,1,1,1] 中位 0.5
    ok, detail = m.gate_g1(rows, max_pp=5.0)
    assert ok and detail["median_pp"] == 0.5 and detail["n_red"] == 0
    extra = [(f"d{i}", 0.01, 0.12, "red") for i in range(3)]
    ok2, d2 = m.gate_g1(rows[:3] + extra, max_pp=5.0)
    assert not ok2 and d2["n_red"] == 3
    rows_big = [("d9", 0.0, 0.09, "green")] * 7   # 中位 9pp > 5
    ok3, _ = m.gate_g1(rows_big, max_pp=5.0)
    assert not ok3


def test_gate_g1b_engine_pred_equals_m9():
    m = _load_backtest()
    eng = {"s1": 500, "s2": 480}
    ok, miss = m.gate_g1b(eng, {"s1": 500, "s2": 481, "s3": 100})
    assert not ok and miss == ["s2", "s3"]
    ok2, miss2 = m.gate_g1b(eng, {"s1": 500, "s2": 480})
    assert ok2 and miss2 == []


def test_gate_g2_level_matches_grade():
    m = _load_backtest()
    cfg = w3c.DSSConfig()
    # level == grade(delta) → ok；pending 滞后（pending_level==grade 且未满 10min）→ 豁免
    rows = [
        {"sid": "a", "delta": -0.02, "level": "green",
         "grade": "green", "pending_level": None, "pending_age_min": None},
        {"sid": "b", "delta": -0.07, "level": "yellow",       # 滞后中
         "grade": "yellow", "pending_level": "red", "pending_age_min": 5},
        {"sid": "c", "delta": -0.12, "level": "yellow",       # 滞后超 10min 仍未确认 = bug
         "grade": "red", "pending_level": "red", "pending_age_min": 11},
    ]
    ok, bad = m.gate_g2(rows, cfg)
    assert not ok and bad == ["c"]
    ok2, bad2 = m.gate_g2(rows[:2], cfg)
    assert ok2 and bad2 == []


def test_gate_g3_lead_time():
    m = _load_backtest()
    # 终局红（mid）：首次确认红 ≤ 3h 锚点（19:30）
    alerts = [
        {"sid": "d_mid", "t": "2026-09-05T19:00:00+08:00", "prev_level": "green",
         "level": "red"},
    ]
    finals = {"d_mid": {"final_level": "red", "anchor3": "2026-09-05T19:30:00+08:00"}}
    ok, miss = m.gate_g3(alerts, finals)
    assert ok and miss == []
    alerts_late = [
        {"sid": "d_mid", "t": "2026-09-05T19:45:00+08:00", "prev_level": "green",
         "level": "red"},
    ]
    ok2, miss2 = m.gate_g3(alerts_late, finals)
    assert not ok2 and miss2 == ["d_mid"]
    # 终局非绿的 scope-day 无确认转换（一直红基线）→ 基线时刻即首次进入
    alerts_base = [
        {"sid": "e_mid", "t": "2026-09-05T16:30:00+08:00", "prev_level": None,
         "level": "red"},
    ]
    finals_e = {"e_mid": {"final_level": "red", "anchor3": "2026-09-05T19:30:00+08:00"}}
    ok3, _ = m.gate_g3(alerts_base, finals_e)
    assert ok3


def test_gate_g4_debounce_stability():
    m = _load_backtest()
    # 拍板 B（2026-09-26）：门 = 锚点前 10min 窗口无转换；次数降观测项
    ok, bad = m.gate_g4(
        [{"sid": "a", "t": "2026-09-05T17:00:00+08:00", "prev_level": "green", "level": "red"},
         {"sid": "a", "t": "2026-09-05T18:00:00+08:00", "prev_level": "red", "level": "green"},
         {"sid": "b", "t": "2026-09-05T21:25:00+08:00", "prev_level": "green", "level": "yellow"}],
        {"a": "2026-09-05T21:30:00+08:00", "b": "2026-09-05T21:30:00+08:00"})
    assert not ok and bad == ["b"]      # 21:25 转换在窗口 [21:20, 21:30) 内
    ok2, _ = m.gate_g4(
        [{"sid": "a", "t": "2026-09-05T17:00:00+08:00", "prev_level": "green", "level": "red"},
         {"sid": "a", "t": "2026-09-05T18:00:00+08:00", "prev_level": "red", "level": "green"},
         {"sid": "a", "t": "2026-09-05T19:00:00+08:00", "prev_level": "green", "level": "red"}],
        {"a": "2026-09-05T21:30:00+08:00"})
    assert ok2                          # 3 次转换均在窗口外 → ok
    # 拍板 B：次数不再是门判条件（09-09_mid 实测 7 次全有 ≥10min 证据 = 数据性质）
    ok3, bad3 = m.gate_g4(
        [{"sid": "c", "t": f"2026-09-05T1{i}:00:00+08:00", "prev_level": "g", "level": "y"}
         for i in range(4)],
        {"c": "2026-09-05T21:30:00+08:00"})
    assert ok3 and bad3 == []           # 4 次转换均在窗口外 → ok（次数仅进 G4_obs）
    # 锚点之后的转换不在窗口 [anchor−10min, anchor) 内 → 不判 bad（T4 M10 首跑修正）
    ok4, bad4 = m.gate_g4(
        [{"sid": "d", "t": "2026-09-05T21:40:00+08:00", "prev_level": "g", "level": "y"}],
        {"d": "2026-09-05T21:30:00+08:00"})
    assert ok4 and bad4 == []
    # 恰在窗口边界：21:20 在内（bad），21:30 锚点时刻本身不在 [.., anchor)（ok）
    ok5, bad5 = m.gate_g4(
        [{"sid": "e", "t": "2026-09-05T21:20:00+08:00", "prev_level": "g", "level": "y"}],
        {"e": "2026-09-05T21:30:00+08:00"})
    assert not ok5 and bad5 == ["e"]
    ok6, bad6 = m.gate_g4(
        [{"sid": "f", "t": "2026-09-05T21:30:00+08:00", "prev_level": "g", "level": "y"}],
        {"f": "2026-09-05T21:30:00+08:00"})
    assert ok6 and bad6 == []


def test_gate_g5_identity():
    m = _load_backtest()
    rows = [
        {"sid": "s1", "pool_n": 300, "hit": 200, "bottleneck": 90, "no_eta": 10,
         "m9_inproc": 300},
        {"sid": "s2", "pool_n": 300, "hit": 200, "bottleneck": 89, "no_eta": 11,
         "m9_inproc": 299},   # 与 M9 inproc 不一致
    ]
    ok, bad = m.gate_g5(rows)
    assert not ok and bad == ["s2"]
    ok2, _ = m.gate_g5([rows[0]])
    assert ok2


def test_gate_g6_coverage():
    m = _load_backtest()
    alerts = [{"sid": "a", "t": "x", "prev_level": None, "level": "green"}]
    ok, miss = m.gate_g6(alerts, ["a", "b", "c"])
    assert not ok and sorted(miss) == ["b", "c"]
    ok2, _ = m.gate_g6(alerts, ["a"])
    assert ok2
