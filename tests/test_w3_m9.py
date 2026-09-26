# -*- coding: utf-8 -*-
"""T3.3 M9 门判纯函数（docs/04 §5；2026-09-25 拍板修正：按 alpha_mode 分判）。

全离线：合成 14 班（7 日 + 7 中）驱动 gates()，无 DB。
拍板（用户 2026-09-25 + agy 独立审核）：生产默认 alpha_mode="off"——
  off 模式：G2 = 全厂 off 5h 中位 |err%| ≤ 10%（绝对线）；
           G3 = True（α 未启用 → 定义上「不劣于静态/off」）；
           G4 = False（α 未启用 → 不严格优）→ main 侧 G4 软化处置（G1–G3 达成且非 DSS_G4_HARD=1，不阻断）
  on  模式：G2 = on 中位 ≤ 10%；G3 = on ≤ off；G4 = 中 on < 中 off（B5 原口径）
G1 两模式不变（引擎一致性，α=off 侧）。

5h 锚点值（每行一班，actual 均 600）：
  static=604（err 0.667%）、off=606（err 1.0%）、on: 日 601（0.167%）/ 中 602（0.333%）
  G1_a: |606−604|=2 ≤ p3(3)+2 ✓  G1_b: |1.0−0.667|=0.333pp ≤ 0.5pp ✓  G1_c: 同 ✓
  off 模式：G2 = 1.0% ≤ 10% ✓   G3 = True   G4 = False（软化路径）
  on  模式：G2 = (0.167+0.333)/2 = 0.25% ≤ 10% ✓  G3 = 0.25 ≤ 1.0 ✓  G4 = 0.333 < 1.0 ✓
"""
from pathlib import Path
import importlib.util


def _load_m9():
    p = Path(__file__).resolve().parent.parent / "spike_w3" / "02_shadow_replay.py"
    spec = importlib.util.spec_from_file_location("w3_shadow_replay", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _rows(static=604, off=606, on_day=601, on_mid=602, p3=3,
          mid_static=None, mid_off=None):
    """14 班（7 日 + 7 中），actual 均 600。"""
    rows = []
    for i in range(7):
        rows.append({"shift_id": f"d{i}", "kind": "day", "actual": 600,
                     "pred_static": static, "pred_roll_off": off,
                     "pred_roll_on": on_day, "p3_inproc": p3})
    for i in range(7):
        rows.append({"shift_id": f"m{i}", "kind": "mid", "actual": 600,
                     "pred_static": mid_static if mid_static is not None else static,
                     "pred_roll_off": mid_off if mid_off is not None else off,
                     "pred_roll_on": on_mid, "p3_inproc": p3})
    return rows


def test_gate_logic_on_synthetic():
    m = _load_m9()
    gates = m.gates

    # ============ alpha_mode="off"（默认：生产默认，M9 门基线 = off）============
    g = gates(_rows())
    assert g["G1"] and g["G2"] and g["G3"] and not g["G4"], g
    assert g["detail"]["alpha_mode"] == "off"
    # 细节字段齐全（报告用）
    for k in ("median_err_static", "median_err_off", "median_err_on",
              "median_err_on_mid", "median_err_off_mid",
              "g1_per_shift_bad", "g1_cap_bad", "g1_full_pp", "g1_mid_pp"):
        assert k in g["detail"], k

    # G1_a 翻转：单班 |roll−static| = 6 > p3+2=5（中位不变 → G1_b/c 仍过）
    rows = _rows()
    rows[0] = {**rows[0], "pred_roll_off": 610}
    g = gates(rows)
    assert not g["G1"] and g["G2"] and g["G3"] and not g["G4"], g
    assert len(g["detail"]["g1_per_shift_bad"]) == 1

    # G1 上限翻转：p3_inproc=30 > 25 硬上限 → G1 不过（池异常）
    rows = _rows(off=604)   # 逐班差 0（G1_a 过）
    for r in rows:
        r["p3_inproc"] = 30
    g = gates(rows)
    assert not g["G1"] and g["G2"] and g["G3"] and not g["G4"], g
    assert g["detail"]["g1_cap_bad"], g["detail"]

    # G1_b 翻转（全厂 5h 相对差 > 0.5pp）：static 系统性偏低 6（err 1.0%）vs
    # off err 0.167% → 差 0.833pp；逐班差 5 ≤ 5 仍过
    g = gates(_rows(static=594, off=599))
    assert not g["G1"] and g["G2"] and g["G3"] and not g["G4"], g
    assert g["detail"]["g1_per_shift_bad"] == []
    assert g["detail"]["g1_full_pp"] > 0.005

    # G1_c 翻转（仅中班相对差 > 0.5pp）：中班 static 594 / off 599 → 0.833pp；
    # 全厂中位差 (0.167+1.0)/2 vs (0.667+1.0)/2 = 0.25pp ≤ 0.5 → G1_b 仍过
    g = gates(_rows(mid_static=594, mid_off=599))
    assert not g["G1"] and g["G2"] and g["G3"] and not g["G4"], g
    assert g["detail"]["g1_full_pp"] <= 0.005
    assert g["detail"]["g1_mid_pp"] > 0.005

    # G2 翻转（off 模式）：off err 13.33% > 10%；static=off → G1 过；
    # G3 = True（α 未启用）
    g = gates(_rows(static=680, off=680))
    assert g["G1"] and not g["G2"] and g["G3"] and not g["G4"], g

    # off 模式不受 on 值影响：on 大幅跑飞（err 50%）→ G2/G3 仍按 off/True
    g = gates(_rows(on_day=900, on_mid=900))
    assert g["G1"] and g["G2"] and g["G3"] and not g["G4"], g

    # ============ alpha_mode="on"（实验开关，B5 原口径）============
    g = gates(_rows(), alpha_mode="on")
    assert g["G1"] and g["G2"] and g["G3"] and g["G4"], g
    assert g["detail"]["alpha_mode"] == "on"

    # G2 翻转（on 模式）：on err 11.67% > 10%（日+中 on 同值）→ G2 不过
    #（G3/G4 连带不过——on 全厂劣于 off、中班非严格优）
    g = gates(_rows(on_day=670, on_mid=670), alpha_mode="on")
    assert g["G1"] and not g["G2"] and not g["G3"] and not g["G4"], g

    # G3 翻转（on 模式）：全厂 on 中位 4.17% > off 中位 1.0%（日班 on 8% 拉高、
    # 中班 on 好 → G4 仍过）；on 中位 4.17% ≤ 10% → G2 仍过
    g = gates(_rows(on_day=648, on_mid=602), alpha_mode="on")
    assert g["G1"] and g["G2"] and not g["G3"] and g["G4"], g

    # G4 翻转（on 模式）：中班 on = off（err 相等，非严格优）→ G4 不过；
    # 全厂 on 中位 (0.167+1.0)/2 = 0.583% ≤ off 1.0% → G3 仍过
    g = gates(_rows(on_mid=606), alpha_mode="on")
    assert g["G1"] and g["G2"] and g["G3"] and not g["G4"], g

    # 非法 mode → ValueError
    try:
        gates(_rows(), alpha_mode="banana")
        assert False, "非法 alpha_mode 应 ValueError"
    except ValueError:
        pass


def test_static_ext_view_selfcontained():
    """G1 对比侧（延展窗）静态视图 = 逐班汇总 + 5h 逐车明细 + p3 清单（agy 审核轮 3 中①：
    正式 JSON 自包含，不依赖 scratch 补件）。"""
    m = _load_m9()
    ext = {"ev_window": ("2026-08-29", "2026-09-20"), "ev_rows": 1005251, "tail_days": 7,
           "p3_candidates": {"b2", "b1"},
           "shifts": {"d0": {"actual": 600, "pred": {5: 610}, "A": {5: 500},
                          "I_bodies": ["b1"], "A_bodies": ["b2"]},
                       "m0": {"actual": 0, "pred": {}, "A": {}}}}
    v = m.static_ext_view(ext)
    assert v["ev_window"] == ("2026-08-29", "2026-09-20")
    assert v["ev_rows"] == 1005251 and v["tail_days"] == 7
    assert v["p3_candidates"] == ["b1", "b2"]      # 排序列表（JSON 可序列化、自包含）
    assert v["shifts"]["d0"] == {"actual": 600, "pred5": 610, "A5": 500,
                                 "I_bodies": ["b1"], "A_bodies": ["b2"]}
    assert v["shifts"]["m0"]["I_bodies"] == []     # 缺键（actual=0 班）容错为空列表
    assert v["shifts"]["m0"]["pred5"] is None
