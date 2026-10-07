# -*- coding: utf-8 -*-
"""eta.py 纯逻辑单元测试（合成数据）。"""
from spike_w1 import eta


def _tables(rem=None, loop_rem=None, fut=None, loop_dur=None, takt=None):
    return {"rem": rem or {}, "loop_rem": loop_rem or {}, "fut": fut or {},
            "loop_dur": loop_dur or {}, "takt": takt or {}}


def test_resolve_fallback_chain():
    table = {("s", "m1"): (100, 110, 120, 50), ("s", "ALL"): (200, 210, 220, 500)}
    assert eta.resolve(table, ("s",), "m1") == 100            # 车型行 n>=30 命中
    assert eta.resolve(table, ("s",), "m_unknown") == 200     # 缺车型行 → ALL
    table2 = {("s", "m1"): (100, 110, 120, 29), ("s", "ALL"): (200, 210, 220, 500)}
    assert eta.resolve(table2, ("s",), "m1") == 200           # 车型行 n<30 → ALL
    assert eta.resolve({("s", "m1"): (100, 110, 120, 29)}, ("s",), "m1") is None  # 无 ALL 且 n<30
    assert eta.resolve({}, ("s",), "m1") is None              # 全缺


def test_direct_main_and_model_fallback():
    events = [eta.Event(0, "s", "0"), eta.Event(100, "EXIT", "0")]
    rem = {("s", "m1"): (500, 520, 600, 50), ("s", "ALL"): (800, 830, 900, 500)}
    assert eta.estimate_vehicle(events, 100, _tables(rem=rem), "m1")[0].eta_direct == 500
    assert eta.estimate_vehicle(events, 100, _tables(rem=rem), "m_rare")[0].eta_direct == 800
    # ALL 也缺 → 无覆盖
    assert eta.estimate_vehicle(events, 100, _tables(rem={("s", "m1"): (500, 520, 600, 50)}),
                                "m_rare")[0].eta_direct is None


def test_loop_flag_uses_loop_distribution_not_main():
    events = [eta.Event(0, "s1", "1"), eta.Event(1000, "EXIT", "0")]
    tables = _tables(
        rem={("s1", "m"): (500, 520, 600, 50)},          # 主分布存在但不得用
        loop_rem={("s1", "1", "m"): (900, 950, 1000, 40)},
        fut={("s1", "m"): (0.5, 0.0, 50)},               # 高 p 也不得补偿（已在环内）
        loop_dur={("1", "m"): (1000, 40)},
    )
    out = eta.estimate_vehicle(events, 1000, tables, "m")
    assert out[0].eta_direct == 900                       # t + P50(环) = 0 + 900


def test_pre_decision_compensation():
    events = [eta.Event(0, "up", "0"), eta.Event(100, "late", "0"), eta.Event(500, "EXIT", "0")]
    tables = _tables(
        rem={("up", "m"): (500, 520, 700, 50), ("late", "m"): (400, 410, 500, 50)},
        fut={("up", "m"): (0.2, 0.1, 50), ("late", "m"): (0.0, 0.0, 50)},
        loop_dur={("1", "m"): (3600, 40), ("2", "m"): (1800, 30)},
    )
    out = eta.estimate_vehicle(events, 500, tables, "m")
    # up: t=0 + 500 + 0.2*3600 + 0.1*1800 = 1400
    assert out[0].eta_direct == 1400
    # late（决策站下游 p≈0）: t=100 + 400，无补偿
    assert out[1].eta_direct == 500


def test_pre_decision_compensation_4tuple_loop_dur():
    # W4.2a 解包回归留痕（docs/15 W4.2a / agy 建议项 4）：loop_dur 行形状 2→4 元组
    # (l, l_mean, l_p90, n)——resolve 取 row[0]=l / row[-1]=n，消费端零改动钉死。
    events = [eta.Event(0, "up", "0"), eta.Event(500, "EXIT", "0")]
    tables = _tables(
        rem={("up", "m"): (500, 520, 700, 50)},
        fut={("up", "m"): (0.2, 0.1, 50)},
        loop_dur={("1", "m"): (3600, 4000, 9000, 40), ("2", "m"): (1800, 2000, 5000, 30)},
    )
    out = eta.estimate_vehicle(events, 500, tables, "m")
    assert out[0].eta_direct == 500 + 0.2 * 3600 + 0.1 * 1800   # row[0]=l


def test_pre_decision_compensation_4tuple_low_n_fallback():
    # 4 元组下 n（row[-1]）<30 仍按不可用处理（行尾 n 语义不变）
    events = [eta.Event(0, "up", "0"), eta.Event(500, "EXIT", "0")]
    tables = _tables(
        rem={("up", "m"): (500, 520, 700, 50)},
        fut={("up", "m"): (0.2, 0.1, 50)},
        loop_dur={("1", "m"): (3600, 4000, 9000, 5)},
    )
    out = eta.estimate_vehicle(events, 500, tables, "m")
    assert out[0].eta_direct == 500                            # l1=0（n<30 回退缺行）


def test_eta_header_documents_4tuple():
    assert "(l, l_mean, l_p90, n)" in eta.__doc__


def test_sum_median_underestimates_vs_mean():
    # 中位数不可加演示：每段 P50=100 但均值 600（右偏排队段），实际 600s/段
    stations = ["a", "b", "c", "d", "e"]
    events = [eta.Event(600 * i, s, "0") for i, s in enumerate(stations)]
    takt = {("a", "b", "m"): (100, 600, 900, 50), ("b", "c", "m"): (100, 600, 900, 50),
            ("c", "d", "m"): (100, 600, 900, 50), ("d", "e", "m"): (100, 600, 900, 50)}
    tables = _tables(rem={("a", "m"): (2500, 2400, 2600, 50)}, takt=takt)
    out = eta.estimate_vehicle(events, 3000, tables, "m")
    e0 = out[0]
    assert e0.eta_sum_median == 400                       # 4×P50，低估 2600s
    assert e0.eta_sum_mean == 2400                        # 4×均值
    assert abs(e0.eta_sum_median - 3000) > abs(e0.eta_sum_mean - 3000) > abs(e0.eta_direct - 3000)


def test_sum_estimators_all_none_when_segment_missing():
    events = [eta.Event(0, "a", "0"), eta.Event(100, "b", "0"), eta.Event(200, "EXIT", "0")]
    tables = _tables(
        rem={("a", "m"): (100, 110, 120, 50), ("b", "m"): (80, 85, 90, 50)},
        takt={("a", "b", "m"): (50, 60, 70, 50)})          # (b, EXIT) 段缺失
    out = eta.estimate_vehicle(events, 200, tables, "m")
    assert all(e.eta_sum_median is None for e in out)     # 整车 None（样本可比性）
    assert all(e.eta_sum_mean is None for e in out)
    # 直接估计不受影响：ETA 为绝对时刻 = t + 剩余估计（100 = 0+100；180 = 100+80）
    assert out[0].eta_direct == 100 and out[1].eta_direct == 180


def test_exit_event_not_in_sample():
    events = [eta.Event(0, "a", "0"), eta.Event(100, "b", "0"), eta.Event(200, "EXIT", "0")]
    tables = _tables(rem={("a", "m"): (100, 110, 120, 50), ("b", "m"): (80, 85, 90, 50)})
    out = eta.estimate_vehicle(events, 200, tables, "m")
    assert len(out) == 2                                   # 下线事件本身不进样本
    assert out[0].remaining_actual == 200
    assert out[1].remaining_actual == 100
