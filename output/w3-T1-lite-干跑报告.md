# W3 T1-lite 干跑报告（周计划解析器，M1-lite 离线验收）

- 日期：2026-09-27
- 依据：`docs/06-T1-lite详细开发计划.md` v1.2（agy 两轮审核 0 阻断）+ 用户 2026-09-27 三项拍板
- 环境：conda `paint-dss`；`analytics_db` @ 127.0.0.1:5432（只读 ods，dss 内写入）
- 结论：**G0–G4 全过，M1-lite 离线验收通过**

## 0. 交付物

| 文件 | 说明 |
|---|---|
| `spike_w3/plan_loader.py` | 新增：解析（CSV/Excel）+ 持久化（upsert 幂等）+ 因果兜底（infer/resolve/recent_history） |
| `spike_w3/01_boot_online.py` | 接线：`load_week_plan` / `plan_note_text` / `replay_rest_days` / `load_rest_days` 垫片委托；DSS_DIM_TABLES 12→13；boot 顺序 ensure→check→persist（失败只警告） |
| `tests/test_w3_plan.py` | 新增 41 测试（T1.1 解析 17 / T1.2 持久化 8 / T1.3 因果 8 / T1.4 接线 8） |
| `tests/test_w3_bootstrap.py` | FakeConn 补 commit/rollback（T1.4 契约扩展：boot 含周计划表 DDL） |

测试基线：174（T4 关账后）→ **215 全绿**（+41，零回归）。

## G0 单测 ✅

```
tests/ 215 passed in 11.33s
```

- T1.1 解析：CSV/Excel 双格式、BOM、M/D 候选年算法（4 用例 + 跨年全扫描唯一性已证）、ISO 周双对齐、工时/总量解析（Excel float 920.0→920）、坏行收集不崩、0 字节文件、表头 strip、跨文件冲突 ValueError、同文件重复日期后行胜。
- T1.2 持久化：FakeCursor 占位符数校验；upsert SQL 显式列清单（PG 无 `SET EXCLUDED.*`）；二次 persist 0 写入；`.xls` = 识别但不支持 → 坏行不崩。
- T1.3 因果：源代码红线断言（无 `detect_day_type`/`detect_mid_type` 复用、`infer_shift_causal` 无 conn 参数）；SQL 纪律（SET TIME ZONE / 只读 ods / `DATE_EVT < now` 因果上界 / 4 下线点单引号）；M4 max_date 钳制（未观测日≠休）；15min 桶班制分类阈值复用 `shifts`。
- T1.4 接线：`load_week_plan` 三模式（目录 / DSS_PLAN_CSV 单文件 / 空→None）；`resolve_shift` plan 优先 inferred 兜底；plan_note 三态；DSS_DIM_TABLES=13；replay 休息日 09-05~09-13 窗 `{09-06, 09-13}` 回归钉死；persist 失败回滚保旧数据；`load_rest_days` 垫片存在且与 `parse_csv().rest_days()` 等价（agy M3：02/03 冻结回放器依赖，禁止删除）；boot 顺序静态钉死 ensure<check、ensure<persist（agy L3）。

## G1 解析干跑 ✅（`scratch/tmp_t1_g1_parse.py`）

`plan_inbox/` 4 文件 → **23 行 / 坏行 0 / 4 文件哈希**。

KW38 与用户确认值逐格一致（出车=底行 810 行，2026-09-22 用户两轮修正后拍板）：

| 日期 | 09-14 | 09-15 | 09-16 | 09-17 | 09-18 | 09-19 | 09-20 |
|---|---|---|---|---|---|---|---|
| 出车计划 | 810 | 980 | 980 | 990 | 940 | 800 | 0 |
| 进车计划 | 900 | 940 | 940 | 940 | 940 | 800 | 0 |

休息日 = {09-06, 09-13, 09-20, 10-04}（4 文件全休日）。

`shift_windows` 抽查（KW40 周一 11h/11h）：日班 [05:00,16:30) + 中班 [16:30, 次日 04:00)，attr_start 日班 04:45 / 中班 16:30 —— 与 W2 口径一致。

## G2 持久化 ✅（`scratch/tmp_t1_g2_persist.py`，真库）

| 步骤 | 结果 |
|---|---|
| 首跑 persist | **23 行**写入 `dss.dim_weekly_plan`（source='file'，单值）|
| `work_plan_file_meta` | **4 行**（file_name PK + sha256）|
| 二次 persist | **0 行**（哈希幂等）✅ |
| 改 KW40_sample 一格（09-28 出车 930→931，临时副本字节级替换）| **仅 7 行**更新，09-28 total_plan=931 ✅ |
| 还原 persist | 7 行回 930 ✅ |

DDL（`dss` schema，可 DROP+CREATE）：`dim_weekly_plan`（plan_date DATE PK，day_hours/mid_hours/total_plan/entry_plan/note/week_label/source/source_file/loaded_at TIMESTAMPTZ DEFAULT now()）+ `work_plan_file_meta`（file_name PK，sha256，loaded_at）。

坑记录：Windows 文本模式读写把 LF 变 CRLF → 哈希全变、幂等失效。**文件哈希比较必须字节级**（门脚本已用 `read_bytes`/`write_bytes`；`plan_loader` 读文件本即 `read_bytes`）。

## G3 启动干跑（真库两档）✅（`scratch/tmp_t1_g3_boot.py`）

档 1 回归锚（now 钉 2026-09-24 23:27，T2.6 基线）：

```
pool_size=837（逐字一致） cross_check_pct=0.358%（基线 0.36%） diff=3
watermark=119996978  frozen=true（STALE，数据止 09-24 16:32 → 预期内）
plan_note=周计划已加载: 23 天 / 4 文件（当周缺 7 天（2026-09-21~2026-09-27），兜底标注）
dim_tables 13 项全 ✓（含 dim_weekly_plan）
postexit derive 一致（5 站）  warning 空
```

档 2 当前墙钟（2026-09-27 19:33）：

```
pool_size=824  cross_check_pct=0.121% ≤ 1% ✅  frozen=true（STALE ✅）
plan_note 同档 1（幂等，二次 persist 0 写入故无「入库 N 行」后缀）
```

两档均不做池数字逐字比对（120h 滑窗随数据推移，agy 低 L1 已定两档口径）；档 1 的 837/0.36% 是 T2.6 回归锚逐字核验。

## G4 兜底干跑 ✅（`scratch/tmp_t1_g4_fallback.py`）

| 场景 | 结果 |
|---|---|
| 空目录 | `load_week_plan → None`，plan_note「无周计划文件 → 兜底自学模式（班制推断、无目标）」不崩 |
| 缺日合成（周只给 1 天）| plan_note「已加载: 1 天 / 1 文件（当周缺 6 天…，兜底标注）」|
| 缺计划日（09-07）| `resolve_shift → (11, 8, inferred)`；`plan_target → (None, None)`——**目标不猜，宁可不报不报假数**（2026-09-27 拍板③）|
| 有目标日（08-31, 900, 11h/11h）| `plan_target → (450, 450)`（复用 `split_target`，拍板②）|
| 无历史 | `infer_shift_causal([], 周一) → (11, 11, inferred)` 不崩 |

## 拍板落实对照（2026-09-27，3 项全按推荐）

1. **DDL 两表**（`dim_weekly_plan` + `work_plan_file_meta`，inferred 不入库、upsert 幂等无 DELETE）→ G2 证据 ✅
2. **复用 `split_target`** 拆日/中目标（零公式重复）→ T1.1 单测 `plan_target ≡ split_target` + G4 ✅
3. **缺计划日 total=None 不猜** → G4 场景 3 ✅

## 遗留 / 后续

- `DSS_PLAN_FOLDER`（默认 `plan_inbox`）已支持 env 覆盖；线上 M5 起周计划文件由用户提供或从排班系统导出（docs/08 规范）。
- 本任务不含：周计划在线热更新（boot 时读取；运行中改文件需重启 boot——M5 前按「每周班前重 boot」口径，如需热更另立任务）。
- 下一步：T6-lite（dashboard 控制台 + 5min 快照 + 单车 ETA），随后 M5 内网在线验收。
