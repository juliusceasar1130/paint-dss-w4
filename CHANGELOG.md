# CHANGELOG — 代码变更摘要

> **粒度**：波次级，每波关账时写入（非逐 commit；逐 commit 明细看 `git log`）。
> **分工**：设计决策 / 口径 / 踩坑 → BM 线程笔记（`basic-memory/projects/paint-dss/`）；验收证据细节 → `output/` 下各波报告；本文件只记代码产物时间线。

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
