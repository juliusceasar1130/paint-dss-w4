# -*- coding: utf-8 -*-
"""T2.4/T2.6 启动编排：启动序列（T2.4 骨架）+ 主循环（T2.6 补）。

启动序列顺序（docs/03 T2.4 Step 5 定版）：
  读周计划【T1 未就绪 = 跳过 + 标注】→ 加载 dss.dim_*/v2_*（存在性检查即可，内容 T3 用）→
  bootstrap（120h 回放同一状态机 + cross_check）→ 打印启动报告（全部生效配置值）→
  增量流以 BootstrapReport.watermark 起步（F8：不得从 0 重拉；meta.refresh_watermark
  是 ETL 层水位、仅作 B3 S2 诊断、不作 DSS 起始水位）→ B3 首查
  （启动即陈旧 → 立即宣告「数据陈旧/无数据属预期」，不假装最新）。

文件名 01_ 前缀（同 W1/W2 脚本约定，直接可执行）；测试 import 走 importlib
（precedent：spike_w3/common.py 的 w1_common/w2_shifts 加载 + sys.modules 注册）。
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spike_w3 import plan_loader  # noqa: E402  T1-lite 周计划解析器
from spike_w3.common import (  # noqa: E402
    DSSConfig, POST_EXIT_STATIONS, load_config, connect,
)
from spike_w3.stream_pipeline import (  # noqa: E402
    BootstrapReport, EventStream, FreshnessGuard, VehiclePool, bootstrap,
    derive_postexit_stations, record_dead_audit,
)
from spike_w3.stream_pipeline import _require_aware  # noqa: E402  R7 同纪律

# dss 学习表存在性清单（W1 六表 + W2 v2 六表；启动只查存在性，内容 T3 用）
DSS_DIM_TABLES = (
    "dim_station_takt", "dim_remaining_time", "dim_future_flag",
    "dim_loop_duration", "dim_loop_remaining", "dim_learner_meta",
    "v2_line_prob", "v2_cohort_remaining", "v2_future_flag",
    "v2_comp_excl", "v2_station_quality", "v2_learner_meta",
    "dim_weekly_plan",   # T1-lite（周计划入库表，docs/06）
)


@dataclass
class BootReport:
    """启动报告数据（渲染 = render_boot_report，含全部生效配置值——DoD）。"""

    plan_note: str
    dim_tables: dict              # 表名 → 是否存在
    bootstrap: BootstrapReport
    guard_status: str
    guard_label: str
    guard_is_anomaly: bool
    frozen: bool
    postexit_topo: str            # 出站延续站 derive 闸门结论（定版：不一致只进报告，不自动改）
    warning: str                  # cross_check > 1% 时非空（DoD 门槛，只报告不自动修）


def load_week_plan(cfg: DSSConfig):
    """读周计划（T1-lite，docs/06）：DSS_PLAN_CSV env = 单文件模式（回放干跑显式覆盖，
    旧 main 行为保留）；否则 load_plan(cfg.plan_folder)（目录扫描 + 跨文件合并）。
    无文件 / 全失败 / 跨文件冲突 → None（兜底自学模式：班制推断、无目标）。"""
    single = os.environ.get("DSS_PLAN_CSV")
    if single:
        p = Path(single)
        if not p.is_file():
            return None
        parse = plan_loader.parse_csv if p.suffix.lower() == ".csv" else plan_loader.parse_excel
        try:
            w = parse(p)
        except Exception:
            return None
        return w if w.rows else None
    try:
        w = plan_loader.load_plan(cfg.plan_folder)
    except ValueError:
        return None          # 跨文件冲突（同日期不同值）→ 兜底自学，人工修文件
    return w if w.rows else None


def plan_note_text(plan, now: datetime) -> str:
    """plan_note 三态（T1.4）：无计划 = 兜底自学 / 已加载（+ 当周缺日标注 + 坏行数）。"""
    if plan is None:
        return "无周计划文件 → 兜底自学模式（班制推断、无目标）"
    parts = [f"周计划已加载: {len(plan.rows)} 天 / {len(plan.file_hashes)} 文件"]
    d0 = now.date() - timedelta(days=now.weekday())
    miss = plan.missing(d0, d0 + timedelta(days=6))
    if miss:
        parts.append(f"当周缺 {len(miss)} 天（{miss[0]}~{miss[-1]}），兜底标注")
    if plan.bad_rows:
        parts.append(f"坏行 {len(plan.bad_rows)}")
    if len(parts) == 1:
        return parts[0]
    return parts[0] + "（" + "，".join(parts[1:]) + "）"


def replay_rest_days(cfg: DSSConfig) -> set:
    """回放休息日（B3 expected_silence）：DSS_PLAN_CSV = 单文件（旧行为）；
    否则目录合并 rest_days（与 02 逐文件 |= 语义逐位一致，T1.4 回归钉死）。"""
    single = os.environ.get("DSS_PLAN_CSV")
    if single:
        return load_rest_days(single)
    w = load_week_plan(cfg)
    return w.rest_days() if w else set()


def check_dim_tables(conn) -> dict:
    """dss.dim_*/v2_* 存在性检查（只读 information_schema，不加载内容）。"""
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'dss'")
    have = {r[0] for r in cur.fetchall()}
    return {t: (t in have) for t in DSS_DIM_TABLES}


def boot_sequence(conn, cfg: DSSConfig, now: datetime,
                  expected_silence: bool = False, pool=None) -> BootReport:
    """启动序列（顺序定版；watermark=0 = 窗内无数据 → 中止，不得从 0 起增量流，F8）。

    pool 可外部注入（main：主循环复用同一池实例）；缺省自建。
    """
    plan = load_week_plan(cfg)
    plan_note = plan_note_text(plan, now)
    warnings = []
    plan_loader.ensure_weekly_plan_tables(conn)   # T1.4：DDL 先行（全新库首启 13 项检查前建表，agy 低 L3）
    if plan is not None:
        try:
            n_written = plan_loader.persist_plan(conn, plan)
            if n_written:
                plan_note += f"（入库 {n_written} 行）"
        except Exception as e:
            plan_note += f"（入库失败 {type(e).__name__}——旧数据保留，只报告不中止）"
            warnings.append(f"周计划入库失败（旧数据保留）: {e}")
    dim_tables = check_dim_tables(conn)
    # 出站延续站 derive 启动闸门（2026-09-24 agy 审核中②接入）：数据驱动重推 vs 常量，
    # 不一致只进启动报告 + warning，不自动改常量
    derived_post = derive_postexit_stations(conn)
    if derived_post == frozenset(POST_EXIT_STATIONS):
        postexit_topo = f"出站延续站 derive 一致（{len(POST_EXIT_STATIONS)} 站）"
    else:
        postexit_topo = (f"出站延续站拓扑漂移：derive={sorted(derived_post)} "
                         f"vs 常量={sorted(POST_EXIT_STATIONS)}——人工核对，不自动改")
    pool = pool if pool is not None else VehiclePool(dead_vehicle_hours=cfg.dead_vehicle_hours)
    report = bootstrap(conn, cfg, pool, now)   # 含 cross_check（B4 点⑤）
    if report.watermark == 0:
        raise RuntimeError("回放窗无数据（watermark=0），中止启动——增量流不得从 0 起步（F8）")
    guard = FreshnessGuard(cfg)
    frozen = guard.check(now, report.data_max_ts, expected_silence=expected_silence)
    if report.cross_check_pct > 0.01:
        warnings.append(f"cross_check 差集率 {report.cross_check_pct * 100:.2f}% > 1% "
                        f"（差 {report.cross_check_diff} 台）——人工核对，不自动修")
    if derived_post != frozenset(POST_EXIT_STATIONS):
        warnings.append(postexit_topo)
    return BootReport(
        plan_note=plan_note, dim_tables=dim_tables, bootstrap=report,
        guard_status=guard.status, guard_label=guard.label,
        guard_is_anomaly=guard.is_anomaly, frozen=frozen,
        postexit_topo=postexit_topo, warning=" ".join(warnings),
    )


def render_boot_report(br: BootReport) -> str:
    """启动报告文本（全部生效配置值 + 池/核对/新鲜度状态）。"""
    b = br.bootstrap
    lines = [
        "=== DSS 启动报告 ===",
        f"周计划        : {br.plan_note}",
        f"学习表存在性  : " + ", ".join(f"{t}={'✓' if ok else '✗'}" for t, ok in br.dim_tables.items()),
        f"在制池        : {b.pool_size} 台 | flag 分布 {b.flag_dist} | 站前缀粗分 {b.seg_dist}",
        f"数据截至      : {b.data_max_ts}",
        f"增量流水位    : {b.watermark}（= 回放窗最大行 ID，F8）",
        f"cross_check   : 差 {b.cross_check_diff} 台 / {b.cross_check_pct * 100:.2f}%",
        f"延续站闸门    : {br.postexit_topo}",
        f"新鲜度        : {br.guard_status}（{br.guard_label}）冻结={br.frozen} 异常={br.guard_is_anomaly}",
    ]
    if br.warning:
        lines.append(f"⚠ {br.warning}")
    lines.append("生效配置     : " + " ".join(f"{k}={v}" for k, v in sorted(b.config_echo.items())))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# T2.6 主循环（wall_clock 在线 / replay_cursor 回放干跑）
# ---------------------------------------------------------------------------

def run_one_round(pool, stream, guard, conn, now, expected_silence: bool = False) -> dict:
    """主循环一轮（唯一一轮逻辑；wall_clock / replay_cursor 共用）。

    顺序定版（docs/03 §7 Step 1）：consume(now) → frozen = guard.check(now, stream.front)
    （每轮唯一调用点，不得二次调用）→ dead = pool.dead_sweep(now, frozen)
    （恒调用，冻结语义在 sweep 内部消化）→ 有死车：record_dead_audit + commit → 状态行。
    """
    stream.consume(now, pool)
    frozen = guard.check(now, stream.front, expected_silence=expected_silence)
    dead = pool.dead_sweep(now, frozen=frozen)
    if dead:
        record_dead_audit(conn, dead, now)
        conn.commit()
    return {
        "now": now,
        "pool_size": len(pool.snapshot()),
        "max_ts": stream.front,
        "guard_state": guard.status,
        "guard_label": guard.label,
        "frozen": frozen,
        "dead_count": len(dead),
    }


def _render_status_line(st: dict) -> str:
    return (f"[{st['now']:%m-%d %H:%M}] 池={st['pool_size']} max_ts={st['max_ts'] or '-'} "
            f"guard={st['guard_state']}({st['guard_label']}) 冻结={st['frozen']} 死车={st['dead_count']}")


def _rebuild(conn, cfg: DSSConfig, pool, stream, now) -> None:
    """周期全量重建（T2.6 F-1 自愈）：先建影子池（120h 窗，同状态机）再原子置换
    （agy T2.6 审核低①：bootstrap 中途失败 → 保留旧池并抛异常，不留空池）
    + 水位对齐（只升不降，F8）+ front = 重建窗 data_max_ts。"""
    temp = VehiclePool(dead_vehicle_hours=pool.dead_vehicle_hours)
    report = bootstrap(conn, cfg, temp, now)
    pool._vehicles = temp._vehicles
    pool._visited = temp._visited
    pool._station_last_event_ts = temp._station_last_event_ts
    pool._seen_row_ids = temp._seen_row_ids
    pool.audit = temp.audit
    stream.set_watermark(report.watermark)
    stream.front = report.data_max_ts


def run_replay(pool, stream, guard, conn, cfg: DSSConfig, start, end,
               step_sec: int | None = None, expected_silence_fn=None,
               verbose: bool = False, rebuild_hours: float | None = None,
               last_rebuild=None, round_hook=None) -> list[dict]:
    """replay_cursor 模式主循环（回放干跑 / 回归）。

    游标步进 start→end（步长 = step_sec，默认 poll_interval_sec，可 env 覆盖；
    now = 游标时刻 aware，R7）；每轮 run_one_round；返回状态行列表（轨迹/测试）。

    rebuild_hours（T2.6 F-1 自愈，2026-09-25）：每间隔做一次 120h 全量重建
    （consume 水位跳过的丢行/中毒上限 = 一个重建间隔）；回放结束后在终点
    再做一次重建（回放终态 = 终点纯 bootstrap 真值，验收锚点）。缺省 None = 关。

    round_hook（T6-lite，docs/07 v1.2）：每轮 run_one_round 后调用
    hook(cur, pool, conn, cfg, st)（T6 控制台/快照 ctx 挂接点）；默认 None = 零影响。
    """
    _require_aware(start)   # R7 同纪律
    _require_aware(end)
    step = timedelta(seconds=step_sec if step_sec is not None else cfg.poll_interval_sec)
    last_rebuild = last_rebuild if last_rebuild is not None else start
    status = []
    cur = start
    while cur < end:
        if rebuild_hours and (cur - last_rebuild) >= timedelta(hours=rebuild_hours):
            _rebuild(conn, cfg, pool, stream, cur)
            last_rebuild = cur
        exp = bool(expected_silence_fn(cur)) if expected_silence_fn else False
        st = run_one_round(pool, stream, guard, conn, cur, expected_silence=exp)
        if round_hook is not None:
            round_hook(cur, pool, conn, cfg, st)
        status.append(st)
        if verbose:
            print(_render_status_line(st))
        cur = cur + step
    if rebuild_hours:
        _rebuild(conn, cfg, pool, stream, end)   # 回放终态 = 终点纯 bootstrap 真值
    return status


def run_wall_clock(pool, stream, guard, conn, cfg: DSSConfig,
                   expected_silence_fn=None, round_hook=None) -> None:
    """wall_clock 在线主循环（生产形态）：5min 轮询（poll_interval_sec），now = 墙钟 aware；
    每 rebuild_hours 一次 120h 全量重建（F-1 丢行/水位中毒自愈，默认 24h）。
    round_hook（T6-lite，docs/07 v1.2）：每轮 run_one_round 后调用
    hook(now, pool, conn, cfg, st)；默认 None = 零影响。"""
    import time as _time
    last_rebuild = datetime.now().astimezone()
    while True:
        now = datetime.now().astimezone()
        if (now - last_rebuild) >= timedelta(hours=cfg.rebuild_hours):
            _rebuild(conn, cfg, pool, stream, now)
            last_rebuild = now
        exp = bool(expected_silence_fn(now)) if expected_silence_fn else False
        st = run_one_round(pool, stream, guard, conn, now, expected_silence=exp)
        if round_hook is not None:
            round_hook(now, pool, conn, cfg, st)
        print(_render_status_line(st))
        _time.sleep(cfg.poll_interval_sec)


def load_rest_days(csv_path: str) -> set:
    """向后兼容垫片（供 02/03 冻结回放器调用——02 L363–367 动态加载本模块并调本函数；
    **T1 接线后禁止删除**，agy 中 M3）。实现委托 plan_loader：utf-8-sig / M-D 双格式 /
    坏行跳过；休息日语义不变 = 日+中皆休。"""
    return plan_loader.parse_csv(csv_path).rest_days()


def main():
    cfg = load_config()
    conn = connect()
    try:
        import os as _os
        replay_start = replay_end = None
        if cfg.clock_mode == "replay_cursor":
            start_s = _os.environ.get("DSS_REPLAY_START")
            end_s = _os.environ.get("DSS_REPLAY_END")
            if not start_s or not end_s:
                raise SystemExit("replay_cursor 模式需 DSS_REPLAY_START/DSS_REPLAY_END env（ISO，如 2026-09-07T00:00）")
            replay_start = datetime.fromisoformat(start_s).astimezone()
            replay_end = datetime.fromisoformat(end_s).astimezone()
        # agy T2.6 审核中等①：replay_cursor 模式 bootstrap 必须锚定回放起点 start
        #（水位 = 回放窗最大 ID）——锚定墙钟会使水位置顶全库当前最大 ID，回放消费全程空转
        boot_now = replay_start if replay_start is not None else datetime.now().astimezone()
        pool = VehiclePool(dead_vehicle_hours=cfg.dead_vehicle_hours)
        br = boot_sequence(conn, cfg, boot_now, pool=pool)
        print(render_boot_report(br))
        # 增量流：F8 水位 = 启动回放窗最大行 ID；front = data_max_ts
        # （agy T2.4 审核低③：防增量流首轮失去时间基准线）
        stream = EventStream(conn, wm=br.bootstrap.watermark,
                             skew_window_min=cfg.skew_window_min)
        stream.front = br.bootstrap.data_max_ts
        guard = FreshnessGuard(cfg)
        # T6-lite（docs/07 v1.2 拍板④）：控制台/快照 hook（DSS_CONSOLE 默认开；
        # 0 = 关，回归基准两跑 diff 判据）；装配失败不阻断主循环（console 是附属件）
        round_hook = None
        if cfg.console:
            try:
                from spike_w3 import dashboard as _dash
                plan_w = load_week_plan(cfg)
                comps = _dash.build_t6_components(
                    conn, cfg, plan_w, guard,
                    mode="replay" if cfg.clock_mode == "replay_cursor" else "wall",
                    snapshot_dir=cfg.snapshot_dir, sink=print,
                    run_tag_src_ts=(replay_start if cfg.clock_mode == "replay_cursor"
                                    else None),
                    get_watermark=lambda: stream.watermark)
                print(f"[T6] run_tag={comps['run_tag']}"
                      + (f"（{comps['tag_note']}）" if comps["tag_note"] else ""))
                round_hook = comps["hook"]
            except Exception as e:  # console 是附属件：装配失败不阻断主循环
                print(f"[T6] WARNING 装配失败，主循环继续（无控制台/快照）: {e!r}")
        if cfg.clock_mode == "replay_cursor":
            rest = replay_rest_days(cfg)   # T1.4：目录合并（单文件 DSS_PLAN_CSV 时 = 旧行为）
            run_replay(
                pool, stream, guard, conn, cfg,
                start=replay_start, end=replay_end,
                expected_silence_fn=(lambda t: t.date() in rest) if rest else None,
                rebuild_hours=cfg.rebuild_hours,
                last_rebuild=replay_start,  # 回放起点 = 初始状态锚点
                verbose=True,
                round_hook=round_hook,
            )
        else:
            run_wall_clock(pool, stream, guard, conn, cfg, round_hook=round_hook)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
