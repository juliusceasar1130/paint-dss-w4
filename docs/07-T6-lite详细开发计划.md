# docs/07 — T6-lite 详细开发计划（控制台 + 5min JSON 快照 + 工位 ETA 菜单）v1.3

> 依据：`docs/02` Task 6-lite 节（T6.1/T6.2/T6.3 + DoD）；事实核查 `scratch/tmp_t6_facts.md`；**agy round 1 审核 `scratch/tmp_t6_r1_findings.md`（2026-09-27：block，2 阻断 + 6 中 + 5 低，全部整改见 §7 changelog）**。
> 定位：在线 5min 循环的**人读出口 + 验收留痕 + 工人查询**，不改变任何 T2–T4 已冻结的池/预测/告警语义。
> 红线（v1.1 修订）：`02_shadow_replay.py` / `03_alert_backtest.py` / `predictor.py` / `stream_pipeline.py` / `common.py` 零改动；`alert_engine.py` 仅允许**新增纯读方法 `deb_state()`**（零语义变化，单测钉死——round 1 F-MED-06）；`01_boot_online.run_one_round` 与 `_render_status_line` 字节不变。

## 1. 交付物与文件计划

| 文件 | 动作 | 内容 |
|---|---|---|
| `spike_w3/dashboard.py` | **新增** | 控制台四件套渲染（纯函数）/ 快照写入（双轨 pred + alert_state + tag 防覆盖）/ ETA 菜单（**仅 4 下线点**，独立连库 CLI） |
| `spike_w3/01_boot_online.py` | 修改 | `run_replay`/`run_wall_clock` 加 `round_hook` 参数（默认 None，同 T4.4 模式）；`main()` 构建 T6 上下文（tables/comp_excl/section_map/plan/tracker/engine）并挂钩；**活跃班次动态解析**（`attr_start <= now < end`，同 02 run_rolling 语义，**禁用 `_shifts_for_window`/`is_complete` 静态过滤**——round 1 F-BLK-02） |
| `spike_w3/alert_engine.py` | 修改（最小） | 新增 `AlertEngine.deb_state() -> dict` 纯读（`{scope_key: {current_level, pending_level, pending_min}}`；不改任何行为） |
| `tests/test_w3_dashboard.py` | **新增** | 纯函数单测（帧渲染/快照 schema/ETA/动态班次/防覆盖/deb_state 纯读） |
| `scratch/tmp_t6_g*.py` | 门脚本 | G0–G3 证据 |
| `output/snapshots/{run_tag}/` | 运行产物 | 5min 快照（gitignore 已覆盖，agy A10 `git check-ignore` 实测确认子目录层级全覆盖） |

新环境变量（`DSS_*` 前缀，进 `.env.example`）：
- `DSS_CONSOLE`（默认 `1`；`0` = 关控制台与快照 → main() 行为与 T2.6 冻结态逐字一致）
- `DSS_RUN_TAG`（快照目录名；缺省 = 回放起点/启动时刻紧凑串；**同 tag 已含快照文件 → 自动追加 `-r2/-r3` 防覆盖 + 启动报告标注**——round 1 F-LOW-01）
- `DSS_SNAPSHOT_DIR`（默认 `output/snapshots`）

## 2. 设计契约

### 2.1 控制台四件套（每 5min 一帧；「5min = 回放游标步长/墙钟轮询间隔，非等待」——round 1 C6 注）

```
┌ 2026-09-09 05:00 │ 数据截至 04:58（lag 2.0min）│ NORMAL 数据新鲜 │ α off ┐
│ 诊断: 在线 S1✓ S2✓(缺口0) S3✓ S4✓        （replay 模式: ETL 诊断不适用·时间穿透） │  ← ① 新鲜度横幅
│ 班次 日 11h 04:45–16:30 │ A=87 pred(α=1.0)=123 目标=478                              │  ← ② A(t)/pred(t)/班次进度（目标 = split_target 分摊，与引擎/M9 门同源）
│ 距3h锚点(08:00) 3.0h·爬坡期：Δ=−74% 观测进度（红黄绿锚点后生效）                     │  ← ③ Δ 语义分层
│ 池 951：在制 780 / 瓶颈 140（返修18 黑顶6）/ no-ETA 31                                 │  ← ④ 池三分（flag 分层）
│ 瓶颈段 top5：W53×41 W52×26 …                                                        │
│ 最近确认告警: —                                                                      │
└────────────────────────────────────────────────────────────────────────────────────┘
```

- **帧标记（G1 机械化判据）**：每帧首行固定前缀 `[DSS] `（如 `[DSS] 2026-09-09 05:00 | ...`）；G1 帧计数 = stdout 中 `[DSS]` 行数 == 288（rich 无 tty 重定向时 = 纯文本行，前缀保留）。
- **③ Δ 语义分层（round 1 F-MED-04，班初假深红整改）**：首锚点（work_start+3h）前 = 「观测进度」——显示 Δ 数值 + 「距 Xh 锚点 剩余 Y（爬坡期，红黄绿未生效）」，**不上红黄绿色标**；首锚点后 = 正式 `grade(Δ)` 红黄绿（−5%/−10%）。控制台显示 ≠ 告警引擎去抖确认（后者仅锚点评估，语义独立，帧注记「最近确认告警」单独一行）。
- **① 新鲜度横幅（round 1 F-MED-05，回放负 lag 整改）**：`wall_clock` 模式正常显示 S1–S4 四信号（`guard.diagnose` 现成，agy B4 实测 5 SQL 合计 ~5ms）；`replay` 模式显示「**ETL 诊断不适用·时间穿透**」（不显示 ✓/✗——回放 now 早于元数据签到时间，`diagnose` 负 lag 会判「假新鲜」；**不改冻结的 stream_pipeline 代码**，显示层拦截 + 干跑报告注明诊断性质）。
- **② α 标注（round 1 F-LOW-03）**：pred 字段显式 `pred(α=1.0)`（off 模式固定 1.0；on 模式 = tracker committed 值）。
- 无活跃班次（班外）：②③ 显示「班外（下一班次 16:30 中班开）」；无计划日：目标=—、Δ=—（不猜数）。
- 缺口车 `gap_n = max(0, target − pred)`（Alert.gap_n 同语义）。

### 2.2 快照（每 5min 一个 JSON，`output/snapshots/{run_tag}/snap_{ts紧凑}.json`；原子写 tmp→rename；不入库）

```json
{
  "ts": "2026-09-09T05:00:00+08:00", "run_tag": "kw37_0909", "mode": "replay|wall",
  "pool": {"size": 951, "flag_dist": {"0": 927, "1": 18, "2": 6},
           "inproc_n": 780, "bottleneck_n": 140, "no_eta_n": 31,
           "bottleneck_sections": {"W53": 41}},
  "pred": {
    "shift":     {"shift_sid": "2026-09-09_day", "A": 87, "pred": 123,
                  "target": 478, "delta": -0.74, "grade": null,
                  "anchor_state": "pre", "anchor_in_min": 180},
    "day_total": {"A_all": null, "pred": null, "target": 956,
                  "delta": null, "grade": null}
  },
  "alpha": {"mode": "off", "value": 1.0},
  "alert_state": {"2026-09-09_day": {"current_level": "green",
                   "pending_level": null, "pending_min": null}},
  "alerts": [ /* 本轮确认的 Alert（dict，t→iso）；多数轮 [] */ ],
  "guard": {"status": "NORMAL", "label": "数据新鲜", "lag_min": 2.0,
            "is_anomaly": false, "diagnose": null,
            "diagnose_note": "replay 模式: ETL 诊断不适用（时间穿透）"},
  "data": {"max_ts": "2026-09-09T04:58:12+08:00", "watermark": 119996978}
}
```

- **双轨 pred（round 1 F-MED-01）**：`pred.shift` = 活跃班次 scope（班外 = 整对象 null；**target = split_target 分摊班次目标**（同引擎/M9 门，956/11h+11h → 478））；`pred.day_total` = **仅中班时段**填充（docs/02/F10：day_total scope 中班时段 A 窗 [D 04:45, 中班窗末) union 语义、target = 全天 total 956）。**target 预填语义（钉死）**：两轨 `target` 均为**计划已知即预填**（plan 有该日 total → 预填；计划缺失 → null）；`A/pred/delta/grade` 严格按 scope 生效期填充（shift = 班次活跃期；day_total = 仅中班时段）——保证 M5 班次级 ±10% 与全天总目标两条轨迹都可事后重算。
- **alert_state（round 1 F-MED-06）**：`engine.deb_state()` 全 scope 去抖器当前态（current/pending/已挂分钟）——99% 轮 alerts=[] 时留痕仍可复盘「当时处于哪级、pending 多久」。
- schema 钉死（键/类型/枚举/可空性），G1 门逐文件校验；每轮新增查询预算（agy 实测）：a_of ≤2 条（~21ms/条，12h 有界窗）+ α 观察 3h 窗 1 条（~5ms）+ diagnose 5 条（~5ms，仅 wall 模式）≈ **<60ms/轮**；predict 全池 by_vehicle ~24ms（892 车实测）——性能余量极大。
- 在制/瓶颈/no-ETA 三分 = `t+rem/α ≤ scope_end` 语义（同 alert_engine._split_pool 构造性精确）。
- **tag 防覆盖（round 1 F-LOW-01）**：启动检测目标目录已有 `snap_*` → tag 自动 `-r2/-r3` + 启动报告标注（M5 留痕审计不可逆覆盖防护）。

### 2.3 工位 ETA 菜单（M14；**v1.1 范围收缩：仅 4 下线点**——round 1 F-BLK-01）

> **收缩依据（agy 实测，证据链完整）**：v1 原设计「主干序切片 Σ takt P50」在并联支路（01/1L、02/03、08/09 互斥）中位 rank 交错下必然断裂——232 站主干序相邻对 231 对仅 13 对（5.6%）在 takt 表有 n≥30 行；跨支路相邻对（08IS005→09IS005 等）物理无流向 0 行。**中间站 ETA 延后至 Phase 3（`dim_station_takt` 有向图最短路，与 T5.3 v2 同机制）**；「主干序持久化（T6.3a）+ G4 用户确认」子任务**整体删除**（切片不用了序表不需要；且中位位置算法产出 232 站，与 W1「119 站主干」口径不一致，v1 不建该表）。

- CLI：`python -m spike_w3.dashboard --eta <BODY_ID> <TARGET> [--as-of ISO]`
**独立连库只读工具**（round 1 F-MED-03：不读内存池、不依赖主循环进程；当前站/flag 一律 = DB 该车 `<= as_of` 最近事件的 station + `MDS_DATA` SUBSTRING(139 FOR 1)，走 `idx_ods_carbody_body_id_date` 索引 ~1ms 实测）。

| 情形 | 算法 | 输出 |
|---|---|---|
| 目标 ∈ 4 下线点 | `dim_remaining_time[车型 n≥30 优先, 否则 ALL][当前站].p50`（agy A6/B2 实测：ALL 行全部主要工作站 n≥30；前 10 大返修站 ALL n>1000） | ETA P50（h/min + 秒）+ 溯源（车型/n/当前站） |
| 返修循环车（flag=1/2） | 同上（环内站残差表直查）+ 「该车在返修循环中」标注（docs/02 口径）+ `dim_loop_remaining[车型, flag, 当前站].p50` 参考（「预计 ~X 回主线」，可缺则省） | 标注 + 参考值 |
| 目标 ∉ 4 下线点 | — | **明确报错「中间站 ETA v1 未开放（Phase 3：takt 图最短路）」**（不猜不估） |
| 当前站残差行缺失（车型+ALL 双缺 n<30） | — | 明确报错「当前站 X 残差样本不足（n<30），不估」 |
| 已出站（最近事件 ∈ 下线点 ∪ **出站延续站**（`common.POST_EXIT_STATIONS`：K3IS230/09IS360；agy r2 实测 76% 出站车最新事件在延续站）/ 该车历史已有下线点事件） | — | 「该车已于 X 出站」 |
| 车不存在 / 非产品车 | — | 明确报错 |

- 学习表 n≥30 纪律（W1 同口径）：车型行 n<30 → ALL；ALL 无/n<30 → 报错不估。
- **v1 零新库写**（快照本地、ETA 只读、无新维表）——dss 写 = 0（对比 v1 原方案含 dim_station_order）。

## 3. 任务拆分与 TDD 验收（RED→GREEN 铁律）

- **T6.0 接线骨架**：`DSS_CONSOLE/DSS_RUN_TAG/DSS_SNAPSHOT_DIR` 进 DSSConfig；`run_replay/run_wall_clock` 加 `round_hook=None`（None = 行为逐字不变）；dashboard.py 骨架；**活跃班次动态解析助手** `active_shifts_at(plan, now) -> list[Shift]`（`attr_start <= now < end`，跨日中班天然覆盖——F-BLK-02 整改核心；计划日缺失 → 空 + 注记）。
  测试：hook=None 时 run_replay 状态序列与旧版一致（合成小窗 + fake conn，字段逐一对；**若 FakeConn 不足以支撑 run_replay 多轮（consume/bootstrap SQL 面），降级为静态断言（同 T1.4-30b 模式）+ G1 真库回归两跑 diff 兑底——TDD 时实证后在报告钉死选了哪条**）；env 三态；**09-09 24h 窗动态解析必含 09-09_mid（attr 16:30, end 次日 04:00）且 16:30–23:55 每步匹配**（F-BLK-02 回归钉）；**缺计划日语义（与 T1-lite 一致）**：inferred 日（兜底自学 11h/8h）→ 班次窗口照定（shift_windows 吃 inferred 工时）、**target = —（计划缺失不猜数）**，帧显示班次 + 目标=—；全周无计划（plan=None）→ 班外显示 + 兜底自学注记。
- **T6.1 控制台四件套**：`build_frame_ctx(...)` 纯组装 + `render_frame(ctx) -> str` 纯函数 + rich 输出包装。
  测试：五态帧（有班次·锚点前 / 有班次·锚点后 / 班外 / 无计划 / STALE）；**锚点前无红黄绿色标 + 「距锚点 X」注记；锚点后 grade 生效**（−4.9% 绿 / −5% 黄 / −10% 红边界）；replay 模式横幅 = 「ETL 诊断不适用·时间穿透」（无 ✓/✗）；gap_n=max(0,target−pred)；pred(α=1.0) 标注。
- **T6.2 快照**：`write_snapshot(dir, tag, ctx)` 原子写 + `validate_snapshot(ctx)` schema 校验（含双轨 pred 可空性、alert_state 枚举）+ **tag 防覆盖**（目录已有 snap_* → `-r2`）。
  测试：schema 全键/坏键/坏枚举/双轨可空组合；原子写无 tmp 残留；**防覆盖：二次同 tag 写 → 落 `tag-r2` 且首份不动**；288 文件命名无碰撞。
- **T6.3 ETA 菜单**：`compute_eta(conn, body_id, target, as_of) -> dict`（独立只读查询 + 组装，分支见 §2.3 表）+ CLI。
  测试：合成表七分支（下线点/车型回退 ALL/返修标注/目标非下线点报错/残差双缺报错/已出站/车不存在）；n≥30 回退边界；输出含溯源；**SQL 纪律（SET TIME ZONE / `DATE_EVT <= as_of` 上界 / 单引号常量）**。
- **T6.4 干跑接线 + alert_engine 最小改动**：main() T6 上下文构建 + round_hook 实装（ctx → 帧 → 快照，异常隔离：快照写失败 warning 不中断）；`AlertEngine.deb_state()` 纯读方法（**唯一冻结文件改动**，单测钉：调用前后引擎状态零变化、返回值键集 = 已 feed 过的 scope key）；α tracker 在线观察（每轮 3h 窗 SQL，alpha_mode=off 时仍观察、显示不参与 pred）。
  测试：hook 一轮调用序（render 在 snapshot 前）；STALE 轮 pred 照算 + alerts=[]（引擎冻结语义）+ alert_state 去抖不推进；deb_state 纯读回归。
- **T6.5 门 + 报告**：G0–G3 门脚本 + `output/w3-T6-lite-干跑报告.md`。

### T6 执行状态（2026-09-28 收尾）

- T6.0–T6.5 全部完成；单测 **270/270**（含 T6 新增 55：12/10/6/18/9，2026-09-28 agy 关账复审纠错）；干跑门 **G0–G3 全过**（证据：`output/w3-T6-lite-干跑报告.md`）。
- **agy 关账复审（2026-09-28）：PASS（0 阻断 / 0 中 / 1 低）**——20 项必查独立验真（红线字节级 git diff / 全套件复跑 270 / 288 快照 7 时点独立查库重算 A 逐点精确 / 两跑 288 状态行逐字一致 / G2 双锚点重算 + 956 回填对账 / G3 重算 + 站点分布逐字一致 / ETA CLI 真库 6 场景全过）；唯一低 = 报告单测分项笔误（56→55，已修）。findings：`scratch/tmp_t6_closeout_findings.md`。
- 门判如实记录：G2 中锚 21:30 Δ=−11.7% 超 ±10% 单日软线 1.7pp（拍板③ 语义：如实报告不阻断；同刻全天累计 +4.6% 绿）；G3 中位 0.3s 由 K3IS055 固定段主导（713/923 台 <1s），尾部指标 P90 342s / max 50min（L3ATK11IS02 方差）。
- 红线核验（git diff）：02/03/predictor/stream_pipeline 零改动；common 仅 +3 env 字段（纯新增）；alert_engine 仅 +`deb_state()` 纯读；01 仅 +round_hook（默认 None 零影响）+ main() console 装配（装配失败不阻断主循环）。

## 4. 验收门（v1.1）

| 门 | 判据 |
|---|---|
| G0 | 新增单测 + 全套件绿（基线 215 + 新增，零回归；deb_state 纯读回归含在内） |
| G1 | 干跑 1 天（**2026-09-09 周三 24h**，288 步 @5min，main() replay_cursor 生产形态 bootstrap=120h——agy C2 实测 120h/168h 活跃池差 **0 台**）：0 异常退出；288 快照逐文件 schema 过（双轨 pred + alert_state）；stdout 288 帧且 **16:30 后帧含 09-09_mid 班次、21:30 锚点帧存在**（F-BLK-02 整改验收）；**DSS_CONSOLE=0 回归：同窗两跑（0 vs 1）状态行序列逐字一致**（机械化判据：stdout 状态行 grep 后 diff 为空；单测层另以 fake conn 断言 status 字典列表逐字段相等——F-LOW-02） |
| G2 | **快照事后重算**：从 288 快照提取 5h 锚点（09-09 日班 10:00 / 中班 21:30）pred + A，独立 DB 查实际出口（日班 [04:45, **16:30 闭区间**（与 a_of `<= end` 的 16:30:00 双计口径同源，F-R2-02）/ 中班 [04:45, 次日 04:00]）——**实际值不依赖干跑窗边界**）→ Δ 表；管线可重算 + 两锚点 \|Δ\| ≤ 10%（单日软线，拍板③）。**诚实注记（干跑报告必写）**：09-09 出车目标 956 = 实测回填（plan CSV 同源性质，同 KW36/37），Δ 含回填偏置，仅验管线与量级，不验「前瞻计划」精度 |
| G3 | **ETA 全量对打（09-09 当日实际出站全部车，~956 台，round 1 F-LOW-04 扩样）**：每车取「出口时刻 −4h 最近事件所在站」为固定查询点（可复现规则；该站残差行缺失 → 跳过并计 skip 数）→ p50 估算 vs 实测剩余（查询点→出口事件）；报告 **中位 / P90 / max + skip 率**；软线 = 中位 ≤ 30min（W1 门同口径），超了如实报告不修 |
| ~~G4~~ | **删除**（原「主干序 diff + 用户确认」随 §2.3 范围收缩消失；主干序若 Phase 3 需要再按新设计立项） |

## 5. 拍板包（v1.1，用户决策项）

> **用户拍板（2026-09-28）：6 项全部按推荐。**

1. **① ETA 菜单 v1 范围收缩 = 仅 4 下线点**（中间站 ETA → Phase 3 takt 图最短路，与 T5.3 v2 同机制；主干序表不建）。依据：round 1 阻断项 F-BLK-01 实证（相邻对 takt 覆盖 5.6%、>91% 断对）+ A4 实测 232 站与 W1 119 口径不一致。
2. **② ETA 降级边界**：目标非下线点 / 残差双缺 n<30 / 车不存在 = 明确报错不猜；返修车 = 环内站残差（直查，覆盖实测充足）+ 标注 + 环残差参考。
3. **③ G2 单日锚点软线 ±10%**（M9 的 5pp 是 9 天窗中位口径，单日不适用；M5 五天在线验收班次级 ±10% 硬门不受影响）+ 目标 = 回填 956 的诚实注记。
4. **④ 控制台/快照默认开**（M5 留痕必备）；**锚点前 Δ = 观测进度（不上红黄绿）、锚点后正式 grade**（班初假深红整改）；replay 模式 S1–S4 显示「不适用·时间穿透」。
5. **⑤ 干跑窗 = 2026-09-09（周三，11h/11h，目标 956 回填）24h = 288 步**。
6. **⑥（新增）`alert_engine.py` 允许唯一最小改动 = 纯读 `deb_state()`**（零语义变化 + 单测钉死；替代方案 = dashboard 直读私有 `engine._deb`，不推荐）。

## 6. 冻结与回归纪律（v1.1）

- 02/03/predictor/stream_pipeline/common **零改动**；alert_engine 仅 +`deb_state()` 纯读（拍板⑥）；01_boot_online 仅 +round_hook 参数 + main() T6 ctx + 动态班次解析（DSS_CONSOLE=0 时逐字不变——单测 + G1 回归双保险）。
- **T6 v1 库写 = 0**（无新维表；快照本地留痕）。
- 时区纪律：新 SQL 一律 `SET TIME ZONE 'Asia/Shanghai'`；ETA 查询历史用 `DATE_EVT <= as_of`（查历史非预测，因果上界语义与主循环 `< now` 区分注明）。
- M9/M10 重跑零影响：02/03 走 `run_rolling` 独立管线，不经 `run_replay`（agy C5 实证）；round_hook 默认 None。
- 干跑/门脚本留 `scratch/`；快照 gitignore 已覆盖（agy A10 `git check-ignore` 实证）。

## 7. 变更记录

- **v1**（2026-09-27）：初稿（T6.3a 主干序 + 中间站切片 ETA）。
- **v1.1**（2026-09-27，agy round 1 = block 后整改，2 阻断 + 6 中 + 5 低全处理）：
  - **F-BLK-01**（中间站切片 5.6% 覆盖必断）→ §2.3 范围收缩为 4 下线点；中间站 → Phase 3；T6.3a/G4 删除；拍板①。
  - **F-BLK-02**（is_complete 丢跨日中班）→ §2.1/§3 T6.0 动态班次解析（`attr_start <= now < end`），G1 新增 21:30 锚点帧存在性判据。
  - **F-MED-01** → 快照双轨 pred（shift + day_total，中班时段填 day_total）。
  - **F-MED-02** → 随主干序删除消失。
  - **F-MED-03** → §2.3 CLI 独立连库只读（不读内存池），flag 取最近事件 MDS_DATA[139]。
  - **F-MED-04** → §2.1 Δ 语义分层（锚点前观测进度/锚点后 grade）+ 拍板④。
  - **F-MED-05** → §2.1 replay 模式诊断显示层拦截（不改冻结代码）+ 干跑报告注记。
  - **F-MED-06** → 快照 alert_state + `alert_engine.deb_state()` 纯读（拍板⑥）。
  - **F-LOW-01** → tag 防覆盖（-r2）；**F-LOW-02** → G1 回归机械化判据（两跑 diff + 单测）；**F-LOW-03** → pred(α=1.0) 标注；**F-LOW-04** → G3 扩为当日全量出站车（中位/P90/max + skip 率）；**F-LOW-05** → 随主干序删除消失。
  - 新增实证入文：predict 24ms/轮、SQL 预算 <60ms/轮、120h/168h 池差 0 台、残差表覆盖（ALL 全主站 n≥30 / 返修站 n>1000）、09-09 排班 956 回填。
- **v1.1 派审前自修**（2026-09-27，自审 3 处自洽性缺口）：① day_total/shift 的 target 预填语义钉死（计划已知即预填，缺失 = null；A/pred/delta/grade 按 scope 生效期）；② 帧首行固定前缀 `[DSS] ` = G1 帧计数机械化判据（288 行）；③ T6.0 测试补缺计划日语义（inferred 日班次照定 + 目标=—，与 T1-lite「不猜数」一致）+ hook=None 回归测试降级兑底说明（FakeConn 不足 → 静态断言 + G1 两跑 diff）。
- **v1.2**（2026-09-28，agy round 2 = 通过（0 阻断 / 1 中 / 1 低）后整改，2 项全处理）：
  - **F-R2-01**（中）→ §2.3「已出站」判据扩展为 最近事件 ∈ 下线点 ∪ `common.POST_EXIT_STATIONS`（K3IS230/09IS360；r2 实测 09-09 76% 出站车最新事件在延续站，原判据会误判「仍在车间」→ 残差报错）。
  - **F-R2-02**（低）→ G2 日班实际出口窗口统一闭区间 `[04:45, 16:30]`（与 a_of `<= end` 的 16:30:00 双计口径同源，防其他日期边界 1 台对不上）。
- **v1.3**（2026-09-28，T6.1 实施前自审）：§2.1/§2.2 示例数字自洽性修正——`pred.shift.target` 示例 956→**478**（split_target(11,11,956) 分摊班次目标，与引擎/M9 门同源；956 属 day_total 轨）、Δ 示例 −89%→−74%、anchor_in_min 295→180（05:00 → 3h 锚点 08:00 = 180min，锚点 = work_start+{3,4,5,6}h 的下一锚点）。口径无变化（引擎口径即事实源）。
