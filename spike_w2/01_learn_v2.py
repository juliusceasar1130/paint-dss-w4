# -*- coding: utf-8 -*-
"""W2 v2 学习器：周度 p（滚动 8 周）+ 线别 cohort 剩余 + 周度线概率 + 站级质量标记/补偿豁免。

用法：python spike_w2\\01_learn_v2.py [--learn-start 2026-04-14] [--learn-end 2026-08-15]
                              [--replay-start 2026-08-16] [--replay-end 2026-09-19]
只写 dss.v2_* 六张表 + dss.dim_expected_delay（W4.2c 新建，doc10 §2 双列净延误）；
W1 六张 dss.dim_*（含 meta）只读不写（回归护栏）。
sanity W2-S1–S4 全 PASS 才 exit 0（否则 exit 1），报告 output/w2_learn_sanity_<YYYYMMDD>.md。
"""
import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spike_w2 import common, shifts
from spike_w1 import sqlbase

CST = ZoneInfo("Asia/Shanghai")

DDL = """
CREATE SCHEMA IF NOT EXISTS dss;
DROP TABLE IF EXISTS dss.v2_future_flag;
DROP TABLE IF EXISTS dss.v2_cohort_remaining;
DROP TABLE IF EXISTS dss.v2_line_prob;
DROP TABLE IF EXISTS dss.v2_station_quality;
DROP TABLE IF EXISTS dss.v2_comp_excl;
DROP TABLE IF EXISTS dss.v2_learner_meta;

CREATE TABLE dss.v2_future_flag (
    body_type TEXT NOT NULL, station TEXT NOT NULL, week TEXT NOT NULL,
    p1 DOUBLE PRECISION, p2 DOUBLE PRECISION, n INT,
    PRIMARY KEY (body_type, station, week));
CREATE TABLE dss.v2_cohort_remaining (
    body_type TEXT NOT NULL, station TEXT NOT NULL, cohort TEXT NOT NULL,
    p50 BIGINT, mean BIGINT, p90 BIGINT, n INT,
    PRIMARY KEY (body_type, station, cohort));
CREATE TABLE dss.v2_line_prob (
    body_type TEXT NOT NULL, week TEXT NOT NULL, w08 DOUBLE PRECISION, n INT,
    PRIMARY KEY (body_type, week));
CREATE TABLE dss.v2_station_quality (
    body_type TEXT NOT NULL, station TEXT NOT NULL,
    n_model INT, p1 DOUBLE PRECISION, mark TEXT NOT NULL,
    PRIMARY KEY (body_type, station));
CREATE TABLE dss.v2_comp_excl (
    body_type TEXT NOT NULL, station TEXT NOT NULL,
    PRIMARY KEY (body_type, station));
CREATE TABLE dss.v2_learner_meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL);
DROP TABLE IF EXISTS dss.dim_expected_delay;
CREATE TABLE dss.dim_expected_delay (
    body_type TEXT NOT NULL, station TEXT NOT NULL,
    delay_mean_s BIGINT, delay_p50_s BIGINT,
    sample_n INT, sample_n_clean INT,
    PRIMARY KEY (body_type, station));
"""

# ⑥a 净化：p 只从前环人群学（flag=0 且早于该车首个 flag 1 与首个 flag 2 事件）
# ——排除"环后回主线"车（flag 复位 0）与多环车的环间事件，使 p = P(未来进环 | 当前前环 flag=0)
SQL_FUTURE_FLAG = """
INSERT INTO dss.v2_future_flag
SELECT e.bt, e.station, %s,
       AVG(CASE WHEN f.f1 IS NOT NULL AND f.f1 > e.t THEN 1.0 ELSE 0.0 END),
       AVG(CASE WHEN f.f2 IS NOT NULL AND f.f2 > e.t THEN 1.0 ELSE 0.0 END),
       count(*)::INT
FROM tmp_ev_base e JOIN (
    SELECT body,
           min(t) FILTER (WHERE flag = '1') AS f1,
           min(t) FILTER (WHERE flag = '2') AS f2
    FROM tmp_ev_base GROUP BY body
) f ON f.body = e.body
WHERE e.flag = '0' AND e.t < e.exit_t
  AND (f.f1 IS NULL OR e.t < f.f1)
  AND (f.f2 IS NULL OR e.t < f.f2)
GROUP BY e.bt, e.station
UNION ALL
SELECT 'ALL', e.station, %s,
       AVG(CASE WHEN f.f1 IS NOT NULL AND f.f1 > e.t THEN 1.0 ELSE 0.0 END),
       AVG(CASE WHEN f.f2 IS NOT NULL AND f.f2 > e.t THEN 1.0 ELSE 0.0 END),
       count(*)::INT
FROM tmp_ev_base e JOIN (
    SELECT body,
           min(t) FILTER (WHERE flag = '1') AS f1,
           min(t) FILTER (WHERE flag = '2') AS f2
    FROM tmp_ev_base GROUP BY body
) f ON f.body = e.body
WHERE e.flag = '0' AND e.t < e.exit_t
  AND (f.f1 IS NULL OR e.t < f.f1)
  AND (f.f2 IS NULL OR e.t < f.f2)
GROUP BY e.station;
"""

SQL_LINE_PROB = """
INSERT INTO dss.v2_line_prob (body_type, week, w08, n)
WITH cohort AS (
    SELECT body, bt,
           bool_or(station LIKE '08IS%%') AS c08,
           bool_or(station LIKE '09IS%%') AS c09
    FROM tmp_ev_base GROUP BY body, bt
)
SELECT bt, %s,
       AVG(CASE WHEN c08 THEN 1.0 ELSE 0.0 END),
       count(*)::INT
FROM cohort WHERE c08 <> c09
GROUP BY bt
UNION ALL
SELECT 'ALL', %s,
       AVG(CASE WHEN c08 THEN 1.0 ELSE 0.0 END),
       count(*)::INT
FROM cohort WHERE c08 <> c09;
"""

# ① 线别 cohort 剩余分布：共享站（非 08IS/09IS 前缀）× 后来走线（c08 XOR c09 单线车，
# cohort 必须基于 tmp_ev_base——09IS360/350 出口后记录站会污染原始流 cohort）
SQL_COHORT_REM = """
WITH cohort AS (
    SELECT body,
           bool_or(station LIKE '08IS%%') AS c08,
           bool_or(station LIKE '09IS%%') AS c09
    FROM tmp_ev_base GROUP BY body
)
INSERT INTO dss.v2_cohort_remaining
SELECT bt, station,
       CASE WHEN c08 THEN '08' ELSE '09' END,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY rem))::BIGINT,
       round(avg(rem))::BIGINT,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY rem))::BIGINT,
       count(*)::INT
FROM (
    SELECT e.bt, e.station, c.c08,
           EXTRACT(EPOCH FROM (e.exit_t - e.t))::BIGINT AS rem
    FROM tmp_ev_base e JOIN cohort c USING (body)
    WHERE c.c08 <> c.c09
      AND e.station NOT LIKE '08IS%%' AND e.station NOT LIKE '09IS%%'
      AND e.flag = '0' AND e.t < e.exit_t
      AND (e.last_loop_t IS NULL OR e.t > e.last_loop_t)
) s GROUP BY bt, station, c08
UNION ALL
SELECT 'ALL', station,
       CASE WHEN c08 THEN '08' ELSE '09' END,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY rem))::BIGINT,
       round(avg(rem))::BIGINT,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY rem))::BIGINT,
       count(*)::INT
FROM (
    SELECT station, c.c08,
           EXTRACT(EPOCH FROM (e.exit_t - e.t))::BIGINT AS rem
    FROM tmp_ev_base e JOIN cohort c USING (body)
    WHERE c.c08 <> c.c09
      AND e.station NOT LIKE '08IS%%' AND e.station NOT LIKE '09IS%%'
      AND e.flag = '0' AND e.t < e.exit_t
      AND (e.last_loop_t IS NULL OR e.t > e.last_loop_t)
) s GROUP BY station, c08;
"""


# W4.2c：站级双列期望净延误表（doc10 第一迭代 ②，口径源 doc10 §2 v2.2.1）
#   delay_mean_s = max(0, P(fut)×mean(rem−base | fut=1) + (mean(rem_clean)−p50(rem_clean)))
#   delay_p50_s  = max(0, P(fut)×p50(rem−base | fut=1))
# - 事件人群 = ⑥a 全窗前环（flag=0 ∧ t<exit_t ∧ t<f1 ∧ t<f2，与 v2_future_flag ⑥a 同源同窗；
#   「diff 与 P(fut) 同窗」——P 从前环人群学，diff 也必须从同人群量，否则 fut=1 子群为空）。
#   注意：不能用干净后缀人群（t>last_loop_t）——该人群与 fut=1（t<f1）结构性不相交（实测 fut=1 恒 0）。
#   fut=1 子群 = 将来重返修（首个 f1 在本事件之后）；rem_clean 子群 = 不再返修（f1 IS NULL，永洁组）。
# - base = 同源基线（值）：逐车取引擎层级 车型→前缀→ALL 在 dss.dim_remaining_time 中
#   n≥30 的首行 p50（W1 rem 表人群是干净后缀，但引擎在任何主线事件查的都是这张表——
#   doc10「diff 与基线同源一致性」指基线值同源，非人群同源）。
#   前置条件：W1 学习器已先重学（同学习窗）。
# - P(fut) = dss.v2_future_flag ⑥a 全窗 p1（week='ALL'；车型行缺失→站 ALL 行；
#   前缀/ALL 层→站 ALL 行）；与 diff 同窗，禁滚动 8 周混用（doc10 季节性双峰）。
# - 双人群 N≥30：sample_n(fut=1) 与 sample_n_clean(fut=0) 各自 ≥30，任一不足不写行
#   （引擎按 车型→前缀→ALL→0 降级，C14）。
# - 前缀层 = LEFT(bt,2) 全前缀（不设平台白名单——与 W1 五表不同；小前缀由双门槛自然淘汰；
#   doc10 §2.3「车型大类规则默认 = body_type 前 2 字符」）。
# - 外层截断 max(0,·) 仅终值；内部样本允许负值抵消（doc10：个体截断引入 1.6~2.9h 正偏）。
# - 基线修正项（肥右尾补偿）只进 delay_mean_s，不进 delay_p50_s（doc10 v2.1 定义）。
SQL_EXPECTED_DELAY = """
WITH ff AS (
    SELECT body,
           min(t) FILTER (WHERE flag = '1') AS f1,
           min(t) FILTER (WHERE flag = '2') AS f2
    FROM tmp_ev_base GROUP BY body
),
ev AS (
    SELECT e.bt, e.station,
           EXTRACT(EPOCH FROM (e.exit_t - e.t))::BIGINT AS rem,
           CASE WHEN f.f1 IS NOT NULL AND f.f1 > e.t THEN 1 ELSE 0 END AS fut
    FROM tmp_ev_base e JOIN ff f ON f.body = e.body
    WHERE e.flag = '0' AND e.t < e.exit_t
      AND (f.f1 IS NULL OR e.t < f.f1)
      AND (f.f2 IS NULL OR e.t < f.f2)
),
eb AS (
    SELECT ev.bt, ev.station, ev.rem, ev.fut,
           COALESCE(rm.p50, rp.p50, ra.p50) AS base
    FROM ev
    LEFT JOIN dss.dim_remaining_time rm
           ON rm.body_type = ev.bt AND rm.station = ev.station AND rm.n >= 30
    LEFT JOIN dss.dim_remaining_time rp
           ON rp.body_type = LEFT(ev.bt, 2) AND rp.station = ev.station AND rp.n >= 30
    LEFT JOIN dss.dim_remaining_time ra
           ON ra.body_type = 'ALL' AND ra.station = ev.station AND ra.n >= 30
),
agg AS (
    SELECT bt AS lvl_key, station,
           AVG(rem - base) FILTER (WHERE fut = 1) AS mean_diff,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY (rem - base)) FILTER (WHERE fut = 1) AS p50_diff,
           count(*) FILTER (WHERE fut = 1) AS n_fut,
           AVG(rem) FILTER (WHERE fut = 0) AS mean_clean,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY rem) FILTER (WHERE fut = 0) AS p50_clean,
           count(*) FILTER (WHERE fut = 0) AS n_clean
    FROM eb WHERE base IS NOT NULL
    GROUP BY bt, station
    UNION ALL
    SELECT LEFT(bt, 2), station,
           AVG(rem - base) FILTER (WHERE fut = 1),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY (rem - base)) FILTER (WHERE fut = 1),
           count(*) FILTER (WHERE fut = 1),
           AVG(rem) FILTER (WHERE fut = 0),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY rem) FILTER (WHERE fut = 0),
           count(*) FILTER (WHERE fut = 0)
    FROM eb WHERE base IS NOT NULL
    GROUP BY LEFT(bt, 2), station
    UNION ALL
    SELECT 'ALL', station,
           AVG(rem - base) FILTER (WHERE fut = 1),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY (rem - base)) FILTER (WHERE fut = 1),
           count(*) FILTER (WHERE fut = 1),
           AVG(rem) FILTER (WHERE fut = 0),
           percentile_cont(0.5) WITHIN GROUP (ORDER BY rem) FILTER (WHERE fut = 0),
           count(*) FILTER (WHERE fut = 0)
    FROM eb WHERE base IS NOT NULL
    GROUP BY station
),
p1s AS (
    SELECT body_type, station, p1 FROM dss.v2_future_flag WHERE week = 'ALL'
)
INSERT INTO dss.dim_expected_delay
SELECT a.lvl_key, a.station,
       GREATEST(0, round(COALESCE(pm.p1, pa.p1, 0.0) * a.mean_diff
                         + (a.mean_clean - a.p50_clean)))::BIGINT,
       GREATEST(0, round(COALESCE(pm.p1, pa.p1, 0.0) * a.p50_diff))::BIGINT,
       a.n_fut::INT, a.n_clean::INT
FROM agg a
LEFT JOIN p1s pm ON pm.body_type = a.lvl_key AND pm.station = a.station
LEFT JOIN p1s pa ON pa.body_type = 'ALL' AND pa.station = a.station
WHERE a.n_fut >= 30 AND a.n_clean >= 30;
"""


def coverage_gaps(p1_cells, delay_rows, p1_threshold: float = 0.10) -> list:
    """净延误表覆盖率缺口（doc10 §2.3：末级兜底 = 0 须配监控）。

    降级链 车型行→前缀行→ALL 行 均无行的 p1≥阈值 格（模型, 站）= 引擎回退 0（纯低估风险）。
    p1_cells: iterable[(body_type, station, p1)]（v2_future_flag week='ALL' 行）；
    delay_rows: set[(body_type, station)]（dim_expected_delay 现有行）。
    返回按 p1 降序的 [(body_type, station, p1), …]。
    """
    gaps = []
    for model, station, p1 in p1_cells:
        if p1 is None or p1 < p1_threshold:
            continue
        if ((model, station) in delay_rows
                or (model[:2], station) in delay_rows
                or ("ALL", station) in delay_rows):
            continue
        gaps.append((model, station, p1))
    return sorted(gaps, key=lambda r: (-r[2], r[0], r[1]))


def _scalar(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchone()[0]


def replay_weeks(replay_start: datetime, replay_end: datetime) -> list:
    """回放窗覆盖的 ISO 周（升序，去重）。"""
    out, d = [], replay_start.date()
    while d <= replay_end.date():
        lb = shifts.iso_week_label(d)
        if lb not in out:
            out.append(lb)
        d += timedelta(days=1)
    return out


def build_quality(comp_report: list, conn) -> int:
    """⑤⑥ 站级质量标记 + 补偿豁免集（Python 侧推导，写 v2_station_quality / v2_comp_excl）。
    mark: low_n = 车型行 n<30（ETA 回退 ALL 行）；mixed = p1≥0.5；both = 两者。
    p1 = v2_future_flag(week='ALL') 车型行，缺 → 该站 ALL 行（⑥a 净化版）。
    comp_excl = mark == 'both'（⑥b：混合低 n 站免补偿）。
    无车型行的格（稀有车型×站）不标记、默认 'ok'（引擎 quality_mark 缺省）——⑤ 目标是
    抓 21IS565 类病态格，车型行格即目标集。
    返回 comp_excl 行数。
    """
    cur = conn.cursor()
    cur.execute("SELECT body_type, station, n FROM dss.dim_remaining_time "
                "WHERE body_type <> 'ALL' AND length(body_type) > 2")
    # W4.2b 三层行：2 字符前缀行（'2N' 等）非车型格，不参与质量标记/豁免判定
    rem_n = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    cur.execute("SELECT body_type, station, p1 FROM dss.v2_future_flag WHERE week = 'ALL'")
    p1_model = {(r[0], r[1]): r[2] for r in cur.fetchall()}
    cur.execute("SELECT station, p1 FROM dss.v2_future_flag "
                "WHERE week = 'ALL' AND body_type = 'ALL'")
    p1_all = {r[0]: r[1] for r in cur.fetchall()}

    rows, excl = [], []
    for (bt, st), n in sorted(rem_n.items()):
        p1 = p1_model.get((bt, st), p1_all.get(st, 0.0)) or 0.0
        low = n < 30
        mixed = p1 >= 0.5
        mark = "both" if (low and mixed) else ("low_n" if low else "mixed" if mixed else "ok")
        rows.append((bt, st, n, p1, mark))
        if mark == "both":
            excl.append((bt, st))
    cur.executemany("INSERT INTO dss.v2_station_quality "
                    "(body_type, station, n_model, p1, mark) VALUES (%s, %s, %s, %s, %s)", rows)
    cur.executemany("INSERT INTO dss.v2_comp_excl (body_type, station) VALUES (%s, %s)", excl)
    comp_report.extend(excl)
    return len(excl)


def sanity_checks(conn, weeks: list) -> list:
    cur = conn.cursor()
    checks = []

    # W2-S1 cohort 表量级：2N54Y- 共享站双线各 n≥100 的行数 ≥ 10（W1 SQL_0809 有同量级行）
    n = _scalar(cur, "SELECT count(*) FROM dss.v2_cohort_remaining "
                     "WHERE body_type = '2N54Y-' AND n >= 100")
    checks.append(("W2-S1", n >= 10, f"cohort 表 2N54Y- n≥100 行数 = {n}（要求 ≥10）"))

    # W2-S2 周度 p 全覆盖 + 决策簇形态：每回放周有行；15IS065(ALL) 每周 n≥300 且 0.03≤p1≤0.98（8周滚动窗实测 343-810）
    cur.execute("SELECT week, count(*) FROM dss.v2_future_flag GROUP BY week")
    per_week = dict(cur.fetchall())
    all_weeks = set(weeks) | {"ALL"}
    missing = sorted(w for w in all_weeks if w not in per_week)
    cur.execute("SELECT week, p1, n FROM dss.v2_future_flag "
                "WHERE body_type = 'ALL' AND station = '15IS065' ORDER BY week")
    cluster = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    cl_ok = all(w in cluster and cluster[w][0] is not None
                and 0.03 <= cluster[w][0] <= 0.98 and cluster[w][1] >= 300
                for w in weeks)
    checks.append(("W2-S2", not missing and cl_ok,
                   f"周度 p 缺失周 = {missing or '无'}；决策簇 15IS065 每周 p1∈[0.03,0.98] 且 n≥300 = {cl_ok}"))

    # W2-S3 补偿豁免集：21IS565(2N54Y-) 必在（W1 抽查解剖的混合站）；总量 ≤500（只抓病态格）
    cur.execute("SELECT count(*) FROM dss.v2_comp_excl")
    n_excl = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM dss.v2_comp_excl "
                "WHERE body_type = '2N54Y-' AND station = '21IS565'")
    has_21 = cur.fetchone()[0] == 1
    checks.append(("W2-S3", has_21 and n_excl <= 500,
                   f"comp_excl 行数 = {n_excl}（要求 ≤500）；(2N54Y-,21IS565) 在豁免集 = {has_21}"))

    # W2-S4 周度线概率：2N54Y- 每周 n≥100（分派比可用）
    cur.execute("SELECT week FROM dss.v2_line_prob WHERE body_type = '2N54Y-' AND n >= 100")
    lp_weeks = {r[0] for r in cur.fetchall()}
    checks.append(("W2-S4", all(w in lp_weeks for w in weeks),
                   f"lineprob 2N54Y- 覆盖周 = {sorted(lp_weeks)}（要求覆盖全部回放周）"))

    # W2-S5（W4.2c）：净延误表结构性自校验：非空 + 双门槛自洽（全行 n≥30）+ 重点车型 YM33J- 有行
    n_ed = _scalar(cur, "SELECT count(*) FROM dss.dim_expected_delay")
    bad_gate = _scalar(cur, "SELECT count(*) FROM dss.dim_expected_delay "
                           "WHERE sample_n < 30 OR sample_n_clean < 30")
    n_ym = _scalar(cur, "SELECT count(*) FROM dss.dim_expected_delay WHERE body_type = 'YM33J-'")
    checks.append(("W2-S5", n_ed > 0 and bad_gate == 0 and n_ym > 0,
                   f"dim_expected_delay 行数 = {n_ed}（要求 >0）；双人群<30 行 = {bad_gate}（要求 0）；"
                   f"YM33J- 行数 = {n_ym}（要求 >0）"))
    return checks


def write_report(conn, checks, a, weeks, comp_report):
    cur = conn.cursor()
    lines = ["# W2 v2 学习器 sanity 报告", "",
             f"- 全窗（fut_all / cohort）：{a.learn_start} → {a.learn_end}（Asia/Shanghai，与 W1 同窗）",
             f"- 回放周（周度 p / 线概率，滚动 8 周学习）：{', '.join(weeks)}",
             f"- 生成时间：{datetime.now(CST):%Y-%m-%d %H:%M}", "", "## 自校验", ""]
    lines += [f"- **{name}** {'PASS' if ok else 'FAIL'}：{detail}" for name, ok, detail in checks]
    lines += ["", "## 周度 p vs 全窗 p（决策簇 top-5 站，人工核对双峰缓解）", ""]
    cur.execute("""SELECT f.station, f.p1 AS p_all, f.n AS n_all, w.week, w.p1 AS p_wk, w.n AS n_wk
                   FROM dss.v2_future_flag f
                   JOIN dss.v2_future_flag w ON w.station = f.station AND w.body_type = f.body_type
                   WHERE f.body_type = 'ALL' AND f.week = 'ALL' AND w.week <> 'ALL'
                     AND f.p1 >= 0.10
                   ORDER BY f.p1 DESC, f.station, w.week LIMIT 40""")
    lines += [f"- {r[0]}: p1_all={r[1]:.4f}(n={r[2]}) | {r[3]}: p1={r[4]:.4f}(n={r[5]})"
              for r in cur.fetchall()]
    lines += ["", "## 线概率 w08（08IS 占比，全窗 + 各回放周，top-8 车型）", ""]
    cur.execute("""SELECT body_type,
              max(CASE WHEN week = 'ALL' THEN w08 END) AS w_all,
              max(CASE WHEN week = 'ALL' THEN n END) AS n_all
              FROM dss.v2_line_prob WHERE body_type <> 'ALL' GROUP BY body_type
              ORDER BY n_all DESC NULLS LAST LIMIT 8""")
    lines += [f"- {r[0]}: w08_all={r[1]:.3f} (n={r[2]})" for r in cur.fetchall()]
    cur.execute("SELECT week, w08, n FROM dss.v2_line_prob WHERE body_type = 'ALL' "
                "ORDER BY week")
    lines += ["- ALL: " + "；".join(f"{r[0]}={r[1]:.3f}(n={r[2]})" for r in cur.fetchall())]
    lines += ["", f"## 补偿豁免集（⑥b，{len(comp_report)} 格，top-30）", ""]
    lines += [f"- {bt}/{st}" for bt, st in comp_report[:30]]
    lines += ["", "## 净延误表覆盖率（W4.2c，p1≥0.1 且降级链无行 = 引擎回退 0 纯低估风险）", ""]
    cur.execute("SELECT body_type, station, p1 FROM dss.v2_future_flag "
                "WHERE week = 'ALL' ORDER BY p1 DESC NULLS LAST")
    cells = cur.fetchall()
    cur.execute("SELECT body_type, station FROM dss.dim_expected_delay")
    delay_rows = {(r[0], r[1]) for r in cur.fetchall()}
    gaps = coverage_gaps(cells, delay_rows)
    lines.append(f"- 缺口格数 = {len(gaps)}（p1≥0.1 且 车型/前缀/ALL 三级均无行）")
    lines += [f"- {bt}/{st} p1={p1:.4f}" for bt, st, p1 in gaps[:30]]
    cur.execute("SELECT count(*) FROM dss.dim_expected_delay")
    lines.append(f"- 表总行数 = {cur.fetchone()[0]:,}")
    lines += ["", "## meta", ""]
    cur.execute("SELECT key, value FROM dss.v2_learner_meta ORDER BY key")
    lines += [f"- {k} = {v}" for k, v in cur.fetchall()]
    out = Path("output") / f"w2_learn_sanity_{datetime.now(CST):%Y%m%d}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"sanity 报告 → {out}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--learn-start", default="2026-04-14")
    ap.add_argument("--learn-end", default="2026-08-15")
    ap.add_argument("--replay-start", default="2026-08-16")
    ap.add_argument("--replay-end", default="2026-09-19")
    a = ap.parse_args()
    learn_start = datetime.fromisoformat(a.learn_start).replace(tzinfo=CST)
    learn_end = datetime.fromisoformat(a.learn_end).replace(hour=23, minute=59, second=59, tzinfo=CST)
    rs = datetime.fromisoformat(a.replay_start).replace(tzinfo=CST)
    re_ = datetime.fromisoformat(a.replay_end).replace(hour=23, minute=59, second=59, tzinfo=CST)
    weeks = replay_weeks(rs, re_)

    conn = common.connect()
    cur = conn.cursor()
    print("DDL …")
    cur.execute(DDL)

    # 1) 全窗 ev_base（fut_all/lineprob 的 ALL 行 + cohort 表 + 质量标记输入）
    print(f"ev_base 全窗（{a.learn_start} → {a.learn_end}）…")
    cur.execute(sqlbase.ev_base_sql(learn_start, learn_end), (learn_start, learn_end))
    print("  行数:", _scalar(cur, "SELECT count(*) FROM tmp_ev_base"))
    print("v2_future_flag(ALL, ⑥a 净化) …")
    cur.execute(SQL_FUTURE_FLAG, ("ALL", "ALL"))
    print("v2_line_prob(ALL, 全窗；cohort 回退链终点) …")
    cur.execute(SQL_LINE_PROB, ("ALL", "ALL"))
    print("v2_cohort_remaining（① 共享站线别）…")
    cur.execute(SQL_COHORT_REM)

    # 1b) W4.2c：站级双列净延误表（必须在全窗 ev_base 之后、周循环之前——周循环会重建 tmp_ev_base；
    #     diff 与 ⑥a 全窗 P(fut) 同窗；读 W1 dim_remaining_time 同源基线 → W1 学习器须先跑）
    print("dim_expected_delay（W4.2c，doc10 §2 双列净延误）…")
    cur.execute(SQL_EXPECTED_DELAY)
    print("  行数:", _scalar(cur, "SELECT count(*) FROM dss.dim_expected_delay"))

    # 2) 每回放周：滚动 8 周 ev_base → fut_week + lineprob
    for w in weeks:
        lo, hi = shifts.week_learning_range(w)
        ws = datetime(lo.year, lo.month, lo.day, tzinfo=CST)
        we = datetime(hi.year, hi.month, hi.day, hour=23, minute=59, second=59, tzinfo=CST)
        print(f"ev_base 周 {w}（{lo} → {hi}）…")
        cur.execute(sqlbase.ev_base_sql(ws, we), (ws, we))
        print("  行数:", _scalar(cur, "SELECT count(*) FROM tmp_ev_base"))
        cur.execute(SQL_FUTURE_FLAG, (w, w))
        cur.execute(SQL_LINE_PROB, (w, w))

    # 3) 质量标记 + 补偿豁免（Python 侧，读 W1 dim_remaining_time 只读）
    comp_report = []
    n_excl = build_quality(comp_report, conn)
    print(f"v2_station_quality / v2_comp_excl：豁免 {n_excl} 格")

    # 4) meta
    meta = {"learn_start": a.learn_start, "learn_end": a.learn_end,
            "replay_weeks": ",".join(weeks),
            "learned_at": f"{datetime.now(CST):%Y-%m-%d %H:%M}",
            "n_v2_future_flag": str(_scalar(cur, "SELECT count(*) FROM dss.v2_future_flag")),
            "n_v2_cohort_remaining": str(_scalar(cur, "SELECT count(*) FROM dss.v2_cohort_remaining")),
            "n_v2_line_prob": str(_scalar(cur, "SELECT count(*) FROM dss.v2_line_prob")),
            "n_v2_comp_excl": str(n_excl),
            "n_dim_expected_delay": str(_scalar(cur, "SELECT count(*) FROM dss.dim_expected_delay"))}
    cur.executemany("INSERT INTO dss.v2_learner_meta (key, value) VALUES (%s, %s)", meta.items())

    checks = sanity_checks(conn, weeks)
    write_report(conn, checks, a, weeks, comp_report)
    conn.close()

    print("\n== 自校验 ==")
    for name, ok, detail in checks:
        print(f"  {name} {'PASS' if ok else 'FAIL'}  {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
