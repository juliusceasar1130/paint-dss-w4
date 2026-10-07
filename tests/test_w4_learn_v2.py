# -*- coding: utf-8 -*-
"""W4.2c 契约测试：dim_expected_delay（doc10 站级双列净延误表）学习器 + 覆盖率缺口。

口径源：docs/10-返工补偿机制问题诊断与期望净延误重构方案.md §2（v2.2.1）+
docs/15-W4双环重构步级开发计划.md W4.2c/C14。
纯契约测试（无 DB）：SQL 形状断言 + 纯函数（coverage_gaps）+ DDL。
"""
from __future__ import annotations

import importlib
from pathlib import Path

lv2 = importlib.import_module("spike_w2.01_learn_v2")


# --- DDL：6 列 + PK ---

def test_ddl_expected_delay_6col():
    ddl = lv2.DDL
    i = ddl.index("CREATE TABLE dss.dim_expected_delay")
    j = ddl.index(";", i)
    block = ddl[i:j]
    for col in ("body_type", "station", "delay_mean_s", "delay_p50_s",
                "sample_n", "sample_n_clean"):
        assert col in block, col
    assert "PRIMARY KEY (body_type, station)" in block
    assert "DROP TABLE IF EXISTS dss.dim_expected_delay;" in ddl


# --- SQL 契约（doc10 §2.1 公式逐项落形） ---

def test_expected_delay_sql_contract():
    s = lv2.SQL_EXPECTED_DELAY
    # 基线同源：引擎层级 车型→前缀→ALL 三处 LEFT JOIN W1 rem 表（n>=30 与引擎层级回退同门）
    assert s.count("LEFT JOIN dss.dim_remaining_time") == 3
    assert "LEFT(ev.bt, 2)" in s and "rm.n >= 30" in s and "ra.body_type = 'ALL'" in s
    # P(fut) = v2_future_flag ⑥a 全窗 p1（与 diff 同窗，禁滚动 8 周混用）
    assert "FROM dss.v2_future_flag WHERE week = 'ALL'" in s
    # fut=1 定义：将来重返修（首个 f1 在本事件之后）；人群 = ⑥a 前环（与 P(fut) 同源同窗）
    assert "f.f1 IS NOT NULL AND f.f1 > e.t" in s
    assert "f.f1 IS NULL OR e.t < f.f1" in s and "f.f2 IS NULL OR e.t < f.f2" in s
    # 不能用干净后缀人群（与 fut=1 结构性不相交，实测 fut=1 恒 0）
    assert "last_loop_t" not in s
    # 双人群 N≥30 门槛（任一不足不写行 → 引擎降级）
    assert "a.n_fut >= 30 AND a.n_clean >= 30" in s
    # 外层截断 max(0,·) 恰好两列；内部样本不截断（doc10：个体截断引入正偏）
    assert s.count("GREATEST(0,") == 2
    # 基线修正项 mean(rem_clean)−p50(rem_clean) 只进 mean 列：恰好出现一次
    assert s.count("a.mean_clean - a.p50_clean") == 1
    # p50 列 = P(fut)×p50_diff（无修正项）：带 a. 前缀恰好一次
    assert s.count("a.p50_diff") == 1
    # 三层行：车型 + 前缀（LEFT(bt,2)，全前缀不设白名单——小前缀被双门槛自然淘汰）+ ALL
    assert "LEFT(bt, 2)" in s and "'ALL'" in s


# --- 覆盖率缺口纯函数（doc10 §2.3：末级兜底 0 须配监控） ---

def test_coverage_gaps():
    delay_rows = {("YM33J-", "22IS340"), ("ALL", "22IS340"), ("2N", "15IS065")}
    cells = [("YM33J-", "22IS340", 0.54),    # 车型行在 → 不缺
             ("YM34K-", "22IS340", 0.51),    # ALL 行在 → 不缺
             ("2N", "20IS010", 0.80),        # 该站三级全无 → 缺
             ("IE14.-", "21IS565", 0.09),    # p1<0.1 → 不告警
             ("DM131-", "20IS010", 0.30)]    # 缺
    gaps = lv2.coverage_gaps(cells, delay_rows)
    assert {g[:2] for g in gaps} == {("2N", "20IS010"), ("DM131-", "20IS010")}
    # 按 p1 降序
    assert gaps[0][2] >= gaps[1][2]


def test_coverage_gaps_empty_prefix_chain():
    # 前缀行兜底：('2N', st) 在 delay_rows 时，2N 系车型不缺
    gaps = lv2.coverage_gaps([("2N54Y-", "22IS340", 0.8)], {("2N", "22IS340")})
    assert gaps == []


def test_coverage_gaps_empty_cells():
    assert lv2.coverage_gaps([], {("ALL", "X")}) == []


# --- sanity W2-S5 + 报告节 + 启动表清单（源码级契约） ---

def test_sanity_and_report_contract():
    src = Path(lv2.__file__).read_text(encoding="utf-8")
    assert "W2-S5" in src                      # sanity 结构性自校验
    assert "dim_expected_delay" in src
    assert "coverage_gaps(" in src and "覆盖率" in src  # 报告节
    # main() 在全窗 ev_base 之后、周循环之前执行（diff 与 ⑥a 全窗同源，禁周窗）
    i_full = src.index("ev_base 全窗")
    i_ed = src.index("cur.execute(SQL_EXPECTED_DELAY)")
    i_week = src.index("for w in weeks:")
    assert i_full < i_ed < i_week


def test_boot_tables_include_expected_delay():
    boot = importlib.import_module("spike_w3.01_boot_online")
    assert "dim_expected_delay" in boot.DSS_DIM_TABLES
