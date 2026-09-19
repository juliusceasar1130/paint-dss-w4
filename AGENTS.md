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

## 数据访问（analytics_db）

- `analytics_db` @ 127.0.0.1:5432，用户 `root`（本机超管）。
- **所有时间/班次查询必须 `SET TIME ZONE 'Asia/Shanghai'`**——会话默认 UTC，工厂时 = UTC+8，否则边界全部错位 8 小时。
- 列名大写需双引号：`"DATE_EVT"`、`"BODY_ID"`、`"BODY_TYPE"`、`"RW_STATION_ID"`、`"MDS_DATA"`、`"SKID_ID"`。
- 核心表 `ods.carbody_history`（过站事件流，~627.6 万条 / 83,493 车 / 259 站，2026-04-14 ~ 09-18）；数据为存量（源库在车间内网，公网环境不续更）。
- **只读原则**：不得修改 `ods` / `dim` / `fct` / `mart` / `meta` 层任何表与视图；本项目产物只写 `dss` schema（`dss.dim_*`、`dss.work_*`），dss 内可 DROP + CREATE。
- 连接凭据不离开本机：将来若推 GitHub remote，先把凭据移到环境变量或 gitignore 的配置文件。

## 开发约定

- Git：`main` 分支；commit message 用中文（例：`W1: 节拍学习器 v1，五张学习表产出`）；**暂不推 remote**；**每次 commit 前必须向用户报告变更文件清单 + commit message 并得到明确确认，禁止自动 commit**。
- 目录：`spike_w1/` = W1（01 学习器 → 02 回放），后续 `spike_w2/` …；`output/` 存运行报告 / CSV（gitignore）。
- 运行：专用 conda 环境 `paint-dss`（`environment.yml` 固化；`conda activate paint-dss` 后 `python spike_w1\01_learn_takt.py`）。
- 验收门（不达标不进入下一步，如实上报）：
  - W1：单车 ETA 中位误差 ≤ 30min（三估计器对打，设计见 BM 笔记）
  - W2：班次级第 5 小时预测误差 ≤ ±10%
- 口径拿不准：先查 BM 笔记；笔记未覆盖的，问用户，不猜不拍板。

## 文件删除安全

全局规则适用（见 `C:\Users\julius\.pi\agent\AGENTS.md`）：删除前列清单、等用户明确确认才动手；拿不准移 `C:\Users\julius\.Trash\` 代替删。
