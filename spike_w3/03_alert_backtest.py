"""T4.4/T4.5 缺口告警回测驱动（docs/05 §3 T4.4/T4.5；M10 门判 G0–G6）。

用法：conda run --no-capture-output -n paint-dss python spike_w3/03_alert_backtest.py
  - 复用 M9 run_rolling 管线 + alert_hook（02 参数化，默认 None 生产零影响）
  - 告警只出 JSON/MD，不写 dss 表（2026-09-25 拍板⑤）
  - 门判 M10：G1 全天主视图准度（中班 21:30 锚点，中位 ≤5pp + 零红）
    + G1b 引擎 pred == M9 pred_off@5h（14/14）+ G2 level==grade（21 scope-day，
    去抖滞后豁免）+ G3 终局非绿首次进入 ≤3h 锚点（提前量 ≥2h）+ G4 去抖稳定
    （拍板 B 2026-09-26：5h 锚点前 10min 窗口 [anchor−10min, anchor) 无转换；次数降
    观测项 G4_obs）+ G5 自洽恒等式（F4，14 shift 5h）
    + G6 21 scope-day 全覆盖
口径：锚点 = work_start + {3,4,5,6}h（shifts.py）；A 窗 = [attr_start, t]
（日班 attr_start = 04:45 归属窗）；全天实际 = 出口 [D 04:45, 中班窗末)。
产出：output/w3-T4-缺口告警回测.{json,md}
"""
from __future__ import annotations

import csv
import dataclasses
import importlib.util
import json
import statistics
import subprocess
import sys
from datetime import datetime, time as dtime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from spike_w2 import shifts as shifts_mod            # noqa: E402
from spike_w3 import common as w3c                   # noqa: E402
from spike_w3.alert_engine import (Alert, AlertEngine, build_station_section,  # noqa: E402
                                   grade, render_alert_line, split_target)
from spike_w3.predictor import PRODUCT_PREFIX, rem_of                   # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------------
# 排班 + 出车计划（全天总台数，2026-09-25 拍板②A：H 占比拆分）
# ---------------------------------------------------------------------------

def load_plan_with_total(plan_files, d0, d1) -> dict:
    """{date: {'total': int|None, 'day': '11h'|None, 'mid': '11h'|None}}。

    出车计划列 = 全天总台数（F2）；空 = 无计划（休/停产 → 无 scope）。
    窗内缺日期 → KeyError（与 02.load_schedule 同纪律）。"""
    plan: dict = {}
    for p in plan_files:
        with open(p, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                d = datetime.strptime(row["日期"].strip(), "%Y-%m-%d").date()
                total_raw = (row.get("出车计划") or "").strip()
                plan[d] = {"total": int(total_raw) if total_raw else None,
                           "day": shifts_mod.normalize_shift_type(row["日班工时"]),
                           "mid": shifts_mod.normalize_shift_type(row["中班工时"])}
    d = d0
    while d <= d1:
        if d not in plan:
            raise KeyError(f"回放窗 {d} 无排班行（plan_inbox 覆盖不全）")
        d += timedelta(days=1)
    return plan


# ---------------------------------------------------------------------------
# M10 门判纯函数（合成数据可单测；G0 在 main 内以 pytest 子进程核算）
# ---------------------------------------------------------------------------

def gate_g1(rows, max_pp: float = 5.0):
    """G1：7 个全天 scope-day，21:30 锚点 |全天Δ_5h − 全天Δ_终局| 中位 ≤ max_pp
    且 21:30 无全天红。rows: [(day, d5h, dfinal, level_5h)]（Δ 为分数）。"""
    diffs = sorted(abs(a - b) * 100 for _, a, b, _ in rows)
    med = statistics.median(diffs)
    n_red = sum(1 for r in rows if r[3] == "red")
    return (med <= max_pp and n_red == 0), {"median_pp": round(med, 2), "n_red": n_red}


def gate_g1b(engine_pred: dict, m9_pred: dict):
    """G1b：引擎 pred（14 shift 5h 锚点）== M9 JSON pred_off@5h（14/14）。"""
    miss = sorted(sid for sid, v in m9_pred.items()
                  if engine_pred.get(sid) != v)
    return (not miss), miss


def gate_g2(rows, cfg, debounce_min: float | None = None) -> tuple:
    """G2：21 scope-day 5h 锚点 level == grade(Δ)；去抖滞后豁免 =
    pending_level == grade(Δ) 且 pending_age_min < debounce_min（设计内行为）。"""
    dbm = cfg.alert_debounce_min if debounce_min is None else debounce_min
    bad = []
    for r in rows:
        if r["level"] == r["grade"]:
            continue
        if (r.get("pending_level") == r["grade"]
                and r.get("pending_age_min") is not None
                and r["pending_age_min"] < dbm):
            continue
        bad.append(r["sid"])
    return (not bad), bad


def gate_g3(alerts: list, finals: dict) -> tuple:
    """G3：终局非绿 scope-day 首次进入终局档（首个 level==final 的确认 Alert，
    含基线）≤ 3h 锚点（提前量 ≥2h）。finals: {sid: {final_level, anchor3}}。"""
    miss = []
    for sid, f in finals.items():
        t = datetime.fromisoformat(f["anchor3"])
        first = None
        for a in sorted(alerts, key=lambda x: x["t"]):
            if a["sid"] == sid and a["level"] == f["final_level"]:
                first = datetime.fromisoformat(a["t"])
                break
        if first is None or first > t:
            miss.append(sid)
    return (not miss), sorted(miss)


def gate_g4(alerts: list, anchors: dict, min_before_min: int = 10) -> tuple:
    """G4（2026-09-26 用户拍板 B）：5h 锚点前 10min 窗口 [anchor−10min, anchor)
    （半开半闭：t==anchor 的转换 = 锚点时刻生效档，物理状态锚点前已持续 ≥10min，不判）
    内无确认转换（锚点读数稳定）。防 flapping 是 Debouncer 构造性保证（≥10min 持续
    才确认，单测穷尽）；全天确认转换次数降为观测项（gate_detail.G4_obs 全量证据）——
    09-09_mid 型「贴线整晚」（7 次、每次 ≥10min 证据、物理根因 01:50 停线）属数据性质
    非引擎缺陷（agy 关账复审 139 步真库重放逐条验真，2026-09-26）。
    anchors: {sid: iso5h}。"""
    per: dict = {}
    for a in alerts:
        if a["prev_level"] is None:
            continue
        per.setdefault(a["sid"], []).append(datetime.fromisoformat(a["t"]))
    bad = set()
    for sid, ts in per.items():
        if sid in anchors:
            anc = datetime.fromisoformat(anchors[sid])
            if any(anc - timedelta(minutes=min_before_min) <= t < anc for t in ts):
                bad.add(sid)
    return (not bad), sorted(bad)


def gate_g5(rows: list) -> tuple:
    """G5：14 shift 5h 锚点 池产品车 = 在制 + 瓶颈 + no-ETA（精确，F4）
    且 池 n == M9 inproc_5h_n（同池同刻）。"""
    bad = [r["sid"] for r in rows
           if r["pool_n"] != r["hit"] + r["bottleneck"] + r["no_eta"]
           or r["pool_n"] != r["m9_inproc"]]
    return (not bad), bad


def gate_g6(alerts: list, scope_days: list) -> tuple:
    """G6：21 scope-day 全覆盖（每个 sid 至少 1 条 Alert，含基线）。"""
    seen = {a["sid"] for a in alerts}
    miss = sorted(s for s in scope_days if s not in seen)
    return (not miss), miss


# ---------------------------------------------------------------------------
# 回测 hook（每 5min 步：AlertEngine 评估 + 锚点快照 G1b/G2/G5 数据）
# ---------------------------------------------------------------------------

def _pool_view(pool, t, tables, comp_excl, cfg) -> dict:
    """predict() 池部分（无 A SQL；F9 零新增查询）：by_vehicle 直推。"""
    by_vehicle = []
    for body_id, v in pool.snapshot().items():
        if not body_id.startswith(PRODUCT_PREFIX):
            continue
        rem = rem_of(v, t, tables, comp_excl)
        is_stale = (t - v.last_event_ts).total_seconds() > cfg.eta_stale_hours * 3600
        by_vehicle.append({"body_id": body_id, "station": v.station,
                           "flag": v.flag, "rem_sec": rem, "stale": is_stale})
    return {"by_vehicle": by_vehicle}


def alert_to_dict(a) -> dict:
    d = dataclasses.asdict(a)
    d["t"] = d["t"].isoformat()
    return d


def make_hook(plan, schedule, start, end, cfg, section_map, exits,
              tables, comp_excl, alpha: float = 1.0):
    """构建 hook + 快照收集器。返回 (hook, snaps)。
    snaps: {"s5": [shift 5h 锚点快照...], "all5": [全天 5h 锚点快照...],
            "a3": {sid: 3h 锚点 iso}, "alerts": [全量确认 Alert...]}（hook 内累积）。
    锚点 = work_start + h（shifts.py 口径）；A 窗 = [attr_start, t]。"""
    m9 = _load("w3_m9_for_hook", ROOT / "spike_w3" / "02_shadow_replay.py")
    shifts_all = m9._shifts_for_window(
        schedule, start - timedelta(days=1), end + timedelta(days=2))
    engine = AlertEngine(plan, cfg, section_map, alpha=alpha)
    snaps = {"s5": [], "all5": [], "a3": {}, "alerts": []}

    def _scope_snap(A, hit, bot, ne, target, key, cur):
        pred = A + hit
        delta = (pred - target) / target
        deb = engine._deb_for(key)
        pend_age = None
        if deb.pending and deb.pending_since is not None:
            pend_age = round((cur - deb.pending_since).total_seconds() / 60, 1)
        return {"sid": key, "t": cur.isoformat(), "A": A, "pred": pred,
                "pool_n": hit + len(bot) + ne, "hit": hit, "bottleneck": len(bot),
                "no_eta": ne, "target": target, "delta": delta,
                "level": deb.current, "grade": grade(delta, cfg),
                "pending_level": deb.pending if deb.pending is not None else None,
                "pending_age_min": pend_age}

    def hook(cur, pool, conn, tables_, comp_excl_, cfg_m9, active_list):
        active = active_list[0] if active_list else None
        if active is None:
            return []
        pv = _pool_view(pool, cur, tables_, comp_excl_, cfg_m9)
        by_vehicle = pv["by_vehicle"]

        def pred_fn(s, t):
            return pv

        def a_fn(s, t):
            return m9._count_exits_in(exits, s.attr_start.timestamp(),
                                      t.timestamp())

        day_done = [s for s in shifts_all if s.kind == "day" and s.end <= cur]
        alerts = engine.evaluate(cur, [active], day_done, pred_fn, a_fn)
        for a in alerts:
            snaps["alerts"].append(alert_to_dict(a))

        # 锚点快照（work_start + {3,5}h）
        for h in (3, 5):
            if cur != active.work_start + timedelta(hours=h):
                continue
            entry = engine._plan_entry(plan, active.day)
            if entry is None:
                continue
            total, hd, hm = entry
            d_t, m_t = split_target(engine._TYPE_HOURS.get(hd),
                                    engine._TYPE_HOURS.get(hm), total)
            target = d_t if active.kind == "day" else m_t
            if target <= 0:
                continue
            A = a_fn(active, cur)
            hit, bot, ne = engine._split_pool(by_vehicle, cur, active.end, alpha)
            key = f"{active.day.isoformat()}_{active.kind}"
            if h == 5:
                snaps["s5"].append(_scope_snap(A, hit, bot, ne, target, key, cur))
            else:
                snaps["a3"][key] = cur.isoformat()
            # 全天主视图（F10 激活窗内）：A_all 窗 [04:45, t]（日班 attr_start 起）
            if active.kind == "mid" and h == 5:
                dd = [x for x in day_done if x.day == active.day]
                if dd:
                    end_later = max(x.end for x in shifts_all
                                    if x.day == active.day)
                    A_all = a_fn(dd[0], cur)
                    hit2, bot2, ne2 = engine._split_pool(by_vehicle, cur,
                                                         end_later, alpha)
                    e_all = engine._plan_entry(plan, active.day)
                    if e_all is not None:
                        snaps["all5"].append(_scope_snap(
                            A_all, hit2, bot2, ne2, e_all[0],
                            active.day.isoformat(), cur))
        return [alert_to_dict(a) for a in alerts]

    return hook, snaps


# ---------------------------------------------------------------------------
# main（T4.5 执行；门判 M10）
# ---------------------------------------------------------------------------

def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = w3c.load_config()
    conn = w3c.connect()
    m9 = _load("w3_m9_main", ROOT / "spike_w3" / "02_shadow_replay.py")
    w2m = _load("w2_replay_main", ROOT / "spike_w2" / "02_replay_shift.py")
    t0 = datetime.now()
    start, end = m9.M9_START, m9.M9_END
    try:
        plan_files = sorted((ROOT / "plan_inbox").glob("week_plan_*.csv"))
        schedule = m9.load_schedule(plan_files, start.date(), end.date())
        plan = load_plan_with_total(plan_files, start.date(), end.date())

        tables = w2m.load_tables(conn)
        cur = conn.cursor()
        cur.execute('SELECT body_type, station FROM dss.v2_comp_excl')
        comp_excl = frozenset(cur.fetchall())
        cur.close()

        section_map = {r["station"]: r["section"]
                       for r in build_station_section(conn)}
        exits = m9._prefetch_exits(conn, start, end)
        hook, snaps = make_hook(plan, schedule, start, end, cfg, section_map,
                                exits, tables, comp_excl, alpha=1.0)
        rolling = m9.run_rolling(conn, cfg, start, end, schedule, tables,
                                 comp_excl, alert_hook=hook)
        alerts = rolling["alerts"]
        s5, all5 = snaps["s5"], snaps["all5"]

        # ---- G1：全天主视图准度（21:30 锚点）----
        # 终局 Δ = (实际全天出口 [D 04:45, 中班窗末) − 计划 total) / total
        all_rows = []
        shifts_all_main = m9._shifts_for_window(schedule, start, end + timedelta(days=1))
        mids_by_day = {}
        for s in shifts_all_main:
            if s.kind == "mid" and start.date() <= s.day <= end.date():
                mids_by_day[s.day] = s
        for d in sorted(mids_by_day):
            e = plan[d]
            if not e["total"]:
                continue
            s_mid = mids_by_day[d]
            w0 = datetime.combine(d, shifts_mod.ATTR_DAY_START).timestamp()
            actual = m9._count_exits_in(exits, w0, s_mid.end.timestamp())
            dfinal = (actual - e["total"]) / e["total"]
            all_rows.append([d.isoformat(), None, dfinal, None])
        for r_all in all5:
            for row in all_rows:
                if row[0] == r_all["sid"]:
                    row[1], row[3] = r_all["delta"], r_all["level"]
        g1_rows = [tuple(r) for r in all_rows if r[1] is not None]
        g1_ok, g1_detail = gate_g1(g1_rows, max_pp=5.0)

        # ---- G1b：引擎 pred vs M9 pred_off@5h（14 shift）----
        m9_json = json.loads((ROOT / "output" / "w3-影子回放验收.json").read_text(
            encoding="utf-8"))
        m9_preds = {sid: v["pred_off"]["5"] for sid, v in
                    m9_json.get("rolling_shifts", {}).items()}
        eng_preds = {s["sid"]: s["pred"] for s in s5}
        g1b_ok, g1b_miss = gate_g1b(eng_preds, m9_preds)

        # ---- G2：21 scope-day（14 shift + 7 全天）5h 锚点 level vs grade ----
        g2_ok, g2_bad = gate_g2(s5 + all5, cfg)

        # ---- G3：终局非绿 scope-day 首次进入 ≤ 3h 锚点（docs/05 原文范围 = 中班 scope-day；
        # 日班 scope 终局非绿属 H 拆分偏置（F8，全天主视图对照可辨），单列注记不进 G3）----
        finals = {}
        # 中班 scope（H 拆分终局档）
        shift_actual = {}
        for s in shifts_all_main:
            shift_actual[f"{s.day.isoformat()}_{s.kind}"] = m9._count_exits_in(
                exits, s.attr_start.timestamp(), s.end.timestamp())
        for s_row in s5:
            act = shift_actual.get(s_row["sid"])
            if act is None or s_row["target"] <= 0 or not s_row["sid"].endswith("_mid"):
                continue
            lv = grade((act - s_row["target"]) / s_row["target"], cfg)
            if lv != "green":
                a3 = snaps["a3"].get(s_row["sid"])
                if a3:
                    finals[s_row["sid"]] = {"final_level": lv, "anchor3": a3}
        # 日班 scope 终局非绿 → 拆分偏置注记（F8 双轨；全天主视图对照；不进 G3）
        all_df = {r[0]: r[2] for r in all_rows}
        day_bias = []
        for s_row in s5:
            if not s_row["sid"].endswith("_day"):
                continue
            act = shift_actual.get(s_row["sid"])
            if act is None or s_row["target"] <= 0:
                continue
            lv = grade((act - s_row["target"]) / s_row["target"], cfg)
            if lv != "green":
                df = all_df.get(s_row["sid"][:10])
                all_lv = grade(df, cfg) if df is not None else None
                day_bias.append({"sid": s_row["sid"], "shift_final_level": lv,
                                 "all_day_final_delta": df,
                                 "all_day_final_level": all_lv,
                                 "note": "H 拆分目标偏置（F8）：班次视图红但全天主视图"
                                         + ("绿 → 拆分偏置非真缺口" if all_lv == "green" else "同向 → 真缺口")})
        # 全天 scope（终局 dfinal 非绿；3h 锚点 = 21:30 − 2h = 19:30）
        for row in all_rows:
            lv = grade(row[2], cfg)
            if lv != "green":
                a5 = next((x for x in all5 if x["sid"] == row[0]), None)
                if a5:
                    finals[row[0]] = {"final_level": lv,
                                      "anchor3": (
                                          datetime.fromisoformat(a5["t"])
                                          - timedelta(hours=2)).isoformat()}
        g3_ok, g3_miss = gate_g3(alerts, finals)

        # ---- G4：锚点前 10min 窗口无转换（拍板 B；转换次数 = 观测项 G4_obs）----
        g4_ok, g4_bad = gate_g4(alerts,
                                {**{s["sid"]: s["t"] for s in s5},
                                 **{s["sid"]: s["t"] for s in all5}})
        g4_obs: dict = {}
        for a in alerts:
            if a["prev_level"] is not None:
                g4_obs[a["sid"]] = g4_obs.get(a["sid"], 0) + 1

        # ---- G5：自洽恒等式（14 shift 5h）----
        g5_rows = [{**s, "m9_inproc": m9_json.get("rolling_shifts", {})
                    .get(s["sid"], {}).get("inproc_5h_n")} for s in s5]
        g5_ok, g5_bad = gate_g5(g5_rows)

        # ---- G6：21 scope-day 全覆盖 ----
        scope_days = sorted({s["sid"] for s in s5} | {s["sid"] for s in all5})
        g6_ok, g6_miss = gate_g6(alerts, scope_days)

        # ---- G0：单测子进程 ----
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_w3_alert.py", "-q"],
            cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
        g0_ok = (r.returncode == 0 and "passed" in r.stdout)

        gates = {"G0": g0_ok, "G1": g1_ok, "G1b": g1b_ok, "G2": g2_ok,
                 "G3": g3_ok, "G4": g4_ok, "G5": g5_ok, "G6": g6_ok}
        passed = all(gates.values())

        ctx = {"meta": {"window": [str(start), str(end)],
                        "alpha": 1.0, "alpha_mode": cfg.alpha_mode,
                        "elapsed_sec": round((datetime.now() - t0).total_seconds(), 1),
                        "section_map_n": len(section_map),
                        "section_pending_n": sum(
                            1 for v in section_map.values() if v == "待确认")},
               "gates": gates,
               "gate_detail": {"G1": g1_detail, "G1b_miss": g1b_miss,
                               "G2_bad": g2_bad, "G3_miss": g3_miss,
                               "G4_bad": g4_bad, "G4_obs": g4_obs,
                               "G5_bad": g5_bad,
                               "G6_miss": g6_miss},
               "alerts": alerts,
               "engine_view_5h": s5,
               "engine_view_all5h": all5,
               "finals_non_green": finals,
               "day_scope_bias_note": day_bias,
               "all_day_final": {r[0]: r[2] for r in all_rows},
               "pass": passed}
        (ROOT / "output" / "w3-T4-缺口告警回测.json").write_text(
            json.dumps(ctx, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        (ROOT / "output" / "w3-T4-缺口告警回测.md").write_text(
            _render_report(ctx), encoding="utf-8")
        print(f"[M10] 门判：G0={g0_ok} G1={g1_ok} G1b={g1b_ok} G2={g2_ok} "
              f"G3={g3_ok} G4={g4_ok} G5={g5_ok} G6={g6_ok} → "
              f"{'✅' if passed else '❌'}（{ctx['meta']['elapsed_sec']}s）")
        return 0 if passed else 2
    finally:
        conn.close()


def _render_report(ctx: dict) -> str:
    """(vii) 缺口告警回测节渲染（报告 §2 形态；告警行 = render_alert_line）。"""
    L = ["# (vii) 缺口告警回测（T4 / M10）", "",
         f"窗 {ctx['meta']['window'][0][:16]} ~ {ctx['meta']['window'][1][:16]}"
         f"（α={ctx['meta']['alpha']}，DSS_ALPHA_MODE={ctx['meta']['alpha_mode']}）",
         "注：plan CSV 出车计划列为实测回填（计划=实际）→ all_day_final ≡ 0，"
         "G1 实际度量 = 21:30 锚点全天预测 vs 实际误差（真前瞻计划下 dfinal 非零，门判逻辑同式适用）",
         f"段映射 {ctx['meta']['section_map_n']} 站"
         f"（待确认 {ctx['meta']['section_pending_n']}）", ""]
    L += ["## 门判 M10", "", "| 门 | 判定 | 明细 |", "|---|---|---|"]
    g, det = ctx["gates"], ctx["gate_detail"]
    L.append(f"| G0 单测 | {'✅' if g['G0'] else '❌'} | tests/test_w3_alert.py |")
    L.append(f"| G1 全天准度（21:30 中位≤5pp+零红） | {'✅' if g['G1'] else '❌'} | "
             f"中位 {det['G1']['median_pp']}pp，21:30 红 {det['G1']['n_red']} |")
    L.append(f"| G1b 管线一致（14/14） | {'✅' if g['G1b'] else '❌'} | "
             f"miss {det['G1b_miss']} |")
    L.append(f"| G2 level==grade（21 scope-day） | {'✅' if g['G2'] else '❌'} | "
             f"bad {det['G2_bad']} |")
    L.append(f"| G3 提前量 ≥2h | {'✅' if g['G3'] else '❌'} | "
             f"miss {det['G3_miss']}（终局非绿 {len(ctx['finals_non_green'])} scope-day） |")
    L.append(f"| G4 锚点前 10min 无转换（拍板 B；次数=观测） | {'✅' if g['G4'] else '❌'} | "
             f"bad {det['G4_bad']}；观测 {det.get('G4_obs', {})} |")
    L.append(f"| G5 自洽恒等式（F4） | {'✅' if g['G5'] else '❌'} | bad {det['G5_bad']} |")
    L.append(f"| G6 21 scope-day 全覆盖 | {'✅' if g['G6'] else '❌'} | miss {det['G6_miss']} |")
    if ctx.get("day_scope_bias_note"):
        L += ["", "## 日班 scope 拆分偏置注记（F8 双轨对照，不进 G3）", ""]
        for b in ctx["day_scope_bias_note"]:
            L.append(f"- {b['sid']} 班次视图终局 {b['shift_final_level']} / "
                     f"全天终局 Δ {b['all_day_final_delta']:+.1%}（{b['all_day_final_level']}）：{b['note']}")
    L += ["", f"总判定：{'✅ 通过' if ctx['pass'] else '❌ 未通过'}", "",
          "## 5h 锚点引擎视图（shift scope，14）", ""]
    for s in ctx["engine_view_5h"]:
        L.append(f"- {s['sid']} Δ {s['delta'] * 100:+.1f}% {s['level']}"
                 f"（grade {s['grade']}）pred {s['pred']} / 目标 {s['target']}"
                 f" / 池 {s['pool_n']}（在制 {s['hit']} + 瓶颈 {s['bottleneck']}"
                 f" + no-ETA {s['no_eta']}）")
    L += ["", "## 5h 锚点全天主视图（7）", ""]
    for s in ctx["engine_view_all5h"]:
        L.append(f"- {s['sid']}_全天 Δ {s['delta'] * 100:+.1f}% {s['level']}"
                 f"（grade {s['grade']}）pred {s['pred']} / 计划 {s['target']}")
    L += ["", "## 确认告警时间线（含基线）", ""]
    for a in ctx["alerts"]:
        d = dict(a)
        d["t"] = datetime.fromisoformat(a["t"])
        L.append(f"- {a['t'][11:16]} " + render_alert_line(Alert(**d)))
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
