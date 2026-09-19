# -*- coding: utf-8 -*-
"""W1 节拍学习器：dss 五张学习表 + 元数据 + 自校验 S1–S6。

用法：python spike_w1\\01_learn_takt.py [--win-start 2026-04-14] [--win-end 2026-08-15]
学习窗内 entry/exit 事件均在窗内的完整行程车（因果切分：回放只用后续窗口）。
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from . import common, sqlbase
except ImportError:
    import common, sqlbase

CST = ZoneInfo("Asia/Shanghai")

DDL = """
CREATE SCHEMA IF NOT EXISTS dss;
DROP TABLE IF EXISTS dss.dim_remaining_time;
DROP TABLE IF EXISTS dss.dim_loop_remaining;
DROP TABLE IF EXISTS dss.dim_station_takt;
DROP TABLE IF EXISTS dss.dim_future_flag;
DROP TABLE IF EXISTS dss.dim_loop_duration;
DROP TABLE IF EXISTS dss.dim_learner_meta;

CREATE TABLE dss.dim_remaining_time (
    body_type TEXT NOT NULL, station TEXT NOT NULL,
    p50 BIGINT, mean BIGINT, p90 BIGINT, n INT,
    PRIMARY KEY (body_type, station));
CREATE TABLE dss.dim_loop_remaining (
    body_type TEXT NOT NULL, flag TEXT NOT NULL, station TEXT NOT NULL,
    p50 BIGINT, mean BIGINT, p90 BIGINT, n INT,
    PRIMARY KEY (body_type, flag, station));
CREATE TABLE dss.dim_station_takt (
    frm TEXT NOT NULL, to_station TEXT NOT NULL, body_type TEXT NOT NULL,
    p50 BIGINT, mean BIGINT, p90 BIGINT, n INT,
    PRIMARY KEY (frm, to_station, body_type));
CREATE TABLE dss.dim_future_flag (
    body_type TEXT NOT NULL, station TEXT NOT NULL,
    p1 DOUBLE PRECISION, p2 DOUBLE PRECISION, n INT,
    PRIMARY KEY (body_type, station));
CREATE TABLE dss.dim_loop_duration (
    body_type TEXT NOT NULL, flag TEXT NOT NULL,
    l BIGINT, n INT,
    PRIMARY KEY (body_type, flag));
CREATE TABLE dss.dim_learner_meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

SQL_REMAINING = """
INSERT INTO dss.dim_remaining_time
SELECT bt, station,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY rem))::BIGINT,
       round(avg(rem))::BIGINT,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY rem))::BIGINT,
       count(*)::INT
FROM (
    SELECT bt, station, EXTRACT(EPOCH FROM (exit_t - t))::BIGINT AS rem
    FROM tmp_ev_base
    WHERE flag = '0' AND t < exit_t AND (last_loop_t IS NULL OR t > last_loop_t)
) s GROUP BY bt, station
UNION ALL
SELECT 'ALL', station,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY rem))::BIGINT,
       round(avg(rem))::BIGINT,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY rem))::BIGINT,
       count(*)::INT
FROM (
    SELECT station, EXTRACT(EPOCH FROM (exit_t - t))::BIGINT AS rem
    FROM tmp_ev_base
    WHERE flag = '0' AND t < exit_t AND (last_loop_t IS NULL OR t > last_loop_t)
) s GROUP BY station;
"""

SQL_LOOP_REMAINING = """
INSERT INTO dss.dim_loop_remaining
SELECT bt, flag, station,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY rem))::BIGINT,
       round(avg(rem))::BIGINT,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY rem))::BIGINT,
       count(*)::INT
FROM (
    SELECT bt, flag, station, EXTRACT(EPOCH FROM (exit_t - t))::BIGINT AS rem
    FROM tmp_ev_base
    WHERE flag IN ('1', '2') AND t < exit_t
) s GROUP BY bt, flag, station
UNION ALL
SELECT 'ALL', flag, station,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY rem))::BIGINT,
       round(avg(rem))::BIGINT,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY rem))::BIGINT,
       count(*)::INT
FROM (
    SELECT flag, station, EXTRACT(EPOCH FROM (exit_t - t))::BIGINT AS rem
    FROM tmp_ev_base
    WHERE flag IN ('1', '2') AND t < exit_t
) s GROUP BY flag, station;
"""

_SQL_TAKT_BASE = """
WITH seg AS (
    SELECT bt,
           station AS frm,
           LEAD(station) OVER w AS to_st,
           EXTRACT(EPOCH FROM (LEAD(t) OVER w - t))::BIGINT AS dur
    FROM tmp_ev_base
    WINDOW w AS (PARTITION BY body ORDER BY t, id)
)
"""

SQL_TAKT_MODEL = _SQL_TAKT_BASE + """
, q AS (
    SELECT frm, to_st, bt,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY dur) AS q1,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY dur) AS q3
    FROM seg WHERE to_st IS NOT NULL
    GROUP BY frm, to_st, bt
),
kept AS (
    SELECT s.frm, s.to_st, s.bt, s.dur
    FROM seg s JOIN q ON q.frm = s.frm AND q.to_st = s.to_st AND q.bt = s.bt
    WHERE s.to_st IS NOT NULL
      AND s.dur <= q.q3 + 3.0 * (q.q3 - q.q1)
)
INSERT INTO dss.dim_station_takt
SELECT frm, to_st, bt,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY dur))::BIGINT,
       round(avg(dur))::BIGINT,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY dur))::BIGINT,
       count(*)::INT
FROM kept GROUP BY frm, to_st, bt;
"""

SQL_TAKT_ALL = _SQL_TAKT_BASE + """
, q AS (
    SELECT frm, to_st,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY dur) AS q1,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY dur) AS q3
    FROM seg WHERE to_st IS NOT NULL
    GROUP BY frm, to_st
),
kept AS (
    SELECT s.frm, s.to_st, s.dur
    FROM seg s JOIN q ON q.frm = s.frm AND q.to_st = s.to_st
    WHERE s.to_st IS NOT NULL
      AND s.dur <= q.q3 + 3.0 * (q.q3 - q.q1)
)
INSERT INTO dss.dim_station_takt
SELECT frm, to_st, 'ALL',
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY dur))::BIGINT,
       round(avg(dur))::BIGINT,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY dur))::BIGINT,
       count(*)::INT
FROM kept GROUP BY frm, to_st;
"""

SQL_FUTURE_FLAG = """
WITH ff AS (
    SELECT body,
           min(t) FILTER (WHERE flag = '1') AS f1,
           min(t) FILTER (WHERE flag = '2') AS f2
    FROM tmp_ev_base GROUP BY body
)
INSERT INTO dss.dim_future_flag
SELECT e.bt, e.station,
       AVG(CASE WHEN f.f1 IS NOT NULL AND f.f1 > e.t THEN 1.0 ELSE 0.0 END),
       AVG(CASE WHEN f.f2 IS NOT NULL AND f.f2 > e.t THEN 1.0 ELSE 0.0 END),
       count(*)::INT
FROM tmp_ev_base e JOIN ff f ON f.body = e.body
WHERE e.flag = '0' AND e.t < e.exit_t
GROUP BY e.bt, e.station
UNION ALL
SELECT 'ALL', e.station,
       AVG(CASE WHEN f.f1 IS NOT NULL AND f.f1 > e.t THEN 1.0 ELSE 0.0 END),
       AVG(CASE WHEN f.f2 IS NOT NULL AND f.f2 > e.t THEN 1.0 ELSE 0.0 END),
       count(*)::INT
FROM tmp_ev_base e JOIN ff f ON f.body = e.body
WHERE e.flag = '0' AND e.t < e.exit_t
GROUP BY e.station;
"""

SQL_LOOP_DURATION = """
WITH vl AS (
    SELECT body, bt, flag,
           EXTRACT(EPOCH FROM (max(t) - min(t)))::BIGINT AS l
    FROM tmp_ev_base
    WHERE flag IN ('1', '2') AND t < exit_t
    GROUP BY body, bt, flag
)
INSERT INTO dss.dim_loop_duration
SELECT bt, flag,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY l))::BIGINT,
       count(*)::INT
FROM vl GROUP BY bt, flag
UNION ALL
SELECT 'ALL', flag,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY l))::BIGINT,
       count(*)::INT
FROM vl GROUP BY flag;
"""

SQL_DEDUP_REMOVED = """
WITH p AS (
    SELECT "ID" AS id, "BODY_ID" AS body, "RW_STATION_ID" AS station, "DATE_EVT" AS t
    FROM ods.carbody_history
    WHERE "BODY_ID" LIKE '78202%%' AND "DATE_EVT" >= %s AND "DATE_EVT" <= %s
),
entry AS (
    SELECT body, min(t) AS entry_t FROM p WHERE station IN ('01IS205', '1L360RB', '01IS045')
    GROUP BY body
),
x AS (
    SELECT body, min(t) AS exit_t FROM p WHERE station IN ('K3IS140', '1J440RB', 'K2IS075', 'K1IS135')
    GROUP BY body
),
span AS (
    SELECT p.id FROM p JOIN entry e ON p.body = e.body JOIN x ON p.body = x.body
    WHERE x.exit_t > e.entry_t AND p.t >= e.entry_t AND p.t <= x.exit_t
)
SELECT (SELECT count(*) FROM span) - (SELECT count(*) FROM tmp_ev_base) AS removed;
"""

SQL_TAKT_EXCL = """
WITH seg AS (
    SELECT bt, station AS frm,
           LEAD(station) OVER w AS to_st,
           EXTRACT(EPOCH FROM (LEAD(t) OVER w - t))::BIGINT AS dur
    FROM tmp_ev_base
    WINDOW w AS (PARTITION BY body ORDER BY t, id)
),
q AS (
    SELECT frm, to_st, bt,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY dur) AS q1,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY dur) AS q3
    FROM seg WHERE to_st IS NOT NULL
    GROUP BY frm, to_st, bt
),
s AS (
    SELECT seg.frm, seg.to_st, seg.bt,
           (seg.dur > q.q3 + 3.0 * (q.q3 - q.q1)) AS outlier
    FROM seg JOIN q ON q.frm = seg.frm AND q.to_st = seg.to_st AND q.bt = seg.bt
    WHERE seg.to_st IS NOT NULL
)
SELECT count(*)::BIGINT AS total,
       count(*) FILTER (WHERE outlier)::BIGINT AS removed,
       (count(*) FILTER (WHERE outlier)::NUMERIC / NULLIF(count(*), 0))::TEXT AS excl_rate
FROM s;
"""

SQL_TAKT_EXCL_TOP20 = """
WITH seg AS (
    SELECT bt, station AS frm,
           LEAD(station) OVER w AS to_st,
           EXTRACT(EPOCH FROM (LEAD(t) OVER w - t))::BIGINT AS dur
    FROM tmp_ev_base
    WINDOW w AS (PARTITION BY body ORDER BY t, id)
),
q AS (
    SELECT frm, to_st, bt,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY dur) AS q1,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY dur) AS q3
    FROM seg WHERE to_st IS NOT NULL
    GROUP BY frm, to_st, bt
)
SELECT s.frm, s.to_st, s.bt, count(*) AS n,
       count(*) FILTER (WHERE s.dur > q.q3 + 3.0 * (q.q3 - q.q1)) AS removed,
       round(100.0 * count(*) FILTER (WHERE s.dur > q.q3 + 3.0 * (q.q3 - q.q1))
             / NULLIF(count(*), 0), 1) AS excl_pct
FROM seg s JOIN q ON q.frm = s.frm AND q.to_st = s.to_st AND q.bt = s.bt
WHERE s.to_st IS NOT NULL
GROUP BY s.frm, s.to_st, s.bt
HAVING count(*) >= 30
ORDER BY excl_pct DESC NULLS LAST
LIMIT 20;
"""

# 六确决策站簇（2026-09-19 干跑复证：首条 flag=1/2 集中度）
REWORK_CLUSTER = ("15IS065", "13IS065", "14IS065")    # 六确 97%；1 周窗实测 97.5%
BLACKTOP_CLUSTER = ("14IS260", "24IS185")             # 六确 76%；1 周窗实测 90%

# 同窗行程 P50（entry→exit），S1 自洽校验用
SQL_JOURNEY_P50 = """
WITH entry AS (
    SELECT body, min(t) AS et, min(station) AS estn, min(exit_t) AS exit_t
    FROM tmp_ev_base
    WHERE station IN ('01IS205', '1L360RB', '01IS045')
    GROUP BY body
)
SELECT estn,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM (exit_t - et))))::BIGINT AS p50,
       count(*) AS n
FROM entry
WHERE exit_t IS NOT NULL AND exit_t > et
GROUP BY estn;
"""

# 首条 flag=1 / flag=2 的站分布（六确复证 + 报告）
SQL_FIRST_FLAG1 = """
WITH ff AS (SELECT body, min(t) AS f1 FROM tmp_ev_base WHERE flag = '1' GROUP BY body)
SELECT e.station AS station, count(*) AS veh
FROM ff JOIN tmp_ev_base e ON e.body = ff.body AND e.t = ff.f1 AND e.flag = '1'
GROUP BY e.station ORDER BY veh DESC;
"""
SQL_FIRST_FLAG2 = """
WITH ff AS (SELECT body, min(t) AS f2 FROM tmp_ev_base WHERE flag = '2' GROUP BY body)
SELECT e.station AS station, count(*) AS veh
FROM ff JOIN tmp_ev_base e ON e.body = ff.body AND e.t = ff.f2 AND e.flag = '2'
GROUP BY e.station ORDER BY veh DESC;
"""

# p1 按行程位置剖面（S2c + 人工核对阶跃形态；无参数执行，% 不转义）
# 干跑实测形态：入口→22% 平坦 0.064；24–40% 08/09 分支分裂（0.017 vs 0.124）；
# 76–82% 阶跃至 ≈0；82%+ 全 0。
SQL_P1_PROFILE = """
WITH cnt AS (SELECT body, count(*) AS c FROM tmp_ev_base GROUP BY body),
     ranked AS (
         SELECT body, station, c,
                row_number() OVER (PARTITION BY body ORDER BY t, id) AS rn
         FROM tmp_ev_base e JOIN cnt USING (body)
     ),
     posmed AS (
         SELECT station,
                percentile_cont(0.5) WITHIN GROUP (ORDER BY rn / c::numeric) AS med_pos
         FROM ranked GROUP BY station
     ),
     ff AS (SELECT body, min(t) FILTER (WHERE flag = '1') AS f1 FROM tmp_ev_base GROUP BY body)
SELECT e.station AS station, pm.med_pos AS pos,
       AVG(CASE WHEN f.f1 IS NOT NULL AND f.f1 > e.t THEN 1.0 ELSE 0 END) AS p1,
       count(*) AS n
FROM tmp_ev_base e
JOIN posmed pm ON pm.station = e.station
JOIN ff f ON f.body = e.body
WHERE e.flag = '0' AND e.t < e.exit_t
GROUP BY e.station, pm.med_pos
HAVING count(*) >= 500
ORDER BY pm.med_pos;
"""


def _scalar(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchone()[0]


def _row(cur, sql, params=None):
    cur.execute(sql, params or ())
    return cur.fetchone()


def sanity_checks(conn) -> list:
    """返回 [(编号, 通过?, 说明)]；全部 True 才允许 exit 0。"""
    cur = conn.cursor()
    checks = []

    # S1 入口站 P50：绝对范围 [8h, 30h] 且与同窗行程 P50 自洽（≤10%）
    # （仅对行程 n≥50 的站判定；01IS045 为小样本站（全窗 ~150 车）只报告不判定；
    #  阶段计划「16–20h」已被干跑实测 14.3h/24.3h 取代，见「干跑校准结论」）
    cur.execute("SELECT station, p50 FROM dss.dim_remaining_time "
                "WHERE body_type = 'ALL' AND station IN (%s, %s, %s)",
                common.ENTRY_STATIONS)
    rt = {r[0]: r[1] for r in cur.fetchall()}
    cur.execute(SQL_JOURNEY_P50)
    jp = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    s1_ok = len(rt) == 3
    s1_detail = []
    for stn in sorted(rt):
        p50 = rt[stn]
        j, n = jp.get(stn, (None, 0))
        if n >= 50:
            good = (8 * 3600 <= p50 <= 30 * 3600
                    and j is not None and abs(p50 - j) / j <= 0.10)
            s1_ok = s1_ok and good
            s1_detail.append(f"{stn}: P50={p50}s（行程 P50={j}s n={n}）")
        else:
            s1_detail.append(f"{stn}: P50={p50}s（行程 n={n} <50 小样本，只报告）")
    checks.append(("S1", s1_ok,
                   "入口站 P50（n≥50 站：8–30h 且与同窗行程 P50 偏差 ≤10%）："
                   + "；".join(s1_detail)))

    # S2a 首条 flag=1 集中于返工簇（≥80%；六确 97%，1 周窗实测 97.5%）
    cur.execute(SQL_FIRST_FLAG1)
    f1 = cur.fetchall()
    tot1 = sum(r[1] for r in f1) or 1
    in1 = sum(r[1] for r in f1 if r[0] in REWORK_CLUSTER)
    top1 = ", ".join(f"{r[0]}:{r[1]}" for r in f1[:5])
    checks.append(("S2a", in1 / tot1 >= 0.80,
                   f"首条 flag=1 top-5=[{top1}] 簇占比={in1}/{tot1}="
                   f"{in1 / tot1:.1%}（要求 ≥80%）"))

    # S2b 首条 flag=2 集中于黑顶簇（≥60%；六确 76%，1 周窗实测 90%）
    cur.execute(SQL_FIRST_FLAG2)
    f2 = cur.fetchall()
    tot2 = sum(r[1] for r in f2) or 1
    in2 = sum(r[1] for r in f2 if r[0] in BLACKTOP_CLUSTER)
    top2 = ", ".join(f"{r[0]}:{r[1]}" for r in f2[:5])
    checks.append(("S2b", in2 / tot2 >= 0.60,
                   f"首条 flag=2 top-5=[{top2}] 簇占比={in2}/{tot2}="
                   f"{in2 / tot2:.1%}（要求 ≥60%）"))

    # S2c p1 剖面：max（n≥1000 站）≥0.03 且 min ≤0.01（阶跃存在且后段 ≈0）
    cur.execute(SQL_P1_PROFILE)
    big = [r for r in cur.fetchall() if r[3] >= 1000]
    pmax = max((r[2] for r in big), default=0.0)
    pmin = min((r[2] for r in big), default=1.0)
    checks.append(("S2c", len(big) >= 5 and pmax >= 0.03 and pmin <= 0.01,
                   f"p1 剖面（n≥1000，{len(big)} 站）：max={pmax:.3f} min={pmin:.3f}"
                   f"（要求 max≥0.03 且 min≤0.01）"))

    # S3 环时长表 n 充足（学习窗 ≈ 全窗 78%：返工 13,010→≥9,000；黑顶 9,711→≥7,000）
    cur.execute("SELECT flag, n FROM dss.dim_loop_duration WHERE body_type = 'ALL'")
    ld = dict(cur.fetchall())
    ok = ld.get("1", 0) >= 9000 and ld.get("2", 0) >= 7000
    checks.append(("S3", ok, f"loop_duration n: flag1={ld.get('1')} flag2={ld.get('2')}"))

    # S4 环分布事件量（全窗 41.7 万 / 18.3 万 × 78%）
    cur.execute("SELECT flag, SUM(n) FROM dss.dim_loop_remaining WHERE body_type <> 'ALL' GROUP BY flag")
    lr = dict(cur.fetchall())
    ok = lr.get("1", 0) >= 250000 and lr.get("2", 0) >= 100000
    checks.append(("S4", ok, f"loop_remaining 事件量: flag1={lr.get('1')} flag2={lr.get('2')}"))

    # S5 takt IQR 剔除率 ≤ 30%
    cur.execute("SELECT value::FLOAT8 FROM dss.dim_learner_meta WHERE key = 'takt_iqr_excl_rate'")
    rate = cur.fetchone()[0]
    checks.append(("S5", rate <= 0.30, f"takt IQR 剔除率={rate:.4f}（>0.30 告警）"))

    # S6 干净后缀保留率 ≥ 0.80（环事件仅 11.3%，损失应可控）
    cur.execute("SELECT value::FLOAT8 FROM dss.dim_learner_meta WHERE key = 'clean_suffix_rate'")
    sr = cur.fetchone()[0]
    checks.append(("S6", sr >= 0.80, f"干净后缀率={sr:.4f}"))
    return checks


def write_sanity_report(conn, checks, win_start, win_end):
    cur = conn.cursor()
    lines = [f"# W1 学习器 sanity 报告", f"",
             f"- 学习窗：{win_start:%Y-%m-%d} → {win_end:%Y-%m-%d}（Asia/Shanghai）",
             f"- 生成时间：{datetime.now(CST):%Y-%m-%d %H:%M}", "", "## 自校验", ""]
    lines += [f"- **{name}** {'PASS' if ok else 'FAIL'}：{detail}" for name, ok, detail in checks]
    lines += ["", "## meta", ""]
    cur.execute("SELECT key, value FROM dss.dim_learner_meta ORDER BY key")
    lines += [f"- {k} = {v}" for k, v in cur.fetchall()]
    lines += ["", "## 入口/出口站 P50（ALL 行，秒）", ""]
    _stns = list(common.ENTRY_STATIONS) + list(common.EXIT_STATIONS)
    _fmt = ",".join(["%s"] * len(_stns))
    cur.execute(f"SELECT station, p50, n FROM dss.dim_remaining_time "
                f"WHERE body_type = 'ALL' AND station IN ({_fmt}) ORDER BY station",
                _stns)
    lines += [f"- {r[0]}: P50={r[1]}s n={r[2]}" for r in cur.fetchall()]
    lines += ["", "## p1/p2 top-20 站（ALL 行）——人工核对 p 形态", ""]
    cur.execute("SELECT station, p1, p2, n FROM dss.dim_future_flag "
                "WHERE body_type = 'ALL' ORDER BY p1 DESC LIMIT 20")
    lines += [f"- {r[0]}: p1={r[1]:.4f} p2={r[2]:.4f} n={r[3]}" for r in cur.fetchall()]
    lines += ["", "## 首条 flag=1 / flag=2 站分布（六确）", ""]
    cur.execute(SQL_FIRST_FLAG1)
    lines += ["首条 flag=1 top-8："]
    lines += [f"- {r[0]}: {r[1]}车" for r in cur.fetchall()[:8]]
    cur.execute(SQL_FIRST_FLAG2)
    lines += ["首条 flag=2 top-8："]
    lines += [f"- {r[0]}: {r[1]}车" for r in cur.fetchall()[:8]]
    lines += ["", "## p1 按行程位置剖面（阶跃形态人工核对，n≥500）", ""]
    cur.execute(SQL_P1_PROFILE)
    lines += [f"- 位置 {r[1]:.0%}: {r[0]} p1={r[2]:.4f} n={r[3]}" for r in cur.fetchall()]
    lines += ["", "## 同窗行程 P50（entry→exit，S1 自洽参照）", ""]
    cur.execute(SQL_JOURNEY_P50)
    lines += [f"- {r[0]}: {r[1]}s（{r[1] / 3600:.1f}h）n={r[2]}" for r in cur.fetchall()]
    lines += ["", "## takt 分桶剔除率 top-20（n≥30）", ""]
    cur.execute(SQL_TAKT_EXCL_TOP20)
    lines += [f"- {r[0]}→{r[1]} ({r[2]}): n={r[3]} removed={r[4]} excl={r[5]}%"
              for r in cur.fetchall()]
    lines += ["", "## 环时长 L（top-10 车型 + ALL）", ""]
    cur.execute("SELECT body_type, flag, l, n FROM dss.dim_loop_duration "
                "ORDER BY n DESC LIMIT 11")
    lines += [f"- {r[0]} flag{r[1]}: L={r[2]}s n={r[3]}" for r in cur.fetchall()]
    out = Path("output") / f"w1_learn_sanity_{datetime.now(CST):%Y%m%d}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"sanity 报告 → {out}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows GBK 控制台：防 emoji/特殊字符 print 崩溃
    ap = argparse.ArgumentParser()
    ap.add_argument("--win-start", default="2026-04-14")
    ap.add_argument("--win-end", default="2026-08-15")
    a = ap.parse_args()
    win_start = datetime.fromisoformat(a.win_start).replace(tzinfo=CST)
    win_end = datetime.fromisoformat(a.win_end).replace(hour=23, minute=59, second=59, tzinfo=CST)

    conn = common.connect()
    cur = conn.cursor()
    print("DDL …")
    cur.execute(DDL)

    print(f"ev_base（{a.win_start} → {a.win_end}）…")
    cur.execute(sqlbase.ev_base_sql(win_start, win_end), (win_start, win_end))
    print("  表行数:", _scalar(cur, "SELECT count(*) FROM tmp_ev_base"))

    for name, sql in (("dim_remaining_time", SQL_REMAINING),
                      ("dim_loop_remaining", SQL_LOOP_REMAINING),
                      ("dim_station_takt(车型)", SQL_TAKT_MODEL),
                      ("dim_station_takt(ALL)", SQL_TAKT_ALL),
                      ("dim_future_flag", SQL_FUTURE_FLAG),
                      ("dim_loop_duration", SQL_LOOP_DURATION)):
        print(f"{name} …")
        cur.execute(sql)

    print("meta …")
    nv, ne, n0, n1, n2, nsuf = _row(
        cur,
        """SELECT count(DISTINCT body)::BIGINT, count(*)::BIGINT,
                  count(*) FILTER (WHERE flag = '0')::BIGINT,
                  count(*) FILTER (WHERE flag = '1')::BIGINT,
                  count(*) FILTER (WHERE flag = '2')::BIGINT,
                  count(*) FILTER (WHERE flag = '0' AND t < exit_t
                                       AND (last_loop_t IS NULL OR t > last_loop_t))::BIGINT
           FROM tmp_ev_base""")
    removed = _scalar(cur, SQL_DEDUP_REMOVED, (win_start, win_end))
    cur.execute(SQL_TAKT_EXCL)
    total, t_removed, excl_rate = cur.fetchone()
    meta = {
        "win_start": f"{win_start:%Y-%m-%d}", "win_end": f"{win_end:%Y-%m-%d}",
        "learned_at": f"{datetime.now(CST):%Y-%m-%d %H:%M}",
        "n_vehicles": str(nv), "n_events": str(ne),
        "n_flag0_events": str(n0), "n_flag1_events": str(n1), "n_flag2_events": str(n2),
        "n_clean_suffix_events": str(nsuf),
        "clean_suffix_rate": f"{nsuf / n0:.6f}" if n0 else "0",
        "n_dedup_removed": str(removed),
        "takt_n_total": str(total), "takt_n_removed": str(t_removed),
        "takt_iqr_excl_rate": excl_rate,
    }
    cur.executemany("INSERT INTO dss.dim_learner_meta (key, value) VALUES (%s, %s)",
                    meta.items())

    checks = sanity_checks(conn)
    write_sanity_report(conn, checks, win_start, win_end)
    conn.close()

    print("\n== 自校验 ==")
    for name, ok, detail in checks:
        print(f"  {name} {'PASS' if ok else 'FAIL'}  {detail}")
    return 0 if all(ok for _, ok, _ in checks) else 1


if __name__ == "__main__":
    sys.exit(main())
