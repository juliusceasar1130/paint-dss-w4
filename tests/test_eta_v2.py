# -*- coding: utf-8 -*-
"""eta_v2.py 纯逻辑单测（合成数据）。"""
from spike_w2 import eta_v2

W1 = "2026-W33"
W2 = "2026-W34"
ALL = eta_v2.ALL


def _tables(**kw):
    base = {"rem": {}, "loop_rem": {}, "loop_dur": {}, "takt": {},
            "fut_week": {}, "fut_all": {}, "cohort": {}, "lineprob": {}, "quality": {}}
    base.update(kw)
    return base


# --- resolve_main（W1 两级回退语义不变） ---

def test_resolve_main_fallback():
    t = {("s", "m1"): (100, 110, 120, 50), ("s", ALL): (200, 210, 220, 500)}
    assert eta_v2.resolve_main(t, "s", "m1") == 100
    assert eta_v2.resolve_main(t, "s", "m_rare") == 200
    assert eta_v2.resolve_main({("s", "m1"): (100, 110, 120, 29)}, "s", "m1") is None
    assert eta_v2.resolve_main({}, "s", "m1") is None


# --- ② 周度 p 回退链 ---

def test_resolve_p_weekly_preferred():
    fw = {("s", "m1", W1): (0.5, 0.1, 60)}
    fa = {("s", "m1"): (0.2, 0.1, 1000)}
    assert eta_v2.resolve_p(fw, fa, "s", "m1", W1) == (0.5, 0.1)


def test_resolve_p_weekly_low_n_falls_back_to_all():
    fw = {("s", "m1", W1): (0.5, 0.1, 49)}        # n<50 → 弃
    fa = {("s", "m1"): (0.2, 0.1, 1000)}
    assert eta_v2.resolve_p(fw, fa, "s", "m1", W1) == (0.2, 0.1)


def test_resolve_p_model_fallback_to_all():
    fw = {("s", ALL, W1): (0.3, 0.05, 200)}
    assert eta_v2.resolve_p(fw, {}, "s", "m_rare", W1) == (0.3, 0.05)


def test_resolve_p_missing():
    assert eta_v2.resolve_p({}, {}, "s", "m", W1) == (0.0, 0.0)


# --- ① cohort 混合 ---

def test_cohort_mix_weighted():
    coh = {("s", "08", "m1"): (100, 105, 120, 50), ("s", "09", "m1"): (200, 205, 220, 50)}
    lp = {("m1", W1): (0.6, 1000)}
    assert eta_v2.resolve_cohort_p50(coh, "s", "m1", lp, W1) == 140.0   # 0.6*100+0.4*200


def test_cohort_lineprob_week_fallback_all():
    coh = {("s", "08", "m1"): (100, 105, 120, 50), ("s", "09", "m1"): (200, 205, 220, 50)}
    lp = {("m1", eta_v2.WEEK_ALL): (0.5, 1000)}
    assert eta_v2.resolve_cohort_p50(coh, "s", "m1", lp, W1) == 150.0


def test_cohort_model_fallback_all_row():
    coh = {("s", "08", ALL): (100, 105, 120, 500), ("s", "09", ALL): (300, 305, 320, 500)}
    lp = {(ALL, W1): (0.25, 1000)}
    assert eta_v2.resolve_cohort_p50(coh, "s", "m_rare", lp, W1) == 250.0  # .25*100+.75*300


def test_cohort_missing_returns_none():
    assert eta_v2.resolve_cohort_p50({}, "s", "m1", {("m1", W1): (0.5, 1000)}, W1) is None
    assert eta_v2.resolve_cohort_p50(
        {("s", "08", "m1"): (100, 105, 120, 50)}, "s", "m1", {("m1", W1): (0.5, 1000)}, W1) is None
    assert eta_v2.resolve_cohort_p50(
        {("s", "08", "m1"): (100, 105, 120, 50), ("s", "09", "m1"): (200, 205, 220, 50)},
        "s", "m1", {("m1", W1): (0.5, 99)}, W1) is None    # 线概率 n<100 弃


# --- estimate_event 主路径 ---

def test_event_flag1_uses_loop_distribution_no_comp():
    tables = _tables(
        rem={("s", "m"): (500, 520, 600, 50)},
        loop_rem={("s", "1", "m"): (900, 950, 1000, 40)},
        fut_week={("s", "m", W1): (0.9, 0.0, 200)},     # 高 p 也不得补偿（已在环内）
        fut_all={("s", "m"): (0.9, 0.0, 1000)},
        loop_dur={("1", "m"): (3600, 40)},
    )
    eta, used = eta_v2.estimate_event(0, "s", "1", "m", W1, tables)
    assert eta == 900 and used is False


def test_event_flag0_main_plus_weekly_comp():
    tables = _tables(
        rem={("s", "m"): (500, 520, 600, 50)},
        fut_week={("s", "m", W1): (0.2, 0.1, 200)},
        loop_dur={("1", "m"): (3600, 40), ("2", "m"): (1800, 30)},
    )
    eta, used = eta_v2.estimate_event(0, "s", "0", "m", W1, tables)
    assert eta == 0 + round(500 + 0.2 * 3600 + 0.1 * 1800)   # = 1400
    assert used is False


def test_event_comp_excl_kills_compensation():
    tables = _tables(
        rem={("s", "m"): (500, 520, 600, 50)},
        fut_week={("s", "m", W1): (0.9, 0.0, 200)},
        loop_dur={("1", "m"): (3600, 40)},
    )
    eta_on, _ = eta_v2.estimate_event(0, "s", "0", "m", W1, tables,
                                      comp_excl=frozenset())
    eta_off, _ = eta_v2.estimate_event(0, "s", "0", "m", W1, tables,
                                       comp_excl=frozenset({("m", "s")}))
    assert eta_on == round(500 + 0.9 * 3600)
    assert eta_off == 500                                  # ⑥b：免补偿


def test_event_cohort_mix_preferred_over_main():
    tables = _tables(
        rem={("s", "m"): (800, 810, 900, 50)},             # W1 主分布 800
        cohort={("s", "08", "m"): (100, 105, 120, 50),
                ("s", "09", "m"): (200, 205, 220, 50)},
        lineprob={("m", W1): (0.5, 1000)},
        fut_all={("s", "m"): (0.0, 0.0, 1000)},
    )
    eta, used = eta_v2.estimate_event(0, "s", "0", "m", W1, tables)
    assert used is True and eta == round(0.5 * 100 + 0.5 * 200)   # = 150


def test_event_cohort_missing_falls_back_to_main():
    tables = _tables(rem={("s", "m"): (800, 810, 900, 50)},
                     fut_all={("s", "m"): (0.0, 0.0, 1000)})
    eta, used = eta_v2.estimate_event(0, "s", "0", "m", W1, tables)
    assert used is False and eta == 800


def test_event_no_coverage():
    tables = _tables()
    assert eta_v2.estimate_event(0, "s", "0", "m", W1, tables)[0] is None


# --- ⑤ 质量标记 ---

def test_quality_mark():
    q = {("m", "a"): "low_n", ("m", "b"): "mixed", ("m", "c"): "both"}
    assert eta_v2.quality_mark(q, "m", "a") == "low_n"
    assert eta_v2.quality_mark(q, "m", "z") == "ok"          # 未标记默认 ok
