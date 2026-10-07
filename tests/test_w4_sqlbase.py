# -*- coding: utf-8 -*-
"""W4.1a TDD：sqlbase 统一纯净抽取 SQL 文本断言钉死（docs/15 §0.1 / W4.1a DoD）。

纯文本断言（无 DB）：钉死 Doc14 §4 + 勘误集的契约要点；
真库语义验证 = W4.1b scratch 八项（output/w4-W4.1b-*.md）。

契约要点（冻结，代码即契约）：
- 产品车 '78202%%'（psycopg2 转义）
- 137 / 139 双位 SUBSTRING 提取
- 烘房出口站 = 勘误后 3 站 {'16IS305','17IS305','18IS345'}，显式断言不含 '20IS010'
- 三线报交网关组 3 站；入口 3 站 / 出口 4 站
- C5 双窗截断：返工窗上界 LEAST(网关首次, 黑顶起点)（仅返修先车）
- 净化 flag ∈ {'0','1','2'} CASE（返工窗 '1' 优先、黑顶窗 '2'、其余 '0'）
- last_loop_t 表达式逐字不变（W1/W2 消费端零改动）
- tmp_loop_bounds 伴随表（§0.1 形状，仅锚点车）
- 两个 %s 占位 → 6 处生产调用点 / 5 文件（01_learn_takt/02_replay_eta/01_learn_v2×2/
  02_replay_shift/02_shadow_replay；双审 B2 勘误）字节级不变
"""
from spike_w1 import sqlbase

def _sql() -> str:
    return sqlbase.ev_base_sql("2026-04-14 00:00:00+08", "2026-08-15 23:59:59+08")

def _n() -> str:
    return " ".join(_sql().split())

def test_placeholder_count_2():
    assert _sql().count("%s") == 2

def test_product_car_filter():
    assert "'78202%%'" in _n()

def test_dual_bit_extract_137_139():
    assert 'SUBSTRING("MDS_DATA" FROM 137 FOR 1)' in _n()
    assert 'SUBSTRING("MDS_DATA" FROM 139 FOR 1)' in _n()

def test_oven_out_3_stations_errata():
    for s in ("16IS305", "17IS305", "18IS345"):
        assert s in _n(), s
    assert "20IS010" not in _n()  # agy 阻断项 1：显式断言不含

def test_gateway_3_stations():
    for s in ("25IS010", "25IS110", "25IS210"):
        assert s in _n(), s

def test_entry_exit_stations():
    for s in ("01IS205", "1L360RB", "01IS045", "K3IS140", "1J440RB", "K2IS075", "K1IS135"):
        assert s in _n(), s

def test_c5_truncation():
    assert ("LEAST(MIN(p.t) FILTER (WHERE s.t_rw_start IS NOT NULL "
            "AND p.t > s.t_rw_start), s.t_bt_start)") in _n()

def test_last_loop_t_expr_unchanged():
    assert ("max(t) FILTER (WHERE flag IN ('1', '2')) "
            "OVER (PARTITION BY body) AS last_loop_t") in _n()

def test_two_contract_tables_dropped_created():
    assert "DROP TABLE IF EXISTS tmp_loop_bounds;" in _n()
    assert "CREATE TEMP TABLE tmp_loop_bounds AS" in _n()
    assert "DROP TABLE IF EXISTS tmp_ev_base;" in _n()
    assert "CREATE TEMP TABLE tmp_ev_base AS" in _n()

def test_loop_bounds_only_anchored_cars():
    assert "WHERE s.t_rw_start IS NOT NULL OR s.t_bt_start IS NOT NULL" in _n()

def test_purified_flag_case():
    assert ("WHEN b.t_rw_start IS NOT NULL AND p.t >= b.t_rw_start "
            "AND (b.t_rw_end IS NULL OR p.t <= b.t_rw_end) THEN '1'") in _n()
    assert ("WHEN b.t_bt_start IS NOT NULL AND p.t >= b.t_bt_start "
            "AND (b.t_bt_end IS NULL OR p.t <= b.t_bt_end) THEN '2'") in _n()
    assert "ELSE '0' END AS flag" in _n()

def test_dedup_lag_unchanged():
    assert "LAG(station) OVER (PARTITION BY body ORDER BY t, id) AS prev_station" in _n()
    assert "WHERE prev_station IS NULL OR station <> prev_station" in _n()

def test_ev_base_shape_unchanged():
    assert "SELECT id, body, bt, station, t, flag, entry_t, exit_t," in _n()
