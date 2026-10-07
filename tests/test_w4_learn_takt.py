# -*- coding: utf-8 -*-
"""W4.2a TDD：dim_loop_duration 扩列 + 前缀行契约（docs/15 §0.4 / W4.2a DoD）。

纯文本断言 + FakeCursor（无 DB）：钉死「逐车 L 来自 tmp_loop_bounds」新契约；
真库重学验证 = W4.2e（output/w4-learn-sanity.md）。

契约要点（冻结，代码即契约）：
- DDL `(body_type, flag, l, l_mean, l_p90, n)`；l = P50（兼容名，消费端 row[0]/row[-1] 零改动）
- L = 纯净窗时长：rw 行 = t_rw_end − t_rw_start；bt 行 = t_bt_end − t_bt_start
- end 为 NULL → 取 exit_t（D4 无网关在制回退）；exit_t 在 tmp_ev_base（JOIN 取，
  tmp_loop_bounds 六列无此列——双审 agy 建议 1）；exit_t NULL 且无 end 的车自然排除
- 三层行：车型行 + 平台前缀行（LEFT(bt,2) + IN 列表）+ 'ALL' 行
- 前缀清单 = 学习窗 n≥30（车数）实测生成、不硬编码（C10）；meta `platform_prefixes`
- 旧后缀法语义（max(t) − min(t) flag 窗）移除
"""
from __future__ import annotations

import importlib
from pathlib import Path

lt = importlib.import_module("spike_w1.01_learn_takt")
rs = importlib.import_module("spike_w2.02_replay_shift")


# --- DDL 契约 ---

def test_ddl_loop_duration_6col():
    ddl = lt.DDL
    seg = ddl[ddl.index("CREATE TABLE dss.dim_loop_duration"):ddl.index("CREATE TABLE dss.dim_learner_meta")]
    for col in ("l BIGINT", "l_mean BIGINT", "l_p90 BIGINT", "n INT"):
        assert col in seg, f"DDL 缺列 {col}"
    assert "PRIMARY KEY (body_type, flag)" in seg


# --- SQL_LOOP_DURATION 新语义契约 ---

def _sql():
    return lt.loop_duration_sql(("2N", "DM"))


def test_sql_uses_loop_bounds_not_suffix():
    sql = _sql()
    assert "tmp_loop_bounds" in sql
    assert "max(t) - min(t)" not in sql, "旧后缀法 L 语义残留"


def test_sql_exit_t_join_ev_base():
    sql = _sql()
    assert "tmp_ev_base" in sql and "exit_t" in sql, "exit_t 须 JOIN tmp_ev_base 取"
    for col in ("t_rw_end", "t_bt_end"):
        assert f"{col} IS NOT NULL THEN" in sql, f"D4 回退缺 {col} → exit_t"
    assert sql.count("EXTRACT(EPOCH FROM") == 2, "L 须 EXTRACT(EPOCH) 秒值（interval 不可直接 round）"


def test_sql_four_stats():
    sql = _sql()
    for frag in ("percentile_cont(0.5)", "avg(", "percentile_cont(0.9)", "count("):
        assert frag in sql, f"四统计缺 {frag}"
    assert "'ALL'" in sql


def test_sql_prefix_rows():
    sql = _sql()
    assert "LEFT(bt, 2)" in sql
    assert "'2N'" in sql and "'DM'" in sql
    # 空清单 → 不前缀块（无 IN 列表）
    assert "IN (" not in lt.loop_duration_sql(())


def test_prefix_in_list_builder():
    assert lt.prefix_in_list(()) is None
    assert lt.prefix_in_list(("2N", "DM")) == "'2N', 'DM'"


def test_prefix_in_list_rejects_bad():
    import pytest
    for bad in (("2N5",), ("2n"), ("2N--"), ("O'Brien",)):
        with pytest.raises(ValueError):
            lt.prefix_in_list(bad)


def test_sql_platform_prefixes_contract():
    sql = lt.SQL_PLATFORM_PREFIXES
    assert "LEFT(bt, 2)" in sql
    assert "count(DISTINCT body)" in sql.lower().replace("count(distinct body)", "count(DISTINCT body)") or "count(distinct body)" in sql.lower()
    assert ">= 30" in sql


# --- W4.2b：其余四张 W1 表前缀行生成模式（车型 + 前缀 + ALL 三层） ---

def _check_prefix_trilayer(sql: str, pfx_marker: str = "LEFT(bt, 2)"):
    assert pfx_marker in sql
    assert "'2N'" in sql and "'DM'" in sql
    assert "'ALL'" in sql


def test_remaining_sql_prefix_trilayer():
    _check_prefix_trilayer(lt.remaining_sql(("2N", "DM")))
    assert "LEFT(bt, 2)" not in lt.remaining_sql(())


def test_loop_remaining_sql_prefix_trilayer():
    _check_prefix_trilayer(lt.loop_remaining_sql(("2N", "DM")))
    assert "LEFT(bt, 2)" not in lt.loop_remaining_sql(())


def test_takt_sql_prefix_trilayer():
    s = lt.takt_sql(("2N", "DM"))
    _check_prefix_trilayer(s)
    # IQR 剔除保留（前缀层按前缀算 IQR，非按车型）：三层各一处
    assert s.count("q.q3 + 3.0 * (q.q3 - q.q1)") >= 3
    assert "LEFT(bt, 2)" not in lt.takt_sql(())


def test_future_flag_sql_prefix_trilayer():
    # W4.2b 重构：join 物化进 j CTE，前缀分支无分支内 join（planner NL 坑），
    # 前缀过滤在 j 上：LEFT(bt, 2)（无 e. 前缀）
    _check_prefix_trilayer(lt.future_flag_sql(("2N", "DM")))
    assert "LEFT(bt, 2)" not in lt.future_flag_sql(())
    # join 只发生在 j CTE 内一次（分支内无 JOIN）
    s = lt.future_flag_sql(("2N", "DM"))
    assert s.count("JOIN ff f ON f.body = e.body") == 1


# --- W4.2d：sanity 新语义（S2b 替换 / S3 S4 重标定 / S6 净化语义） ---

def _sanity_src() -> str:
    src = Path(lt.__file__).read_text(encoding="utf-8")
    i = src.index("def sanity_checks")
    j = src.index("def write_sanity_report", i)
    return src[i:j]


def test_s2b_replaced_by_anchor_rate():
    s = _sanity_src()
    # 新 S2b = 黑顶锚点归位率 ≥90%（复用 SQL_BT_ANCHOR_RATE，doc12 红线 3）
    assert "SQL_BT_ANCHOR_RATE" in s
    assert ">= 0.90" in s
    # 旧规则不留代码残留：集中度判定（in2/tot2 ≥0.60）已删除
    assert "SQL_FIRST_FLAG2" not in s
    assert "in2 / tot2" not in s


def test_s3_s4_recalibrated_thresholds():
    s = _sanity_src()
    # 过渡基线 = 新实测 ×92%（新实测 flag1 9,351 / flag2 4,356 / 事件 192,197 / 86,210）
    assert ">= 8500" in s and ">= 4000" in s
    assert ">= 180000" in s and ">= 80000" in s
    # 旧阈值（旧后缀法样本量校准）不复存在
    assert ">= 9000" not in s and ">= 7000" not in s
    assert ">= 250000" not in s and ">= 100000" not in s
    # S4 指标改真值人群（tmp_ev_base 直接量；三层行下表行和会重复计数）
    assert "SQL_LOOP_EV_COUNTS" in s
    assert "SUM(n) FROM dss.dim_loop_remaining" not in s


def test_s6_purified_semantics_label():
    s = _sanity_src()
    # 公式不变（flag/last_loop_t 在 W4 ev_base 下已为净化值），文案更新为新语义
    assert "净化" in s and ">= 0.80" in s


def test_s1_calibrated_to_w1_closeout():
    s = _sanity_src()
    # W4.2e 校准：01IS045 只报告不判定（原注释意图；W1 关账起长期 FAIL 的根源）；
    # 一致性阈值 = max(10%, 1.5×W1 关账实测偏差) 回归门（w1-验证摘要 L38 依据）
    assert "s1_report_only" in s and "01IS045" in s
    assert "s1_base_dev" in s
    assert "p50 <= j" in s  # 方向检查（干净人群 rem ≤ 全人群行程）


# --- W4 meta 新键（纯函数） ---

def test_w4_meta_rows():
    rows = dict(lt.w4_meta_rows(n_rw=8000, n_bt=4300, bt_anchor_rate=0.989,
                                prefixes=("2N", "DM")))
    assert rows["base_version"] == "w4-purified"
    assert rows["n_rw_vehicles"] == "8000"
    assert rows["n_bt_vehicles"] == "4300"
    assert rows["bt_anchor_rate"] == "0.989000"
    assert rows["platform_prefixes"] == "2N,DM"


def test_w4_meta_rows_no_prefixes():
    rows = dict(lt.w4_meta_rows(n_rw=1, n_bt=2, bt_anchor_rate=0.5, prefixes=()))
    assert rows["platform_prefixes"] == ""


# --- load_tables 行形状 2→4（02_replay_shift 唯一改点；02_replay_eta 零改动） ---

class _FakeCursor:
    def __init__(self, mapping):
        self._m = mapping
        self._last = ""

    def execute(self, sql):
        self._last = sql

    def fetchall(self):
        return self._m.get(self._last, [])


class _FakeConn:
    def __init__(self, mapping):
        self._cur = _FakeCursor(mapping)

    def cursor(self):
        return self._cur


def test_load_tables_loop_dur_4tuple():
    mapping = {
        "SELECT body_type, flag, l, l_mean, l_p90, n FROM dss.dim_loop_duration":
            [("m", "1", 100, 200, 300, 40), ("ALL", "1", 110, 210, 310, 500)],
    }
    tables = rs.load_tables(_FakeConn(mapping))
    assert tables["loop_dur"][("1", "m")] == (100, 200, 300, 40)
    assert tables["loop_dur"][("1", "ALL")] == (110, 210, 310, 500)
