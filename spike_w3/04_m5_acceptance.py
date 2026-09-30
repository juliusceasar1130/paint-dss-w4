# -*- coding: utf-8 -*-
"""04_m5_acceptance.py — M5 在线验收收账（5 天在线跑完后一条命令出验收报告）。

用法（内网部署机 / 公网收账机均可，需连得上库与快照目录）：
    conda run --no-capture-output -n paint-dss python spike_w3\\04_m5_acceptance.py \
        [--snap-dir output/snapshots/m5] [--tol 0.10] \
        [--dates 2026-10-05,2026-10-06,...] [--out-md output/w3-M5-验收报告.md] \
        [--out-json output/w3-M5-验收.json]

口径（docs/01 W3 验收门 + W2/M9 同源，与 G2 干跑门同源）：
- 主判据：逐工作日 5h 锚点 |Δ实际| ≤ tol（默认 10%）。
  Δ实际 = (pred_锚点 − 实际终局) / 实际终局；
  实际终局 = 4 下线点产品车闭区间计数：
    日班 [D 04:45, D 16:30]（与 a_of `<= end` 同源）
    中班 [D 16:30, D+1 MID_END_TIMES[班次型]]（11h→04:00 / 10h→03:00 / 8h→01:00）
- 锚点帧 = work_start+5h（日班开班随型变：11h→10:00 / 10h→11:00 / 8h→13:00；中恒 21:30）；
  容差匹配 ±3min（墙钟网格对齐后的秒级漂移/首帧未对齐）。
- 锚点帧 STALE（断流冻结）→ 该行不计门，单独标注（四桶互斥：gated/actual_zero/stale/anchor_missing）。
- 休日（锚点帧无班次 / 计划休）→ 剔出门外，列于注记。
- 断流时段：guard.status=STALE 轮次单独统计（连续段归并），不计门（ETL SLA 拍板）。
- 告警台账：快照 alerts 数组（确认转换才发事件）逐条汇总。
- 快照目录可多个合并收账（--snap-dir 可重复）；未纳入的同前缀 -rN 目录显式 WARNING。

只读：本脚本零库写（仅 SELECT）；产物只写本地 MD/JSON。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spike_w2 import shifts as shifts_mod                      # noqa: E402
from spike_w3.common import EXIT_STATIONS                      # noqa: E402

TZ = ZoneInfo("Asia/Shanghai")
PRODUCT_LIKE = "78202%"          # 产品车前缀（同 predictor.PRODUCT_PREFIX）
WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
ANCHOR_TOL_SEC = 180             # 锚点帧容差 ±3min（网格对齐后的帧仍可能有秒级漂移/首帧未对齐）


# ---------------------------------------------------------------- 纯函数

def parse_ts(ts: str) -> datetime:
    """快照 ts（ISO 带偏移）→ aware datetime。"""
    return datetime.fromisoformat(ts)


def anchor_for(d: date, kind: str, shift_type: str) -> datetime:
    """5h 锚点时刻 = work_start + 5h（agy 阻断 F5：日班开班随型变，不再硬编码 10:00）。

    日班 work_start = shifts_mod.DAY_TYPES[type]（11h→05:00→锚 10:00 / 10h→06:00→11:00 / 8h→08:00→13:00）；
    中班 work_start = 16:30 恒常 → 锚恒 21:30。
    """
    ws = shifts_mod.DAY_TYPES[shift_type] if kind == "day" else shifts_mod.MID_START
    return datetime.combine(d, ws, tzinfo=TZ) + timedelta(hours=5)


def pick_anchor(snaps: dict, d: date, kind: str, tol_sec: int = ANCHOR_TOL_SEC):
    """取 (d, kind) 的 5h 锚点帧（容差匹配，agy 阻断 F2）。

    候选 = 该日 ts.date()==d 且 pred.shift.kind==kind 的帧；班次型取最早候选的
    pred.shift.type（跨日中班候选不影响——中班锚恒 21:30）；在候选中取距锚点时刻
    最近且 ≤tol_sec 的一帧。返回 (dt, snap, 期望锚点时刻) 或 None。
    """
    cands = []
    for ts_iso, s in snaps.items():
        dt = parse_ts(ts_iso)
        if dt.date() != d:
            continue
        sh = (s.get("pred") or {}).get("shift")
        if not sh or sh.get("kind") != kind:
            continue
        cands.append((dt, s))
    if not cands:
        return None
    cands.sort(key=lambda x: x[0])
    stype = cands[0][1]["pred"]["shift"].get("type") or "11h"
    anchor = anchor_for(d, kind, stype)
    best = min(cands, key=lambda x: abs((x[0] - anchor).total_seconds()))
    if abs((best[0] - anchor).total_seconds()) > tol_sec:
        return None
    return best[0], best[1], anchor


def shift_actual_window(d: date, kind: str, shift_type: str):
    """实际终局闭区间 [start, end]（与 a_of `<= end` 同源）。

    日班 [D 04:45, D 16:30]；中班 [D 16:30, D+1 MID_END_TIMES[type]]。
    """
    if kind == "day":
        return (datetime.combine(d, shifts_mod.ATTR_DAY_START, tzinfo=TZ),
                datetime.combine(d, shifts_mod.TANK_DAY_END, tzinfo=TZ))
    if kind == "mid":
        return (datetime.combine(d, shifts_mod.MID_START, tzinfo=TZ),
                datetime.combine(d + timedelta(days=1),
                                 shifts_mod.MID_END_TIMES[shift_type], tzinfo=TZ))
    raise ValueError(f"未知班次类型: {kind!r}")


def assess(pred: int, target, actual: int, tol: float) -> dict:
    """单班验收判定。Δ计划 = pred/target−1（参考）；Δ实际 = (pred−actual)/actual（门）。

    actual=0（休日/停线）→ reason=actual_zero（不计门 + 注记，agy 中等 F4：
    结构化 reason 取代 note 子串匹配，避「数据缺失」误判为锚点帧缺失）。
    """
    out = {"pred": pred, "target": target, "actual": actual}
    out["delta_plan"] = round(pred / target - 1, 4) if target else None
    if actual <= 0:
        out["delta_actual"] = None
        out["pass"] = None
        out["reason"] = "actual_zero"
        out["note"] = "实际终局 0（休/停线/无事件）——不计门"
        return out
    out["delta_actual"] = round((pred - actual) / actual, 4)
    out["pass"] = abs(out["delta_actual"]) <= tol
    out["reason"] = "gated"
    out["note"] = ""
    return out


def stale_stats(snaps: dict) -> dict:
    """断流统计：STALE 轮数、分钟数、连续段（5min/轮，段末 = 首非 STALE 轮 ts）。"""
    items = sorted((parse_ts(k), v) for k, v in snaps.items())
    rounds = [(dt, v.get("guard", {}).get("status")) for dt, v in items]
    stale = [dt for dt, st in rounds if st == "STALE"]
    if not stale:
        return {"n_rounds": 0, "minutes": 0, "spans": []}
    spans, start, prev = [], stale[0], stale[0]
    for dt in stale[1:]:
        if dt - prev > timedelta(minutes=5):
            spans.append([start, prev])
            start = dt
        prev = dt
    spans.append([start, prev])
    # 段末扩展到段内最后一轮的下一轮（连续段覆盖时长 = 段长 × 5min）
    spans = [[s, e + timedelta(minutes=5)] for s, e in spans]
    return {"n_rounds": len(stale), "minutes": len(stale) * 5,
            "spans": [[s.isoformat(), e.isoformat()] for s, e in spans]}


def alert_ledger(snaps: dict) -> list:
    """告警台账：快照 alerts 数组（仅确认转换才发事件）按时间排序汇总。"""
    out = []
    for ts_iso in sorted(snaps):
        for a in snaps[ts_iso].get("alerts") or []:
            out.append({"ts": a.get("t", ts_iso), "sid": a.get("sid", "?"),
                        "level": a.get("level"), "prev_level": a.get("prev_level"),
                        "gap_n": a.get("gap_n"), "delta": a.get("delta")})
    return out


# ---------------------------------------------------------------- DB（只读）

def query_actual(conn, d: date, kind: str, shift_type: str) -> int:
    """实际终局出口数（4 下线点、产品车、闭区间 [start, end]）。"""
    s, e = shift_actual_window(d, kind, shift_type)
    cur = conn.cursor()
    cur.execute("SET TIME ZONE 'Asia/Shanghai'")
    ph = ", ".join(["%s"] * len(EXIT_STATIONS))
    cur.execute(
        f"""SELECT count(DISTINCT "BODY_ID") FROM ods.carbody_history
            WHERE "RW_STATION_ID" IN ({ph})
              AND "BODY_ID" LIKE %s
              AND "DATE_EVT" >= %s AND "DATE_EVT" <= %s""",
        tuple(EXIT_STATIONS) + (PRODUCT_LIKE, s, e))
    row = cur.fetchone()
    cur.close()
    return int(row[0]) if row else 0


# ---------------------------------------------------------------- 报告

def _fmt_pct(x):
    return "—" if x is None else f"{x * 100:+.1f}%"


def _fmt_n(x):
    return "—" if x is None else str(x)


def build_report(snap_dirs: list, run_tag: str, n_snaps: int, results: list,
                 stale: dict, ledger: list, tol: float, rest_days: list) -> str:
    """验收报告 MD（主表 + 断流 + 告警台账 + 终判）。snap_dirs = 快照目录列表（可多目录合并收账）。"""
    L = []
    L.append("# W3-M5 在线验收报告\n")
    L.append(f"- 快照目录：{', '.join(f'`{x}`' for x in snap_dirs)}（run_tag = `{run_tag}`，快照 {n_snaps} 个）")
    L.append(f"- 主判据：逐工作日 5h 锚点（日 = work_start+5h 随班次型：11h→10:00 / 10h→11:00 / 8h→13:00；中恒 21:30）"
             f"|Δ实际| ≤ {tol * 100:.0f}%；Δ实际 = (pred − 实际终局)/实际终局；实际 = 4 下线点产品车闭区间计数"
             f"（日 [04:45,16:30] / 中 [16:30,次日按型]，与 a_of `<=end` 同源；锚点帧容差 ±{ANCHOR_TOL_SEC // 60}min）")
    L.append(f"- 生成时间：{datetime.now(TZ).isoformat(timespec='seconds')}\n")

    L.append("## 逐日逐班次判定\n")
    L.append("| 日期 | 周 | 班次 | 锚点帧 | A@锚 | pred | 目标 | 实际终局 | Δ计划 | Δ实际 | 锚点灯 | 判定 | 注记 |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        d: date = r["date"]
        judge = ("—" if r["pass"] is None
                 else ("PASS" if r["pass"] else "FAIL"))
        if r["stale"]:
            judge = "断流冻结(不计门)"
        L.append(f"| {d.isoformat()} | {r.get('weekday', WEEKDAY_CN[d.weekday()])} "
                 f"| {r['kind']} | {r.get('anchor_ts', '—')} | {_fmt_n(r.get('A'))} "
                 f"| {_fmt_n(r['pred'])} | {_fmt_n(r['target'])} | {_fmt_n(r['actual'])} "
                 f"| {_fmt_pct(r.get('delta_plan'))} | {_fmt_pct(r.get('delta_actual'))} "
                 f"| {r.get('grade') or '—'} | **{judge}** | {r.get('note', '')} |")
    L.append("")

    L.append("## 断流统计（不计门，ETL SLA 拍板）\n")
    if stale["n_rounds"] == 0:
        L.append("无 STALE 轮次。")
    else:
        L.append(f"STALE 轮次 {stale['n_rounds']} 轮（≈{stale['minutes']} min），连续段：")
        for s, e in stale["spans"]:
            L.append(f"- {s} ~ {e}")
    L.append("")

    L.append("## 告警台账（确认转换）\n")
    if not ledger:
        L.append("无确认告警。")
    else:
        L.append("| 时刻 | scope | 转换 | 缺口台 | Δ |")
        L.append("|---|---|---|---|---|")
        for a in ledger:
            L.append(f"| {a['ts']} | {a['sid']} | {a.get('prev_level')}→{a.get('level')} "
                     f"| {_fmt_n(a.get('gap_n'))} | {_fmt_pct(a.get('delta'))} |")
    L.append("")

    if rest_days:
        L.append(f"注：休日剔出门外：{', '.join(d.isoformat() for d in rest_days)}。\n")

    failed = [r for r in results if r["reason"] == "gated" and r["pass"] is False]
    gated = [r for r in results if r["reason"] == "gated"]
    na = [r for r in results if r["reason"] == "actual_zero"]
    missing = [r for r in results if r["reason"] == "anchor_missing"]
    stale_rows = [r for r in results if r["reason"] == "stale"]
    verdict = "FAIL" if failed else "PASS"
    L.append(f"## 终判: {verdict}\n")
    L.append(f"计入门 {len(gated)} 个班次行（PASS {len(gated) - len(failed)} / FAIL {len(failed)}）；"
             f"不计门：实际 0（休/停线）{len(na)}、锚点帧 STALE {len(stale_rows)}、"
             f"锚点帧缺失 {len(missing)}（四桶互斥，和 + 计入门 = 总行数）。")
    if missing:
        L.append("锚点帧缺失明细：" + "; ".join(
            f"{r['date'].isoformat()} {r['kind']}（期望锚点 {r.get('anchor_ts', '?')}）" for r in missing))
    if failed:
        L.append("FAIL 明细：" + "; ".join(
            f"{r['date'].isoformat()} {r['kind']} Δ实际={_fmt_pct(r['delta_actual'])}"
            for r in failed))
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- main

def load_snapshots(snap_dirs: list) -> tuple:
    """{ts_iso: snap}（多目录合并，agy 阻断 F3b）。ts 冲突（同轮重复）→ 先入者胜 + 计数。

    返回 (snaps, n_dup)。"""
    out, n_dup = {}, 0
    for d in snap_dirs:
        for p in sorted(Path(d).glob("snap_*.json")):
            with open(p, encoding="utf-8") as f:
                s = json.load(f)
            if s["ts"] in out:
                n_dup += 1
                continue
            out[s["ts"]] = s
    return out, n_dup


def find_sibling_run_dirs(snap_dirs: list) -> list:
    """未纳入收账的同前缀 -rN 快照目录（agy 阻断 F3：显式 tag 幂等追加后正常不会出现；
    旧版本/自动 tag 遗留的 -r 目录会被这里点名提醒，防静默漏收）。"""
    listed = {str(Path(d).resolve()) for d in snap_dirs}
    found = []
    for d in snap_dirs:
        p = Path(d).resolve()
        for sib in sorted(p.parent.glob(f"{p.name}-r*")):
            if sib.is_dir() and str(sib) not in listed and not any(
                    s.startswith(sib.name) for s in map(lambda x: Path(x).name, listed)):
                found.append(str(sib))
    return sorted(set(found))


def main(argv=None):
    ap = argparse.ArgumentParser(description="M5 在线验收收账")
    ap.add_argument("--snap-dir", action="append", default=None,
                    help="快照目录（可重复传多个以合并收账，如 m5 与遗留的 m5-r2；"
                         "缺省 = output/snapshots/m5）")
    ap.add_argument("--tol", type=float, default=0.10)
    ap.add_argument("--dates", default="", help="逗号分隔 YYYY-MM-DD（默认=快照内全部日期）")
    ap.add_argument("--out-md", default="output/w3-M5-验收报告.md")
    ap.add_argument("--out-json", default="output/w3-M5-验收.json")
    args = ap.parse_args(argv)

    from spike_w3.common import connect
    dirs = args.snap_dir or ["output/snapshots/m5"]
    for d in dirs:
        if not Path(d).is_dir():
            raise SystemExit(f"快照目录不存在: {d}")
    snaps, n_dup = load_snapshots(dirs)
    if not snaps:
        raise SystemExit(f"快照目录为空: {dirs}")
    siblings = find_sibling_run_dirs(dirs)
    if siblings:
        print(f"[M5] WARNING: 发现未纳入 --snap-dir 的同前缀快照目录：{siblings}"
              f"（如属同一 M5 运行请补传，否则该时段将被记为锚点帧缺失）")

    dates = ([date.fromisoformat(x) for x in args.dates.split(",") if x.strip()]
             if args.dates else sorted({parse_ts(k).date() for k in snaps}))

    conn = connect()
    results, rest_days = [], []
    try:
        for d in dates:
            for kind in ("day", "mid"):
                got = pick_anchor(snaps, d, kind)
                if got is None:
                    # 锚点帧缺失（进程掉线/快照缺帧/漂移超容差）——显式留痕，不静默跳过
                    exp = anchor_for(d, kind, "11h")   # 型未知时按 11h 期望展示
                    results.append({"date": d, "weekday": WEEKDAY_CN[d.weekday()],
                                    "kind": kind,
                                    "anchor_ts": f"{exp.isoformat()}（缺失）",
                                    "A": None, "pred": None, "target": None, "actual": None,
                                    "delta_plan": None, "delta_actual": None, "grade": None,
                                    "stale": False, "pass": None, "reason": "anchor_missing",
                                    "note": "锚点帧缺失（进程离线/快照缺帧/漂移超容差）——不计门，查该时段日志"})
                    continue
                dt, snap, _exp = got
                sh = (snap.get("pred") or {}).get("shift")
                if not sh or sh.get("pred") is None:
                    if kind == "day":
                        rest_days.append(d)
                    continue
                stale = snap.get("guard", {}).get("status") == "STALE"
                actual = query_actual(conn, d, kind, sh.get("type", "11h"))
                r = assess(pred=sh["pred"], target=sh.get("target"), actual=actual,
                           tol=args.tol)
                r.update({"date": d, "weekday": snap.get("weekday") or WEEKDAY_CN[d.weekday()],
                          "kind": kind, "anchor_ts": dt.isoformat(), "A": sh.get("A"),
                          "grade": sh.get("grade"), "stale": stale})
                if stale:
                    r["reason"] = "stale"      # 断流冻结优先于 gated/actual_zero（四桶互斥）
                    r["note"] = "锚点帧 STALE（断流冻结）——不计门"
                results.append(r)
    finally:
        conn.close()

    stale_stats_all = stale_stats(snaps)
    ledger = alert_ledger(snaps)
    run_tag = Path(dirs[0]).name
    md = build_report(snap_dirs=dirs, run_tag=run_tag, n_snaps=len(snaps),
                      results=results, stale=stale_stats_all,
                      ledger=ledger, tol=args.tol, rest_days=sorted(rest_days))
    out_md = Path(args.out_md)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(md, encoding="utf-8")
    out_json = Path(args.out_json)
    out_json.write_text(json.dumps(
        {"snap_dirs": dirs, "n_dup": n_dup,
         "siblings_unlisted": siblings, "tol": args.tol,
         "results": [{**r, "date": r["date"].isoformat()} for r in results],
         "stale": stale_stats_all, "ledger": ledger,
         "rest_days": [d.isoformat() for d in rest_days]},
        ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    gated = [r for r in results if r["reason"] == "gated"]
    print(f"[M5] 收账完成：计入门 {len(gated)} 行"
          f" / FAIL {sum(1 for r in gated if r['pass'] is False)}"
          f" / STALE 轮 {stale_stats_all['n_rounds']} / 告警 {len(ledger)}"
          + (f" / 重复帧 {n_dup}" if n_dup else ""))
    print(f"[M5] 报告: {out_md}  数据: {out_json}")


if __name__ == "__main__":
    main()
