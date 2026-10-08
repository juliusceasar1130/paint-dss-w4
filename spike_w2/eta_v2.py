# -*- coding: utf-8 -*-
"""W2 ETA 纯逻辑：W1 直接估计器 + ① 线别 cohort 混合 + ② 周度 p + ⑥ 补偿豁免 + ⑤ 质量标记（TDD）。
W4.3a 新增：双环统一在线引擎（Doc14 §5 逐字 + docs/15 §0.2 规格）——
常量四集合 / resolve_effective_flag / resolve_hierarchical / resolve_expected_delay /
estimate_event_unified（effective_flag 直通通道）/ estimate_for_replay 共享分发（DSS_DUAL_RING 门）。
旧入口 estimate_event 与 quality_mark/resolve_p 等保留不动（DSS_DUAL_RING=0 回滚路径 + 既有测试锚点）。

W1 表行形状（model 含平台前缀行 + 'ALL' 三层，见 spike_w1/eta.py 头注）：
  rem:      {(station, model): (p50, mean, p90, n)}
  loop_rem: {(station, flag, model): (p50, mean, p90, n)}
  loop_dur: {(flag, model): (l, l_mean, l_p90, n)}   # W4.2a 2→4（l=P50 兼容名；消费端 row[0]/row[-1] 零改动）
  takt:     {(frm, to, model): (p50, mean, p90, n)}
W2 表行形状：
  fut_week: {(station, model, week): (p1, p2, n)}   # 滚动 8 周学习（week = "YYYY-Www"）
  fut_all:  {(station, model): (p1, p2, n)}         # 全窗 04-14→08-15（⑥a 前环人群净化）
  cohort:   {(station, cohort, model): (p50, mean, p90, n)}   # cohort ∈ ("08", "09")
  lineprob: {(model, week): (w08, n)}               # 滚动 8 周线概率；week=WEEK_ALL 为全窗
  quality:  {(model, station): mark}                # mark ∈ ok/low_n/mixed/both
W4 新增（doc10 净延误表）：
  expected_delay: {(station, model): (delay_mean_s, delay_p50_s, n_fut, n_clean)}
  # ⚠️ 列序与 rem/loop_rem/loop_dur 相反（mean 在前、p50 第二）：必须显式索引；
  # 两档均消费 row[1]=delay_p50_s（W4.4b 修订 2026-10-06：shift 档原 row[0]=mean，G2 阈值计数
  #  实测证伪「班次级加法无偏」——延误分布右偏 mean≫p50（91% 行 mean>2×p50），mean 列把 ETA
  #  系统性推晚（D 臂 20.78%/−20.78% 低估；b 臂 p50 = 10.60%/+0.71% ≈无偏）；mean 列留表内
  #  仅诊断；rem/loop_rem 列不得连带 p50 化（cc bp 臂 11.17% 反证会恶化）；
  # 回退判定 = 双人群双门槛（n_fut≥30 且 n_clean≥30，缺一降层），独立于其余表 n≥30 通用门槛；
  # 统一引擎 / predictor / dashboard 三处调用点必须走 resolve_expected_delay，禁止行内解析。

α 语义（doc10 L121 / 双审 B10）：α 单次作用于 rem（含 base+delay），在消费点（predict 比较式
rem/α）施加；estimate_event_unified 内部不消费 α、无双重缩放。doc14 §5 伪码的
tables.get('alpha') 不采纳（否则与消费点 α 双重缩放）。
"""
import os
from typing import Optional

ALL = "ALL"
WEEK_ALL = "ALL"
P_WEEK_MIN_N = 50          # 周度 p 行 n≥50 才用，否则回退 fut_all（周窗小样本不稳）
COHORT_MIN_N = 30          # cohort 表行 n≥30 才用（与主分布同门槛）
LINEPROB_MIN_N = 100       # 线概率行 n≥100 才用（分派比小样本噪声大）
MIN_N = 30                 # 主/环分布行 n 门槛（与 W1 resolve 一致）
DELAY_MIN_N = 30           # expected_delay 双人群双门槛：n_fut 与 n_clean 各自 ≥30（缺一降层）

# ================= 物理常量四集合（单一事实源 = 本模块，W3 import；值与 Doc14 §5 逐字一致） =================

# 1. 通用出环物理工位集合（双环通用解冻网关：三线网关组、注蜡走廊与出站口全集，共 37 站）
UNIVERSAL_EXIT_STATIONS: frozenset = frozenset({
    # 1. 三线报交物理网关组与直接移载站
    '25IS010', '25IS110', '25IS210', '25IS365', '25IS395', '25IS460',
    # 2. 注蜡与总装主干支路 A (32IS010 系)
    '32IS010', '32IS230', '32IS408', '33IS005', '33IS025',
    '35IS025', '35IS045', '35IS050',
    # 3. 总装并行支路 B (32IS015 / 42IS 系，实测高频补漏站)
    '32IS015', '32IS130', '32IS406', '34IS230', '34IS340',
    '35IS075', '35IS080', '41IS315',
    '42IS050', '42IS210', '42IS230', '42IS245', '42IS455',
    # 4. 高频交接与出站走廊必经站
    'L3ATK11IS01', 'L3ATK11IS02', 'K3IS055', 'K3IS140', 'K3IS230',
    'K2IS055', 'K2IS075', 'K1IS135', '1J440RB', '09IS360',
})

# 2. 黑顶二喷在线准入白名单（报交入口簇 6 站 88.1% + 打磨返修线 4 站 10.8%，含 14IS120）
BLACKTOP_ONLINE_WHITELIST: frozenset = frozenset({
    '24IS185', '24IS115', '22IS170', '23IS170', '23IS110', '22IS110',
    '14IS120', '14IS065', '13IS065', '15IS065',
})

# 3. 一喷喷房前排队缓存线（黑顶假首发闪现高发区）
SPRAY_QUEUE_BLACKLIST: frozenset = frozenset({'14IS260', '13IS260'})

# 4. 面漆烘房出口站（黑顶物理前置门槛；Doc14 勘误：20IS010 移出 = 2 线储存线入口，
#    紧随 17IS305 中位间隔 46s，实测 0 辆次早于三出口——W4.0c 留痕）
OVEN_OUT_STATIONS: frozenset = frozenset({'16IS305', '17IS305', '18IS345'})

# 5. 出环守卫站（Doc14 v1.4 R3'，2026-10-06 W4.4c (α)）：三线通往注蜡/总装的物理交接出口
#    = 净化窗三站（sqlbase.tmp_loop_bounds 的 t_rw_end/t_bt_end 站集，镜像离线语义）。MDS 139 位
#    出环后残留不清除（实测 2026-09 全窗：返修车 96% / 黑顶车 45%）→ 在线状态机以本站集
#    感知「已出环」，R3'/R2.4 守卫将残留位冻结为主线 '0'。
LOOP_EXIT_GATEWAYS: frozenset = frozenset({'25IS010', '25IS110', '25IS210'})


def resolve_main(table: dict, station: str, model: str, min_n: int = MIN_N) -> Optional[float]:
    """W1 主分布两级回退：(station, model) → (station, 'ALL')；行尾 n < min_n 视为不可用。"""
    for m in (model, ALL):
        row = table.get((station, m))
        if row is not None and row[-1] >= min_n:
            return row[0]
    return None


def _row_n(table: dict, key: tuple, min_n: int):
    row = table.get(key)
    if row is not None and row[-1] >= min_n:
        return row
    return None


def resolve_cohort_p50(cohort: dict, station: str, model: str,
                       lineprob: dict, week: str) -> Optional[float]:
    """① 线别 cohort 混合：P50 = w08·P50_08 + w09·P50_09。
    各 cohort P50 走 model→ALL 回退（n≥COHORT_MIN_N）；线概率走 (model, week) → (model, WEEK_ALL)
    → (ALL, week) → (ALL, WEEK_ALL)（n≥LINEPROB_MIN_N）。任一缺失 → None（调用方回退 W1 主分布）。
    非后见：线概率用过去 8 周学出（Task 3），不是该车自己的实际走线。
    """
    if not cohort:
        return None

    def c50(coh):
        for m in (model, ALL):
            row = _row_n(cohort, (station, coh, m), COHORT_MIN_N)
            if row is not None:
                return row[0]
        return None

    p50_08, p50_09 = c50("08"), c50("09")
    if p50_08 is None or p50_09 is None:
        return None
    w08 = None
    for m in (model, ALL):
        for wk in (week, WEEK_ALL):
            row = _row_n(lineprob, (m, wk), LINEPROB_MIN_N)
            if row is not None:
                w08 = row[0]
                break
        if w08 is not None:
            break
    if w08 is None:
        return None
    return w08 * p50_08 + (1.0 - w08) * p50_09


def resolve_p(fut_week: dict, fut_all: dict, station: str, model: str,
              week: str) -> tuple:
    """② 周度 p：(station, model, week) n≥P_WEEK_MIN_N → 用；否则 fut_all (station, model)
    （model→ALL 回退，行 n≥30，与 W1 resolve 默认门槛一致）；全缺 → (0.0, 0.0)。"""
    if fut_week:
        for m in (model, ALL):
            row = _row_n(fut_week, (station, m, week), P_WEEK_MIN_N)
            if row is not None:
                return row[0], row[1]
    if fut_all:
        for m in (model, ALL):
            row = _row_n(fut_all, (station, m), MIN_N)
            if row is not None:
                return row[0], row[1]
    return 0.0, 0.0


def estimate_event(t: int, station: str, flag: str, model: str, week: str,
                   tables: dict, comp_excl: frozenset = frozenset()
                   ) -> tuple:
    """单事件 ETA（W1 直接估计器 v2）。返回 (eta 绝对秒|None, used_cohort bool)。

    flag ∈ {1,2} → 环分布 P50（model→ALL，n≥30）；不加补偿（已在环内），不走 cohort。
    flag = 0 → 主分布 P50：① 先试 cohort 混合（缺 → W1 rem 表）
              + 补偿 p1·L1 + p2·L2（② 周度 p；⑥b (model,station) ∈ comp_excl → 补偿 = 0）。
    L1/L2 走 loop_dur model→ALL（n≥30），缺 → 0。
    """
    if flag in ("1", "2"):
        base = None
        for m in (model, ALL):
            row = tables["loop_rem"].get((station, flag, m))
            if row is not None and row[-1] >= MIN_N:
                base = row[0]
                break
        if base is None:
            return None, False
        return t + int(round(base)), False

    used_cohort = False
    base = resolve_cohort_p50(tables.get("cohort"), station, model,
                              tables.get("lineprob"), week)
    if base is not None:
        used_cohort = True
    if base is None:
        base = resolve_main(tables["rem"], station, model)
    if base is None:
        return None, used_cohort

    p1 = p2 = 0.0
    if (model, station) not in comp_excl:
        p1, p2 = resolve_p(tables.get("fut_week"), tables.get("fut_all"),
                           station, model, week)

    def ld(flagc):
        for m in (model, ALL):
            row = tables["loop_dur"].get((flagc, m))
            if row is not None and row[-1] >= MIN_N:
                return row[0]
        return 0.0

    eta = t + int(round(base + p1 * ld("1") + p2 * ld("2")))
    return eta, used_cohort


def quality_mark(quality: dict, model: str, station: str) -> str:
    """⑤ 质量标记：未标记默认 'ok'。mark ∈ ok/low_n/mixed/both。"""
    return quality.get((model, station), "ok")


# ================= W4.3a 双环统一在线引擎（Doc14 §5 逐字 + docs/15 §0.2） =================

def _model_tiers(model: str) -> list:
    """层级回退序列：车型 → 平台前缀 LEFT(model,2) → ALL（与 W1 学习侧三层行对齐）。
    model 为空/'ALL' → 仅 ('ALL',)；len<2 的车型无意义前缀层。"""
    tiers = []
    if model and model != ALL:
        tiers.append(model)
        if len(model) >= 2:
            tiers.append(model[:2])
    tiers.append(ALL)
    return tiers


def resolve_effective_flag(station: str, flag137: str, flag139: str,
                           passed_oven: bool, loop_exited: bool = False) -> str:
    """双环统一在线物理状态机解析器（Doc14 §5，v1.4 出环守卫修订）。纯函数。

    R1：station ∈ 37 站通用出环网关 → 强制解冻 '0'（优先级最高；解冻后按车型干净基线行预测）；
    R2：139=2 四重防护：2.1 单色车（137≠1，含 '-' 缺失）→ '0'；2.2 一喷排队缓存线且未出炉 → '0'；
        2.3 未出烘房且不在白名单 → '0'；2.4 出环守卫（v1.4）：loop_exited → '0'——
        出炉/白名单防御只护未出炉态，出环后车恒已出炉（防御穿透）且真库实测 45% 黑顶车
        出环后 raw '2' 残留（agy-2 审核纠偏 1，2026-10-06）；否则 '2'；
    R3'：139=1 且未出环（loop_exited=False）→ '1'（打磨站集中度 97.12%）；139=1 且出环 → '0'——
        raw 位出环后残留不清除（实测返修车 96%），把离线净化窗语义 [首 raw-1, 25IS 出口]
        镜像回在线（Doc14 v1.4，2026-10-06 W4.4c (α)）；
    其余 '0'。

    loop_exited 缺省 False = 旧语义（DUAL_RING=0 回滚路径 / 静态 effective_flag 直通 / 既有单测不变）。
    """
    if station in UNIVERSAL_EXIT_STATIONS:
        return '0'
    if flag139 == '2':
        if flag137 != '1':
            return '0'
        if station in SPRAY_QUEUE_BLACKLIST and not passed_oven:
            return '0'
        if not passed_oven and station not in BLACKTOP_ONLINE_WHITELIST:
            return '0'
        if loop_exited:
            return '0'
        return '2'
    if flag139 == '1':
        return '0' if loop_exited else '1'
    return '0'


def resolve_hierarchical(table: dict, key: tuple, model: str, mode: str,
                         min_n: int = MIN_N) -> Optional[float]:
    """三层回退（车型 → 平台前缀 → ALL，行尾 n≥min_n）；mode 消费列：
    vehicle → row[0]（P50/l）；shift → row[1]（mean/l_mean）。非 shift 一律按 vehicle 取（防误用）。"""
    for m in _model_tiers(model):
        row = table.get(key + (m,))
        if row is not None and row[-1] >= min_n:
            return row[1] if mode == 'shift' else row[0]
    return None


def resolve_expected_delay(tables: dict, station: str, model: str,
                           mode: str) -> Optional[int]:
    """doc10 净延误专用解析器（双审 agy 建议 3）：车型 → 前缀 → ALL → None；
    双人群双门槛（n_fut≥30 且 n_clean≥30，缺一降层）；显式列索引：
    两档均 → row[1]=delay_p50_s（mode 参数保留契约显式性，两档取值语义相同）。
    W4.4b 修订（2026-10-06）：shift 档原 → row[0]=delay_mean_s，G2 阈值计数实测
    （D 臂 20.78%/−20.78% 低估；cc 独立重跑复现 + agy-2 交叉一致）证伪 doc10 §2.1
    「班次级加法无偏」假设——延误分布肥尾右偏（mean ≫ p50），mean 列把 ETA 系统性推晚，
    有偏阈值计数下班次产量低报；p50 臂（b 实验）= 10.60%/+0.71% ≈无偏。mean 列留表内仅诊断。
    统一引擎 / predictor / dashboard 三处调用点必须走此函数，禁止行内解析。"""
    table = tables.get("expected_delay") or {}
    for m in _model_tiers(model):
        row = table.get((station, m))
        if row is not None and row[2] >= DELAY_MIN_N and row[3] >= DELAY_MIN_N:
            return int(row[1])  # 两档均消费 delay_p50_s（W4.4b 2026-10-06）
    return None


def resolve_expected_delay_shift(tables: dict, station: str, model: str,
                                 shift: str) -> Optional[int]:
    """W4.4h 班次型档净延误解析（DSS_SHIFT_TIER_DELAY=1 消费）：车型 → 前缀 → ALL → None；
    与 resolve_expected_delay 同纪律（显式列索引：row[1]=delay_p50_s；双人群双门槛
    n_fut≥30 且 n_clean≥30，缺一降层）；键 = (station, model, shift)，shift ∈ {'DAY','MID'}。
    缺行/门槛不过/表缺失（空 dict）→ None，调用方回退共享行（不猜数纪律；
    回退路径与开关 off 完全一致 = 零漂移）。"""
    table = tables.get("expected_delay_shift") or {}
    for m in _model_tiers(model):
        row = table.get((station, m, shift))
        if row is not None and row[2] >= DELAY_MIN_N and row[3] >= DELAY_MIN_N:
            return int(row[1])
    return None


def entry_shift_of(ts) -> str:
    """W4.4h 入口事件班次归属（固定带，计划无关，纯函数）：
    DAY = [04:45, 16:30)，MID = [16:30, 次日 04:45)——与 shifts.py 归属窗同口径
    （日班窗末 16:30 含 30min 交班尾；提早开工 04:45–正式开班计日班）；低谷尾隙
    （中班窗末, 04:45）并入 MID 尾（事件量可忽略，固定带无空档）。
    ts 必须 aware（Asia/Shanghai 工厂时）；返回 'DAY'/'MID'。"""
    m = ts.hour * 60 + ts.minute
    return "DAY" if 4 * 60 + 45 <= m < 16 * 60 + 30 else "MID"


def shift_tier_delay_enabled() -> bool:
    """DSS_SHIFT_TIER_DELAY 门（W4.4h，2026-10-07 用户拍板）：=1 → 统一引擎 delay 优先
    查班次型档行（expected_delay_shift，入口事件班次归属）；缺行/门槛不过回退共享行；
    默认 0 = 现状（共享行，零漂移）。仅影响 estimate_event_unified/estimate_for_replay
    的 DUAL_RING=1 路径；旧引擎路径不消费。"""
    return _strict01("DSS_SHIFT_TIER_DELAY", 0) == 1


def estimate_event_unified(t: int, station: str, flag137: Optional[str],
                           flag139: Optional[str], model: str, week: str,
                           tables: dict, mode: str = 'vehicle',
                           passed_oven: bool = False,
                           comp_excl: frozenset = frozenset(),
                           effective_flag: Optional[str] = None,
                           loop_exited: bool = False,
                           entry_shift: Optional[str] = None) -> tuple:
    """统一单车 ETA 与班次产量在线推断核心函数（Doc14 §5 语义 + W2 既有 week/comp_excl 超集参数）。

    W4.4h（2026-10-07）：entry_shift（'DAY'/'MID'/None = 入口事件班次归属，
      eta_v2.entry_shift_of 派生）+ DSS_SHIFT_TIER_DELAY=1 → delay 优先班次型档行
      （resolve_expected_delay_shift），缺行/门槛不过回退共享行；
      开关 off 或 entry_shift=None → 与 W4.4h 前逐位一致（零漂移）。
    返回 (eta 绝对秒|None, used_cohort bool)，与旧入口同形状。

    eff = effective_flag if effective_flag is not None \
        else resolve_effective_flag(station, flag137, flag139, passed_oven, loop_exited)
    ⚠️ loop_exited（Doc14 v1.4，W4.4c (α)）= 出环守卫信号（池状态 VehicleState.loop_exited /
      单车 CLI 25IS 出口轨迹派生）：139='1'/'2' 出环后残留位 → 主线 '0'；缺省 False = 旧语义。
      仅全状态机路径消费（effective_flag=None）；effective_flag 直通路径不消费（净化 flag 已含窗语义）。
    ⚠️ effective_flag = 离线净化 flag 直通通道（双审 agy 阻断项 3 修复）：
      02_replay_shift 回放端 / 单测端传 tmp_ev_base 已净化 flag（'0'/'1'/'2'），跳过在线状态机——
      离线净化打标即真值，在线状态机是其无状态镜像；若回放端改传 raw 137/139 + passed_oven，
      缺 137 位会把纯净黑顶（fl='2'）全部误杀回 '0'，G2 门判失真。在线端（predictor）恒传 None 走全状态机。
    分支 A（eff ∈ {'1','2'}）：rem = 环内剩余（车型→前缀→ALL，n≥30；vehicle→P50/shift→mean）；
      缺 → loop_dur L 兜底（同回退；vehicle→l/shift→l_mean）；再缺 → (None, False)。
      C8：loop_rem/loop_dur 训练目标为 station→exit（含网关后主线段），eta = t + round(rem) 不叠加任何项。
    分支 B（eff == '0'）：base = ① 线别 cohort 混合（与旧引擎同机制，model→ALL）
      → 缺则 rem 三层回退；base 缺 → (None, used_cohort)。
      delay = 0（station ∈ 37 站通用出环后）否则：W4.4h shift 档（DSS_SHIFT_TIER_DELAY=1
      ∧ entry_shift 非 None）优先 resolve_expected_delay_shift → 回退 resolve_expected_delay
      （缺 → 0）；新表替代旧 p×L1+p×L2（doc10 合流日语义）；comp_excl 参数仅旧路径消费，本函数不消费。
    α 不在本函数消费（消费点 rem/α 单次，doc10 L121 / 双审 B10，无双重缩放）。
    """
    eff = effective_flag if effective_flag is not None \
        else resolve_effective_flag(station, flag137, flag139, passed_oven, loop_exited)

    if eff in ('1', '2'):
        rem = resolve_hierarchical(tables['loop_rem'], (station, eff), model, mode)
        if rem is None:
            rem = resolve_hierarchical(tables['loop_dur'], (eff,), model, mode)
        if rem is None:
            return None, False
        return t + int(round(rem)), False

    used_cohort = False
    base = resolve_cohort_p50(tables.get('cohort'), station, model,
                              tables.get('lineprob'), week)
    if base is not None:
        used_cohort = True
    if base is None:
        base = resolve_hierarchical(tables['rem'], (station,), model, mode)
    if base is None:
        return None, used_cohort

    if station in UNIVERSAL_EXIT_STATIONS:
        delay = 0.0
    else:
        delay = None
        if shift_tier_delay_enabled() and entry_shift is not None:
            delay = resolve_expected_delay_shift(tables, station, model, entry_shift)
        if delay is None:
            delay = resolve_expected_delay(tables, station, model, mode)
        if delay is None:
            delay = 0.0
    eta = t + int(round(base + delay))
    return eta, used_cohort


def _strict01(name: str, default: int) -> int:
    """严格 1/0 解析（§0.5 契约：非法值报错，缺省默认值）。"""
    v = os.environ.get(name)
    if v is None:
        return default
    v = v.strip()
    if v not in ("0", "1"):
        raise ValueError(f"{name} 必须为严格 1/0（D6 回滚门纪律），当前: {v!r}")
    return int(v)


def _dual_ring(explicit: Optional[bool] = None) -> bool:
    """DSS_DUAL_RING 门（D6：默认 1 = 统一引擎生产默认，0 = 旧路径 30 秒回滚）。
    explicit 非 None 时用注入值（单测/调用点显式控制）；否则读环境变量（严格 1/0，§0.5）。"""
    if explicit is not None:
        return bool(explicit)
    return _strict01("DSS_DUAL_RING", 1) == 1


def dual_ring_enabled() -> bool:
    """DSS_DUAL_RING 门公开读（消费点：predictor / dashboard；单测 monkeypatch.setenv）。"""
    return _dual_ring(None)


def use_net_delay_enabled() -> bool:
    """DSS_USE_NET_DELAY 门（§0.5 / 双审 B11）：仅 DUAL_RING=0 时生效
    （1 = 旧引擎消费 expected_delay 替代 p×L，doc10 语义）；DUAL_RING=1 时新引擎恒消费、忽略本参数。"""
    return _strict01("DSS_USE_NET_DELAY", 1) == 1


def estimate_event_net_delay(t: int, station: str, flag: str, model: str, week: str,
                             tables: dict, comp_excl: frozenset = frozenset(),
                             mode: str = 'shift') -> tuple:
    """doc10 retrofit（DUAL_RING=0 + DSS_USE_NET_DELAY=1，G5 双重修正复查项）：
    旧引擎 flag=0 分支的 p×L 补偿 → expected_delay 替代（两档均 → delay_p50_s，W4.4b 修订）；
    flag ∈ {1,2} 环分布路径不动（不加 delay）。base 解析与旧引擎同机制（cohort→rem 两层）；
    comp_excl 豁免位与旧 comp 同门（(model,station) ∈ comp_excl → delay=0）；
    expected_delay 缺 → delay=0。返回 (eta|None, used_cohort)，同旧入口形状。
    """
    if flag in ("1", "2"):
        return estimate_event(t, station, flag, model, week, tables, comp_excl)
    used_cohort = False
    base = resolve_cohort_p50(tables.get("cohort"), station, model,
                              tables.get("lineprob"), week)
    if base is not None:
        used_cohort = True
    if base is None:
        base = resolve_main(tables["rem"], station, model)
    if base is None:
        return None, used_cohort
    delay = 0.0
    if (model, station) not in comp_excl:
        d = resolve_expected_delay(tables, station, model, mode)
        if d is not None:
            delay = float(d)
    return t + int(round(base + delay)), used_cohort


def estimate_for_replay(t: int, station: str, flag_purified: str, model: str,
                        week: str, tables: dict, comp_excl: frozenset = frozenset(),
                        dual_ring: Optional[bool] = None,
                        entry_shift: Optional[str] = None) -> tuple:
    """回放/影子静态参照共享分发（双审 cc 阻断项 3 修复 → G1 恢复同引擎一致性门）。

    DSS_DUAL_RING=1 → estimate_event_unified(mode='shift', effective_flag=flag_purified 直通，
      跳过在线状态机——回放端不维护 137/passed_oven 轨迹态）；
    DSS_DUAL_RING=0 → DSS_USE_NET_DELAY=1 → doc10 retrofit（p×L → expected_delay，
      §0.5 / B11）；=0 → 旧 estimate_event（comp_excl 语义保持）。两侧同调本分发：
    滚动侧（predictor 静态参照）与静态参照侧（02_shadow_replay L266/L295）差异只来自
    滚动池/α 状态；回滚路径两侧同走旧引擎、门判语义保持。
    W4.4h：entry_shift（'DAY'/'MID'/None）仅透传 DUAL_RING=1 分支（班次型档 delay）；
    ND/旧路径不消费。
    """
    if _dual_ring(dual_ring):
        return estimate_event_unified(t, station, None, flag_purified, model,
                                      week, tables, mode='shift',
                                      comp_excl=comp_excl,
                                      effective_flag=flag_purified,
                                      entry_shift=entry_shift)
    if use_net_delay_enabled():
        return estimate_event_net_delay(t, station, flag_purified, model, week, tables,
                                        comp_excl)
    return estimate_event(t, station, flag_purified, model, week, tables, comp_excl)
