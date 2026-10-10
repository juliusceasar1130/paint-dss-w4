# 涂装车间 DSS — 开发仓库

模块②（班次产量达成预测）为核心起点。设计定稿见 Basic Memory 笔记
`basic-memory/projects/paint-dss/涂装车间-dss-方案设计与开发路线`（口径唯一来源）。

**当前状态（2026-10-10）**：W1/W2/W3/W4 全部关账（W4 = 物理双环统一状态机 + 净延误第一迭代，2026-10-07 关账）；
M5 内网 5 天在线验收进行中（验收窗 2026-10-12~16、预跑 10-08~11；内网运行手册 `docs/09`、收账脚本
`spike_w3/04_m5_acceptance.py`、D0 快查 `d0_check.py`）。全套件 470 测试用例（W4.4h 基线）。

## 数据源

- `analytics_db` @ 127.0.0.1:5432（root/root，本机超管）
- 核心表：`ods.carbody_history`（过站事件流，**709.4 万条 / 93,819 车 / 259 站**，2026-04-14 16:36 ~ 10-09 15:27，2026-10-10 实测；5min 增量 ETL）
- 数据截止随用户同步（源库在车间内网，公网环境不自动续更）
- **所有时间查询必须 `SET TIME ZONE 'Asia/Shanghai'`**（会话默认 UTC，工厂时 = UTC+8）
- 列名大写需双引号：`"DATE_EVT"`, `"BODY_ID"`, `"BODY_TYPE"`, `"RW_STATION_ID"`, `"MDS_DATA"`, `"SKID_ID"`, `"SKID_IS_EMPTY"`
- **只读原则**：`ods` / `dim` / `fct` / `mart` / `meta` 层任何表与视图只读；本项目产物只写 `dss` schema（`dss.dim_*` / `dss.v2_*` / `dss.work_*`，dss 内可 DROP + CREATE）
- **ETL SLA（2026-09-23 拍板，两段式）**：开发期（公网）= 用户按需同步快照、不设 RTO；生产期（内网实时连接）= 链路故障由内网数据负责人（角色）处理、RTO ≤1h；断流时段预测/预警单独统计、不计入验收门

## 关键口径（2026-09-19 五确，详见 BM 笔记）

| 项 | 口径 |
|---|---|
| 产品车（学习样本） | `"BODY_ID" LIKE '78202%'` |
| 上线点（3） | 01IS205 / 1L360RB / 01IS045 |
| 下线点（4） | K3IS140 / 1J440RB / K2IS075 / K1IS135 |
| 行程口径 | 逐车：实际经过的上线点事件 → 下线点事件（单程，返工/黑顶为线内环） |
| 返工/黑顶标志 | `SUBSTRING("MDS_DATA" FROM 139 FOR 1)`（1-based）：0=正常 1=返工 2=黑顶二喷 -=未填充 |
| 主分布样本 | 每车**最后一次 flag∈{1,2} 事件之后**的 flag=0 事件（干净后缀） |
| 环分布 | `车型 × 标志{1,2} × 站` 剩余时间分布 |
| 标志位语义 | **车身实时状态机**：返工/黑顶在生产中决策、决策后才翻转 139 位 → 在线读最新事件标志 = 当前状态（flag=0 同时覆盖“未返工”与“返工完成回主线”） |
| 前决策补偿 | 最新事件 flag=0 且位于决策站簇**上游**时：`ETA = P50(主) + p(车型,站)×L(车型,环)`；p = 未来标志概率（车型×站），L = 环时长中位数（车型×环）。决策站：返工 97% @ 15IS065/13IS065/14IS065；黑顶二喷 99.0% 归位 @ 报交质检入口簇（24IS185/24IS115/22IS170/23IS170/23IS110/22IS110）+ 返修打磨线（14IS065/13IS065/15IS065）（已彻底剔除 14IS260/13IS260 喷房前 4.7min 闪现伪信号）；决策站**下游** p≈0 无缺口；实际打标后 5min ETL 内自动切环分布、补偿退出 |
| 黑车顶大环线锚定（2026-10-05 doc12/doc14 定版） | **方案 A（面漆烘房出口后置法）为离线训练主基线 + 方案 C 为在线流式防护**（详见 `docs/12` 与 `docs/14`）：① 强制 `SUBSTRING("MDS_DATA" FROM 137 FOR 1) = '1'`（严防 4,941 辆单色闪现车二次污染）；② 仅采纳出 1/2/3 线面漆烘房出口站（16IS305/17IS305/18IS345；原列 20IS010 已勘误为 2 线烘房储存线入口站、剔除，`OVEN_OUT_STATIONS` 3 站，见 doc15 C2）之后的 139=2 事件作为环起点，出环终点锁定三线报交网关组（25IS010/25IS110/25IS210），彻底消除 536 辆 14IS260 假首发车（缓存线合计 581 辆）中位 3.38h/均值 9.28h 虚增；③ 方案 B（干净后缀逆推）存在 5.3% 漏锚/错锚降级为无烘房兜底；④ 方案 C 在线白名单放行（报交簇 + 打磨返修线 13/14/15IS065）+ 14IS260/13IS260 前置静默抑制 |
| 质量返工回路锚定（2026-10-05 doc13/doc14 定版） | **物理双锚点（三大打磨站入口 + 三线通用报交网关组 25IS010/25IS110/25IS210）+ 车型分级退化**（详见 `docs/13` 与 `docs/14`）：① 进环起点 97.17% 集中于三大打磨站（15IS065/13IS065/14IS065）；② MES 电报 86.62% 出环不复位（单向粘滞），以三线通用报交网关组（1线 25IS010 / 2线 25IS110 / 3线 25IS210）为物理出环锚点强制解冻为 0，闭环覆盖率跃升至 98.17%（出站完工车漏失仅剩 16 辆），2线主干与正常车剩余时间中位仅差 2.1min；③ 在线防漏抑制全集扩充至 37 站（补齐 L3ATK11IS01、K3IS230、32IS015、42IS* 等）；④ 车型耗时 10 倍断层（4.05h~39.95h），建立车型→平台族群→ALL 三级收缩退化链 |
| 双环统一状态机与净延误（W4，2026-10-07 关账） | **`DSS_DUAL_RING=1` 生产默认**（统一引擎：`MDS_DATA` 137/139 双位状态机 + 面漆烘房出口 3 站 `16IS305`/`17IS305`/`18IS345` + 25IS 三网关 `25IS010`/`25IS110`/`25IS210` 出环锚定 + R3' 出环记忆：出环后 raw 位残留归 '0'）；净延误 shift/vehicle 两档均消费 `delay_p50_s` 列（2026-10-06 (b)≡(d) 拍板，mean 列仅诊断）；**真回滚 = `DSS_DUAL_RING=0` ∧ `DSS_USE_NET_DELAY=0`**（ND 单开为半迁移退化态、非安全回滚）；`DSS_SHIFT_TIER_DELAY`（W4.4h 班次型档）= 0 停线保留 |
| 08IS/09IS 并行线 | 等价工艺线（负荷分派），无需分桶；**W1 实测：分叉前共享站存在线别 P50 差**（2N54Y- rel_diff ~0.17–0.30，细节见 `output/w1-验证摘要.md` cohort 分桶） |
| 班次窗口与窗末（W2.1；中班口径 2026-09-23 用户拍板） | **严格对齐车间报表与交接班制度**：日班 11h 05:00–16:30 / 10h 06:00–16:30 / 8h 08:00–16:30；中班 11h 16:30–次日 04:00 / 10h 16:30–次日 03:00 / 8h 16:30–次日 01:00（窗末 = 工时干满 03:30/02:30/00:30 + 30min 交班尾，唯一计算口径，交班尾实测 0 台差异）；日班提早开工（04:45–正式开班）计日班 |
| 排班日历接口（W2.1） | **极简设计**：业务层仅录入 `8` / `10` / `11` / `休`（或留空），后台自动映射窗末；无排班时自动 fallback 自学保证高可用；**训练阶段 0% 依赖排班表**（物理节拍与几点上下班无关，免翻陈年旧账），仅在预测期按需提供 |
| 预测计数规则（v3） | **锚点重锚定**（`t_anchor + rem ≤ 窗末`）：排除过期在制车误算，计数逻辑为纯函数 |
| 三个偏差指标（2026-10-10） | **Δ** = (pred−目标)/目标：对计划、实时 5min 帧、供缺口预警（红 ≤−10% / 黄 ≤−5%）、无硬门；**回放偏差（G2）** = (pred−收班最终实际)/最终实际：对实际、收班后算、5h 锚点 ±10% = **唯一未达刚性线**；**G1** = 双臂（静态/滚动）逐班 \|Δ\| 中位 ≤0.5pp：重构等价门，W4.4c 已销账 |
| 出站延续站与进出围栏（W3 T2） | **出站延续站全集（5）**：`09IS360 / 09IS350 / K3IS230 / K2IS165 / K1IS225`（连廊/总装交接站，99.97% 紧随 4 下线点）；**双保险防幽灵车**：① 已出站车未过入口前绝对禁止触发 R4 收养；② 5 个出站延续站事件不收养、在池触发池移除（防幽灵车膨胀 300% 阻断 T2.4 交叉核对） |
| 参数配置与调试规范 | **环境变量与 .env 支持**：关键业务与算法参数一律支持环境变量（前缀 `DSS_*`）配置与本地调试；统一使用 `python-dotenv` 从项目根目录 `.env` 加载（模版见 `.env.example`） |
| 验收基线（至 W4 关账） | W2 离线基线 14.16% → W2.1 排班接入：全窗 12.30%、KW36/37 完备排班周全厂 **8.08% ✅（达标 ≤10%）**（中班中位 3.38%、符号 +1.39%）；W4 新栈（双环统一引擎 + 净延误 p50）G2 5h @KW36/37：W2 回放 **10.60%** / M9 滚动 **11.26%** 留痕 FAIL 关账（刚性线不软化不删门、残留转在线，明细见 BM 线程笔记与 `output/w4-双环重构验证摘要.md`） |
| M5 在线验收（进行中） | **全厂 ≤10%（班次级 5h 预测误差、T-5h 锚点）= 唯一未达刚性线**，由 M5 内网 5 天在线验收仲裁：验收窗 **2026-10-12~16**（内网生产环境），5h 锚点 \|Δ实际\| ≤10% 判分（实际终局 = 4 下线点产品车闭区间计数）；ETL 断流 / 工厂事故窗单独统计不计门；内网执行按 `docs/09`、收账 `spike_w3/04_m5_acceptance.py` |

## 目录

- `docs/` — 计划与口径文档：`01` 开发阶段计划；`02`–`07` W3 实施/步级计划；`08` 周计划与排班输入数据规范；`09` M5 内网在线验收运行手册；`10` 读写站拓扑五边口径 + 返工净延误重构方案；`11`–`15` 净延误实施 / 黑顶与返工环锚定 / 双环统一状态机 / W4 步级计划；`16`–`18` MDS 200 字节官方定义 / 滚床 30 字符实时报文字典 / MDS 237 字符电报 PG 映射参考
- `spike_w1/` — W1 节拍学习器（`dss.dim_*` 六张学习表；学习器五步：过滤 → 分段 → 清洗 → 聚合 → 自校准）+ 单车 ETA 回放（三估计器对打）；`sqlbase.py` = W4 统一纯净抽取 SQL（W1/W2 学习器与回放共用）；W1 已关账（2026-09-19，30min 单车门结构性不可达 → 三项式关账，见 `output/w1-验证摘要.md`）
- `spike_w2/` — W2 学习器 v2（`v2_*` 六表 + 净延误表 `dim_expected_delay` + 班次型档可选表 `dim_expected_delay_shift`）+ 班次日历 `shifts.py` + `eta_v2.py` ETA 引擎（W4 统一状态机 / 净延误 / 严格 1/0 门）+ 班次级回放（D1' 门判）；W2.1 排班接入关账（KW36/37 全厂 8.08% ✅，见 `output/w2-验证摘要.md`）
- `spike_w3/` — W3 在线引擎：入口脚本 `01_boot_online.py`（生产主循环 5min）/ `02_shadow_replay.py`（M9 影子回放）/ `03_alert_backtest.py`（M10 告警回测）/ `04_m5_acceptance.py`（M5 验收收账）；核心模块 `stream_pipeline.py`（WIP 池状态机 R1–R7 + B3 新鲜度护栏）/ `predictor.py`（滚动预测 + α）/ `alert_engine.py`（红黄绿缺口告警、双 scope）/ `dashboard.py`（控制台帧 + 5min 快照 + ETA 菜单）/ `plan_loader.py`（周计划解析 → `dim_weekly_plan`）/ `common.py`（DSSConfig 参数层）
- `tests/` — 全套件 470 用例（W4.4h 基线；仓库根 `python -m pytest`；`test_db_smoke` 的 DB 快照断言随数据同步刷新——2026-10-09 同步后 flag 分布快照过期 1 failed，按维护惯例刷新）
- `plan_inbox/` — 周计划输入文件夹（`week_plan_YYYYKWww*.csv`；`DSS_PLAN_FOLDER` 可覆盖；命名/监听约定见 `plan_inbox/README.md`）
- `topology/` — 读写站拓扑图资产（五种边分类，口径见 `docs/10`）
- `d0_check.py` — 内网部署机 D0 快查（DB 连通 + ETL 活性 + dss 表盘点）
- `output/` — 运行报告 / CSV（内容默认忽略，正式报告 `*.md`/`*.json` 入库——见 .gitignore 放行规则）
- `openwiki/` — 代码记忆 wiki（生成物：只读检索、不手改；`openwiki update` 再生成；引用纪律见 `openwiki/INSTRUCTIONS.md`）
- `.env.example` — `DSS_*` 参数模版（`.env` 不入库）

## 运行

连库脚本一律 `conda run --no-capture-output -n paint-dss python <脚本>` 启动（Windows 运行坑①②见 `AGENTS.md` 开发约定）；门判约定 exit 0 过 / exit 1 sanity 失败 / exit 2 门未过：

```bash
conda run --no-capture-output -n paint-dss python spike_w1\01_learn_takt.py     # W1 学习器（dss.dim_* 六表 + sanity 自检）
conda run --no-capture-output -n paint-dss python spike_w1\02_replay_eta.py     # W1 单车 ETA 回放（三估计器对打）
conda run --no-capture-output -n paint-dss python spike_w2\01_learn_v2.py       # W2 v2 学习器（v2_* 六表 + 净延误表 + sanity 报告）
conda run --no-capture-output -n paint-dss python spike_w2\02_replay_shift.py   # W2 班次级回放（D1' 门判）
conda run --no-capture-output -n paint-dss python spike_w3\01_boot_online.py    # W3 在线引擎（生产主循环；DSS_CLOCK_MODE=wall_clock/replay_cursor）
conda run --no-capture-output -n paint-dss python spike_w3\02_shadow_replay.py  # M9 影子回放（5min 滚动 + G1–G4 门判）
conda run --no-capture-output -n paint-dss python spike_w3\03_alert_backtest.py # M10 缺口告警回测（G0–G6）
conda run --no-capture-output -n paint-dss python spike_w3\04_m5_acceptance.py  # M5 验收收账（5h 锚点 ±10% 判分；只读零库写）
conda run --no-capture-output -n paint-dss python -m pytest                     # 全套件（根 pytest.ini：basetemp→scratch/.pytest-tmp）
conda run --no-capture-output -n paint-dss python d0_check.py                   # D0 快查（内网部署机：ETL 活性 + dss 表盘点）
```

顺序约束：W1 学习器先于 W2 学习器（W1 六表是 W2 的**只读**输入——`01_learn_v2.py` 头注钉死「只读不写」护栏；学习器 DROP+CREATE 重建，回放窗只用学习窗之后的数据）。启动存在性清单 = `spike_w3/01_boot_online.py` 的 `DSS_DIM_TABLES`（14 张必填：W1 六表 + W2 v2 六表 + `dim_expected_delay` + `dim_weekly_plan`；`dim_expected_delay_shift` 为可选表，缺表不阻断启动、引擎回退共享行）。M5 内网部署（D0 快查 → 学习器建表 → 预跑 → D1–D5 主循环 → 收账）按 `docs/09-M5内网在线验收运行手册.md` 执行。
