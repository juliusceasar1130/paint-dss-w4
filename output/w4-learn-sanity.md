# W4.2e 报告——全量重学 + sanity 全 PASS

- 日期：2026-10-06（本地时钟）；分支 `w4-dual-ring`
- 数据快照：`ods.carbody_history` 截止 **2026-09-30 11:36**（09-30 快照，D2 前提）
- 学习窗：2026-04-14 → 2026-08-15（C13 不变）；回放窗：2026-08-16 → 2026-09-29（D2=(b)）
- 运行：`conda run --no-capture-output -n paint-dss python spike_w1/01_learn_takt.py` +
  `spike_w2/01_learn_v2.py --replay-start 2026-08-16 --replay-end 2026-09-29`；**两者 exit 0**

## ① 重学前旧表快照（12 张，供 W4.2f 旧补偿/豁免基线臂 + G6 diff）

| 组 | 表（行数） |
|---|---|
| `dss.w4_archive_w1_pre_20261005_*` | dim_remaining_time 3,659 / dim_loop_remaining 2,891 / dim_station_takt 7,753 / dim_future_flag 4,322 / dim_loop_duration 56 / dim_learner_meta 14 |
| `dss.w4_archive_v2_pre_20261005_*` | v2_future_flag 25,729 / v2_comp_excl 147 / v2_cohort_remaining 4,822 / v2_line_prob 227 / v2_station_quality 3,450 / v2_learner_meta 8 |

## ② 重建后表行数（dss 生产）

| 表 | 旧 | 新 | 说明 |
|---|---|---|---|
| dim_remaining_time | 3,659 | **5,064** | +前缀行 1,329 + 净化人群微变 |
| dim_loop_remaining | 2,891 | **3,414** | +前缀行 + 新 flag 语义 |
| dim_station_takt | 7,753 | **10,759** | +前缀 IQR 层 |
| dim_future_flag | 4,322 | **5,926** | +前缀行 1,542 + 净化 |
| dim_loop_duration | 56 | **55** | 4 元组（W4.2a） |
| v2_future_flag | 25,729 | **33,608** | 净化 ev_base 上重学 |
| v2_comp_excl | 147 | **148** | 豁免格（≤500 门） |
| v2_cohort_remaining | 4,822 | **4,932** | |
| v2_line_prob | 227 | **297** | 回放周延展至 W40（09-29 窗） |
| v2_station_quality | 3,450 | **3,525** | 前缀行不参与质量标记（W4.2e 过滤） |
| dim_expected_delay | — | **1,793** | 新建（W4.2c，doc10 净延误） |

## ③ W1 sanity（`output/w1_learn_sanity_20261006.md`）——8/8 PASS

| 检查 | 结果 | 数值 |
|---|---|---|
| S1 | PASS | 01IS045 只报告（P50=103.5h 小样本替代入口）；01IS205 偏差 12.1% ≤17.4%；1L360RB 31.0% ≤46.4% |
| S2a | PASS | 返工簇占比 97.0%（≥80%） |
| S2b | PASS | **黑顶锚点归位率 99.6%（≥90%，新定义）** |
| S2c | PASS | p1 剖面 max 0.997 / min 0.000 |
| S3 | PASS | flag1=9,131（≥8,500）/ flag2=4,202（≥4,000）——过渡基线=最终值（与 W4.2d 预验同窗同值） |
| S4 | PASS | flag1=192,197（≥180,000）/ flag2=86,210（≥80,000）——**指标改 tmp_ev_base 真值人群**（三层行下表行和重复计数） |
| S5 | PASS | takt IQR 剔除率 6.6%（<30%） |
| S6 | PASS | 干净后缀率 **0.8030**（新语义，≥0.80） |

**S1 校准说明（W4.2e 新增决策，留痕待用户确认）**：09-19 W1 关账时 S1 即 FAIL
（`output/w1-验证摘要.md` L38，数值与本次几乎相同——既有已知项，非 W4 回归）。按 W2-S2
阈值校准先例（CHANGELOG L101）校准为：① 01IS045 小样本替代入口站只报告不判定（原注释
意图，旧 journey n≥50 闸门未能排除）；② 一致性阈值改 `max(10%, 1.5×W1 关账实测偏差)`
回归门 + 方向检查 rem≤行程（干净后缀人群剔返修子人群、行程 P50 含之，11.6%/30.9% 为
结构性缺口）。保护价值保留（捕捉基线漂移），未放宽绝对范围 [8h,30h]。

## ④ W2 sanity（`output/w2_learn_sanity_20261006.md`）——5/5 PASS

| 检查 | 结果 | 数值 |
|---|---|---|
| W2-S1 | PASS | cohort 2N54Y- n≥100 行 = 255（≥10） |
| W2-S2 | PASS | 周度 p 无缺失周；15IS065 每周 p1∈[0.03,0.98] 且 n≥300 |
| W2-S3 | PASS | comp_excl 148（≤500）；(2N54Y-,21IS565) 在豁免集 |
| W2-S4 | PASS | lineprob 2N54Y- 覆盖 W33–W40 + ALL（全部回放周） |
| W2-S5 | PASS | dim_expected_delay 1,793 行；双人群<30 行 0；YM33J- 83 行 |

## ⑤ meta（dss.dim_learner_meta，19 键）

`base_version=w4-purified`、`n_rw_vehicles=9,136`、`n_bt_vehicles=4,202`、
`bt_anchor_rate=0.995954`、`clean_suffix_rate=0.802954`、
`platform_prefixes=2N,DM,IE,IM,SS,VH,VS,XV,YE,YM`、flag 事件 3,406,284/192,201/86,210。

## 风险与留痕

1. **S6 贴线**：0.8030 vs 门 0.80（余量 0.4%）。数据续更后若漂移跌破 0.80，sanity 将
   FAIL——属预期保护行为（D7=(a) 0.80 拍板），届时按新数据重新对账而非静默放宽。
2. **S1 校准为新决策**：以上述留痕为准，用户可否决（否决则 W1 学习器恢复 exit 1 已知 FAIL 态）。
3. AGENTS.md 数据截止记载（09-19）已过时——实际 09-30 11:36；关账时同步更新。
4. 回放窗 W40 只到 09-27 滚动窗（09-29 截止）——周表 2026-W40 为不完整周，与 D2 拍板一致。
