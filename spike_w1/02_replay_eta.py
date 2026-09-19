# -*- coding: utf-8 -*-
"""W1 回放：三估计器对打 + 门判（因果切分：学习窗之外的后窗）。

用法：python spike_w1\\02_replay_eta.py [--learn-end 2026-08-15] [--win-end 2026-09-18]
门判：直接剩余分布估计器，逐车中位 |err| 再跨车取中位 ≤ 1800s（30min）。
exit 0 = 过门；exit 2 = 未过（报告含按车型/站/剩余时长分桶诊断，供误差源定位）。
"""
import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

try:
    from . import common, eta, sqlbase
except ImportError:
    import common, eta, sqlbase

CST = ZoneInfo("Asia/Shanghai")
GATE_SEC = 1800  # 30min 验收门

SQL_0809 = """
-- cohort 必须基于 tmp_ev_base（截断 exit）构建：原始流的 09IS350/360 是出口后记录站，
-- 会污染 `LIKE '09IS%'`（几乎全部主线车出口后都过），见「干跑校准结论」第 2 条。
WITH cohort AS (
    SELECT body,
           bool_or(station LIKE '08IS%') AS c08,      -- 无参数执行：% 不转义
           bool_or(station LIKE '09IS%') AS c09
    FROM tmp_ev_base GROUP BY body
),
s AS (
    SELECT e.bt, e.station,
           EXTRACT(EPOCH FROM (e.exit_t - e.t))::BIGINT AS rem,
           c.c08, c.c09
    FROM tmp_ev_base e JOIN cohort c USING (body)
    WHERE e.t < e.exit_t
      AND e.station NOT LIKE '08IS%' AND e.station NOT LIKE '09IS%'
      AND c.c08 <> c.c09          -- 单线车（0.0% 站对交集，几乎无混合）
)
SELECT bt, station,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY rem) FILTER (WHERE c08) AS p50_08,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY rem) FILTER (WHERE c09) AS p50_09,
       count(*) FILTER (WHERE c08) AS n08,
       count(*) FILTER (WHERE c09) AS n09
FROM s
GROUP BY bt, station
HAVING count(*) FILTER (WHERE c08) >= 100
   AND count(*) FILTER (WHERE c09) >= 100
ORDER BY bt, station;
"""


def load_tables(conn) -> dict:
    """dss 学习表 → eta.py 行形状 dict（车型在 key 末位，n 在行尾）。"""
    cur = conn.cursor()

    def rows(sql):
        cur.execute(sql)
        return cur.fetchall()

    return {
        "rem": {(r[1], r[0]): (r[2], r[3], r[4], r[5])
                for r in rows("SELECT body_type, station, p50, mean, p90, n "
                              "FROM dss.dim_remaining_time")},
        "loop_rem": {(r[2], r[1], r[0]): (r[3], r[4], r[5], r[6])
                     for r in rows("SELECT body_type, flag, station, p50, mean, p90, n "
                                   "FROM dss.dim_loop_remaining")},
        "fut": {(r[1], r[0]): (r[2], r[3], r[4])
                for r in rows("SELECT body_type, station, p1, p2, n "
                              "FROM dss.dim_future_flag")},
        "loop_dur": {(r[1], r[0]): (r[2], r[3])
                     for r in rows("SELECT body_type, flag, l, n "
                                   "FROM dss.dim_loop_duration")},
        "takt": {(r[0], r[1], r[2]): (r[3], r[4], r[5], r[6])
                 for r in rows("SELECT frm, to_station, body_type, p50, mean, p90, n "
                               "FROM dss.dim_station_takt")},
    }


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows GBK 控制台：防 emoji/特殊字符 print 崩溃
    ap = argparse.ArgumentParser()
    ap.add_argument("--learn-end", default="2026-08-15")
    ap.add_argument("--win-end", default="2026-09-18")
    a = ap.parse_args()
    replay_start = (datetime.fromisoformat(a.learn_end)
                    .replace(hour=0, minute=0, second=0, tzinfo=CST) + timedelta(days=1))
    replay_end = datetime.fromisoformat(a.win_end).replace(hour=23, minute=59, second=59, tzinfo=CST)

    conn = common.connect()
    cur = conn.cursor()
    print(f"回放窗 {replay_start:%Y-%m-%d} → {replay_end:%Y-%m-%d}，ev_base …")
    cur.execute(sqlbase.ev_base_sql(replay_start, replay_end), (replay_start, replay_end))
    cur.execute("SELECT count(*) FROM tmp_ev_base")
    print("  回放窗事件数:", cur.fetchone()[0])

    print("拉取事件流 …")
    cur.execute("""SELECT body, bt, station, EXTRACT(EPOCH FROM t)::BIGINT, flag
                   FROM tmp_ev_base
                   WHERE flag IN ('0', '1', '2')     -- '-'/'3' 共 0.08%，不进估计样本。
                   -- 边角：某车出口事件恰为 -/3（全库量级，预计十几~几十台）时被本条件滤掉 →
                   -- 该车最后一个有效事件被 estimate_vehicle 误当「下线事件」跳过：
                   -- 该车丢 1 个事件样本（仍在样本内，对中位门无感）；其两个 Σ 估计器另丢末段、
                   -- 低估略加重（诊断用途，不影响门判的 direct 估计器）
                   ORDER BY body, t, id""")
    df = pd.DataFrame(cur.fetchall(), columns=["body", "bt", "station", "t", "flag"])
    cur.execute("""SELECT body, EXTRACT(EPOCH FROM min(exit_t))::BIGINT
                   FROM tmp_ev_base GROUP BY body""")
    veh = pd.DataFrame(cur.fetchall(), columns=["body", "exit_t"]).set_index("body")
    tables = load_tables(conn)
    print("学习表行数:", {k: len(v) for k, v in tables.items()})

    print("逐车估计 …")
    recs = []
    for body, g in df.groupby("body", sort=False):
        evs = [eta.Event(int(t), st, fl)
               for t, st, fl in g[["t", "station", "flag"]].itertuples(index=False)]
        model = g["bt"].iloc[0]
        exit_t = int(veh.loc[body, "exit_t"])
        for r in eta.estimate_vehicle(evs, exit_t, tables, model):
            recs.append((body, model, exit_t, r.t, r.station, r.flag,
                         r.remaining_actual, r.eta_direct,
                         r.eta_sum_median, r.eta_sum_mean))
    d = pd.DataFrame(recs, columns=["body", "bt", "exit_t", "t", "station", "flag",
                                    "remaining_actual", "eta_direct",
                                    "eta_sum_median", "eta_sum_mean"])
    for c in ("eta_direct", "eta_sum_median", "eta_sum_mean"):
        d[c + "_err"] = (d[c] - d["exit_t"]).abs()

    stamp = datetime.now(CST).strftime("%Y%m%d")
    outdir = Path("output")
    outdir.mkdir(exist_ok=True)

    # --- 门判：直接估计器，逐车中位 → 跨车中位 ---
    g = d.dropna(subset=["eta_direct_err"])
    per_veh = g.groupby("body")["eta_direct_err"].median()
    gate_val = float(per_veh.median())
    passed = gate_val <= GATE_SEC

    def _stat(s):
        s = s.dropna()
        return {"n": int(len(s)), "median": float(s.median()), "p90": float(s.quantile(0.9))}

    est = {c: _stat(d[c + "_err"]) for c in
           ("eta_direct", "eta_sum_median", "eta_sum_mean")}
    coverage = {c: float(d[c].notna().mean()) for c in
                ("eta_direct", "eta_sum_median", "eta_sum_mean")}

    # --- 诊断分桶（失败判据用） ---
    by_flag = (g.groupby("flag")["eta_direct_err"].median().to_dict())
    by_model = (g.assign(n=g.groupby("body")["body"].transform("size"))
                .groupby("bt")["eta_direct_err"]
                .agg(median="median", n="count")
                .sort_values("n", ascending=False))
    by_station = (d.dropna(subset=["eta_direct_err"])
                  .groupby("station")["eta_direct_err"]
                  .agg(median="median", n="count")
                  .sort_values("median", ascending=False))
    d["rem_bucket"] = pd.cut(d["remaining_actual"],
                             bins=[0, 7200, 28800, 10**9],
                             labels=["<2h", "2-8h", ">8h"])
    by_rem = d.dropna(subset=["eta_direct_err"]).groupby("rem_bucket", observed=True)["eta_direct_err"].median().to_dict()

    # --- 08/09 等价线检查（五确假设回放实证；按车型口径，干跑实测 2N54Y- rel_diff ≈ 0.20–0.30） ---
    cur.execute(SQL_0809)
    s89 = pd.DataFrame(cur.fetchall(),
                       columns=["bt", "station", "p50_08", "p50_09", "n08", "n09"])
    if len(s89):
        s89["rel_diff"] = ((s89["p50_08"] - s89["p50_09"]).abs()
                           / s89[["p50_08", "p50_09"]].min(axis=1))
        s89_main = s89[s89["bt"] == "2N54Y-"]
        s89_median_rel = (float(s89_main["rel_diff"].median()) if len(s89_main)
                          else float(s89["rel_diff"].median()))
        s89_top = s89.sort_values("rel_diff", ascending=False).head(10)
    else:
        s89_median_rel, s89_top, s89_main = None, s89, s89

    # --- cohort 误差分桶（2N54Y- 主线：08IS 线 vs 09IS 线——量化前分支共享站的混合偏差） ---
    cur.execute("""SELECT body,
                          bool_or(station LIKE '08IS%') AS c08,
                          bool_or(station LIKE '09IS%') AS c09
                   FROM tmp_ev_base GROUP BY body""")
    coh = pd.DataFrame(cur.fetchall(), columns=["body", "c08", "c09"])
    d = d.merge(coh, on="body", how="left")
    d["cohort"] = np.where(d["c08"] == d["c09"], "other",
                           np.where(d["c08"], "08IS", "09IS"))
    by_cohort = (d[d["bt"] == "2N54Y-"].dropna(subset=["eta_direct_err"])
                 .groupby("cohort")["eta_direct_err"]
                 .agg(median="median", n="count"))

    # --- 报告 ---
    lines = [
        "# W1 回放验证报告（三估计器对打）", "",
        f"- 回放窗：{replay_start:%Y-%m-%d} → {replay_end:%Y-%m-%d}（学习窗截止 {a.learn_end}，因果切分）",
        f"- 样本：{d['body'].nunique()} 车 / {len(d)} 事件（量产车、entry/exit 均在窗内）",
        f"- 生成时间：{datetime.now(CST):%Y-%m-%d %H:%M}", "",
        "## 门判（直接剩余分布估计器）", "",
        f"- **逐车中位 |err| 再跨车中位 = {gate_val:.0f}s（{gate_val/60:.1f}min）→ "
        f"{'✅ 通过（≤30min）' if passed else '❌ 未通过（>30min）'}**",
        f"- 覆盖（有 ETA 事件占比）：direct={coverage['eta_direct']:.3f} "
        f"sum_med={coverage['eta_sum_median']:.3f} sum_mean={coverage['eta_sum_mean']:.3f}", "",
        "## 三估计器对比（全部事件）", "",
        "| 估计器 | n | 中位误差 (s) | P90误差 (s) | 预期形态 |",
        "|---|---|---|---|---|",
        f"| Σ段中位数 | {est['eta_sum_median']['n']} | {est['eta_sum_median']['median']:.0f} "
        f"| {est['eta_sum_median']['p90']:.0f} | 预期大幅低估（中位数不可加，~5×） |",
        f"| Σ段均值 | {est['eta_sum_mean']['n']} | {est['eta_sum_mean']['median']:.0f} "
        f"| {est['eta_sum_mean']['p90']:.0f} | 无偏高方差 |",
        f"| 直接剩余分布 | {est['eta_direct']['n']} | {est['eta_direct']['median']:.0f} "
        f"| {est['eta_direct']['p90']:.0f} | **主候选，门判对象** |", "",
        "## 诊断分桶（direct 估计器中位误差，s）", "",
        f"- 按 flag：{by_flag}",
        f"- 按剩余时长：<2h={by_rem.get('<2h')} 2-8h={by_rem.get('2-8h')} >8h={by_rem.get('>8h')}",
        "",
        "按车型（top-8 量 + 误差最差的 5 个）：", "",
    ]
    lines += [f"- {k}: median={v['median']:.0f}s n={int(v['n'])}"
              for k, v in by_model.head(8).iterrows()]
    lines += [f"- {k}: median={v['median']:.0f}s n={int(v['n'])}（误差高）"
              for k, v in by_model.sort_values("median", ascending=False).head(5).iterrows()]
    lines += ["", "按站（误差 top-15，n≥50）：", ""]
    lines += [f"- {k}: median={v['median']:.0f}s n={int(v['n'])}"
              for k, v in by_station[by_station["n"] >= 50].head(15).iterrows()]
    if s89_median_rel is not None:
        lines += ["", "## 08IS/09IS 等价线检查（五确假设回放实证，按车型口径）", "",
                  f"- 共享站行数（每车型每线各 n≥100）：{len(s89)}；2N54Y- rel_diff 中位 = "
                  f"{s89_median_rel:.3f}",
                  "- **干跑预发现：2N54Y- rel_diff ≈ 0.20–0.30——五确「P50 近无偏」在 span 口径下不成立**"
                  "（该结论疑基于被 09IS360/350 出口后记录站污染的原始流 cohort 测出）",
                  "- 对门的影响：前分支共享站 ≈ 行程事件的前 24%，直接估计器对这些事件偏差 ~1.2–1.4h；"
                  "门为中位数（后段站 ≈76% 事件无偏），门大概率不受影响——以下 cohort 分桶量化实际影响", "",
                  "rel_diff top-10（按车型）：", ""]
        lines += [f"- {r.bt} {r.station}: P50_08={r.p50_08:.0f}s P50_09={r.p50_09:.0f}s "
                  f"rel={r.rel_diff:.3f} (n {int(r.n08)}/{int(r.n09)})"
                  for r in s89_top.itertuples()]
    lines += ["", "## Cohort 误差分桶（2N54Y- 主线，direct 估计器中位 |err|，s）", "",
              "（前分支共享站的混合偏差在此显影：08IS/09IS 两组中位误差若显著高于 other，"
              "即共享站 P50 混合偏差的实锤）", ""]
    lines += [f"- {k}: median={v['median']:.0f}s n={int(v['n'])}"
              for k, v in by_cohort.iterrows()]
    lines += ["", "## 结论", "",
              f"- 门判：{'通过' if passed else '未通过'}。"
              + ("" if passed else
                 "按失败判据：用上面分桶定位误差源（车型/站/时段），未诊断清楚不进 W2；"
                 "诊断结论追加到本文件并同步 BM 笔记。"),
              f"- 08/09 共享站 P50：2N54Y- rel_diff 中位 = "
              f"{None if s89_median_rel is None else round(s89_median_rel, 3)}"
              f"（>0.10 = 共享站存在线别剩余时长差；对门的影响见 cohort 误差分桶，"
              "W2 建议：共享站学习键加线别 cohort 维度）。"]
    report = outdir / f"w1_replay_report_{stamp}.md"
    report.write_text("\n".join(lines), encoding="utf-8")

    d.to_csv(outdir / f"w1_replay_errors_{stamp}.csv", index=False)
    by_station.to_csv(outdir / f"w1_replay_by_station_{stamp}.csv")
    gate_json = {
        "replay_window": f"{replay_start:%Y-%m-%d} ~ {replay_end:%Y-%m-%d}",
        "gate_metric": "直接剩余分布估计器，逐车中位|err|再跨车中位（秒）",
        "gate_threshold_sec": GATE_SEC,
        "gate_value_sec": gate_val,
        "pass": bool(passed),
        "estimators_sec": est,
        "coverage": coverage,
        "shared_station_rel_diff_median_0809": s89_median_rel,
        "cohort_median_err_sec": {k: float(v["median"]) for k, v in by_cohort.iterrows()},
    }
    (outdir / f"w1_replay_gate_{stamp}.json").write_text(
        json.dumps(gate_json, ensure_ascii=False, indent=2), encoding="utf-8")
    conn.close()

    print(f"\n门判：{gate_val/60:.1f}min {'✅ ≤30min 通过' if passed else '❌ >30min 未通过'}")
    print(f"报告 → {report}")
    return 0 if passed else 2


if __name__ == "__main__":
    sys.exit(main())
