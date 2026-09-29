# -*- coding: utf-8 -*-
"""T4 缺口告警引擎（docs/05 v3 拍板定版，2026-09-25）。

口径单一事实源 = docs/05 §2.2（10 条）；模块分工：
- split_target / grade / Debouncer：纯函数/纯状态（T4.1，全合成单测）
- build_station_section：两级段映射 bootstrap（T4.2，profile 走 conn；前缀码纯）
- AlertEngine.evaluate：scope Δ + 去抖确认 + 瓶颈分层 + 自洽恒等式（T4.3）
拍板继承：① 全天主视图仅中班时段 [16:30, 次日 04:00) 激活（F10）；
② 班次视图目标 = H 占比 split_target；⑤ 回测 alerts 只进 JSON/MD（DDL 就位但回测不写库）。
时区纪律同 T2/T3：全 aware Asia/Shanghai；naive 抛 TypeError。
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from spike_w3.common import CST, DSSConfig, FLAG_BLACKTOP, FLAG_REWORK

# ---------------------------------------------------------------------------
# T4.0 DDL（dss 可写；幂等。回测只读不写库（拍板⑤），生产期再启用写入）
# ---------------------------------------------------------------------------

_WORK_SHIFT_ALERT_DDL = (
    "CREATE TABLE IF NOT EXISTS dss.work_shift_alert ("
    " id BIGSERIAL PRIMARY KEY, "
    " scope TEXT NOT NULL, "            # 'day_total' | 'shift'
    " sid TEXT NOT NULL, "              # day_total: 'YYYY-MM-DD'；shift: 'YYYY-MM-DD_day|mid'
    " scope_date DATE NOT NULL, "
    " ts TIMESTAMPTZ NOT NULL, "        # 确认时刻
    " prev_level TEXT, "                # None = 基线首档
    " level TEXT NOT NULL, "            # 'red' | 'yellow' | 'green'
    " delta DOUBLE PRECISION NOT NULL, "
    " gap_n INTEGER NOT NULL, "
    " bottleneck_n INTEGER NOT NULL, "
    " rework_n INTEGER NOT NULL, "
    " blacktop_n INTEGER NOT NULL, "
    " sections JSONB NOT NULL, "
    " created_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    ")"
)

_DIM_STATION_SECTION_DDL = (
    "CREATE TABLE IF NOT EXISTS dss.dim_station_section ("
    " station TEXT PRIMARY KEY, "
    " prefix_section TEXT, "            # 一级：站号前 2 位段码映射
    " area_mode TEXT, "                 # 二级：profile 众数 area
    " n INTEGER, "                      # 二级配对样本数
    " section TEXT NOT NULL, "          # 最终段（docs/02 八段标准词汇 | '待确认'）
    " status TEXT NOT NULL, "           # 'auto' | 'confirmed'
    " updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
    ")"
)


def ensure_work_shift_alert(conn) -> None:
    """dss.work_shift_alert DDL（幂等；告警 = 确认转换含基线，不去重 UNIQUE）。"""
    cur = conn.cursor()
    cur.execute(_WORK_SHIFT_ALERT_DDL)


def ensure_dim_station_section(conn) -> None:
    """dss.dim_station_section DDL（幂等；build_station_section 后 upsert 落表）。"""
    cur = conn.cursor()
    cur.execute(_DIM_STATION_SECTION_DDL)


def _json_dumps(v) -> str:
    return json.dumps(v, ensure_ascii=False)


# ---------------------------------------------------------------------------
# T4.3 AlertEngine（docs/05 §2.1/§2.2；scope = shift（H 拆分目标）+ day_total
# （F10 激活窗仅中班时段；F3 union A 单计 + end_later 池窗）
# ---------------------------------------------------------------------------

@dataclass
class Alert:
    """确认转换事件（含基线 prev_level=None）。sections = 瓶颈车按段计数（
    返修/黑顶在 rework_n/blacktop_n 单列，sections 为全瓶颈车直方图）。"""
    scope: str                    # 'day_total' | 'shift'
    sid: str                      # day_total: 'YYYY-MM-DD'；shift: 'YYYY-MM-DD_day|mid'
    t: datetime
    prev_level: Optional[str]
    level: str                    # 'red' | 'yellow' | 'green'
    delta: float                  # 确认时刻 Δ
    gap_n: int                    # max(0, target − pred)
    bottleneck_n: int
    rework_n: int                 # 瓶颈车中 flag=1（质量返修）
    blacktop_n: int               # 瓶颈车中 flag=2（黑顶双色，正常工艺）
    sections: dict                # {段: n}


class AlertEngine:
    """缺口告警引擎（纯计算 + 去抖状态；无 DB 依赖 → 全合成单测）。
    evaluate 每步（5min）调用：活跃 shift scope + 中班时段 day_total scope（F10）；
    pred 自算 = a_fn + 池内 t+rem/α ≤ scope_end（by_vehicle 直推，F9 零新增查询；
    pred_fn 返回值的 A/pred 字段不采信，仅用 by_vehicle——错值不传染）；
    guard 非 FRESH → 空返回且去抖不推进（STALE 冻结，docs/02）；
    debouncer 按 scope-key 隔离（shift: 'YYYY-MM-DD_kind'；day_total: 'YYYY-MM-DD'）。
    自洽恒等式（F4）：池产品车 = 在制 + 瓶颈 + no-ETA（_split_pool 构造性精确；
    回测 G5 从 JSON 独立重算验证）。
    """

    def __init__(self, plan: dict, cfg: DSSConfig, section_map: dict,
                 guard=None, alpha: float = 1.0):
        """plan: {date: {'total': int, 'day': '11h'|None, 'mid': '11h'|None}}（出车计划 = 全天总台数）；
        section_map: {station: 段}（build_station_section 产物，缺站 → '待确认'）；
        alpha: 与 pred 通路一致的 α（生产默认 off = 1.0；on = tracker committed）。"""
        self.plan = plan
        self.cfg = cfg
        self.section_map = section_map
        self.guard = guard
        self.alpha = float(alpha)
        self._deb: dict = {}

    # 班次型 → 名义工时（plan CSV '日班工时' 列归一后为 '11h' 等；split_target 吃数值）
    _TYPE_HOURS = {"8h": 8.0, "10h": 10.0, "11h": 11.0}

    # -- 内部 --

    def _deb_for(self, key: str) -> Debouncer:
        if key not in self._deb:
            self._deb[key] = Debouncer(debounce_min=self.cfg.alert_debounce_min)
        return self._deb[key]

    def deb_state(self, now: datetime) -> dict:
        """纯读去抖器状态（docs/07 拍板⑥，T6-lite 快照留痕）：
        {scope_key: {current_level, pending_level, pending_min}}（pending_min =
        now − pending_since 分钟，1 位小数；无 pending → None）。

        **零语义变化**：不触碰 _deb 任何字段（单测钉死前后深拷贝相等）；
        不调 feed/不推进状态机。T6 v1 对 alert_engine.py 的唯一允许微动。
        """
        Debouncer._require_aware(now)
        out = {}
        for k, deb in self._deb.items():
            pm = None
            if deb.pending is not None and deb.pending_since is not None:
                pm = round((now - deb.pending_since).total_seconds() / 60, 1)
            out[k] = {"current_level": deb.current,
                      "pending_level": deb.pending,
                      "pending_min": pm}
        return out

    @staticmethod
    def _plan_entry(plan: dict, day) -> Optional[tuple]:
        """(total, h_day, h_mid)；缺排班日/休/total=0 → None（无 scope，口径 9/10）。"""
        e = plan.get(day)
        if not e:
            return None
        total = int(e.get("total") or 0)
        if total <= 0:
            return None
        return (total, e.get("day"), e.get("mid"))

    @staticmethod
    def _split_pool(by_vehicle: list, t: datetime, scope_end: datetime,
                    alpha: float) -> tuple:
        """(hit, bottleneck[...], no_eta)：rem 空 → no-ETA；t+rem/α ≤ end → 在制；其余瓶颈。"""
        hit, bottleneck, no_eta = 0, [], 0
        for v in by_vehicle:
            rem = v.get("rem_sec")
            if rem is None:
                no_eta += 1
            elif t + timedelta(seconds=rem / alpha) <= scope_end:
                hit += 1
            else:
                bottleneck.append(v)
        return hit, bottleneck, no_eta

    def _mk_alert(self, scope: str, sid: str, t: datetime, ev, delta: float,
                  target: int, pred: int, bottleneck: list) -> Alert:
        prev, level = ev
        rework = sum(1 for v in bottleneck if v["flag"] == FLAG_REWORK)
        blacktop = sum(1 for v in bottleneck if v["flag"] == FLAG_BLACKTOP)
        sections: dict = {}
        for v in bottleneck:
            sec = self.section_map.get(v["station"], "待确认")
            sections[sec] = sections.get(sec, 0) + 1
        return Alert(scope=scope, sid=sid, t=t, prev_level=prev, level=level,
                     delta=delta, gap_n=max(0, target - pred),
                     bottleneck_n=len(bottleneck), rework_n=rework,
                     blacktop_n=blacktop, sections=sections)

    # -- 入口 --

    def evaluate(self, t, shifts_today, day_shifts_done, pred_fn, a_fn) -> list:
        """每步评估（docs/05 §2.1 冻结签名）：
        t: 评估时刻（aware）；shifts_today: 当日班次 [day?, mid?]（Shift）；
        day_shifts_done: 已下班次（end ≤ t，Shift 列表——day_total 的 A_all 窗口源）；
        pred_fn(shift, t) -> M9 predict() 形状 dict（引擎只采 by_vehicle）；
        a_fn(shift, t) -> int（A(t)，F9 预取 bisect）。
        返回确认转换 Alert 列表（含基线；STALE/无 scope → []）。"""
        if t.tzinfo is None:
            raise TypeError("t 必须 aware（Asia/Shanghai），收到 naive")
        if self.guard is not None and self.guard.status() != "FRESH":
            return []                      # STALE 冻结：不出新告警、不推进去抖
        alerts = []
        # ---- shift scope（活跃班次）----
        for s in shifts_today:
            if not (s.attr_start <= t < s.end):
                continue
            entry = self._plan_entry(self.plan, s.day)
            if entry is None:
                continue                   # 休/停产/缺排班 → 无 scope
            total, h_day, h_mid = entry
            d_t, m_t = split_target(
                self._TYPE_HOURS.get(h_day), self._TYPE_HOURS.get(h_mid), total)
            target = d_t if s.kind == "day" else m_t
            if target <= 0:
                continue
            r = pred_fn(s, t)
            A = a_fn(s, t)
            hit, bottleneck, _no_eta = self._split_pool(r["by_vehicle"], t,
                                                        s.end, self.alpha)
            pred = A + hit
            delta = (pred - target) / target
            sid = f"{s.day.isoformat()}_{s.kind}"
            ev = self._deb_for(sid).feed(t, grade(delta, self.cfg))
            if ev is not None:
                alerts.append(self._mk_alert("shift", sid, t, ev, delta,
                                             target, pred, bottleneck))
        # ---- day_total scope（F10：仅中班时段 [16:30, 次日 04:00)）----
        mids = [s for s in shifts_today
                if s.kind == "mid" and s.attr_start <= t < s.end]
        if mids:
            ms = mids[0]
            entry = self._plan_entry(self.plan, ms.day)
            day_done = [s for s in day_shifts_done
                        if s.kind == "day" and s.day == ms.day]
            if entry is not None and day_done:
                total, _hd, _hm = entry
                src = day_done[0]          # A_all 窗 [04:45, t] ⊇ 中班窗 → union 单计
                A_all = a_fn(src, t)
                hit, bottleneck, _no_eta = self._split_pool(
                    pred_fn(src, t)["by_vehicle"], t,
                    max(s.end for s in shifts_today if s.day == ms.day),
                    self.alpha)
                pred = A_all + hit
                delta = (pred - total) / total
                ev = self._deb_for(ms.day.isoformat()).feed(
                    t, grade(delta, self.cfg))
                if ev is not None:
                    alerts.append(self._mk_alert("day_total", ms.day.isoformat(),
                                                 t, ev, delta, total, pred,
                                                 bottleneck))
        return alerts


# ---------------------------------------------------------------------------
# T4.2 两级段映射 bootstrap（docs/05 §2.1/§3 T4.2；docs/02 八段标准词汇）
# ---------------------------------------------------------------------------

SECTION_VOCAB = (
    "前处理", "电泳", "PVC预装密封", "打磨", "顶涂", "打蜡", "黑顶",
    "交付-针孔修补-审核",
)

# 一级：站号前 2 位 = 线体/工艺段码（2026-09-25 真库验证 scratch/tmp_w4_facts_prefix.py；
# 26 个有证据前缀覆盖 ~163/259 站；无证据前缀不在此表 → 走二级/待确认）
PREFIX_MAP = {
    "01": "前处理", "05": "前处理",
    "03": "电泳",
    "06": "PVC预装密封", "08": "PVC预装密封", "09": "PVC预装密封", "22": "PVC预装密封",
    "11": "打磨", "12": "打磨", "13": "打磨", "15": "打磨",
    "14": "顶涂", "16": "顶涂", "17": "顶涂", "19": "顶涂", "20": "顶涂", "21": "顶涂",
    "L3": "顶涂",
    "23": "交付-针孔修补-审核", "24": "交付-针孔修补-审核",
    "33": "打蜡", "34": "打蜡", "35": "打蜡", "K3": "打蜡",
    "41": "黑顶", "42": "黑顶",
}

# 二级关键词（顺序 = 优先级，先命中先赢：更特异的在前——前处理/电泳/注蜡/PVC/打磨/黑顶
# 先于「储存线/烘房」类泛化词，否则「4.5米打磨前储存线」会被误归顶涂）
_KEYWORDS = (
    ("前处理", "前处理"),
    ("预处理", "前处理"),   # 真库 area 名两种写法均有（05IS163 一线预处理储存线 / 01IS285 预处理二线）
    ("电泳", "电泳"),
    ("注蜡", "打蜡"),
    ("PVC", "PVC预装密封"),
    ("打磨", "打磨"),
    ("黑车顶", "黑顶"),
    ("黑顶", "黑顶"),
    ("面漆", "顶涂"), ("分色线", "顶涂"), ("色漆", "顶涂"), ("清漆", "顶涂"),
    ("烘房", "顶涂"), ("储存线", "顶涂"), ("色选", "顶涂"),
    ("报交", "交付-针孔修补-审核"), ("点修", "交付-针孔修补-审核"),
    ("修补", "交付-针孔修补-审核"), ("审核", "交付-针孔修补-审核"),
)


def kw_section(area) -> Optional[str]:
    """profile area 名 → 八段标准词汇（首命中赢；不命中 → None = 无证据）。"""
    if not area:
        return None
    for kw, sec in _KEYWORDS:
        if kw in area:
            return sec
    return None


_STATIONS_SQL = (
    'SELECT "RW_STATION_ID" FROM ods.carbody_history GROUP BY 1 ORDER BY 1'
    # 全史单列 hash GROUP BY（= 259 站全表；W1 学习器同类查询模式，非窗口函数）
)

_PROFILE_MODE_SQL = (
    'SELECT "carbody_last_rw_station", "current_process_area", count(*) '
    "FROM dim.dim_vehicle_profile "
    'WHERE "carbody_last_rw_station" IS NOT NULL '
    "AND \"current_process_area\" IS NOT NULL AND \"current_process_area\" != '' "
    "GROUP BY 1, 2 HAVING count(*) >= %s ORDER BY 1, 3 DESC"
)


def build_station_section(conn, min_n_profile: int = 3) -> list:
    """M10b 两级映射（docs/05 §2.1）：一级站号前缀码 + 二级 profile 众数修正
    （非 NULL 配对 n≥min_n_profile → kw_section 归段）；二级有证据优先（修正前缀），
    两级均无证据 → section='待确认'（用户补）。
    返回 259 站全表 [{station, prefix_section, area_mode, n, section,
    status:'auto'|'待确认'}]。用户确认后 upsert dss.dim_station_section（status='confirmed'）。"""
    cur = conn.cursor()
    cur.execute(_STATIONS_SQL)
    stations = [r[0] for r in cur.fetchall()]
    cur.execute(_PROFILE_MODE_SQL, (min_n_profile,))
    mode: dict = {}
    for st, area, n in cur.fetchall():
        if st not in mode:            # 已按 n DESC → 每站首行即众数
            mode[st] = (area, n)
    out = []
    for st in stations:
        prefix = PREFIX_MAP.get(st[:2])
        pm = mode.get(st)
        if pm is not None and pm[1] < min_n_profile:   # Python 侧兜底（真库 HAVING 已滤；假 cursor 不执行）
            pm = None
        area_sec = kw_section(pm[0]) if pm else None
        if area_sec:
            section, status = area_sec, "auto"
        elif prefix:
            section, status = prefix, "auto"
        else:
            section, status = "待确认", "待确认"
        out.append({
            "station": st,
            "prefix_section": prefix,
            "area_mode": pm[0] if pm else None,
            "n": pm[1] if pm else 0,
            "section": section,
            "status": status,
        })
    return out


# ---------------------------------------------------------------------------
# T4.1 纯函数/纯状态：split_target / grade / Debouncer
# ---------------------------------------------------------------------------

def split_target(h_day, h_mid, total) -> tuple:
    """M2 通用公式（docs/08 拍板）：日占比 = H日/(H日+H中)；largest-remainder 凑整
    （日班取 floor，中班拿余数，保证和 == total）。
    休/0/缺 → 对应侧工时 0（单侧休 → 对侧独占 total）；双侧无/total=0 → (0, 0)。
    """
    hd = float(h_day) if h_day else 0.0
    hm = float(h_mid) if h_mid else 0.0
    if total == 0 or (hd + hm) == 0:
        return (0, 0)
    r = hd / (hd + hm)
    d = math.floor(total * r)
    mid = math.floor(total * (1 - r))
    d += total - d - mid             # 余数（0 或 1）归日班
    return (d, mid)


def grade(delta: float, cfg: DSSConfig) -> str:
    """拍板 C：Δ ≤ cfg.alert_red → 'red'；alert_red < Δ ≤ alert_yellow → 'yellow'；其余 'green'（含边界）。"""
    if delta <= cfg.alert_red:
        return "red"
    if delta <= cfg.alert_yellow:
        return "yellow"
    return "green"


class Debouncer:
    """双向转换均需持续 debounce_min（物理时差判据，docs/05 口径 6）：
    feed(t, level) -> 确认后转换 (prev_level, level) or None。
    判据 (t − pending_since) ≥ timedelta(minutes=debounce_min)（含等号；aware 时间戳）；
    pending 期间闪回原档 → 清零；第三档 → 换档重起算；首档（初始态）首次 feed 即确认
    （基线留痕）；确认后同档不连发。状态按 scope-day 隔离（每 scope-day 新建实例）。"""

    def __init__(self, debounce_min: int = 10):
        self.debounce_min = debounce_min
        self.current: Optional[str] = None          # 已确认档（None = 未初始化）
        self.pending: Optional[str] = None
        self.pending_since: Optional[datetime] = None

    @staticmethod
    def _require_aware(t: datetime, what: str = "t") -> None:
        if t.tzinfo is None:
            raise TypeError(f"{what} 必须 aware（Asia/Shanghai），收到 naive")

    def feed(self, t: datetime, level: str):
        """返回 (prev_level, level) = 确认转换（含首档 prev=None），否则 None。"""
        self._require_aware(t)
        if self.current is None:
            self.current = level                    # 首档即确认（基线）
            return (None, level)
        if level == self.current:
            self.pending = None                     # 同档/闪回原档 → 无转换、清 pending
            self.pending_since = None
            return None
        if self.pending is None:
            self.pending, self.pending_since = level, t
            return None
        if level != self.pending:                   # 第三档 → 换档重起算
            self.pending, self.pending_since = level, t
            return None
        if (t - self.pending_since) >= timedelta(minutes=self.debounce_min):
            prev, self.current = self.current, level
            self.pending = None
            self.pending_since = None
            return (prev, level)
        return None


# ---------------------------------------------------------------------------
# T4.4 render（docs/05 §2.3）
# ---------------------------------------------------------------------------

def render_alert_line(a: "Alert", day_total_level: Optional[str] = None) -> str:
    """告警行渲染（含双轨对照 hint）：
    `09-12_mid Δ -11.7% red | 缺口 56 台 | 瓶颈 636 台（返修 109 / 黑顶 107 / 主线 420）:
    顶涂 210 · 打磨 180 · 前处理 90 …`
    主线 = bottleneck_n − rework_n − blacktop_n（构造性一致）；段按台数降序 Top-3。
    shift 红且全天主视图绿 → 尾注「拆分目标偏置」（F8 双轨对照）；day_total 红不注。"""
    name = (f"{a.t:%m-%d}_{a.sid.split('_')[-1]}" if a.scope == "shift"
            else f"{a.t:%m-%d}_全天")
    main = a.bottleneck_n - a.rework_n - a.blacktop_n
    secs = sorted(a.sections.items(), key=lambda kv: (-kv[1], kv[0]))
    top = " · ".join(f"{k} {v}" for k, v in secs[:3])
    more = " …" if len(secs) > 3 else ""
    line = (f"{name} Δ {a.delta * 100:+.1f}% {a.level} | 缺口 {a.gap_n} 台 | "
            f"瓶颈 {a.bottleneck_n} 台（返修 {a.rework_n} / 黑顶 {a.blacktop_n} / "
            f"主线 {main}）")
    if secs:
        line += f": {top}{more}"
    if a.scope == "shift" and a.level == "red" and day_total_level == "green":
        line += " [拆分目标偏置（全天主视图绿，见 F8）]"
    return line
