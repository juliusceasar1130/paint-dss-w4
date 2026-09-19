# -*- coding: utf-8 -*-
"""共享 SQL：tmp_ev_base 临时表（学习器与回放唯一共用口径，DRY）。

口径：产品车 78202% → 窗口内 entry/exit 事件各取第一次 → 行程内事件（含 entry/exit）
→ 连续同站去重（保首条，全库仅 0.1%；真实相邻站快过站 18.2% 不受影响，禁止时间窗合并）
→ 解析 MDS[139] 标志 → 每车 last_loop_t。
"""
try:
    from . import common
except ImportError:          # 脚本直跑（python spike_w1\01_learn_takt.py）
    import common

_EV_BASE_SQL = """
DROP TABLE IF EXISTS tmp_ev_base;
CREATE TEMP TABLE tmp_ev_base AS
WITH p AS (
    SELECT "ID" AS id, "BODY_ID" AS body, "BODY_TYPE" AS bt, "RW_STATION_ID" AS station,
           "DATE_EVT" AS t,
           SUBSTRING("MDS_DATA" FROM 139 FOR 1) AS flag
    FROM ods.carbody_history
    WHERE "BODY_ID" LIKE '78202%%'
      AND "DATE_EVT" >= %s AND "DATE_EVT" <= %s
),
entry AS (
    SELECT body, min(t) AS entry_t
    FROM p WHERE station IN ('01IS205', '1L360RB', '01IS045')
    GROUP BY body
),
x AS (
    SELECT body, min(t) AS exit_t
    FROM p WHERE station IN ('K3IS140', '1J440RB', 'K2IS075', 'K1IS135')
    GROUP BY body
),
span AS (
    SELECT p.id, p.body, p.bt, p.station, p.t, p.flag, e.entry_t, x.exit_t
    FROM p
    JOIN entry e ON p.body = e.body
    JOIN x     ON p.body = x.body
    WHERE x.exit_t > e.entry_t
      AND p.t >= e.entry_t AND p.t <= x.exit_t
),
lagged AS (
    SELECT s.*, LAG(station) OVER (PARTITION BY body ORDER BY t, id) AS prev_station
    FROM span s
)
SELECT id, body, bt, station, t, flag, entry_t, exit_t,
       max(t) FILTER (WHERE flag IN ('1', '2')) OVER (PARTITION BY body) AS last_loop_t
FROM lagged
WHERE prev_station IS NULL OR station <> prev_station;
"""


def ev_base_sql(win_start, win_end) -> str:
    """返回 (SQL, params) 无关的 SQL 文本（两个 %s 占位，按序传 start, end）。"""
    return _EV_BASE_SQL
