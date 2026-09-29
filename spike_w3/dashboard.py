# -*- coding: utf-8 -*-
"""T6-lite（docs/07 v1.2/v1.3）：控制台四件套 + 5min JSON 快照 + 工位 ETA 菜单。

红线（docs/07 §6）：
- 02/03/predictor/stream_pipeline/common 零改动；本模块**零库写**（T6 v1 全量，
  快照/控制台/ETA 全内存或只读 SQL）。
- 活跃班次一律动态解析 active_shifts_at（attr_start <= t < end，同 02 run_rolling
  语义）；禁用 is_complete/_shifts_for_window 静态过滤（round 1 F-BLK-02 整改：
  24h 干跑窗 is_complete 会把跨日中班整条丢掉）。
- 冻结代码唯一允许微动 = alert_engine 纯读 deb_state()（docs/07 拍板⑥，T6.4 实施）。
- 口径单一事实源：池三分 = AlertEngine._split_pool（静态方法直接复用）；
  班次目标 = plan_loader.WeeklyPlan.plan_target（split_target 分摊，同引擎/M9 门）。
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from spike_w3.alert_engine import AlertEngine, grade as _grade_fn
from spike_w3.common import CST, EXIT_STATIONS, iso_week_label
from spike_w3.plan_loader import WeeklyPlan

WEEKDAY_CN = "一二三四五六日"
ANCHOR_HOURS = (3, 4, 5, 6)


def _require_aware(t: datetime):
    if t.tzinfo is None:
        raise TypeError("t 必须 aware（R7 同纪律）")


def active_shifts_at(plan, t: datetime) -> list:
    """t 时刻活跃班次（docs/07 v1.2 动态解析，round 1 F-BLK-02 整改核心）。

    语义（与 02 run_rolling 的 `s.attr_start <= cur < s.end` 逐字同义）：
    取 t 当日 + 前一日两日的 plan.shift_windows，过滤 attr_start <= t < end。
    前一日并集 = 跨日中班覆盖（中班 attr 16:30，end 次日 04:00——09-10 00:00–03:59
    仍属 09-09 中班）；日班/中班窗口不重叠（16:30 边界恰一班，日班 end 不含）。

    plan = plan_loader.WeeklyPlan 或 None；缺计划日 shift_windows 返 []（T1-lite
    兜底语义在 resolve 层，本函数纯班次窗口，不猜数）。
    """
    _require_aware(t)
    if plan is None:
        return []
    out = []
    for d in (t.date(), t.date() - timedelta(days=1)):
        for s in plan.shift_windows(d):
            if s.attr_start <= t < s.end:
                out.append(s)
    return out


def _next_shift(plan, t: datetime):
    """t 之后最近将开的班次（当日 + 次日 shift_windows，attr_start > t 最小者）。"""
    if plan is None:
        return None
    cands = []
    for d in (t.date(), t.date() + timedelta(days=1)):
        cands.extend(plan.shift_windows(d))
    nxt = [s for s in cands if s.attr_start > t]
    return min(nxt, key=lambda s: s.attr_start) if nxt else None


def _day_shift_for(plan, t: datetime):
    """day_total 轨 A 窗锚（[D 04:45, t]）：计划有日班用之；无（单中班日）→
    合成 04:45 attr 窗（F10 口径：A 窗起点恒 = 归属日 04:45）。"""
    ds = [s for s in (plan.shift_windows(t.date()) if plan else []) if s.kind == "day"]
    if ds:
        return ds[0]
    from spike_w3.common import Shift
    base = t.replace(hour=0, minute=0, second=0, microsecond=0)
    return Shift(kind="day", day=t.date(), type="11h",
                 work_start=base + timedelta(hours=5),
                 attr_start=base + timedelta(hours=4, minutes=45),
                 end=base + timedelta(hours=16, minutes=30))


def _jsonable(obj):
    """diagnose dict → JSON 可序列化（datetime → iso，None 保持）。"""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    return obj


def build_frame_ctx(pool, conn, guard, plan, cfg, t: datetime, pred_fn, a_fn, *,
                    section_map=None, alpha_value=1.0, alpha_mode="off",
                    alerts=None, alert_state=None, mode="wall",
                    max_ts=None, data_extras=None) -> dict:
    """每 5min 帧上下文（= §2.2 快照 schema 同构；控制台与快照共用，T6.2 直写）。

    参数：
    - pool：VehiclePool（.snapshot() → {body_id: VehicleState}）
    - guard：FreshnessGuard（.status/.label/.lag_minutes/.is_anomaly/.diagnose）
    - plan：WeeklyPlan 或 None（T1.4 load_week_plan）
    - pred_fn(t) → {"by_vehicle": [...]}（hook 侧 03._pool_view 闭包，零新增池外查询）
    - a_fn(shift, t) → int（predictor.a_of 有界 SQL）
    - alpha_value：off 模式恒 1.0；on = tracker committed 值
    - alerts / alert_state：hook 侧引擎产物透传（T6.4；本函数不评引擎）
    - mode：'wall' | 'replay'（replay → diagnose 零调用 + note 拦截，F-MED-05）

    口径（docs/07 v1.3 §2.1/2.2）：
    - pred.shift.target = split_target 分摊班次目标（同引擎/M9 门）；班外整对象 None。
    - pred.day_total 仅中班时段填充（A 窗 [D 04:45, t]，target = 全天 total 预填）。
    - 池三分 scope_end：活跃班次 end；班外 = 下一班次 end；无下一班次 = 有 rem 全瓶颈。
    - 锚点 = work_start+{3,4,5,6}h；pre 首锚点前 shift.grade=None（观测进度）。
    """
    _require_aware(t)
    if mode not in ("wall", "replay"):
        raise ValueError(f"mode 必须 wall/replay，当前: {mode!r}")
    if alpha_value is None or alpha_value <= 0:
        raise ValueError(f"alpha_value 必须 > 0，当前: {alpha_value!r}")

    acts = active_shifts_at(plan, t)
    active = acts[0] if acts else None
    nxt = _next_shift(plan, t) if active is None else None
    by_v = pred_fn(t)["by_vehicle"]
    snap = pool.snapshot()

    # —— 池 flag 分布（产品车） ——
    from spike_w3.predictor import PRODUCT_PREFIX
    flag_dist = {"0": 0, "1": 0, "2": 0}
    for bid, v in snap.items():
        if bid.startswith(PRODUCT_PREFIX) and v.flag in flag_dist:
            flag_dist[v.flag] += 1

    # —— 池三分（_split_pool 单一事实源） ——
    scope_end = active.end if active is not None else (nxt.end if nxt else None)
    if scope_end is not None:
        hit, bottleneck, no_eta = AlertEngine._split_pool(by_v, t, scope_end, alpha_value)
    else:
        hit = 0
        no_eta = sum(1 for v in by_v if v.get("rem_sec") is None)
        bottleneck = [v for v in by_v if v.get("rem_sec") is not None]
    bottleneck_sections: dict = {}
    bot_flag = {"1": 0, "2": 0}
    for v in bottleneck:
        sec = (section_map or {}).get(v["station"], v["station"])
        bottleneck_sections[sec] = bottleneck_sections.get(sec, 0) + 1
        if v.get("flag") in bot_flag:
            bot_flag[v["flag"]] += 1

    # —— pred.shift 轨 ——
    shift_obj = None
    if active is not None:
        A = a_fn(active, t)
        pred = A + hit
        targets = plan.plan_target(active.day) if plan is not None else (None, None)
        target = targets[1] if active.kind == "mid" else targets[0]
        delta = (pred - target) / target if target else None
        first_anchor = active.work_start + timedelta(hours=ANCHOR_HOURS[0])
        pre = t < first_anchor
        if pre:
            anchor_in = round((first_anchor - t).total_seconds() / 60)
        else:
            na = next((active.work_start + timedelta(hours=h) for h in ANCHOR_HOURS
                       if active.work_start + timedelta(hours=h) > t), None)
            anchor_in = round((na - t).total_seconds() / 60) if na else None
        shift_obj = {
            "shift_sid": f"{active.day.isoformat()}_{active.kind}",
            "kind": active.kind, "type": active.type,
            "attr_hm": active.attr_start.strftime("%H:%M"),
            "end_hm": active.end.strftime("%H:%M"),
            "A": A, "pred": pred, "target": target, "delta": delta,
            "grade": None if pre else (None if delta is None else _grade_fn(delta, cfg)),
            "anchor_state": "pre" if pre else "post",
            "anchor_in_min": anchor_in,
            "anchor_first_hm": first_anchor.strftime("%H:%M"),
        }

    # —— pred.day_total 轨（仅中班时段） ——
    row = plan.rows.get(t.date()) if plan is not None else None
    total = row.total if row is not None else None
    if active is not None and active.kind == "mid":
        A_all = a_fn(_day_shift_for(plan, t), t)
        pred_all = A_all + hit
        delta_all = (pred_all - total) / total if total else None
        day_total = {"A_all": A_all, "pred": pred_all, "target": total,
                     "delta": delta_all,
                     "grade": None if delta_all is None else _grade_fn(delta_all, cfg)}
    else:
        day_total = {"A_all": None, "pred": None, "target": total,
                     "delta": None, "grade": None}

    # —— guard 块（replay 显示层拦截，F-MED-05） ——
    if mode == "wall":
        g = {"status": guard.status, "label": guard.label,
             "lag_min": guard.lag_minutes, "is_anomaly": guard.is_anomaly,
             "diagnose": _jsonable(guard.diagnose(conn, t)),
             "diagnose_note": None}
    else:
        g = {"status": guard.status, "label": guard.label,
             "lag_min": guard.lag_minutes, "is_anomaly": guard.is_anomaly,
             "diagnose": None,
             "diagnose_note": "replay 模式: ETL 诊断不适用（时间穿透）"}

    return {
        "ts": t.isoformat(),
        "week": iso_week_label(t.date()),
        "weekday": f"周{WEEKDAY_CN[t.weekday()]}",
        "mode": mode,
        "next_shift": (None if nxt is None else
                       {"kind": nxt.kind, "attr_hm": nxt.attr_start.strftime("%H:%M")}),
        "pool": {"size": len(snap), "flag_dist": flag_dist,
                 "inproc_n": hit, "bottleneck_n": len(bottleneck), "no_eta_n": no_eta,
                 "bottleneck_flag_dist": bot_flag,
                 "bottleneck_sections": bottleneck_sections},
        "pred": {"shift": shift_obj, "day_total": day_total},
        "alpha": {"mode": alpha_mode, "value": alpha_value},
        "alert_state": alert_state,
        "alerts": alerts or [],
        "guard": g,
        "data": {"max_ts": max_ts.isoformat() if max_ts is not None else None,
                 **(data_extras or {})},
    }


def _fmt_eta(sec: float) -> str:
    """秒 → '1h 23m 20s'（h=0 省）。"""
    s = int(round(sec))
    h, m, s_ = s // 3600, (s % 3600) // 60, s % 60
    return (f"{h}h {m:02d}m {s_:02d}s" if h else f"{m}m {s_:02d}s")


def compute_eta(conn, body_id: str, target_station: str, as_of: datetime) -> dict:
    """工位 ETA（v1 仅 4 下线点；docs/07 v1.2 §2.3；独立连库只读，不读内存池）。

    返回 dict：
    - status='eta'：eta_sec/eta_label/source(车型|ALL)/n/station_now/flag/in_loop/
      loop_note/loop_back_sec（dim_loop_remaining 参考，可 None）
    - status='exited'：exit_ts（下线点事件时刻；最新事件在延续站时 = 下线点 MAX）
    - status='error'：error 文案（中间站未开放 / 残差样本不足 / 非产品车 / 不存在）

    口径（r2 F-R2-01 整改）：已出站判定 = 库内最近事件 ∈ EXIT ∪ POST_EXIT
    （实测 76% 出站车最新事件在延续站，仅判下线点会误判「仍在车间」）。
    残差 n≥30 纪律（W1 同口径）：车型行 n≥30 优先 → ALL → 报错不估。
    """
    _require_aware(as_of)
    from spike_w3.common import EXIT_STATIONS as _EX
    from spike_w3.common import POST_EXIT_STATIONS as _PX
    from spike_w3.predictor import PRODUCT_PREFIX
    out0 = {"body_id": body_id, "as_of": as_of.isoformat()}
    if not body_id.startswith(PRODUCT_PREFIX):
        return {**out0, "status": "error",
                "error": f"非产品车（无 {PRODUCT_PREFIX} 前缀）：{body_id}"}
    cur = conn.cursor()
    # —— 最近事件（<= as_of；同 ts 取 ID 大 = 消费顺序一致） ——
    cur.execute(
        'SELECT "DATE_EVT", "RW_STATION_ID", "BODY_TYPE", '
        'SUBSTRING("MDS_DATA" FROM 139 FOR 1) FROM ods.carbody_history '
        'WHERE "BODY_ID" = %s AND "DATE_EVT" <= %s '
        'ORDER BY "DATE_EVT" DESC, "ID" DESC LIMIT 1', (body_id, as_of))
    rows = cur.fetchall()
    if not rows:
        return {**out0, "status": "error",
                "error": f"车 {body_id} 不存在或 as-of 前无事件"}
    ts, station, body_type, flag = rows[0]
    # —— 已出站判定（F-R2-01：下线点 ∪ 出站延续站） ——
    if station in _EX:
        return {**out0, "status": "exited", "exit_ts": ts.isoformat(),
                "station_now": station}
    if station in _PX:
        cur.execute(
            'SELECT MAX("DATE_EVT") FROM ods.carbody_history '
            'WHERE "BODY_ID" = %s AND "RW_STATION_ID" IN (%s, %s, %s, %s)',
            (body_id, *_EX))
        exit_ts = cur.fetchall()[0][0]
        return {**out0, "status": "exited",
                "exit_ts": exit_ts.isoformat() if exit_ts else None,
                "station_now": station}
    # —— 目标校验（v1 仅 4 下线点，F-BLK-01 范围收缩） ——
    if target_station not in _EX:
        return {**out0, "status": "error",
                "error": (f"中间站 ETA v1 未开放（Phase 3：takt 图最短路）："
                          f"目标 {target_station} ∉ 4 下线点")}
    # —— 残差 n≥30 纪律：车型优先 → ALL ——
    cur.execute(
        'SELECT body_type, p50, n FROM dss.dim_remaining_time '
        'WHERE station = %s AND body_type IN (%s, %s)', (station, body_type, "ALL"))
    rem = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    src = None
    if body_type in rem and rem[body_type][1] >= 30:
        src = body_type
    elif "ALL" in rem and rem["ALL"][1] >= 30:
        src = "ALL"
    if src is None:
        return {**out0, "status": "error",
                "error": f"当前站 {station} 残差样本不足（n<30），不估"}
    p50, n = rem[src]
    in_loop = flag in ("1", "2")
    loop_note, loop_back = None, None
    if in_loop:
        loop_note = f"该车在返修循环中（flag={flag}）"
        cur.execute(
            'SELECT p50, n FROM dss.dim_loop_remaining '
            'WHERE body_type = %s AND flag = %s AND station = %s',
            (body_type, flag, station))
        lr = cur.fetchall()
        if lr:
            loop_back = float(lr[0][0])
    return {**out0, "status": "eta", "target": target_station,
            "station_now": station, "flag": flag, "body_type": body_type,
            "eta_sec": float(p50), "eta_label": _fmt_eta(float(p50)),
            "source": src, "n": int(n),
            "in_loop": in_loop, "loop_note": loop_note, "loop_back_sec": loop_back}


def main(argv=None, _connect=None) -> int:
    """CLI：python -m spike_w3.dashboard --eta <BODY_ID> <TARGET> [--as-of ISO]
    独立连库只读工具（F-MED-03：不读内存池、不依赖主循环进程）。"""
    import argparse
    import sys
    ap = argparse.ArgumentParser(prog="spike_w3.dashboard")
    ap.add_argument("--eta", nargs=2, metavar=("BODY_ID", "TARGET_STATION"),
                    help="工位 ETA（v1 仅 4 下线点）")
    ap.add_argument("--as-of", default=None, help="ISO 时刻（默认 now aware）")
    args = ap.parse_args(argv)
    if args.eta is None:
        ap.print_usage(sys.stderr)
        return 2
    body_id, target = args.eta
    as_of = (datetime.fromisoformat(args.as_of) if args.as_of
             else datetime.now().astimezone())
    if as_of.tzinfo is None:
        as_of = as_of.astimezone()
    conn = (_connect or _default_connect)()
    r = compute_eta(conn, body_id, target, as_of)   # CLI 短命进程：不显式 close（退出即回收）
    if r["status"] == "eta":
        extra = f"（{r['loop_note']}）" if r["loop_note"] else ""
        ref = (f" | 预计 ~{_fmt_eta(r['loop_back_sec'])} 回主线"
               if r["loop_back_sec"] is not None else "")
        print(f"ETA {r['body_id']} → {r['target']}: {r['eta_label']}"
              f"（P50 | {r['source']} n={r['n']} | 当前站 {r['station_now']}）"
              f"{extra}{ref}")
        return 0
    if r["status"] == "exited":
        print(f"{r['body_id']} 已于 {r['exit_ts']} 出站（当前事件站 {r['station_now']}）")
        return 0
    print(f"ERROR: {r['error']}")
    return 2


def _default_connect():
    from spike_w3.common import connect
    return connect()


# ---------------------------------------------------------------------------
# T6.4：主循环挂接（hook 闭包工厂 + 生产组件装配）
# ---------------------------------------------------------------------------

class EngineGuardAdapter:
    """把属性式 FreshnessGuard（.status 字符串）适配为引擎协议（.status() → 'FRESH'/'STALE')。

    STALE = 断流冻结（引擎零告警、去抖不推进）；NORMAL/RECOVERY = 数据在流 → FRESH。
    """
    def __init__(self, guard):
        self._g = guard

    def status(self):
        return "STALE" if self._g.status == "STALE" else "FRESH"


def make_t6_hook(plan, cfg, guard, engine=None, tracker=None, mode="replay",
                 run_tag="run", snapshot_dir=None, sink=None,
                 pv_fn=None, a_fn=None, a_map=None, tables=None,
                 comp_excl=None, section_map=None, exit_count_fn=None,
                 get_watermark=None, write_snapshots=True):
    """主循环每 5min 一帧的 hook 闭包工厂（T6.4；零库写，快照本地）。

    返回 hook(t, pool, conn_, cfg_, st) -> ctx（同 build_frame_ctx schema）。
    每轮顺序：pv（单次，注入或 _pool_view 真算）→ α（on 模式先 update 再取
    committed，同 02 run_rolling）→ 引擎 evaluate（STALE→[]）→ build_frame_ctx
    → write_snapshot → sink(render_frame)。

    注入点（测试）：pv_fn(t)->by_vehicle / a_fn(s,t)->int / a_map((kind,day)->int)
    （a_map 优先于 a_fn 推导）/ engine / tracker / exit_count_fn。
    生产：pv_fn=a_fn=None → _pool_view + predictor.a_of（conn 来自 hook 参数）。
    """
    from spike_w3.predictor import obs_window

    if a_fn is None and a_map is not None:
        a_fn = lambda s, t_: a_map.get((s.kind, s.day), 0)

    def hook(t, pool, conn_, cfg_, st):
        _require_aware(t)
        acts = active_shifts_at(plan, t)
        active = acts[0] if acts else None
        # pv 单次（引擎与 ctx 共用，F9 零新增查询）
        if pv_fn is not None:
            pv = pv_fn(t)
        else:
            pv = _w3_03()._pool_view(pool, t, tables, comp_excl, cfg_)
        if a_fn is None:
            from spike_w3 import predictor
            a_fn_local = lambda s, t_: predictor.a_of(conn_, s, t_)
        else:
            a_fn_local = a_fn
        # α（on 模式：先 update 再取 committed；off 恒 1.0 且 tracker 不推进）
        alpha = 1.0
        if tracker is not None and cfg_.alpha_mode == "on" and active is not None:
            w = obs_window(t, active, tracker.window_hours)
            if w is not None:
                n = exit_count_fn(conn_, w[0], w[1]) if exit_count_fn else 0
                tracker.update(t, active, n, w[2])
            alpha = tracker.alpha()
        # 引擎（STALE 冻结 → []；去抖按 scope-key 隔离）
        alerts_d = []
        if engine is not None and active is not None:
            day_done = []
            for d_ in (t.date(), t.date() - timedelta(days=1)):
                day_done.extend(s for s in plan.shift_windows(d_)
                                if s.kind == "day" and s.end <= t)

            def pred_fn(s, t_):
                return pv
            m03 = _w3_03()
            alerts_d = [m03.alert_to_dict(a) for a in
                        engine.evaluate(t, [active], day_done, pred_fn, a_fn_local)]
        ctx = build_frame_ctx(pool, conn_, guard, plan, cfg_, t,
                              pred_fn=(lambda t_: pv), a_fn=a_fn_local,
                              section_map=section_map, alpha_value=alpha,
                              alpha_mode=cfg_.alpha_mode, alerts=alerts_d,
                              alert_state=(engine.deb_state(t)
                                           if engine is not None else None),
                              mode=mode, max_ts=(st or {}).get("max_ts"),
                              data_extras={"watermark":
                                           get_watermark() if get_watermark else None})
        if write_snapshots and snapshot_dir is not None:
            write_snapshot(ctx, run_tag, snapshot_dir, t)
        if sink is not None:
            sink(render_frame(ctx))
        return ctx
    return hook


def build_t6_components(conn, cfg, plan, guard, mode="replay",
                        snapshot_dir=None, sink=None, run_tag_src_ts=None,
                        get_watermark=None):
    """生产路径装配（真库只读，零库写）：tables/comp_excl/section_map/engine/tracker
    + run_tag 防覆盖 + hook。返回 dict(hook, run_tag, tag_note, engine, tracker)。

    plan = plan_loader.WeeklyPlan（build_frame_ctx 用）；引擎 plan dict 由其派生。
    """
    import importlib.util
    from pathlib import Path as _P
    from spike_w3.predictor import AlphaTracker, build_takt_baseline
    from spike_w3.alert_engine import AlertEngine, build_station_section
    root = _P(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location(
        "w2_replay_main_t6", root / "spike_w2" / "02_replay_shift.py")
    w2m = importlib.util.module_from_spec(spec)
    import sys as _sys
    _sys.modules["w2_replay_main_t6"] = w2m
    spec.loader.exec_module(w2m)

    tables = w2m.load_tables(conn)
    cur = conn.cursor()
    cur.execute('SELECT body_type, station FROM dss.v2_comp_excl')
    comp_excl = frozenset(cur.fetchall())
    cur.close()
    section_map = {r["station"]: r["section"] for r in build_station_section(conn)}
    # 引擎 plan dict（由 WeeklyPlan 派生）
    engine_plan = {}
    for d, r in (plan.rows if plan is not None else {}).items():
        engine_plan[d] = {"total": r.total,
                          "day": f"{r.day_h}h" if r.day_h else None,
                          "mid": f"{r.mid_h}h" if r.mid_h else None}
    eng_guard = EngineGuardAdapter(guard)
    engine = AlertEngine(engine_plan, cfg, section_map, guard=eng_guard, alpha=1.0)
    tracker = None
    if cfg.alpha_mode == "on":
        baseline = build_takt_baseline(conn, cfg.learn_win_start, cfg.learn_win_end,
                                       cfg.alpha_min_samples)
        tracker = AlphaTracker(baseline, window_hours=cfg.alpha_window_hours,
                               ema_beta=cfg.alpha_ema_beta,
                               clamp_min=cfg.alpha_clamp_min,
                               clamp_max=cfg.alpha_clamp_max)
    # run_tag 防覆盖（F-LOW-01）
    snap_root = snapshot_dir or "output/snapshots"
    ts_src = run_tag_src_ts or datetime.now().astimezone()
    run_tag, tag_note = resolve_run_tag(cfg, ts_src, _P(snap_root))

    def _exit_count(conn_, a, b):
        c = conn_.cursor()
        c.execute(
            'SELECT COUNT(DISTINCT "BODY_ID") FROM ods.carbody_history '
            'WHERE "RW_STATION_ID" IN (%s,%s,%s,%s) '
            'AND "DATE_EVT" >= %s AND "DATE_EVT" <= %s',
            (*EXIT_STATIONS, a, b))
        r = c.fetchall()
        c.close()
        return int(r[0][0]) if r else 0

    hook = make_t6_hook(plan, cfg, guard, engine=engine, tracker=tracker,
                        mode=mode, run_tag=run_tag, snapshot_dir=snap_root,
                        sink=sink, tables=tables, comp_excl=comp_excl,
                        section_map=section_map, exit_count_fn=_exit_count,
                        get_watermark=get_watermark)
    return {"hook": hook, "run_tag": run_tag, "tag_note": tag_note,
            "engine": engine, "tracker": tracker}


def _w3_03():
    """延迟加载 03_alert_backtest（取 _pool_view / alert_to_dict，单一事实源）。"""
    import importlib.util
    import sys as _sys
    p = Path(__file__).resolve().parent / "03_alert_backtest.py"
    if "w3_03_dashboard" in _sys.modules:
        return _sys.modules["w3_03_dashboard"]
    spec = importlib.util.spec_from_file_location("w3_03_dashboard", p)
    m = importlib.util.module_from_spec(spec)
    _sys.modules["w3_03_dashboard"] = m
    spec.loader.exec_module(m)
    return m


def _alpha_disp(a: dict) -> str:
    return "1.0" if a["mode"] == "off" else f"{a['value']:.2f}"


# ---------------------------------------------------------------------------
# T6.2：快照写入 / schema 校验 / tag 防覆盖（docs/07 §2.2）
# ---------------------------------------------------------------------------

def resolve_run_tag(cfg, ts: datetime, snapshot_dir: Path) -> tuple:
    """(最终 tag, 防覆盖注记)（F-LOW-01，M5 留痕审计不可逆覆盖防护）。

    cfg.run_tag 空 → 自动生成 YYYYMMDD-HHMMSS（ts aware 墙钟/游标）；目标目录
    已有 snap_* → 追加 -r2/-r3 递增（检测时机 = 启动一次，单进程无并发竞争）。
    """
    _require_aware(ts)
    base = cfg.run_tag or ts.strftime("%Y%m%d-%H%M%S")
    tag, n = base, 1
    while any((Path(snapshot_dir) / tag).glob("snap_*")):
        n += 1
        tag = f"{base}-r{n}"
    note = f"快照目录已存在同名 tag，改用 {tag}（首份留痕不动）" if n > 1 else None
    return tag, note


def _json_default(o):
    """快照序列化兜底（T6.2）：DB numeric → Decimal 转 float；date/datetime → ISO。"""
    from decimal import Decimal
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return str(o)


def write_snapshot(ctx: dict, run_tag: str, snapshot_dir: Path, ts: datetime) -> Path:
    """原子写 tmp（同目录 .tmp 前缀）→ os.replace；文件名 snap_{ts紧凑}.json。

    同 run 同 ts 重写 = 幂等覆盖（单进程 5min 一写无竞争）；跨 run 由
    resolve_run_tag 防覆盖拦截。ctx 不修改（run_tag 注入在副本上）。
    """
    _require_aware(ts)
    d = Path(snapshot_dir) / run_tag
    d.mkdir(parents=True, exist_ok=True)
    final = d / f"snap_{ts.strftime('%Y%m%dT%H%M%S')}.json"
    obj = dict(ctx)
    obj["run_tag"] = run_tag
    tmp = d / f".tmp_{final.name}"
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2,
                              default=_json_default), encoding="utf-8")
    os.replace(tmp, final)
    return final


_SNAP_TOP_KEYS = {"ts", "run_tag", "week", "weekday", "mode", "next_shift", "pool",
                  "pred", "alpha", "alert_state", "alerts", "guard", "data"}
_SHIFT_KEYS = {"shift_sid", "kind", "type", "attr_hm", "end_hm", "A", "pred",
               "target", "delta", "grade", "anchor_state", "anchor_in_min",
               "anchor_first_hm"}
_GUARD_STATES = {"NORMAL", "STALE", "RECOVERY"}
_LEVELS = {"green", "yellow", "red"}


def validate_snapshot(obj: dict) -> list:
    """快照 schema 结构校验（键/类型/枚举/可空性/恒等式）→ 错误清单（空 = 通过）。

    只验结构不验业务值（target/delta 数值合理性归 G2 事后重算门）。G1 门逐文件调用。
    """
    errs = []
    if not isinstance(obj, dict):
        return ["根不是 dict"]
    missing = _SNAP_TOP_KEYS - set(obj)
    if missing:
        errs.append(f"缺顶层键: {sorted(missing)}")
        return errs
    # mode / week / weekday
    if obj["mode"] not in ("wall", "replay"):
        errs.append(f"mode 枚举: {obj['mode']!r}")
    try:
        datetime.fromisoformat(obj["ts"])
    except Exception:
        errs.append(f"ts 非法 ISO: {obj['ts']!r}")
    # pool
    p = obj["pool"]
    if not all(isinstance(p.get(k), int) and p[k] >= 0 for k in
               ("size", "inproc_n", "bottleneck_n", "no_eta_n")):
        errs.append("pool 计数字段须 int≥0")
    elif p["inproc_n"] + p["bottleneck_n"] + p["no_eta_n"] > p["size"]:
        errs.append("pool 三分和 > size（产品车 ⊆ 全池）")
    if sum(p.get("bottleneck_sections", {}).values()) != p["bottleneck_n"]:
        errs.append("bottleneck_sections 和 != bottleneck_n（恒等式）")
    if set(p.get("flag_dist", {})) != {"0", "1", "2"}:
        errs.append("flag_dist 键须 0/1/2")
    # pred
    pr = obj["pred"]
    if set(pr) != {"shift", "day_total"}:
        errs.append("pred 须恰好 shift/day_total 双轨")
    sh = pr["shift"]
    if sh is not None:
        if set(sh) != _SHIFT_KEYS:
            errs.append(f"shift 键集异常: {sorted(set(sh) ^ _SHIFT_KEYS)}")
        else:
            if sh["kind"] not in ("day", "mid"):
                errs.append(f"shift.kind 枚举: {sh['kind']!r}")
            if sh["anchor_state"] not in ("pre", "post"):
                errs.append(f"anchor_state 枚举: {sh['anchor_state']!r}")
            if sh["grade"] is not None and sh["grade"] not in _LEVELS:
                errs.append(f"grade 枚举: {sh['grade']!r}")
            if sh["grade"] is not None and sh["anchor_state"] != "post":
                errs.append("pre 锚点 grade 必须 null（观测进度，F-MED-04）")
            if not isinstance(sh["A"], int) or not isinstance(sh["pred"], int):
                errs.append("shift A/pred 须 int")
            if sh["target"] is not None and not isinstance(sh["target"], int):
                errs.append("shift.target 须 int 或 null")
    dt = pr["day_total"]
    if set(dt) != {"A_all", "pred", "target", "delta", "grade"}:
        errs.append("day_total 键集异常")
    else:
        if dt["pred"] is not None and dt["A_all"] is None:
            errs.append("day_total.pred 非 null → A_all 必非 null")
        if dt["grade"] is not None and dt["grade"] not in _LEVELS:
            errs.append(f"day_total.grade 枚举: {dt['grade']!r}")
    # alpha
    a = obj["alpha"]
    if not (set(a) == {"mode", "value"} and a["mode"] in ("off", "on")
            and isinstance(a["value"], (int, float)) and a["value"] > 0):
        errs.append(f"alpha 结构异常: {a!r}")
    # alert_state
    st = obj["alert_state"]
    if st is not None:
        for k, v in st.items():
            if (set(v) != {"current_level", "pending_level", "pending_min"}
                    or v["current_level"] not in _LEVELS
                    or (v["pending_level"] is not None and v["pending_level"] not in _LEVELS)
                    or (v["pending_min"] is not None
                        and not isinstance(v["pending_min"], (int, float)))):
                errs.append(f"alert_state[{k}] 结构异常: {v!r}")
                break
    # alerts / guard / data
    if not isinstance(obj["alerts"], list):
        errs.append("alerts 须 list")
    g = obj["guard"]
    if g["status"] not in _GUARD_STATES:
        errs.append(f"guard.status 枚举: {g['status']!r}")
    if obj["mode"] == "replay" and g["diagnose"] is not None:
        errs.append("replay 快照 diagnose 必须 null（F-MED-05）")
    if obj["mode"] == "wall" and g["diagnose"] is None:
        errs.append("wall 快照 diagnose 必须非 null")
    return errs


def render_frame(ctx: dict) -> str:
    """帧渲染（纯函数，T6.1；§2.1 四件套；首行固定 [DSS] 前缀 = G1 机械判据）。"""
    ts_hm = ctx["ts"][11:16]
    p, pr, a, g = ctx["pool"], ctx["pred"], ctx["alpha"], ctx["guard"]
    L = []
    max_hm = ctx["data"]["max_ts"][11:16] if ctx["data"].get("max_ts") else "—"
    lag = g.get("lag_min")
    lag_s = f"{lag:.1f}" if isinstance(lag, (int, float)) else "—"
    alpha_s = f"α {a['mode']}" if a["mode"] == "off" else f"α {a['value']:.2f}"
    L.append(f"[DSS] {ctx['ts'][:10]} {ts_hm} | {ctx['week']} {ctx['weekday']} | "
             f"数据截至 {max_hm}（lag {lag_s}min）| {g['status']} {g['label']} | {alpha_s}")

    # ① 新鲜度横幅（replay → 不适用注记）
    if g.get("diagnose") is None:
        L.append("  诊断: ETL 诊断不适用·时间穿透（replay 模式）")
    else:
        d = g["diagnose"]
        s1 = "✓" if d["s1_sync_job"]["status"] == "success" else "✗"
        gap = d["s2_watermark"]["gap"]
        s2 = f"✓(缺口0)" if gap == 0 else f"✗(缺口{gap})"
        s3l = d["s3_lag_min"]
        s3 = f"✓(lag {s3l:.1f})" if s3l is not None else "✗"
        s4 = "✓" if d["s4_lag_min"] is not None else "✗"
        L.append(f"  诊断: 在线 S1{s1} S2{s2} S3{s3} S4{s4} | 判定 {d['verdict']}")

    # ②③ 班次进度 + Δ 语义分层
    sh = pr["shift"]
    if sh is None:
        ns = ctx.get("next_shift")
        if ns is not None:
            kind_cn = "日班" if ns["kind"] == "day" else "中班"
            L.append(f"  班外（下一班次 {ns['attr_hm']} {kind_cn}开）")
        else:
            L.append("  班外（无计划班次）")
    else:
        kind_cn = "日班" if sh["kind"] == "day" else "中班"
        target_s = "—" if sh["target"] is None else str(sh["target"])
        L.append(f"  班次 {kind_cn} {sh['type']} {sh['attr_hm']}–{sh['end_hm']} | "
                 f"A={sh['A']} pred(α={_alpha_disp(a)})={sh['pred']} 目标={target_s}")
        delta_s = "—" if sh["delta"] is None else f"{sh['delta']:+.0%}"
        if sh["anchor_state"] == "pre":
            L.append(f"  Δ={delta_s} 观测进度（首锚点 {sh['anchor_first_hm']} 剩 "
                     f"{sh['anchor_in_min']}min·爬坡期）")
        else:
            grade_cn = {"red": "红", "yellow": "黄", "green": "绿"}.get(sh["grade"], "—")
            L.append(f"  Δ={delta_s} {grade_cn}")
        dt = pr["day_total"]
        if dt["pred"] is not None:
            dt_delta = "—" if dt["delta"] is None else f"{dt['delta']:+.0%}"
            dt_grade = {"red": "红", "yellow": "黄", "green": "绿"}.get(dt["grade"], "—")
            L.append(f"  全天累计: A={dt['A_all']} pred={dt['pred']} 目标={dt['target']} "
                     f"Δ={dt_delta} {dt_grade}")

    # ④ 池三分 + 瓶颈 flag 分层 + 瓶颈段 top5
    bfd = p.get("bottleneck_flag_dist", {"1": 0, "2": 0})
    L.append(f"  池 {p['size']}：在制 {p['inproc_n']} / 瓶颈 {p['bottleneck_n']}"
             f"（返修 {bfd.get('1', 0)} 黑顶 {bfd.get('2', 0)}）/ no-ETA {p['no_eta_n']}")
    if p["bottleneck_sections"]:
        top = sorted(p["bottleneck_sections"].items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        L.append("  瓶颈段 top" + str(len(top)) + ": "
                 + " ".join(f"{k}×{v}" for k, v in top))

    # 最近确认告警
    if ctx.get("alerts"):
        fa = ctx["alerts"][0]
        extra = f"（+{len(ctx['alerts']) - 1} 条）" if len(ctx["alerts"]) > 1 else ""
        L.append(f"  最近确认告警: {fa.get('level', '?')} {fa.get('sid', '?')}{extra}")
    else:
        L.append("  最近确认告警: —")
    return "\n".join(L)


if __name__ == "__main__":
    raise SystemExit(main())
