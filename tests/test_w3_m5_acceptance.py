# -*- coding: utf-8 -*-
"""M5 在线验收收账（spike_w3/04_m5_acceptance.py）单测。

口径（docs/01 W3 验收门 + W2/M9 同源）：
- 主判据：逐工作日 5h 锚点 |Δ实际| ≤ 10%；
  Δ实际 = (pred_锚点 − 实际终局) / 实际终局；
  实际终局 = 4 下线点产品车闭区间计数（日 [D 04:45, D 16:30]；
  中 [D 16:30, D+1 MID_END_TIMES[type]]，与 a_of `<= end` 同源）。
- 锚点帧 = work_start+5h（日 10:00 / 中 21:30，固定——work_start 日 05:00 / 中 16:30 不随班次型变）。
- 断流（guard.status=STALE）轮次单独统计、不计门；休日（锚点帧无班次）剔出门外。
"""
from __future__ import annotations

import importlib.util
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

from spike_w2 import shifts as shifts_mod
from spike_w3.common import EXIT_STATIONS

TZ = __import__("zoneinfo").ZoneInfo("Asia/Shanghai")
D = date(2026, 10, 5)


def _load_mod():
    import sys
    p = Path(__file__).resolve().parents[1] / "spike_w3" / "04_m5_acceptance.py"
    name = "w3_m5_acc"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


M5 = _load_mod()


def _snap(ts, shift=None, day_total=None, guard="FRESH", alerts=None):
    return {
        "ts": ts.isoformat(), "week": "2026KW41", "weekday": "周一",
        "mode": "wall_clock", "next_shift": None,
        "pool": {"size": 500, "inproc_n": 400, "bottleneck_n": 50,
                 "no_eta_n": 50, "flag_dist": {"0": 400, "1": 50, "2": 50},
                 "bottleneck_flag_dist": {}, "bottleneck_sections": {}},
        "pred": {"shift": shift, "day_total": day_total},
        "alpha": {"mode": "off", "value": 1.0},
        "alert_state": {}, "alerts": alerts or [],
        "guard": {"status": guard, "label": "数据新鲜" if guard == "FRESH" else "数据断流",
                  "lag_min": 1.0, "is_anomaly": guard != "FRESH",
                  "diagnose": None, "diagnose_note": "x"},
        "data": {"max_ts": ts.isoformat(), "watermark": 1.0},
        "run_tag": "m5",
    }


def _shift(kind="day", t=11, type_=None, A=283, pred=518, target=478, grade="green"):
    return {"shift_sid": f"2026-10-05_{kind}", "kind": kind, "type": type_ or f"{t}h",
            "attr_hm": "04:45" if kind == "day" else "16:30",
            "end_hm": "16:30" if kind == "day" else "04:00",
            "A": A, "pred": pred, "target": target,
            "delta": round(pred / target - 1, 4), "grade": grade,
            "anchor_state": "post", "anchor_in_min": 120,
            "anchor_first_hm": "08:00" if kind == "day" else "19:30"}


# ---------- pick_anchor ----------

def test_pick_anchor_day_and_mid():
    snaps = {
        "2026-10-05T09:55:00+08:00": _snap(datetime(2026, 10, 5, 9, 55, tzinfo=TZ), _shift()),
        "2026-10-05T10:00:00+08:00": _snap(datetime(2026, 10, 5, 10, 0, tzinfo=TZ), _shift()),
        "2026-10-05T21:30:00+08:00": _snap(datetime(2026, 10, 5, 21, 30, tzinfo=TZ),
                                            _shift(kind="mid")),
    }
    got = M5.pick_anchor(snaps, D, "day")
    assert got is not None and got[0].hour == 10 and got[0].minute == 0
    got = M5.pick_anchor(snaps, D, "mid")
    assert got is not None and got[0].hour == 21 and got[0].minute == 30


def test_pick_anchor_day_anchor_follows_type():
    """agy 阻断 F5：日班锚点随班次型——10h 开班 06:00 → 锚 11:00；8h → 13:00。"""
    for mt, ah, am in (("11h", 10, 0), ("10h", 11, 0), ("8h", 13, 0)):
        t = datetime(2026, 10, 5, ah, am, tzinfo=TZ)
        snaps = {t.isoformat(): _snap(t, _shift(type_=mt))}
        got = M5.pick_anchor(snaps, D, "day")
        assert got is not None and (got[0].hour, got[0].minute) == (ah, am), mt
        assert (got[2].hour, got[2].minute) == (ah, am)   # 期望锚点也按型


def test_pick_anchor_tolerance():
    """agy 阻断 F2：容差 ±180s——10:00:40 漂移帧可中；只有 10:05:00（300s 超差）→ None。"""
    drift = _snap(datetime(2026, 10, 5, 10, 0, 40, tzinfo=TZ), _shift())
    got = M5.pick_anchor({"2026-10-05T10:00:40+08:00": drift}, D, "day")
    assert got is not None
    far = _snap(datetime(2026, 10, 5, 10, 5, 0, tzinfo=TZ), _shift())
    assert M5.pick_anchor({"2026-10-05T10:05:00+08:00": far}, D, "day") is None


def test_pick_anchor_missing():
    snaps = {"2026-10-05T09:55:00+08:00": _snap(datetime(2026, 10, 5, 9, 55, tzinfo=TZ), _shift())}
    assert M5.pick_anchor(snaps, D, "day") is None
    assert M5.pick_anchor({}, D, "mid") is None


# ---------- shift_actual_window ----------

def test_actual_window_day():
    s, e = M5.shift_actual_window(D, "day", "11h")
    assert s == datetime(2026, 10, 5, 4, 45, tzinfo=TZ)
    assert e == datetime(2026, 10, 5, 16, 30, tzinfo=TZ)


@pytest.mark.parametrize("mt,exp_h", [("11h", 4), ("10h", 3), ("8h", 1)])
def test_actual_window_mid_by_type(mt, exp_h):
    s, e = M5.shift_actual_window(D, "mid", mt)
    assert s == datetime(2026, 10, 5, 16, 30, tzinfo=TZ)
    assert e == datetime(2026, 10, 6, exp_h, 0, tzinfo=TZ)
    # 与 shifts.MID_END_TIMES 同源
    assert e.time() == shifts_mod.MID_END_TIMES[mt]


# ---------- assess ----------

def test_assess_pass():
    r = M5.assess(pred=956, target=950, actual=1000, tol=0.10)
    assert r["pass"] is True                      # (956-1000)/1000 = -4.4%
    assert r["delta_actual"] == pytest.approx(-0.044)
    assert r["delta_plan"] == pytest.approx(956 / 950 - 1, abs=1e-4)  # 实现侧 round(4)


def test_assess_boundary_inclusive():
    assert M5.assess(pred=900, target=950, actual=1000, tol=0.10)["pass"] is True   # 恰 -10%
    assert M5.assess(pred=899, target=950, actual=1000, tol=0.10)["pass"] is False  # -10.1%
    assert M5.assess(pred=1101, target=950, actual=1000, tol=0.10)["pass"] is False # +10.1%


def test_assess_zero_actual():
    r = M5.assess(pred=0, target=478, actual=0, tol=0.10)
    assert r["pass"] is None and r["note"]          # 实际 0（休/停线）= 不计门 + 注记
    assert r["reason"] == "actual_zero"             # agy 中 F4：结构化 reason（非 note 子串）


def test_assess_gated_reason():
    assert M5.assess(pred=950, target=950, actual=1000, tol=0.10)["reason"] == "gated"


# ---------- query_actual ----------

class FakeCur:
    def __init__(self):
        self.sqls = []
        self.params = []
    def execute(self, sql, params=None):
        self.sqls.append(sql)
        self.params.append(params)
    def fetchone(self):
        return (478,)
    def close(self):
        pass


class FakeConn:
    def __init__(self):
        self.cur = FakeCur()
    def cursor(self):
        return self.cur
    def close(self):
        pass


def test_query_actual_sql_mid_11h():
    conn = FakeConn()
    n = M5.query_actual(conn, D, "mid", "11h")
    assert n == 478
    sql = conn.cur.sqls[-1]
    assert "count(DISTINCT " in sql and "ods.carbody_history" in sql
    assert M5.PRODUCT_LIKE in conn.cur.params[-1]       # 产品车前缀（参数位）
    for st in EXIT_STATIONS:
        assert st in conn.cur.params[-1]              # 4 下线点（参数位）
    p = conn.cur.params[-1]
    assert p[5] == datetime(2026, 10, 5, 16, 30, tzinfo=TZ)
    assert p[6] == datetime(2026, 10, 6, 4, 0, tzinfo=TZ)   # 闭区间 <= end
    assert "TIME ZONE 'Asia/Shanghai'" in conn.cur.sqls[0]


# ---------- stale_stats / alert_ledger ----------

def _stale_snaps():
    base = datetime(2026, 10, 5, 10, 0, tzinfo=TZ)
    snaps = {}
    for i in range(6):
        ts = base + timedelta(minutes=5 * i)
        guard = "STALE" if i in (1, 2, 3, 6 - 1) else "FRESH"
        snaps[ts.isoformat()] = _snap(ts, _shift(), guard=guard)
    return snaps


def test_stale_stats():
    st = M5.stale_stats(_stale_snaps())
    assert st["n_rounds"] == 4
    assert st["minutes"] == 20
    # 连续段归并：[10:05,10:20) 3 轮一段 + 10:25 单轮一段
    assert len(st["spans"]) == 2


def test_alert_ledger():
    snaps = {
        "2026-10-05T10:00:00+08:00": _snap(datetime(2026, 10, 5, 10, 0, tzinfo=TZ),
                                           alerts=[{"sid": "2026-10-05_day", "level": "yellow",
                                                    "prev_level": "green", "gap_n": 30,
                                                    "delta": -0.06, "t": "2026-10-05T10:00:00+08:00"}]),
        "2026-10-05T10:05:00+08:00": _snap(datetime(2026, 10, 5, 10, 5, tzinfo=TZ),
                                           alerts=[{"sid": "2026-10-05_day", "level": "red",
                                                    "prev_level": "yellow", "gap_n": 55,
                                                    "delta": -0.12, "t": "2026-10-05T10:05:00+08:00"}]),
    }
    led = M5.alert_ledger(snaps)
    assert len(led) == 2
    assert led[0]["level"] == "yellow" and led[1]["level"] == "red"


# ---------- build_report ----------

def _canned_results():
    return [
        {"date": D, "weekday": "周一", "kind": "day", "anchor_ts": "2026-10-05T10:00:00+08:00",
         "A": 283, "pred": 518, "target": 478, "actual": 578, "delta_plan": 0.084,
         "delta_actual": -0.104, "grade": "green", "stale": False, "pass": False,
         "reason": "gated", "note": ""},
        {"date": D, "weekday": "周一", "kind": "mid", "anchor_ts": "2026-10-05T21:30:00+08:00",
         "A": 221, "pred": 422, "target": 478, "actual": 371, "delta_plan": -0.117,
         "delta_actual": 0.137, "grade": "red", "stale": False, "pass": False,
         "reason": "gated", "note": ""},
    ]


def test_build_report_contains():
    md = M5.build_report(
        snap_dirs=["output/snapshots/m5"], run_tag="m5", n_snaps=1440,
        results=_canned_results(), stale={"n_rounds": 4, "minutes": 20,
                                          "spans": [("10:05", "10:25")]},
        ledger=[{"ts": "2026-10-05T10:00:00+08:00", "sid": "2026-10-05_day",
                 "level": "yellow", "prev_level": "green", "gap_n": 30}],
        tol=0.10, rest_days=[date(2026, 10, 11)],
    )
    assert "终判: FAIL" in md
    assert "2026-10-05" in md and "m5" in md
    assert "断流" in md and "2026-10-11" in md          # 断流节 + 休日剔除注记
    assert "518" in md and "578" in md                  # 主表数字


def test_build_report_all_pass():
    ok = [dict(_canned_results()[0], delta_actual=-0.044, reason="gated",
               **{"pass": True}),
          dict(_canned_results()[1], delta_actual=0.02, reason="gated",
               **{"pass": True})]
    md = M5.build_report(snap_dirs=["x"], run_tag="m5", n_snaps=288, results=ok,
                         stale={"n_rounds": 0, "minutes": 0, "spans": []},
                         ledger=[], tol=0.10, rest_days=[])
    assert "终判: PASS" in md


def test_build_report_reason_buckets_mutually_exclusive():
    """agy 中 F4：四桶互斥——actual=0 不得误判为锚点帧缺失；stale 行只计一次。"""
    a0 = {"date": D, "weekday": "周一", "kind": "day", "anchor_ts": "2026-10-05T10:00:00+08:00",
          "A": 0, "pred": 0, "target": 478, "actual": 0, "delta_plan": None,
          "delta_actual": None, "grade": None, "stale": False, "pass": None,
          "reason": "actual_zero", "note": "实际终局 0（休/停线/无事件）——不计门"}
    st = {"date": D, "weekday": "周一", "kind": "mid", "anchor_ts": "2026-10-05T21:30:00+08:00",
          "A": 100, "pred": 300, "target": 478, "actual": 350, "delta_plan": -0.374,
          "delta_actual": -0.143, "grade": "red", "stale": True, "pass": False,
          "reason": "stale", "note": "锚点帧 STALE（断流冻结）——不计门"}
    md = M5.build_report(snap_dirs=["x"], run_tag="m5", n_snaps=288, results=[a0, st],
                         stale={"n_rounds": 3, "minutes": 15, "spans": []},
                         ledger=[], tol=0.10, rest_days=[])
    assert "实际 0（休/停线）1" in md      # a0 计入 actual_zero 桶
    assert "锚点帧 STALE 1" in md
    assert "锚点帧缺失 0" in md            # a0 未被误判为缺失（旧 bug）
    assert "终判: FAIL" not in md          # 两行均不计门 → 无 FAIL 行 → PASS
    assert "计入门 0 个班次行" in md


def test_build_report_missing_anchor_row():
    miss = {"date": D, "weekday": "周一", "kind": "mid",
            "anchor_ts": f"{D.isoformat()}T21:30:00（缺失）",
            "A": None, "pred": None, "target": None, "actual": None,
            "delta_plan": None, "delta_actual": None, "grade": None,
            "stale": False, "pass": None, "reason": "anchor_missing",
            "note": "锚点帧缺失（进程离线/快照缺帧/漂移超容差）——不计门，查该时段日志"}
    md = M5.build_report(snap_dirs=["x"], run_tag="m5", n_snaps=100, results=[miss],
                         stale={"n_rounds": 0, "minutes": 0, "spans": []},
                         ledger=[], tol=0.10, rest_days=[])
    assert "锚点帧缺失" in md and "（缺失）" in md          # 不留痕静默跳过
    assert "锚点帧缺失明细" in md and "终判: PASS" in md   # 缺失本身不致 FAIL


# ---------- agy 阻断 F3b：多目录合并 + 同前缀 -r 目录点名 ----------

def _write_snap(d, ts, kind="day"):
    import json
    s = _snap(ts, _shift(kind=kind))
    p = d / f"snap_{ts.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(s, ensure_ascii=False, default=str), encoding="utf-8")
    return p


def test_load_snapshots_multi_dir_and_dup(tmp_path):
    a, b = tmp_path / "m5", tmp_path / "m5-r2"
    a.mkdir(); b.mkdir()
    t1 = datetime(2026, 10, 5, 9, 0, tzinfo=TZ)
    t2 = datetime(2026, 10, 5, 9, 5, tzinfo=TZ)
    t3 = datetime(2026, 10, 5, 9, 10, tzinfo=TZ)
    _write_snap(a, t1)
    _write_snap(b, t2)
    _write_snap(b, t3)
    _write_snap(a, t3)                    # 同 ts 重复（两目录都有）→ 先入者胜 + 计数
    snaps, n_dup = M5.load_snapshots([str(a), str(b)])
    assert len(snaps) == 3 and n_dup == 1


def test_find_sibling_run_dirs(tmp_path):
    base = tmp_path / "snapshots"
    (base / "m5").mkdir(parents=True)
    (base / "m5-r2").mkdir()
    (base / "m5-r3").mkdir()
    (base / "other").mkdir()
    # 只传 m5 → 点名 m5-r2/m5-r3（other 不相关）
    assert M5.find_sibling_run_dirs([str(base / "m5")]) == [
        str(base / "m5-r2"), str(base / "m5-r3")]
    # 全部传入 → 无点名
    assert M5.find_sibling_run_dirs(
        [str(base / "m5"), str(base / "m5-r2"), str(base / "m5-r3")]) == []
    # 只传 m5-r2 → 不再点名 m5 本体（只认 -rN 形态）
    assert M5.find_sibling_run_dirs([str(base / "m5-r2")]) == []
