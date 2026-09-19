# W1 节拍学习器 + 单车 ETA 回放 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 构建 W1 离线 spike——节拍学习器（写 `dss` schema 五张学习表 + 元数据）与单车 ETA 历史回放（三估计器对打），用因果切分（前窗学习 → 后窗回放）验证最高风险假设「位置×节拍能否预测准」。

**Architecture:** 学习器与回放共用同一 `tmp_ev_base` 会话临时表（产品车过滤 → 上下线点分段 → 同站去重 → MDS[139] 标志解析 → 每车最后环事件时间）；重计算（分位数 / IQR 清洗 / 聚合）全部用 Postgres 窗口函数完成；估计决策（三估计器、两级回退、环分流、前决策补偿）是纯 Python 模块 `spike_w1/eta.py`（无 DB 依赖，可单元测试）；回放把学习表加载进 Python dict，逐车调用纯估计函数，产出误差与门判报告。

**Tech Stack:** Python 3.13（conda env `paint-dss`）、psycopg2、pandas、pytest；PostgreSQL `analytics_db` @ 127.0.0.1:5432（数据为存量，公网环境不续更，可断言精确数值）。

**Spec:**
- 设计定稿（口径单一事实源）：Basic Memory 笔记 `basic-memory/projects/paint-dss/涂装车间DSS-方案设计与开发路线`（「节拍学习器」「鲁棒性：返工/黑顶」「待用户确认已办结」节）
- 可执行阶段计划：`docs/01-开发阶段计划.md` 的 **W1** 节（范围 / 验收 / 失败判据）
- 口径速查：`README.md` 关键口径对照表

## Global Constraints

每条任务的要求都隐含包含本节（值从 spec 原文逐字复制）：

- **时区**：所有 DB 查询必须 `SET TIME ZONE 'Asia/Shanghai'`（会话默认 UTC，工厂时 = UTC+8，否则边界全部错位 8 小时）。本计划所有连接一律经 `common.connect()`（内部已 SET）。
- **只读原则**：不得修改 `ods` / `dim` / `fct` / `mart` / `meta` 层任何表与视图；本项目产物只写 `dss` schema（`dss` 内可 DROP + CREATE）。学习器对 `ods` 只 SELECT。
- **学习样本**：产品车 `"BODY_ID" LIKE '78202%'`（全量 83,343 车 / 5,371,895 事件——2026-09-20 冒烟测试对活库钉死，替代原估 536.1 万；项目车 144 台与异常车不进学习链）。
- **上线点（3）**：`01IS205` / `1L360RB` / `01IS045`；**下线点（4）**：`K3IS140` / `1J440RB` / `K2IS075` / `K1IS135`。行程口径 = 逐车实际经过的上线点事件 → 下线点事件（单程，返工/黑顶为线内环，上下线点各 1 次）。
- **标志位**：`SUBSTRING("MDS_DATA" FROM 139 FOR 1)`（1-based）：`0` 正常 / `1` 返工 / `2` 黑顶二喷 / `-` 未填充（视同未知，不参与环判定）/ `3` 未定义（全库 2 条，归异常日志）。
- **主分布 = 干净后缀**：每车最后一次 flag∈{1,2} 事件**之后**的 flag=0 事件（此前 flag=0 站的剩余时间含环内延迟，有偏必须弃）；环分布 = `车型×标志{1,2}×站` 独立学习。
- **前决策补偿**：`ETA = P50(主) + p(车型,站)×L(车型,环)`，p = 未来标志概率（车型×站，主分布口径下该站 flag=0 车后续首次被打 1/2 的条件概率，决策站簇下游自然 ≈0），L = 环时长中位数（车型×环）。返工/黑顶独立计算（1,715 台两标志兼有）。
- **验收门**：单车 ETA 中位误差 ≤ 30min（=1800s；直接剩余分布估计器、量产车、完整行程车）。Learning sanity（2026-09-19 干跑校准后，依据见下节「干跑校准结论」）：入口站 P50 绝对范围 [8h, 30h] 且与同窗行程 P50 自洽（偏差 ≤10%；阶段计划「16–20h」已被实测 14.3h/24.3h 取代）；首条 flag=1 集中于 15IS065/13IS065/14IS065（≥80%，六确 97%）；首条 flag=2 集中于 14IS260/24IS185（≥60%，六确 76%）；p1 剖面 max（n≥1000 站）≥0.03 且 min ≤0.01；环表 n 充足（全窗返工 13,010 台 / 黑顶 9,711 台）。
- **因果切分**：默认学习窗 `2026-04-14` → `2026-08-15`，回放窗 `2026-08-16` → `2026-09-18`（CLI 可配）。学习/回放均只使用各自窗口内的数据（自含口径：entry/exit 事件都必须在窗口内，窗口截断的在制车不进样本）。
- **失败判据**：中位误差 > 30min → 按车型/站分桶定位误差源，未诊断清楚不进 W2（诊断数据已内置于回放报告，见 Task 4）。
- **运行环境**：conda env `paint-dss`；环境解释器 `D:\000_software_install\miniconda3\envs\paint-dss\python.exe`。**Windows 坑**：`conda run -n paint-dss python -c "多行代码"` 报错（不支持含换行的参数）——一律用环境解释器直调，或保持单行参数。
- **Git**：`main` 分支；commit message 用中文（例：`W1: 节拍学习器 v1，五张学习表产出`）；暂不推 remote；**每次 commit 前必须向用户报告变更文件清单 + commit message 并得到明确确认（如"确认"），禁止自动 commit**。
- **output/ 策略**：`output/` 在 .gitignore（原始 CSV / 运行报告不入库）；**唯一例外**：W1 验证摘要 `output/w1-验证摘要.md` 用 `git add -f` 强制提交（验收项原文「验证摘要写入 output/ 并 commit」）。
- **列名**：大写列名 SQL 中必须双引号：`"DATE_EVT"`、`"BODY_ID"`、`"BODY_TYPE"`、`"RW_STATION_ID"`、`"MDS_DATA"`、`"ID"`（源表列均为大写存储，无引号小写引用会报 UndefinedColumn——`ev_base` 在入口 CTE 用 `"ID" AS id` 归一成小写，下游临时表列用小写 `id`）。
- **时间单位**：学习表内全部存**整数秒**（BIGINT），CLI 日期参数按 Asia/Shanghai 墙钟解析。

## 干跑校准结论（2026-09-19 实测，计划阈值依据此节）

计划定稿前用 2 天窗 + 1 周窗（08-03→08-09）在真实库跑了学习器五表聚合、S 校验 SQL 与 SQL_0809（干跑 dss 表已 DROP 复原）。以下实测结论**覆盖**阶段计划/BM 笔记中的旧期望值：

1. **入口站行程 P50（span 口径）= 01IS205 线 14.3h（2N54Y- 14.1h，n=3064）/ 1L360RB 线 24.3h**——阶段计划「16–20h」与 BM 笔记 16.2h 与 span 口径不符（后者疑含 09IS360/350 出口后记录站尾段 ~1.5–2h）。S1 阈值改为 [8h, 30h] + 自洽检查。
2. **09IS360 / 09IS350 是出口后记录站**（68,262 事件，全部位于 K3IS140 之后）：基于原始流（未截断 exit）建 08/09 线 cohort 时 `LIKE '09IS%'` 对几乎所有主线车为真，cohort 被污染；**cohort 必须基于 `tmp_ev_base`（截断 exit）构建**。本计划 SQL_0809 已如此 ✓。
3. **p1 剖面（按行程位置）**：入口→行程 22% 平坦 0.064；24–40% 08/09 分支分裂（08IS 线 p1=0.017 vs 09IS 0.124，线别返工倾向不同——新发现）；42% 汇合后 0.064 平坦至 76%；78–80%（23IS 返工区）0.011–0.022；**82%+（25IS 及以后）0.000**。阶跃在行程 76–82% 后段，**不在** 15/13/14IS065 决策站簇（该簇是首条 flag=1 的记录处，97.5% 返工车，但 p1 在其后不降——车被打标后继续走到返工区）。前决策补偿机制不受影响（p 是逐站条件概率，按学得的用）；S2 改为「首条 flag 集中度 + 剖面 max/min」，不再断言硬编码站级阶跃。
4. **逐月返工率双峰 + 周内波动**（按入场月口径）：04 23.5% / 05 21.4% / 06 10.8% / 07 23.1% / 08 11.6% / 09 15.5%；08-03→08-09 那一周仅 6.4%（201/3155 车）。p 表是全窗平均 → 低返工期补偿过量、高返工期补偿不足，偏差方向随时段摆动；回放报告 flag 分桶诊断会显影。S3/S4 阈值（返工 ≥9,000 / 黑顶 ≥7,000 车）按 4 个月学习窗标定（学习窗预期 ≈9,070 台返工车）；**短窗干跑（如 1 周）S3/S4 预期 FAIL，属窗口样本量问题而非 bug**。
5. **2N54Y- 只走主线**（01IS205 入口，学习窗 28,811 车；1L360RB R 线属其他车型族）——08/09 等价线检查只需关注主线车型。
6. **psycopg2 规则（实测）**：带参执行的 SQL 中内联 LIKE 通配符必须写 `%%`——单个 `%` 会被 pqformat 破坏、查询语义改变（实测：`LIKE '08IS%'` + `%s` 参数 → 空集；`'08IS%%'` → 正确 207,942 行）；模式作为绑定参数、或 SQL 无参数时单个 `%` 正常。
7. **08/09 线 cohort（2N54Y-，span 口径，1 周窗）共享站剩余 P50 差 20–30%**（01IS205：12.9h vs 15.5h）——与五确「P50 近无偏」矛盾（该结论疑基于被 09IS360/350 污染的原始流 cohort 测出）。对门的影响：前分支共享站 ≈ 行程事件的前 24%，直接估计器对这些事件偏差 ~1.2–1.4h；但门是中位数（后段站 ≈76% 事件无偏），门大概率不受影响——回放报告必须用 cohort 误差分桶量化（见 Task 4）。

## File Structure

| 文件 | 责任 |
|---|---|
| `spike_w1/common.py` | DSN + `connect()`（自动 `SET TIME ZONE 'Asia/Shanghai'`、autocommit）+ 口径常量（上线/下线点、flag 值） |
| `spike_w1/sqlbase.py` | 共享 SQL 生成器 `ev_base_sql(start, end)` → `tmp_ev_base` 临时表（学习器与回放唯一共用口径） |
| `spike_w1/eta.py` | 纯估计逻辑：`Event` / `EventEta` / `resolve()`（两级回退）/ `estimate_vehicle()`（三估计器 + 环分流 + 前决策补偿）。无 DB 依赖 |
| `spike_w1/01_learn_takt.py` | 学习器 CLI：建 `dss` 六表、跑聚合 SQL、写 `dim_learner_meta`、S1–S6 自校验（不通过 exit 1）、sanity 报告写 `output/` |
| `spike_w1/02_replay_eta.py` | 回放 CLI：读学习表 + 回放窗事件流 → 逐车纯估计 → 三估计器对打、门判（exit 0/2）、08/09 等价线检查、报告 + CSV + gate JSON 写 `output/` |
| `tests/test_eta.py` | `eta.py` 纯逻辑单元测试（合成数据，7 个用例） |
| `tests/test_db_smoke.py` | 真实 DB 只读冒烟：tz 边界、flag 分布精确值、上下线点事件量（DB 不可达时自动 skip） |
| `environment.yml` | +`pytest` 依赖 |
| `output/` | 运行产物（gitignore；验证摘要例外强提） |

运行方式（仓库根目录）：

```bash
D:\000_software_install\miniconda3\envs\paint-dss\python.exe -m pytest tests/ -v
D:\000_software_install\miniconda3\envs\paint-dss\python.exe spike_w1\01_learn_takt.py            # 学习
D:\000_software_install\miniconda3\envs\paint-dss\python.exe spike_w1\02_replay_eta.py           # 回放
```

---

### Task 0: 清理工作区（W1 前置）

现状（2026-09-19 实测）：`git status` 有未提交内容——修改 3 个文件（`AGENTS.md` +1、`README.md` +2、`.claude/CLAUDE.md` +1）、未跟踪 `docs/`（含 `01-开发阶段计划.md` 与本计划）、未跟踪 33 字节 `nul`（Windows 重定向误产物，非本项目文件）。先让工作区干净，后续每个任务的 commit 才边界清晰。

**Files:**
- Commit: `docs/`、`AGENTS.md`、`README.md`、`.claude/CLAUDE.md`
- Delete（走删除闸门）: `nul`

**Interfaces:**
- Consumes: 无
- Produces: 干净工作区（HEAD 含 docs/ 与口径同步）

- [ ] **Step 1: 核对未提交 diff**

Run: `git status --short && git diff`
Expected: 仅上列 3 个文件 +4 行改动（AGENTS.md/CLAUDE.md 各 +1 Windows conda 运行坑；README.md +2 行 docs/ 目录），无意外内容。

- [ ] **Step 2: 向用户报告并提交（含删除清单，等明确确认）**

报告内容（一次性给用户）：
1. 拟 commit 文件清单：`docs/01-开发阶段计划.md`、`docs/superpowers/plans/2026-09-19-w1-takt-learner-eta-replay.md`、`AGENTS.md`、`README.md`、`.claude/CLAUDE.md`；拟用 message：`docs: W1 计划文档入库 + README 目录与 agent 运行说明同步`
2. 删除清单（删除闸门）：`F:\000_dev\Python\workplace\paint-dss\nul`（33 字节 Windows 重定向误产物，非项目文件；**`nul` 是 Windows 保留设备名，普通路径解析命中 NUL 设备而非该文件（实测：普通路径 Test-Path=False 且文件不可见，`\\?\` 扩展路径可见 33B），删除命令必须用 `\\?\` 前缀**：`Remove-Item -LiteralPath '\\?\F:\000_dev\Python\workplace\paint-dss\nul'`（已实测可用；cmd 等价 `del \?\F:\000_dev\Python\workplace\paint-dss\nul`），PowerShell 执行）。
3. 建议：`.gitignore` 追加一行 `nul` 防止再生（该文件将随下次 commit 提交，本次先改后报）。

得到用户明确确认后依次执行：

```bash
Remove-Item -LiteralPath '\\?\F:\000_dev\Python\workplace\paint-dss\nul'   # PowerShell：保留设备名必须 \\?\ 扩展路径（普通路径解析到 NUL 设备而失败）
echo nul >> .gitignore   # 或 edit 工具在 .gitignore 末尾追加一行
git add docs AGENTS.md README.md .claude/CLAUDE.md .gitignore
git commit -m "docs: W1 计划文档入库 + README 目录与 agent 运行说明同步"
```

删除 `nul` 后向用户报告已删路径（本项目外无其他删除）。

---

### Task 1: 测试基建 + DB 连接层（common.py）

pytest 尚未进环境；`common.py` 是所有 DB 访问的唯一入口（tz 强制、口径常量单一来源）。冒烟测试用真实 DB 只读查询钉死口径常量（数据存量不更新，可断言精确值）。

**Files:**
- Modify: `environment.yml`（dependencies 追加 `pytest`）
- Create: `spike_w1/common.py`
- Test: `tests/test_db_smoke.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `common.connect()` → psycopg2 连接（autocommit=True，已 `SET TIME ZONE 'Asia/Shanghai'`）
  - `common.DSN: dict`、`common.ENTRY_STATIONS: tuple[str, ...]`（3 个上线点）、`common.EXIT_STATIONS: tuple[str, ...]`（4 个下线点）
  - `common.FLAG_NORMAL/FLAG_REWORK/FLAG_BLACKTOP = "0"/"1"/"2"`

- [ ] **Step 1: 环境装 pytest 并固化依赖**

```bash
D:\000_software_install\miniconda3\envs\paint-dss\python.exe -m pip install pytest
```

`environment.yml` 的 `dependencies` 列表末尾追加一行：

```yaml
  - pytest
```

Run 验证: `D:\000_software_install\miniconda3\envs\paint-dss\python.exe -m pytest --version`
Expected: 输出版本号（如 `pytest 8.x`）。

- [ ] **Step 2: 写失败测试 `tests/test_db_smoke.py`**

```python
# -*- coding: utf-8 -*-
"""DB 集成冒烟：真实 DB 只读。钉死 tz / 引号 / 口径常量（数据为存量，精确值可断言）。
DB 不可达时自动 skip（公网环境 DB 正常，正常路径不应触发）。"""
import socket
from datetime import datetime, timedelta, timezone

import pytest

import spike_w1.common as common


def _db_up() -> bool:
    try:
        socket.create_connection(("127.0.0.1", 5432), timeout=2).close()
        return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not _db_up(), reason="analytics_db 不可达")

CST = timezone(timedelta(hours=8))


def test_tz_and_window_bounds():
    conn = common.connect()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT min("DATE_EVT") FROM ods.carbody_history')
            lo = cur.fetchone()[0]
    finally:
        conn.close()
    assert lo == datetime(2026, 4, 14, 16, 36, 51, 210000, tzinfo=CST)


def test_flag_distribution_product_vehicles():
    conn = common.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT SUBSTRING("MDS_DATA" FROM 139 FOR 1), count(*) '
                'FROM ods.carbody_history WHERE "BODY_ID" LIKE \'78202%\' '
                'GROUP BY 1')
            got = dict(cur.fetchall())
    finally:
        conn.close()
    assert got == {"0": 4768035, "1": 416905, "2": 182720, "-": 4233, "3": 2}


def test_entry_exit_station_counts():
    conn = common.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                'SELECT "RW_STATION_ID", count(*) FROM ods.carbody_history '
                'WHERE "BODY_ID" LIKE \'78202%%\' '   # 带参执行：% 必须 %% 转义
                '  AND "RW_STATION_ID" IN (%s, %s, %s, %s, %s, %s, %s) '
                'GROUP BY 1',
                common.ENTRY_STATIONS + common.EXIT_STATIONS)
            got = dict(cur.fetchall())
    finally:
        conn.close()
    assert got["01IS205"] >= 68000
    assert got["K3IS140"] >= 64000
    assert set(common.ENTRY_STATIONS + common.EXIT_STATIONS) <= set(got)
```

- [ ] **Step 3: 运行测试确认失败**

Run: `D:\000_software_install\miniconda3\envs\paint-dss\python.exe -m pytest tests/test_db_smoke.py -v`
Expected: FAIL，`ModuleNotFoundError: No module named 'spike_w1.common'`（或 collection error）。

- [ ] **Step 4: 实现 `spike_w1/common.py`**

```python
# -*- coding: utf-8 -*-
"""DB 连接与口径常量（W1 单一事实源，值见 BM 笔记 2026-09-19 拍板记录）。"""
import psycopg2

DSN = dict(host="127.0.0.1", port=5432, dbname="analytics_db",
           user="root", password="root")

# 3 上线点（2026-09-19 用户提供 + 数据验证：98.9% 车恰经其一）
ENTRY_STATIONS = ("01IS205", "1L360RB", "01IS045")
# 4 下线点（= dim_vehicle_profile.retention_checkpoint，2026-09-19 确认）
EXIT_STATIONS = ("K3IS140", "1J440RB", "K2IS075", "K1IS135")

# MDS_DATA 第 139 位（1-based）返修/黑顶标志
FLAG_NORMAL, FLAG_REWORK, FLAG_BLACKTOP = "0", "1", "2"


def connect():
    """analytics_db 连接（psycopg2.extensions.connection）：autocommit + 会话时区 Asia/Shanghai（工厂时 = UTC+8，必须）。"""
    conn = psycopg2.connect(**DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Asia/Shanghai'")
    return conn
```

- [ ] **Step 5: 运行测试确认通过**

Run: `D:\000_software_install\miniconda3\envs\paint-dss\python.exe -m pytest tests/test_db_smoke.py -v`
Expected: 3 passed（flag 分布精确值命中，说明 tz/引号/列名口径全对）。

- [ ] **Step 6: Commit（需用户确认）**

向用户报告：变更文件 `environment.yml`、`spike_w1/common.py`、`tests/test_db_smoke.py`；拟用 message：`W1: 测试基建 + DB 连接层（tz 强制 / 口径常量 / 冒烟测试）`。确认后：

```bash
git add environment.yml spike_w1/common.py tests/test_db_smoke.py
git commit -m "W1: 测试基建 + DB 连接层（tz 强制 / 口径常量 / 冒烟测试）"
```

---

### Task 2: 纯估计逻辑 `eta.py`（TDD，三估计器对打核心）

`eta.py` 是全项目唯一含估计决策的代码：直接剩余分布（主候选）+ 环分流 + 前决策补偿，以及两个诊断用 Σ 估计器（Σ段中位数 / Σ段均值）。纯函数、无 DB 依赖，先写 7 个合成数据测试再实现。

**Files:**
- Create: `spike_w1/eta.py`
- Test: `tests/test_eta.py`

**Interfaces:**
- Consumes: 无（纯标准库）
- Produces（Task 4 回放依赖，签名逐字）：
  - `eta.ALL = "ALL"`（回退行的车型标记）
  - `eta.Event(t: int, station: str, flag: str)`（frozen dataclass，位置参序 t, station, flag）
  - `eta.EventEta(t, station, flag, remaining_actual, eta_direct, eta_sum_median, eta_sum_mean)`（frozen dataclass；三个 eta 为**绝对 epoch 秒** int 或 None）
  - `eta.resolve(table: dict, key: tuple, model: str, stat_idx: int = 0, min_n: int = 30) -> float | None`——`key` 不含车型，车型按 `key + (model,)` 拼接查表；回退链 `(model) → ('ALL')`，`row[-1]`（n）< min_n 或缺行则降级，两级都不可用返回 None
  - `eta.estimate_vehicle(events, exit_t, tables, model) -> list[EventEta]`——`events` = 该车窗口内完整行程**含下线点事件**；返回逐入口（不含下线事件本身）；`tables` 为 5 个 dict 组成的 dict（键与行形状见下）
  - 表行约定（回放加载器必须照此构建，**n 恒为行尾**）：
    - `tables["rem"]`：`{(station, model): (p50, mean, p90, n)}` ← `dss.dim_remaining_time`
    - `tables["loop_rem"]`：`{(station, flag, model): (p50, mean, p90, n)}` ← `dss.dim_loop_remaining`
    - `tables["fut"]`：`{(station, model): (p1, p2, n)}` ← `dss.dim_future_flag`
    - `tables["loop_dur"]`：`{(flag, model): (l, n)}` ← `dss.dim_loop_duration`
    - `tables["takt"]`：`{(frm, to, model): (p50, mean, p90, n)}` ← `dss.dim_station_takt`

- [ ] **Step 1: 写失败测试 `tests/test_eta.py`（7 用例全量代码）**

```python
# -*- coding: utf-8 -*-
"""eta.py 纯逻辑单元测试（合成数据）。"""
from spike_w1 import eta


def _tables(rem=None, loop_rem=None, fut=None, loop_dur=None, takt=None):
    return {"rem": rem or {}, "loop_rem": loop_rem or {}, "fut": fut or {},
            "loop_dur": loop_dur or {}, "takt": takt or {}}


def test_resolve_fallback_chain():
    table = {("s", "m1"): (100, 110, 120, 50), ("s", "ALL"): (200, 210, 220, 500)}
    assert eta.resolve(table, ("s",), "m1") == 100            # 车型行 n>=30 命中
    assert eta.resolve(table, ("s",), "m_unknown") == 200     # 缺车型行 → ALL
    table2 = {("s", "m1"): (100, 110, 120, 29), ("s", "ALL"): (200, 210, 220, 500)}
    assert eta.resolve(table2, ("s",), "m1") == 200           # 车型行 n<30 → ALL
    assert eta.resolve({("s", "m1"): (100, 110, 120, 29)}, ("s",), "m1") is None  # 无 ALL 且 n<30
    assert eta.resolve({}, ("s",), "m1") is None              # 全缺


def test_direct_main_and_model_fallback():
    events = [eta.Event(0, "s", "0"), eta.Event(100, "EXIT", "0")]
    rem = {("s", "m1"): (500, 520, 600, 50), ("s", "ALL"): (800, 830, 900, 500)}
    assert eta.estimate_vehicle(events, 100, _tables(rem=rem), "m1")[0].eta_direct == 500
    assert eta.estimate_vehicle(events, 100, _tables(rem=rem), "m_rare")[0].eta_direct == 800
    # ALL 也缺 → 无覆盖
    assert eta.estimate_vehicle(events, 100, _tables(rem={("s", "m1"): (500, 520, 600, 50)}),
                                "m_rare")[0].eta_direct is None


def test_loop_flag_uses_loop_distribution_not_main():
    events = [eta.Event(0, "s1", "1"), eta.Event(1000, "EXIT", "0")]
    tables = _tables(
        rem={("s1", "m"): (500, 520, 600, 50)},          # 主分布存在但不得用
        loop_rem={("s1", "1", "m"): (900, 950, 1000, 40)},
        fut={("s1", "m"): (0.5, 0.0, 50)},               # 高 p 也不得补偿（已在环内）
        loop_dur={("1", "m"): (1000, 40)},
    )
    out = eta.estimate_vehicle(events, 1000, tables, "m")
    assert out[0].eta_direct == 900                       # t + P50(环) = 0 + 900


def test_pre_decision_compensation():
    events = [eta.Event(0, "up", "0"), eta.Event(100, "late", "0"), eta.Event(500, "EXIT", "0")]
    tables = _tables(
        rem={("up", "m"): (500, 520, 700, 50), ("late", "m"): (400, 410, 500, 50)},
        fut={("up", "m"): (0.2, 0.1, 50), ("late", "m"): (0.0, 0.0, 50)},
        loop_dur={("1", "m"): (3600, 40), ("2", "m"): (1800, 30)},
    )
    out = eta.estimate_vehicle(events, 500, tables, "m")
    # up: t=0 + 500 + 0.2*3600 + 0.1*1800 = 1400
    assert out[0].eta_direct == 1400
    # late（决策站下游 p≈0）: t=100 + 400，无补偿
    assert out[1].eta_direct == 500


def test_sum_median_underestimates_vs_mean():
    # 中位数不可加演示：每段 P50=100 但均值 600（右偏排队段），实际 600s/段
    stations = ["a", "b", "c", "d", "e"]
    events = [eta.Event(600 * i, s, "0") for i, s in enumerate(stations)]
    takt = {("a", "b", "m"): (100, 600, 900, 50), ("b", "c", "m"): (100, 600, 900, 50),
            ("c", "d", "m"): (100, 600, 900, 50), ("d", "e", "m"): (100, 600, 900, 50)}
    tables = _tables(rem={("a", "m"): (2500, 2400, 2600, 50)}, takt=takt)
    out = eta.estimate_vehicle(events, 3000, tables, "m")
    e0 = out[0]
    assert e0.eta_sum_median == 400                       # 4×P50，低估 2600s
    assert e0.eta_sum_mean == 2400                        # 4×均值
    assert abs(e0.eta_sum_median - 3000) > abs(e0.eta_sum_mean - 3000) > abs(e0.eta_direct - 3000)


def test_sum_estimators_all_none_when_segment_missing():
    events = [eta.Event(0, "a", "0"), eta.Event(100, "b", "0"), eta.Event(200, "EXIT", "0")]
    tables = _tables(
        rem={("a", "m"): (100, 110, 120, 50), ("b", "m"): (80, 85, 90, 50)},
        takt={("a", "b", "m"): (50, 60, 70, 50)})          # (b, EXIT) 段缺失
    out = eta.estimate_vehicle(events, 200, tables, "m")
    assert all(e.eta_sum_median is None for e in out)     # 整车 None（样本可比性）
    assert all(e.eta_sum_mean is None for e in out)
    # 直接估计不受影响：ETA 为绝对时刻 = t + 剩余估计（100 = 0+100；180 = 100+80）
    assert out[0].eta_direct == 100 and out[1].eta_direct == 180


def test_exit_event_not_in_sample():
    events = [eta.Event(0, "a", "0"), eta.Event(100, "b", "0"), eta.Event(200, "EXIT", "0")]
    tables = _tables(rem={("a", "m"): (100, 110, 120, 50), ("b", "m"): (80, 85, 90, 50)})
    out = eta.estimate_vehicle(events, 200, tables, "m")
    assert len(out) == 2                                   # 下线事件本身不进样本
    assert out[0].remaining_actual == 200
    assert out[1].remaining_actual == 100
```

- [ ] **Step 2: 运行测试确认失败**

Run: `D:\000_software_install\miniconda3\envs\paint-dss\python.exe -m pytest tests/test_eta.py -v`
Expected: FAIL，`ImportError: cannot import name 'eta'`（模块不存在）。

- [ ] **Step 3: 实现 `spike_w1/eta.py`**

```python
# -*- coding: utf-8 -*-
"""纯估计逻辑：三估计器对打（无 DB 依赖，单元测试覆盖）。

口径（BM 笔记 2026-09-19 六确）：
- 直接剩余分布（主候选）：flag∈{1,2} → 环分布 P50；flag=0 → 主分布 P50（干净后缀学出）
  + 前决策补偿 p(车型,站)×L(车型,环)（返工/黑顶独立相加）。
- Σ段中位数 / Σ段均值：沿车**实际经过**的窗口内路径求和（后见路径，纯诊断用途——
  验证中位数不可加 / 均值可加，非生产口径）。
- 回退链：车型行 n≥30 → 'ALL' 行 n≥30 → 无覆盖（None）。
表行形状（n 恒在行尾）：
  rem:      {(station, model): (p50, mean, p90, n)}
  loop_rem: {(station, flag, model): (p50, mean, p90, n)}
  fut:      {(station, model): (p1, p2, n)}
  loop_dur: {(flag, model): (l, n)}
  takt:     {(frm, to, model): (p50, mean, p90, n)}
"""
from dataclasses import dataclass
from typing import Optional, Sequence

ALL = "ALL"


@dataclass(frozen=True)
class Event:
    t: int            # epoch 秒（任意单调基准，全链路一致即可）
    station: str
    flag: str         # '0' | '1' | '2'


@dataclass(frozen=True)
class EventEta:
    t: int
    station: str
    flag: str
    remaining_actual: int        # exit_t - t（秒）
    eta_direct: Optional[int]    # 绝对 epoch 秒；None = 无覆盖
    eta_sum_median: Optional[int]
    eta_sum_mean: Optional[int]


def resolve(table: dict, key: tuple, model: str,
            stat_idx: int = 0, min_n: int = 30) -> Optional[float]:
    """两级回退取统计量：(key, model) → (key, 'ALL')；行尾 n < min_n 视为不可用。"""
    for m in (model, ALL):
        row = table.get(key + (m,))
        if row is not None and row[-1] >= min_n:
            return row[stat_idx]
    return None


def estimate_vehicle(events: Sequence[Event], exit_t: int,
                     tables: dict, model: str) -> list[EventEta]:
    """逐事件 ETA。events = 完整窗口内行程 [entry, ..., exit]（含下线点事件）。
    返回 len(events)-1 条（下线事件本身 remaining=0，无预测信息，不进样本）。
    Σ 估计器按整车 all-or-none：任一段无覆盖 → 该车两个 Σ 估计器全 None。
    """
    n = len(events)

    # --- Σ 估计器：沿实际路径的段级统计（后见路径，诊断用途） ---
    segs = []
    seg_ok = True
    for a, b in zip(events, events[1:]):
        p50 = resolve(tables["takt"], (a.station, b.station), model, stat_idx=0)
        mean = resolve(tables["takt"], (a.station, b.station), model, stat_idx=1)
        if p50 is None or mean is None:
            seg_ok = False
            break
        segs.append((p50, mean))
    if seg_ok:
        ssum_med = [0.0] * (n - 1)
        ssum_mean = [0.0] * (n - 1)
        for i in range(n - 2, -1, -1):          # 后缀和：ssum[i] = Σ segs[i:]
            prev_med = ssum_med[i + 1] if i + 1 < n - 1 else 0.0
            prev_mean = ssum_mean[i + 1] if i + 1 < n - 1 else 0.0
            ssum_med[i] = prev_med + segs[i][0]
            ssum_mean[i] = prev_mean + segs[i][1]
    else:
        ssum_med = ssum_mean = None

    out = []
    for i, ev in enumerate(events):
        if i == n - 1:
            continue                            # 下线事件本身不进样本
        if ev.flag in ("1", "2"):               # 环内：直接用环分布，不加补偿
            base = resolve(tables["loop_rem"], (ev.station, ev.flag), model)
            eta_direct = ev.t + int(round(base)) if base is not None else None
        else:                                   # flag=0：主分布 + 前决策补偿
            base = resolve(tables["rem"], (ev.station,), model)
            if base is None:
                eta_direct = None
            else:
                p1 = resolve(tables["fut"], (ev.station,), model, stat_idx=0) or 0.0
                p2 = resolve(tables["fut"], (ev.station,), model, stat_idx=1) or 0.0
                l1 = resolve(tables["loop_dur"], ("1",), model) or 0.0
                l2 = resolve(tables["loop_dur"], ("2",), model) or 0.0
                eta_direct = ev.t + int(round(base + p1 * l1 + p2 * l2))
        eta_med = int(round(ev.t + ssum_med[i])) if ssum_med is not None else None
        eta_mean = int(round(ev.t + ssum_mean[i])) if ssum_mean is not None else None
        out.append(EventEta(t=ev.t, station=ev.station, flag=ev.flag,
                            remaining_actual=exit_t - ev.t, eta_direct=eta_direct,
                            eta_sum_median=eta_med, eta_sum_mean=eta_mean))
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `D:\000_software_install\miniconda3\envs\paint-dss\python.exe -m pytest tests/test_eta.py -v`
Expected: 7 passed。再跑全量：`D:\000_software_install\miniconda3\envs\paint-dss\python.exe -m pytest tests/ -v` → 10 passed。

- [ ] **Step 5: Commit（需用户确认）**

向用户报告：变更文件 `spike_w1/eta.py`、`tests/test_eta.py`；拟用 message：`W1: 纯估计逻辑（三估计器对打 + 两级回退 + 环分流 + 前决策补偿）`。确认后：

```bash
git add spike_w1/eta.py tests/test_eta.py
git commit -m "W1: 纯估计逻辑（三估计器对打 + 两级回退 + 环分流 + 前决策补偿）"
```

---

### Task 3: 学习器 `01_learn_takt.py`（五张学习表 + 元数据 + 自校验）

学习器把「五步机制」（过滤 → 分段 → 清洗 → 聚合 → 自校准的离线首跑）落成 SQL：`tmp_ev_base` 临时表承载前四步的中间结果，五张 `dss` 表 + meta 表是产出。自校验 S1–S6 不通过则 exit 1（不产出即不合格）。

**Files:**
- Create: `spike_w1/sqlbase.py`
- Create: `spike_w1/01_learn_takt.py`

**Interfaces:**
- Consumes: `common.connect()`、`common.ENTRY_STATIONS`、`common.EXIT_STATIONS`（Task 1）
- Produces（Task 4 回放读取，DDL 逐字）：
  - `sqlbase.ev_base_sql(win_start, win_end) -> str`：两段 `%s` 占位（start, end，aware datetime）；执行后产生会话临时表 `tmp_ev_base`，列：`id BIGINT, body TEXT, bt TEXT, station TEXT, t TIMESTAMPTZ, flag TEXT, entry_t TIMESTAMPTZ, exit_t TIMESTAMPTZ, last_loop_t TIMESTAMPTZ`（窗口内产品车、entry/exit 事件均在窗口内的车、同站去重后的行程事件；`last_loop_t` = 该车最后一条 flag∈{1,2} 事件时刻，无则 NULL）
  - `dss.dim_remaining_time (body_type TEXT, station TEXT, p50 BIGINT, mean BIGINT, p90 BIGINT, n INT, PK(body_type, station))`
  - `dss.dim_loop_remaining (body_type TEXT, flag TEXT, station TEXT, p50 BIGINT, mean BIGINT, p90 BIGINT, n INT, PK(body_type, flag, station))`
  - `dss.dim_station_takt (frm TEXT, to_station TEXT, body_type TEXT, p50 BIGINT, mean BIGINT, p90 BIGINT, n INT, PK(frm, to_station, body_type))`
  - `dss.dim_future_flag (body_type TEXT, station TEXT, p1 DOUBLE PRECISION, p2 DOUBLE PRECISION, n INT, PK(body_type, station))`
  - `dss.dim_loop_duration (body_type TEXT, flag TEXT, l BIGINT, n INT, PK(body_type, flag))`
  - `dss.dim_learner_meta (key TEXT PK, value TEXT)`——键：`win_start, win_end, learned_at, n_vehicles, n_events, n_flag0_events, n_flag1_events, n_flag2_events, n_clean_suffix_events, clean_suffix_rate, n_dedup_removed, takt_n_total, takt_n_removed, takt_iqr_excl_rate`
  - 所有表含 `body_type='ALL'`（或对应 `'ALL'` 位）的全体车型回退行

- [ ] **Step 1: 写 `spike_w1/sqlbase.py`**

```python
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
```

> 注意：psycopg2 的 `%` 转义——`LIKE '78202%%'` 中的 `%%` 在传参执行时还原为单个 `%`（psycopg2 参数化要求）。

- [ ] **Step 2: 写 `spike_w1/01_learn_takt.py` 骨架 + DDL**

```python
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
```

- [ ] **Step 3: 追加五张表的聚合 SQL 常量**

以下 5 段 SQL 追加到同一文件（均为模块级常量字符串，`cur.execute(常量)` 执行；**除 3f `SQL_DEDUP_REMOVED` 带参 `(start, end)`（已按 `%%` 规则转义）外均无参数**）：

**3a. `SQL_REMAINING`（主分布 = 干净后缀；车型行 + ALL 行）**

```python
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
```

**3b. `SQL_LOOP_REMAINING`（环分布：车型×标志×站）**

```python
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
```

**3c. `SQL_TAKT_MODEL` + `SQL_TAKT_ALL`（段级诊断；Q3+3×IQR 桶内清洗，夜停跨窗段由桶内离群剔除吸收；ALL 行用全体车型 fence 独立计算）**

```python
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
```

**3d. `SQL_FUTURE_FLAG`（前决策补偿概率 p：该站 flag=0 事件后续首次被打 1/2 的条件概率，车型行 + ALL 行）**

```python
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
```

**3e. `SQL_LOOP_DURATION`（环时长 L = 每车×标志 首末 flag 事件时差的中位数；两标志兼有车辆独立贡献两行）**

```python
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
```

**3f. `SQL_DEDUP_REMOVED`（去重移除数 = span 原始行数 − tmp_ev_base 行数）与 `SQL_TAKT_EXCL`（IQR 剔除率：总 + 分桶 top20）**

```python
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
```

> **psycopg2 % 规则（2026-09-19 干跑实测）**：带参执行的 SQL 中内联 `LIKE` 通配符必须写 `%%`——单个 `%` 会被 pqformat 破坏、查询语义改变（实测：`LIKE '08IS%'` + `%s` 参数返回空集，改 `'08IS%%'` 返回正确 207,942 行）；模式作为绑定参数或 SQL 无参数时单个 `%` 正常。本计划 `SQL_DEDUP_REMOVED`（参数 `(start, end)`）用 `%%`；`SQL_TAKT_EXCL` / `SQL_TAKT_EXCL_TOP20` / `SQL_0809` 与 S 校验 SQL 无参数，用单 `%`。

- [ ] **Step 4: 追加自校验 S1–S6 与 main()**

```python
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
```

> 注：`excl_rate` 来自 SQL 内 `::TEXT` 转换，meta 表直接存文本；S5 读取时再 `value::FLOAT8` 转回。

- [ ] **Step 5: 运行学习器**

Run: `D:\000_software_install\miniconda3\envs\paint-dss\python.exe spike_w1\01_learn_takt.py`
Expected: 数分钟完成（6 次全表窗扫描 + 聚合）；S1–S6 全 PASS；exit 0；`output/w1_learn_sanity_*.md` 生成。
S1 不过：先看报告「同窗行程 P50」节——行程 P50 本身超出 [8h, 30h] 则是 entry/exit 点口径可疑；行程 P50 在范围内但自洽偏差 >10% 则主分布（干净后缀）口径有问题。如实上报，不放松阈值。
S2a/S2b 不过（簇占比 < 阈值）：看报告「首条 flag 站分布」节——决策簇可能已迁移（六确簇站是 2026-04→09 的实测值）；确为新簇迁移则把 `REWORK_CLUSTER`/`BLACKTOP_CLUSTER` 常量改为实测新簇，重跑并在报告中记录改簇理由；若首条 flag 分散无簇，则是标志语义变化，如实上报不硬过。
S2c 不过：看报告「p1 剖面」节确认阶跃形态；max<0.03 说明返工率低到补偿无意义（回查前决策补偿假设），min>0.01 说明后段仍有决策（拓扑变化）。不放松阈值，如实上报。
S5 > 30% 同理：看 top-20 分桶定位异常段，如实上报。

- [ ] **Step 6: 人工复核 sanity 报告**

读 `output/w1_learn_sanity_YYYYMMDD.md`，逐项确认：入口站 P50（干跑实测参照：01IS205 线 ≈14.3h、1L360RB 线 ≈24.3h，且与同窗行程 P50 偏差 ≤10%）；首条 flag=1 集中于 15IS065/14IS065（占比应 ~97%）；p1 剖面在行程 76–82% 后段阶跃、24–40% 处 08/09 分支分裂（0.017 vs 0.124 为正常形态，非异常）；环时长 L 返工 > 黑顶（返工 ~32 事件/车 vs 黑顶 ~18 站次/车，量级应明显不同）。把复核结论（含任何异常）一并带入 Step 7 的用户报告。

- [ ] **Step 7: Commit（需用户确认）**

向用户报告：变更文件 `spike_w1/sqlbase.py`、`spike_w1/01_learn_takt.py` + sanity 报告关键数字（S1–S6 结果、入口站 P50、p 阶跃、L 量级）；拟用 message：`W1: 节拍学习器 v1，五张学习表 + 元数据 + 自校验产出`。确认后：

```bash
git add spike_w1/sqlbase.py spike_w1/01_learn_takt.py
git commit -m "W1: 节拍学习器 v1，五张学习表 + 元数据 + 自校验产出"
```

（`output/` 下报告不入库——gitignore；若 Step 6 有换站/改阈值，改动随本 commit 一起报。）

---

### Task 4: 回放 `02_replay_eta.py`（三估计器对打 + 门判 + 08/09 检查）

回放窗内逐车用学习窗的表算 ETA，与实际下线时刻对打。门判指标 = **直接剩余分布估计器**的「逐车中位 |err| 再跨车取中位」≤ 1800s。失败时报告自带按车型/站/剩余时长分桶的诊断（失败判据：定位误差源，未诊断清楚不进 W2）。

**Files:**
- Create: `spike_w1/02_replay_eta.py`

**Interfaces:**
- Consumes: `common.connect()`、`sqlbase.ev_base_sql()`（Task 1/3）、`eta.Event/EventEta/estimate_vehicle` 与 5 张表行形状（Task 2/3）
- Produces:
  - CLI `--learn-end`（默认 `2026-08-15`，回放窗 = 其后一天起）、`--win-end`（默认 `2026-09-18`）
  - `output/w1_replay_report_YYYYMMDD.md`（门判 + 三估计器对比 + 诊断分桶 + 08/09 检查）
  - `output/w1_replay_errors_YYYYMMDD.csv`（逐事件明细，gitignore）
  - `output/w1_replay_by_station_YYYYMMDD.csv`（站级汇总，gitignore）
  - `output/w1_replay_gate_YYYYMMDD.json`（门判机器可读）
  - exit code：0 = 门过，2 = 门未过（报告照常产出）

- [ ] **Step 1: 写 `spike_w1/02_replay_eta.py` 全量代码**

```python
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
```

- [ ] **Step 2: 运行回放**

Run: `D:\000_software_install\miniconda3\envs\paint-dss\python.exe spike_w1\02_replay_eta.py`
Expected: 1–3 分钟（回放窗 ~34 天 ≈ 1.1M 事件 / ~1.9 万车）；报告 + CSV + JSON 产出；exit 0（门过）或 2（门未过——**不要**在门未过时修改估计逻辑，先走 Step 3 诊断）。
前置检查：若 exit 1 报 KeyError/缺表，先重跑 Task 3 学习器（回放只读 `dss` 表，不重建）。

- [ ] **Step 3: 门判结果处理**

**门过（exit 0）**：人工读报告核对三件事后进入 Step 4：
1. Σ段中位数确实大幅低估（中位 |err| 远大于另两者，量级应接近 BM 笔记实测的 ~5× 全程低估）——若 Σ中位数反而最优，说明 takt 或 remaining 表口径出了问题，回查 Task 3 sanity 报告；
2. 直接估计器按 flag 分桶：flag=1/2 组的中位 |err| 应与 flag=0 组同量级（环分流生效的证据）；
3. 08/09 检查：2N54Y- rel_diff 中位预期 >0.10（干跑实测 0.20–0.30）——核对 cohort 误差分桶（08IS/09IS/other）是否量化了前分支共享站偏差；门过时把共享站混合偏差记为已知偏差带入 W2；门未过且误差集中于前分支共享站则以其为首要误差源。

**门未过（exit 2）**：按失败判据做误差源定位（这是本步骤的核心产出，结论写进报告「结论」节并记入 BM 笔记）：
1. 看 `w1_replay_gate_*.json` 与报告分桶：误差集中在哪些车型 / 站 / 剩余时长桶 / flag 组；
2. 对 top 误差站，取 `w1_replay_errors_*.csv` 该车次明细，对照 `dss.dim_remaining_time` 该行 P50/P90/n 判断是「样本少回退 ALL 偏差」还是「分布本身漂移」；
3. 定位结论（哪类误差、占多少、是否可被 W2 班次级 ±10% 预算吸收）写入报告后，向用户如实上报，**未诊断清楚不进 W2**。

- [ ] **Step 4: Commit（需用户确认）**

向用户报告：变更文件 `spike_w1/02_replay_eta.py` + 门判数字（gate 中位误差、三估计器对比、覆盖、08/09 结论）；拟用 message：`W1: 回放 v1，三估计器对打 + 门判报告 + 08/09 等价线检查`。确认后：

```bash
git add spike_w1/02_replay_eta.py
git commit -m "W1: 回放 v1，三估计器对打 + 门判报告 + 08/09 等价线检查"
```

---

### Task 5: W1 验证摘要 + 文档/知识库同步

验收项「验证摘要写入 `output/` 并 commit」+ 阶段计划/README 现状更新 + BM 线程笔记沉淀（决策与实测结论按 Startup Router 规则 4：写前重读、保留原结构、只追加）。

**Files:**
- Create: `output/w1-验证摘要.md`（`git add -f` 强提）
- Modify: `docs/01-开发阶段计划.md`（「现状」节）
- Modify: `README.md`（目录节下方加一行 W1 结果指向摘要 + 口径表 08IS/09IS 行按实测修正）
- BM 笔记：`basic-memory/projects/paint-dss/涂装车间DSS-方案设计与开发路线`（MCP 写入）

**Interfaces:**
- Consumes: Task 3/4 的报告与 gate JSON
- Produces: W1 收尾状态（工作区干净、知识库同步、BM Task Board W1 任务状态更新）

- [ ] **Step 1: 写 `output/w1-验证摘要.md`**

从 `output/w1_replay_report_*.md` 与 `w1_learn_sanity_*.md` 摘出，结构固定：

```markdown
# W1 验证摘要（节拍学习器 + 单车 ETA 回放）

- 日期 / 学习窗 / 回放窗
- **门判**：单车 ETA 中位误差 = X min（≤30min 门）→ 通过/未通过
- 三估计器对比（Σ中位数 / Σ均值 / 直接剩余分布 的中位 |err| 一行表）
- sanity 结论：入口站 P50（实测参照 14.3h/24.3h）+ 同窗行程 P50 自洽、首条 flag 簇占比、p1 剖面阶跃、环表 n、IQR 剔除率
- 08/09 线检查：2N54Y- rel_diff 中位、cohort 误差分桶、对门的影响（干跑预发现：五确「P50 近无偏」在 span 口径下不成立，W2 需共享站加线别 cohort 维度）
- 已知偏差与 W2 影响（含逐月返工率双峰、前分支共享站混合偏差；若有门未过：误差源定位结论）
- 产物位置：dss 六表 + output/ 报告文件名
```

- [ ] **Step 2: 更新 `docs/01-开发阶段计划.md` 与 `README.md`**

- `docs/01-开发阶段计划.md`「现状」节：`⏳ W1 进行中` 行改为
  `- ✅ W1 完成（YYYY-MM-DD）：单车 ETA 中位误差 X min（门 ≤30min 通过/未通过+诊断结论），产物 dss 六表 + output/w1-验证摘要.md`
- `README.md` 目录节 `spike_w1/` 描述后追加一行：
  `  - W1 结果见 \`output/w1-验证摘要.md\`（门判 + 三估计器对比）`
- `README.md` 口径对照表 `08IS/09IS 并行线` 行：「W1 回放顺带确认分叉前共享站 P50 近无偏」改为按实测记录：
  `| 08IS/09IS 并行线 | 等价工艺线（负荷分派），无需分桶；**W1 实测：分叉前共享站存在线别 P50 差**（2N54Y- rel_diff ~0.20–0.30，五确「近无偏」在 span 口径下不成立，细节见 \`output/w1-验证摘要.md\` cohort 分桶） |`

- [ ] **Step 3: 更新 BM 线程笔记 + Task Board（MCP，写前重读）**

1. `basic-memory_read_note(project="basic-memory", identifier="basic-memory/projects/paint-dss/涂装车间DSS-方案设计与开发路线")` 重读当前全文（写前必读，保留原结构）；
2. 追加（不删改既有内容）：
   - Context 行尾的日期/事件序列追加「2026-09-19 W1 spike 完成（门判结果…）」；
   - Observations 追加 2–4 条：
     `[status] W1 回放门判（2026-09-19）：单车 ETA 中位误差 X min（≤/＞30min），三估计器对比 Σ中位数 A / Σ均值 B / 直接 C；dss 六表已建 #paint-dss`
     `[insight] （实测新发现，如 Σ中位数低估倍数实测值、环分流后 flag 组误差形态、08/09 共享站 rel_diff 实测中位）#paint-dss`
     （如有口径修订或踩坑，对应追加 `[decision]` / `[problem]`）
3. Task Board：按 Startup Router 任务型工作加载 `memory://instructions/task-instructions` + `[[Task Board]]`，把 W1 任务（前缀 `paint-dss:`）状态更新为完成并附日期与门判数字；若 Task Board 无 W1 任务则新建一条「paint-dss: W2 班次级回放（开工条件：W1 门过 + 误差源已诊断）」并双向链接本线程笔记。
4. 所有 BM 调用显式 `project: "basic-memory"`；overwrite 必须基于 Step 1 重读到的当前内容全量重写（保留 changelog / observations 原有行）。

- [ ] **Step 4: Commit（需用户确认）**

向用户报告：变更文件 `output/w1-验证摘要.md`（`git add -f`）、`docs/01-开发阶段计划.md`、`README.md`；拟用 message：`W1: 验证摘要 + 阶段计划/README 现状同步`。确认后：

```bash
git add -f output/w1-验证摘要.md docs/01-开发阶段计划.md README.md
git commit -m "W1: 验证摘要 + 阶段计划/README 现状同步"
```

---

## Self-Review（写计划时自查结果）

1. **Spec 覆盖**：
   - 五张学习表 + meta → Task 3（`dim_remaining_time` 干净后缀 ✓、`dim_loop_remaining` 环分布 ✓、`dim_station_takt` LAG 差分 + Q3+3×IQR 清洗 ✓、`dim_future_flag` p ✓、`dim_loop_duration` L ✓、`dim_learner_meta` 窗口/样本量/剔除率 ✓）
   - 五步机制中的「自校准（60 天滚动窗每日重学 + P50 漂移 >20% 告警）」→ W3 在线化范围，W1 离线首跑不实现（阶段计划 W1 范围未含，非缺口）
   - 三估计器对打 + 因果切分 → Task 2（逻辑）+ Task 4（回放）✓
   - 前决策补偿 → Task 2 `estimate_vehicle` + Task 3 `dim_future_flag`/`dim_loop_duration` ✓
   - 验收门 ≤30min → Task 4 门判（exit 0/2）✓；sanity（入口站 P50 自洽 / 首条 flag 集中度 / p1 剖面 / 环表 n）→ Task 3 S1–S6（阈值依据「干跑校准结论」节）✓
   - 08IS/09IS 顺带确认 → Task 4 `SQL_0809` ✓
   - 失败判据（分桶定位、未诊断不进 W2）→ Task 4 Step 3 ✓
   - 「验证摘要写入 output/ 并 commit」→ Task 5（`-f` 强提，与 gitignore 的矛盾已显式裁决）✓
   - 「三级回退」第三级（区域级）依赖 249 区↔站映射（用户输入 #8 未到）→ W1 实现两级（车型→ALL），已在 Task 2 接口注释标明；非缺口，W3 映射到位后补
2. **Placeholder 扫描**：无 TBD/TODO；所有代码步含完整代码（带参 SQL 的 `%%` 与无参 SQL 的单 `%` 转义规则已在 Task 3 Step 3f 注释与 Global Constraints 明示，非占位符）。
3. **类型一致性**：`eta` 表行形状（n 行尾、车型 key 末位）在 Task 2 接口、Task 3 DDL、Task 4 `load_tables` 三处一致；`Event(t, station, flag)` 位置参序在 Task 2 测试与 Task 4 调用一致；`ev_base_sql(start, end)` 两参占位在 Task 3/4 调用一致；flag 全链路为 str `'0'/'1'/'2'`（DB `flag TEXT`）。
4. **代码验证（2026-09-19 写计划时已执行）**：计划内全部 Python 代码块提取后 `py_compile` 全过；Task 2 的 `eta.py` + 7 个单测、Task 1 的 `common.py` + 3 个真实 DB 冒烟测试在临时目录实跑 **10/10 通过**（其中抓到并修复 4 处 bug：`Optional[int)` 括号笔误、两处测试断言误把 ETA 绝对时刻当剩余量、`psycopg2.Connection` 注解 import 即炸、带参 SQL `LIKE` 未 `%%` 转义）。执行者照抄即可，无需再改 Task 1/2 代码。
5. **干跑校准（2026-09-19 计划定稿前，真实库 2 天窗 + 1 周窗 08-03→08-09 实测）**：学习器五表聚合 + S 校验 SQL + SQL_0809 全部跑通，产出四项计划修订（详见「干跑校准结论」节）：(a) 入口站 P50 实测 14.3h/24.3h → S1 阈值改 [8h, 30h] + 同窗行程 P50 自洽 ≤10%；(b) p1 阶跃在行程 76–82% 后段（不在 15/13/14IS065 簇）→ S2 改「首条 flag 集中度（S2a/S2b）+ p1 剖面 max/min（S2c）」；(c) 08/09 span cohort 2N54Y- rel_diff 0.20–0.30（五确「P50 近无偏」不成立）→ SQL_0809 改按车型口径 + 回放报告加 cohort 误差分桶；(d) 发现两个坑：带参 SQL 内联单 `%` 被 pqformat 破坏（实测空集）、09IS360/350 出口后记录站污染原始流 cohort。
6. **端到端干跑（2026-09-19 计划定稿前）**：从计划提取完整学习器 + 回放在 1 周窗真实库跑通（学习器 S1/S2a–c/S5/S6 PASS、S3/S4 因短窗样本量 FAIL 属预期；回放门判/08/09/cohort 分桶/报告/CSV/JSON 全链路产出，实测 2N54Y- rel_diff 中位 0.312、cohort 分桶 08IS 5537s vs 09IS 7559s，与校准结论一致）。额外抓出并修复 4 处代码 bug：`SQL_JOURNEY_P50` CTE 漏选 `exit_t` 列（UndefinedColumn）、报告入口/出口站查询 7 站 vs 6 占位符（psycopg2 TypeError）、S1 对小样本站 01IS045（全窗 ~150 车）改为 n≥50 才判定（小样本只报告）、两个 CLI 的 print emoji 在 Windows GBK 控制台崩溃（加 `sys.stdout.reconfigure`）。干跑 dss 表已全部 DROP（复原 W1 前 DB 状态），`tmp_ev_base` 为会话级临时表随连接关闭自动清除。
7. **用户评审复核（2026-09-19 计划定稿后）**：Task 0 的 `nul` 删除命令改 `\\?\` 扩展路径（普通路径解析到 NUL 设备而失败，已实测验证）；commit message 改为与实际 diff 一致（实测 diff 不含六确口径内容）；Task 3 Step 3 引语补 3f 带参例外；Task 4 补出口事件 -/3 边角注释（边角实际语义：该车仍进样本、仅丢 1 事件 + Σ 估计器丢末段）；Task 5 补 README「P50 近无偏」行按实测修正。
