# -*- coding: utf-8 -*-
"""shifts.py 纯函数单测（合成数据）。"""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from spike_w2 import shifts

CST = ZoneInfo("Asia/Shanghai")


# --- detect_day_type ---

def test_day_type_11h():
    assert shifts.detect_day_type({"05:00": 50, "06:00": 60}, 40000) == "11h"


def test_day_type_10h_when_05_bin_below_threshold():
    assert shifts.detect_day_type({"05:00": 10, "06:00": 60}, 40000) == "10h"


def test_day_type_8h():
    assert shifts.detect_day_type({"05:00": 5, "06:00": 8, "08:00": 45}, 30000) == "8h"


def test_day_type_shutdown_day():
    assert shifts.detect_day_type({"05:00": 50}, 100) is None          # 全天 <500 事件


def test_day_type_fallback_10h():
    # 全天有量但三个候选桶都 <30（如 04:45 渐进爬坡型）→ 回退最常见 10h，审计表留痕
    assert shifts.detect_day_type({"05:00": 10, "06:00": 10}, 6000) == "10h"


# --- detect_mid_type ---

def test_mid_type_by_last_event():
    assert shifts.detect_mid_type(660) == "11h"     # 16:30+660min = 03:30
    assert shifts.detect_mid_type(600) == "10h"     # = 02:30
    assert shifts.detect_mid_type(510) == "10h"     # 边界：16:30+510min = 01:00
    assert shifts.detect_mid_type(509) == "8h"      # 边界：00:59
    assert shifts.detect_mid_type(480) == "8h"      # = 00:30
    assert shifts.detect_mid_type(300) == "8h"


def test_mid_type_truncated_data():
    assert shifts.detect_mid_type(10) is None       # 回放窗末日数据截断（10min 即停）
    assert shifts.detect_mid_type(None) is None     # 中班段无事件（停产日）


# --- shift_windows ---

def test_shift_windows_10h_day_and_mid():
    ds = shifts.shift_windows(date(2026, 8, 17), "10h", "10h")
    assert len(ds) == 2
    day, mid = ds
    assert day.kind == "day" and day.type == "10h"
    assert day.work_start == datetime(2026, 8, 17, 6, 0, tzinfo=CST)
    assert day.attr_start == datetime(2026, 8, 17, 4, 45, tzinfo=CST)
    assert day.end == datetime(2026, 8, 17, 16, 30, tzinfo=CST)
    assert mid.kind == "mid" and mid.type == "10h"
    assert mid.work_start == datetime(2026, 8, 17, 16, 30, tzinfo=CST)
    assert mid.end == datetime(2026, 8, 18, 3, 0, tzinfo=CST)          # 窗末 03:00


def test_shift_windows_11h_mid_end():
    mid = shifts.shift_windows(date(2026, 8, 17), None, "11h")[0]
    assert mid.end == datetime(2026, 8, 18, 4, 0, tzinfo=CST)          # 窗末 04:00


def test_shift_windows_none_types():
    assert shifts.shift_windows(date(2026, 8, 17), None, None) == []


# --- anchors / gate_anchor ---

def test_anchors_day_10h():
    day = shifts.shift_windows(date(2026, 8, 17), "10h", None)[0]
    a = shifts.anchors(day)
    assert [x.time() for x in a] == [time(9, 0), time(10, 0), time(11, 0), time(12, 0)]
    assert shifts.gate_anchor(day).time() == time(11, 0)


def test_anchors_mid_independent_of_type():
    mid = shifts.shift_windows(date(2026, 8, 17), None, "11h")[0]
    a = shifts.anchors(mid)
    assert [x.time() for x in a] == [time(19, 30), time(20, 30), time(21, 30), time(22, 30)]
    assert shifts.gate_anchor(mid).time() == time(21, 30)


# --- is_complete ---

def test_is_complete():
    rs = datetime(2026, 8, 16, 0, 0, tzinfo=CST)
    re_ = datetime(2026, 9, 19, 23, 59, 59, tzinfo=CST)
    ok = shifts.shift_windows(date(2026, 8, 17), "10h", "10h")
    assert shifts.is_complete(ok[0], rs, re_) is True
    assert shifts.is_complete(ok[1], rs, re_) is True
    # 回放窗首日之前的中班（08-15 归属，attr_start 在窗外）不完整
    # ——日循环自回放首日始，此中班本就不构造；is_complete 是安全网
    edge = shifts.shift_windows(date(2026, 8, 15), None, "10h")[0]
    assert edge.attr_start < rs
    assert shifts.is_complete(edge, rs, re_) is False
    # 回放窗末日之后的窗末
    late = shifts.shift_windows(date(2026, 9, 19), None, "10h")[0]
    assert shifts.is_complete(late, rs, re_) is False


# --- ISO 周 ---

def test_iso_week_label_and_start():
    assert shifts.iso_week_label(date(2026, 8, 16)) == "2026-W33"      # 08-16 为周日
    assert shifts.week_start(date(2026, 8, 16)) == date(2026, 8, 10)
    assert shifts.iso_week_label(date(2026, 9, 19)) == "2026-W38"


def test_week_learning_range():
    lo, hi = shifts.week_learning_range("2026-W33")
    assert lo == date(2026, 6, 15)     # 08-10 周一 往前 8 周（08-10 − 56 天）
    assert hi == date(2026, 8, 9)      # 到该周前一天（无未来泄漏）


# --- normalize_shift_type & schedule 接入 ---

def test_normalize_shift_type():
    assert shifts.normalize_shift_type(11) == "11h"
    assert shifts.normalize_shift_type("10") == "10h"
    assert shifts.normalize_shift_type("8h") == "8h"
    assert shifts.normalize_shift_type("休") is None
    assert shifts.normalize_shift_type("休息") is None
    assert shifts.normalize_shift_type(None) is None


def test_shift_windows_with_schedule():
    d = date(2026, 9, 8)
    sched = {d: {"day": 11, "mid": 10}}
    ds = shifts.shift_windows(d, day_type="10h", mid_type="11h", schedule=sched)
    assert len(ds) == 2
    day, mid = ds
    # 优先采用排班表中的 11 和 10，覆盖传入的 10h 和 11h
    assert day.type == "11h"
    assert mid.type == "10h"
    assert mid.end == datetime(2026, 9, 9, 3, 0, tzinfo=CST)


def test_shift_windows_schedule_rest_day():
    d = date(2026, 9, 6)
    sched = {d: {"day": "休", "mid": "休"}}
    ds = shifts.shift_windows(d, day_type="10h", mid_type="10h", schedule=sched)
    assert ds == []  # 休息日无班次

