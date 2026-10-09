# 涂装车间 DSS — 开发项目

> 本文件是项目 agent 全局指令（AGENTS.md 开放标准），pi / OpenAI Codex / Antigravity /
> GitHub Copilot 等支持该标准的 agent 启动时自动加载项目根部的本文件。
> `.claude/CLAUDE.md` 仅是对只读 CLAUDE.md 的 agent 的镜像；**改规则以本文件为原件，两边同步更新**。

## 项目定位

涂装车间决策支持系统（物流均衡 + 班次产量达成预测 + 缺口预警），开发顺序：先做模块②。

- **设计与口径单一事实源**：Basic Memory 笔记（见下节「知识库（Basic Memory）约定」）；本仓库 `README.md` 的口径对照表是其固化快照——写代码前先对表，口径变更时先改 BM 笔记，再同步此表与代码。
- 本仓库只放代码 + 验证脚本 + 报告，不放生产数据与业务文档。

## 知识库（Basic Memory）约定

本项目的工作知识（设计决策 / 口径拍板 / 实测结论 / 踩坑沉淀）在 Basic Memory 项目
`basic-memory`（F:\basic-memory），单线程笔记：
`basic-memory/projects/paint-dss/涂装车间-dss-方案设计与开发路线`。

1. 读写走 BM MCP 工具（本目录 `.mcp.json` 已注册 `basic-memory` server，锁定该项目）；
   不可用时降级 CLI `bm tool <动词>`（中文需 `PYTHONUTF8=1`）；裸文件直写永远禁止。
   豁免：本仓库与 BM 仓库的非笔记配置文件（AGENTS.md、CLAUDE.md、.mcp.json 等）与路径检查可用原生文件工具。
2. BM 操作前先读 Startup Router（`memory://instructions/startup-router`；无法解析时按标题搜索该笔记），
   按其分发表加载对应指令笔记；加载完成前不写。
3. 写入前先 `search_notes` 搜 "paint-dss" 确认线程笔记，再 `read_note` 读全文，然后覆盖
   （保留原有结构：分节 / changelog / observations）。
4. 每次 BM 调用显式传 `project: "basic-memory"`，绝不写到别的项目。
5. 设计决策 / 重要发现 / 踩坑追加到上述线程笔记（同一线程、不新建重复笔记）。
6. 任务 → Basic Memory 的 Task Board（笔记前缀 `paint-dss:`）；代码事实 / 口径速查 → 本仓库 `README.md`。

## 知识库分工：Basic Memory × OpenWiki（2026-10-06 拍板）

两级 agent 记忆：**BM = 决策记忆**（为什么、拍板了什么、实测结论、踩坑），
**OpenWiki = 代码记忆**（代码现在长什么样、模块怎么连、怎么跑）。OpenWiki 产物在仓库 `openwiki/`
（随 git 入库），排除边界 `.openwikiignore`，brief 为 `openwiki/INSTRUCTIONS.md`（用户自撰，OpenWiki 只读不改）。

- 定位：BM 线程笔记是**口径唯一事实源**（项目级、跨仓库、不可再生）；OpenWiki 是**派生产物**
  （代码 grounding、可再生、仅本仓库有效、所有接入 agent 可读）。
- 分工硬边界（每类内容只写一处，不重复）：
  - 口径 / 决策理由 / 演进历史 → 只进 BM（`README.md` 口径表是其快照）；OpenWiki 只引用出处（README 行 / docs 编号 / BM 笔记），不复制口径细节
  - 代码架构 / 模块职责 / 运行路径 / `DSS_*` 参数 → 只进 OpenWiki；BM 只提产物路径，不展开代码结构
  - 实测结论与验收结果 → BM 存「结论 + 理由 + 日期」；数字在 `output/` 报告；OpenWiki 只引报告文件名（`output/` 被 ignore）
  - 任务 / 工单 → BM Task Board（前缀 `paint-dss:`）；OpenWiki 不管任务
  - 代码变更时间线 → `CHANGELOG.md`（波次级）；设计决策 → BM
- 冲突优先级：**代码 > BM 笔记 > README 快照 > OpenWiki**。OpenWiki 口径漂移 → 相关页面标注差异、以 BM 为准、下次 update 自愈；代码与 BM 口径不一致 → 流程违规（代码应跟随拍板），报出修代码，文档层不得掩盖。
- 生成与维护：主开发 agent（Pi）发起 `init` / `update`；多 agent 可并行读 / 搜，生成写串行（`openwiki/` 队列为单写者）。
- 波次关账流程：用户提交代码 → BM 线程笔记追加本波决策 / 实测 → `CHANGELOG.md` → Pi 发起 `openwiki update` → 呈报文件清单 + 建议 commit message → 用户提交。
- 新 agent 上手顺序：本文件（自动加载）→ `README.md` 口径表（速查）→ 涉口径 / 决策查 BM 线程笔记（按上节 BM 协议）→ 涉代码结构用 OpenWiki `search` / `read`（可选上下文，非每次任务起手式）。

## 数据访问（analytics_db）

- `analytics_db` @ 127.0.0.1:5432，用户 `root`（本机超管）。
- **所有时间/班次查询必须 `SET TIME ZONE 'Asia/Shanghai'`**——会话默认 UTC，工厂时 = UTC+8，否则边界全部错位 8 小时。
- 列名大写需双引号：`"DATE_EVT"`、`"BODY_ID"`、`"BODY_TYPE"`、`"RW_STATION_ID"`、`"MDS_DATA"`、`"SKID_ID"`。
- 核心表 `ods.carbody_history`（过站事件流，633.9 万条 / 84,274 车 / 259 站，2026-04-14 ~ 09-19 16:40，2026-09-19 实测）；数据截止随用户同步（2026-09-19 用户同步至当日；源库在车间内网，公网环境不自动续更，再续更取决于用户）。
- **只读原则**：不得修改 `ods` / `dim` / `fct` / `mart` / `meta` 层任何表与视图；本项目产物只写 `dss` schema（`dss.dim_*`、`dss.work_*`），dss 内可 DROP + CREATE。
- **ETL SLA（2026-09-23 拍板，两段式）**：开发期（公网）= 用户同步快照、按需同步、不设 RTO，离线验收（影子回放）公网完成；生产期（内网实时连接）= 链路故障由内网数据负责人（角色）处理、RTO ≤1h，断流时段预测/预警单独统计不计入验收门，5 天在线验收在内网生产环境跑；盲窗运行时行为由 B3 新鲜度熔断兜底（STALE 冻结告警 + 横幅）。
- 连接凭据不离开本机：将来若推 GitHub remote，先把凭据移到环境变量或 gitignore 的配置文件。

## 开发约定

- Git：`main` 分支；commit message 用中文（例：`W1: 节拍学习器 v1，五张学习表产出`）；**暂不推 remote**；**agent 不执行 git commit（2026-09-20 用户拍板）——提交全部由用户自行执行；agent 只在任务/波次完成时呈报变更文件清单 + 建议 commit message，用户核对后自己提交**。
- `CHANGELOG.md`：代码变更摘要，波次级粒度、每波关账时写入（非逐 commit）；设计决策/口径/踩坑进 BM 线程笔记，本文件只记代码产物时间线。
- 目录：`spike_w1/` = W1（01 学习器 → 02 回放），后续 `spike_w2/` …；`output/` 存运行报告 / CSV（内容默认忽略，正式报告 `*.md`/`*.json` 入库——见 .gitignore 的 `output/*` + 放行规则）。
- 运行：专用 conda 环境 `paint-dss`（`environment.yml` 固化；`conda activate paint-dss` 后 `python spike_w1\01_learn_takt.py`）。
  - Windows 运行坑 ①（2026-09-21 实测修正）：连库脚本（psycopg2）一律用 `conda run --no-capture-output -n paint-dss python <脚本>` 启动（不依赖 shell 激活状态）——直调环境解释器 `D:\000_software_install\miniconda3\envs\paint-dss\python.exe` 实测出现过 `psycopg2.connect` 处 access violation 崩溃（exit -1073741819 = 0xC0000005；依赖 shell 的 DLL/PATH 状态、非必现，09-19 / 09-21 均遇过），旧指引「改用环境解释器直调」作废；`conda activate paint-dss` 后交互直跑 `python` 正常。
  - Windows 运行坑 ②：`conda run -n paint-dss python -c "多行代码"` 报错（不支持含换行的参数）——`-c` 仅限单行；脚本文件不受此限。另：02 脚本门判 FAIL 时约定 `exit 2`，conda run 会打印 `ERROR conda.cli... failed` 行——属上报约定而非运行失败，以产物为准。
- 验收门（不达标不进入下一步，如实上报）：
  - W1：单车 ETA 中位误差 ≤ 30min（三估计器对打，设计见 BM 笔记）
  - W2：班次级第 5 小时预测误差 ≤ ±10%
- **参数配置与调试规范（2026-09-22 用户拍板）**：关键业务与算法参数（新鲜度熔断、死车超时、α 窗口、预警阈值、驱动模式等）一律支持环境变量（前缀 `DSS_*`）配置与调试；统一使用 `python-dotenv` 加载根目录 `.env`；`.env` 不入库，由 `.env.example` 提供配置模版。
- 口径拿不准：先查 BM 笔记；笔记未覆盖的，问用户，不猜不拍板。

## 多 agent 协作规则（2026-10-09 用户拍板）

- **审核/复核类 agent 只读原则**：审核/复核类 agent 对被审文档与主 agent 发起的文档**只读**，不得直接修改；发现错误以「位置 + 正确值 + 证据（SQL/文件行号）」回报，由主 agent 修改后交审核方复验。
- **逃生门**：直接修改仅在用户明确认可、或任务书明确指定该 agent 为某文件的写者时允许；授权范围以任务书为准，不默认延伸。
- **各自产物不受限**：审核 agent 写自己的工作产物（验证日志、scratch、自己的 brain 工件）自由；主 agent 修改审核方产物不受限（主 agent 为默认写者）。
- **理由**（2026-10-09 复盘）：单一写者 = 可追责；审者不动笔保独立性与「审前/审后」基线；本仓库已发生审核 agent（agy）先改两份文档后上报、迫使主 agent 写后反查的事故——审计成本应前置（写前审）而非后置（写后查）。

## 文件删除安全

全局规则适用（见 `C:\Users\julius\.pi\agent\AGENTS.md`）：删除前列清单、等用户明确确认才动手；拿不准移 `C:\Users\julius\.Trash\` 代替删。

<!-- OPENWIKI:START -->

## OpenWiki

This repository has a generated `openwiki/` evidence index. It is optional just-in-time context, not required startup reading.

- Do not enumerate, preload, or search wikis at task start. Use retrieval when the user asks for it, when unfamiliar architecture or dependency behavior materially affects the task, or when source inspection leaves an important uncertainty. Stop once the question is grounded.
- When those conditions apply and OpenWiki retrieval tools are available, use `openwiki_search` for just-in-time context and `openwiki_read` for the relevant complete sections. If search returns `workspace_required`, ask which listed workspace to use and retry with its ID.
- Use `openwiki_list_workspaces` or `openwiki_list_wikis` when workspace membership itself needs to be discovered.
- If the retrieval tools are unavailable, read `openwiki/quickstart.md` and follow its links to the relevant pages.
- Treat source code and tests as authoritative. A brief's unknowns and review items are verification gaps, not automatic requirements.
- Prefer the narrowest quiet validation that proves the changed behavior. Preserve complete failure output.

The scheduled OpenWiki GitHub Actions workflow refreshes the repository wiki. Do not hand-edit generated OpenWiki pages unless explicitly asked; prefer updating source code/docs and letting OpenWiki regenerate.

<!-- OPENWIKI:END -->
