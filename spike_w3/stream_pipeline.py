# -*- coding: utf-8 -*-
"""T2 WIP 池 + 增量管道（状态机单一实现）。

池不变式（类级承诺，DoD）：
- `snapshot()` 内每车必有 `last_event_ts`；
- 出站车必已移除（当前周期首次出站 = 池移除；不在池的出站事件只审计、不动池）；
- `audit` 字典四键：exits / same_station_dups / invalid_flags / late_out_of_window
  （前三键 = 逐车计数 dict，第四键 = 全局 int；审计计数不动池状态）；
- `last_event_by_station()` 每站 = 该站最近过车事件 ts（站内级留存：每个 ingest 事件
  含仅审计的出站事件都更新，max 语义幂等；车驶离/池空后保留，B7 停线感知依赖）；
- 所有 datetime 一律 aware（Asia/Shanghai），naive ts → TypeError（R7）。

状态机单一实现（B4 七点）：在线消费与 bootstrap 回放喂同一个 `ingest`，不建第二套逻辑。
规则 R1–R7 见 docs/03 §2 规则表（R1 同站幂等 / R2 flag 继承 / R3 多次进出 /
R4 中途收养排出站 / R5 BODY_TYPE 直取 / R6 loop 计数 / R7 tz 强制）。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

try:  # 包内导入（测试 / 01_boot_online 走包）
    from .common import ENTRY_STATIONS, EXIT_STATIONS, POST_EXIT_STATIONS
except ImportError:  # 脚本直跑（python spike_w3\01_boot_online.py）
    from common import ENTRY_STATIONS, EXIT_STATIONS, POST_EXIT_STATIONS

_VALID_FLAGS = ("0", "1", "2")


@dataclass
class Event:
    """过站事件（DB 行映射；body_type = 列 "BODY_TYPE" 直取，R5 禁前缀推导）。"""
    body_id: str
    body_type: str
    station: str
    flag: str
    ts: datetime
    row_id: int


@dataclass
class VehicleState:
    """在制车状态（§0 契约字段；内部辅助结构留在 VehiclePool 侧）。"""
    body_type: str
    station: str
    flag: str
    last_event_ts: datetime
    entry_ts: datetime
    exits_seen: int
    loop_count: int


@dataclass
class DeadCar:
    """死车（M7：自最近事件无新事件 ≥ dead_vehicle_hours）。"""
    body_id: str
    station: str
    flag: str
    since: datetime
    hours: float


class EventStream:
    """增量消费（单查询）：ID 水位 + `"DATE_EVT" <= now` 上界 + skew 两档分流。

    上界纪律（一轮审核 B1）：replay_cursor 模式 ts > now 的事件本轮不拉（水位推进不得越过游标，
    游标走到后下轮自然拉到，无需「未来事件」分支）；wall_clock 模式数据恒在过去，条件恒真零副作用。
    缺上界 = replay 首轮拉穿全表（633 万行）耗尽水位。
    """

    def __init__(self, conn, wm: int, skew_window_min: int = 15):
        self._conn = conn
        self._wm = wm
        self.skew_window_min = skew_window_min
        self.front: datetime | None = None  # 已处理 max ts（主循环设 = BootstrapReport.data_max_ts）

    @property
    def watermark(self) -> int:
        return self._wm

    def set_watermark(self, wm: int) -> None:
        """水位对齐（T2.6 周期重建后调用）：只升不降（F8：增量流起点不倒退）。"""
        if wm > self._wm:
            self._wm = wm

    def consume(self, now: datetime, pool: VehiclePool) -> list[Event]:
        """拉取 + 喂 pool；返回本轮顺序（ts >= front）已应用的事件。

        轮内分发序 = (ts, ID) 升序（T2.6 真库回放踩坑修，2026-09-25）：ID 序分发时，
        轮内 ID/ts 逆序行（真实数据 ~52%）在 front 推进后被误判「窗内迟到」→
        按车日志子查询（真库 7 天 2016 轮回放 ≈ 20 万次子查询 → 11h 不可行）；
        ts 序分发使 front 单调推进，轮内刚够格行（ts ∈ (now−step, now]）全部顺序
        ingest——与 T2.4 bootstrap「(BODY_ID, ts, ID) 升序纯 ingest」同教训。
        skew 三档分流保留为真迟到（ts < front − 窗，ETL 晚到行）兑底，不受影响。
        """
        _require_aware(now)  # R7 同纪律
        cur = self._conn.cursor()
        cur.execute(_CONSUME_SQL, (self._wm, now))
        rows = cur.fetchall()
        out: list[Event] = []
        for row in sorted(rows, key=lambda r: (r[5], r[0])):  # (ts, ID) 升序
            self._dispatch(_row_to_event(row), pool, now, out)
        return out

    def _dispatch(self, ev: Event, pool: VehiclePool, now: datetime, out: list[Event]) -> None:
        """单事件分流（**仅供增量消费 consume 内部使用**；bootstrap 回放走
        (BODY_ID, ts, ID) 升序纯 pool.ingest、不经过本路径——完整回放窗无「迟到」概念，
        2026-09-24 agy 审核低①同步）：
        ID 水位推进 + skew 三路（顺序 ingest / 窗内迟到按车重算 / 更旧审计）。状态机
        本体仍是单一 ingest（B4「同一个 ingest」不建第二套逻辑）。"""
        if ev.row_id > self._wm:  # 水位推进 = max(row_id)
            self._wm = ev.row_id
        if self.front is None or ev.ts >= self.front:
            pool.ingest(ev)  # 顺序
            self.front = ev.ts if self.front is None else max(self.front, ev.ts)
            out.append(ev)
        elif ev.ts >= self.front - timedelta(minutes=self.skew_window_min):
            # 迟到但窗内 → 按车日志重算（合并规则见 docs/03 §3）
            self._replay_late(pool, ev, now)
        else:
            # 更旧于窗 → 仅审计不动池（每日全量核对兑底）
            pool.audit["late_out_of_window"] += 1

    def _replay_late(self, pool: VehiclePool, ev: Event, now: datetime) -> None:
        """迟到事件 → 按车查事件日志（在池 since=entry_ts 全周期；不在池 since=now−24h）→ replay_car。"""
        st = pool.snapshot().get(ev.body_id)
        since = st.entry_ts if st is not None else now - timedelta(hours=24)
        cur = self._conn.cursor()
        cur.execute(_CAR_LOG_SQL, (ev.body_id, since, now))
        rows = cur.fetchall()
        events = [_row_to_event(r) for r in rows]
        if events:
            pool.replay_car(ev.body_id, events)


_CONSUME_SQL = (
    'SELECT "ID", "BODY_ID", "BODY_TYPE", "RW_STATION_ID", '
    'SUBSTRING("MDS_DATA" FROM 139 FOR 1) AS flag, "DATE_EVT" '
    'FROM ods.carbody_history '
    'WHERE "ID" > %s AND "DATE_EVT" <= %s '
    'ORDER BY "ID"'
)

_CAR_LOG_SQL = (
    'SELECT "ID", "BODY_ID", "BODY_TYPE", "RW_STATION_ID", '
    'SUBSTRING("MDS_DATA" FROM 139 FOR 1) AS flag, "DATE_EVT" '
    'FROM ods.carbody_history '
    'WHERE "BODY_ID" = %s AND "DATE_EVT" BETWEEN %s AND %s '
    'ORDER BY "DATE_EVT","ID"'
)


def _row_to_event(row) -> Event:
    """DB 行（ID, BODY_ID, BODY_TYPE, RW_STATION_ID, flag, DATE_EVT）→ Event（R5：body_type 列直取）。"""
    return Event(body_id=row[1], body_type=row[2], station=row[3], flag=row[4], ts=row[5], row_id=row[0])


@dataclass
class ReconcileReport:
    """每日全量核对结果（差异 > 0 只报告不自动修——人工判断，审计留痕）。"""
    pool_size: int
    only_in_live: int
    only_in_fresh: int
    state_mismatches: int


_RECONCILE_SQL = (
    'SELECT "ID", "BODY_ID", "BODY_TYPE", "RW_STATION_ID", '
    'SUBSTRING("MDS_DATA" FROM 139 FOR 1) AS flag, "DATE_EVT" '
    'FROM ods.carbody_history '
    'WHERE "BODY_ID" LIKE \'78202%\' AND "DATE_EVT" >= %s '
    'ORDER BY "DATE_EVT","ID"'
)


def reconcile(conn, now: datetime, pool: VehiclePool, days: int = 7) -> ReconcileReport:
    """每日全量核对：拉近 `days` 天产品车全事件、按 ts 序喂干净池 → 与运行池比
    （车集合差 + 逐车 station/flag 差）。差异 > 0 只报告不自动修（人工判断，审计留痕）。
    """
    _require_aware(now)  # R7 同纪律
    since = now - timedelta(days=days)
    cur = conn.cursor()
    cur.execute(_RECONCILE_SQL, (since,))
    fresh = VehiclePool()
    for row in cur.fetchall():
        fresh.ingest(_row_to_event(row))
    live_s, fresh_s = pool.snapshot(), fresh.snapshot()
    only_live = sum(1 for b in live_s if b not in fresh_s)
    only_fresh = sum(1 for b in fresh_s if b not in live_s)
    mismatch = sum(
        1 for b in live_s
        if b in fresh_s and (live_s[b].station != fresh_s[b].station or live_s[b].flag != fresh_s[b].flag)
    )
    return ReconcileReport(
        pool_size=len(live_s), only_in_live=only_live,
        only_in_fresh=only_fresh, state_mismatches=mismatch,
    )


def _require_aware(ts: datetime) -> None:
    """R7：naive datetime 直接 TypeError（naive/aware 混比运行期炸，机器强制）。"""
    if ts.tzinfo is None or ts.tzinfo.utcoffset(ts) is None:
        raise TypeError(f"naive datetime 禁止入池（须 aware，Asia/Shanghai）: {ts!r}")


_POSTEXIT_MIN_SAMPLES = 30  # 重推小样本排除（事件数 < 30 的站不作出站延续站判定）

_POSTEXIT_SQL = """
    WITH ex AS (
        SELECT "BODY_ID" b, min("DATE_EVT") m
        FROM ods.carbody_history
        WHERE "RW_STATION_ID" IN (%s,%s,%s,%s) AND "BODY_ID" LIKE '78202%%'
        GROUP BY 1
    ),
    c AS (
        SELECT s."RW_STATION_ID" st,
               count(*) total,
               count(ex.b) FILTER (WHERE ex.m < s."DATE_EVT") with_prior
        FROM ods.carbody_history s
        LEFT JOIN ex ON ex.b = s."BODY_ID"
        WHERE s."BODY_ID" LIKE '78202%%'
        GROUP BY 1
    )
    SELECT st, total, with_prior FROM c WHERE total >= %s
"""


def derive_postexit_stations(conn, min_rate: float = 0.95) -> frozenset[str]:
    """出站延续站集合数据驱动重推（2026-09-24 启动闸门）。

    定义：某站产品车事件中，同车存在前置出站站事件（4 出站站 min DATE_EVT 之前）占比 ≥ min_rate
    且事件数 ≥ 30 → 出站延续站。用法：01_boot_online 启动时与 POST_EXIT_STATIONS 常量比对；
    不一致只进启动报告（不自动改常量——人工拍板扩清单，记 BM 线程笔记）。
    """
    cur = conn.cursor()
    cur.execute(_POSTEXIT_SQL, (*EXIT_STATIONS, _POSTEXIT_MIN_SAMPLES))
    return frozenset(
        st for st, total, with_prior in cur.fetchall()
        # 小样本过滤 Python 侧双重强制（SQL 侧 WHERE 只是性能优化，FakeCursor 测试下不执行）
        if total >= _POSTEXIT_MIN_SAMPLES and with_prior / total >= min_rate
    )


# ---------- T2.5 死车审计（dss 层可选表；只写 dss schema，ods/dim/fct/mart/meta 只读） ----------

_WORK_ALERT_LOG_DDL = (
    "CREATE TABLE IF NOT EXISTS dss.work_alert_log ("
    " body_id TEXT NOT NULL, "
    " type TEXT NOT NULL, "
    " station TEXT, "
    " flag TEXT, "
    " since TIMESTAMPTZ, "
    " detected_at TIMESTAMPTZ NOT NULL"
    ")"
)

_DEAD_AUDIT_SQL = (
    "INSERT INTO dss.work_alert_log (body_id, type, station, flag, since, detected_at) "
    "VALUES (%s, 'dead_car', %s, %s, %s, %s)"
)


def ensure_work_alert_log(conn) -> None:
    """dss.work_alert_log DDL（幂等；主循环首次死车审计前 / 启动序列可显式调用）。"""
    cur = conn.cursor()
    cur.execute(_WORK_ALERT_LOG_DDL)


def record_dead_audit(conn, dead: list[DeadCar], now: datetime) -> int:
    """死车审计持久化（T2.5，编排层——dead_sweep 冻结签名无 conn）。

    主循环：`dead = pool.dead_sweep(now, frozen)` 后 `if dead: record_dead_audit(conn, dead, now)`；
    DDL 幂等（CREATE TABLE IF NOT EXISTS，随首次死车审计懒建）；
    type 字面量 'dead_car'（同表将来容纳其他预警类型）；
    since = 最近事件 ts，detected_at = 判定时刻 now。返回写入行数（空列表 = 0，不建表不写行）。
    """
    _require_aware(now)
    if not dead:
        return 0
    ensure_work_alert_log(conn)
    cur = conn.cursor()
    for d in dead:
        cur.execute(_DEAD_AUDIT_SQL, (d.body_id, d.station, d.flag, d.since, now))
    return len(dead)


class VehiclePool:
    """在制池：dict[BODY_ID -> VehicleState]（~1000 车，内存态，无本地状态文件）。

    池不变式见模块 docstring。
    """

    def __init__(self, dead_vehicle_hours: float = 120.0):
        self._vehicles: dict[str, VehicleState] = {}
        self._visited: dict[str, set[str]] = {}   # 每车当前周期已访问站集合（R6 辅助）
        self._station_last_event_ts: dict[str, datetime] = {}  # 站内级最近过车 ts（B7 停线依赖，max 语义）
        self._seen_row_ids: dict[str, set[int]] = {}  # 每车近期 row_id（replay_car 去重，小集合上限 256）
        self.dead_vehicle_hours = dead_vehicle_hours  # M7 死车阈值（主循环传 cfg.dead_vehicle_hours；测试压缩验证边界，B4 点 7）
        self.audit: dict = {
            "exits": {},                # body_id -> 累计出站数（≥2 = 多余出站）
            "same_station_dups": {},    # body_id -> 连续同站重复计数
            "invalid_flags": {},        # body_id -> 无效 flag（'-'/'3'）事件计数
            "late_out_of_window": 0,    # 窗外更旧事件计数（T2.2 skew 分流）
        }

    def reset(self) -> None:
        """全量重建归零（T2.6 周期重建，F-1 丢行自愈）：清全部池状态——
        状态纯由事件流重建（B4 点③，无本地状态）；阈值参数保留（重建不换实例）。"""
        self._vehicles.clear()
        self._visited.clear()
        self._station_last_event_ts.clear()
        self._seen_row_ids.clear()
        self.audit = {
            "exits": {},
            "same_station_dups": {},
            "invalid_flags": {},
            "late_out_of_window": 0,
        }

    def ingest(self, ev: Event) -> None:
        """状态机主入口（含中途收养）——R1–R7 全部规则（_ingest_core）；
        成功后记录该车近期 row_id（replay_car 去重，小集合上限 256，2026-09-24 补）。"""
        self._ingest_core(ev)
        self._record_seen(ev.body_id, ev.row_id)

    def _record_seen(self, body_id: str, row_id: int) -> None:
        seen = self._seen_row_ids.setdefault(body_id, set())
        seen.add(row_id)
        if len(seen) > 256:  # 有界收缩：超限只留最新（窗内事件数远小于 256，仅服务审计去重）
            self._seen_row_ids[body_id] = {row_id}

    def _ingest_core(self, ev: Event) -> None:
        """状态机规则体（T2.1 定版，含审核修订）：

        R7 tz 强制；不在池：入口/非出站站事件入池（入口进站 / 中途收养 R4），
        出站站事件仅审计（F6）；在池：连续同站幂等（R1）/ 过入站站 = 新周期（R3 泛化）/
        当前周期首次出站 = 池移除（R3）/ 非连续重过同站 loop+1（R6）；flag 无效继承（R2）。
        """
        _require_aware(ev.ts)  # R7
        # 站内级最近过车 ts：单点更新，覆盖全部分支（含仅审计出站事件；max 语义幂等，车驶离后保留）
        prev = self._station_last_event_ts.get(ev.station)
        if prev is None or ev.ts > prev:
            self._station_last_event_ts[ev.station] = ev.ts
        body = ev.body_id
        st = self._vehicles.get(body)
        if st is None:
            # 不在池：出站站事件仅审计、不收养（R4 排出站，F6）
            if ev.station in EXIT_STATIONS:
                self.audit["exits"][body] = self.audit["exits"].get(body, 0) + 1
                return
            # R4 双保险②（2026-09-24 幽灵车）：出站延续站 → 仅审计不收养（静默：一次物理出线
            # 会过多个延续站，不计 exits，防每车 exit 计数虚增）
            if ev.station in POST_EXIT_STATIONS:
                return
            # R4 双保险①：动态历史锁——已出站车（生命周期 exits>0）+ 非入口站 → 禁止收养
            # （返线头必经入口站，入口站豁免；兜底未知/未来新增延续站）
            if ev.station not in ENTRY_STATIONS and self.audit["exits"].get(body, 0) > 0:
                return
            # 入池（入口进站 / 中途收养 R4）：entry_ts = 该事件 ts（收养近似，docs/03 §2 注）
            flag = ev.flag if ev.flag in _VALID_FLAGS else "0"
            if flag != ev.flag:
                self.audit["invalid_flags"][body] = self.audit["invalid_flags"].get(body, 0) + 1
            self._vehicles[body] = VehicleState(
                body_type=ev.body_type or "", station=ev.station, flag=flag,  # R5：DB 列 NULL 兜底空串
                last_event_ts=ev.ts, entry_ts=ev.ts,
                exits_seen=self.audit["exits"].get(body, 0), loop_count=0,
            )
            self._visited[body] = {ev.station}
            return
        # 在池：R2 flag 解算（无效 → 继承最近有效值，不得复位 0）
        if ev.flag in _VALID_FLAGS:
            new_flag = ev.flag
        else:
            new_flag = st.flag
            self.audit["invalid_flags"][body] = self.audit["invalid_flags"].get(body, 0) + 1
        if ev.station in EXIT_STATIONS:
            # 当前周期首次出站 = 池移除（R3）；多余出站（已不在池）走上审计分支
            del self._vehicles[body]
            self._visited.pop(body, None)
            self.audit["exits"][body] = self.audit["exits"].get(body, 0) + 1
            return
        st.flag = new_flag
        st.last_event_ts = ev.ts
        if ev.body_type:  # R5：直取透传（空值保持原值）
            st.body_type = ev.body_type
        if ev.station in POST_EXIT_STATIONS:
            # R4 双保险②：在池车过出站延续站 → 池移除（防穿透 + 漏扫出站站清理；静默不计 exits）
            del self._vehicles[body]
            self._visited.pop(body, None)
            return
        if ev.station == st.station:
            # R1：连续同站 = 幂等更新（禁时间窗丢弃，真相邻快过保留）
            self.audit["same_station_dups"][body] = self.audit["same_station_dups"].get(body, 0) + 1
            return
        if ev.station in ENTRY_STATIONS:
            # R3 泛化：在池车再过入站站（返工回线头）= 新周期
            st.station = ev.station
            st.entry_ts = ev.ts
            st.loop_count = 0
            self._visited[body] = {ev.station}
            return
        # 过站：R6 非连续重过同站 +1（连续段不算；在池车 _visited 必已初始化，直接索引）
        if ev.station in self._visited[body]:
            st.loop_count += 1
        self._visited[body].add(ev.station)
        st.station = ev.station

    def snapshot(self) -> dict[str, VehicleState]:
        """在制车只读视图（浅拷贝 dict；值对象共享）。"""
        return dict(self._vehicles)

    def last_event_by_station(self) -> dict[str, datetime]:
        """B7 依赖：每站最近过车事件 ts（站内级留存，见模块不变式；从未有事件 = 空 dict）。"""
        return dict(self._station_last_event_ts)

    def dead_sweep(self, now: datetime, frozen: bool = False) -> list[DeadCar]:
        """M7 死车清扫（T2.5；主循环恒调用；frozen=True（B3 STALE）= 空操作不挂起判定）。

        判定基准 = 自**最近事件**无新事件 ≥ dead_vehicle_hours（不是总行程）；
        移出活跃池 → DeadCar 报表（T3 只读活跃池 → 天然不计班次预测）；
        新事件自动复活 = R4 收养（天然成立，test_dead_revive 钉死）；
        审计持久化在编排层 record_dead_audit（冻结签名无 conn，DB 不混入纯状态机）。
        清理口径与出站站/延续站移除路径一致：只清 _vehicles + _visited（不变式成对）；
        **_seen_row_ids 保留**（生命周期去重集——pop 会使后续 skew 迟到重算对旧 row_id 的
        dup/invalid 审计双计，test_dead_sweep_replay_no_audit_double_count 钉死；
        2026-09-25 自查发现的 T2.5 首轮实现偏差）；
        audit 生命周期计数不清理（复活后动态锁仍有效）；站级 ts 留存不清理（max 语义）。
        """
        _require_aware(now)  # R7 同纪律
        if frozen:
            return []
        threshold = timedelta(hours=self.dead_vehicle_hours)
        dead: list[DeadCar] = []
        for body_id, st in list(self._vehicles.items()):
            if now - st.last_event_ts >= threshold:
                dead.append(DeadCar(
                    body_id=body_id, station=st.station, flag=st.flag,
                    since=st.last_event_ts,
                    hours=(now - st.last_event_ts).total_seconds() / 3600.0,
                ))
                del self._vehicles[body_id]
                self._visited.pop(body_id, None)
        return dead

    def replay_car(self, body_id: str, events: list[Event]) -> None:
        """T2.2 迟到重放（按车日志切片；合并规则 = docs/03 §3 定版，F7 修复）。

        重算 = 同一套 R1/R2/R6 状态机重放；合并：`station / flag / loop_count / last_event_ts`
        = 重算结果；**entry_ts / exits_seen 不动**（生命周期计数只来自顺序事件）；
        最新事件在出站站或出站延续站 → 在池车移除 / 不在池不收养（仅审计，R4 双保险）；
        不在池 + 最新非出站非延续站（且过动态历史锁）→ R4 收养（entry_ts = 日志首事件 ts，近似）；
        row_id 去重（审计计数不重复，幂等）；站级 ts 并入（max 语义幂等）。
        """
        if not events:
            return
        _require_aware(events[-1].ts)  # R7 同纪律
        seen = self._seen_row_ids.setdefault(body_id, set())
        # —— 重算（R1/R2/R6 规则走查，新鲜状态起步） ——
        pos: str | None = None        # 当前在制站；None = 在切片内已出站
        visited: set[str] = set()
        flag = "0"
        loop = 0
        dups_new = 0
        invalid_new = 0
        last_ts: datetime | None = None
        for ev in events:
            if ev.station in EXIT_STATIONS or ev.station in POST_EXIT_STATIONS:
                pos = None            # 出站/出站延续（切片内再入 = 新周期）
                visited = set()
            else:
                if pos is None:
                    # 切片内入池/再入（R4 语义，含动态历史锁：已出站车 + 非入口站 → 不收养）
                    if ev.station in ENTRY_STATIONS or self.audit["exits"].get(body_id, 0) == 0:
                        pos, visited, loop = ev.station, {ev.station}, 0
                elif pos == ev.station:
                    if ev.row_id not in seen:
                        dups_new += 1  # R1 连续同站（仅新 row_id 计数）
                else:
                    if ev.station in visited:
                        loop += 1      # R6 非连续重过同站
                    visited.add(ev.station)
                    pos = ev.station
                    if ev.station in ENTRY_STATIONS:
                        loop, visited = 0, {ev.station}  # R3 泛化：过入站站 = 新周期
                if ev.flag in _VALID_FLAGS:
                    flag = ev.flag     # R2
                elif ev.row_id not in seen:
                    invalid_new += 1   # 无效值（仅新 row_id 计数）
            last_ts = ev.ts
        # —— 合并 ——
        last = events[-1]
        st = self._vehicles.get(body_id)
        if last.station in EXIT_STATIONS or last.station in POST_EXIT_STATIONS:
            # 最新在出站/出站延续站：不在池 → 不收养仅审计；在池 → 池移除（不变式），exits 计数不动（顺序事件归口）
            if st is not None:
                del self._vehicles[body_id]
                self._visited.pop(body_id, None)
        elif st is None:
            if pos is not None:
                # 不在池 + 最新非出站非延续站（过锁）→ R4 收养（entry_ts = 日志首事件 ts，近似）
                self._vehicles[body_id] = VehicleState(
                    body_type=events[0].body_type or "", station=pos, flag=flag,
                    last_event_ts=last_ts, entry_ts=events[0].ts,
                    exits_seen=self.audit["exits"].get(body_id, 0), loop_count=loop,
                )
                self._visited[body_id] = visited  # 收养必初始化（在池 ⇒ _visited 存在不变式，2026-09-24 干跑踩坑修）
            # pos None = 动态锁拒绝切片内收养 → 不收养仅审计
        else:
            if pos is None:
                # 防御：重算结果无在制位置（正常在池车切片必从入口/已收养站起步）→ 移除
                del self._vehicles[body_id]
                self._visited.pop(body_id, None)
            else:
                st.station, st.flag, st.loop_count, st.last_event_ts = pos, flag, loop, last_ts
                self._visited[body_id] = visited
        if dups_new:
            self.audit["same_station_dups"][body_id] = self.audit["same_station_dups"].get(body_id, 0) + dups_new
        if invalid_new:
            self.audit["invalid_flags"][body_id] = self.audit["invalid_flags"].get(body_id, 0) + invalid_new
        # 站级 ts 并入（切片涉及的各站取 max，max 语义幂等）
        for ev in events:
            cur_ts = self._station_last_event_ts.get(ev.station)
            if cur_ts is None or ev.ts > cur_ts:
                self._station_last_event_ts[ev.station] = ev.ts
        # 标记切片全部 row_id 为已见（去重：同一切片重算不重复计数）
        if len(seen) > 256:  # 有界收缩（同 _record_seen 口径）
            seen.clear()
        seen.update(ev.row_id for ev in events)


# ---------------------------------------------------------------------------
# B3 新鲜度护栏（T2.3）
# ---------------------------------------------------------------------------

class FreshnessGuard:
    """B3 数据新鲜度护栏：三态机 + 30min latch + 四信号诊断 + 休息日降级。

    **STALE 冻结语义（B3 定稿，DoD 注释项）：STALE 时冻结对象 = 告警 + 死车 sweep；
    不冻结池消费 / 预测计算**（consume/ETA 照常推进，避免停线恢复后数据回灌失序；
    STALE 同时驱动启动横幅与「暂停死车 sweep 防整池集体判死」）。

    三态 + latch：NORMAL（lag < stale_min）→ STALE（lag ≥ stale_min，冻结）→
    RECOVERY（过渡态，不冻结）→ 再 fresh 一次 → NORMAL；RECOVERY 中再陈旧 → 回 STALE。
    恢复需连续两次 fresh check（滤瞬时抖动），转换全部记 `log`（时间, "旧->新"）。
    expected_silence（休息日，P2 条款）：STALE 状态与冻结不变，label 降级
    「无数据属预期」且 is_anomaly=False（不计异常/不计验收，告警仍冻结）。
    """

    _SYNC_JOB = "refresh_carbody_ods"    # S1：写 ods.carbody_history 的 ETL 作业名
    _WM_KEY = "ods.carbody_history.max_id"  # S2：ETL 层水位行（仅诊断，不作 DSS 起始水位，F8）

    def __init__(self, cfg: DSSConfig):
        self.cfg = cfg
        self.status = "NORMAL"
        self.label = "数据新鲜"
        self.is_anomaly = False
        self.lag_minutes: float = 0.0
        self.log: list[tuple[datetime, str]] = []  # (转换时刻, "旧态->新态")

    # -- check：纯计算（注入 now / max_event_ts，可离线测） -----------------

    def check(self, now: datetime, max_event_ts: datetime | None,
              expected_silence: bool = False) -> bool:
        """推进三态机；返回 frozen（仅 STALE 为 True）。

        冻结对象 = 告警 + 死车 sweep，不冻结池消费/计算（B3 定稿）。
        max_event_ts=None（尚无事件）→ lag 视为无穷 → STALE（保守冻结，防无数据误告警）。
        """
        _require_aware(now)  # R7 同纪律
        if max_event_ts is not None:
            _require_aware(max_event_ts)
            lag = (now - max_event_ts).total_seconds() / 60.0
        else:
            lag = float("inf")
        self.lag_minutes = lag
        fresh = lag < self.cfg.stale_min
        old = self.status
        if self.status == "STALE":
            new = "RECOVERY" if fresh else "STALE"
        elif self.status == "RECOVERY":
            new = "NORMAL" if fresh else "STALE"
        else:  # NORMAL
            new = "STALE" if not fresh else "NORMAL"
        if new != old:
            self.log.append((now, f"{old}->{new}"))
        self.status = new
        self.is_anomaly = (new == "STALE") and not expected_silence
        if new == "NORMAL":
            self.label = "数据新鲜"
        elif new == "RECOVERY":
            self.label = "恢复中"
        else:  # STALE
            self.label = "无数据属预期" if expected_silence else "数据陈旧"
        return new == "STALE"

    # -- diagnose：四信号诊断（连库只读；断流判定口径 BM 2026-09-22） -------

    def diagnose(self, conn, now: datetime) -> dict:
        """四信号：S1 sync_job_log 签到 / S2 ETL 水位 vs 全表 max_id /
        S3 max DATE_EVT / S4 rb_position_data 独立快照。

        判定：事件停 + ETL 停 = 「断流」（B3 STALE 冻结）；事件停 + ETL 正常 =
        「移交模式A」（真停线非链路问题，由 B7 站级白名单判定）。
        etl_healthy = S1 成功且签到新鲜（< lag_min） ∧ S2 水位缺口 = 0 ∧
        S4 独立快照新鲜（< lag_min，5min 快照 ×3 周期未更新即判 ETL 侧死）。
        事件新鲜（S3 lag < stale_min）→ 「新鲜」，ETL 信号仅作诊断不推翻事件层事实。
        """
        _require_aware(now)  # R7 同纪律
        cur = conn.cursor()
        cur.execute(
            'SELECT started_at, status FROM meta.sync_job_log '
            'WHERE job_name = %s ORDER BY id DESC LIMIT 1', (self._SYNC_JOB,))
        s1 = cur.fetchall()
        s1_ts, s1_status = (s1[0][0], s1[0][1]) if s1 else (None, None)
        cur.execute(
            'SELECT watermark_value, updated_at FROM meta.refresh_watermark '
            'WHERE source_name = %s', (self._WM_KEY,))
        s2a = cur.fetchall()
        wm_raw = s2a[0][0] if s2a else None
        s2_wm_id = int(wm_raw) if wm_raw not in (None, "") else None
        cur.execute('SELECT MAX("ID") FROM ods.carbody_history')
        s2b = cur.fetchall()
        table_max_id = s2b[0][0] if s2b and s2b[0][0] is not None else None
        cur.execute('SELECT MAX("DATE_EVT") FROM ods.carbody_history')
        s3 = cur.fetchall()
        s3_ts = s3[0][0] if s3 else None
        cur.execute('SELECT MAX(vehicle_updated_at) FROM ods.rb_position_data')
        s4 = cur.fetchall()
        s4_ts = s4[0][0] if s4 else None

        def _lag(ts):
            if ts is None:
                return None
            _require_aware(ts)  # DB 恒 aware；naive 即数据源异常，直接炸
            return (now - ts).total_seconds() / 60.0

        s1_lag, s3_lag, s4_lag = _lag(s1_ts), _lag(s3_ts), _lag(s4_ts)
        gap = int(table_max_id - s2_wm_id) if (s2_wm_id is not None and table_max_id is not None) else None
        s1_ok = s1_status == "success" and s1_lag is not None and s1_lag < self.cfg.lag_min
        s2_ok = gap == 0
        s4_ok = s4_lag is not None and s4_lag < self.cfg.lag_min
        event_fresh = s3_lag is not None and s3_lag < self.cfg.stale_min
        etl_healthy = s1_ok and s2_ok and s4_ok
        if event_fresh:
            verdict = "新鲜"
        elif not etl_healthy:
            verdict = "断流"
        else:
            verdict = "移交模式A"
        return {
            "now": now,
            "s1_sync_job": {"started_at": s1_ts, "status": s1_status, "lag_min": s1_lag},
            "s2_watermark": {"etl_max_id": s2_wm_id, "table_max_id": table_max_id, "gap": gap},
            "s3_max_event_ts": s3_ts, "s3_lag_min": s3_lag,
            "s4_rb_max_ts": s4_ts, "s4_lag_min": s4_lag,
            "event_fresh": event_fresh,
            "etl_healthy": etl_healthy,
            "verdict": verdict,
        }


# ---------------------------------------------------------------------------
# B4 bootstrap（T2.4）：120h 回放 + 交叉核对
# ---------------------------------------------------------------------------

_BOOTSTRAP_SQL = (
    'SELECT "ID", "BODY_ID", "BODY_TYPE", "RW_STATION_ID", '
    'SUBSTRING("MDS_DATA" FROM 139 FOR 1) AS flag, "DATE_EVT" '
    'FROM ods.carbody_history '
    'WHERE "DATE_EVT" >= %s AND "DATE_EVT" <= %s '
    'ORDER BY "ID"'
)

_CROSS_CHECK_SQL = (
    'SELECT vehicle_id FROM dim.dim_vehicle_profile '
    'WHERE carbody_last_seen_at >= %s AND carbody_last_seen_at <= %s '
    'AND carbody_last_rw_station NOT IN '
    '(%s,%s,%s,%s,%s,%s,%s,%s,%s)'
)


@dataclass
class BootstrapReport:
    """B4 启动回放报告（启动报告打印全部生效配置值）。

    watermark = 回放窗内最大行 ID = 增量流起点（F8：防 bootstrap 后二次重拉 120h；
    0 = 窗内无数据，启动序列应中止，不得从 0 起增量流）。
    data_max_ts = 回放已处理 max ts（主循环 EventStream.front 初值）。
    """

    pool_size: int
    flag_dist: dict            # flag → 车数（0/1/2/-/3）
    seg_dist: dict             # 站前缀粗分（前 2 字符线/区码）→ 车数
    data_max_ts: datetime | None
    watermark: int
    config_echo: dict          # 全部生效值（dataclasses.asdict(cfg)）
    cross_check_diff: int
    cross_check_pct: float


def bootstrap(conn, cfg: DSSConfig, pool: VehiclePool, now: datetime) -> BootstrapReport:
    """B4 七点：回放窗 = [now − bootstrap_hours, now]，**同一个 ingest**
    （VehiclePool.ingest 单一状态机，不建第二套逻辑）；无本地状态文件
    （状态纯由事件流重建）。

    回放喂入序 = (BODY_ID, ts, ID) 升序：状态机逐车独立，车内 ts 序即正确序；
    窗口是完整数据，无「迟到」概念（skew 分流是增量流专属）——实测 120h 窗
    ID 序 ts 逆序率 52%（2026-09-24 干跑踩坑：按 ID 序喂会触发 ~13.6 万次
    per-car 日志子查询 → 15min 超时），纯 ingest 26 万行 ≈ 秒级。
    watermark = 窗内最大行 ID（F8 增量流起点）；随后交叉核对并出报告。
    """
    _require_aware(now)  # R7 同纪律
    since = now - timedelta(hours=cfg.bootstrap_hours)
    cur = conn.cursor()
    cur.execute(_BOOTSTRAP_SQL, (since, now))
    events = [_row_to_event(r) for r in cur.fetchall()]
    for ev in sorted(events, key=lambda e: (e.body_id, e.ts, e.row_id)):
        pool.ingest(ev)
    watermark = max((e.row_id for e in events), default=0)
    data_max_ts = max((e.ts for e in events), default=None)
    snap = pool.snapshot()
    flag_dist: dict[str, int] = {}
    seg_dist: dict[str, int] = {}
    for st in snap.values():
        flag_dist[st.flag] = flag_dist.get(st.flag, 0) + 1
        seg = st.station[:2]
        seg_dist[seg] = seg_dist.get(seg, 0) + 1
    diff, pct = cross_check(pool, conn, hours=cfg.bootstrap_hours, now=now)
    return BootstrapReport(
        pool_size=len(snap),
        flag_dist=flag_dist,
        seg_dist=seg_dist,
        data_max_ts=data_max_ts,
        watermark=watermark,
        config_echo=asdict(cfg),
        cross_check_diff=diff,
        cross_check_pct=pct,
    )


def cross_check(pool: VehiclePool, conn, hours: float = 120.0,
                now: datetime | None = None) -> tuple[int, float]:
    """B4 点⑤ 启动交叉核对：回放池 vs 外部在制参考集。

    参考集 = `dim.dim_vehicle_profile` 中 carbody_last_seen_at 在 [now−hours, now]
    内 且 carbody_last_rw_station ∉（4 出站站 ∪ 5 出站延续站）——即「窗内见过
    且末站在中线」的外部在制车集合（B4 拍板文本的 dim_vehicle_profile 口径；
    本库该表 = 车辆主档含 carbody_* 生命周期列，实测 2026-09-24 同步新鲜）。
    返回 (差集数, 差集%)；pct = 差集 / max(池, 参考)——>1% 为 DoD 门槛
    （启动报告出 warning，不自动修）。now=None 时取 DB NOW()（wall_clock 裸调用）。
    """
    if now is None:
        cur = conn.cursor()
        cur.execute("SELECT NOW()")
        now = cur.fetchall()[0][0]
    _require_aware(now)  # R7 同纪律
    since = now - timedelta(hours=hours)
    cur = conn.cursor()
    cur.execute(_CROSS_CHECK_SQL, (since, now, *EXIT_STATIONS, *POST_EXIT_STATIONS))
    ref = {r[0] for r in cur.fetchall()}
    pool_bodies = set(pool.snapshot().keys())
    diff = len(pool_bodies ^ ref)
    base = max(len(pool_bodies), len(ref), 1)
    return diff, diff / base
