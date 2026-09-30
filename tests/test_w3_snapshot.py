# -*- coding: utf-8 -*-
"""T6.2（docs/07 v1.2 §2.2）：5min JSON 快照 —— 原子写 / schema 校验 / tag 防覆盖。

口径：
- 文件名 snap_{ts紧凑}.json（ts = 游标/轮询时刻 aware → 20260909T050000）。
- 原子写 = tmp（同目录 .tmp 前缀）→ os.replace；不残留 tmp。
- tag 防覆盖（F-LOW-01）：启动检测目标目录已有 snap_* → -r2/-r3 递增 + 返回注记。
- validate_snapshot = 结构校验（键/类型/枚举/可空性/恒等式），G1 门逐文件调用。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from spike_w3.common import DSSConfig
from spike_w3.dashboard import (resolve_run_tag, validate_snapshot,
                                write_snapshot)

TZ = ZoneInfo("Asia/Shanghai")
CFG = DSSConfig()
TS = datetime(2026, 9, 9, 5, 0, tzinfo=TZ)


def _valid_ctx() -> dict:
    return {
        "ts": TS.isoformat(),
        "week": "2026-W37",
        "weekday": "周三",
        "mode": "replay",
        "next_shift": None,
        "pool": {"size": 3, "flag_dist": {"0": 1, "1": 1, "2": 1},
                 "inproc_n": 1, "bottleneck_n": 1, "no_eta_n": 1,
                 "bottleneck_flag_dist": {"1": 1, "2": 0},
                 "bottleneck_sections": {"12IS015": 1}},
        "pred": {
            "shift": {"shift_sid": "2026-09-09_day", "kind": "day", "type": "11h",
                      "attr_hm": "04:45", "end_hm": "16:30",
                      "A": 87, "pred": 88, "target": 478,
                      "delta": 88 / 478 - 1, "grade": None,
                      "anchor_state": "pre", "anchor_in_min": 180,
                      "anchor_first_hm": "08:00"},
            "day_total": {"A_all": None, "pred": None, "target": 956,
                          "delta": None, "grade": None},
        },
        "alpha": {"mode": "off", "value": 1.0},
        "alert_state": {"2026-09-09_day": {"current_level": "green",
                                           "pending_level": None,
                                           "pending_min": None}},
        "alerts": [],
        "guard": {"status": "NORMAL", "label": "数据新鲜", "lag_min": 2.0,
                  "is_anomaly": False, "diagnose": None,
                  "diagnose_note": "replay 模式: ETL 诊断不适用（时间穿透）"},
        "data": {"max_ts": "2026-09-09T04:58:00+08:00", "watermark": 119996978},
    }


# ---------------------------------------------------------------------------

def test_write_snapshot_roundtrip(tmp_path):
    ctx = _valid_ctx()
    p = write_snapshot(ctx, "kw37_0909", tmp_path, TS)
    assert p == tmp_path / "kw37_0909" / "snap_20260909T050000.json"
    assert p.exists()
    obj = json.loads(p.read_text(encoding="utf-8"))
    assert obj["run_tag"] == "kw37_0909"
    obj.pop("run_tag")
    assert obj == ctx
    # 原子写无 tmp 残留
    assert not list((tmp_path / "kw37_0909").glob(".*.tmp"))
    assert not list((tmp_path / "kw37_0909").glob(".tmp*"))


def test_write_snapshot_overwrite_same_ts_ok(tmp_path):
    """同 run 内同 ts 重写 = 幂等覆盖（单进程 5min 一写，无竞争）；跨 run 由 tag 防覆盖拦。"""
    ctx = _valid_ctx()
    p1 = write_snapshot(ctx, "r1", tmp_path, TS)
    p2 = write_snapshot(ctx, "r1", tmp_path, TS)
    assert p1 == p2 and p2.exists()


def test_validate_snapshot_ok(tmp_path):
    p = write_snapshot(_valid_ctx(), "kw37_0909", tmp_path, TS)
    obj = json.loads(p.read_text(encoding="utf-8"))
    assert validate_snapshot(obj) == []


def test_validate_snapshot_bad(tmp_path):
    def _bad(mut):
        c = _valid_ctx()
        mut(c)
        p = write_snapshot(c, "bad", tmp_path, TS)
        obj = json.loads(p.read_text(encoding="utf-8"))
        return validate_snapshot(obj)

    def m1(c):
        del c["alpha"]
    def m2(c):
        c["mode"] = "banana"
    def m3(c):
        c["pred"]["shift"]["grade"] = "red"          # pre 锚点 grade 必须 null
    def m4(c):
        c["pool"]["bottleneck_sections"] = {"X": 99}  # 恒等式：sum != bottleneck_n
    def m5(c):
        c["pred"]["day_total"]["pred"] = 10          # pred 非 null → A_all 非 null
    def m6(c):
        c["guard"]["status"] = "MAYBE"
    def m7(c):
        c["pred"]["shift"]["target"] = 956           # 类型仍 int，值不校验（快照不含计划源）

    assert any("alpha" in e for e in _bad(m1))
    assert any("mode" in e for e in _bad(m2))
    assert any("grade" in e for e in _bad(m3))
    assert any("bottleneck" in e for e in _bad(m4))
    assert any("A_all" in e for e in _bad(m5))
    assert any("status" in e for e in _bad(m6))
    assert _bad(m7) == []                             # 值不校验，只校验结构


def test_resolve_run_tag_auto_and_collision(tmp_path):
    """run_tag 空 → 自动生成；目录已有 snap_* → -r2/-r3 递增 + 注记。"""
    d = tmp_path / "snapshots"
    d.mkdir()
    tag, note = resolve_run_tag(CFG, TS, d)
    assert tag == "20260909-050000" and note is None

    (d / tag).mkdir()
    (d / tag / "snap_20260909T050000.json").write_text("{}", encoding="utf-8")
    tag2, note2 = resolve_run_tag(CFG, TS, d)
    assert tag2 == f"{tag}-r2" and note2 and "r2" in note2

    (d / tag2).mkdir()
    (d / tag2 / "snap_20260909T050005.json").write_text("{}", encoding="utf-8")
    tag3, _ = resolve_run_tag(CFG, TS, d)
    assert tag3 == f"{tag}-r3"


def test_resolve_run_tag_explicit(tmp_path):
    """显式 run_tag（M5 准备 agy 阻断 F3 改语义）：目录已有 snap_* 也幂等追加同目录，
    不落 -r2（文件名自带时间戳不冲撞；重启自愈不落新目录）。"""
    d = tmp_path / "snapshots"
    d.mkdir()
    cfg2 = DSSConfig(run_tag="kw37_0909")
    tag, note = resolve_run_tag(cfg2, TS, d)
    assert tag == "kw37_0909" and note is None
    (d / tag).mkdir()
    (d / tag / "snap_x.json").write_text("{}", encoding="utf-8")
    tag2, note2 = resolve_run_tag(cfg2, TS, d)
    assert tag2 == "kw37_0909" and note2 is None          # 显式 tag：追加，不 -r2
