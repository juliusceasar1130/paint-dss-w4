# -*- coding: utf-8 -*-
"""W4.3a eta_v2 统一在线引擎单测（TDD RED；规格 = docs/15 §0.2 + Doc14 §5 逐字 + 双审修订）。

覆盖：物理常量四集合（逐字钉死）/ resolve_effective_flag 状态机用例矩阵 /
resolve_hierarchical 三层回退 / resolve_expected_delay 专用解析器（显式列索引 + 双人群双门槛）/
estimate_event_unified 分支 A/B（effective_flag 直通、IE14.- 16.5h D14-5、出网关 delay=0、
α 不内嵌、comp_excl 不消费）/ estimate_for_replay 共享分发（DSS_DUAL_RING 门）。
"""
import os

import pytest

from spike_w2 import eta_v2

ALL = eta_v2.ALL
T0 = 1_700_000_000  # 任意锚点 epoch 秒


def _tables(**kw):
    base = {"rem": {}, "loop_rem": {}, "loop_dur": {}, "takt": {},
            "fut_week": {}, "fut_all": {}, "cohort": {}, "lineprob": {},
            "quality": {}, "expected_delay": {}}
    base.update(kw)
    return base


# ================= 物理常量四集合（Doc14 §5 逐字 + 勘误，字节级钉死） =================

UNIV_37 = {
    # 1. 三线报交物理网关组与直接移载站（6）
    '25IS010', '25IS110', '25IS210', '25IS365', '25IS395', '25IS460',
    # 2. 注蜡与总装主干支路 A（8）
    '32IS010', '32IS230', '32IS408', '33IS005', '33IS025',
    '35IS025', '35IS045', '35IS050',
    # 3. 总装并行支路 B（13）
    '32IS015', '32IS130', '32IS406', '34IS230', '34IS340',
    '35IS075', '35IS080', '41IS315',
    '42IS050', '42IS210', '42IS230', '42IS245', '42IS455',
    # 4. 高频交接与出站走廊必经站（10）
    'L3ATK11IS01', 'L3ATK11IS02', 'K3IS055', 'K3IS140', 'K3IS230',
    'K2IS055', 'K2IS075', 'K1IS135', '1J440RB', '09IS360',
}
WHITELIST_10 = {
    # 报交入口簇 6（88.1%）
    '24IS185', '24IS115', '22IS170', '23IS170', '23IS110', '22IS110',
    # 打磨返修线 4（10.8%，含 14IS120）
    '14IS120', '14IS065', '13IS065', '15IS065',
}


def test_universal_exit_37_exact():
    assert eta_v2.UNIVERSAL_EXIT_STATIONS == frozenset(UNIV_37)
    assert len(eta_v2.UNIVERSAL_EXIT_STATIONS) == 37


def test_blacktop_whitelist_10_exact():
    assert eta_v2.BLACKTOP_ONLINE_WHITELIST == frozenset(WHITELIST_10)
    assert len(eta_v2.BLACKTOP_ONLINE_WHITELIST) == 10


def test_spray_queue_blacklist_2_exact():
    assert eta_v2.SPRAY_QUEUE_BLACKLIST == frozenset({'14IS260', '13IS260'})


def test_oven_out_3_exact_20is010_excluded():
    # Doc14 勘误：20IS010 = 2 线烘房储存线入口（紧随 17IS305，中位间隔 46s），非烘房出口
    assert eta_v2.OVEN_OUT_STATIONS == frozenset({'16IS305', '17IS305', '18IS345'})
    assert '20IS010' not in eta_v2.OVEN_OUT_STATIONS


def test_gateway_trio_subset_of_universal_exit():
    assert {'25IS010', '25IS110', '25IS210'} <= eta_v2.UNIVERSAL_EXIT_STATIONS


# ================= resolve_effective_flag（Doc14 §5 状态机用例矩阵） =================

def test_eff_r1_gateway_unfreeze_wins():
    # R1：37 站强制解冻优先于一切信号（含 139=1 粘滞）
    assert eta_v2.resolve_effective_flag('K3IS140', '1', '1', True) == '0'
    assert eta_v2.resolve_effective_flag('25IS010', '1', '2', True) == '0'


def test_eff_r21_monochrome_137_not_1():
    # R2.1：139=2 且 137≠1（单色闪现 4,941 辆）→ '0'；'-'（缺失）同
    assert eta_v2.resolve_effective_flag('24IS185', '0', '2', True) == '0'
    assert eta_v2.resolve_effective_flag('24IS185', '-', '2', True) == '0'


def test_eff_r22_spray_queue_pre_oven_suppressed():
    # R2.2：一喷排队缓存线 + 未出炉 → 坚决静默
    assert eta_v2.resolve_effective_flag('14IS260', '1', '2', False) == '0'
    assert eta_v2.resolve_effective_flag('13IS260', '1', '2', False) == '0'


def test_eff_r22_spray_queue_post_oven_pass():
    # 出炉后喷房队列不再抑制（R2.2 条件 not passed_oven 不成立）
    assert eta_v2.resolve_effective_flag('14IS260', '1', '2', True) == '2'


def test_eff_r23_pre_oven_non_whitelist_suppressed():
    assert eta_v2.resolve_effective_flag('15IS120', '1', '2', False) == '0'


def test_eff_r23_post_oven_non_whitelist_pass():
    # 出炉后白名单外 139=2 通过（R2.3 条件不成立）
    assert eta_v2.resolve_effective_flag('15IS120', '1', '2', True) == '2'


def test_eff_r3_flag1_direct():
    # R3：139=1 直通（无出炉/白名单约束）
    assert eta_v2.resolve_effective_flag('14IS260', '1', '1', False) == '1'
    assert eta_v2.resolve_effective_flag('21IS565', '0', '1', False) == '1'


def test_eff_default_zero():
    for f139 in ('0', '-', '3'):
        assert eta_v2.resolve_effective_flag('21IS565', '1', f139, True) == '0'
        assert eta_v2.resolve_effective_flag('21IS565', '0', f139, False) == '0'


def test_eff_whitelist_pre_oven_blacktop_pass():
    # 白名单站内未出炉黑顶锚点（24IS185 首发 52.6%）→ '2'
    assert eta_v2.resolve_effective_flag('24IS185', '1', '2', False) == '2'


# ================= resolve_hierarchical（车型 → 前缀 → ALL，mode 列） =================

def test_hier_three_tiers_and_n_gate():
    t = {('s', 'YM33J-'): (100.0, 110.0, 120.0, 29),   # n<30 → 弃
         ('s', 'YM'): (200.0, 210.0, 220.0, 40),        # 前缀行
         ('s', ALL): (300.0, 310.0, 320.0, 9000)}
    assert eta_v2.resolve_hierarchical(t, ('s',), 'YM33J-', 'vehicle') == 200.0
    assert eta_v2.resolve_hierarchical(t, ('s',), 'YM33J-', 'shift') == 210.0
    assert eta_v2.resolve_hierarchical(t, ('s',), 'ZZ999-', 'vehicle') == 300.0
    assert eta_v2.resolve_hierarchical(t, ('s',), ALL, 'vehicle') == 300.0


def test_hier_mode_columns():
    t = {('s', 'IM186-'): (100.0, 110.0, 120.0, 50)}
    assert eta_v2.resolve_hierarchical(t, ('s',), 'IM186-', 'vehicle') == 100.0
    assert eta_v2.resolve_hierarchical(t, ('s',), 'IM186-', 'shift') == 110.0


def test_hier_missing_none():
    assert eta_v2.resolve_hierarchical({}, ('s',), 'IM186-', 'vehicle') is None
    assert eta_v2.resolve_hierarchical({('s', ALL): (100.0, 110.0, 120.0, 29)},
                                       ('s',), 'IM186-', 'vehicle') is None


# ================= resolve_expected_delay（专用解析器：显式列索引 + 双门槛） =================

def test_delay_explicit_column_index_per_mode():
    # 行序 = (delay_mean_s, delay_p50_s, n_fut, n_clean)：两档均消费 row[1]=delay_p50_s
    # （W4.4b 修订 2026-10-06：shift 档原 row[0]=mean 列，G2 阈值计数实测证伪「班次级加法无偏」
    # ——cc/agy-2 双审核一致判定；mean 列留表内仅诊断用途）
    t = _tables(expected_delay={('s', 'YM33J-'): (5000, 3000, 100, 100)})
    assert eta_v2.resolve_expected_delay(t, 's', 'YM33J-', 'shift') == 3000
    assert eta_v2.resolve_expected_delay(t, 's', 'YM33J-', 'vehicle') == 3000


def test_delay_dual_population_dual_gate():
    t = _tables(expected_delay={
        ('s', 'YM33J-'): (5000, 3000, 29, 100),    # n_fut<30 → 弃
        ('s', 'YM'): (4000, 2000, 100, 29),        # n_clean<30 → 弃
        ('s', ALL): (1000, 800, 100, 100),         # 双达 → 用
    })
    assert eta_v2.resolve_expected_delay(t, 's', 'YM33J-', 'vehicle') == 800
    t2 = _tables(expected_delay={('s', 'YM33J-'): (5000, 3000, 30, 30)})
    assert eta_v2.resolve_expected_delay(t2, 's', 'YM33J-', 'vehicle') == 3000


def test_delay_hierarchy_and_missing():
    t = _tables(expected_delay={('s', 'YM'): (4000, 2000, 100, 100)})
    assert eta_v2.resolve_expected_delay(t, 's', 'YM33J-', 'vehicle') == 2000
    assert eta_v2.resolve_expected_delay(_tables(), 's', 'YM33J-', 'vehicle') is None
    assert eta_v2.resolve_expected_delay(_tables(expected_delay={}), 's', 'YM33J-', 'vehicle') is None


# ================= estimate_event_unified 分支 A（eff ∈ {1,2}） =================

def test_unified_branch_a_loop_rem_mode_columns():
    t = _tables(loop_rem={('14IS120', '2', 'YM33J-'): (36000, 40000, 44000, 100)})
    eta_v, used = eta_v2.estimate_event_unified(
        T0, '14IS120', '1', '2', 'YM33J-', '2026-W34', t, mode='vehicle')
    assert (eta_v, used) == (T0 + 36000, False)
    eta_s, _ = eta_v2.estimate_event_unified(
        T0, '14IS120', '1', '2', 'YM33J-', '2026-W34', t, mode='shift')
    assert eta_s == T0 + 40000


def test_unified_branch_a_hier_fallback():
    t = _tables(loop_rem={
        ('14IS120', '2', 'YM33J-'): (10, 20, 30, 29),  # n<30 → 弃
        ('14IS120', '2', 'YM'): (36000, 40000, 44000, 50),
        ('14IS120', '2', ALL): (99999, 99999, 99999, 5000),
    })
    eta_v, _ = eta_v2.estimate_event_unified(
        T0, '14IS120', '1', '2', 'YM33J-', '2026-W34', t, mode='vehicle')
    assert eta_v == T0 + 36000  # 前缀行，非 ALL


def test_unified_branch_a_loop_dur_fallback():
    # loop_rem 缺 → loop_dur L 兜底（vehicle → l=row[0]；shift → l_mean=row[1]）
    t = _tables(loop_rem={}, loop_dur={('2', 'YM33J-'): (49200, 55000, 60000, 500)})
    eta_v, _ = eta_v2.estimate_event_unified(
        T0, '14IS120', '1', '2', 'YM33J-', '2026-W34', t, mode='vehicle')
    assert eta_v == T0 + 49200
    eta_s, _ = eta_v2.estimate_event_unified(
        T0, '14IS120', '1', '2', 'YM33J-', '2026-W34', t, mode='shift')
    assert eta_s == T0 + 55000


def test_unified_branch_a_all_missing_none():
    eta_v, used = eta_v2.estimate_event_unified(
        T0, '14IS120', '1', '2', 'ZZ999-', '2026-W34', _tables(), mode='vehicle')
    assert (eta_v, used) == (None, False)


def test_unified_effective_flag_bypasses_state_machine():
    # 回放直通：raw 137='0'/139='2' 在线状态机判 '0'（R2.1），但净化 flag='2' 直通 → 分支 A
    t = _tables(loop_rem={('24IS185', '2', 'YM33J-'): (36000, 40000, 44000, 100)})
    eta_v, _ = eta_v2.estimate_event_unified(
        T0, '24IS185', '0', '2', 'YM33J-', '2026-W34', t,
        mode='vehicle', effective_flag='2')
    assert eta_v == T0 + 36000
    # 对照：不传 effective_flag → 状态机 R2.1 → '0' → 分支 B（无 rem 行 → None）
    eta_v0, _ = eta_v2.estimate_event_unified(
        T0, '24IS185', '0', '2', 'YM33J-', '2026-W34', t, mode='vehicle')
    assert eta_v0 is None


# ================= estimate_event_unified 分支 B（eff = '0'） =================

def test_unified_branch_b_base_plus_delay_mode_columns():
    t = _tables(
        rem={('21IS565', 'YM33J-'): (10000, 11000, 12000, 100)},
        expected_delay={('21IS565', 'YM33J-'): (2000, 1500, 40, 40)},
    )
    eta_v, used = eta_v2.estimate_event_unified(
        T0, '21IS565', '0', '0', 'YM33J-', '2026-W34', t, mode='vehicle')
    assert (eta_v, used) == (T0 + 10000 + 1500, False)
    eta_s, _ = eta_v2.estimate_event_unified(
        T0, '21IS565', '0', '0', 'YM33J-', '2026-W34', t, mode='shift')
    assert eta_s == T0 + 11000 + 1500  # shift：rem mean 列（11000）+ delay p50 列（1500，W4.4b 修订）


def test_unified_branch_b_gateway_delay_zero():
    # 出网关后（station ∈ 37 站）delay 恒 0——即使 expected_delay 有行
    t = _tables(
        rem={('K3IS140', 'IM186-'): (9000, 9500, 10000, 200)},
        expected_delay={('K3IS140', 'IM186-'): (4000, 3000, 100, 100)},
    )
    # 139=1 粘滞 + 37 站 → R1 解冻 '0' → 分支 B，delay=0
    eta_v, _ = eta_v2.estimate_event_unified(
        T0, 'K3IS140', '1', '1', 'IM186-', '2026-W34', t, mode='vehicle')
    assert eta_v == T0 + 9000


def test_unified_ie14_model_specific_baseline_d14_5():
    # D14-5：IE14.- 过网关后车型专属基线 16.5h，严禁 pooled ALL 2h
    t = _tables(
        rem={('K3IS140', 'IE14.-'): (59400, 59400, 61000, 60),
             ('K3IS140', ALL): (7200, 7200, 8000, 5000)},
        expected_delay={('K3IS140', 'IE14.-'): (3600, 3000, 50, 50)},
    )
    eta_v, _ = eta_v2.estimate_event_unified(
        T0, 'K3IS140', '1', '1', 'IE14.-', '2026-W34', t, mode='vehicle')
    assert eta_v == T0 + 59400  # 车型专属 16.5h（且 37 站 delay=0），绝非 T0 + 7200


def test_unified_branch_b_base_missing_none():
    eta_v, used = eta_v2.estimate_event_unified(
        T0, '21IS565', '0', '0', 'ZZ999-', '2026-W34', _tables(), mode='vehicle')
    assert (eta_v, used) == (None, False)


def test_unified_branch_b_delay_missing_zero():
    # expected_delay 全层缺 → delay=0，ETA 仍出基线
    t = _tables(rem={('21IS565', 'IM186-'): (10000, 11000, 12000, 100)})
    eta_v, _ = eta_v2.estimate_event_unified(
        T0, '21IS565', '0', '0', 'IM186-', '2026-W34', t, mode='vehicle')
    assert eta_v == T0 + 10000


def test_unified_branch_b_cohort_base_then_delay():
    # ① 线别 cohort 混合保留为 base 首选（与旧引擎同机制），delay 叠加其后
    t = _tables(
        cohort={('21IS565', '08', 'IM186-'): (10000, 0, 0, 60),
                ('21IS565', '09', 'IM186-'): (12000, 0, 0, 60)},
        lineprob={('IM186-', '2026-W34'): (0.5, 400)},
        rem={('21IS565', 'IM186-'): (1000, 1000, 1000, 29)},  # n<30，仅当 cohort 缺时
        expected_delay={('21IS565', 'IM186-'): (2000, 1500, 40, 40)},
    )
    eta_v, used = eta_v2.estimate_event_unified(
        T0, '21IS565', '0', '0', 'IM186-', '2026-W34', t, mode='vehicle')
    base = 0.5 * 10000 + 0.5 * 12000  # = 11000
    assert (eta_v, used) == (T0 + int(round(base + 1500)), True)


def test_unified_alpha_not_applied_inside():
    # α 在消费点单次作用于 rem（doc10 L121 / 双审 B10）——引擎内不消费 tables['alpha']
    t = _tables(
        rem={('21IS565', 'IM186-'): (10000, 11000, 12000, 100)},
        alpha=0.5,
    )
    eta_v, _ = eta_v2.estimate_event_unified(
        T0, '21IS565', '0', '0', 'IM186-', '2026-W34', t, mode='shift')
    assert eta_v == T0 + 11000  # 未被 α 缩放


def test_unified_comp_excl_not_consumed():
    # 新引擎不消费 comp_excl（expected_delay 恒消费、无 comp 项；comp_excl 仅旧路径）
    t = _tables(
        rem={('21IS565', 'IM186-'): (10000, 11000, 12000, 100)},
        expected_delay={('21IS565', 'IM186-'): (2000, 1500, 40, 40)},
    )
    eta_v, _ = eta_v2.estimate_event_unified(
        T0, '21IS565', '0', '0', 'IM186-', '2026-W34', t, mode='vehicle',
        comp_excl=frozenset({('IM186-', '21IS565')}))
    assert eta_v == T0 + 10000 + 1500  # delay 照常叠加


def test_unified_default_mode_vehicle():
    t = _tables(
        rem={('21IS565', 'IM186-'): (10000, 11000, 12000, 100)},
        expected_delay={('21IS565', 'IM186-'): (2000, 1500, 40, 40)},
    )
    eta_v, _ = eta_v2.estimate_event_unified(
        T0, '21IS565', '0', '0', 'IM186-', '2026-W34', t)
    assert eta_v == T0 + 10000 + 1500  # 缺省 mode='vehicle' → p50 列


# ================= estimate_for_replay（共享分发，G1 同引擎一致性） =================

def test_replay_dispatch_dual_1_matches_unified(monkeypatch):
    monkeypatch.delenv('DSS_DUAL_RING', raising=False)
    t = _tables(
        rem={('21IS565', 'IM186-'): (10000, 11000, 12000, 100)},
        expected_delay={('21IS565', 'IM186-'): (2000, 1500, 40, 40)},
        fut_week={('21IS565', 'IM186-', '2026-W34'): (0.4, 0.1, 60)},
        loop_dur={('1', 'IM186-'): (50000, 55000, 60000, 200)},
    )
    got = eta_v2.estimate_for_replay(T0, '21IS565', '0', 'IM186-',
                                     '2026-W34', t, frozenset(), dual_ring=True)
    want = eta_v2.estimate_event_unified(
        T0, '21IS565', None, '0', 'IM186-', '2026-W34', t,
        mode='shift', effective_flag='0')
    assert got == want


def test_replay_dispatch_dual_0_matches_old(monkeypatch):
    monkeypatch.delenv('DSS_DUAL_RING', raising=False)
    t = _tables(
        rem={('21IS565', 'IM186-'): (10000, 11000, 12000, 100)},
        fut_week={('21IS565', 'IM186-', '2026-W34'): (0.4, 0.1, 60)},
        loop_dur={('1', 'IM186-'): (50000, 55000, 60000, 200)},
    )
    excl = frozenset({('IM186-', '21IS565')})
    got = eta_v2.estimate_for_replay(T0, '21IS565', '0', 'IM186-',
                                     '2026-W34', t, excl, dual_ring=False)
    want = eta_v2.estimate_event(T0, '21IS565', '0', 'IM186-',
                                 '2026-W34', t, excl)
    assert got == want


def test_replay_dispatch_env_default_on(monkeypatch):
    # D6：DSS_DUAL_RING 缺省 = 1（统一路径）
    monkeypatch.delenv('DSS_DUAL_RING', raising=False)
    t = _tables(rem={('21IS565', 'IM186-'): (10000, 11000, 12000, 100)})
    got = eta_v2.estimate_for_replay(T0, '21IS565', '0', 'IM186-',
                                     '2026-W34', t)
    want = eta_v2.estimate_event_unified(
        T0, '21IS565', None, '0', 'IM186-', '2026-W34', t,
        mode='shift', effective_flag='0')
    assert got == want
    monkeypatch.setenv('DSS_DUAL_RING', '0')
    got0 = eta_v2.estimate_for_replay(T0, '21IS565', '0', 'IM186-',
                                      '2026-W34', t)
    want0 = eta_v2.estimate_event(T0, '21IS565', '0', 'IM186-', '2026-W34', t)
    assert got0 == want0
