# -*- coding: utf-8 -*-
"""共享 SQL：事件底座（学习器与回放唯一共用口径，DRY）。W4.1 统一纯净抽取（Doc14 §4 + 勘误）。

构建流水线（单 execute 调用，多语句；TEMP 表会话级存活，autocommit 下逐语句各自提交）：

1. ``tmp_w4_p``（内部中间表）：产品车 78202% + 学习窗内全事件，一次扫库落 TEMP，
   提取 137 位（flag_137）/ 139 位（raw_flag）双标志；供后续 CTE 复用（避免 3 次全表扫）。
2. ``tmp_loop_bounds``（契约伴随表，docs/15 §0.1）：每车双环边界
   - t_rw_start = 首个 139=1（返工起点）
   - t_bt_start = 首个 137=1 ∧ 139=2 ∧ t ≥ first_oven（黑顶起点；出炉硬约束，
     3 站勘误集合 {'16IS305','17IS305','18IS345'}，不含 20IS010=2 线储存线入口）
   - t_rw_end / t_bt_end = 打标后首次过三线报交网关组（25IS010/110/210）时刻；
     NULL = 无网关（旁路/在制车）
   - C5 双窗截断：返修先车（t_bt_start > t_rw_start）返工窗上界 = LEAST(网关首次, t_bt_start)
   - 仅锚点车入表（rw_start / bt_start 之一非 NULL）
3. ``tmp_ev_base``（契约主表，形状不变，flag 净化语义变更）：
   - 产品车 → 窗内 entry/exit 首次 → 行程内事件 → 连续同站去重（保首条，LAG 去重不变）
   - flag 净化 ∈ {'0','1','2'}：纯净返工窗 [rw_start, rw_end] 内 → '1'（优先）；
     纯净黑顶窗 [bt_start, bt_end] 内 → '2'；其余（出网关后 / 烘房前假信号 /
     '-'、'3' 无效值）→ '0'
   - last_loop_t 表达式逐字不变：max(t) FILTER (flag IN ('1','2')) OVER (PARTITION BY body)
     （W1/W2 消费端零改动；干净后缀排除语义保留）

签名：ev_base_sql(win_start, win_end) -> str —— 返回多语句 SQL 文本（两个 %s 占位：
start, end），**6 处生产调用点 / 5 文件**（01_learn_takt:498 / 02_replay_eta:97 /
01_learn_v2:303,318 / 02_replay_shift:155 / 02_shadow_replay:191；双审 B2 勘误，原「3 处」系 grep 截断）
``cur.execute(sqlbase.ev_base_sql(a, b), (a, b))`` 字节级不变。
"""
try:
    from . import common
except ImportError:          # 脚本直跑（python spike_w1\01_learn_takt.py）
    import common

_EV_BASE_SQL = """
DROP TABLE IF EXISTS tmp_w4_p;
CREATE TEMP TABLE tmp_w4_p AS
SELECT "ID" AS id, "BODY_ID" AS body, "BODY_TYPE" AS bt, "RW_STATION_ID" AS station,
       "DATE_EVT" AS t,
       SUBSTRING("MDS_DATA" FROM 137 FOR 1) AS flag_137,
       SUBSTRING("MDS_DATA" FROM 139 FOR 1) AS raw_flag
FROM ods.carbody_history
WHERE "BODY_ID" LIKE '78202%%'
  AND "DATE_EVT" >= %s AND "DATE_EVT" <= %s;

CREATE INDEX tmp_w4_p_body_t ON tmp_w4_p (body, t);
CREATE INDEX tmp_w4_p_station ON tmp_w4_p (station);

DROP TABLE IF EXISTS tmp_loop_bounds;
CREATE TEMP TABLE tmp_loop_bounds AS
WITH first_oven AS (
    SELECT body, MIN(t) AS t_oven_out
    FROM tmp_w4_p
    WHERE station IN ('16IS305', '17IS305', '18IS345')
    GROUP BY body
),
loop_starts AS (
    SELECT p.body,
           MIN(p.bt) AS bt,
           MIN(p.t) FILTER (WHERE p.raw_flag = '1') AS t_rw_start,
           MIN(p.t) FILTER (WHERE p.flag_137 = '1' AND p.raw_flag = '2'
                             AND o.t_oven_out IS NOT NULL AND p.t >= o.t_oven_out) AS t_bt_start
    FROM tmp_w4_p p
    LEFT JOIN first_oven o ON p.body = o.body
    GROUP BY p.body
),
bounds AS (
    SELECT s.body, s.bt, s.t_rw_start, s.t_bt_start,
           CASE WHEN s.t_bt_start IS NOT NULL AND s.t_bt_start > s.t_rw_start
                THEN LEAST(MIN(p.t) FILTER (WHERE s.t_rw_start IS NOT NULL
                                              AND p.t > s.t_rw_start), s.t_bt_start)
                ELSE MIN(p.t) FILTER (WHERE s.t_rw_start IS NOT NULL
                                       AND p.t > s.t_rw_start) END AS t_rw_end,
           MIN(p.t) FILTER (WHERE s.t_bt_start IS NOT NULL
                             AND p.t > s.t_bt_start) AS t_bt_end
    FROM loop_starts s
    LEFT JOIN tmp_w4_p p ON s.body = p.body
                        AND p.station IN ('25IS010', '25IS110', '25IS210')
    WHERE s.t_rw_start IS NOT NULL OR s.t_bt_start IS NOT NULL
    GROUP BY s.body, s.bt, s.t_rw_start, s.t_bt_start
)
SELECT body, bt, t_rw_start, t_rw_end, t_bt_start, t_bt_end
FROM bounds;

DROP TABLE IF EXISTS tmp_ev_base;
CREATE TEMP TABLE tmp_ev_base AS
WITH entry AS (
    SELECT body, MIN(t) AS entry_t
    FROM tmp_w4_p
    WHERE station IN ('01IS205', '1L360RB', '01IS045')
    GROUP BY body
),
x AS (
    SELECT body, MIN(t) AS exit_t
    FROM tmp_w4_p
    WHERE station IN ('K3IS140', '1J440RB', 'K2IS075', 'K1IS135')
    GROUP BY body
),
purified AS (
    SELECT p.id, p.body, p.bt, p.station, p.t, e.entry_t, x.exit_t,
           CASE
               WHEN b.t_rw_start IS NOT NULL AND p.t >= b.t_rw_start
                    AND (b.t_rw_end IS NULL OR p.t <= b.t_rw_end) THEN '1'
               WHEN b.t_bt_start IS NOT NULL AND p.t >= b.t_bt_start
                    AND (b.t_bt_end IS NULL OR p.t <= b.t_bt_end) THEN '2'
               ELSE '0'
           END AS flag
    FROM tmp_w4_p p
    JOIN entry e ON p.body = e.body
    JOIN x ON p.body = x.body
    LEFT JOIN tmp_loop_bounds b ON p.body = b.body
    WHERE x.exit_t > e.entry_t
      AND p.t >= e.entry_t AND p.t <= x.exit_t
),
lagged AS (
    SELECT s.*, LAG(station) OVER (PARTITION BY body ORDER BY t, id) AS prev_station
    FROM purified s
)
SELECT id, body, bt, station, t, flag, entry_t, exit_t,
       max(t) FILTER (WHERE flag IN ('1', '2')) OVER (PARTITION BY body) AS last_loop_t
FROM lagged
WHERE prev_station IS NULL OR station <> prev_station;
"""


def ev_base_sql(win_start, win_end) -> str:
    """返回统一纯净抽取 SQL 文本（两个 %s 占位，按序传 start, end）。

    单 execute 调用构建 3 张 TEMP 表（tmp_w4_p 内部中间表 / tmp_loop_bounds 伴随表 /
    tmp_ev_base 主表）；TEMP 表会话级存活，后续学习/回放查询须在同一连接上执行。
    """
    return _EV_BASE_SQL
