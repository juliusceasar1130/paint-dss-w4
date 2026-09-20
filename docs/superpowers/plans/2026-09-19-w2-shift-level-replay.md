# W2 班次级回放（第 5 小时预测 ±10% 门判）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 对回放窗（2026-08-16 → 2026-09-19）每个班次，在其第 5 小时（信息集严格截止该时刻）预测本班下线量，与实际下线量比，逐班 |误差%| 中位数 ≤ 10% 即过门（D1'：全厂总闸 + 主车型分门，2026-09-20 拍板）。

**Architecture:** 复用 W1 学习表（主分布/环分布/takt/环时长，学习窗 04-14→08-15 不重算）作基线；`spike_w2` 新增四样东西——① 08/09 线别 cohort 混合（周度线概率加权，无后见泄漏）、② 周度滚动 8 周 p 表（未来标志概率，防逐月返工率双峰偏差）、⑤ 站级质量标记表、⑥ 补偿净化（前环人群 + 混合低 n 站免补偿）。回放引擎按班次日历（类型自学）逐班逐锚点（3/4/5/6h）聚合「已下线 + Σ在制车 ETA≤窗末」，与 4 下线点实际数对比。

**Tech Stack:** Python 3.13（conda env `paint-dss`），psycopg2（analytics_db @ 127.0.0.1，只读 ods / 可写 dss），pandas + numpy，pytest。

**Spec:** Basic Memory 笔记 `basic-memory/projects/paint-dss/涂装车间-dss-方案设计与开发路线`（W2 六项必做 / 班次口径 2026-09-19 拍板+细化 / W1 门判终局与已知偏差）；`docs/01-开发阶段计划.md`（W2 节）；`docs/superpowers/plans/2026-09-19-w1-takt-learner-eta-replay.md`（W1 先例与代码基线）。

## 修订记录（2026-09-20 复审 + D1' 拍板）

审核报告 `output/w2-计划审核报告_20260920.md`（13 条：5 阻断 / 3 中 / 5 低）经独立复核全部属实，逐条修订：
- **B1** 测试 `detect_mid_type(500)` 断言 "10h" 与阈值 510 矛盾（500<510 → "8h"）→ 改边界测试 510/509（实现不动）
- **B2** `week_learning_range` 测试断言 2026-07-06（实为 08-10 往前 5 周）→ 2026-06-15（8 周）
- **B3** cohort 回退测试期望 150.0 算术错 → 250.0（0.25×100 + 0.75×300）
- **B4** `SQL_LINE_PROB` 缺 `("ALL","ALL")` 调用（write_report 取 week='ALL' 行 → NULL → `{:.3f}` 崩溃）→ 全窗块补一行
- **B5** 列名 `nocomp_err_pct` 与门判读取的 `direct_nocomp_err_pct` 不一致（KeyError）→ 统一
- **M1** ⑥a 净化条件 `last_loop_t`（最后一环）与注释「前环人群」不符（多环车环间事件误入分母且分子判错）→ 改 `f1/f2` 首环前过滤
- **M2** ③④「三级回退 + 报告分桶」未实现 → Task 4 报告新增「车型 @5h 分桶」+「入口站分桶」（spans SQL 加 entry_st 列）
- **M3→D1'** docs/01「（车型汇总）/ 返工车（15.6%）成组显示」未落地 → **用户拍板 D1' 选 3**：门 = 全厂中位数 ≤10% ∧ 主车型（逐班实际量中位数 ≥30）各自中位数 ≤10%；返工车组 + 稀有车型报告分桶（Task 4 代码/gate JSON/报告新增）
- **L1** 类型自学全天前视 → 加「回放专用」注记；**L2** `fut_all` 行 min_n 1→30（与 W1 resolve 一致，docstring 同步）；**L3** `idx` 越界跳过语义注释澄清（缺退出车计入 no_eta，对门判影响 ≈0）；**L4** ev 窗右端 +1d 用途注明（覆盖末班窗末 09-20 04:00 下线）
- **L5** File Structure 补 README.md 行 / Task 2 commit message 15→16 例 / Task 4 Step 3 命令删死代码 glob / 「上线」错别字 →「下线」×2

## 待拍板项（2026-09-20 全部拍板完毕，下表为最终口径）

| # | 决策 | 推荐 | 理由 / 备选 |
|---|---|---|---|
| D1'（2026-09-20 拍板，原 D1 修订） | 门判统计量与统计单元 | **锚点 = 第 5 小时**；门 = **全厂逐班 \|误差%\| 中位数 ≤ 10% 且 所有主车型各自逐班 \|误差%\| 中位数 ≤ 10%**；主车型 = 该车型在全部进门判班次中**逐班实际量中位数 ≥ 30**；逐班实际量 = 0 的班次不参与该车型中位数（C3 推广到车型级）；稀有车型 + 返工车组仅报告分桶（兑现 docs/01「车型维度汇总 / 返工车成组显示」）。报告同时给均值 / P90 / 符号中位、锚点 3/4/6h 曲线 | 2026-09-20 用户拍板选 3（全厂总闸 + 主车型分门）：docs/01 原文「（车型汇总）」要求车型维度，但稀有车型逐班实际仅几台、按车型全门统计失真；主车型门槛 30 保证逐班误差% 统计有效。全厂侧备选（均值≤10%）保留 |
| D2 | 生产估计器 | **直接剩余分布**（P50 + flag 切换 + v2 补偿 + cohort 混合）作门判对象；Σ段中位 / Σ段均值 仅诊断（后见路径，生产不可得） | 与 W1 定位一致（Σ 系后见路径诊断器）；班次级下三估计器全量输出，曲线交用户看 |
| D3 | 学习窗策略 | 主分布/环/takt/环时长 = **复用 W1 表**（04-14→08-15，零重算、回归零风险）；**p 表与线概率 = 每回放周滚动 8 周**（学习窗 = 该周之前 8 个 ISO 周，严格无未来泄漏，模拟生产 60 天滚动重学） | 固定窗对回放周（8 月中–9 月中）无周度 p 可用（全窗 p 正是 W1 的偏差源）；滚动窗是生产语义且因果干净。备选：全窗 p（=W1 原样，② 不生效） |
| D4 | 六项必做的落地形态 | ① = cohort 表 + 周度线概率 + 加权混合（非后见：线上分派概率用过去 8 周学）；② = 滚动周度 p（n≥50 用周度，否则回退全窗）；③④ = 稀有长行程车型/01IS045 小样本站 = 三级回退 + 报告（不专门建模）；⑤ = 质量标记表（ok/low_n/mixed/both，报告分组显示）；⑥ = p 学习只用前环人群（flag=0 且该车该事件前无 flag 1/2）+ 混合低 n 站（p1≥0.5 且车型行 n<30）免补偿（21IS565 类） | 全部六项做，但 ③④ 只做"回退+报告"不新增模型（YAGNI）；⑤⑥ 规则具体可测。11IS A/B 并行线仅做报告检查（rel_diff），不改表 |
| C1 | 日班归属窗 | **[04:45, 16:30] 统一**（把「04:45–正式开班下线计日班」推广：8h 型日 04:45–08:00 也计日班）；中班 = [16:30, 窗末]；(中班窗末, 04:45) 无归属（低谷，报告覆盖率） | 与 2026-09-19 班次窗细化（窗 = 开班+工时+30min 尾）一致。备选：日班窗 = [正式开班, 16:30]（8h 型日会丢 04:45–08:00 下线） |
| C2 | 不完整班次 | 只统计**完整班次**（归属窗 ⊆ 回放窗）；08-15 中班（归属回放前一日）日循环不构造（首中班 = 08-16 中班，完整）；09-19 中班（跨窗末）排除并列表；类型 None（停产/数据截断）的班次直接缺席 | 样本外边界干净；实际量无法完整数 |
| C3 | 零产班次 | 实际下线 = 0 的班次（停产日）排除出门判，列表报告 | 无真值可比；低产但非零的班次保留（±10% 按实际量算） |

## Global Constraints

- **执行环境**：`D:\000_software_install\miniconda3\envs\paint-dss\python.exe`（3.13）；bash = git-bash。每个脚本 `main()` 第一行 `sys.stdout.reconfigure(encoding="utf-8", errors="replace")`（Windows GBK 控制台）。
- **psycopg2 规则**：内联 LIKE 通配符必须 `%%`（2026-09-19 实测：双通配在 LIKE 里语义 ≡ 单通配，因此带不带参数序列传 `%%` 都正确——无参 SQL 里的 `%%` 不需要"修复"）；`sqlbase.ev_base_sql(start, end)` 返回含两个 `%s` 的 SQL 文本，调用方必须传 `(start, end)` 参数；`connection` 没有 `.execute()`——一律 `cur = conn.cursor(); cur.execute(...)`。
- **PG 坑**：timestamp 差值取秒用 `EXTRACT(EPOCH FROM ...)`；UNION ALL 分支带 ORDER BY/LIMIT 需子查询包裹；会话必须 `SET TIME ZONE 'Asia/Shanghai'`（`common.connect()` 已做）。
- **DB 只读原则**：不得写 `ods/dim/fct/mart/meta` 任何对象；本项目产物只写 `dss` schema（dss 内可 DROP+CREATE）。W1 的六张 `dss.dim_*` 表**本波次只读不写**（回归护栏）。
- **cohort 污染**：08/09 线别 cohort 必须从 `tmp_ev_base`（entry/exit 截断）构建，禁止从原始流 `LIKE '09IS%'`（09IS360/350 是出口后记录站，几乎全车都过）。
- **CLI 窗口约定**：`--win-end` / `--replay-end` 等日期参数按当天 23:59:59 解析（Asia/Shanghai）。
- **git**：工作分支 `main`；实现者（subagent）不碰 git；commit 由 controller 在评审通过后执行，且**每次 commit 前向用户报告变更文件清单 + message 并获确认**（AGENTS.md）。
- **plan = 合同**：代码以本计划 verbatim 为准；执行中如需偏离，停下报告，不擅自改。
- **验收门拍板权**：门值 / 统计量 / 估计器的解释权在用户（W1 先例：门判 FAIL 是合法实测结果，如实上报）。

## File Structure

```
spike_w2/                      # 本波次代码（新目录）
  common.py                    # W1 基座再导出（DSN/connect/站/flag）
  shifts.py                    # 班次日历纯函数：类型自学 + 窗/锚点构建 + ISO 周（TDD）
  eta_v2.py                    # W2 ETA 纯逻辑：cohort 混合 + 周度 p + 补偿豁免 + 质量标记（TDD）
  01_learn_v2.py               # v2 学习器：六张 v2 表 + sanity（写 dss.v2_*）
  02_replay_shift.py           # 班次级回放：多锚点 + 三估计器 + 门判 + 产物
tests/
  test_common_w2.py            # import/DB 冒烟
  test_shifts.py               # shifts.py 单测（合成数据）
  test_eta_v2.py               # eta_v2.py 单测（合成数据）
output/                        # 运行报告（md/json 入库，CSV/SQL 忽略）
docs/01-开发阶段计划.md          # W2 节现状行更新（Task 5）
CHANGELOG.md                   # W2 条目（Task 5）
README.md                      # 目录/运行说明加 spike_w2 行（Task 5，只加不改 W1 行）
```

接口总览（先读）：

```
spike_w2.common:  DSN, connect(), ENTRY_STATIONS, EXIT_STATIONS, FLAG_NORMAL/REWORK/BLACKTOP
spike_w2.shifts:  Shift(kind, day, work_start, attr_start, end, type)
                  detect_day_type(counts: dict[str,int], day_total: int) -> str|None
                  detect_mid_type(last_min: int|None) -> str|None
                  shift_windows(day: date, day_type: str|None, mid_type: str|None) -> list[Shift]
                  anchors(shift: Shift) -> list[datetime]          # 开班+{3,4,5,6}h
                  gate_anchor(shift: Shift) -> datetime            # 开班+5h
                  is_complete(shift, replay_start, replay_end) -> bool
                  iso_week_label(d: date) -> str                   # "2026-W34"
                  week_start(d: date) -> date                      # 周一
                  week_learning_range(label: str) -> (date, date)  # 前 8 ISO 周 [周一-8w, 周一-1d]
                  常量: ANCHOR_HOURS=(3,4,5,6), GATE_ANCHOR_H=5, ATTR_DAY_START=time(4,45),
                        TANK_DAY_END=time(16,30), MID_START=time(16,30), SURGE_MIN_15MIN=30,
                        MIN_DAY_EVENTS=500, MID_MIN_FLOOR=300
spike_w2.eta_v2:    ALL="ALL", WEEK_ALL="ALL", P_WEEK_MIN_N=50, COHORT_MIN_N=30, LINEPROB_MIN_N=100
                  resolve_main(table, station, model, min_n=30) -> float|None
                  resolve_cohort_p50(cohort, station, model, lineprob, week) -> float|None
                  resolve_p(fut_week, fut_all, station, model, week) -> (p1, p2)
                  estimate_event(t, station, flag, model, week, tables, comp_excl=frozenset())
                      -> (eta:int|None, used_cohort:bool)
                  quality_mark(quality, model, station) -> str     # ok/low_n/mixed/both
  tables dict 键（02 装载）: rem/loop_rem/loop_dur/takt（W1 形状，见 spike_w1/eta.py 头注）
                  fut_week {(station,model,week):(p1,p2,n)}  fut_all {(station,model):(p1,p2,n)}
                  cohort {(station,cohort,model):(p50,mean,p90,n)}  cohort∈("08","09")
                  lineprob {(model,week):(w08,n)}  quality {(model,station):mark}
```

## Task 0: spike_w2 基座（common 再导出 + 测试冒烟）

**Files:**
- Create: `spike_w2/common.py`
- Create: `tests/test_common_w2.py`

**Interfaces:**
- Consumes: `spike_w1.common`（W1 已交付）
- Produces: `spike_w2.common.{DSN, connect, ENTRY_STATIONS, EXIT_STATIONS, FLAG_NORMAL, FLAG_REWORK, FLAG_BLACKTOP}`（后续任务 `from spike_w2 import common`）

- [ ] **Step 1: 写失败测试**

`tests/test_common_w2.py`：

```python
# -*- coding: utf-8 -*-
"""spike_w2 基座冒烟：再导出完整性 + DB 连接 + dss 可读（W1 表在位）。"""
import pytest

from spike_w2 import common


def test_reexports():
    assert callable(common.connect)
    assert set(common.ENTRY_STATIONS) == {"01IS205", "1L360RB", "01IS045"}
    assert set(common.EXIT_STATIONS) == {"K3IS140", "1J440RB", "K2IS075", "K1IS135"}
    assert (common.FLAG_NORMAL, common.FLAG_REWORK, common.FLAG_BLACKTOP) == ("0", "1", "2")


def test_db_smoke():
    conn = common.connect()
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM dss.dim_remaining_time")
        assert cur.fetchone()[0] > 0          # W1 学习表在位（回归护栏：W2 只读它们）
        cur.execute("SELECT 1")
        assert cur.fetchone()[0] == 1
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/000_software_install/miniconda3/envs/paint-dss/python.exe -m pytest tests/test_common_w2.py -v`
Expected: `ModuleNotFoundError: No module named 'spike_w2'`

- [ ] **Step 3: 写最小实现**

`spike_w2/common.py`：

```python
# -*- coding: utf-8 -*-
"""W2 基座：再导出 W1 common（DSN/connect/上下线点/flag 常量），保证 W1/W2 口径单一来源。
班次窗口常量在 spike_w2.shifts（2026-09-19 拍板 + 细化，见 BM 笔记）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spike_w1.common import DSN, connect, ENTRY_STATIONS, EXIT_STATIONS  # noqa: F401
from spike_w1.common import FLAG_NORMAL, FLAG_REWORK, FLAG_BLACKTOP      # noqa: F401
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/000_software_install/miniconda3/envs/paint-dss/python.exe -m pytest tests/test_common_w2.py -v`
Expected: 2 passed（本机 DB 可达；不可达则如实报告，不擅自改）

- [ ] **Step 5: Commit（controller 在评审后执行，message 供用户确认）**

```bash
git add spike_w2/common.py tests/test_common_w2.py
git commit -m "W2-T0: spike_w2 基座（common 再导出 + 冒烟测试）"
```

---

## Task 1: 班次日历纯函数 `shifts.py`（TDD）

**Files:**
- Create: `spike_w2/shifts.py`
- Test: `tests/test_shifts.py`

**Interfaces:**
- Consumes: 无（纯函数，仅标准库）
- Produces: 接口总览中 `spike_w2.shifts` 全部符号（Task 3/4 消费）

口径（BM 2026-09-19 拍板 + 细化，verbatim 进 docstring）：
- 窗 = 开班 + 工作时长 + 30min 交班尾；日班窗末统一 16:30；中班 16:30 开（11h→04:00 / 10h→03:00 / 8h→01:00 窗末）
- 归属窗：日班 [04:45, 16:30]（C1）；中班 [16:30, 窗末]；(中班窗末, 04:45) 无归属
- 第 5 小时锚点 = 工作开班 + 5h（日 11h→10:00 / 10h→11:00 / 8h→13:00；中班恒 21:30）；多锚点 = 开班 + {3,4,5,6}h
- 类型自学（回放窗无周计划表）：日班 = {05:00,06:00,08:00} 中最早的"15min 桶事件数 ≥30"；中班 = 中班段末条产品车事件（相对 16:30 的分钟数：≥630→11h / ≥510→10h / 其余→8h；<300min 视为数据截断 → None）
- 【回放专用】类型自学用全天事件（含锚点时刻之后），属回放后视——生产（W3）无周计划表时不可直接沿用，届时改用前视规则/周计划表（口径变更需用户拍板）

- [ ] **Step 1: 写失败测试**

`tests/test_shifts.py`：

```python
# -*- coding: utf-8 -*-
"""shifts.py 纯函数单测（合成数据）。"""
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from spike_w2 import shifts

CST = ZoneInfo("Asia/Shanghai")


# --- detect_day_type ---

def test_day_type_11h():
    assert shifts.detect_day_type({"05:00": 50, "06:00": 60}, 40000) == "11h"


def test_day_type_10h_when_05_bin_below_threshold():
    assert shifts.detect_day_type({"05:00": 10, "06:00": 60}, 40000) == "10h"


def test_day_type_8h():
    assert shifts.detect_day_type({"05:00": 5, "06:00": 8, "08:00": 45}, 30000) == "8h"


def test_day_type_shutdown_day():
    assert shifts.detect_day_type({"05:00": 50}, 100) is None          # 全天 <500 事件


def test_day_type_fallback_10h():
    # 全天有量但三个候选桶都 <30（如 04:45 渐进爬坡型）→ 回退最常见 10h，审计表留痕
    assert shifts.detect_day_type({"05:00": 10, "06:00": 10}, 6000) == "10h"


# --- detect_mid_type ---

def test_mid_type_by_last_event():
    assert shifts.detect_mid_type(660) == "11h"     # 16:30+660min = 03:30
    assert shifts.detect_mid_type(600) == "10h"     # = 02:30
    assert shifts.detect_mid_type(510) == "10h"     # 边界：16:30+510min = 01:00
    assert shifts.detect_mid_type(509) == "8h"      # 边界：00:59
    assert shifts.detect_mid_type(480) == "8h"      # = 00:30
    assert shifts.detect_mid_type(300) == "8h"


def test_mid_type_truncated_data():
    assert shifts.detect_mid_type(10) is None       # 回放窗末日数据截断（10min 即停）
    assert shifts.detect_mid_type(None) is None     # 中班段无事件（停产日）


# --- shift_windows ---

def test_shift_windows_10h_day_and_mid():
    ds = shifts.shift_windows(date(2026, 8, 17), "10h", "10h")
    assert len(ds) == 2
    day, mid = ds
    assert day.kind == "day" and day.type == "10h"
    assert day.work_start == datetime(2026, 8, 17, 6, 0, tzinfo=CST)
    assert day.attr_start == datetime(2026, 8, 17, 4, 45, tzinfo=CST)
    assert day.end == datetime(2026, 8, 17, 16, 30, tzinfo=CST)
    assert mid.kind == "mid" and mid.type == "10h"
    assert mid.work_start == datetime(2026, 8, 17, 16, 30, tzinfo=CST)
    assert mid.end == datetime(2026, 8, 18, 3, 0, tzinfo=CST)          # 窗末 03:00


def test_shift_windows_11h_mid_end():
    mid = shifts.shift_windows(date(2026, 8, 17), None, "11h")[0]
    assert mid.end == datetime(2026, 8, 18, 4, 0, tzinfo=CST)          # 窗末 04:00


def test_shift_windows_none_types():
    assert shifts.shift_windows(date(2026, 8, 17), None, None) == []


# --- anchors / gate_anchor ---

def test_anchors_day_10h():
    day = shifts.shift_windows(date(2026, 8, 17), "10h", None)[0]
    a = shifts.anchors(day)
    assert [x.time() for x in a] == [time(9, 0), time(10, 0), time(11, 0), time(12, 0)]
    assert shifts.gate_anchor(day).time() == time(11, 0)


def test_anchors_mid_independent_of_type():
    mid = shifts.shift_windows(date(2026, 8, 17), None, "11h")[0]
    a = shifts.anchors(mid)
    assert [x.time() for x in a] == [time(19, 30), time(20, 30), time(21, 30), time(22, 30)]
    assert shifts.gate_anchor(mid).time() == time(21, 30)


# --- is_complete ---

def test_is_complete():
    rs = datetime(2026, 8, 16, 0, 0, tzinfo=CST)
    re_ = datetime(2026, 9, 19, 23, 59, 59, tzinfo=CST)
    ok = shifts.shift_windows(date(2026, 8, 17), "10h", "10h")
    assert shifts.is_complete(ok[0], rs, re_) is True
    assert shifts.is_complete(ok[1], rs, re_) is True
    # 回放窗首日之前的中班（08-15 归属，attr_start 在窗外）不完整
    # ——日循环自回放首日始，此中班本就不构造；is_complete 是安全网
    edge = shifts.shift_windows(date(2026, 8, 15), None, "10h")[0]
    assert edge.attr_start < rs
    assert shifts.is_complete(edge, rs, re_) is False
    # 回放窗末日之后的窗末
    late = shifts.shift_windows(date(2026, 9, 19), None, "10h")[0]
    assert shifts.is_complete(late, rs, re_) is False


# --- ISO 周 ---

def test_iso_week_label_and_start():
    assert shifts.iso_week_label(date(2026, 8, 16)) == "2026-W33"      # 08-16 为周日
    assert shifts.week_start(date(2026, 8, 16)) == date(2026, 8, 10)
    assert shifts.iso_week_label(date(2026, 9, 19)) == "2026-W38"


def test_week_learning_range():
    lo, hi = shifts.week_learning_range("2026-W33")
    assert lo == date(2026, 6, 15)     # 08-10 周一 往前 8 周（08-10 − 56 天）
    assert hi == date(2026, 8, 9)      # 到该周前一天（无未来泄漏）
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/000_software_install/miniconda3/envs/paint-dss/python.exe -m pytest tests/test_shifts.py -v`
Expected: `ModuleNotFoundError: No module named 'spike_w2.shifts'`（或 AttributeError）

- [ ] **Step 3: 写最小实现**

`spike_w2/shifts.py`：

```python
# -*- coding: utf-8 -*-
"""班次日历纯函数（TDD）：类型自学 + 班次窗/锚点构建 + ISO 周工具。

口径（BM 笔记 2026-09-19 拍板 + 细化，verbatim）：
- 班次窗 = 开班 + 工作时长 + 30min 交班尾：日班窗末统一 16:30（11h 05:00–16:30 /
  10h 06:00–16:30 / 8h 08:00–16:30）；中班 16:30 开（11h → 04:00 / 10h → 03:00 / 8h → 01:00 窗末）
- 归属窗：日班 = [04:45, 16:30]（提早开工 04:45–正式开班计日班的推广，8h 型日 04:45–08:00 同计日班）；
  中班 = [16:30, 窗末]；(中班窗末, 04:45) 无归属（低谷微隙，回放报告覆盖率）
- 第 5 小时锚点 = 工作开班 + 5h（日 11h→10:00 / 10h→11:00 / 8h→13:00；中班恒 21:30）；
  多锚点 = 开班 + {3,4,5,6}h
- 类型自学（回放窗无周计划表）：
  日班 = {05:00, 06:00, 08:00} 中最早的"15min 桶事件数 ≥ SURGE_MIN_15MIN"（低谷底 ~12% 峰值
       ≈ 7–8 条/15min，满 takt ≈ 40–60 条/15min，阈值 30 居中）；全天事件 < MIN_DAY_EVENTS → None
  中班 = 中班段 [D 16:30, D+1 04:45) 末条产品车事件相对 16:30 的分钟数：
       ≥630 → 11h（10h 型若 03:00 后炉区拖尾会被误判，审计表留痕）/ ≥510 → 10h / 其余 → 8h；
       < MID_MIN_FLOOR（300min，真实 8h 中班末事件 ≈ 480–540min）视为数据截断 → None
- 【回放专用】类型自学用全天事件（含锚点之后），属回放后视；生产（W3）不可直接沿用
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

CST = ZoneInfo("Asia/Shanghai")

DAY_TYPES = {"11h": time(5, 0), "10h": time(6, 0), "8h": time(8, 0)}
MID_END_TIMES = {"11h": time(4, 0), "10h": time(3, 0), "8h": time(1, 0)}
ATTR_DAY_START = time(4, 45)        # 日班归属窗起点（提早开工口径推广）
TANK_DAY_END = time(16, 30)         # 日班归属窗末（工作收班 16:00 + 30min 交班尾）
MID_START = time(16, 30)            # 中班固定开班（2026-09-19 二确）
ANCHOR_HOURS = (3, 4, 5, 6)
GATE_ANCHOR_H = 5
SURGE_MIN_15MIN = 30                # 开班判定 15min 桶阈值
MIN_DAY_EVENTS = 500                # 全天事件低于此 → 无日班（停产日）
MID_MIN_FLOOR = 300                 # 中班末事件距 16:30 低于此 → 数据截断


@dataclass(frozen=True)
class Shift:
    kind: str            # 'day' | 'mid'
    day: date            # 名义日（日班 = 当日；中班 = 开班日）
    work_start: datetime # 工作开班（锚点起算点）
    attr_start: datetime # 归属窗起点（实际下线量计数起点）
    end: datetime        # 归属窗末（窗末；预测「ETA ≤ 班末」的班末）
    type: str            # '11h' | '10h' | '8h'


def detect_day_type(counts: dict, day_total: int):
    """counts: {'05:00': n, '06:00': n, '08:00': n}（各 15min 桶事件数）。
    返回 '11h'/'10h'/'8h'/None（停产日）。"""
    if day_total < MIN_DAY_EVENTS:
        return None
    for tt in (time(5, 0), time(6, 0), time(8, 0)):
        if counts.get(f"{tt:%H:%M}", 0) >= SURGE_MIN_15MIN:
            return {time(5, 0): "11h", time(6, 0): "10h", time(8, 0): "8h"}[tt]
    return "10h"  # 三桶都未达（渐进爬坡）→ 回退最常见型；审计表留痕


def detect_mid_type(last_min):
    """last_min: 中班段末条产品车事件距 D 16:30 的分钟数（None = 段内无事件）。"""
    if last_min is None or last_min < MID_MIN_FLOOR:
        return None
    if last_min >= 630:
        return "11h"
    if last_min >= 510:
        return "10h"
    return "8h"


def shift_windows(day: date, day_type, mid_type) -> list:
    """该日的班次列表 [日班?, 中班?]（type None → 无该班次）。"""
    out = []
    if day_type is not None:
        out.append(Shift(
            kind="day", day=day, type=day_type,
            work_start=datetime.combine(day, DAY_TYPES[day_type], tzinfo=CST),
            attr_start=datetime.combine(day, ATTR_DAY_START, tzinfo=CST),
            end=datetime.combine(day, TANK_DAY_END, tzinfo=CST)))
    if mid_type is not None:
        out.append(Shift(
            kind="mid", day=day, type=mid_type,
            work_start=datetime.combine(day, MID_START, tzinfo=CST),
            attr_start=datetime.combine(day, MID_START, tzinfo=CST),
            end=datetime.combine(day + timedelta(days=1), MID_END_TIMES[mid_type], tzinfo=CST)))
    return out


def anchors(shift: Shift) -> list:
    return [shift.work_start + timedelta(hours=h) for h in ANCHOR_HOURS]


def gate_anchor(shift: Shift) -> datetime:
    return shift.work_start + timedelta(hours=GATE_ANCHOR_H)


def is_complete(shift: Shift, replay_start: datetime, replay_end: datetime) -> bool:
    """完整 = 归属窗 ⊆ 回放窗（C2：不完整班次排除出门判并列表）。"""
    return shift.attr_start >= replay_start and shift.end <= replay_end


def iso_week_label(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_start(d: date) -> date:
    return d - timedelta(days=d.isoweekday() - 1)


def week_learning_range(week_label: str) -> tuple:
    """该周之前的 8 个 ISO 周（滚动学习窗，D3）：[周一-8周, 周一-1天]。无未来泄漏。"""
    y, w = int(week_label[:4]), int(week_label[6:8])
    monday = date.fromisocalendar(y, w, 1)
    return monday - timedelta(weeks=8), monday - timedelta(days=1)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/000_software_install/miniconda3/envs/paint-dss/python.exe -m pytest tests/test_shifts.py -v`
Expected: 15 passed

- [ ] **Step 5: 全量回归**

Run: `D:/000_software_install/miniconda3/envs/paint-dss/python.exe -m pytest tests/ -v`
Expected: 全部通过（含 W1 的 test_eta.py / test_db_smoke.py）

- [ ] **Step 6: Commit（controller）**

```bash
git add spike_w2/shifts.py tests/test_shifts.py
git commit -m "W2-T1: 班次日历纯函数（类型自学 + 窗/锚点 + ISO 周，TDD 15 例）"
```

---

## Task 2: W2 ETA 纯逻辑 `eta_v2.py`（TDD）

**Files:**
- Create: `spike_w2/eta_v2.py`
- Test: `tests/test_eta_v2.py`

**Interfaces:**
- Consumes: 无（纯逻辑）；W1 表行形状（`spike_w1/eta.py` 头注）
- Produces: 接口总览中 `spike_w2.eta_v2` 全部符号

机制（对应六项必做）：
- ① cohort 混合：flag=0 时先试 `cohort` 表（线别 P50，model→ALL 回退，n≥30）+ `lineprob`（周度→WEEK_ALL 回退，n≥100）→ P50 = w08·P50_08 + w09·P50_09；缺任一 → 回退 W1 `rem` 表。flag 1/2 不走 cohort（环站非共享站）。
- ② 周度 p：`resolve_p` 先 `fut_week`（model→ALL，n≥50）再 `fut_all`（model→ALL）；全缺 → (0,0)
- ⑥ 补偿净化：`(model, station) ∈ comp_excl` → 不加 p×L（⑥b 混合低 n 站免补偿）
- ⑤ 质量标记：`quality_mark` 纯查表（表在 Task 3 学出）

- [ ] **Step 1: 写失败测试**

`tests/test_eta_v2.py`：

```python
# -*- coding: utf-8 -*-
"""eta_v2.py 纯逻辑单测（合成数据）。"""
from spike_w2 import eta_v2

W1 = "2026-W33"
W2 = "2026-W34"
ALL = eta_v2.ALL


def _tables(**kw):
    base = {"rem": {}, "loop_rem": {}, "loop_dur": {}, "takt": {},
            "fut_week": {}, "fut_all": {}, "cohort": {}, "lineprob": {}, "quality": {}}
    base.update(kw)
    return base


# --- resolve_main（W1 两级回退语义不变） ---

def test_resolve_main_fallback():
    t = {("s", "m1"): (100, 110, 120, 50), ("s", ALL): (200, 210, 220, 500)}
    assert eta_v2.resolve_main(t, "s", "m1") == 100
    assert eta_v2.resolve_main(t, "s", "m_rare") == 200
    assert eta_v2.resolve_main({("s", "m1"): (100, 110, 120, 29)}, "s", "m1") is None
    assert eta_v2.resolve_main({}, "s", "m1") is None


# --- ② 周度 p 回退链 ---

def test_resolve_p_weekly_preferred():
    fw = {("s", "m1", W1): (0.5, 0.1, 60)}
    fa = {("s", "m1"): (0.2, 0.1, 1000)}
    assert eta_v2.resolve_p(fw, fa, "s", "m1", W1) == (0.5, 0.1)


def test_resolve_p_weekly_low_n_falls_back_to_all():
    fw = {("s", "m1", W1): (0.5, 0.1, 49)}        # n<50 → 弃
    fa = {("s", "m1"): (0.2, 0.1, 1000)}
    assert eta_v2.resolve_p(fw, fa, "s", "m1", W1) == (0.2, 0.1)


def test_resolve_p_model_fallback_to_all():
    fw = {("s", ALL, W1): (0.3, 0.05, 200)}
    assert eta_v2.resolve_p(fw, {}, "s", "m_rare", W1) == (0.3, 0.05)


def test_resolve_p_missing():
    assert eta_v2.resolve_p({}, {}, "s", "m", W1) == (0.0, 0.0)


# --- ① cohort 混合 ---

def test_cohort_mix_weighted():
    coh = {("s", "08", "m1"): (100, 105, 120, 50), ("s", "09", "m1"): (200, 205, 220, 50)}
    lp = {("m1", W1): (0.6, 1000)}
    assert eta_v2.resolve_cohort_p50(coh, "s", "m1", lp, W1) == 140.0   # 0.6*100+0.4*200


def test_cohort_lineprob_week_fallback_all():
    coh = {("s", "08", "m1"): (100, 105, 120, 50), ("s", "09", "m1"): (200, 205, 220, 50)}
    lp = {("m1", eta_v2.WEEK_ALL): (0.5, 1000)}
    assert eta_v2.resolve_cohort_p50(coh, "s", "m1", lp, W1) == 150.0


def test_cohort_model_fallback_all_row():
    coh = {("s", "08", ALL): (100, 105, 120, 500), ("s", "09", ALL): (300, 305, 320, 500)}
    lp = {(ALL, W1): (0.25, 1000)}
    assert eta_v2.resolve_cohort_p50(coh, "s", "m_rare", lp, W1) == 250.0  # .25*100+.75*300


def test_cohort_missing_returns_none():
    assert eta_v2.resolve_cohort_p50({}, "s", "m1", {("m1", W1): (0.5, 1000)}, W1) is None
    assert eta_v2.resolve_cohort_p50(
        {("s", "08", "m1"): (100, 105, 120, 50)}, "s", "m1", {("m1", W1): (0.5, 1000)}, W1) is None
    assert eta_v2.resolve_cohort_p50(
        {("s", "08", "m1"): (100, 105, 120, 50), ("s", "09", "m1"): (200, 205, 220, 50)},
        "s", "m1", {("m1", W1): (0.5, 99)}, W1) is None    # 线概率 n<100 弃


# --- estimate_event 主路径 ---

def test_event_flag1_uses_loop_distribution_no_comp():
    tables = _tables(
        rem={("s", "m"): (500, 520, 600, 50)},
        loop_rem={("s", "1", "m"): (900, 950, 1000, 40)},
        fut_week={("s", "m", W1): (0.9, 0.0, 200)},     # 高 p 也不得补偿（已在环内）
        fut_all={("s", "m"): (0.9, 0.0, 1000)},
        loop_dur={("1", "m"): (3600, 40)},
    )
    eta, used = eta_v2.estimate_event(0, "s", "1", "m", W1, tables)
    assert eta == 900 and used is False


def test_event_flag0_main_plus_weekly_comp():
    tables = _tables(
        rem={("s", "m"): (500, 520, 600, 50)},
        fut_week={("s", "m", W1): (0.2, 0.1, 200)},
        loop_dur={("1", "m"): (3600, 40), ("2", "m"): (1800, 30)},
    )
    eta, used = eta_v2.estimate_event(0, "s", "0", "m", W1, tables)
    assert eta == 0 + round(500 + 0.2 * 3600 + 0.1 * 1800)   # = 1400
    assert used is False


def test_event_comp_excl_kills_compensation():
    tables = _tables(
        rem={("s", "m"): (500, 520, 600, 50)},
        fut_week={("s", "m", W1): (0.9, 0.0, 200)},
        loop_dur={("1", "m"): (3600, 40)},
    )
    eta_on, _ = eta_v2.estimate_event(0, "s", "0", "m", W1, tables,
                                      comp_excl=frozenset())
    eta_off, _ = eta_v2.estimate_event(0, "s", "0", "m", W1, tables,
                                       comp_excl=frozenset({("m", "s")}))
    assert eta_on == round(500 + 0.9 * 3600)
    assert eta_off == 500                                  # ⑥b：免补偿


def test_event_cohort_mix_preferred_over_main():
    tables = _tables(
        rem={("s", "m"): (800, 810, 900, 50)},             # W1 主分布 800
        cohort={("s", "08", "m"): (100, 105, 120, 50),
                ("s", "09", "m"): (200, 205, 220, 50)},
        lineprob={("m", W1): (0.5, 1000)},
        fut_all={("s", "m"): (0.0, 0.0, 1000)},
    )
    eta, used = eta_v2.estimate_event(0, "s", "0", "m", W1, tables)
    assert used is True and eta == round(0.5 * 100 + 0.5 * 200)   # = 150


def test_event_cohort_missing_falls_back_to_main():
    tables = _tables(rem={("s", "m"): (800, 810, 900, 50)},
                     fut_all={("s", "m"): (0.0, 0.0, 1000)})
    eta, used = eta_v2.estimate_event(0, "s", "0", "m", W1, tables)
    assert used is False and eta == 800


def test_event_no_coverage():
    tables = _tables()
    assert eta_v2.estimate_event(0, "s", "0", "m", W1, tables)[0] is None


# --- ⑤ 质量标记 ---

def test_quality_mark():
    q = {("m", "a"): "low_n", ("m", "b"): "mixed", ("m", "c"): "both"}
    assert eta_v2.quality_mark(q, "m", "a") == "low_n"
    assert eta_v2.quality_mark(q, "m", "z") == "ok"          # 未标记默认 ok
```

- [ ] **Step 2: 跑测试确认失败**

Run: `D:/000_software_install/miniconda3/envs/paint-dss/python.exe -m pytest tests/test_eta_v2.py -v`
Expected: `ModuleNotFoundError`（无 spike_w2.eta_v2）

- [ ] **Step 3: 写最小实现**

`spike_w2/eta_v2.py`：

```python
# -*- coding: utf-8 -*-
"""W2 ETA 纯逻辑：W1 直接估计器 + ① 线别 cohort 混合 + ② 周度 p + ⑥ 补偿豁免 + ⑤ 质量标记（TDD）。

W1 表行形状（不变，见 spike_w1/eta.py 头注）：
  rem:      {(station, model): (p50, mean, p90, n)}
  loop_rem: {(station, flag, model): (p50, mean, p90, n)}
  loop_dur: {(flag, model): (l, n)}
  takt:     {(frm, to, model): (p50, mean, p90, n)}
W2 表行形状（新增，Task 3 学出）：
  fut_week: {(station, model, week): (p1, p2, n)}   # 滚动 8 周学习（week = "YYYY-Www"）
  fut_all:  {(station, model): (p1, p2, n)}         # 全窗 04-14→08-15（⑥a 前环人群净化）
  cohort:   {(station, cohort, model): (p50, mean, p90, n)}   # cohort ∈ ("08", "09")
  lineprob: {(model, week): (w08, n)}               # 滚动 8 周线概率；week=WEEK_ALL 为全窗
  quality:  {(model, station): mark}                # mark ∈ ok/low_n/mixed/both
"""
from typing import Optional

ALL = "ALL"
WEEK_ALL = "ALL"
P_WEEK_MIN_N = 50          # 周度 p 行 n≥50 才用，否则回退 fut_all（周窗小样本不稳）
COHORT_MIN_N = 30          # cohort 表行 n≥30 才用（与主分布同门槛）
LINEPROB_MIN_N = 100       # 线概率行 n≥100 才用（分派比小样本噪声大）
MIN_N = 30                 # 主/环分布行 n 门槛（与 W1 resolve 一致）


def resolve_main(table: dict, station: str, model: str, min_n: int = MIN_N) -> Optional[float]:
    """W1 主分布两级回退：(station, model) → (station, 'ALL')；行尾 n < min_n 视为不可用。"""
    for m in (model, ALL):
        row = table.get((station, m))
        if row is not None and row[-1] >= min_n:
            return row[0]
    return None


def _row_n(table: dict, key: tuple, min_n: int):
    row = table.get(key)
    if row is not None and row[-1] >= min_n:
        return row
    return None


def resolve_cohort_p50(cohort: dict, station: str, model: str,
                       lineprob: dict, week: str) -> Optional[float]:
    """① 线别 cohort 混合：P50 = w08·P50_08 + w09·P50_09。
    各 cohort P50 走 model→ALL 回退（n≥COHORT_MIN_N）；线概率走 (model, week) → (model, WEEK_ALL)
    → (ALL, week) → (ALL, WEEK_ALL)（n≥LINEPROB_MIN_N）。任一缺失 → None（调用方回退 W1 主分布）。
    非后见：线概率用过去 8 周学出（Task 3），不是该车自己的实际走线。
    """
    if not cohort:
        return None

    def c50(coh):
        for m in (model, ALL):
            row = _row_n(cohort, (station, coh, m), COHORT_MIN_N)
            if row is not None:
                return row[0]
        return None

    p50_08, p50_09 = c50("08"), c50("09")
    if p50_08 is None or p50_09 is None:
        return None
    w08 = None
    for m in (model, ALL):
        for wk in (week, WEEK_ALL):
            row = _row_n(lineprob, (m, wk), LINEPROB_MIN_N)
            if row is not None:
                w08 = row[0]
                break
        if w08 is not None:
            break
    if w08 is None:
        return None
    return w08 * p50_08 + (1.0 - w08) * p50_09


def resolve_p(fut_week: dict, fut_all: dict, station: str, model: str,
              week: str) -> tuple:
    """② 周度 p：(station, model, week) n≥P_WEEK_MIN_N → 用；否则 fut_all (station, model)
    （model→ALL 回退，行 n≥30，与 W1 resolve 默认门槛一致）；全缺 → (0.0, 0.0)。"""
    if fut_week:
        for m in (model, ALL):
            row = _row_n(fut_week, (station, m, week), P_WEEK_MIN_N)
            if row is not None:
                return row[0], row[1]
    if fut_all:
        for m in (model, ALL):
            row = _row_n(fut_all, (station, m), MIN_N)
            if row is not None:
                return row[0], row[1]
    return 0.0, 0.0


def estimate_event(t: int, station: str, flag: str, model: str, week: str,
                   tables: dict, comp_excl: frozenset = frozenset()
                   ) -> tuple:
    """单事件 ETA（W1 直接估计器 v2）。返回 (eta 绝对秒|None, used_cohort bool)。

    flag ∈ {1,2} → 环分布 P50（model→ALL，n≥30）；不加补偿（已在环内），不走 cohort。
    flag = 0 → 主分布 P50：① 先试 cohort 混合（缺 → W1 rem 表）
              + 补偿 p1·L1 + p2·L2（② 周度 p；⑥b (model,station) ∈ comp_excl → 补偿 = 0）。
    L1/L2 走 loop_dur model→ALL（n≥30），缺 → 0。
    """
    if flag in ("1", "2"):
        base = None
        for m in (model, ALL):
            row = tables["loop_rem"].get((station, flag, m))
            if row is not None and row[-1] >= MIN_N:
                base = row[0]
                break
        if base is None:
            return None, False
        return t + int(round(base)), False

    used_cohort = False
    base = resolve_cohort_p50(tables.get("cohort"), station, model,
                              tables.get("lineprob"), week)
    if base is not None:
        used_cohort = True
    if base is None:
        base = resolve_main(tables["rem"], station, model)
    if base is None:
        return None, used_cohort

    p1 = p2 = 0.0
    if (model, station) not in comp_excl:
        p1, p2 = resolve_p(tables.get("fut_week"), tables.get("fut_all"),
                           station, model, week)

    def ld(flagc):
        for m in (model, ALL):
            row = tables["loop_dur"].get((flagc, m))
            if row is not None and row[-1] >= MIN_N:
                return row[0]
        return 0.0

    eta = t + int(round(base + p1 * ld("1") + p2 * ld("2")))
    return eta, used_cohort


def quality_mark(quality: dict, model: str, station: str) -> str:
    """⑤ 质量标记：未标记默认 'ok'。mark ∈ ok/low_n/mixed/both。"""
    return quality.get((model, station), "ok")
```

- [ ] **Step 4: 跑测试确认通过**

Run: `D:/000_software_install/miniconda3/envs/paint-dss/python.exe -m pytest tests/test_eta_v2.py -v`
Expected: 16 passed

- [ ] **Step 5: 全量回归**

Run: `D:/000_software_install/miniconda3/envs/paint-dss/python.exe -m pytest tests/ -v`
Expected: 全部通过

- [ ] **Step 6: Commit（controller）**

```bash
git add spike_w2/eta_v2.py tests/test_eta_v2.py
git commit -m "W2-T2: eta_v2 ETA 纯逻辑（cohort 混合 + 周度 p + 补偿豁免 + 质量标记，TDD 16 例）"
```

---

## Task 3: v2 学习器 `01_learn_v2.py`（六张 v2 表 + sanity）

**Files:**
- Create: `spike_w2/01_learn_v2.py`

**Interfaces:**
- Consumes: `spike_w2.common`（Task 0）、`spike_w2.shifts.week_learning_range`（Task 1）、`spike_w1.sqlbase.ev_base_sql`
- Produces: `dss.v2_future_flag` / `dss.v2_cohort_remaining` / `dss.v2_line_prob` / `dss.v2_station_quality` / `dss.v2_comp_excl` / `dss.v2_learner_meta`；报告 `output/w2_learn_sanity_<YYYYMMDD>.md`

学习窗策略（D3）：
- `fut_all`（week='ALL'）= W1 窗 04-14→08-15，⑥a 前环人群净化；`lineprob` 的 week='ALL' 行同窗学出（cohort 回退链终点）
- 每回放周 w（回放窗覆盖的 ISO 周，由参数推导）：ev_base 重建为 [w 周一 − 8 周, w 周一 − 1 天]，学 `fut_week`(week=w) + `lineprob`(week=w)
- `cohort` 表 = W1 窗（04-14→08-15）一次
- W1 六表**不写**；`v2_station_quality` / `v2_comp_excl` 由 Python 侧从 `dss.dim_remaining_time`（W1，只读）+ `v2_future_flag` 推导

- [ ] **Step 1: 写学习器脚本**

`spike_w2/01_learn_v2.py`：

```python
# -*- coding: utf-8 -*-
"""W2 v2 学习器：周度 p（滚动 8 周）+ 线别 cohort 剩余 + 周度线概率 + 站级质量标记/补偿豁免。

用法：python spike_w2\\01_learn_v2.py [--learn-start 2026-04-14] [--learn-end 2026-08-15]
                              [--replay-start 2026-08-16] [--replay-end 2026-09-19]
只写 dss.v2_* 六张表；W1 六张 dss.dim_* 只读不写（回归护栏）。
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
                "WHERE body_type <> 'ALL'")
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

    # W2-S2 周度 p 全覆盖 + 决策簇形态：每回放周有行；15IS065(ALL) 每周 n≥1000 且 0.03≤p1≤0.98
    cur.execute("SELECT week, count(*) FROM dss.v2_future_flag GROUP BY week")
    per_week = dict(cur.fetchall())
    all_weeks = set(weeks) | {"ALL"}
    missing = sorted(w for w in all_weeks if w not in per_week)
    cur.execute("SELECT week, p1, n FROM dss.v2_future_flag "
                "WHERE body_type = 'ALL' AND station = '15IS065' ORDER BY week")
    cluster = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    cl_ok = all(w in cluster and cluster[w][0] is not None
                and 0.03 <= cluster[w][0] <= 0.98 and cluster[w][1] >= 1000
                for w in weeks)
    checks.append(("W2-S2", not missing and cl_ok,
                   f"周度 p 缺失周 = {missing or '无'}；决策簇 15IS065 每周 p1∈[0.03,0.98] 且 n≥1000 = {cl_ok}"))

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
            "n_v2_comp_excl": str(n_excl)}
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
```

- [ ] **Step 2: 运行学习器**

Run: `D:/000_software_install/miniconda3/envs/paint-dss/python.exe spike_w2/01_learn_v2.py`
Expected: 6 张 v2 表写入；W2-S1–S4 全 PASS；`output/w2_learn_sanity_<YYYYMMDD>.md` 生成。**任何 FAIL 如实报告 + 诊断，不擅自改阈值**（W1 先例）。
注意：运行耗时 = 1（全窗）+ 6（周窗）次 ev_base 构建，预计 5–15 分钟；若 W2-S3 的 21IS565 断言失败（p1 净化后 <0.5 或 n_model 变化），先查报告里 21IS565 的 p1/n，如实上报诊断，不停下来猜。

- [ ] **Step 3: 独立复核（controller 或实现者自验）**

Run（bash，抽数核对）：
```bash
D:/000_software_install/miniconda3/envs/paint-dss/python.exe -c "import sys; sys.path.insert(0,'.'); from spike_w2 import common; c=common.connect(); cur=c.cursor(); cur.execute(\"SELECT week, count(*), min(p1), max(p1) FROM dss.v2_future_flag WHERE body_type='ALL' GROUP BY week ORDER BY week\"); [print(r) for r in cur.fetchall()]; cur.execute(\"SELECT body_type, station, n_model, round(p1,4), mark FROM dss.v2_station_quality WHERE mark='both' ORDER BY p1 DESC LIMIT 10\"); [print(r) for r in cur.fetchall()]; c.close()"
```
Expected: 周度 p 形态合理（每周 p1 范围与全窗相近、决策簇站 p1 高）；'both' 格含 21IS565/2N54Y-。

- [ ] **Step 4: Commit（controller）**

```bash
git add spike_w2/01_learn_v2.py
git commit -m "W2-T3: v2 学习器（周度 p 滚动 8 周 + 线别 cohort + 线概率 + 质量标记/补偿豁免）"
```

---

## Task 4: 班次级回放 `02_replay_shift.py`（多锚点 + 三估计器 + 门判）

**Files:**
- Create: `spike_w2/02_replay_shift.py`

**Interfaces:**
- Consumes: `spike_w2.{common, shifts, eta_v2}`；`spike_w1.{sqlbase, eta}`（Σ 估计器复用 W1 `eta.estimate_vehicle`——后见路径诊断器，与 W1 同语义）；`dss.dim_*`（W1，只读）+ `dss.v2_*`（Task 3）
- Produces: `output/w2_replay_shifts_<YYYYMMDD>.csv` / `output/w2_replay_models_<YYYYMMDD>.csv`（D1' 逐班×车型 @5h）/ `output/w2_replay_gate_<YYYYMMDD>.json` / `output/w2_replay_report_<YYYYMMDD>.md`；exit 0 = 门过 / exit 2 = 门未过

机制（D1'/D2/C1–C3）：
- ev_base 窗 = [回放始 − 7d, 回放末 + 1d]（左端 7d ≥ 最长行程 120h，保证回放窗内下线的车其 entry 事件在窗内——W1 ev_base 只保留 entry 在窗内的车；右端 +1d 覆盖最后中班窗末 09-20 04:00 的下线事件，供 A/I 计数）
- 类型自学 SQL 走**原始流**（产品车 78202%），不是 ev_base（ev_base 丢 entry 在窗外的车，会系统性少计事件）
- 已下线 A(t) = 下线点事件 ∈ [attr_start, t]；在制 I(t) = entry_t ≤ t < exit_t；预测 P(t) = A(t) + #{v∈I(t): ETA_v(t) ≤ 窗末}
- 门（D1'）= 直接估计器 @5h 锚：**全厂**逐班 |err%| 中位数 ≤ 10% **且** 所有**主车型**（逐班实际量中位数 ≥ 30）各自逐班 |err%| 中位数 ≤ 10%；不完整 / 实际=0 班次排除并列表；返工车组 / 稀有车型仅报告
- ⑥b A/B：同一次扫描里 comp_excl 开/关各算一遍直接估计器预测

- [ ] **Step 1: 写回放脚本**

`spike_w2/02_replay_shift.py`：

```python
# -*- coding: utf-8 -*-
"""W2 班次级回放：第 5 小时（多锚点 3/4/5/6h）预测班次下线量 vs 实际。
门（D1'）= 全厂逐班 |err%| 中位数 ≤ 10% 且 主车型（逐班实际量中位数 ≥ 30）各自中位数 ≤ 10%。

用法：python spike_w2\\02_replay_shift.py [--replay-start 2026-08-16] [--replay-end 2026-09-19]
因果切分：主分布/环/takt/环时长 = W1 学习表（学习窗 04-14→08-15）；周度 p / 线概率 =
每回放周前 8 ISO 周（dss.v2_*，Task 3）。ev_base 窗 = [回放始-7d, 回放末+1d]
（7d ≥ 最长行程 120h：窗内下线的车其 entry 事件必在窗内）。
exit 0 = 门过；exit 2 = 门未过（报告含逐班/锚点/类型/标记分桶 + 类型审计 + 归属覆盖）。
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
       max(EXTRACT(EPOCH FROM (t - (mid_day + interval '16 hours 30 minutes'))))::INT AS last_min
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
    """W1 表 + v2 表 → eta_v2/eta_w1 行形状 dict（车型在 key 末位，n 在行尾）。"""
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
        "loop_dur": {(r[1], r[0]): (r[2], r[3])
                     for r in rows("SELECT body_type, flag, l, n FROM dss.dim_loop_duration")},
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
    }


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
            if is_gate_h:
                A_bm, I_bm = {}, {}
                for j in np.where((exit_arr >= attr_ep) & (exit_arr <= t))[0]:
                    A_bm[bt_arr[j]] = A_bm.get(bt_arr[j], 0) + 1
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
                eta_d, used = eta_v2.estimate_event(t_e, st_e, fl_e, model, week,
                                                    tables, comp_excl)
                eta_d0, _ = eta_v2.estimate_event(t_e, st_e, fl_e, model, week,
                                                  tables, frozenset())
                m = eta_v2.quality_mark(tables["quality"], model, st_e)
                mark_cnt[m] = mark_cnt.get(m, 0) + 1
                if used:
                    cohort_used += 1
                if eta_d is not None and eta_d <= end_ep:
                    cnt["direct"] += 1
                    if is_gate_h:
                        I_bm[model] = I_bm.get(model, 0) + 1
                        if rework_arr[i]:
                            rework_pred += 1
                if eta_d0 is not None and eta_d0 <= end_ep:
                    cnt["direct_nocomp"] += 1
                si = sum_idx.get(body)
                if si is not None and idx < len(si[1]):
                    if si[1][idx] is not None and si[1][idx] <= end_ep:
                        cnt["sum_median"] += 1
                    if si[2][idx] is not None and si[2][idx] <= end_ep:
                        cnt["sum_mean"] += 1
                else:
                    no_sum += 1
            if is_gate_h:
                row["m_gate"] = {m: (A_bm.get(m, 0) + I_bm.get(m, 0), act_bm.get(m, 0))
                                 for m in set(A_bm) | set(I_bm) | set(act_bm)}
                row["rework_pred"] = rework_pred
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
    model_actual_med = {m: float(np.median([r["m_gate"][m][1] for r in g_recs
                                            if m in r["m_gate"]]))
                        for m in all_models}
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
    lines += [f"| {m}{'（主）' if m in main_models else ''} | {model_actual_med[m]:.0f} | "
              f"{model_gate[m] * 100:.2f}% | {'✅' if model_gate[m] <= GATE_PCT else '❌'} |"
              if not math.isnan(model_gate[m]) else
              f"| {m}{'（主）' if m in main_models else ''} | {model_actual_med[m]:.0f} | — | — |"
              for m in sorted(all_models, key=lambda x: -model_actual_med[x])]
    rework_pred_tot = int(g["rework_pred"].sum())
    rework_act_tot = int(g["rework_actual"].sum())
    lines += ["", "## 返工车组 @5h（docs/01：返工车单独修正并成组显示；修正 = 环分布 + 前决策补偿）", "",
              f"- 回放全期合计：预测 {rework_pred_tot}，实际 {rework_act_tot}"
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
    lines += ["", "（direct_nocomp = ⑥b 豁免关闭的对照——量化补偿净化的班次级净效果）", "",
              "## 分桶 @5h（直接估计器）", "",
              f"- 按班次种：{ {k: v['median_abs_pct'] for k, v in by_kind.items()} }%",
              f"- 按 ISO 周：{ {k: v['median_abs_pct'] for k, v in by_week.items()} }%", "",
              "## 排除班次（C2/C3）", ""]
    lines += [f"- {s.kind} {s.day}（{s.type}）：{reason}" for s, reason, _dt, _ml in excluded]
    lines += ["", "## 班次类型自学审计（人工抽查；阈值：15min 桶 ≥30 / 中班末事件分钟数）", "",
              "| 日 | 日型判定（05:00/06:00/08:00 桶） | 全天事件 | 日班型 | 中班型（中班末事件 min） |",
              "|---|---|---|---|---|"]
    for d, dt_row, dt, mt, ml in day_audit:
        lines += [f"| {d} | {dt_row['05:00']} / {dt_row['06:00']} / {dt_row['08:00']} | {dt_row['total']} | "
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
```

**实现注意（verbatim 代码内的已知边角，行为已设计好，不要"修复"）：**
1. `idx < 0`（t 前无 0/1/2 事件）或 `idx >= len(evlist) - 1`（锚点落在该车最后一条事件之后）→ 计入 no_eta。对 exit 在 ev 窗内的车，最后一条事件就是下线事件，在制车不会触发后一支；对缺退出车（exit_t=inf，数据末端在制）会触发——此类车在数据窗内无下线事件、不进实际量，对门判影响 ≈0（设计如此，非缺陷，不要"修复"）。
2. 内存：事件流 ~230 万元组 + spans ~2 万行，峰值 ~1–1.5 GB，工作站正常；内存不足如实报告，不擅自分片。
3. 无参 SQL 里的 `%%`（SQL_11IS / SQL_COHORT_REM）：LIKE 双通配 ≡ 单通配，不传参数也正确（2026-09-19 实测），不要"改回" `%` 后再传参。

- [ ] **Step 2: 运行回放**

Run: `D:/000_software_install/miniconda3/envs/paint-dss/python.exe spike_w2/02_replay_shift.py`
Expected: 四份产物生成（shifts CSV / models CSV / gate JSON / 报告 md）；exit 0（门过）或 exit 2（门未过——**合法结果**，如实上报，不擅自改门/估计器）。运行预计 5–15 分钟（ev_base ~41 天 + 逐车 Σ 预计算 ~2 万车）。

- [ ] **Step 3: 独立复核（controller）**

Run（bash，读 gate JSON 并与 CSV 手算一个班核对）：
```bash
D:/000_software_install/miniconda3/envs/paint-dss/python.exe -c "import sys, json, glob; sys.path.insert(0,'.'); import pandas as pd; j=json.load(open(sorted(glob.glob('output/w2_replay_gate_2026*.json'))[-1],encoding='utf-8')); print(json.dumps({k:j[k] for k in ('gate_value_pct','model_gates_pct','main_models','models_pass','pass','n_shifts_in_gate')}, ensure_ascii=False, indent=1)); d=pd.read_csv(sorted(glob.glob('output/w2_replay_shifts_*.csv'))[-1]); r=d[(d.anchor_h==5)&(d.kind=='day')].sort_values('day').head(3); print(r[['day','type','A','inproc','direct_pred','actual','direct_err_pct']]); dm=pd.read_csv(sorted(glob.glob('output/w2_replay_models_*.csv'))[-1]); print(dm.groupby('model')[['pred_m','actual_m']].sum())"
```
Expected: gate JSON（全厂 + 主车型门）与两份 CSV 可互相印证（抽 3 个班：direct_pred = A + 在制车 ETA≤窗末 计数，与 actual 差 = err%；models CSV 各车型 actual_m 合计 = 班 actual）。

- [ ] **Step 4: Commit（controller）**

```bash
git add spike_w2/02_replay_shift.py
git commit -m "W2-T4: 班次级回放器（多锚点 3/4/5/6h + 三估计器 + D1' 全厂/主车型门判 + ⑥b A/B + 产物）"
```

---

## Task 5: W2 验证摘要 + 门判上报 + 文档/知识库同步

**Files:**
- Create: `output/w2-验证摘要.md`
- Modify: `docs/01-开发阶段计划.md`（W2 节现状行）
- Modify: `CHANGELOG.md`（W2 条目）
- Modify: `README.md`（目录与运行说明加 spike_w2 行——只加，不改 W1 行）

**Interfaces:**
- Consumes: Task 3/4 的全部产物
- Produces: 关账/上报文档三件套 + BM 同步（controller 执行）

- [ ] **Step 1: 写 `output/w2-验证摘要.md`**（结构如下，数字全部从 gate JSON / 报告实抄，不得心算）

```markdown
# W2 验证摘要（班次级回放，第 5 小时 ±10% 门判）

- 回放窗 / 基线 / 生成时间：（抄报告头三行）
- 门判结果（D1'）：（全厂值 + 主车型清单及各值 + 总过/未过 + n 班次 + 符号/均值/P90）
- 多锚点曲线：（表）
- 三估计器 @5h：（表）
- ⑥b A/B 净效果：（direct vs direct_nocomp 差值，一句解读）
- 排除班次与原因：（表）
- 类型自学审计结论：（误判嫌疑日列表，若有）
- 11IS A/B 检查：（rel_diff 中位 + 一句解读）
- 覆盖与机制用量：（无 ETA 占比 / cohort 命中）
- 返工车组 @5h：（组级预测/实际/误差 + 占全厂比例）
- 车型 @5h 分桶 + 入口站分桶（③④）：（稀有车型覆盖 + 01IS045 数量）
- 误差源分桶（若门未过）：按 锚点/周/班次种/质量标记/车型 的定位结论
- 产物位置：（4 份 output 文件 + v2 六张 dss 表）
```

- [ ] **Step 2: 门判上报（controller，不代拍板）**

把门判值 + 多锚点曲线 + 误差源分桶（若 FAIL）整理成一屏摘要交用户；**门判 FAIL 不自动修订**（W1 先例：诊断清楚 + 用户拍板才动口径）。用户拍板后按其决定更新验证摘要「门判终局」节。

- [ ] **Step 3: 文档同步**

- `docs/01-开发阶段计划.md`：W2 节现状行 →「W2 班次级回放 已关账/进行中（日期，门判值）」；验收句「（车型汇总）/返工车成组显示」已由 D1' 主车型分门 + 报告分桶兑现，**不改原文**
- `CHANGELOG.md`：追加 W2 条目（波次级粒度：产物文件 + 门判值 + 关键决策一句话；设计决策细节进 BM，不进 CHANGELOG）
- `README.md`：目录加 `spike_w2/` 行；「agent 运行说明」加 W2 两脚本的用法行

- [ ] **Step 4: BM 同步（controller，MCP-first，project 显式 `basic-memory`）**

- 线程笔记 `涂装车间DSS-方案设计与开发路线`：append `[status]` 观察（W2 门判结果 + 多锚点曲线要点 + ⑥b 净效果 + 排除班次数）与（若修订）`[decision]` 观察；先 `search_notes` 确认线程 + `read_note` 读全文再 `edit_note`（find_replace，唯一锚点）
- Task Board `paint-dss:` 前缀任务行：W2 → 关账状态
- 工具名用连字符前缀（`basic-memory_read_note` 等）；大笔记读取截断时 grep 临时文件验证尾部

- [ ] **Step 5: Commit（controller，用户确认后）**

```bash
git add output/w2-验证摘要.md docs/01-开发阶段计划.md CHANGELOG.md README.md
git commit -m "W2-T5: 验证摘要 + 门判上报 + 文档/CHANGELOG 同步（W2 关账）"
```

---

## Self-Review（写计划时自查结果）

1. **Spec 覆盖**：W2 六项必做——① cohort（Task 3 SQL_COHORT_REM + Task 2 resolve_cohort_p50）✅；② 周度 p（SQL_FUTURE_FLAG 周循环 + resolve_p）✅；③ 稀有长行程车型（三级回退 + 报告分桶，无专门建模，D4 拍板项）✅；④ 01IS045（W1 S1 已"只报告不判定"，W2 回放按站分桶报告覆盖，不建模）✅；⑤ 质量标记（build_quality + quality_mark + 报告分桶）✅；⑥ 补偿净化（⑥a 前环人群 SQL 条件 + ⑥b comp_excl + A/B 对照）✅。门判 ±10% 第 5 小时（D1'：全厂 ∧ 主车型，2026-09-20 拍板）✅；班次口径 2026-09-19 细化（6 型窗 + 04:45 归属推广 C1 + 多锚点 D2 曲线）✅。
2. **占位符扫描**：无 TBD/TODO；每步有代码或命令。Task 4 的两处已知边角显式注明"不要修复"（防实现者好心改坏）。
3. **类型一致性**：`eta_v2.estimate_event` 返回 `(eta|None, used_cohort)` 与 Task 4 调用一致；`Shift` 字段（kind/day/work_start/attr_start/end/type）与 shifts.py 定义一致；`tables` dict 键（rem/loop_rem/loop_dur/takt/**fut**/fut_week/fut_all/cohort/lineprob/quality）与 load_tables / 单测一致——`fut` 键是 `eta_w1.estimate_vehicle` 的必需键（W1 键名，取 v2 全窗 p 作别名），漏掉即 KeyError（2026-09-19 自查后已修）；`week_learning_range` 返回 `(date, date)` 与 Task 3 解包一致。ISO 周标签：08-16 属 2026-W33（周日）、09-19 属 2026-W38（已按 date.isocalendar 实算校准，Task 1 测试固化）。
4. **因果泄漏检查**：p/线概率 学习窗 = 回放周之前 8 周（week_learning_range hi = 周一-1天）✅；cohort 表/主分布 = 04-14→08-15 固定窗 ✅；类型自学用原始流事件（回放窗内数据；**回放专用**——用全天事件属回放后视，生产不可直接沿用，见 Task 1 注记）✅；Σ 估计器 = 后见路径（显式标注诊断用途，非门判对象）✅。
5. **2026-09-20 复审修订**（审核报告 13 条全部独立复核属实，逐条修；D1' 用户拍板选 3）：B1 测试 500→边界 510/509；B2 断言 07-06→06-15；B3 150.0→250.0；B4 补 `SQL_LINE_PROB ("ALL","ALL")`（全窗块，write_report 的 top-8 车型节依赖）；B5 列名统一 `direct_nocomp_err_pct`；M1 ⑥a 条件改 `f1/f2` 首环前过滤；M2 报告加「车型 @5h 分桶 + 入口站分桶」（spans SQL 加 entry_st 列 + Counter）；M3→D1' 门改「全厂 ∧ 主车型（逐班实际量中位数 ≥30）各自中位数 ≤10%」（5h 锚点逐车型 A_bm/I_bm 累加 + models CSV + gate JSON model_gates_pct + 报告三新节 + 返工车组）；L1–L4 注释/注记；L5 文档小瑕疵 ×4。修订后全部 8 个代码块重过 ast.parse、15+16 单测期望值重算。
