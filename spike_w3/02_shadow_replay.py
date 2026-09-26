# -*- coding: utf-8 -*-
"""W3 T3.3 M9 影子回放验收（docs/04 §5；KW36/37 09-05→09-13，5min 步，锚点 3/4/5/6h，门 @5h）。

结构（TDD：门判纯函数先行，Step A/B/C 依次落）：
- gates()：G1–G4 门判纯函数（α on/off 分别判定；G1 = 逐班容差 p3+2 ∧ p3≤25 硬上限
  ∧ 全厂/中班 5h 相对差 ≤0.5pp）
- Step A 静态参照：in-situ 重实现 W2.1 场景 2（独立窗 09-05→09-13，ev_base 7d 前置边；
  直接估计器 v2；产品车）+ 自检固定带 [8.0%, 10.5%] + 本地 scratch 交叉对拍
  （|Δpred| ≤ 2 台/班，单班超差停机，round2 低③）
- Step B 滚动驱动：T2 管线原样（bootstrap_hours=168 显式注入 + rebuild 24h 生产语义；
  锚定回放起点，agy 中①）+ 锚点 predict(off/on) + α 预取结构
  （dict[body]→list[exit_epoch]，一次预取 + 每轮 bisect）
- Step C 门判 + 报告 output/w3-影子回放验收.md + JSON。

因果切分：滚动侧只消费 ≤t 事件（T2 水位线）；静态侧 = W2 因果窗口径。
G4 软化（docs/04 v2.1，待用户拍板；DSS_G4_HARD=1 恢复硬门）：G1–G3 达成而 G4 未严格优
→ 不阻断，报告中班敏感性分析 + 带数据回拍板。
"""
from __future__ import annotations

import bisect
import csv
import dataclasses
import importlib.util
import json
import os
import statistics
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spike_w2 import eta_v2, shifts            # noqa: E402
from spike_w1 import sqlbase                   # noqa: E402
from spike_w3.common import CST, EXIT_STATIONS, connect, load_config  # noqa: E402
from spike_w3.predictor import (AlphaTracker, build_takt_baseline,  # noqa: E402
                                obs_window, predict, _w2_replay_shift)
from spike_w3.stream_pipeline import (EventStream, FreshnessGuard, VehiclePool,  # noqa: E402
                                      bootstrap)

M9_START = datetime.fromisoformat(os.environ.get("DSS_M9_START", "2026-09-05T00:00")).replace(tzinfo=CST)
M9_END = datetime.fromisoformat(os.environ.get("DSS_M9_END", "2026-09-13T23:59:59")).replace(tzinfo=CST)
MARGIN_DAYS = 7          # ≥ 最长行程 120h（与 W2 ev_base 同口径）
STEP_SEC = 300           # 5min 步
BASELINE_MIN_SAMPLES = 30  # docs/04 §4：n_hours < 30 → 回退班次型

P3_CAP = 25          # p3_inproc 硬上限（docs/04 §1，round2 中②；超 = 池异常 → G1 fail）
PP_TOL = 0.005       # G1 相对差容差 0.5 个百分点
GATE_PCT = 0.10      # G2 绝对线（docs/02 原文：全厂 5h 中位 |err%| ≤ 10%）
SELF_CHECK_BAND = (0.080, 0.105)  # Step A 自检固定带（§7：超带 = 结构性漂移，exit 2）
CROSS_MAX_DELTA = 2  # scratch 交叉对拍逐班 |Δpred| 上限（台）

ENTRY_STATIONS = ("01IS205", "1L360RB", "01IS045")


def _err(pred, actual) -> float:
    return abs(pred - actual) / actual if actual and actual > 0 else 0.0


def _median(xs) -> float:
    return float(statistics.median(xs)) if xs else 0.0


def gates(rows: list[dict], alpha_mode: str = "off") -> dict:
    """M9 门判纯函数（docs/04 §5；2026-09-25 拍板修正：按 alpha_mode 分判）。

    rows：逐班一行（5h 锚点值）：{shift_id, kind, actual, pred_static,
    pred_roll_off, pred_roll_on, p3_inproc}。
    - G1（α=off 引擎一致性，两模式不变）：逐班 |roll−static| ≤ min(p3,25)+2 ∧ p3 ≤ 25 硬上限 ∧
      全厂 5h |median(off_err)−median(static_err)| ≤ 0.5pp ∧ 中班同相对门 ≤ 0.5pp
      （相对差——基线窗差见 docs/04 §1，不硬编码绝对基线值）
    - alpha_mode="off"（默认，2026-09-25 用户拍板：生产默认 off）：
      G2 = 全厂 off 5h 中位 |err%| ≤ 10%（绝对线）；
      G3 = True（α 未启用 → 定义上「不劣于静态/off」）；
      G4 = False（α 未启用 → 不严格优）→ main 侧 G4 软化处置（G1–G3 达成且非 DSS_G4_HARD=1，不阻断）。
    - alpha_mode="on"（实验开关，B5 原口径）：
      G2 = on 中位 ≤ 10%；G3 = 全厂 on ≤ off；G4 = 中班 on < off（严格）。
    返回 {"G1".."G4": bool, "detail": {...}}（detail 供报告与归因）。
    """
    if alpha_mode not in ("on", "off"):
        raise ValueError(f"alpha_mode 必须为 on/off，当前: {alpha_mode!r}")
    off_err = {r["shift_id"]: _err(r["pred_roll_off"], r["actual"]) for r in rows}
    st_err = {r["shift_id"]: _err(r["pred_static"], r["actual"]) for r in rows}
    on_err = {r["shift_id"]: _err(r["pred_roll_on"], r["actual"]) for r in rows}
    ids = [r["shift_id"] for r in rows]
    mid_ids = [r["shift_id"] for r in rows if r["kind"] == "mid"]

    p3_bad = [r["shift_id"] for r in rows if r["p3_inproc"] > P3_CAP]
    per_bad = [r["shift_id"] for r in rows
               if abs(r["pred_roll_off"] - r["pred_static"])
               > min(r["p3_inproc"], P3_CAP) + 2]
    full_pp = abs(_median([off_err[i] for i in ids]) - _median([st_err[i] for i in ids]))
    mid_pp = (abs(_median([off_err[i] for i in mid_ids])
                  - _median([st_err[i] for i in mid_ids])) if mid_ids else 0.0)
    g1 = (not per_bad) and (not p3_bad) and full_pp <= PP_TOL and mid_pp <= PP_TOL

    if alpha_mode == "off":
        g2 = _median([off_err[i] for i in ids]) <= GATE_PCT
        g3 = True          # α 未启用：「不劣于静态/off」定义成立
        g4 = False         # α 未启用：不严格优（main 侧软化处置）
    else:
        g2 = _median([on_err[i] for i in ids]) <= GATE_PCT
        g3 = _median([on_err[i] for i in ids]) <= _median([off_err[i] for i in ids])
        g4 = (_median([on_err[i] for i in mid_ids])
              < _median([off_err[i] for i in mid_ids])) if mid_ids else False

    return {
        "G1": g1, "G2": g2, "G3": g3, "G4": g4,
        "detail": {
            "alpha_mode": alpha_mode,
            "median_err_static": _median([st_err[i] for i in ids]),
            "median_err_off": _median([off_err[i] for i in ids]),
            "median_err_on": _median([on_err[i] for i in ids]),
            "median_err_on_mid": _median([on_err[i] for i in mid_ids]) if mid_ids else None,
            "median_err_off_mid": _median([off_err[i] for i in mid_ids]) if mid_ids else None,
            "g1_per_shift_bad": per_bad,
            "g1_cap_bad": p3_bad,
            "g1_full_pp": full_pp,
            "g1_mid_pp": mid_pp,
        },
    }


# ---------------------------------------------------------------------------
# 排班（plan_inbox 周计划 CSV 直读，docs/02 P4）
# ---------------------------------------------------------------------------

def load_schedule(paths, d0: date, d1: date) -> dict:
    """plan_inbox 周计划 CSV 直读 → {date: (day_type, mid_type)}（场景 2 名义工时；
    休 = None）。窗内缺日期 → KeyError（缺排班不得静默丢班）。"""
    sched: dict[date, tuple] = {}
    for p in paths:
        with open(p, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                d = datetime.strptime(row["日期"].strip(), "%Y-%m-%d").date()
                dt = shifts.normalize_shift_type(row["日班工时"])
                mt = shifts.normalize_shift_type(row["中班工时"])
                if d in sched and sched[d] != (dt, mt):
                    raise ValueError(f"排班冲突：{d} 在多个周计划文件中不一致")
                sched[d] = (dt, mt)
    d = d0
    while d <= d1:
        if d not in sched:
            raise KeyError(f"回放窗 {d} 无排班行（plan_inbox 覆盖不全）")
        d += timedelta(days=1)
    return sched


def _shifts_for_window(schedule: dict, replay_start: datetime, replay_end: datetime) -> list:
    """窗内完整班次（C2：归属窗 ⊆ 回放窗）；actual 在 static_reference 侧计。"""
    out = []
    d = replay_start.date()
    while d <= replay_end.date():
        dt, mt = schedule.get(d, (None, None))
        for s in shifts.shift_windows(d, dt, mt):
            if shifts.is_complete(s, replay_start, replay_end):
                out.append(s)
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Step A：静态参照（in-situ 重实现 W2.1 场景 2 直接估计器路径）
# ---------------------------------------------------------------------------

def static_reference(conn, replay_start: datetime, replay_end: datetime,
                     schedule: dict, tail_days: int = 1) -> dict:
    """W2.1 场景 2 in-situ 重实现（独立窗；产品车；ev_base 7d 前置边；v2 直接估计器）。

    复用：sqlbase.ev_base_sql 临时表 + W2 load_tables（importlib 只读引 02_replay_shift）
    + shifts.shift_windows（排班名义型）。出车计划列不用（docs/02 P4：实测回填、M9 不用）。
    tail_days：ev 窗尾边（默认 1 = W2 原口径，自检 + scratch 交叉对拍用；
    M9 G1 对比侧用 tail_days=MARGIN_DAYS=7（2026-09-25 拍板 A：窗尾 +7d 与 7d 前置边
    对称，把窗末之后才出口的在制车还原进静态参照，消除 cls4 窗尾盲区））。
    返回 {"shifts": {shift_id: {..., pred: {h: int}, A: {h}, inproc: {h}, no_eta: {h}}},
          "p3_candidates": set[body]（静态全量 spans 中无入口站事件的在窗产品车，
          p3_inproc 口径的静态侧集合——round2 中②独立计算），
          "tail_days"}。
    """
    ev_start = replay_start - timedelta(days=MARGIN_DAYS)
    ev_end = (replay_end + timedelta(days=tail_days)).replace(hour=23, minute=59, second=59)
    cur = conn.cursor()
    cur.execute(sqlbase.ev_base_sql(ev_start, ev_end), (ev_start, ev_end))
    cur.execute("SELECT count(*) FROM tmp_ev_base")
    n_ev = cur.fetchone()[0]
    print(f"[M9-A] ev_base {ev_start:%m-%d} → {ev_end:%m-%d}"
          f"（前置 {MARGIN_DAYS}d / 尾 +{tail_days}d）：{n_ev} 行")

    # 车辆 span（与 W2 同查询：entry/exit 首事件；缺 = 窗内无该事件）
    cur.execute(
        "SELECT body, max(bt) AS bt, "
        "EXTRACT(EPOCH FROM min(t) FILTER (WHERE station IN ('01IS205','1L360RB','01IS045')))::BIGINT, "
        "EXTRACT(EPOCH FROM min(t) FILTER (WHERE station IN ('K3IS140','1J440RB','K2IS075','K1IS135')))::BIGINT "
        "FROM tmp_ev_base GROUP BY body")
    spans = pd.DataFrame(cur.fetchall(),
                         columns=["body", "bt", "entry_t", "exit_t"]).set_index("body")
    bodies = spans.index.to_numpy()
    entry_arr = spans["entry_t"].to_numpy(dtype=np.int64)
    exit_arr = np.where(spans["exit_t"].isna(), np.inf,
                        spans["exit_t"].to_numpy(dtype=np.float64))
    bt_arr = spans["bt"].to_numpy()

    # 事件流（flag 0/1/2，W2 同口径；- / 3 不进估计）
    cur.execute("SELECT body, station, EXTRACT(EPOCH FROM t)::BIGINT, flag "
                "FROM tmp_ev_base WHERE flag IN ('0', '1', '2') ORDER BY body, t, id")
    evts: dict[str, list] = {}
    for body, st, t, fl in cur.fetchall():
        evts.setdefault(body, []).append((int(t), st, fl))
    ts_lists = {b: [e[0] for e in v] for b, v in evts.items()}

    w2m = _w2_replay_shift()
    tables = w2m.load_tables(conn)
    cur.execute("SELECT body_type, station FROM dss.v2_comp_excl")
    comp_excl = frozenset(cur.fetchall())
    print(f"[M9-A] 学习表: { {k: len(v) for k, v in tables.items()} }")

    # p3 候选（静态侧独立计算，round2 中②）：窗内产品车全量 spans 中无入口站事件者
    in_st = ",".join(f"'{s}'" for s in ENTRY_STATIONS)
    cur.execute(
        f"SELECT \"BODY_ID\", "
        f"EXTRACT(EPOCH FROM min(\"DATE_EVT\") FILTER (WHERE \"RW_STATION_ID\" IN ({in_st}))) "
        "FROM ods.carbody_history WHERE \"BODY_ID\" LIKE '78202%%' "
        "AND \"DATE_EVT\" >= %s AND \"DATE_EVT\" <= %s GROUP BY \"BODY_ID\"",
        (ev_start, ev_end))
    p3_candidates = {b for b, e in cur.fetchall() if e is None}
    print(f"[M9-A] p3 候选（无入口站事件）：{len(p3_candidates)} 台")

    out = {}
    for s in _shifts_for_window(schedule, replay_start, replay_end):
        sid = f"{s.day.isoformat()}_{s.kind}"
        attr_ep, end_ep = s.attr_start.timestamp(), s.end.timestamp()
        actual = int(((exit_arr >= attr_ep) & (exit_arr <= end_ep)).sum())
        rec = {"kind": s.kind, "type": s.type, "actual": actual,
               "pred": {}, "A": {}, "inproc": {}, "no_eta": {}, "clip_pred": {}}
        if actual == 0:
            out[sid] = rec
            continue
        week_cache = {}
        for h, t_dt in zip(shifts.ANCHOR_HOURS, shifts.anchors(s)):
            t = int(t_dt.timestamp())
            week = shifts.iso_week_label(t_dt.date())
            a_idx = np.where((exit_arr >= attr_ep) & (exit_arr <= t))[0]
            A = int(a_idx.size)
            inproc = np.where((entry_arr <= t) & (exit_arr > t))[0]
            cnt = no_eta = 0
            i_bodies = []
            for i in inproc:
                body = bodies[i]
                evlist = evts.get(body)
                if not evlist:
                    no_eta += 1
                    continue
                idx = bisect.bisect_right(ts_lists[body], t) - 1
                if idx < 0 or idx >= len(evlist) - 1:
                    no_eta += 1          # 末事件即窗内末事件 → 过期 ETA 不计（W2 口径）
                    continue
                t_e, st_e, fl_e = evlist[idx]
                eta_d, _used = eta_v2.estimate_event(int(t_e), st_e, fl_e, bt_arr[i],
                                                     week, tables, comp_excl)
                if eta_d is not None and t + (int(eta_d) - int(t_e)) <= end_ep:
                    cnt += 1
                    if h == shifts.GATE_ANCHOR_H:
                        i_bodies.append(body)
            rec["pred"][h] = A + cnt
            rec["A"][h] = A
            rec["inproc"][h] = len(inproc)
            rec["no_eta"][h] = no_eta
            if h == shifts.GATE_ANCHOR_H:
                rec["I_bodies"] = i_bodies
                rec["A_bodies"] = [bodies[i] for i in a_idx]
            # 尾带分析（报告 (v)）：窗末 −30min 交班尾的截断对照（仅中班；仅静态侧）
            if s.kind == "mid":
                clip_ep = (s.end - timedelta(minutes=30)).timestamp()
                cnt_clip = no_eta_clip = 0
                A_clip = A
                for i in inproc:
                    body = bodies[i]
                    evlist = evts.get(body)
                    if not evlist:
                        no_eta_clip += 1
                        continue
                    idx = bisect.bisect_right(ts_lists[body], t) - 1
                    if idx < 0 or idx >= len(evlist) - 1:
                        no_eta_clip += 1
                        continue
                    t_e, st_e, fl_e = evlist[idx]
                    eta_d, _u = eta_v2.estimate_event(int(t_e), st_e, fl_e, bt_arr[i],
                                                      week, tables, comp_excl)
                    if eta_d is not None and t + (int(eta_d) - int(t_e)) <= clip_ep:
                        cnt_clip += 1
                rec["clip_pred"][h] = A_clip + cnt_clip
        out[sid] = rec
    return {"shifts": out, "p3_candidates": p3_candidates,
            "ev_rows": n_ev, "ev_window": (f"{ev_start:%Y-%m-%d}", f"{ev_end:%Y-%m-%d}"),
            "tail_days": tail_days,
            "shifts_list": _shifts_for_window(schedule, replay_start, replay_end)}


# ---------------------------------------------------------------------------
# Step B：滚动驱动（T2 管线原样 + 锚点 predict + α 接线）
# ---------------------------------------------------------------------------

def _w3_boot():
    """importlib 引 01_boot_online（数字开头模块不可正常 import；main 有守卫，import 无副作用）。"""
    m = sys.modules.get("w3_boot")
    if m is None:
        spec = importlib.util.spec_from_file_location(
            "w3_boot", Path(__file__).resolve().parent / "01_boot_online.py")
        m = importlib.util.module_from_spec(spec)
        sys.modules["w3_boot"] = m
        spec.loader.exec_module(m)
    return m


def _prefetch_exits(conn, start: datetime, end: datetime) -> dict:
    """α 观察用出口事件预取（回放窗内 4 出口站产品车，一次预取 → dict[body]→list[epoch]；
    免逐轮 SQL，无未来泄漏——计数只取 [obs_start, obs_end]）。"""
    in_st = ",".join(f"'{s}'" for s in EXIT_STATIONS)
    cur = conn.cursor()
    cur.execute(
        f"SELECT \"BODY_ID\", EXTRACT(EPOCH FROM \"DATE_EVT\")::FLOAT8 "
        f"FROM ods.carbody_history WHERE \"RW_STATION_ID\" IN ({in_st}) "
        f"AND \"BODY_ID\" LIKE '78202%%' AND \"DATE_EVT\" >= %s AND \"DATE_EVT\" <= %s "
        "ORDER BY \"BODY_ID\", \"DATE_EVT\"", (start, end))
    d: dict[str, list] = {}
    for b, t in cur.fetchall():
        d.setdefault(b, []).append(float(t))
    return d


def _count_exits_in(exits: dict, start_ep: float, end_ep: float) -> int:
    """每车首条 ≥ obs_start 的出口，若 ≤ obs_end 计 1（bisect_left；正常单班窗内至多 1 条/车）。"""
    n = 0
    for lst in exits.values():
        i = bisect.bisect_left(lst, start_ep)
        if i < len(lst) and lst[i] <= end_ep:
            n += 1
    return n


def run_rolling(conn, cfg, replay_start: datetime, replay_end: datetime,
                schedule: dict, tables: dict, comp_excl: frozenset,
                alert_hook=None) -> dict:
    """Step B：T2 管线原样 + 锚点 predict（off/on）+ α 接线。

    bootstrap_hours=168 显式注入（round2 低④：DSSConfig frozen 必须 dataclasses.replace；
    后续 _rebuild 周期重建全部用 cfg_m9——否则第 2 天重建回 120h 窗）；rebuild_hours=24
    生产语义；锚定回放起点（agy 中①：bootstrap 锚定 start 而非墙钟）。
    返回 {"shifts": {shift_id: {kind, pred_off/on: {h}, alpha: {h}, inproc_5h: set}},
          "alpha_trace": {shift_id: [(ts, alpha)]}, "alerts": [...], "cfg_echo": {...}}。
    T4.4：alert_hook 默认 None = 生产零影响（M9 行为不变）；非 None 时每步调用
    alert_hook(cur, pool, conn, tables, comp_excl, cfg_m9, [active_shift]|[])
    → list[dict]（已序列化告警），累积进返回的 alerts。
    """
    boot_mod = _w3_boot()
    cfg_m9 = dataclasses.replace(cfg, bootstrap_hours=168.0)   # round2 低④
    rest = set()
    for p in ROOT.glob("plan_inbox/week_plan_*.csv"):
        rest |= boot_mod.load_rest_days(str(p))
    exp_fn = (lambda t: t.date() in rest) if rest else None

    pool = VehiclePool(dead_vehicle_hours=cfg_m9.dead_vehicle_hours)
    report = bootstrap(conn, cfg_m9, pool, replay_start)     # 锚定回放起点
    print(f"[M9-B] bootstrap(168h) 池={report.pool_size} 水位={report.watermark}")
    stream = EventStream(conn, wm=report.watermark,
                         skew_window_min=cfg_m9.skew_window_min)
    stream.front = report.data_max_ts
    guard = FreshnessGuard(cfg_m9)

    baseline = build_takt_baseline(conn, cfg.learn_win_start, cfg.learn_win_end,
                                   BASELINE_MIN_SAMPLES)
    print(f"[M9-B] takt baseline {[(k, (round(v[0], 2), v[1], v[2])) for k, v in sorted(baseline.items())]}")
    tracker = AlphaTracker(baseline)

    exits = _prefetch_exits(conn, replay_start, replay_end)
    shifts_list = _shifts_for_window(schedule, replay_start, replay_end)
    anchor_map: dict[datetime, tuple] = {}
    for s in shifts_list:
        for h, t_dt in zip(shifts.ANCHOR_HOURS, shifts.anchors(s)):
            anchor_map[t_dt] = (s, h)
    out = {f"{s.day.isoformat()}_{s.kind}":
           {"kind": s.kind, "pred_off": {}, "pred_on": {}, "alpha": {},
            "inproc_5h": set()} for s in shifts_list}
    alpha_trace: dict[str, list] = {}
    alerts: list = []

    last_rebuild = replay_start
    cur = replay_start
    cur_shift_id = None
    n_rounds = 0
    while cur < replay_end:
        if (cur - last_rebuild) >= timedelta(hours=cfg_m9.rebuild_hours):
            boot_mod._rebuild(conn, cfg_m9, pool, stream, cur)   # cfg_m9：168h 重建窗
            last_rebuild = cur
            print(f"[M9-B] 周期重建 {cur:%m-%d %H:%M}（168h 窗）")
        boot_mod.run_one_round(pool, stream, guard, conn, cur,
                               expected_silence=bool(exp_fn(cur)) if exp_fn else False)
        n_rounds += 1
        # 班次切换 → on_shift 重置（committed=1.0）
        active = None
        for s in shifts_list:
            if s.attr_start <= cur < s.end:
                active = s
                break
        sid = f"{active.day.isoformat()}_{active.kind}" if active else None
        if active is not None and sid != cur_shift_id:
            tracker.on_shift(active)
            cur_shift_id = sid
            alpha_trace.setdefault(sid, []).append((cur, tracker.alpha()))
        # α 观察（每轮；窗长 <1h / 班外 / t≥end → 状态静止）
        if active is not None:
            w = obs_window(cur, active, tracker.window_hours)
            if w is not None:
                n_exits = _count_exits_in(exits, w[0].timestamp(), w[1].timestamp())
                tracker.update(cur, active, n_exits, w[2])
        # 锚点 predict（off = α1.0；on = 当前 committed）
        hit = anchor_map.get(cur)
        if hit is not None:
            s, h = hit
            sid = f"{s.day.isoformat()}_{s.kind}"
            r_off = predict(conn, pool, s, cur, tables, comp_excl, cfg_m9, alpha=1.0)
            a_now = tracker.alpha()
            r_on = predict(conn, pool, s, cur, tables, comp_excl, cfg_m9, alpha=a_now)
            out[sid]["pred_off"][h] = r_off["pred"]
            out[sid]["pred_on"][h] = r_on["pred"]
            out[sid]["alpha"][h] = a_now
            if h == shifts.GATE_ANCHOR_H:
                out[sid]["inproc_5h"] = {b for b in pool.snapshot()
                                         if b.startswith("78202")}
                # 诊断：滚动侧 5h 逐车明细（全量 by_vehicle 含 rem_sec/stale；
                # 计数集 = rem 非空且 t+rem/α ≤ end，分析侧按 α=1.0 同式过滤）
                out[sid]["roll_by_vehicle_off"] = r_off["by_vehicle"]
                out[sid]["end_epoch"] = s.end.timestamp()
                cur_a = conn.cursor()
                cur_a.execute(
                    "SELECT DISTINCT \"BODY_ID\" FROM ods.carbody_history"
                    " WHERE \"BODY_ID\" LIKE '78202%%'"
                    f" AND \"RW_STATION_ID\" IN ({','.join(chr(39) + st + chr(39) for st in EXIT_STATIONS)})"
                    " AND \"DATE_EVT\" >= %s AND \"DATE_EVT\" <= %s",
                    (s.attr_start, cur))
                out[sid]["roll_A_bodies"] = [r[0] for r in cur_a.fetchall()]
                cur_a.close()
                alpha_trace.setdefault(sid, []).append((cur, a_now))
        # T4.4 告警 hook（默认 None = 零影响；非 None 时每步评估）
        if alert_hook is not None:
            step_alerts = alert_hook(cur, pool, conn, tables, comp_excl,
                                     cfg_m9, [active] if active is not None else [])
            if step_alerts:
                alerts.extend(step_alerts)
        cur = cur + timedelta(seconds=STEP_SEC)
    print(f"[M9-B] 完成 {n_rounds} 轮（5min 步，rebuild 24h 生产语义）")
    return {"shifts": out, "alpha_trace": alpha_trace, "alerts": alerts,
            "cfg_echo":
            {k: getattr(cfg_m9, k) for k in ("bootstrap_hours", "rebuild_hours",
                                              "eta_stale_hours")}}


# ---------------------------------------------------------------------------
# Step A 自检：固定带 + scratch 交叉对拍
# ---------------------------------------------------------------------------

def selfcheck_static(static: dict) -> float:
    """自检门：全厂 5h 中位 |err%| ∈ [8.0%, 10.5%] 固定带（§7：超带 = 结构性漂移，exit 2）。"""
    errs = [abs(r["pred"][5] - r["actual"]) / r["actual"]
            for r in static["shifts"].values() if r["actual"] > 0]
    med = float(statistics.median(errs))
    lo, hi = SELF_CHECK_BAND
    if not (lo <= med <= hi):
        raise SystemExit(
            f"自检失败：静态复测全厂 5h 中位 |err%| = {med * 100:.2f}% 超出固定带 "
            f"[{lo * 100:.1f}%, {hi * 100:.1f}%]——结构性漂移（基线二次漂移），exit 2 停下重新定标")
    return med


def scratch_crosscheck(static: dict) -> dict:
    """本地 scratch 复跑（W2.1 原脚本，未入库产物、仅本地交叉验证）逐班 5h pred 对拍。

    |Δpred| ≤ 2 台/班；任一班次超差 → 停下（round2 低③ 单班超差停机规约），不进 Step B。
    返回 {shift_id: scratch_pred5}。
    """
    print("[M9-A] scratch 交叉对拍（02_replay_kw37.py 独立窗 场景 2 复跑）…")
    outdir = ROOT / "output" / "tmp_kw37_review"
    pre_mtime = max((p.stat().st_mtime for p in outdir.glob("w2_replay_shifts_*_sc2.csv")),
                    default=0.0)
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scratch" / "02_replay_kw37.py"),
         "--replay-start", "2026-09-05", "--replay-end", "2026-09-13",
         "--scenario", "2"],
        cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8", errors="replace")
    cands = sorted(outdir.glob("w2_replay_shifts_*_sc2.csv"), key=lambda p: p.stat().st_mtime)
    if not cands or cands[-1].stat().st_mtime <= pre_mtime:
        raise SystemExit(f"交叉对拍失败：scratch 未产出新 CSV（returncode={proc.returncode}，"
                         f"stderr 尾: {proc.stderr[-500:]}；stdout 尾: {proc.stdout[-300:]}）")
    df = pd.read_csv(cands[-1])
    g5 = df[df["anchor_h"] == 5]
    scratch5 = {f"{r['day']}_{r['kind']}": int(r["direct_pred"]) for _, r in g5.iterrows()}
    issues = []
    for sid, r in static["shifts"].items():
        if sid not in scratch5:
            issues.append(f"{sid}：scratch 缺班")
            continue
        d = abs(r["pred"][5] - scratch5[sid])
        if d > CROSS_MAX_DELTA:
            issues.append(f"{sid}：|Δpred| = {d} > {CROSS_MAX_DELTA}（insitu={r['pred'][5]} "
                          f"scratch={scratch5[sid]}）")
    if issues:
        raise SystemExit("交叉对拍超差（单班超差停机规约，round2 低③）——禁止进入 Step B：\n  "
                         + "\n  ".join(issues))
    print(f"[M9-A] 交叉对拍通过（{len(scratch5)} 班，|Δpred| ≤ {CROSS_MAX_DELTA}）")
    return scratch5


# ---------------------------------------------------------------------------
# Step C：报告
# ---------------------------------------------------------------------------

def static_ext_view(ext: dict, h: int = 5) -> dict:
    """G1 对比侧（延展窗）静态视图——正式 JSON 自包含（agy 审核轮 3 中①）。

    逐班汇总（actual/pred_h/A_h）+ h 锚点逐车 I_bodies/A_bodies + p3 候选清单（排序列表），
    使正式产物可离线逐车审计，不依赖 scratch 补件。
    """
    return {
        "ev_window": ext["ev_window"], "ev_rows": ext["ev_rows"], "tail_days": ext["tail_days"],
        "p3_candidates": sorted(ext["p3_candidates"]),
        "shifts": {sid: {"actual": r["actual"], "pred5": r["pred"].get(h),
                         "A5": r["A"].get(h),
                         "I_bodies": r.get("I_bodies", []), "A_bodies": r.get("A_bodies", [])}
                   for sid, r in ext["shifts"].items()},
    }

def render_report(ctx: dict) -> str:
    """M9 验收报告（docs/04 §5 Step 5 五项 + 门判结论）。"""
    static, rolling, gates_r, rows = ctx["static"], ctx["rolling"], ctx["gates"], ctx["rows"]
    d = gates_r["detail"]
    L = ["# W3 M9 影子回放验收报告（Task 3：滚动预测 + α 自适应）", "",
         f"- 回放窗：{M9_START:%Y-%m-%d %H:%M} → {M9_END:%Y-%m-%d %H:%M}（KW36/37；"
         f"09-06 / 09-13 停产）",
         f"- 静态参照 ev_base 窗（W2 原口径，自检/对拍/基线用）：{ctx['static']['ev_window'][0]} → "
         f"{ctx['static']['ev_window'][1]}（7d 前置边，尾 +1d，{ctx['static']['ev_rows']} 行）",
         f"- G1 对比侧静态（延展窗）：{ctx['static_ext']['ev_window'][0]} → "
         f"{ctx['static_ext']['ev_window'][1]}（前置 7d / 尾 +7d 对称，{ctx['static_ext']['ev_rows']} 行；"
         f"2026-09-25 拍板 A：消除窗尾盲区）",
         f"- α 门基线：{ctx['alpha_mode']}（DSS_ALPHA_MODE；2026-09-25 拍板：生产默认 off，"
         f"中班 α 修复结合就餐工时日历另立任务）",
         f"- 滚动侧配置：{ctx['rolling']['cfg_echo']}（rebuild 24h 生产语义；"
         f"bootstrap 168h 对齐静态 7d 边）",
         f"- 生成时间：{datetime.now(CST):%Y-%m-%d %H:%M}（Asia/Shanghai）", "",
         "## (i) 逐班 3/4/5/6h 收敛轨迹（pred vs actual；静态 / 滚动 off / 滚动 on）", "",
         "| 班次 | 型 | actual | 静态 3h/4h/5h/6h | 滚动off 3h/4h/5h/6h | 滚动on 3h/4h/5h/6h | "
         "5h err 静/off/on |",
         "|---|---|---|---|---|---|---|"]
    for r in rows:
        sid = r["shift_id"]
        st, ro, rn = static["shifts"][sid], rolling["shifts"][sid], None
        traj = lambda rec, key: "/".join(str(rec[key][h]) for h in (3, 4, 5, 6))
        e_st = abs(st["pred"][5] - r["actual"]) / r["actual"] if r["actual"] else 0
        e_off = abs(ro["pred_off"][5] - r["actual"]) / r["actual"] if r["actual"] else 0
        e_on = abs(ro["pred_on"][5] - r["actual"]) / r["actual"] if r["actual"] else 0
        L.append(f"| {sid} | {r['kind']} {st['type']} | {r['actual']} | {traj(st, 'pred')} "
                 f"| {traj(ro, 'pred_off')} | {traj(ro, 'pred_on')} "
                 f"| {e_st * 100:.2f}% / {e_off * 100:.2f}% / {e_on * 100:.2f}% |")
    L += ["", "## (ii) 终值 vs W2.1 基线（5h 中位 |err%|）", "",
          f"- 本跑静态复测（W2 原窗口径，自检值）：**{ctx['median_err_static_selfcheck'] * 100:.2f}%**"
          f"（自检固定带 {SELF_CHECK_BAND[0] * 100:.1f}–{SELF_CHECK_BAND[1] * 100:.1f}% 内）",
          f"- 延展窗静态 5h 中位（G1 对比侧）：{d['median_err_static'] * 100:.2f}%"
          f"（与 (i) 原窗值不可互比；G1 全厂相对差以此为基准）",
          f"- 本跑滚动 off：{d['median_err_off'] * 100:.2f}%；滚动 on：{d['median_err_on'] * 100:.2f}%",
          "- 历史参照（非本门基准）：全窗 35 天 12.30% / 场景 2 全窗子集 10.04%（09-21）/ "
          "好周 8.08%；09-25 独立窗复测 8.74%（本次复测值以 (i) 为准，窗差归因见 docs/04 §1）", ""]
    L += ["## (iii) α on/off 对打（α 为实验参数；门基线 = off，2026-09-25 拍板）", "",
          f"- 全厂 5h 中位：off {d['median_err_off'] * 100:.2f}% → on {d['median_err_on'] * 100:.2f}%",
          f"- 中班 5h 中位：off {(d['median_err_off_mid'] or 0) * 100:.2f}% → "
          f"on {(d['median_err_on_mid'] or 0) * 100:.2f}%",
          "- 逐班 α@锚点（on 值 = 该锚点 committed α；首锚点恒 1.0 = 观测未满 2 周期）：",
          "", "| 班次 | α@3h | α@4h | α@5h | α@6h |",
          "|---|---|---|---|---|"]
    for r in rows:
        sid = r["shift_id"]
        a = rolling["shifts"][sid]["alpha"]
        L.append(f"| {sid} | " + " | ".join(f"{a[h]:.3f}" if h in a else "—" for h in (3, 4, 5, 6)) + " |")
    L += ["", "## (iv) G1 一致性表（逐班延展窗静态 / roll-off / Δ / p3_inproc / 容差）",
          "- 静态列 = 延展窗（尾 +7d）；(i)(ii) 的静态列 = W2 原窗（尾 +1d）基线——两窗不可互比，"
          "差集 = 窗末之后才出口的在制车（首跑 cls4 窗尾盲区，2026-09-25 拍板 A 以延展窗消除）", "",
          "| 班次 | 静态 5h（延展窗） | 滚动off 5h | Δ | p3_inproc | 容差 p3+2 | 过 |",
          "|---|---|---|---|---|---|---|"]
    for r in rows:
        delta = r["pred_roll_off"] - r["pred_static"]
        tol = min(r["p3_inproc"], P3_CAP) + 2
        ok = abs(delta) <= tol and r["p3_inproc"] <= P3_CAP
        L.append(f"| {r['shift_id']} | {r['pred_static']} | {r['pred_roll_off']} | {delta:+d} "
                 f"| {r['p3_inproc']} | {tol} | {'✅' if ok else '❌'} |")
    L += ["", "## (v) 尾带分析（窗末 30min 交班尾；是否被 α 吸收）", ""]
    for r in rows:
        if r["kind"] != "mid":
            continue
        sid = r["shift_id"]
        st_rec = static["shifts"][sid]
        clip = st_rec["clip_pred"].get(5)
        if clip is None:
            continue
        nom = st_rec["pred"][5]
        over = nom - clip
        L.append(f"- {sid}（{st_rec['type']}）：静态名义窗末 5h pred {nom} vs 窗末−30min 截断 {clip}"
                 f"（Δ {over:+d} 台）；α on/off 5h pred "
                 f"{rolling['shifts'][sid]['pred_on'][5]}/{rolling['shifts'][sid]['pred_off'][5]}"
                 f"（on−off {rolling['shifts'][sid]['pred_on'][5] - rolling['shifts'][sid]['pred_off'][5]:+d}）")
    L += ["- 判定：若尾带多计未被 α 吸收（on−off ≈ 0 且截断 Δ 显著）→ 按 docs/02 带数据回拍板"
          "「预测侧工时干满点截断」口径，**本波次不自行改口径**。", ""]
    L += ["## 门判结论（docs/04 §5；α 基线 = " + ctx.get("alpha_mode", "off") + "）", ""]
    mode = ctx.get("alpha_mode", "off")
    g2_name = ("达标线（全厂 5h 中位 |err%| ≤ 10%，α=off 基线）" if mode == "off"
               else "α=on 达标线（全厂 5h 中位 |err%| ≤ 10%）")
    g3_name = ("不劣于静态（α 未启用 → 定义成立）" if mode == "off"
               else "不劣于静态（全厂 on ≤ off）")
    g4_name = ("中班严格优（α 未启用 → 不严格优，软化处置）" if mode == "off"
               else "中班严格优（on < off）")
    for g, name in (("G1", "α=off 引擎一致性（逐班 p3+2 ∧ 全厂/中班 5h 相对差 ≤0.5pp）"),
                    ("G2", g2_name),
                    ("G3", g3_name),
                    ("G4", g4_name)):
        mark = "✅" if gates_r[g] else "❌"
        soft = ""
        if g == "G4" and not gates_r["G4"] and ctx.get("g4_soft"):
            mark, soft = "⚠", "（软化处置：不阻断——中班敏感性分析见 (iii)，带数据回拍板）"
        L.append(f"- **{g} {mark}** {name}{soft}")
    L += [f"", 
          f"- G1 细节：逐班超差 {d['g1_per_shift_bad'] or '—'}；p3 超上限 {d['g1_cap_bad'] or '—'}；"
          f"全厂相对差 {d['g1_full_pp'] * 100:.3f}pp；中班 {d['g1_mid_pp'] * 100:.3f}pp",
          "- 注：`DSS_G4_HARD=1` 恢复硬门；α=off 基线下 α 未启用、G4 恒 False → 该开关会阻断，"
          "仅在 `DSS_ALPHA_MODE=on` 时有生产准入诊断意义（agy 审核轮 3 低①）"]
    L += ["", f"## 总判定：{'✅ 通过' if ctx['pass'] else '❌ 未通过'}"
             + (f"（G4 软化不阻断，待用户拍板：{ctx['g4_note']}）" if ctx.get("g4_soft") else ""),
          ""]
    return "\n".join(L)


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    cfg = load_config()
    conn = connect()
    t0 = datetime.now()
    try:
        plan_files = sorted((ROOT / "plan_inbox").glob("week_plan_*.csv"))
        schedule = load_schedule(plan_files, M9_START.date(), M9_END.date())
        n_work = sum(1 for d, (dt, mt) in schedule.items()
                     if M9_START.date() <= d <= M9_END.date() and (dt or mt))
        print(f"[M9] 排班 {len(plan_files)} 文件 → {n_work} 个工作日（窗内 "
              f"{M9_START.date()}~{M9_END.date()}）")

        # --- Step A：静态参照 + 自检（W2 原窗：尾 +1d，W2 兼容口径）---
        static = static_reference(conn, M9_START, M9_END, schedule)
        med_static = selfcheck_static(static)
        print(f"[M9-A] 自检通过：全厂 5h 中位 |err%| = {med_static * 100:.2f}%"
              f"（固定带 {SELF_CHECK_BAND[0] * 100:.1f}–{SELF_CHECK_BAND[1] * 100:.1f}%）")
        scratch5 = scratch_crosscheck(static)

        # G1 对比侧：延展窗静态（尾 +7d 与 7d 前置边对称；2026-09-25 拍板 A——
        # 窗末之后才出口的在制车还原进静态参照，消除 cls4 窗尾盲区；
        # 自检/对拍/基线 (i)(ii) 仍用原窗 W2 口径，两窗不可互比）
        static_ext = static_reference(conn, M9_START, M9_END, schedule,
                                      tail_days=MARGIN_DAYS)

        # --- Step B：滚动驱动 ---
        w2m = _w2_replay_shift()
        tables = w2m.load_tables(conn)
        cur = conn.cursor()
        cur.execute("SELECT body_type, station FROM dss.v2_comp_excl")
        comp_excl = frozenset(cur.fetchall())
        rolling = run_rolling(conn, cfg, M9_START, M9_END, schedule, tables, comp_excl)

        # --- Step C：p3_inproc + 门判 + 报告 ---
        rows = []
        for s in static["shifts_list"]:
            sid = f"{s.day.isoformat()}_{s.kind}"
            st_ext = static_ext["shifts"][sid]
            ro = rolling["shifts"][sid]
            p3 = len(ro["inproc_5h"] & static_ext["p3_candidates"])  # G1 对比侧口径（延展窗 p3）
            rows.append({"shift_id": sid, "kind": s.kind,
                         "actual": st_ext["actual"],
                         "pred_static": st_ext["pred"][5],
                         "pred_roll_off": ro["pred_off"][5],
                         "pred_roll_on": ro["pred_on"][5],
                         "p3_inproc": p3})
        g = gates(rows, alpha_mode=cfg.alpha_mode)
        g4_hard = os.environ.get("DSS_G4_HARD", "") == "1"
        g4_soft = (not g["G4"]) and g["G1"] and g["G2"] and g["G3"] and not g4_hard
        passed = g["G1"] and g["G2"] and g["G3"] and (g["G4"] or g4_soft)
        ctx = {"static": static, "rolling": rolling, "gates": g, "rows": rows,
               "pass": passed, "g4_soft": g4_soft,
               "alpha_mode": cfg.alpha_mode,
               "static_ext": static_ext_view(static_ext),
               "g4_note": "硬门或软化" + ("（DSS_G4_HARD=1 已恢复硬门）" if g4_hard else ""),
               "scratch5": scratch5,
               "median_err_static_selfcheck": med_static,
               "elapsed_sec": round((datetime.now() - t0).total_seconds(), 1)}
        (ROOT / "output" / "w3-影子回放验收.json").write_text(
            json.dumps({**ctx, "rolling": {k: v for k, v in ctx["rolling"].items()
                                           if k != "shifts"},
                        "rolling_shifts": {k: {kk: vv for kk, vv in v.items()
                                               if kk != "inproc_5h"} |
                                           {"inproc_5h_n": len(v["inproc_5h"])}
                                           for k, v in ctx["rolling"]["shifts"].items()}},
                       ensure_ascii=False, indent=2, default=str),
            encoding="utf-8")
        report = ROOT / "output" / "w3-影子回放验收.md"
        report.write_text(render_report(ctx), encoding="utf-8")
        print(f"\n[M9] 门判（α 基线 = {cfg.alpha_mode}）："
              f"G1={g['G1']} G2={g['G2']} G3={g['G3']} G4={g['G4']}"
              f"{'（G4 软化不阻断）' if g4_soft else ''} → 总判定 {'✅' if passed else '❌'}")
        print(f"[M9] 报告 → {report}（{ctx['elapsed_sec']}s）")
        return 0 if passed else 2
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
