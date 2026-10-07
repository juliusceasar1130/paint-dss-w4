# -*- coding: utf-8 -*-
"""W2 班次级回放：第 5 小时（多锚点 3/4/5/6h）预测班次下线量 vs 实际。
门（D1'）= 全厂逐班 |err%| 中位数 ≤ 10% 且 主车型（逐班实际量中位数 ≥ 30）各自中位数 ≤ 10%。

用法：python spike_w2\\02_replay_shift.py [--replay-start 2026-08-16] [--replay-end 2026-09-19]
因果切分：主分布/环/takt/环时长 = W1 学习表（学习窗 04-14→08-15）；周度 p / 线概率 =
每回放周前 8 ISO 周（dss.v2_*，Task 3）。ev_base 窗 = [回放始-7d, 回放末+1d]
（7d ≥ 最长行程 120h：窗内下线的车其 entry 事件必在窗内）。
exit 0 = 门过；exit 2 = 门未过（报告含逐班/锚点/类型/标记分桶 + 类型审计 + 归属覆盖）。
计数口径（修订 10，2026-09-21）：在制车计 t + 模型剩余 ≤ 窗末（从锚点起算；direct/⑥b/Σ 全同改），
排除过期 ETA（eta < 锚点）计入当班。
"""
import argparse
import bisect
import json
import math
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spike_w2 import common, eta_v2, shifts
from spike_w1 import eta as eta_w1, sqlbase

CST = ZoneInfo("Asia/Shanghai")
GATE_PCT = 0.10                # D1'：±10%
MARGIN_DAYS = 7                # ≥ 最长行程 120h
MAIN_MODEL_MIN_MEDIAN = 30     # D1'：主车型门槛（逐班实际量中位数 ≥ 30）

# 日班型判定：候选开班时刻的 15min 桶事件数（原始流，产品车）
SQL_DAYTYPE = """
SELECT date_trunc('day', "DATE_EVT") AS d,
       count(*)::INT AS total,
       count(*) FILTER (WHERE "DATE_EVT" >= date_trunc('day', "DATE_EVT") + interval '5 hours'
                         AND "DATE_EVT" <  date_trunc('day', "DATE_EVT") + interval '5 hours 15 minutes')::INT AS c05,
       count(*) FILTER (WHERE "DATE_EVT" >= date_trunc('day', "DATE_EVT") + interval '6 hours'
                         AND "DATE_EVT" <  date_trunc('day', "DATE_EVT") + interval '6 hours 15 minutes')::INT AS c06,
       count(*) FILTER (WHERE "DATE_EVT" >= date_trunc('day', "DATE_EVT") + interval '8 hours'
                         AND "DATE_EVT" <  date_trunc('day', "DATE_EVT") + interval '8 hours 15 minutes')::INT AS c08
FROM ods.carbody_history
WHERE "BODY_ID" LIKE '78202%%'
  AND "DATE_EVT" >= %s AND "DATE_EVT" <= %s
GROUP BY 1 ORDER BY 1;
"""

# 中班型判定：中班段 [D 16:30, D+1 04:45) 末条产品车事件距 D 16:30 的分钟数
# （I-1：EXTRACT(EPOCH) 为秒，/60 四舍五入到分钟后再与 300/510/630 阈值比较）
SQL_MIDLAST = """
WITH p AS (
    SELECT "DATE_EVT" AS t,
           CASE WHEN "DATE_EVT" >= date_trunc('day', "DATE_EVT") + interval '16 hours 30 minutes'
                THEN date_trunc('day', "DATE_EVT")
           WHEN "DATE_EVT" <  date_trunc('day', "DATE_EVT") + interval '4 hours 45 minutes'
                THEN date_trunc('day', "DATE_EVT") - interval '1 day'
           ELSE NULL END AS mid_day
    FROM ods.carbody_history
    WHERE "BODY_ID" LIKE '78202%%'
      AND "DATE_EVT" >= %s AND "DATE_EVT" <= %s
)
SELECT mid_day,
       (max(EXTRACT(EPOCH FROM (t - (mid_day + interval '16 hours 30 minutes')))) / 60)::INT AS last_min
FROM p WHERE mid_day IS NOT NULL
GROUP BY mid_day ORDER BY mid_day;
"""

# 11IS A/B 并行线 rel_diff（D4：仅报告检查，不改表）；cohort 基于 tmp_ev_base
SQL_11IS = """
WITH cohort AS (
    SELECT body,
           bool_or(station = '11IS240') AS cA,
           bool_or(station = '11IS310') AS cB
    FROM tmp_ev_base GROUP BY body
),
s AS (
    SELECT e.bt, e.station, EXTRACT(EPOCH FROM (e.exit_t - e.t))::BIGINT AS rem, c.cA, c.cB
    FROM tmp_ev_base e JOIN cohort c USING (body)
    WHERE e.t < e.exit_t
      AND e.station NOT LIKE '11IS%%' AND e.station NOT LIKE '08IS%%' AND e.station NOT LIKE '09IS%%'
      AND c.cA <> c.cB
)
SELECT bt, station,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY rem) FILTER (WHERE cA) AS p50_a,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY rem) FILTER (WHERE cB) AS p50_b,
       count(*) FILTER (WHERE cA) AS n_a, count(*) FILTER (WHERE cB) AS n_b
FROM s GROUP BY bt, station
HAVING count(*) FILTER (WHERE cA) >= 100 AND count(*) FILTER (WHERE cB) >= 100
ORDER BY bt, station;
"""


def load_tables(conn) -> dict:
    """W1 表 + v2 表 + W4 净延误表 → eta_v2/eta_w1 行形状 dict（车型在 key 末位，n 在行尾）。

    W4.3b：新增 expected_delay 键 = {(station, model): (delay_mean_s, delay_p50_s, n_fut, n_clean)}
    （三层行：车型/平台前缀/'ALL' 全量装载；⚠️ 列序与 rem 族相反——mean 在前、p50 第二，
    消费必须走 eta_v2.resolve_expected_delay 显式索引 + 双人群双门槛，禁行内解析；
    W4.4b 修订 2026-10-06：两档均消费 delay_p50_s——shift 档原 mean 列经 G2 阈值计数实测证伪）。"""
    cur = conn.cursor()

    def rows(sql):
        cur.execute(sql)
        return cur.fetchall()

    fut_all = {(r[1], r[0]): (r[2], r[3], r[4])
               for r in rows("SELECT body_type, station, p1, p2, n "
                             "FROM dss.v2_future_flag WHERE week = 'ALL'")}
    return {
        # W1 形状（eta_w1.estimate_vehicle 消费；键名必须与 W1 eta.py 完全一致：
        # rem/loop_rem/loop_dur/takt/fut——"fut" 取 v2 全窗 p 作别名，供 W1 直接/Σ 逻辑用）
        "rem": {(r[1], r[0]): (r[2], r[3], r[4], r[5])
                for r in rows("SELECT body_type, station, p50, mean, p90, n "
                              "FROM dss.dim_remaining_time")},
        "loop_rem": {(r[2], r[1], r[0]): (r[3], r[4], r[5], r[6])
                     for r in rows("SELECT body_type, flag, station, p50, mean, p90, n "
                                   "FROM dss.dim_loop_remaining")},
        "loop_dur": {(r[1], r[0]): (r[2], r[3], r[4], r[5])
                     for r in rows("SELECT body_type, flag, l, l_mean, l_p90, n FROM dss.dim_loop_duration")},
        "takt": {(r[0], r[1], r[2]): (r[3], r[4], r[5], r[6])
                 for r in rows("SELECT frm, to_station, body_type, p50, mean, p90, n "
                               "FROM dss.dim_station_takt")},
        "fut": fut_all,
        # v2 形状（eta_v2 消费）
        "fut_week": {(r[1], r[0], r[2]): (r[3], r[4], r[5])
                     for r in rows("SELECT body_type, station, week, p1, p2, n "
                                   "FROM dss.v2_future_flag WHERE week <> 'ALL'")},
        "fut_all": fut_all,
        "cohort": {(r[1], r[2], r[0]): (r[3], r[4], r[5], r[6])
                   for r in rows("SELECT body_type, station, cohort, p50, mean, p90, n "
                                 "FROM dss.v2_cohort_remaining")},
        "lineprob": {(r[0], r[1]): (r[2], r[3])
                     for r in rows("SELECT body_type, week, w08, n FROM dss.v2_line_prob")},
        "quality": {(r[0], r[1]): r[4]
                    for r in rows("SELECT body_type, station, n_model, p1, mark "
                                  "FROM dss.v2_station_quality")},
        # W4.2c 净延误表（doc10 合流日）；三层行全量装载，消费走 resolve_expected_delay
        "expected_delay": {(r[1], r[0]): (r[2], r[3], r[4], r[5])
                           for r in rows("SELECT body_type, station, delay_mean_s, delay_p50_s, "
                                         "sample_n, sample_n_clean "
                                         "FROM dss.dim_expected_delay")},
    }


def _eta_pair(t_e, st_e, fl_e, model, week, tables, comp_excl):
    """W4.3d 双臂分发（docs/15 W4.3d；direct_nocomp 重定义——agy 建议 2 + cc 阻断项 3）。

    DUAL_RING=1: (统一引擎净化 flag 直通, 净延误关闭基线 = expected_delay 强制 0)——
      direct_nocomp 现度量 expected_delay 表的净效果（旧「豁免列表」语义作废：
      新引擎无 p×L 补偿，无豁免对象；表缺失/门槛不过时两臂相等，对比恒平，语义成立）；
    DUAL_RING=0: (旧引擎 comp_excl 语义保持, 旧引擎不豁免旧 ⑥b 语义)，
      ND=1 时两臂同走 doc10 retrofit（p×L → expected_delay）。
    返回 (eta_d, eta_d0, used)——used = d 臂 cohort 命中标志（cohort_used 计数保持）。"""
    if eta_v2.dual_ring_enabled():
        eta_d, used = eta_v2.estimate_for_replay(t_e, st_e, fl_e, model, week, tables)
        tables_nc = {**tables, "expected_delay": {}}
        eta_d0, _ = eta_v2.estimate_for_replay(t_e, st_e, fl_e, model, week, tables_nc)
        return eta_d, eta_d0, used
    if eta_v2.use_net_delay_enabled():
        eta_d, used = eta_v2.estimate_event_net_delay(t_e, st_e, fl_e, model, week, tables, comp_excl)
        eta_d0, _ = eta_v2.estimate_event_net_delay(t_e, st_e, fl_e, model, week, tables, frozenset())
    else:
        eta_d, used = eta_v2.estimate_event(t_e, st_e, fl_e, model, week, tables, comp_excl)
        eta_d0, _ = eta_v2.estimate_event(t_e, st_e, fl_e, model, week, tables, frozenset())
    return eta_d, eta_d0, used


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--replay-start", default="2026-08-16")
    ap.add_argument("--replay-end", default="2026-09-19")
    a = ap.parse_args()
    replay_start = datetime.fromisoformat(a.replay_start).replace(tzinfo=CST)
    replay_end = datetime.fromisoformat(a.replay_end).replace(hour=23, minute=59, second=59, tzinfo=CST)
    ev_start = replay_start - timedelta(days=MARGIN_DAYS)
    ev_end = (replay_end + timedelta(days=1)).replace(hour=23, minute=59, second=59)

    conn = common.connect()
    cur = conn.cursor()
    print(f"ev_base（{ev_start:%Y-%m-%d} → {ev_end:%Y-%m-%d}，左 7d 前置边；右 +1d 覆盖末班窗末下线）…")
    cur.execute(sqlbase.ev_base_sql(ev_start, ev_end), (ev_start, ev_end))
    cur.execute("SELECT count(*) FROM tmp_ev_base")
    print("  行数:", cur.fetchone()[0])

    # --- 车辆 span（entry_t/exit_t/bt；exit 缺 = 数据末端在制） ---
    cur.execute("""SELECT body, max(bt) AS bt,
                          EXTRACT(EPOCH FROM min(t) FILTER (WHERE station IN ('01IS205','1L360RB','01IS045')))::BIGINT,
                          EXTRACT(EPOCH FROM min(t) FILTER (WHERE station IN ('K3IS140','1J440RB','K2IS075','K1IS135')))::BIGINT,
                          (array_agg(station ORDER BY t)
                           FILTER (WHERE station IN ('01IS205','1L360RB','01IS045')))[1] AS entry_st
                   FROM tmp_ev_base GROUP BY body""")
    spans = pd.DataFrame(cur.fetchall(),
                         columns=["body", "bt", "entry_t", "exit_t", "entry_st"]).set_index("body")
    bodies = spans.index.to_numpy()
    entry_arr = spans["entry_t"].to_numpy(dtype=np.int64)
    exit_arr = spans["exit_t"].to_numpy(dtype=np.float64)
    exit_arr = np.where(np.isnan(exit_arr), np.inf, exit_arr)
    bt_arr = spans["bt"].to_numpy()

    # --- 事件流（flag 0/1/2，与 W1 回放同口径；- / 3 共 0.08% 不进估计） ---
    print("拉取事件流 …")
    cur.execute("""SELECT body, station, EXTRACT(EPOCH FROM t)::BIGINT, flag
                   FROM tmp_ev_base WHERE flag IN ('0', '1', '2') ORDER BY body, t, id""")
    evts = {}
    for body, st, t, fl in cur.fetchall():
        evts.setdefault(body, []).append((int(t), st, fl))
    ts_lists = {b: [e[0] for e in v] for b, v in evts.items()}   # bisect 缓存（每车一次）
    rework_arr = np.array([any(fl in ("1", "2") for _, _, fl in evts.get(b, ()))
                           for b in bodies], dtype=bool)         # 返工车（15.6%，D1' 组显示）
    tables = load_tables(conn)
    print("学习表行数:", {k: len(v) for k, v in tables.items()})
    cur.execute("SELECT body_type, station FROM dss.v2_comp_excl")
    comp_excl = frozenset(cur.fetchall())
    print("comp_excl（⑥b 豁免）格数:", len(comp_excl))

    # --- 逐车 Σ 估计器预计算（后见路径，W1 语义；仅诊断） ---
    print("逐车 Σ 估计器预计算 …")
    sum_idx = {}   # body -> (ts_list, s_med_list, s_mean_list)，索引对齐 evts[body] 前 n-1 个事件
    n_sum_ok = 0
    for body, evlist in evts.items():
        if body not in spans.index or np.isnan(spans.loc[body, "exit_t"]):
            continue
        evs = [eta_w1.Event(t, st, fl) for t, st, fl in evlist]
        out = eta_w1.estimate_vehicle(evs, int(spans.loc[body, "exit_t"]), tables,
                                      spans.loc[body, "bt"])
        sum_idx[body] = ([t for t, _, _ in evlist],
                         [r.eta_sum_median for r in out],
                         [r.eta_sum_mean for r in out])
        n_sum_ok += 1
    print(f"  Σ 可算车数 = {n_sum_ok}/{len(evts)}（缺 exit 的在制车无 Σ，仅诊断器受影响）")

    # --- 班次日历（类型自学 + 完整班次 + 实际量） ---
    print("班次类型自学 …")
    cur.execute(SQL_DAYTYPE, (ev_start, replay_end + timedelta(days=1)))
    daytype = {r[0].date(): {"total": r[1], "05:00": r[2], "06:00": r[3], "08:00": r[4]}
               for r in cur.fetchall()}
    cur.execute(SQL_MIDLAST, (ev_start, replay_end + timedelta(days=1)))
    midlast = {r[0].date(): r[1] for r in cur.fetchall()}
    # I-1 护栏：SQL 已 /60；中班段 [16:30, 04:45) 物理上限 735min，超限即单位漂移
    _ml_max = max((v for v in midlast.values() if v is not None), default=None)
    assert _ml_max is None or _ml_max <= 800, f"midlast 超物理上限（分钟）: {_ml_max}"

    shifts_all = []
    day_audit = []    # (日, 15min桶计数, 日型, 中型, 中班末事件min) 报告审计表用
    for d in pd.date_range(replay_start.date(), replay_end.date(), freq="D"):
        d = d.date()
        dt_row = daytype.get(d, {"total": 0, "05:00": 0, "06:00": 0, "08:00": 0})
        dt = shifts.detect_day_type({k: dt_row[k] for k in ("05:00", "06:00", "08:00")},
                                    dt_row["total"])
        mt = shifts.detect_mid_type(midlast.get(d))
        day_audit.append((d, dt_row, dt, mt, midlast.get(d)))
        for s in shifts.shift_windows(d, dt, mt):
            actual = int(((exit_arr >= s.attr_start.timestamp())
                          & (exit_arr <= s.end.timestamp())).sum())
            shifts_all.append((s, dt_row, midlast.get(d), actual))

    eligible, excluded = [], []
    for s, dt_row, ml, actual in shifts_all:
        reason = None
        if not shifts.is_complete(s, replay_start, replay_end):
            reason = "不完整（跨回放窗）"
        elif actual == 0:
            reason = "实际=0（停产/无下线）"
        if reason:
            excluded.append((s, reason, dt_row, ml))
        else:
            eligible.append((s, actual))
    print(f"班次总数 {len(shifts_all)}；进门判 {len(eligible)}；排除 {len(excluded)}")

    # --- 逐班逐锚点预测（直接 + Σ中位 + Σ均值；直接再带 ⑥b A/B；D1' 5h 逐车型） ---
    print("逐班逐锚点预测 …")
    recs = []
    for s, actual in eligible:
        attr_ep = s.attr_start.timestamp()
        end_ep = s.end.timestamp()
        # D1'：逐车型全窗实际量 + 返工车组全窗实际（[attr_start, 窗末] 下线）
        act_bm = {}
        for j in np.where((exit_arr >= attr_ep) & (exit_arr <= end_ep))[0]:
            act_bm[bt_arr[j]] = act_bm.get(bt_arr[j], 0) + 1
        rework_actual = int(((exit_arr >= attr_ep) & (exit_arr <= end_ep)
                             & rework_arr).sum())
        for h, t_dt in zip(shifts.ANCHOR_HOURS, shifts.anchors(s)):
            t = int(t_dt.timestamp())
            week = shifts.iso_week_label(t_dt.date())
            inproc = np.where((entry_arr <= t) & (exit_arr > t))[0]
            A = int(((exit_arr >= attr_ep) & (exit_arr <= t)).sum())
            row = dict(kind=s.kind, day=f"{s.day}", type=s.type, anchor_h=h,
                       actual=actual, A=A, inproc=len(inproc),
                       rework_actual=rework_actual, rework_pred=0,
                       m_gate={}, no_eta_bm={}, ip_bm={})
            no_eta = 0     # 无直接 ETA 的在制车（覆盖口径，报告用）
            no_sum = 0     # 无 Σ 估计的在制车（仅诊断覆盖）
            cnt = {est: 0 for est in ("direct", "direct_nocomp", "sum_median", "sum_mean")}
            mark_cnt = {}
            cohort_used = 0
            is_gate_h = (h == shifts.GATE_ANCHOR_H)
            A_bm = I_bm = None
            rework_pred = 0
            A_rework = 0   # M-2：锚点前已下线返工车（返工组预测与全厂同口径 = A + I）
            if is_gate_h:
                A_bm, I_bm = {}, {}
                for j in np.where((exit_arr >= attr_ep) & (exit_arr <= t))[0]:
                    A_bm[bt_arr[j]] = A_bm.get(bt_arr[j], 0) + 1
                    if rework_arr[j]:
                        A_rework += 1
            for i in inproc:
                body = bodies[i]
                if is_gate_h:
                    row["ip_bm"][bt_arr[i]] = row["ip_bm"].get(bt_arr[i], 0) + 1
                evlist = evts.get(body)
                if not evlist:
                    no_eta += 1
                    if is_gate_h:
                        row["no_eta_bm"][bt_arr[i]] = row["no_eta_bm"].get(bt_arr[i], 0) + 1
                    continue
                idx = bisect.bisect_right(ts_lists[body], t) - 1
                if idx < 0 or idx >= len(evlist) - 1:
                    no_eta += 1
                    if is_gate_h:
                        row["no_eta_bm"][bt_arr[i]] = row["no_eta_bm"].get(bt_arr[i], 0) + 1
                    continue
                t_e, st_e, fl_e = evlist[idx]
                model = bt_arr[i]
                eta_d, eta_d0, used = _eta_pair(t_e, st_e, fl_e, model, week, tables, comp_excl)
                m = eta_v2.quality_mark(tables["quality"], model, st_e)
                mark_cnt[m] = mark_cnt.get(m, 0) + 1
                if used:
                    cohort_used += 1
                if eta_d is not None and t + (eta_d - t_e) <= end_ep:
                    cnt["direct"] += 1
                    if is_gate_h:
                        I_bm[model] = I_bm.get(model, 0) + 1
                        if rework_arr[i]:
                            rework_pred += 1
                if eta_d0 is not None and t + (eta_d0 - t_e) <= end_ep:
                    cnt["direct_nocomp"] += 1
                si = sum_idx.get(body)
                if si is not None and idx < len(si[1]):
                    if si[1][idx] is not None and t + (si[1][idx] - t_e) <= end_ep:
                        cnt["sum_median"] += 1
                    if si[2][idx] is not None and t + (si[2][idx] - t_e) <= end_ep:
                        cnt["sum_mean"] += 1
                else:
                    no_sum += 1
            if is_gate_h:
                row["m_gate"] = {m: (A_bm.get(m, 0) + I_bm.get(m, 0), act_bm.get(m, 0))
                                 for m in set(A_bm) | set(I_bm) | set(act_bm)}
                row["rework_pred"] = rework_pred + A_rework
            row.update(cnt=cnt, no_eta=no_eta, no_sum=no_sum, mark_cnt=mark_cnt,
                       cohort_used=cohort_used)
            recs.append(row)
    d = pd.DataFrame([{k: v for k, v in r.items()
                       if k not in ("cnt", "mark_cnt", "m_gate", "no_eta_bm", "ip_bm")} |
                      {f"{e}_pred": r["A"] + r["cnt"][e] for e in
                       ("direct", "direct_nocomp", "sum_median", "sum_mean")}
                      | {"direct_err_pct": (r["A"] + r["cnt"]["direct"] - r["actual"]) / r["actual"],
                         "direct_nocomp_err_pct": (r["A"] + r["cnt"]["direct_nocomp"] - r["actual"]) / r["actual"],
                         "summed_median_err_pct": (r["A"] + r["cnt"]["sum_median"] - r["actual"]) / r["actual"],
                         "summed_mean_err_pct": (r["A"] + r["cnt"]["sum_mean"] - r["actual"]) / r["actual"]}
                     for r in recs])

    # --- ⑤ 质量标记分布（5h 锚点在制车） ---
    mark_agg = {}
    for r in recs:
        if r["anchor_h"] == shifts.GATE_ANCHOR_H:
            for m, c in r["mark_cnt"].items():
                mark_agg[m] = mark_agg.get(m, 0) + c

    # --- 门判（D1'：全厂 ∧ 主车型；直接估计器 @5h，逐班 |err%| 中位数 ≤ 10%） ---
    g = d[d["anchor_h"] == shifts.GATE_ANCHOR_H]
    gate_val = float(g["direct_err_pct"].abs().median())
    g_recs = [r for r in recs if r["anchor_h"] == shifts.GATE_ANCHOR_H]
    all_models = set()
    for r in g_recs:
        all_models.update(r["m_gate"])
    model_actual_med = {}
    for m in all_models:   # D1'（C3 推广）：逐班实际量 = 0 的班次不参与该车型中位数
        _nz = [r["m_gate"][m][1] for r in g_recs if m in r["m_gate"] and r["m_gate"][m][1] > 0]
        model_actual_med[m] = float(np.median(_nz)) if _nz else 0.0
    model_gate = {}
    for m in all_models:
        errs = [abs((r["m_gate"][m][0] - r["m_gate"][m][1]) / r["m_gate"][m][1])
                for r in g_recs if m in r["m_gate"] and r["m_gate"][m][1] > 0]
        model_gate[m] = float(np.median(errs)) if errs else float("nan")
    main_models = sorted(m for m, med in model_actual_med.items()
                         if med >= MAIN_MODEL_MIN_MEDIAN)
    models_pass = all(model_gate[m] <= GATE_PCT for m in main_models)   # NaN 比较 = False（保守）
    passed = (gate_val <= GATE_PCT) and models_pass

    def _pct_stats(col):
        s = pd.Series(col).dropna()
        return {"n": int(len(s)),
                "median_abs_pct": round(float(s.abs().median()) * 100, 2),
                "mean_abs_pct": round(float(s.abs().mean()) * 100, 2),
                "p90_abs_pct": round(float(s.abs().quantile(0.9)) * 100, 2),
                "signed_median_pct": round(float(s.median()) * 100, 2)}

    anchors = {h: _pct_stats(d[d["anchor_h"] == h]["direct_err_pct"])
               for h in shifts.ANCHOR_HOURS}
    estimators = {e: _pct_stats(g[f"{e}_err_pct"]) for e in
                  ("direct", "direct_nocomp", "summed_median", "summed_mean")}
    by_kind = {k: _pct_stats(v["direct_err_pct"]) for k, v in g.groupby("kind")}
    by_week = {w: _pct_stats(v["direct_err_pct"])
               for w, v in g.assign(week=g["day"].map(
                   lambda x: shifts.iso_week_label(datetime.fromisoformat(x).date())))
                   .groupby("week")}

    # --- 11IS A/B 检查（报告） ---
    cur.execute(SQL_11IS)
    s11 = pd.DataFrame(cur.fetchall(),
                       columns=["bt", "station", "p50_a", "p50_b", "n_a", "n_b"])
    s11_rel = None
    if len(s11):
        s11["rel_diff"] = ((s11["p50_a"] - s11["p50_b"]).abs()
                           / s11[["p50_a", "p50_b"]].min(axis=1))
        s11_rel = float(s11["rel_diff"].median())

    # --- 产物 ---
    stamp = datetime.now(CST).strftime("%Y%m%d")
    outdir = Path("output")
    outdir.mkdir(exist_ok=True)
    d.to_csv(outdir / f"w2_replay_shifts_{stamp}.csv", index=False)
    m_rows = [{"day": r["day"], "kind": r["kind"], "type": r["type"], "model": m,
               "main_model": m in main_models, "pred_m": pm, "actual_m": am,
               "err_pct": (pm - am) / am}
              for r in g_recs for m, (pm, am) in r["m_gate"].items() if am > 0]
    dm = pd.DataFrame(m_rows)
    dm.to_csv(outdir / f"w2_replay_models_{stamp}.csv", index=False)

    gate_json = {
        "replay_window": f"{replay_start:%Y-%m-%d} ~ {replay_end:%Y-%m-%d}",
        "gate_metric": "D1'：直接剩余分布估计器（v2：cohort 混合 + 周度 p + ⑥b 豁免），"
                       "第 5 小时锚；门 = 全厂逐班 |预测-实际|/实际 中位数 ≤10% 且 所有主车型"
                       "（逐班实际量中位数 ≥ 30）各自逐班 |err%| 中位数 ≤10%",
        "gate_threshold_pct": 10.0,
        "gate_value_pct": round(gate_val * 100, 2),
        "model_gates_pct": {str(m): (round(v * 100, 2) if not math.isnan(v) else None)
                            for m, v in model_gate.items()},
        "main_models": [str(m) for m in main_models],
        "model_actual_median": {str(m): model_actual_med[m] for m in main_models},
        "models_pass": bool(models_pass),
        "pass": bool(passed),
        "n_shifts_in_gate": int(len(g)),
        "excluded_shifts": [
            {"kind": s.kind, "day": f"{s.day}", "type": s.type, "reason": reason}
            for s, reason, _dt, _ml in excluded],
        "anchors_direct": anchors,
        "estimators_at_5h": estimators,
        "by_kind_at_5h": by_kind,
        "by_week_at_5h": by_week,
        "is11_pair_rel_diff_median": s11_rel,
    }
    (outdir / f"w2_replay_gate_{stamp}.json").write_text(
        json.dumps(gate_json, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = ["# W2 班次级回放验证报告（第 5 小时 ±10% 门判）", "",
             f"- 回放窗：{replay_start:%Y-%m-%d} → {replay_end:%Y-%m-%d}（ev_base 前置 7d 边）",
             f"- 基线：W1 学习表（04-14→08-15）+ v2 周度 p/线概率/cohort/质量标记（Task 3）",
             f"- 生成时间：{datetime.now(CST):%Y-%m-%d %H:%M}", "",
             "## 门判（D1'：全厂 + 主车型分门；直接估计器 @5h，逐班 |err%| 中位数 ≤ 10%）", "",
             f"- **全厂 {gate_val * 100:.2f}% → {'✅' if gate_val <= GATE_PCT else '❌'}**"
             f"（进门判班次 n={len(g)}；符号中位 {g['direct_err_pct'].median() * 100:+.2f}%，"
             f"均值 {g['direct_err_pct'].abs().mean() * 100:.2f}%，"
             f"P90 {g['direct_err_pct'].abs().quantile(0.9) * 100:.2f}%）",
             f"- **总门：{'✅ 通过' if passed else '❌ 未通过'}**（全厂 ∧ 全部主车型；"
             f"主车型 = 逐班实际量中位数 ≥ {MAIN_MODEL_MIN_MEDIAN}）", ""]
    lines += ["", f"## 车型分门明细（D1'；主车型带（主）标记；逐班×车型全量见 w2_replay_models_*.csv）", "",
              "| 车型 | 逐班实际量中位 | 中位\\|err%\\| | ≤10% |",
              "|---|---|---|---|"]
    lines += [f"| {m}{'（主）' if m in main_models else ''} | {model_actual_med[m]:.1f} | "
              f"{model_gate[m] * 100:.2f}% | {'✅' if model_gate[m] <= GATE_PCT else '❌'} |"
              if not math.isnan(model_gate[m]) else
              f"| {m}{'（主）' if m in main_models else ''} | {model_actual_med[m]:.1f} | — | — |"
              for m in sorted(all_models, key=lambda x: -model_actual_med[x])]
    rework_pred_tot = int(g["rework_pred"].sum())
    rework_act_tot = int(g["rework_actual"].sum())
    lines += ["", "## 返工车组 @5h（docs/01：返工车单独修正并成组显示；修正 = 环分布 + 前决策补偿）", "",
              f"- 回放全期合计：预测 {rework_pred_tot}（已下线 A + 在制 I，与全厂同口径），实际 {rework_act_tot}"
              + (f"，组级误差 {(rework_pred_tot - rework_act_tot) / rework_act_tot:+.2%}"
                 if rework_act_tot else "（实际 = 0）"),
              f"- 占全厂：预测 {rework_pred_tot / max(int(g['direct_pred'].sum()), 1):.2%}"
              f" / 实际 {rework_act_tot / max(int(g['actual'].sum()), 1):.2%}"]
    ip_bm_tot, no_eta_bm_tot = {}, {}
    for r in g_recs:
        for m, c in r["ip_bm"].items():
            ip_bm_tot[m] = ip_bm_tot.get(m, 0) + c
        for m, c in r["no_eta_bm"].items():
            no_eta_bm_tot[m] = no_eta_bm_tot.get(m, 0) + c
    lines += ["", "## 车型 @5h 分桶（③ 稀有长行程车型可见化；无直接 ETA = 覆盖缺口）", ""]
    lines += [f"- {m}：5h 在制 {ip_bm_tot[m]} 车，无直接 ETA {no_eta_bm_tot.get(m, 0)}"
              f"（{no_eta_bm_tot.get(m, 0) / max(ip_bm_tot[m], 1):.1%}）"
              for m in sorted(ip_bm_tot, key=lambda x: -ip_bm_tot[x])]
    es_cnt = Counter(spans["entry_st"].dropna())
    lines += ["", "## 入口站分桶（④ 01IS045 小样本站可见化）", ""]
    lines += [f"- {st}: {n} 车" + ("（n<30，W1 S1 已『只报告不判定』；本波次同样只报告）"
             if st == "01IS045" else "") for st, n in es_cnt.most_common()]
    lines += ["", "## 多锚点曲线（直接估计器，D2：提前量 × 精度）", "",
              "| 锚点 | n | 中位\\|err%\\| | 均值\\|err%\\| | P90\\|err%\\| | 符号中位 |",
              "|---|---|---|---|---|---|"]
    lines += [f"| {h}h | {anchors[h]['n']} | {anchors[h]['median_abs_pct']:.2f}%"
              f" | {anchors[h]['mean_abs_pct']:.2f}% | {anchors[h]['p90_abs_pct']:.2f}%"
              f" | {anchors[h]['signed_median_pct']:+.2f}% |"
              for h in shifts.ANCHOR_HOURS]
    lines += ["", "## 三估计器对打 @5h（D2：直接 = 生产口径；Σ 系后见路径，仅诊断）", "",
              "| 估计器 | 中位\\|err%\\| | 均值\\|err%\\| | P90 | 符号中位 |",
              "|---|---|---|---|---|"]
    lines += [f"| {e} | {estimators[e]['median_abs_pct']:.2f}% | {estimators[e]['mean_abs_pct']:.2f}%"
              f" | {estimators[e]['p90_abs_pct']:.2f}% | {estimators[e]['signed_median_pct']:+.2f}% |"
              for e in ("direct", "direct_nocomp", "summed_median", "summed_mean")]
    lines += ["", f"（direct_nocomp = W4.3d 门控重定义：DUAL_RING=1 → 净延误关闭基线"
              f"（expected_delay 强制 0，度量该表净效果）；DUAL_RING=0 → ⑥b 豁免关闭对照）", "",
              "## 分桶 @5h（直接估计器）", "",
              f"- 按班次种：{ {k: v['median_abs_pct'] for k, v in by_kind.items()} }%",
              f"- 按 ISO 周：{ {k: v['median_abs_pct'] for k, v in by_week.items()} }%", "",
              "## 排除班次（C2/C3）", ""]
    lines += [f"- {s.kind} {s.day}（{s.type}）：{reason}" for s, reason, _dt, _ml in excluded]
    lines += ["", "## 班次类型自学审计（人工抽查；阈值：15min 桶 ≥30 / 中班末事件分钟数）", "",
              "| 日 | 日型判定（05:00/06:00/08:00 桶） | 全天事件 | 日班型 | 中班型（中班末事件 min） |",
              "|---|---|---|---|---|"]
    for ad_day, dt_row, dt, mt, ml in day_audit:
        lines += [f"| {ad_day} | {dt_row['05:00']} / {dt_row['06:00']} / {dt_row['08:00']} | {dt_row['total']} | "
                  f"{dt or '—(无日班)'} | {mt or '—(无中班)'}（{ml if ml is not None else '—'}min） |"]
    lines += ["", f"## 11IS A/B 并行线 rel_diff（D4：仅报告；中位 = "
              f"{None if s11_rel is None else round(s11_rel, 3)}，n={len(s11)} 行）", ""]
    if len(s11):
        lines += [f"- {r.bt} {r.station}: P50_a={r.p50_a:.0f}s P50_b={r.p50_b:.0f}s "
                  f"rel={r.rel_diff:.3f} (n {int(r.n_a)}/{int(r.n_b)})"
                  for r in s11.sort_values("rel_diff", ascending=False).head(10).itertuples()]
    lines += ["", "## 覆盖与机制用量（5h 锚点）", "",
              f"- 在制车无直接 ETA 占比："
              f"{d[d['anchor_h'] == 5]['no_eta'].sum() / max(d[d['anchor_h'] == 5]['inproc'].sum(), 1):.2%}",
              f"- cohort 混合命中："
              f"{d[d['anchor_h'] == 5]['cohort_used'].sum() / max(d[d['anchor_h'] == 5]['inproc'].sum(), 1):.2%}",
              f"- Σ 估计缺车数（仅诊断覆盖）：{d[d['anchor_h'] == 5]['no_sum'].sum()}",
              f"- ⑤ 质量标记分布（在制车）：{mark_agg or '—'}", "",
              "## 结论", "",
              f"- 门判：{'✅ 通过' if passed else '❌ 未通过'}。"
              + ("" if passed else
                 "按 W1 先例：门 FAIL 是合法实测结果——用分桶定位误差源（锚点/周/班次种/标记），"
                 "未诊断清楚不拍板修订；诊断结论追加本文件并同步 BM。"),
              f"- 多锚点曲线回答「能否比第 5 小时更早喊话」（3h/4h 误差若也在 ±10% 内 → 生产可提前）。"]
    report = outdir / f"w2_replay_report_{stamp}.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    conn.close()

    print(f"\n门判（5h 直接）：{gate_val * 100:.2f}% {'✅ ≤10% 通过' if passed else '❌ >10% 未通过'}")
    print(f"报告 → {report}")
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
