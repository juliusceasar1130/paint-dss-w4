# -*- coding: utf-8 -*-
"""W4.4h 班次型档净延误单测（TDD RED；规格 = 2026-10-07 用户拍板 + 任务单 paint-dss W4.4h）。

覆盖：entry_shift_of 固定带归属边界（DAY [04:45,16:30) / MID [16:30,04:45)）/
resolve_expected_delay_shift 三层回退 + 双人群双门槛 + 不跨班次借行 /
DSS_SHIFT_TIER_DELAY 开关（默认 off = 零漂移 / on + entry_shift → shift 档 /
on 缺行·门槛不过·entry_shift=None → 回退共享行 / 37 站网关恒 0）/
estimate_for_replay 透传（G1 同引擎 parity）。
"""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from spike_w2 import eta_v2

ALL = eta_v2.ALL
T0 = 1_700_000_000  # 任意锚点 epoch 秒
CST = ZoneInfo("Asia/Shanghai")


def _tables(**kw):
    base = {"rem": {}, "loop_rem": {}, "loop_dur": {}, "takt": {},
            "fut_week": {}, "fut_all": {}, "cohort": {}, "lineprob": {},
            "quality": {}, "expected_delay": {}, "expected_delay_shift": {}}
    base.update(kw)
    return base


def _env_clear(monkeypatch):
    monkeypatch.delenv("DSS_SHIFT_TIER_DELAY", raising=False)
    monkeypatch.setenv("DSS_DUAL_RING", "1")


# ================= entry_shift_of：固定带归属 =================

@pytest.mark.parametrize("hm, want", [
    ("04:44", "MID"), ("04:45", "DAY"), ("05:00", "DAY"), ("12:00", "DAY"),
    ("16:29", "DAY"), ("16:30", "MID"), ("23:59", "MID"), ("00:00", "MID"),
    ("02:30", "MID"), ("03:59", "MID"),
])
def test_entry_shift_bands(hm, want):
    h, m = map(int, hm.split(":"))
    ts = datetime(2026, 9, 7, h, m, 0, tzinfo=CST)
    assert eta_v2.entry_shift_of(ts) == want


# ================= resolve_expected_delay_shift：三层回退 + 双门槛 =================

def test_shift_delay_model_row_p50():
    t = _tables(expected_delay_shift={('21IS565', 'YM33J-', 'DAY'): (9999, 1500, 40, 40)})
    assert eta_v2.resolve_expected_delay_shift(t, '21IS565', 'YM33J-', 'DAY') == 1500


def test_shift_delay_prefix_fallback_no_cross_shift():
    t = _tables(expected_delay_shift={('21IS565', 'YM', 'DAY'): (0, 800, 50, 60)})
    assert eta_v2.resolve_expected_delay_shift(t, '21IS565', 'YM33J-', 'DAY') == 800
    # 班次不跨借：MID 无行 → None（调用方回退共享行，不拿 DAY 行充数）
    assert eta_v2.resolve_expected_delay_shift(t, '21IS565', 'YM33J-', 'MID') is None


def test_shift_delay_all_fallback_and_n_gate():
    t = _tables(expected_delay_shift={
        ('21IS565', 'YM33J-', 'DAY'): (0, 100, 29, 999),   # n_fut < 30 → 降层
        ('21IS565', 'YM', 'DAY'): (0, 200, 999, 29),       # n_clean < 30 → 降层
        ('21IS565', ALL, 'DAY'): (0, 300, 40, 40),
    })
    assert eta_v2.resolve_expected_delay_shift(t, '21IS565', 'YM33J-', 'DAY') == 300


def test_shift_delay_missing_table_none():
    assert eta_v2.resolve_expected_delay_shift(_tables(), '21IS565', 'YM33J-', 'DAY') is None


# ================= DSS_SHIFT_TIER_DELAY 开关 =================

def test_switch_default_off_zero_drift(monkeypatch):
    # 缺省 0（未设置）：shift 行存在 + entry_shift 已传 → 仍消费共享行（与 W4.4h 前逐位一致）
    _env_clear(monkeypatch)
    assert eta_v2.shift_tier_delay_enabled() is False
    t = _tables(
        rem={('21IS565', 'YM33J-'): (10000, 11000, 12000, 100)},
        expected_delay={('21IS565', 'YM33J-'): (0, 1500, 40, 40)},
        expected_delay_shift={('21IS565', 'YM33J-', 'DAY'): (0, 999, 40, 40)},
    )
    eta, _ = eta_v2.estimate_event_unified(
        T0, '21IS565', '0', '0', 'YM33J-', '2026-W34', t, mode='shift', entry_shift='DAY')
    assert eta == T0 + 11000 + 1500  # 共享行 p50，shift 行未消费


def test_switch_on_uses_shift_tier(monkeypatch):
    _env_clear(monkeypatch)
    monkeypatch.setenv("DSS_SHIFT_TIER_DELAY", "1")
    t = _tables(
        rem={('21IS565', 'YM33J-'): (10000, 11000, 12000, 100)},
        expected_delay={('21IS565', 'YM33J-'): (0, 1500, 40, 40)},
        expected_delay_shift={('21IS565', 'YM33J-', 'DAY'): (0, 999, 40, 40),
                              ('21IS565', 'YM33J-', 'MID'): (0, 777, 40, 40)},
    )
    eta_d, _ = eta_v2.estimate_event_unified(
        T0, '21IS565', '0', '0', 'YM33J-', '2026-W34', t, mode='shift', entry_shift='DAY')
    assert eta_d == T0 + 11000 + 999
    eta_m, _ = eta_v2.estimate_event_unified(
        T0, '21IS565', '0', '0', 'YM33J-', '2026-W34', t, mode='shift', entry_shift='MID')
    assert eta_m == T0 + 11000 + 777


def test_switch_on_n_below_gate_falls_back(monkeypatch):
    # on + 行存在但 n_fut < 30 → 行不成立 → 回退共享行
    _env_clear(monkeypatch)
    monkeypatch.setenv("DSS_SHIFT_TIER_DELAY", "1")
    t = _tables(
        rem={('21IS565', 'YM33J-'): (10000, 11000, 12000, 100)},
        expected_delay={('21IS565', 'YM33J-'): (0, 1500, 40, 40)},
        expected_delay_shift={('21IS565', 'YM33J-', 'DAY'): (0, 999, 29, 40)},
    )
    eta, _ = eta_v2.estimate_event_unified(
        T0, '21IS565', '0', '0', 'YM33J-', '2026-W34', t, mode='shift', entry_shift='DAY')
    assert eta == T0 + 11000 + 1500


def test_switch_on_entry_shift_none_uses_shared(monkeypatch):
    # on 但调用方未传 entry_shift（None）→ 共享行，零漂移
    _env_clear(monkeypatch)
    monkeypatch.setenv("DSS_SHIFT_TIER_DELAY", "1")
    t = _tables(
        rem={('21IS565', 'YM33J-'): (10000, 11000, 12000, 100)},
        expected_delay={('21IS565', 'YM33J-'): (0, 1500, 40, 40)},
        expected_delay_shift={('21IS565', 'YM33J-', 'DAY'): (0, 999, 40, 40)},
    )
    eta, _ = eta_v2.estimate_event_unified(
        T0, '21IS565', '0', '0', 'YM33J-', '2026-W34', t, mode='shift')
    assert eta == T0 + 11000 + 1500


def test_gateway_station_ignores_shift_tier(monkeypatch):
    # 37 站通用出环网关：delay 恒 0，shift 档同样不生效
    _env_clear(monkeypatch)
    monkeypatch.setenv("DSS_SHIFT_TIER_DELAY", "1")
    t = _tables(
        rem={('K3IS140', 'IM186-'): (9000, 9500, 10000, 200)},
        expected_delay_shift={('K3IS140', 'IM186-', 'DAY'): (0, 888, 40, 40)},
    )
    eta, _ = eta_v2.estimate_event_unified(
        T0, 'K3IS140', '0', '0', 'IM186-', '2026-W34', t, mode='shift', entry_shift='DAY')
    assert eta == T0 + 9500  # shift 档：rem 消费 mean 列（9500）+ 网关 delay 恒 0


def test_switch_strict_01(monkeypatch):
    _env_clear(monkeypatch)
    monkeypatch.setenv("DSS_SHIFT_TIER_DELAY", "2")
    with pytest.raises(ValueError):
        eta_v2.shift_tier_delay_enabled()


# ================= estimate_for_replay 透传（G1 同引擎 parity） =================

def test_replay_dispatch_shift_tier_parity(monkeypatch):
    _env_clear(monkeypatch)
    monkeypatch.setenv("DSS_SHIFT_TIER_DELAY", "1")
    t = _tables(
        rem={('21IS565', 'IM186-'): (10000, 11000, 12000, 100)},
        expected_delay={('21IS565', 'IM186-'): (0, 1500, 40, 40)},
        expected_delay_shift={('21IS565', 'IM186-', 'MID'): (0, 2500, 40, 40)},
    )
    got = eta_v2.estimate_for_replay(T0, '21IS565', '0', 'IM186-',
                                     '2026-W34', t, dual_ring=True, entry_shift='MID')
    want = eta_v2.estimate_event_unified(T0, '21IS565', None, '0', 'IM186-',
                                         '2026-W34', t, mode='shift',
                                         effective_flag='0', entry_shift='MID')
    assert got == want
    assert got[0] == T0 + 11000 + 2500
