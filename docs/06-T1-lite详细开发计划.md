# docs/06 — T1-lite 详细开发计划（周计划解析器 `plan_loader.py`）

> **状态**：v1.2（2026-09-27 起草；agy 两轮审 0 阻断 / 5 中 / 8 低 全部修复落版，见 §8；§5 三项 2026-09-27 用户拍板 = 全推荐（①确认 ②复用 split_target ③目标不猜）→ 可执行 TDD）
> **范围**：docs/02 Task 1 的 lite 部分 = **T1.1–T1.4**（离线验收）；T1.5 聊天文本形态 = Phase 2 不在本轮
> **上游事实源**：`docs/08-周计划与排班输入数据规范.md`（2026-09-22 定稿，口径以它为准）· `docs/02` Task 1 契约快照 · `plan_inbox/README.md`（2026-09-23 拍板：监听文件夹 = 项目内 `plan_inbox/`，生产 `DSS_PLAN_FOLDER` 覆盖）
> **事实清单**：§0（F1–F12，脚本证据 `scratch/tmp_t1_facts.md`）
> **预估**：~2 天（T1.1 0.5d + T1.2 0.5d + T1.3 0.5d + T1.4 0.5d，含 TDD）

---

## 0. 事实核查（F1–F12）

- **F1 规范**：6 列（日期/日班工时/中班工时/出车计划/进车计划/备注）；工时 `8/10/11/休`（`0`/空 = 休）；日期 `YYYY-MM-DD` 与 `M/D` 双格式；窗映射委托 `shifts.py`（B1 勿复制常量）；H 占比通用公式 `H日/(H日+H中)`（覆盖 8h 组合）；fallback 事件流自学；训练阶段 0% 依赖。
- **F2 数据现状**：`plan_inbox/` 4 文件共 23 行可解析（KW36 2 行部分周 / KW37 7 / KW38 7 = 真实原计划 / KW40_sample 7）；均纯 UTF-8 **无 BOM**，现有代码 `utf-8-sig` 读（兼容两种，Excel 导出常带 BOM → 统一 `utf-8-sig`）；进车计划列 KW36/37 为空（选填列合法）。
- **F3 契约（docs/02 冻结）**：`parse_csv(path) -> WeeklyPlan` · `load_plan(folder) -> WeeklyPlan`（命名识别 + 幂等重解析）· `WeeklyPlan.shift_windows(date) -> list[Shift]`（委托 shifts.py）· `plan_target(date) -> (day_target, mid_target)` · `infer_shift_causal(history, weekday) -> (day_h, mid_h, source)`。坏行 = 记日志 + 部分采纳，不得崩溃。
- **F4 三处 ad-hoc 直读器现状**：`01_boot_online.load_rest_days`（utf-8 / 严格 `%Y-%m-%d` / 仅「休+休」/ 单文件）、`02_shadow_replay.load_schedule`（utf-8-sig / 跨文件冲突 ValueError / 窗内缺日 KeyError）、`03_alert_backtest.load_plan_with_total`（同纪律 + 出车 int 或 None）。**共性缺口**：不支持 M/D、无 Excel、无持久化、编码不一、01 无冲突检测。
- **F5 接线决策（本轮定版）**：T1 后 **`01_boot_online` 接入 plan_loader**（生产启动路径）；**`02`/`03` 保持冻结不动**（M9/M10 已验收的验收工具；T4c 教训：动验收工具 = 重渲染/重跑成本）。三处直读器不删（02/03 仍用；01 的 `load_rest_days` 保留函数体、main 路径改走 plan）。
- **F6 DB 现状**：dss schema 14 表，`dim_weekly_plan` / `dim_shift_target` **不存在** → T1.2 新建 DDL（懒建幂等、只写 dss）。
- **F7 接线点**：`load_week_plan(cfg)` 现为 `return None` stub（01 L56）；`DSS_DIM_TABLES` 12 项 → +`dim_weekly_plan`（13 项存在性检查）；`main()` replay_cursor 的 `DSS_PLAN_CSV` 单文件 env 保留为**显式覆盖**（回放干跑指定单文件），否则走文件夹。
- **F8 依赖**：`environment.yml` 已固化 `openpyxl`（注释明示 T1.1 Excel 形态）；无真实 Excel 样本 → 单测合成 xlsx（openpyxl 生成）。
- **F9 测试影响面**：现有 174 测试不引用三处直读器（仅 `test_w3_common` 断言 `cfg.plan_folder` 默认值）→ 替换低风险；02/03 冻结 → **M9/M10 无需重跑**，回归门 = 全套件 + 短回放 smoke。
- **F10 单一事实源**：`alert_engine.split_target`（T4 已单测）= largest-remainder（日班 floor + 余数归日班，和恒 == total；单侧休 → 对侧独占；双侧无/total=0 → (0,0)）→ **`plan_target` 直接 import 复用，零重复公式**（纯函数无 DB 依赖；分层瑕疵留注释：将来可下沉 common.py，T1 不动 T4 代码）。
- **F11 M/D 年份歧义**：文件名带年份（`week_plan_2026KW40`）但不可靠——**ISO 跨年周**（2020-KW53 含 2021-01-01 / 2025-KW01 含 2024-12-30）下「文件名年份直接拼接」会误杀合法日期（agy 二轮 M-new 实证）→ 解析规则 = **候选年份算法**（§2.1：文件年 ±1 三候选，取 ISO (年,周) 与文件名 (年,KW) 匹配者，全不中 → 坏行）。
- **F12 工时预校验**：`normalize_shift_type` 对未知值（如笔误 `12`）也返回 None（与「休」不可区分）→ plan_loader **先预校验**：raw ∈ {8,10,11}（可带 `h`）→ 工时；∈ {休,0,空} → 休；**其他 → 坏行**（跳过 + 日志，不当休）。预校验过后再调 `normalize_shift_type` 取类型串。

---

## 1. 接口契约（冻结；事实源 = docs/02 Task 1 Interfaces + 本计划细化）

**模块**：`spike_w3/plan_loader.py`（新文件；无 DB 副作用的纯函数层 + 编排层 persist 分离，同 T2/T4 纪律）

```python
@dataclass(frozen=True)
class PlanRow:
    date: date                 # 生产日期（date，aware 无关）
    day_h: int | None          # 8/10/11；None = 休/停产
    mid_h: int | None          # 8/10/11；None = 休/停产
    total: int | None          # 全天出车计划；None = 无计划
    entry: int | None          # 进车计划（选填，L1 存档）
    note: str                  # 备注（可空串）
    source: str                # 'file' | 'inferred'
    source_file: str | None    # 来源文件名（inferred = None）

@dataclass
class WeeklyPlan:
    rows: dict[date, PlanRow]
    bad_rows: list[tuple[str, str]]          # (文件名, 原因)——坏行留痕
    file_hashes: dict[str, str] = field(default_factory=dict)  # 文件名 → sha256（load_plan 填充；persist 幂等判据，agy 低 L5/L-new3：禁 None 默认）
    def shift_windows(self, d: date) -> list[Shift]:      # 委托 shifts.shift_windows（F1：B1 单一事实源）
        # **仅静态文件内容**：行存在 → ('{day_h}h', '{mid_h}h')；无行（缺日或双侧休）→ []。
        # 缺日与双侧休同返 []（agy 低 L2）——在线运行路径**必须走 resolve_shift**（触发兜底），
        # 不得直接 for s in plan.shift_windows(d)（否则缺日被静默当停产日、绕过因果兜底）
    def plan_target(self, d: date) -> tuple[int | None, int | None]:
        # 复用 alert_engine.split_target（F10）；total None / 无行 → (None, None)
    def rest_days(self) -> set[date]:        # 日+中皆休（expected_silence，同 load_rest_days 语义）
    def missing(self, d0: date, d1: date) -> list[date]:   # 窗内缺日（覆盖校验）

def parse_csv(path: str | Path) -> WeeklyPlan            # 单文件；表头校验 + 坏行留痕
def parse_excel(path: str | Path) -> WeeklyPlan          # openpyxl 首 sheet 同 6 列结构（F8）
def load_plan(folder: str | Path) -> WeeklyPlan          # 命名识别 + 跨文件合并 + 冲突 ValueError + 坏行合并留痕
def infer_shift_causal(history: list[tuple[date, int | None, int | None]],
                       weekday: int) -> tuple[int | None, int | None, str]
    # 纯函数（无 DB）：history = 过去已完日的 (date, day_h, mid_h)；取近 14 天同 weekday 众数；
    # 无数据 → (11, 11, 'inferred')（docs/02 T1.3：缺数据默认 11h）；source 恒 'inferred'
def resolve_shift(d: date, plan: WeeklyPlan, history: list[tuple[date, int | None, int | None]])
    -> tuple[int | None, int | None, str]
    # docs/02 冻结契约的编排层（source='inferred'|'plan'）：plan 有行 → 计划值 + 'plan'；
    # 无行 → infer_shift_causal（'inferred'）。在线主循环（T6/M5）每班窗用它定班制。
def recent_shift_history(conn, now: datetime, weeks: int = 2) -> list[tuple[date, int | None, int | None]]
    # 因果 DB 构建器（T1.3）：只取 [now-14d, now) 内**已完日**；per-day 班型事后判定
    # （日班 = 当日首事件簇 05:00 vs 06:00 型；中班 = 当日最后下线时刻 02:30 vs 03:00/04:00 型；
    #   零下线 = 休）；SET TIME ZONE 'Asia/Shanghai'；只读 ods.carbody_history；
    #   **禁用 W2 detect_day_type/detect_mid_type**（回放语义，F3 红线）
def persist_plan(conn, plan: WeeklyPlan) -> int
    # 编排层（写 dss）：文件哈希幂等（F/T1.2）；返回新写入行数
def ensure_weekly_plan_tables(conn) -> None
    # 懒建 DDL（幂等；只写 dss）
```

**DDL（新表 2 张，懒建；只写 `dss`——只读纪律对 ods/dim/fct/mart/meta 不变）**：

```sql
CREATE TABLE IF NOT EXISTS dss.dim_weekly_plan (
    plan_date   DATE PRIMARY KEY,        -- 生产日期（一天一行；同日期多文件 = 冲突，入库前已 ValueError）
    day_hours   SMALLINT,                -- 8/10/11；NULL = 休
    mid_hours   SMALLINT,                -- 8/10/11；NULL = 休
    total_plan  INTEGER,                 -- 全天出车计划；NULL = 无计划
    entry_plan  INTEGER,                 -- 进车计划（选填）
    note        TEXT,
    week_label  TEXT,                    -- ISO 周（shifts.iso_week_label，如 2026-W38）
    source      TEXT NOT NULL,           -- 'file' | 'inferred'（T1-lite 只持久化 'file' 行；inferred 仅内存 + 报告标注）
    source_file TEXT,                    -- 来源文件名
    loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 时区说明（agy C4-2）：TIMESTAMPTZ 存绝对时刻，会话 SET TIME ZONE 只影响显示格式、无漂移风险；
-- 与项目其余 timestamptz 列（dss.work_alert_log 等）同纪律。

CREATE TABLE IF NOT EXISTS dss.work_plan_file_meta (
    file_name   TEXT PRIMARY KEY,        -- plan_inbox 文件名（meta 只增/改，不清理——README：历史文件保留不删）
    sha256      TEXT NOT NULL,           -- 内容哈希（幂等：同内容不重复入库）
    loaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

**DSSConfig**：`plan_folder` 已存在（F7，无新增配置项）；`DSS_PLAN_FOLDER` env 已接（.env.example 已预留）。

---

## 2. 设计细节

### 2.1 文件识别与解析（T1.1 + T1.2）

- **命名识别**：`^week_plan_(\d{4})?KW(\d{2,3})(\D*)\.(csv|xlsx|xls)$`；`_sample` 后缀**不排除**（README：解析器不认 `_sample`，认命名与列结构）；不匹配文件忽略（目录里允许其他文件，不报错）。
- **表头校验**：6 列规范名（单元格 strip 后比对，C4-3）（日期/日班工时/中班工时/出车计划/进车计划/备注）；**4 核心列**（日期/日班工时/中班工时/出车计划）缺一 → 该文件整体解析失败（`ParseError` 留痕，**其他文件不受影响**）；进车计划/备注缺列 → 允许（None/空串）。
- **行级规则**（坏行 = 跳过 + `bad_rows` 留痕，不崩溃、不当默认值）：
  - 日期：`YYYY-MM-DD` 直接解析；`M/D` → **候选年份算法**（agy 二轮 M-new；解唯一，确定性成立——ISO 周 (Y,kw) 内跨自然年的日期只会落在 Y±1 中恰好一个年份）：
    ```python
    def resolve_md_date(file_year: int, file_kw: int, month: int, day: int) -> date | None:
        for cand_y in (file_year, file_year - 1, file_year + 1):
            try:
                d = date(cand_y, month, day)
            except ValueError:
                continue
            if d.isocalendar()[:2] == (file_year, file_kw):
                return d
        return None   # 三候选全不中 → 真坏行
    ```
    验算：`2020-KW53` 内 `1/1` → 2021-01-01（ISO (2020,53) ✓，KW53 = 12-28~01-03 跨年的 1 月段）；`2025-KW01` 内 `12/30` → 2024-12-30（ISO (2025,1) ✓，KW01 = 12-30~01-05 跨年的 12 月段）；`2026-KW01` 内 `12/31` → 2025-12-31（ISO (2026,1) ✓）；`2025-KW38` 内 `1/1` → 三候选全不中 → 坏行。文件名无年份 → file_year = 当前年（同名规则）
  - 日期重复（同文件内）→ 后行覆盖 + 留痕（宽松：同一份计划里重复行视为修订）
  - 工时：F12 预校验（{8,10,11} / {休,0,空} / 其他 = 坏行）；Excel 数值单元格可能为 float（`11.0`）→ `_parse_int` 统一处理（下）
  - 出车计划 / 进车计划：空 → None；`_parse_int` 失败 → 坏行
  - **`_parse_int`（agy 中 M2）**：None/空串 → None；int → 直接；float → **仅整数值**（`v.is_integer()`，`920.5` = 坏行）；str → 先 `int(s)`，失败再 `int(float(s))` + 整数值校验（`"920.0"` → 920 合法；`"920.5"` = 坏行）；其他 → 坏行。CSV/Excel 共用（防 Excel 数值列 `920.0` 被误判坏行）
  - **表头宽容（agy C4-3）**：表头单元格 `strip()` 后比对（Excel/CSV 人工导出常带空格）；全角逗号等结构性污染 → 该文件表头校验失败（ParseError，不牵连他文件）
  - **空文件/0 字节（agy C4-1）**：捕获 → 该文件记 `bad_rows`（「文件为空」）+ 不崩溃、不牵连他文件
- **跨文件合并**（`load_plan`）：同日期出现在多文件且**值不一致** → `ValueError`（沿用 02/03 纪律：排班冲突不得静默）；值一致 → 合并（多文件覆盖同一周合法，F2 KW36/37 实例）；`bad_rows` 合并。
- **编码**：统一 `utf-8-sig`（BOM/无 BOM 兼容，F2）。
- **Excel**（`parse_excel`）：openpyxl 只读首 sheet；首行 = 表头（同 6 列名）；单元格值 `str(cell).strip()` 后走**同一行级规则**（`_parse_rows` 共享，CSV/Excel 无分支逻辑差）。
- **覆盖校验**：`missing(d0, d1)` = 窗内缺日清单（**不抛错**——KW36 部分周是合法形态，F2）；缺日由调用方触发 T1.3 兜底 + 报告标注（docs/02 T1.2）。

### 2.2 幂等持久化（T1.2）

- `persist_plan`：逐文件 `sha256(内容)` vs `dss.work_plan_file_meta` → 哈希相同 = 跳过（0 写入）；不同/新文件 = 重解析入库：**upsert**（agy 中 M1：原按 source_file DELETE 在**文件改名**场景删 0 行 → INSERT 撞单列 PK `plan_date` 唯一约束；upsert 天然幂等且改名安全）。显式 SQL 模板（PostgreSQL 不支持 `SET EXCLUDED.*`，列须显式枚举；`loaded_at = now()` **刷新为最新导入时刻**——审计语义 = 该日期计划数据的最新生效时间，agy 低 L-new2）：
    ```sql
    INSERT INTO dss.dim_weekly_plan
      (plan_date, day_hours, mid_hours, total_plan, entry_plan, note,
       week_label, source, source_file, loaded_at)
    VALUES (%s, ...)
    ON CONFLICT (plan_date) DO UPDATE SET
      day_hours   = EXCLUDED.day_hours,   mid_hours   = EXCLUDED.mid_hours,
      total_plan  = EXCLUDED.total_plan,  entry_plan  = EXCLUDED.entry_plan,
      note        = EXCLUDED.note,        week_label  = EXCLUDED.week_label,
      source      = EXCLUDED.source,      source_file = EXCLUDED.source_file,
      loaded_at   = now();
    ```
- 同日期多文件值一致合并：upsert 按文件处理序（文件名字典序）逐行写入，**后处理文件的 source_file 归属**（确定性）；值冲突仍由 load_plan 层 ValueError 拦下，到不了 persist。
- `inferred` 行**不入库**（审计表只存用户给过的计划；推断值仅内存 + 报告标注，§2.4）。
- upsert 同事务；失败回滚保旧数据（同 T2 影子池纪律精神）。

### 2.3 兜底自学（T1.3，因果版）

- `infer_shift_causal`（纯函数）：近 14 天内同 weekday 的 (day_h, mid_h) 各自取众数；平票 → 取**较新日**值（最近优先，确定性规则）；一侧全无数据 → 该侧默认 11（docs/02：缺数据默认 11h）；全休周同 weekday 众数为休 → 休（source 恒 'inferred'）。
- `recent_shift_history`（因果 DB 构建器）：`[now−14d, now)` 已完日逐日判定——日班型 = 当日产品车（`LIKE '78202%'`）事件剖面（首事件簇 05:00 型 vs 06:00 型，阈值对齐 shifts 常量 05:00/06:00 开班）；中班型 = 当日 4 下线点最后时刻（02:30 vs 03:00/04:00 边界）；零事件日 = 休。**只用 `DATE_EVT < now` 的数据**（因果红线：不得用未来事件——与 W2 回放口径的本质区别，F3 禁用 detect_* 的原因）。
- **数据上界钳制（agy 中 M4）**：有效样本日上界 = `min(now.date(), max_date)`（`max_date = MAX("DATE_EVT")::date` 全表实测）——断流/公网停更期（如 now=09-27、数据止 09-24 16:32），09-25/26 零事件 = **未观测**（不参与众数），**不是休**（否则平票取较新日会把正常工作日推断成休，真库实证：09-25/26 事件数严格为 0）。
- T1-lite 离线验收口径：纯函数用合成 history 单测；DB 构建器 = 假游标 SQL 纪律测试（只读 / 表名 / tz）+ 真库干跑 smoke（报告记录，不作门）。**在线因果正确性验收在 M5**。

### 2.4 目标拆分 + 无计划语义（T1.4）

- `plan_target`：`total` None 或无行 → `(None, None)`（**无目标 → 当日无缺口告警**，引擎 scope 自然为空，同 03 纪律）；有 total → `split_target(day_h, mid_h, total)`（F10 复用；单侧休 = 对侧独占）。
- **无计划 fallback 语义（本轮定版，待拍板 ③）**：缺日/整周无计划 → 班制 = `infer_shift_causal`（告警 scope 与窗可定）+ **total = None（不推断目标值）**——目标 = 业务承诺，不是节拍，推断目标会污染告警诚实性；报告标注「N 日无计划：班制为推断、无目标缺口告警」。
- 进车计划（L1）：解析存档（`entry` 字段 + 入库列），**不接任何告警逻辑**（模块③/次日推演远期用，docs/02 T1.4）。

### 2.5 接线（T1.4，01_boot_online）

- `load_week_plan(cfg)` 实现：`DSS_PLAN_CSV` env 有值 → 单文件模式（回放干跑显式覆盖）；否则 → `load_plan(cfg.plan_folder)`。无文件/全失败 → 返回 None + plan_note「无计划文件 → 兜底自学模式（班制推断、无目标）」。
- **`load_rest_days` 兼容垫片保留（agy 中 M3）**：02_shadow_replay L363–367 动态加载 01 模块并调 `boot_mod.load_rest_days(str(p))`（02/03 冻结态依赖）——函数**不得删除**，改实现为 `parse_csv(path).rest_days()`（行为等价，单测钉死），docstring 标注「供 02/03 冻结回放器调用，T1 接线后禁止删除」。
- `boot_sequence`：plan_note 改「周计划已加载：N 天 / X 文件（坏行 Y）」「周计划已加载 + 缺 Z 天（兜底标注）」等；`DSS_DIM_TABLES` 12→13（+`dim_weekly_plan`）；**顺序：`ensure_weekly_plan_tables(conn)` 必须在 `check_dim_tables(conn)` 之前**（agy 低 L3：全新库首启否则 13 项里 dim_weekly_plan=✗ 假报错）；persist 紧随其后（失败 = warning 不中止）。
- `main()`：replay_cursor 的 `expected_silence_fn` 改从 plan.rest_days() 取（保留 DSS_PLAN_CSV 单文件覆盖；行为等价于现 load_rest_days，回归测试钉死——同 09-05~09-13 窗 rest = {09-06, 09-13}）；wall_clock 路径同样接线（在线主循环消费 plan 留 T6/M5，本轮只保证启动序列与休息日降级可用）。
- `persist_plan` 在 `boot_sequence` 内调用（有 conn，两种 clock_mode 均执行——写的是 dss，回放干跑写入无害且行为一致；单测用假 conn）；失败 = 回滚保旧数据 + boot 报告 warning（不中止启动，计划入库非启动关键路径）。

---

## 3. TDD 步（每步 RED → GREEN → 全回归）

### T1.1 解析核心（0.5d）
**RED 测试**（`tests/test_w3_plan.py`，新建）：
1. CSV happy path：KW40_sample 7 行全解析（值逐格断言，含备注/空进车列）
2. M/D 双格式：合成 `9/28` 行 + 文件名 2026 → 2026-09-28
3. M/D ISO 周不符 → 坏行跳过（`week_plan_2026KW40` 里放 `1/2`）
4. 工时预校验：`12`（笔误）→ 坏行；`10h`/`10`/`休`/`0`/空 → 各自正确归一
5. 出车计划非整数 → 坏行；空 → None
6. 4 核心列缺一 → ParseError（该文件失败，不牵连）
7. 同文件日期重复 → 后行覆盖 + 留痕
8. 跨文件冲突（同日期不同值）→ ValueError；值一致 → 合并
9. Excel：openpyxl 合成 xlsx（含 int 单元格/带 BOM 无）→ 与 CSV 同值
10. `shift_windows` 委托：11h 日班 work_start 05:00 / 10h 日班 06:00 / 中班 16:30 开、11h 收 04:00、10h 收 03:00（对 shifts 常量断言，防复制）
11. `plan_target` 与 `split_target` 等价（全 H 组合 11/11、11/10、11/8、10/10、10/8、8/8 × total ∈ {0, 770, 920, 999} 逐一对打）+ total None → (None,None)
12. `rest_days`：休+休 日 ∈；单侧休 ∉
12a. Excel 数值单元格 float：`920.0`/`11.0` → 合法（920/11）；`920.5` → 坏行（agy M2）
12b. 表头带空格（`" 日期 "`/`"出车计划 "`）→ strip 后识别（agy C4-3）
12c. M/D 候选年份算法（agy 二轮 M-new + 一轮 L4 合并）：放行 3 例 = `2020-KW53` 内 `1/1` → 2021-01-01 / `2025-KW01` 内 `12/30` → 2024-12-30 / `2026-KW01` 内 `12/31` → 2025-12-31（三例验算 ISO 对齐）；拦截 1 例 = `2025-KW38` 内 `1/1` → 三候选全不中 → 坏行；解唯一性 = 同 (m,d) 两候选不双中（构造反例扫描 1/1~12/31 × 各 KW01/KW52/KW53 文件断言至多一个候选命中）
12d. 0 字节文件 → `bad_rows` 记「文件为空」，不崩溃、不牵连他文件（agy C4-1）
**GREEN**：`plan_loader.py` 骨架（PlanRow/WeeklyPlan/parse_csv/parse_excel/load_plan/_parse_rows/_parse_int 共享行级规则）。

### T1.2 监听 + 持久化（0.5d）
**RED**：
13. 命名识别：正例（csv/xlsx/xls/带年份/无年份/_sample 不排除）+ 反例（其他文件忽略不报错）
14. 幂等：同内容二次 persist → 0 写入（meta 哈希未变）
15. 内容变更：改 1 格 → 仅该文件周行 DELETE+INSERT，他文件行不动
16. DDL 纪律：`ensure_weekly_plan_tables` SQL 断言（CREATE IF NOT EXISTS / 只 dss / 列名钉死）；persist SQL 断言（只写 dim_weekly_plan + work_plan_file_meta）
17. `missing(d0,d1)`：KW36 部分周（只 2 行）→ 缺日清单正确
18. 空目录 → WeeklyPlan(rows={}, bad_rows=[])（不崩溃）
18a. 改名 upsert：文件 A.csv（date D 有行）改名 B.csv（同内容）→ 二次 persist 不撞 PK，D 行 source_file 更新为 B.csv（agy M1）
18b. `WeeklyPlan.file_hashes` 由 load_plan 填充且 persist 幂等判读自它（agy L5）
**GREEN**：`load_plan` 扫描/合并 + `persist_plan`/`ensure_weekly_plan_tables`（编排层写 dss，upsert 语义）。

### T1.3 因果兜底（0.5d）
**RED**：
19. `infer_shift_causal`：同 weekday 众数（11 vs 10 三比一 → 11）；平票 → 较新日值
20. 缺数据 → (11, 11, 'inferred')；一侧缺 → 该侧 11 另一侧众数
21. 全休众数 → 休（None, None, 'inferred'）
22. 纯函数纪律：不调用 DB / 不 import W2 detect_*（静态断言源码无 `detect_day_type` 字样 + 无 conn 参数）
23. `recent_shift_history` SQL 纪律：假游标（SET TIME ZONE / 只读 ods.carbody_history / `DATE_EVT < :now` 上界钉死 / 4 下线点单引号站名 / 参数数匹配）
24. 因果边界：history 构建不含 `now` 当日未完日（合成 now 边界用例）
24a. 数据上界钳制：now=09-27、max_date=09-24 → 09-25/09-26（零事件）不入样本（未观测 ≠ 休）；众数只用 ≤max_date 日（agy M4，真库形态合成）
**GREEN**：`infer_shift_causal` + `recent_shift_history`（只读查询 + per-day 判定纯函数 + max_date 钳制）。

### T1.4 接线 + 回归（0.5d）
**RED**：
25. `load_week_plan`：文件夹模式 / DSS_PLAN_CSV 覆盖模式 / 空目录 → None + 兜底标注
26. `resolve_shift` 编排：plan 有行 → 计划值 + 'plan'；无行 → 推断值 + 'inferred'（docs/02 契约 source 双值钉死）
27. boot_sequence plan_note 三态（已加载 / 已加载+缺日 / 无计划兜底）断言
28. `DSS_DIM_TABLES` = 13 项含 dim_weekly_plan
29. main() replay 休息日等价：同 09-05~09-13 窗 + plan_inbox 现状 → rest == {09-06, 09-13}（与旧 load_rest_days 逐位一致，回归钉死）
30. persist 失败回滚：假 conn 抛错 → 旧数据保留（影子纪律）
30a. `load_rest_days` 垫片存在且行为等价：02 的调用形态 `boot_mod.load_rest_days(path)` → 与 plan.rest_days() 逐位一致（09-05~09-13 窗 = {09-06, 09-13}）；源码断言函数未被删（agy M3）
30b. boot_sequence 顺序：ensure_weekly_plan_tables 在 check_dim_tables 之前（全新假 conn 13 项全 ✓；agy L3）
**GREEN**：01_boot_online 接线（load_week_plan 实现 / DSS_DIM_TABLES / main() 两处 / boot_sequence 顺序 + persist / load_rest_days 垫片）。
**回归门**：全套件（174 + ~39 新 ≈ 213）+ 短回放 smoke（replay_cursor 50 轮真库，plan 接线不崩、rest 降级生效）。**M9/M10 不重跑**（02/03 冻结，F9）。

---

## 4. 验收门（M1-lite，离线）

| 门 | 口径 |
|---|---|
| G0 单测 | T1.1–T1.4 全过（~39 新 + 174 旧，零回归） |
| G1 解析干跑 | `plan_inbox/` 4 文件 23 行全解析、坏行 0；KW38 7 行与用户确认值逐格一致（810/980/980/990/940/800/0）；KW40_sample 全过（docs/02 DoD 原文） |
| G2 持久化 | `dss.dim_weekly_plan` 23 行（source='file'）+ `work_plan_file_meta` 4 行；二次 persist 0 写入（幂等）；改 KW40_sample 一格 → 仅 7 行更新 |
| G3 启动干跑（真库，两档——agy 低 L1：120h 滑窗数字随数据推移，逐字比对必误 FAIL） | boot 报告 plan_note「已加载 23 天 / 4 文件」；DSS_DIM_TABLES 13 项全 ✓；**档 1（回归锚）**：`now` 钉 T2.6 基线时刻复跑 → 池 837 / cross_check 0.36% 逐字一致；**档 2（当前墙钟）**：cross_check ≤1% + B3 正确报 STALE（断流期数据止 09-24 16:32），不做逐字比对 |
| G4 兜底干跑 | 空目录/缺日合成场景：班制推断标注 + total None 无告警 + 不崩溃（报告留痕） |

**报告**：`output/w3-T1-lite-干跑报告.md`（G1–G4 证据 + SQL/日志摘录）。

---

## 5. 待用户拍板（3 项）

1. **DDL 两张新表**（§1，`dss.dim_weekly_plan` + `dss.work_plan_file_meta`）——结构确认（尤其「inferred 行不入库」与「ON CONFLICT (plan_date) DO UPDATE upsert 幂等」两条语义）
2. **plan_target 复用 `alert_engine.split_target`**（F10：单一事实源、零重复公式、T4 代码零改动）or 在 plan_loader 内独立实现 + 对打测试
3. **无计划 fallback 目标语义**（§2.4 定版案）：缺日/无计划 = **班制推断 + total None（不推断目标值，当日无缺口告警 + 标注）**（推荐：目标 = 业务承诺，推断目标污染诚实性）or 目标也推断（近 2 周同 weekday 实测出车中位数，标注「推断目标」）

---

## 6. 风险与退路

| 风险 | 处置 |
|---|---|
| Excel 真实模板与 docs/08 6 列假设不符（用户实际给的是截图转排，未见过原始 Excel） | T1-lite 只承诺「docs/08 模板结构 Excel」；真实 Excel 样本到位后加一行级兼容（坏行机制已兜底：不崩、留痕）；非阻断 |
| `M/D` 跨年（12/28 → 次年 KW01） | F11 规则：文件名年份 + ISO 周校验双保险；坏行不静默 |
| persist 写 dss 与「只读纪律」边界 | 只写 dss（DDL + 2 表），ods/dim/fct/mart/meta 零写；SQL 断言测试钉死（同 T2 work_alert_log 先例） |
| 02/03 冻结 → 三套解析并存（plan_loader + 02/03 直读器） | 已知技术债：02/03 是验收工具（冻结态）；M5 生产路径只走 plan_loader；T6 或 Phase 2 统一（记录不修） |
| replay 主循环 rest 来源切换回归风险 | 等价测试钉死（§3 T1.4-28：同窗 rest 集合逐位一致）+ 短回放 smoke |

## 7. Commit 计划（1 笔，docs/02 原提案）

`spike_w3/plan_loader.py` + `spike_w3/01_boot_online.py` + `tests/test_w3_plan.py` + `output/w3-T1-lite-干跑报告.md`
建议 message：`W3-T1: 周计划解析器 plan_loader（CSV/Excel 双格式 + 目录幂等入库 dss.dim_weekly_plan + 因果兜底 + plan_target 复用 split_target；01_boot 接线；~213 测试绿）`

---

## 8. 审核记录

### agy 一轮审（2026-09-27，只读 + 连库抽验，scratch/tmp_t1_plan_review_findings.md）
**总判定：修完可放行（0 阻断 / 4 中 / 5 低）**——全部采纳修复：
- **中 M1**（按 source_file DELETE 在文件改名场景删 0 行 → 撞单列 PK plan_date）→ §2.2 改 `ON CONFLICT (plan_date) DO UPDATE` upsert + 测试 18a
- **中 M2**（Excel 数值单元格 float `920.0` 经 `int(str())` 崩 → 合法行误判坏行）→ §2.1 `_parse_int` 整数值校验（`920.5` = 坏行）+ 测试 12a
- **中 M3**（02 L363–367 动态加载 01 模块调 `boot_mod.load_rest_days`——删除则 M9/M10 运行时 AttributeError）→ §2.5 兼容垫片保留 + docstring「禁止删除」+ 测试 30a
- **中 M4**（断流期 recent_shift_history 把零事件未观测日判「休」→ 平票取较新日把工作日推成休；真库实证 09-25/26 事件=0）→ §2.3 样本上界 `min(now.date(), max_date)` 钳制 + 测试 24a
- **低 L1**（G3「与 T2.6 基线逐字一致」在 120h 滑窗下必误 FAIL——池 837→824 随数据推移）→ §4 G3 两档（回归锚钉基线时刻 + 当前墙钟只验 ≤1% 与 STALE）
- **低 L2**（shift_windows 缺日/双侧休同返 [] → 调用方静默绕过兜底）→ §1 注释强化「在线必须走 resolve_shift」
- **低 L3**（全新库首启 check_dim_tables 先于建表 → dim_weekly_plan=✗ 假报错）→ §2.5 顺序钉死 + 测试 30b
- **低 L4**（M/D 跨年仅校验周号可放行偏离 1 年的日期）→ ISO 年+周双重对齐 + 测试 12c
- **低 L5**（WeeklyPlan 缺 file_hashes 字段 → persist 幂等判读无处来）→ §1 字段补 + 测试 18b
- **C4 独立风险 3 条**：0 字节文件（→ §2.1 + 测试 12d）/ loaded_at 时区（timestamptz 绝对时刻无漂移，DDL 注释说明）/ 表头空格全角污染（→ strip 比对 + 测试 12b）
- 已核实成立项 13 条（A1–A9 事实全过 / B1 三语义自洽 / B7 rest 等价逐位一致 {09-06, 09-13} / B8 STALE 期 persist 合理 / D1 三拍板项自洽）

### agy 二轮审（2026-09-27，修复验证 + 终审，scratch/tmp_t1_plan_review_findings.md；一轮版存档 tmp_t1_plan_review_findings_round1.md）
**总判定：修完可放行（0 阻断 / 1 中 / 3 低）**——R1–R12 逐条验真（一轮 9 条 + 3 风险全部确认落位；G3 档 1 基线时刻 2026-09-24 23:27 经真库实测精确复现池 837 / 0.36%；max_date 全表 MAX 走索引 2.8ms 零性能隐患）；本轮发现已全部修复：
- **中 M-new（R8，一轮修复引入的新问题）**：「文件名年份拼接 + ISO 双对齐」在 **ISO 跨年周**误杀合法日期——`2020-KW53` 内 `1/1`（该周含 2021-01-01）/ `2025-KW01` 内 `12/30`（该周含 2024-12-30）均被错判坏行（执行方一轮修 12c 时把 `2025-KW01`+`12/30` 当拦截例 = 日历算错，agy 纠出）→ §2.1 改**候选年份算法**（文件年 ±1 三候选，ISO (年,周) 匹配者胜出，全不中 → 坏行；解唯一性已推演：ISO 周跨自然年的日期只落在 Y±1 中恰好一个年份）+ F11 事实同步 + 测试 12c 重写（3 放行 + 1 拦截 + 唯一性扫描）
- **低 L-new1**：§5 拍板项 1 残留「按文件为删除键」旧字 → 同步为 upsert 语义
- **低 L-new2**：upsert 列清单显式枚举（PG 不支持 `SET EXCLUDED.*`）+ `loaded_at = now()` 冲突时刷新语义（= 最新生效时间）→ §2.2 显式 SQL 模板落版
- **低 L-new3**：`file_hashes` 默认值 `= None` → `field(default_factory=dict)`（防 `plan.file_hashes.items()` AttributeError）
- 终审：39 条新测试计数闭合（16/8/7/8）+ 213 总数；§5 三拍板项完备无需增补（改名 meta 残留 = 标准审计行为）。**可放行进入 TDD（待用户拍板 §5）**
