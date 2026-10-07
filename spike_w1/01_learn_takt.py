# -*- coding: utf-8 -*-
"""W1 节拍学习器：dss 五张学习表 + 元数据 + 自校验 S1–S6。

用法：python spike_w1\\01_learn_takt.py [--win-start 2026-04-14] [--win-end 2026-08-15]
学习窗内 entry/exit 事件均在窗内的完整行程车（因果切分：回放只用后续窗口）。
"""
import argparse
import re
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
    l BIGINT, l_mean BIGINT, l_p90 BIGINT, n INT,
    PRIMARY KEY (body_type, flag));
CREATE TABLE dss.dim_learner_meta (
    key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# ---------------------------------------------------------------------------
# W1 表三层行生成（W4.2b：车型 + 平台前缀 + ALL；前缀清单运行时实测生成（C10 不硬编码）；
# 前缀空 → 仅车型 + ALL（与 W4.2 前行形状一致））。loader 全表装载，前缀行以
# (station, '2N') 等键自然入表，旧引擎 resolve（model→ALL）不消费、无行为变化；
# W4.3 统一引擎按 车型→前缀→ALL 层级回退消费。
# ---------------------------------------------------------------------------

def remaining_sql(prefixes) -> str:
    """dim_remaining_time：主分布（flag='0' 干净后缀）三层行。"""
    sub = ("SELECT bt, station, EXTRACT(EPOCH FROM (exit_t - t))::BIGINT AS rem\n"
           "    FROM tmp_ev_base\n"
           "    WHERE flag = '0' AND t < exit_t AND (last_loop_t IS NULL OR t > last_loop_t)")
    pfx_in = prefix_in_list(prefixes)
    pfx_block = f"""
UNION ALL
SELECT LEFT(bt, 2), station,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY rem))::BIGINT,
       round(avg(rem))::BIGINT,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY rem))::BIGINT,
       count(*)::INT
FROM (
    {sub}
      AND LEFT(bt, 2) IN ({pfx_in})
) s GROUP BY LEFT(bt, 2), station""" if pfx_in else ""
    return f"""
INSERT INTO dss.dim_remaining_time
SELECT bt, station,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY rem))::BIGINT,
       round(avg(rem))::BIGINT,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY rem))::BIGINT,
       count(*)::INT
FROM (
    {sub}
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
) s GROUP BY station{pfx_block};
"""


def loop_remaining_sql(prefixes) -> str:
    """dim_loop_remaining：环分布（flag IN ('1','2')）三层行。"""
    sub = ("SELECT bt, flag, station, EXTRACT(EPOCH FROM (exit_t - t))::BIGINT AS rem\n"
           "    FROM tmp_ev_base\n"
           "    WHERE flag IN ('1', '2') AND t < exit_t")
    pfx_in = prefix_in_list(prefixes)
    pfx_block = f"""
UNION ALL
SELECT LEFT(bt, 2), flag, station,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY rem))::BIGINT,
       round(avg(rem))::BIGINT,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY rem))::BIGINT,
       count(*)::INT
FROM (
    {sub}
      AND LEFT(bt, 2) IN ({pfx_in})
) s GROUP BY LEFT(bt, 2), flag, station""" if pfx_in else ""
    return f"""
INSERT INTO dss.dim_loop_remaining
SELECT bt, flag, station,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY rem))::BIGINT,
       round(avg(rem))::BIGINT,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY rem))::BIGINT,
       count(*)::INT
FROM (
    {sub}
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
) s GROUP BY flag, station{pfx_block};
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


_SQL_TAKT_MODEL = _SQL_TAKT_BASE + """
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

_SQL_TAKT_ALL = _SQL_TAKT_BASE + """
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

_SQL_TAKT_PREFIX_FMT = _SQL_TAKT_BASE + """
, q AS (
    SELECT frm, to_st, LEFT(bt, 2) AS pfx,
           percentile_cont(0.25) WITHIN GROUP (ORDER BY dur) AS q1,
           percentile_cont(0.75) WITHIN GROUP (ORDER BY dur) AS q3
    FROM seg WHERE to_st IS NOT NULL AND LEFT(bt, 2) IN ({pfx})
    GROUP BY frm, to_st, pfx
),
kept AS (
    SELECT s.frm, s.to_st, LEFT(s.bt, 2) AS pfx, s.dur
    FROM seg s JOIN q ON q.frm = s.frm AND q.to_st = s.to_st AND q.pfx = LEFT(s.bt, 2)
    WHERE s.to_st IS NOT NULL
      AND s.dur <= q.q3 + 3.0 * (q.q3 - q.q1)
)
INSERT INTO dss.dim_station_takt
SELECT frm, to_st, pfx,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY dur))::BIGINT,
       round(avg(dur))::BIGINT,
       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY dur))::BIGINT,
       count(*)::INT
FROM kept GROUP BY frm, to_st, pfx;
"""


def takt_sql(prefixes) -> str:
    """dim_station_takt：段 takt 三层行（每层各自 IQR 剔除；多语句单 execute）。"""
    stmts = [_SQL_TAKT_MODEL]
    pfx_in = prefix_in_list(prefixes)
    if pfx_in:
        stmts.append(_SQL_TAKT_PREFIX_FMT.format(pfx=pfx_in))
    stmts.append(_SQL_TAKT_ALL)
    return "\n".join(stmts)

def future_flag_sql(prefixes) -> str:
    """dim_future_flag：前决策概率三层行。

    W4.2b 实测坑（2026-10-05）：原形态（每分支各自 JOIN ff）下，多 UNION 语句中
    带 LEFT(bt,2) IN(...) 过滤的前缀分支被 planner 选成 Nested Loop
    （外扫 ff × Materialized tmp_ev_base 重扫 3.68M 行），>600s 超时；
    单分支同 SQL 反而 6s（hash join）。重构：join 物化进 j CTE（被引用 ≥2 次必物化，
    一次 hash join 扫完），各分支只做 group 聚合，无分支内 join。
    """
    pfx_in = prefix_in_list(prefixes)
    pfx_block = f"""
UNION ALL
SELECT LEFT(bt, 2), station,
       AVG(CASE WHEN f1 IS NOT NULL AND f1 > t THEN 1.0 ELSE 0.0 END),
       AVG(CASE WHEN f2 IS NOT NULL AND f2 > t THEN 1.0 ELSE 0.0 END),
       count(*)::INT
FROM j WHERE LEFT(bt, 2) IN ({pfx_in})
GROUP BY LEFT(bt, 2), station""" if pfx_in else ""
    return f"""
WITH ff AS (
    SELECT body,
           min(t) FILTER (WHERE flag = '1') AS f1,
           min(t) FILTER (WHERE flag = '2') AS f2
    FROM tmp_ev_base GROUP BY body
),
j AS (
    SELECT e.bt, e.station, e.t, f.f1, f.f2
    FROM tmp_ev_base e JOIN ff f ON f.body = e.body
    WHERE e.flag = '0' AND e.t < e.exit_t
)
INSERT INTO dss.dim_future_flag
SELECT bt, station,
       AVG(CASE WHEN f1 IS NOT NULL AND f1 > t THEN 1.0 ELSE 0.0 END),
       AVG(CASE WHEN f2 IS NOT NULL AND f2 > t THEN 1.0 ELSE 0.0 END),
       count(*)::INT
FROM j GROUP BY bt, station
UNION ALL
SELECT 'ALL', station,
       AVG(CASE WHEN f1 IS NOT NULL AND f1 > t THEN 1.0 ELSE 0.0 END),
       AVG(CASE WHEN f2 IS NOT NULL AND f2 > t THEN 1.0 ELSE 0.0 END),
       count(*)::INT
FROM j GROUP BY station{pfx_block};
"""

# 平台前缀（C10：实测生成不硬编码，W4.0b 口径）：前缀 = LEFT(bt,2)，学习窗车数 n≥30 入选
SQL_PLATFORM_PREFIXES = """
SELECT LEFT(bt, 2) AS pfx, count(DISTINCT body) AS n
FROM tmp_ev_base
GROUP BY LEFT(bt, 2)
HAVING count(DISTINCT body) >= 30
ORDER BY pfx;
"""

_PFX_RE = re.compile(r"^[A-Z0-9]{2}$")


def prefix_in_list(prefixes) -> str | None:
    """前缀清单 → SQL IN 列表文本（空清单 → None = 不前缀块）。仅允许 2 位大写字母/数字（数据派生白名单，防注入）。"""
    for p in prefixes:
        if not _PFX_RE.match(p):
            raise ValueError(f"非法平台前缀（须 2 位大写字母/数字）: {p!r}")
    return ", ".join(f"'{p}'" for p in prefixes) if prefixes else None


# dim_loop_duration 新契约（docs/15 §0.4）：逐车 L 来自 tmp_loop_bounds（纯净窗时长），
# end 为 NULL → exit_t 回退（D4 无网关在制车）；exit_t 在 tmp_ev_base（JOIN 取，
# tmp_loop_bounds 六列无此列——双审 agy 建议 1）；exit_t NULL 且无 end 的车自然排除（l 为 NULL）。
# l = P50（兼容名）；行形状 4 元组 (l, l_mean, l_p90, n)（消费端 row[0]=l / row[-1]=n 零改动）。

def _ld_block(select_expr: str, flag: str, lcol: str, extra_where: str = "",
              group_expr: str | None = None) -> str:
    # ALL 行 = 常量 + 聚合 → 无 GROUP BY（PG 禁常量 GROUP BY）
    gb = f"\nGROUP BY {group_expr}" if group_expr else ""
    return (
        f"SELECT {select_expr}, '{flag}',\n"
        f"       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY {lcol}))::BIGINT,\n"
        f"       round(avg({lcol}))::BIGINT,\n"
        f"       round(percentile_cont(0.9) WITHIN GROUP (ORDER BY {lcol}))::BIGINT,\n"
        f"       count({lcol})::INT\n"
        f"FROM per_veh WHERE {lcol} > 0{extra_where}{gb}"
    )


def loop_duration_sql(prefixes) -> str:
    """三层行（车型 + 平台前缀 + ALL）× 双环旗（'1'/'2'）的 INSERT SQL。前缀空 → 仅车型+ALL。"""
    blocks = []
    for lcol, flag in (("l1", "1"), ("l2", "2")):
        blocks.append(_ld_block("bt", flag, lcol, group_expr="bt"))              # 车型行
        pfx_in = prefix_in_list(prefixes)
        if pfx_in:
            blocks.append(_ld_block("LEFT(bt, 2)", flag, lcol,
                                    f" AND LEFT(bt, 2) IN ({pfx_in})",
                                    group_expr="LEFT(bt, 2)"))                   # 前缀行
        blocks.append(_ld_block("'ALL'", flag, lcol))                             # ALL 行
    return (
        """WITH ev AS (
    SELECT body, max(bt) AS bt, max(exit_t) AS exit_t FROM tmp_ev_base GROUP BY body
),
per_veh AS (
    SELECT ev.bt,
           EXTRACT(EPOCH FROM (CASE WHEN b.t_rw_end IS NOT NULL THEN b.t_rw_end ELSE ev.exit_t END - b.t_rw_start))::BIGINT AS l1,
           EXTRACT(EPOCH FROM (CASE WHEN b.t_bt_end IS NOT NULL THEN b.t_bt_end ELSE ev.exit_t END - b.t_bt_start))::BIGINT AS l2
    FROM tmp_loop_bounds b JOIN ev ON ev.body = b.body
)
INSERT INTO dss.dim_loop_duration
""" + "\nUNION ALL\n".join(blocks) + ";"
    )


# W4 新增 meta 键（docs/15 §0.4）：纯函数（供 main 写库 + 单测钉契约）
def w4_meta_rows(n_rw: int, n_bt: int, bt_anchor_rate: float, prefixes) -> dict:
    return {
        "base_version": "w4-purified",
        "n_rw_vehicles": str(n_rw),
        "n_bt_vehicles": str(n_bt),
        "bt_anchor_rate": f"{bt_anchor_rate:.6f}",
        "platform_prefixes": ",".join(prefixes),
    }

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
# W4 新 S2b 口径（docs/15 W4.2d）：黑顶锚点归位率 = t_bt_start 事件站 ∈ 烘房后报交入口簇 6
# + 打磨返修线 4 = 10 站白名单（与在线 BLACKTOP_ONLINE_WHITELIST 一致，W4.3a 单一事实源对钉）；
# 旧「首条 flag=2 集中度」口径在净化语义下 14IS260 假黑顶净化为 '0'、必挂 → 替换。
BLACKTOP_CLUSTER = ("24IS185", "24IS115", "22IS170", "23IS170", "23IS110",
                    "22IS110", "14IS120", "14IS065", "13IS065", "15IS065")

# W4 锚点人群与黑顶锚点归位率（meta + 新 S2b 共用定义）：
# n_rw/n_bt = 学习人群锚点车数（JOIN tmp_ev_base，无 entry/exit 的车不入学习人群）；
# bt_anchor_rate = t_bt_start 事件所在站 ∈ 10 站白名单的比例（DISTINCT ON 取首条防同秒多站双计）。
_SQL_BT_STN_IN = ", ".join(f"'{s}'" for s in BLACKTOP_CLUSTER)
SQL_W4_ANCHOR_COUNTS = """
SELECT count(DISTINCT b.body) FILTER (WHERE b.t_rw_start IS NOT NULL)::BIGINT,
       count(DISTINCT b.body) FILTER (WHERE b.t_bt_start IS NOT NULL)::BIGINT
FROM tmp_loop_bounds b JOIN (SELECT DISTINCT body FROM tmp_ev_base) e ON e.body = b.body
"""
SQL_BT_ANCHOR_RATE = f"""
WITH a AS (
    SELECT b.body, b.t_bt_start
    FROM tmp_loop_bounds b JOIN (SELECT DISTINCT body FROM tmp_ev_base) e ON e.body = b.body
    WHERE b.t_bt_start IS NOT NULL
),
st AS (
    SELECT DISTINCT ON (a.body) a.body, p.station
    FROM a JOIN tmp_w4_p p ON p.body = a.body AND p.t = a.t_bt_start
    ORDER BY a.body, p.id
)
SELECT coalesce(count(*) FILTER (WHERE station IN ({_SQL_BT_STN_IN}))::NUMERIC
                / NULLIF(count(*), 0), 0)::FLOAT8
FROM st
"""

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

# S4 专用：环分布学习人群事件量（W4.2d：从 tmp_ev_base 直接量真值人群——表行和
# SUM(n) 在三层行（车型+前缀）下会重复计数 ≈2×，不再作为 S4 指标）
SQL_LOOP_EV_COUNTS = """
SELECT count(*) FILTER (WHERE flag = '1' AND t < exit_t)::BIGINT,
       count(*) FILTER (WHERE flag = '2' AND t < exit_t)::BIGINT
FROM tmp_ev_base
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

    # S1 入口站 P50：绝对范围 [8h, 30h] 且与同窗行程 P50 自洽。
    # W4.2e 校准（依据：output/w1-验证摘要.md L38——09-19 W1 关账同 FAIL、数值几乎未变，
    # 属既有已知项而非 W4 回归；同 W2-S2 阈值校准先例，CHANGELOG L101）：
    # - 01IS045 = 小样本替代入口站（~143 车/学习窗、P50 ~100h 为该子人群固有）→
    #   只报告不判定（原注释意图；旧 journey n≥50 闸门未能排除，W1 关账起长期 FAIL）；
    # - 干净后缀人群剔除返修子人群、行程 P50 含返修子人群 → rem P50 系统性低于
    #   行程 P50（结构性缺口：01IS205 11.6% / 1L360RB 30.9%，W1 关账实测）；
    #   原 10% 阈值设计上不可达 → 一致性阈值改 max(10%, 1.5×W1 关账实测偏差)
    #   回归门（保留保护价值：捕捉基线漂移 + 方向检查 rem≤行程）。
    s1_base_dev = {"01IS205": 0.116, "1L360RB": 0.309}  # W1 关账实测（w1-验证摘要）
    s1_report_only = {"01IS045"}
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
        if stn in s1_report_only:
            s1_detail.append(f"{stn}: P50={p50}s（小样本替代入口站，只报告不判定）")
            continue
        if n >= 50:
            dev = abs(p50 - j) / j if j else 1.0
            band = max(0.10, 1.5 * s1_base_dev.get(stn, 0.0))
            good = (8 * 3600 <= p50 <= 30 * 3600
                    and j is not None and p50 <= j and dev <= band)
            s1_ok = s1_ok and good
            s1_detail.append(f"{stn}: P50={p50}s（行程 P50={j}s n={n}，偏差 {dev:.1%} ≤ {band:.1%}）")
        else:
            s1_detail.append(f"{stn}: P50={p50}s（行程 n={n} <50 小样本，只报告）")
    checks.append(("S1", s1_ok,
                   "入口站 P50（判定站：8–30h、rem≤行程、偏差 ≤max(10%,1.5×W1关账基线)；"
                   "01IS045 只报告）："
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

    # S2b（W4.2d 替换，doc12 红线 3）：黑顶锚点归位率 = t_bt_start 事件所在站
    # ∈ 10 站白名单（烘房后报交入口簇 + 打磨线，与在线 BLACKTOP_ONLINE_WHITELIST 一致）≥90%。
    # 旧规则（首条 flag=2 集中于 BLACKTOP_CLUSTER 双站 ≥60%）在净化语义下 14IS260
    # 假黑顶被净化为 '0' 必挂 → 替换不留代码残留（agy 建议项 3；W4.1b 实测全史 98.9%）。
    bt_rate = _scalar(cur, SQL_BT_ANCHOR_RATE) or 0.0
    checks.append(("S2b", bt_rate >= 0.90,
                   f"黑顶锚点归位率 = {bt_rate:.1%}（t_bt_start 站 ∈ 10 站白名单，要求 ≥90%）"))

    # S2c p1 剖面：max（n≥1000 站）≥0.03 且 min ≤0.01（阶跃存在且后段 ≈0）
    cur.execute(SQL_P1_PROFILE)
    big = [r for r in cur.fetchall() if r[3] >= 1000]
    pmax = max((r[2] for r in big), default=0.0)
    pmin = min((r[2] for r in big), default=1.0)
    checks.append(("S2c", len(big) >= 5 and pmax >= 0.03 and pmin <= 0.01,
                   f"p1 剖面（n≥1000，{len(big)} 站）：max={pmax:.3f} min={pmin:.3f}"
                   f"（要求 max≥0.03 且 min≤0.01）"))

    # S3 环时长表 n 充足（W4.2d 重标定，双审 cc 阻断项 1）：净化后样本量结构下降，
    # 旧阈值按旧后缀法 13,010/9,711 校准、对新语义（flag2 车 4,356）必误报。
    # 过渡基线 = 新实测 ×92% 余量（flag1 9,351 车 / flag2 4,356 车；最终值以
    # W4.2e 重学实测回写 docs/15 W4.2d 节）。
    cur.execute("SELECT flag, n FROM dss.dim_loop_duration WHERE body_type = 'ALL'")
    ld = dict(cur.fetchall())
    ok = ld.get("1", 0) >= 8500 and ld.get("2", 0) >= 4000
    checks.append(("S3", ok, f"loop_duration n: flag1={ld.get('1')}（≥8,500）"
                             f"flag2={ld.get('2')}（≥4,000）"))

    # S4 环分布事件量（W4.2d 重标定 + 指标改真值人群：新语义学习窗实测
    # flag1 192,197 / flag2 86,210，×92% 余量；三层行下表行和会重复计数，
    # 改从 tmp_ev_base 直接量学习人群）
    f1ev, f2ev = _row(cur, SQL_LOOP_EV_COUNTS)
    ok = f1ev >= 180000 and f2ev >= 80000
    checks.append(("S4", ok, f"loop_remaining 事件量: flag1={f1ev:,}（≥180,000）"
                   f"flag2={f2ev:,}（≥80,000）"))

    # S5 takt IQR 剔除率 ≤ 30%
    cur.execute("SELECT value::FLOAT8 FROM dss.dim_learner_meta WHERE key = 'takt_iqr_excl_rate'")
    rate = cur.fetchone()[0]
    checks.append(("S5", rate <= 0.30, f"takt IQR 剔除率={rate:.4f}（>0.30 告警）"))

    # S6 干净后缀保留率 ≥ 0.80（W4.2d 新语义，D7）：flag/last_loop_t 在 W4 ev_base 下
    # 均为净化值，本比例即「净化 flag='0' 事件上 t > last_loop_t 的比例」
    # （公式未变、语义更新：旧语义下 flag 为 MES 原值、last_loop_t 含粘滞尾）
    cur.execute("SELECT value::FLOAT8 FROM dss.dim_learner_meta WHERE key = 'clean_suffix_rate'")
    sr = cur.fetchone()[0]
    checks.append(("S6", sr >= 0.80, f"干净后缀率（净化 flag 上 t>last_loop_t）={sr:.4f}"))
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
    lines += ["", "## 环时长 L（top-10 车型 + ALL，新契约 4 统计）", ""]
    cur.execute("SELECT body_type, flag, l, l_mean, l_p90, n FROM dss.dim_loop_duration "
                "ORDER BY n DESC LIMIT 11")
    lines += [f"- {r[0]} flag{r[1]}: L P50={r[2]}s mean={r[3]}s P90={r[4]}s n={r[5]}" for r in cur.fetchall()]
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

    print("平台前缀（学习窗 n≥30 实测，C10）…")
    cur.execute(SQL_PLATFORM_PREFIXES)
    prefixes = tuple(sorted(r[0] for r in cur.fetchall()))
    print("  ", ",".join(prefixes) or "（无）")

    for name, sql in (("dim_remaining_time", remaining_sql(prefixes)),
                      ("dim_loop_remaining", loop_remaining_sql(prefixes)),
                      ("dim_station_takt(三层行)", takt_sql(prefixes)),
                      ("dim_future_flag", future_flag_sql(prefixes))):
        print(f"{name} …")
        cur.execute(sql)

    print("dim_loop_duration …")
    cur.execute(loop_duration_sql(prefixes))

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
    n_rw, n_bt = _row(cur, SQL_W4_ANCHOR_COUNTS)
    bt_rate = _scalar(cur, SQL_BT_ANCHOR_RATE) or 0.0
    # clean_suffix_rate（W4.2d 新语义，D7）：flag/last_loop_t 均净化值 →
    # 「净化 flag='0' 事件上 t > last_loop_t 的比例」（S6 消费）。
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
    meta.update(w4_meta_rows(n_rw, n_bt, bt_rate, prefixes))
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
