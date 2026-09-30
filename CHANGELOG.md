# CHANGELOG — 代码变更摘要

> **粒度**：波次级，每波关账时写入（非逐 commit；逐 commit 明细看 `git log`）。
> **分工**：设计决策 / 口径 / 踩坑 → BM 线程笔记（`basic-memory/projects/paint-dss/`）；验收证据细节 → `output/` 下各波报告；本文件只记代码产物时间线。

## 2026-09-29 — W3-M5 准备件：验收收账脚本 04 + 内网运行手册 docs/09 + agy 一审 5 项修复（TDD 297/297 绿；未 commit，待二轮审核）

- **产物**：`spike_w3/04_m5_acceptance.py` 新增（M5 验收收账 CLI：5h 锚点 Δ实际 ±10% 判分 / 实际终局 = 4 下线点产品车闭区间计数（日 [04:45,16:30] / 中 [16:30,次日按型]）/ 四桶互斥 reason（gated/actual_zero/stale/anchor_missing）/ 断流连续段归并 / 告警台账 / 多目录合并收账 + 同前缀 -rN 目录 WARNING；只读零库写）+ `tests/test_w3_m5_acceptance.py` 21 新增+ `docs/09-M5内网在线验收运行手册.md` 新增（阶段 0 / D0 checklist / D1–D5 PowerShell 自动重启循环 + 每日检查单 + 三张记录模板 / 收账 / 红线 / 排障速查 7 项）
- **agy 一审（BLOCK 3 阻断 / 2 中等）修复**：F1 = `spike_w1/common.py` DSN 支持 `DSS_DB_*` env 覆盖（`_dsn()` 纯函数 + `connect()` 现解析，此前硬编码 127.0.0.1 导致 .env 改了不生效）；F2 = `01_boot_online.next_grid_wait()` 纯函数 5min 网格对齐 + 漂移补偿（帧 ts 分钟恒落网格，M5 锚点帧可匹配）；F3 = `dashboard.resolve_run_tag` 显式 tag 幂等追加同目录（重启不再割裂 -r2）+ 04 多目录收账 + `find_sibling_run_dirs` 提醒；F4 = 04 结构化 reason 四桶互斥（旧 note 子串匹配会误判 actual=0 为锚点缺失 + STALE 双重计数）；F5 = 04 锚点按班次型（日 11h→10:00 / 10h→11:00 / 8h→13:00；中恒 21:30）+ 容差匹配 ±180s（旧硬编码 10:00 对 10h/8h 日班错锚）
- **验证**：全套件 **297 passed**（基线 270 + 27：M5 收账 21 + DSN env 4 + 网格对齐 2；分项见各测试文件）；冒烟 t6dry1 09-09 数字与修复前逐位一致（日 Δ实际 −10.4% / 中 +11.6% / 578+378=956，回填日预期 FAIL）；DSN 覆盖 / 网格对齐 / 显式 tag / 多目录均有独立单测
- **关键文件**：`spike_w3/04_m5_acceptance.py` · `spike_w1/common.py` · `spike_w3/01_boot_online.py` · `spike_w3/dashboard.py` · `tests/test_w3_m5_acceptance.py` · `tests/test_w1_dsn_env.py` · `tests/test_w3_snapshot.py` · `tests/test_w3_mainloop.py` · `docs/09-M5内网在线验收运行手册.md` · `scratch/tmp_m5prep_review_findings.md`（agy 一审）

## 2026-09-28 — W3-T6-lite 关账：控制台四件套 + 5min 快照 + ETA 菜单 + 主循环挂接（TDD 270/270 绿；干跑门 G0–G3 全过；agy 两轮审核 r1 BLOCK 全整改 / r2 PASS）

- **产物**：`spike_w3/dashboard.py` 新增（`active_shifts_at` 动态班次解析含跨日 union / `build_frame_ctx`+`render_frame` 纯函数帧渲染（班次轨 target=split_target 分摊、day_total 轨仅中班窗激活、池三分复用 `AlertEngine._split_pool`、pre 锚点「观测进度」无红黄绿、`[DSS] ` 前缀=G1 机械判据）/ `write_snapshot` 原子写 + tag -r2 防覆盖 + `validate_snapshot` / `compute_eta`+CLI `--eta`（仅 4 下线点、已出站=最近事件∈下线点∪POST_EXIT、n≥30 不猜数、返修标注）/ `make_t6_hook`+`build_t6_components`+`EngineGuardAdapter`）+ `spike_w3/01_boot_online.py`（`run_replay`/`run_wall_clock` +`round_hook=None` 默认零影响；main() console 门装配，装配失败 WARNING 不阻断）+ `spike_w3/alert_engine.py` 仅 +`deb_state()` 纯读（拍板⑥）+ `spike_w3/common.py` DSSConfig +3 env 字段（DSS_CONSOLE 严格 1/0 / DSS_RUN_TAG / DSS_SNAPSHOT_DIR，纯新增）；`tests/test_w3_{dashboard,snapshot,eta_menu,t64_hook}.py` 55 新增；全套件 **270 passed**（基线 215 + 55 零回归；分项 12/10/6/18/9，2026-09-28 agy 关账复审纠错）
- **红线核验（git diff）**：02/03/predictor/stream_pipeline 零改动；冻结代码唯一微动 = alert_engine `deb_state()`；`run_one_round`/`_render_status_line` 字节不变
- **拍板（2026-09-28「全部推荐」，六项）**：① ETA v1 仅 4 下线点 ② ETA 降级不猜数（无样本=显式报错/返修=环内残差+标注）③ G2 单日锚点 ±10% 软线 + 956 回填诚实注记 ④ 控制台默认 ON + pre 锚点观测进度 + replay 诊断「不适用」⑤ dry-run 窗 09-09 24h/288 步 ⑥ alert_engine 仅纯读 `deb_state()`
- **干跑门（报告 `output/w3-T6-lite-干跑报告.md`，证据 `scratch/tmp_t6_dry1_out.txt` + `output/snapshots/t6dry1/` 288 快照）**：G1 帧 288/288（中班帧 126 = 36 跨日 + 90 当日）+ console=0 两跑状态行序列（各 288）逐字一致 + 快照 288/288 schema 过；G2 锚点重算管线一致（A 精确 + grade 回推）：日 10:00 Δ+8.4% 绿 / 中 21:30 Δ−11.7% 超软线 1.7pp 如实报告（同刻全天 +4.6% 绿 = 班次间转移）；G3 ETA 全量对打 924 出口事件：skip 0.108%、中位 0.3s（K3IS055 固定段主导）、P90 342s / max 50min（L3ATK11IS02 方差）
- **踩坑沉淀**：`write` 工具整文件覆盖（追加必用 edit）；`run_replay` `while cur < end` 排他（288 步须 T_END=次日 00:00）；快照 Decimal 序列化；G1 回归判据 = 状态行序列（非全输出）；importlib 模块名禁含 `.`；帧告警键 = `sid`；中班 end 按型（MID_END_TIMES）
- **关键文件**：`spike_w3/dashboard.py` · `spike_w3/01_boot_online.py` · `spike_w3/alert_engine.py` · `spike_w3/common.py` · `tests/test_w3_{dashboard,snapshot,eta_menu,t64_hook}.py` · `docs/07`（v1.3 + 拍板 + 执行状态）· `output/w3-T6-lite-干跑报告.md` · `scratch/tmp_t6_dryrun.py`（门脚本）

## 2026-09-27 — W3-T1-lite 关账：周计划解析器（T1.1–T1.4 TDD 215/215 绿；M1-lite 离线验收 G0–G4 全过；agy 两轮计划审 0 阻断）

- **产物**：`spike_w3/plan_loader.py` 新增（CSV/Excel 双格式解析 + BOM + M/D 候选年算法（file_year±1 三候选、ISO 周双对齐、唯一性已证）+ 坏行收集不崩 + 跨文件合并（异值 ValueError 写库前抛）；persist_plan 文件哈希幂等 upsert（ON CONFLICT (plan_date) DO UPDATE 显式列清单，同事务失败回滚）+ `work_plan_file_meta` sha256；因果兜底 classify_completed_day（15min 桶阈值复用 shifts）/ infer_shift_causal（纯函数、同星期几众数）/ recent_shift_history（max_date 钳制：未观测日≠休、DATE_EVT<now 因果上界）/ resolve_shift（plan 优先→inferred））+ `spike_w3/01_boot_online.py` 接线（load_week_plan 三模式 / plan_note_text 三态 / replay_rest_days / load_rest_days 垫片委托 parse_csv（02/03 冻结回放器依赖，禁删）/ DSS_DIM_TABLES 12→13 / boot 顺序 ensure→check→persist（失败=警告不中止））+ DDL 两表 `dss.dim_weekly_plan`（plan_date PK）/ `dss.work_plan_file_meta`（inferred 不入库，2026-09-27 拍板①）；`tests/test_w3_plan.py` 41 新增 + `tests/test_w3_bootstrap.py` FakeConn 补 commit/rollback；全套件 **215 passed**（基线 174 + 41 零回归）
- **M1-lite 验收（报告 `output/w3-T1-lite-干跑报告.md`）**：G0 215/215；G1 plan_inbox 4 文件 23 行坏行 0 + KW38 逐格与用户确认值一致；G2 真库幂等（23 行 + meta 4 行，二次 0 写入，改一格→仅 7 行）；G3 真库 boot 两档（档 1 钉 09-24 23:27：池 837 逐字 / cross_check 0.358% / 13 表全 ✓ / STALE 预期；档 2 墙钟：0.12% ≤1% + STALE）；G4 兜底（空目录/缺日标注、缺计划日 total=None 不猜、无历史 (11,11,inferred) 不崩）
- **拍板（2026-09-27，全按推荐）**：① DDL 两表确认 ② 日/中目标拆分复用 `split_target`（零公式重复）③ 缺计划日 total=None 不猜（宁可不报不报假数）
- **踩坑沉淀**：Windows 文本模式换行回环（read_text/write_text LF↔CRLF）破坏文件哈希幂等——文件哈希比较/临时副本必须字节级（read_bytes/write_bytes）；plan_loader 本体 read_bytes 不受影响
- **关键文件**：`spike_w3/plan_loader.py` · `spike_w3/01_boot_online.py` · `tests/test_w3_plan.py` · `docs/06`（v1.2 + 拍板记录）· `output/w3-T1-lite-干跑报告.md`

## 2026-09-27 — W3-T4c + KW38 真前瞻计划模拟（渲染器窗自适应；用户原计划周 09-14~09-20 回测 M9 G2❌/M10 6-8 门；agy 独立复核 22/22 通过）

- **代码**：`03_alert_backtest.py::_render_report` 窗自适应 2 处（门判逻辑零改动，174/174 绿）：① 报告注记按 `all_day_final` 计划性质条件化（全 0 → 旧「实测回填」注记逐字保留；非零 → 真前瞻计划注记）② 硬编码计数标签（14/14、21 scope-day、shift scope 14、全天 7）→ 动态 `len`（KW38 窗实际 12 shift + 6 全天 = 18 scope-day；KW36/37 窗重渲染逐字不变）
- **KW38 模拟**（用户原计划，出车 810/980/980/990/940/800/0，行序经用户两轮确认；M9 经 scratch 包装器绕过 2 项 KW36/37 窗专属回归——固定带 [8.0,10.5] + scratch 对拍，其余门判全保真；M10 直跑；M9 328.9s/2016 轮，M10 346.5s）：
  - **M9**：G1 ✅（滚动==静态 0.19pp）G2 ❌（α=off 全厂 5h 中位 14.32% > 10% 硬线——线按 KW36/37 的 8.74% 标定；本窗 = 周内漂移（周二~四日班低估 11~26%）+ 周五崩产（中班高估 49.2%））G3 ✅ G4 ❌（α=off + G2 未达 → 不软化）。α 对打：全厂 on 8.69% 过线 / off 14.32%，但中班 on 16.67% 劣于 off 13.35% → **α 收益周依赖**（坐实 BM 挂账「α 窗/就餐日历」任务）
  - **M10（真计划 dfinal≠0，G1 首次诚实度量）**：G0/G1b/G2/G4/G5/G6 ✅，G1 ❌（21:30 锚点 vs 终局中位 5.55pp>5pp；逐日 8.52/2.55/3.47/2.42/19.47/7.62pp，剔除周五 3.47pp；21:30 零红）G3 ❌（miss 09-14_mid/09-18/09-18_mid/09-19_mid，真漏报仅周五 1 例）
  - **业务事实**：周二~四中班红警 = 真信号（中班实际 380/361/371 vs 拆分目标 490/490/495，产量向日班迁移；21:30 预测 405/395/347 误差仅 25/34/24 台，全周最准）；周一/周六零误报；**周五崩产（786 vs 940，−16.4%）= 21:30 锚点结构性盲区（崩在锚点后），23:05 黄（缺 68 台）→ 00:45 红（缺 104 台）自救**
- **agy 独立复核（2026-09-27，不信任复核：独立查库 + 重算 + 字节级对账）**：22/22 项通过，0 阻断 0 中；唯一低 = 执行方口头报「剔除周五 3.9pp」口算误差，正确 3.47pp（未污染产物，门判如实记 False）
- **数据转录教训（本会话沉淀）**：原计划表两条数值行靠「贴近实测」推断行序猜错（贴近实测的是进车计划——进车跟产线节拍走量级本就接近出口）→ **行序以表头标签为准，实测贴近度只能当辅证**；用户两轮纠偏后定稿
- **待拍板（不阻断 T1-lite）**：① G2 10% 硬线窗自适应化 or 保持（换窗=回归门，模拟跑用宽带）② 周五型锚点后崩产：加中班深夜二次锚点（23:00）or 接受为已知边界（B3 STALE 熔断兑底）；另 M3 交付物：段映射表 85 待确认站名待用户补
- **关键文件**：`plan_inbox/week_plan_2026KW38.csv`（用户原计划）· `output/w3-KW38-影子回放.{json,md}` · `output/w3-KW38-缺口告警回测.{json,md}` · `spike_w3/03_alert_backtest.py`（渲染器）· `scratch/tmp_kw38_m9_run.py`（模拟包装器）· `scratch/tmp_kw38_review_findings.md`（agy 复核清单）

## 2026-09-26 — W3-T4 关账：缺口告警 + 瓶颈定位（T4.0–T4.5 TDD 174/174 绿；M10 真库回测 8/8 ✅；agy 关账复审 0 阻断 0 中 2 低；G4 拍板 B）

- **产物**：`spike_w3/alert_engine.py`（split_target H 拆分 / grade 红黄绿 −5/−10 / Debouncer 双向 10min 物理时差 / Alert dataclass / AlertEngine：引擎自算 pred 防 M9 误差传递、双 scope（班次视图 + 全天主视图仅 [16:30, 次日 04:00) 激活 F10）、_split_pool 恒等式池=在制+瓶颈+no-ETA、STALE 冻结、naive ts 抛 TypeError）+ `render_alert_line`（双轨提示：班次红+全天绿 → 拆分偏置注记）+ `build_station_section` 两级段映射（前缀码 + profile 众数 n≥3）+ `02_shadow_replay.run_rolling` 加 `alert_hook`（默认 None 零生产影响）+ `spike_w3/03_alert_backtest.py`（M10 驱动：G0–G6 纯函数门判 + JSON/MD 双产物）+ `tests/test_w3_alert.py`（T4.0–T4.4 66 单测）；全套件 **174 passed**
- **段映射**（T4.2）：`output/w3-T4-段映射表.md` 259 站 = 174 自动 67% + 85 待确认（已呈用户，生产启用前补段名，不阻断关账）
- **M10 真库回测（09-05~09-13，2592 轮 5min 步 ~13min/跑）**：首跑 7/8（G4 旧「转换 ≤3」上限唯一失败 = 09-09_mid 确认转换 7 次）→ agy 关账复审（0 阻断 0 中 2 低）：139 步真库重放逐条验真 7 次转换全部 ≥10min 持续证据（最短间隔 20min）、伪影四类排除（A(t) 逐台闭合 / 死车清扫未跳档 / no-ETA 10–25 台微幅 / hook 与 M9 通路同态）、**物理根因 = 01:50–04:00 停线（2h10m 仅 2 台下线）+ 整夜贴红/黄分界线 ±10 台**、终局 378/478 = −20.9% 深红 → **用户拍板 B（2026-09-26）**：G4 终版 = 5h 锚点前 10min 窗口 [anchor−10min, anchor) 无确认转换 + 次数降观测项（JSON `G4_obs`）→ 重跑 **8/8 ✅**（G1 中位 3.87pp+零红 / G1b 14/14 / G2 21/21 / G3 4/4 中班 / G5 14/14 / G6 21/21；G4_obs = {09-05_day:2, 09-05:3, 09-07_day:2, 09-09_mid:7, 09-12_day:2}）
- **报告注记（agy 2 低已修）**：G1 诚实性注记（plan CSV 出车计划 = 实测回填 → all_day_final ≡ 0，G1 实际度量 21:30 锚点预测 vs 实际）+ G4 窗口开闭区间文档注记；`day_scope_bias_note`（09-12_day 班次红/全天绿 = F8 拆分偏置，不进 G3）
- **首跑踩坑沉淀（已修）**：Debouncer 属性名 current/pending/pending_since；G1 section `t0` 变量遮蔽顶层 datetime.now()（datetime−float）；G4 初版窗口 `t ≥ anchor−10min` 误判锚点后全窗口（正确 = [anchor−10min, anchor)）
- **关键文件**：`spike_w3/alert_engine.py` · `spike_w3/03_alert_backtest.py` · `spike_w3/02_shadow_replay.py`（alert_hook）· `tests/test_w3_alert.py` · `output/w3-T4-缺口告警回测.{json,md}` · `output/w3-T4-段映射表.md` · `docs/05`（v3 拍板版 + G4-B 终版 + 全勾）

## 2026-09-25 — W3-T3 关账：滚动预测 + α 自适应（T3.0–T3.3 TDD 108/108 绿；M9 影子回放三跑 ✅ G1–G3 过 + G4 软化；agy 三轮独立审核全闭环）

- **产物**：`spike_w3/predictor.py`（rem_of / a_of 有界闭区间 SQL / predict α 只作用 rem / build_takt_baseline P50+工时距 fallback / obs_window 3h 尾带截断 / AlphaTracker EMA β=0.3 + 同向 2 周期迟滞 + clamp [0.6,1.5]）+ `spike_w3/02_shadow_replay.py`（Step A 静态参照×2（W2 原窗：自检+对拍 / 延展窗：G1 对比侧）/ Step B 5min 滚动驱动（复用 T2 run_one_round/168h bootstrap/24h rebuild 生产语义）/ Step C p3_inproc+G1–G4 门判（按 DSS_ALPHA_MODE 分判）+报告）+ `tests/test_w3_{predictor,alpha,m9}.py`（10 单测）；全套件 **108 passed**
- **DSSConfig 19→23**（T3.0 +eta_stale_hours/learn_win_start/learn_win_end；M9 拍板轮 +alpha_mode，非法值 ValueError + env 覆盖）
- **M9 真库首跑（2592 轮，13min）FAIL 归因闭环（报告 (vi) 节，逐车对账 14 班 Δ 全精确吻合）**：G1 主因 = 静态窗尾盲区（出口 >09-14 在制车 132 台/84%；静态 spans 对出口表 INNER JOIN 抹除 1079 台窗内入口车）；其余 = F-1 偏斜残留 15（11 台车，毫秒级水位取证）+ P3/ETA/M7 口径差 14；**滚动引擎无新 bug**；G2–G4 = α 参数化追噪声（班次型分化：日班 5/7 改善 14.29→10.34%、中班 1/7 9.04→11.64%，病根 = 观察窗撞 18:30–19:30 就餐停工）；**α=off 9.79% ≤ 10% 达标**；agy 独立审核：「归因成立 + 选项可用」（0 阻断 / 2 中 / 1 低）
- **用户拍板（2026-09-25）+ 二跑（2592 轮，18.5min）✅**：① G1 窗尾 → 静态参照延展窗（尾 +7d 与 7d 前置边对称；容差 p3+2 维持；自检/对拍/基线保持 W2 原窗 8.74% 固定带）→ G1 逐班 Δ 最大 +5（首跑 +37）、全厂相对差 0.256pp / 中班 0.282pp ≤ 0.5pp ✅；② α → 门按 α=off 判定、生产默认 off（`DSS_ALPHA_MODE`，on=实验开关；G2 off 9.79% ≤ 10% ✅、G3 定义成立 ✅）；③ G4 软化默认（G1–G3 达成不阻断 + 敏感性数据；`DSS_G4_HARD=1` 恢复硬门）→ **总判定 ✅**；中班 α 修复（就餐工时日历）另立任务（Task Board：paint-dss 日班 α 启用与多周验证）
- **agy 审核轮 3（二跑关账复审，只读 + 真库重推）**：总判定「拍板落地保真 + 数字闭合 + 无阻断 + 残差可归因」——cls4 消除 14 班精确闭合（二跑 Δ = 首跑 Δ − cls4 台数逐班吻合）；p3 753→757 的 +4 真库核验（4 台首事件 09-16~18、回放窗内未出现，对 p3_inproc 影响 = 0）；残差逐车抽查（09-05_day +5 / 09-09_mid +5 逐台穿透，全归 F-1 残留 + P3 + M7，无新类）。2 条发现已修：中① 正式 JSON 补延展窗逐车明细（`static_ext_view`：5h I_bodies/A_bodies + p3 清单，产物自包含）+ 低① 报告/docs 注明 DSS_G4_HARD=1 仅 DSS_ALPHA_MODE=on 时有生产准入意义；M9 三跑（1585s）数字与二跑逐位一致（确定性）
- **真库踩坑沉淀**：psycopg2 本环境 `execute()` 返回 None（禁链式 fetch）；SQL 站名单引号（双引号=标识符，`_A_OF_SQL`/`_EXIT_HOURS_SQL` 潜在 bug 一并修 + 测试断言单引号形状）；MDS_DATA flag = 定长位 139（`SUBSTRING FROM 139 FOR 1`，`flag=` 正则全 NULL 勿用）
- **关键文件**：`spike_w3/predictor.py` · `spike_w3/02_shadow_replay.py` · `tests/test_w3_*.py` · `output/w3-影子回放验收.md/.json`（含 (vi) 归因与拍板闭环节）· `docs/04`（v2.1 + 拍板终版门判）· `scratch/tmp_w3_m9_{diff,diag3,diag5,diag6}.py`（归因工具）

## 2026-09-25 — W3-T2 关账：WIP 池 + 增量管道（T2.0–T2.6 全量，96/96 绿，KW37 干跑 GT 真值核对 0.11%）

- **产物**：`spike_w3/`（common 参数层 DSSConfig 19 项 / stream_pipeline 池状态机+增量流+护栏+重建+死车 / 01_boot_online 启动序列+主循环）+ `tests/test_w3_*`（7 文件 43 单测，全套件 96 passed）；`dss.work_alert_log`（死车审计表，DDL 懒建）
- **核心机制**：池状态机 R1–R7（连续同站幂等 / flag 继承 / 多次进出 / 中途收养 + POST_EXIT_STATIONS 双保险 + 动态历史锁 / BODY_TYPE 直取 / loop 计数 / tz 强制）；增量消费（ID 水位 + `:now` 上界 + skew 两档：窗内按车重算 / 更旧审计）；B3 三态护栏（NORMAL/STALE/RECOVERY + 四信号诊断 + 休班日降级）；B4 bootstrap（120h 同状态机回放，**3.7s**，交叉核对 0.36%）；M7 死车（120h 判定 / 复活 / 审计 + STALE 冻结）；主循环（consume → guard.check 唯一调用点 → dead_sweep 恒调用 → 审计）
- **F-1 丢行发现与修复（T2.6 干跑暴露，真库实证）**：consume 水位跳过系统性丢行（真库 0.37%/日 + 09-13 wm 中毒实案 ~11h 丢失，B3 正确报 203 轮 STALE）→ **周期 120h 全量重建**（`DSS_REBUILD_HOURS` 默认 24h，影子池原子置换，失败保旧池）+ **consume 拉取后按 (ts, ID) 排序再 dispatch**（52% ID/ts 逆序消除 per-car 子查询，回放 11h+ → 7.8min）
- **F-2 口径**：dim cross_check 为现态快照、历史时点结构性无效 → 回放验收门 = **GT SQL 真值核对**（120h 窗 GROUP BY，末事件站 ∉ 9 站对称差）
- **KW37 七天回放干跑（09-07→09-14，2016 轮，7.8min）**：池 885→1,105 峰→终态 943（873–1,075 带内）；死车 11 条（含 1 辆真车卡 10IS026 满 120h）；guard STALE 251 轮两段全可归因（周末停产尾 / 周日停产+丢行叠加，休班日降级生效）；**GT 核对差 1 台 0.11% ≤ 1% 过**（差异 = 空撬假号，真车零差）
- **质量门**：96/96 绿（W1/W2 零改动）；agy 三轮独立审核（T2.1 / T2.4 / T2.5+T2.6）阻断 0，中/低全部 TDD 修复；真库安全坑：全表窗口函数曾崩 Postgres → 一律 ts 区间有界 GROUP BY
- **关键文件**：`spike_w3/*.py` · `tests/test_w3_*.py` · `output/w3-T2-干跑报告.md`（定稿）· `docs/03`（步级计划 + 接口契约）· `.env.example`

## 2026-09-23 — W3 计划与地基：计划审核（B1–B7 闭环）+ 实施方案 + T2 步级计划 + 窗口口径拍板

- **W3 计划审核**（`output/w3-计划审核报告_20260922.md`，本地仓库 + 真库实证，tab2 agy 独立复核 7 条全部核实修复）：B1–B7 阻断项全部闭环——B1 死车阈值 8h 窗口实测 / B2 OCR 不需要 / B3 三态护栏 30min + 休班降级 / B4 回放重建 7 点定版 / B5 α 参数族定版 / B6 4 出站点计数确认 / B7 关键站 17 站 A 全量 + 7 日校准
- **实施计划**：`docs/02-W3开发实施方案.md`（T1–T6 task 级 + 接口契约快照，两轮外部审 7+8 条全修）+ `docs/03-T2详细开发计划.md`（T2.0–T2.6 步级 + §0 冻结接口契约，两轮审 7+8 条全修）
- **地基**：`DSS_*` 参数 env 化（python-dotenv + `.env.example`）；周计划输入规范 `docs/08` 窗口口径拍板（窗末 = 工时干满 + 30min 交班尾，含 8h 映射 + 通用拆分公式）；`plan_inbox/` 周计划文件夹（KW36/37 实测 + KW40 样例）；README 口径表 / AGENTS 约定同步

## 2026-09-22 — W2.1 强化关账：排班日历闭环接入（中班多算消除，KW36/37 全厂 8.08% 达标，全窗基线提升至 12.30%）

- **背景与根因定位**：排查确认 W2 中班 +16.06% 系统性高估的根因系缺少排班日历，自学（detect_mid_type）受凌晨炉区排空事件拖尾干扰将 27/28 个中班误判为 11h（04:00 截断），虚构 1.5~2h 工时多算 49 台；日班硬卡 16:30 近似无偏；就餐 15min P50 已均摊且 WIP 解耦无需建模，进日历作为 W3 自动停线免报警白名单。
- **排班接口落地（极简设计）**：`spike_w2/shifts.py` 新增 `normalize_shift_type` 与 `shift_windows(..., schedule=...)` 接口；业务层仅需录入 `10` / `11` / `休`，后台自动映射（10h 日 06:00-16:30 / 中 16:30-03:00；11h 日 05:00-16:30 / 中 16:30-04:00；休息日跳过）；无排班时自动 fallback 自学保证高可用；`tests/test_shifts.py` 增 3 项单测，全套件 **46 passed**。
- **KW36~KW37 真实排班实测与 Tab1 独立复现**：
  - 用户提供 09-05 ~ 09-13 真实排班，样本外无泄漏（训练集 04-14~08-15 零重训、零微调）；
  - 全厂逐班 @5h 中位绝对误差：原自学 10.44% → 排班停工窗末 **8.08% ✅（成功达标 ≤10%）**；
  - 中班中位绝对误差：9.77% 暴降至 **3.38%**，符号偏差从 +6.45% 几乎抹平至 **+1.39%**；
  - 10h 中班精准消除虚增（09-08 差 10 台/+2.82%，09-11 差 12 台/+3.38%，09-12 周六大产差 8 台/+1.39%）；
  - Tab1 独立实证对拍：全量 35 天逐比特 100% 成功复现，确认零数据泄漏；全窗 35 天外推中位误差从 14.16% 降至 **12.30%**。
- **关键决策与口径固化**：
  - 中班窗末严格尊重车间现场报表与交接班制度，定为 10h 到 03:00、11h 到 04:00；
  - 全厂 12.30%（好周 8.08%）固化为 **W2.1 强化基线**；日班冲产追赶与 YM33J- 返工长滞留偏差转 W3 动态流速吸收；
  - 突发故障停线推演（What-If 双轨制：自动感知 + 调度交互推演）确认作为 W3 核心待办；
  - 周生产计划与排班输入数据规范定案（`docs/08-周计划与排班输入数据规范.md`）：极简 4 核心字段，自适应全天总预警与工时比自动拆解，兼顾 Excel/CSV、文本、截图三种形态，训练 0% 依赖。
- **关键文件**：`spike_w2/shifts.py` · `tests/test_shifts.py` · `docs/08-周计划与排班输入数据规范.md` · `CHANGELOG.md`

## 2026-09-21 — W2 关账：班次级回放（v3 重锚定，固化 14.16% 离线基线，终局门转入 W3）

- **产物**：`spike_w2/` 四模块（common 复用 W1 基座 / shifts 班次日历 TDD / eta_v2 纯函数 TDD / 01 学习器 6 张 v2 表 + sanity / 02 回放器 D1' 门判）+ `tests/`（+33 单测，全套件 43 passed）；dss.v2_* 六表（周度 p 滚动 8 周 + 线别 cohort + 线概率 + 质量标记/补偿豁免 + meta）
- **回放验证（v3 终局，窗 08-16→09-19，进门判 59 班）**：
  - 修复计数规则无下界缺陷（v3 重锚定口径：`t + rem ≤ 窗末`，排除 38.3% 过期在制车误算）：全厂逐班 @5h |err%| 中位从 40.24% 骤降至 **14.16%**（符号偏差仅 **+3.41%**，总体近似无偏）；
  - 主车型表现：`IM186-` **7.81% ✅（已达标 ≤10%）**，`2N54Y-` **11.54%（逼近 10%）**，`YM33J-` **42.67% ❌（返工滞留人群偏置）**；
  - 多锚点 3/4/5/6h = 14.33 / 16.55 / 14.16 / 13.58%；⑥b A/B 净效果 −0.44pp；day 12.34% vs mid 16.06%。
- **学习器自校验修正**：W2-S2 阈值从 1000 降至实测匹配的 300（8 周滚动窗实测 343–810），解除 exit 1 虚假报错，S1–S4 全部 PASS（exit 0）。
- **关键决策与关账（用户拍板采纳方案 c）**：14.16% 固化为 W2 离线基线，≤10% 终局目标门刚性保持，未观测到的车间停线、夜间凹陷与返工滞留交付 W3 在线滚动预测吸收；W2 正式关账。
- **关键文件**：`spike_w2/*.py` · `tests/test_{common_w2,shifts,eta_v2}.py` · `output/w2-验证摘要.md` · `output/w2_replay_{shifts,models,gate,report}_20260921.*` · `output/w2_learn_sanity_20260921.md`

## 2026-09-19 — W1 关账：节拍学习器 + 单车 ETA 回放

- **产物**：`spike_w1/` 五模块（common 基座 / sqlbase 事件基座 / eta 纯函数 / 01 学习器 / 02 回放）+ `tests/`（DB 冒烟 + eta 单测）；dss 六张学习表（学习窗 2026-04-14→08-15，57,925 车 / 368.5 万事件）
- **回放验证**（样本外窗 08-16→09-18，22,817 车 / 139.0 万事件）：三估计器对打 Σ中位 114.1min / Σ均值 115.2min / 直接 211.4min；官方门 194.2min > 30min
- **门判关账（用户拍板 (b) 三项式）**：原 30min 单车门备注「结构性不可达」（误差地板 = 车间固有离散 IQR/2 1.3–17h）；结构性地板量化 + 中心偏差量化（主力车型入口符号中位 +44~+87min，记为已知偏差，W2 抓手⑤⑥ 修正）+ 机制验证（两车单车抽查 / 跨窗无漂移 / 6 轮评审）全部达成
- **关键文件**：`spike_w1/*.py` · `tests/*.py` · `output/w1-验证摘要.md` · `output/w1-门判修订_20260919.md`（+ `w1_gate_revised_20260919.json`）
- **W2 交接（设计要点 6 条，已录 BM）**：线别 cohort / 周度 p / 稀有长旅程车型 / 01IS045 不建模 / 站级质量标记 / 补偿净化

## 2026-09-19 — 前 W1：仓库初始化、环境与约定、W1 计划文档

- 仓库初始化（README 口径表 + spike 目录结构）；专用 conda env `paint-dss`（`environment.yml`）
- 全局 agent 配置（`AGENTS.md` + `.claude/CLAUDE.md` 镜像 + `.mcp.json` Basic Memory 接入）
- `docs/01-开发阶段计划.md`（W1/W2/W3 + 模块①③ + 用户输入清单）+ W1 执行计划（`docs/superpowers/plans/2026-09-19-w1-takt-learner-eta-replay.md`）
