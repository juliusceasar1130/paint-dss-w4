# -*- coding: utf-8 -*-
"""T1-lite 周计划解析器（docs/06 v1.2；§3 T1.1 解析核心 RED）。

全离线（tmp_path 合成文件 + plan_inbox 真实样本只读）。
契约冻结 = docs/06 §1；行级规则 = §2.1（坏行留痕 / _parse_int / M/D 候选年份 / 表头 strip）。
"""
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from openpyxl import Workbook

from spike_w2 import shifts
from spike_w3 import plan_loader as pl

ROOT = Path(__file__).resolve().parent.parent
KW40 = ROOT / "plan_inbox" / "week_plan_2026KW40_sample.csv"


def _csv_path(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


HDR = "日期,日班工时,中班工时,出车计划,进车计划,备注\n"


# --- T1.1-1 CSV happy path（KW40_sample 7 行逐格） ---
def test_csv_happy_kw40_sample():
    p = pl.parse_csv(KW40)
    assert len(p.rows) == 7 and p.bad_rows == []
    r = p.rows[date(2026, 9, 28)]
    assert (r.day_h, r.mid_h, r.total, r.entry) == (11, 11, 930, 950)
    assert r.note == "正常双班" and r.source == "file"
    assert r.source_file == "week_plan_2026KW40_sample.csv"
    assert p.rows[date(2026, 9, 29)].mid_h == 10          # 中班10h
    assert p.rows[date(2026, 10, 3)].total == 980         # 周六大加班
    rest = p.rows[date(2026, 10, 4)]                      # 休,休,0,0
    assert (rest.day_h, rest.mid_h, rest.total, rest.entry) == (None, None, 0, 0)
    assert p.file_hashes["week_plan_2026KW40_sample.csv"] == hashlib.sha256(
        KW40.read_bytes()).hexdigest()


def test_csv_bom_compatible(tmp_path):
    p = _csv_path(tmp_path, "week_plan_2026KW40.csv",
                  "\ufeff" + HDR + "9/28,11,11,900,,\n")
    w = pl.parse_csv(p)
    assert w.rows[date(2026, 9, 28)].total == 900 and w.bad_rows == []


# --- T1.1-2/3 M/D 双格式 + ISO 周不符坏行 ---
def test_md_format_file_year():
    # tmp 合成（文件名 KW40 决定 ISO 校验基准）
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        q = _csv_path(Path(td), "week_plan_2026KW40.csv", HDR + "9/28,11,11,900,900,MD行\n")
        w = pl.parse_csv(q)
        assert w.rows[date(2026, 9, 28)].total == 900    # 2026-W40-1
        assert w.bad_rows == []


def test_md_iso_week_mismatch_bad_row(tmp_path):
    q = _csv_path(tmp_path, "week_plan_2026KW40.csv", HDR + "1/2,11,11,900,,跨年不符\n")
    w = pl.parse_csv(q)
    assert w.rows == {}
    assert len(w.bad_rows) == 1 and w.bad_rows[0][0] == "week_plan_2026KW40.csv"


# --- T1.1-4 工时预校验 ---
def test_hours_prevalidation(tmp_path):
    q = _csv_path(tmp_path, "week_plan_2026KW40.csv",
                  HDR + "9/28,12,11,900,,笔误\n"     # 12 = 坏行
                  + "9/29,10h,10,900,,归一\n"         # 10h → 10
                  + "9/30,11,休,900,,休中\n"
                  + "10/1,11,0,900,,零中\n"
                  + "10/2,11,,900,,空中\n")
    w = pl.parse_csv(q)
    assert date(2026, 9, 28) not in w.rows and any("9/28" in r[1] or "12" in r[1] for r in w.bad_rows)
    assert w.rows[date(2026, 9, 29)].day_h == 10
    assert w.rows[date(2026, 9, 30)].mid_h is None
    assert w.rows[date(2026, 10, 1)].mid_h is None
    assert w.rows[date(2026, 10, 2)].mid_h is None
    assert len(w.bad_rows) == 1


# --- T1.1-5 出车计划 非整数/空 ---
def test_total_parse(tmp_path):
    q = _csv_path(tmp_path, "week_plan_2026KW40.csv",
                  HDR + "9/28,11,11,abc,,非整\n" + "9/29,11,11,,900,空出车\n")
    w = pl.parse_csv(q)
    assert date(2026, 9, 28) not in w.rows and len(w.bad_rows) == 1
    assert w.rows[date(2026, 9, 29)].total is None


# --- T1.1-6 核心列缺 → ParseError ---
def test_missing_core_header_raises(tmp_path):
    q = _csv_path(tmp_path, "week_plan_2026KW40.csv",
                  "日期,中班工时,出车计划,进车计划,备注\n9/28,11,900,900,\n")
    with pytest.raises(pl.ParseError):
        pl.parse_csv(q)


# --- T1.1-7 同文件日期重复 → 后行覆盖 + 留痕 ---
def test_dup_date_last_wins(tmp_path):
    q = _csv_path(tmp_path, "week_plan_2026KW40.csv",
                  HDR + "9/28,11,11,900,,初版\n9/28,11,11,950,,修订\n")
    w = pl.parse_csv(q)
    assert w.rows[date(2026, 9, 28)].total == 950
    assert len(w.bad_rows) == 1 and "重复" in w.bad_rows[0][1]


# --- T1.1-8 跨文件冲突/一致（load_plan） ---
def test_cross_file_conflict_and_merge(tmp_path):
    a = _csv_path(tmp_path, "week_plan_2026KW40.csv", HDR + "9/28,11,11,900,900,甲\n")
    b = _csv_path(tmp_path, "week_plan_2026KW40_b.csv", HDR + "9/28,11,11,900,900,乙\n")
    w = pl.load_plan(tmp_path)
    assert len(w.rows) == 1 and w.rows[date(2026, 9, 28)].total == 900 and w.bad_rows == []
    b2 = _csv_path(tmp_path, "week_plan_2026KW40_b.csv", HDR + "9/28,11,11,910,900,改\n")
    with pytest.raises(ValueError):
        pl.load_plan(tmp_path)
    del a


# --- T1.1-9 Excel 同值 ---
def test_excel_same_values(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["日期", "日班工时", "中班工时", "出车计划", "进车计划", "备注"])
    ws.append(["2026-09-28", 11, 11, 930, 950, "正常双班"])
    x = tmp_path / "week_plan_2026KW40.xlsx"
    wb.save(x)
    w = pl.parse_excel(x)
    r = w.rows[date(2026, 9, 28)]
    assert (r.day_h, r.mid_h, r.total, r.entry) == (11, 11, 930, 950)
    assert w.bad_rows == []


# --- T1.1-10 shift_windows 委托（对 shifts 常量断言） ---
def test_shift_windows_delegates():
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        q = _csv_path(Path(td), "week_plan_2026KW40.csv",
                      HDR + "9/28,11,11,900,,\n9/29,10,10,900,,\n9/30,11,11,900,,\n10/1,11,10,900,,\n")
        w = pl.parse_csv(q)
        d = date(2026, 9, 28)
        sw = w.shift_windows(d)
        assert len(sw) == 2
        day, mid = sw
        assert day.kind == "day" and day.type == "11h"
        assert day.work_start.time() == shifts.DAY_TYPES["11h"]        # 05:00（常量防复制）
        assert day.end.time() == shifts.TANK_DAY_END                  # 16:30
        assert mid.kind == "mid" and mid.type == "11h"
        assert mid.work_start.time() == shifts.MID_START
        assert mid.end.time() == shifts.MID_END_TIMES["11h"]          # 04:00
        sw2 = w.shift_windows(date(2026, 9, 29))
        assert sw2[0].work_start.time() == shifts.DAY_TYPES["10h"]    # 06:00
        assert sw2[1].end.time() == shifts.MID_END_TIMES["10h"]       # 03:00
        # 单侧休 → 仅一班
        sw3 = w.shift_windows(date(2026, 9, 30))
        assert len(sw3) == 2
        # 缺日 → []
        assert w.shift_windows(date(2026, 9, 27)) == []


# --- T1.1-11 plan_target ≡ split_target（全 H 组合 × total 对打） ---
def test_plan_target_equals_split_target():
    from spike_w3.alert_engine import split_target
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        # 7 个不同日（W40 = 09-28~10-04 恰 7 天）× 7 组 H（docs 6 组 + 非对称 10/11）
        combos = [(11, 11), (11, 10), (11, 8), (10, 11), (10, 10), (10, 8), (8, 8)]
        totals = [0, 770, 920, 999, 0, 770, 920]
        d0 = date(2026, 9, 28)
        lines = [f"{d0 + timedelta(days=i)},{hd},{hm},{totals[i]},,\n"
                 for i, (hd, hm) in enumerate(combos)]
        q = _csv_path(Path(td), "week_plan_2026KW40.csv", HDR + "".join(lines))
        w = pl.parse_csv(q)
        assert w.bad_rows == []
        for i, (hd, hm) in enumerate(combos):
            assert w.plan_target(d0 + timedelta(days=i)) == split_target(hd, hm, totals[i])
        # total None / 缺行 → (None, None)
        q2 = _csv_path(Path(td), "week_plan_2026KW40.csv", HDR + "9/28,11,11,,,\n")
        w2 = pl.parse_csv(q2)
        assert w2.plan_target(date(2026, 9, 28)) == (None, None)
        assert w2.plan_target(date(2026, 9, 29)) == (None, None)


# --- T1.1-12 rest_days ---
def test_rest_days(tmp_path):
    q = _csv_path(tmp_path, "week_plan_2026KW40.csv",
                  HDR + "9/28,休,休,0,,双休\n" + "9/29,11,休,900,,仅中休\n" + "9/30,休,11,900,,仅日休\n")
    w = pl.parse_csv(q)
    assert w.rest_days() == {date(2026, 9, 28)}


# --- T1.1-12a Excel float 单元格（agy M2） ---
def test_excel_float_cells(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.append(["日期", "日班工时", "中班工时", "出车计划", "进车计划", "备注"])
    ws.append(["2026-09-28", 11.0, 11.0, 920.0, 950.0, "float 整数值"])
    ws.append(["2026-09-29", 11.0, 11.0, 920.5, 950.0, "float 非整数"])
    x = tmp_path / "week_plan_2026KW40.xlsx"
    wb.save(x)
    w = pl.parse_excel(x)
    assert w.rows[date(2026, 9, 28)].total == 920
    assert date(2026, 9, 29) not in w.rows and len(w.bad_rows) == 1


# --- T1.1-12b 表头带空格（agy C4-3） ---
def test_header_strip(tmp_path):
    q = _csv_path(tmp_path, "week_plan_2026KW40.csv",
                  " 日期 ,日班工时, 中班工时 ,出车计划,进车计划, 备注 \n"
                  "9/28,11,11,900,900,\n")
    w = pl.parse_csv(q)
    assert w.rows[date(2026, 9, 28)].total == 900


# --- T1.1-12c M/D 候选年份（agy 二轮 M-new） ---
def test_md_candidate_years(tmp_path):
    cases = [  # (文件名, 行, 期望日期, 期望是否坏行)
        ("week_plan_2020KW53.csv", "1/1,11,11,900,,", date(2021, 1, 1), False),
        ("week_plan_2025KW01.csv", "12/30,11,11,900,,", date(2024, 12, 30), False),
        ("week_plan_2026KW01.csv", "12/31,11,11,900,,", date(2025, 12, 31), False),
        ("week_plan_2025KW38.csv", "1/1,11,11,900,,", None, True),
    ]
    for name, row, exp_d, is_bad in cases:
        q = _csv_path(tmp_path, name, HDR + row)
        w = pl.parse_csv(q)
        if is_bad:
            assert w.rows == {} and len(w.bad_rows) == 1
        else:
            assert exp_d in w.rows and w.bad_rows == [], name


# --- T1.1-12d 0 字节文件（agy C4-1） ---
def test_empty_file(tmp_path):
    q = tmp_path / "week_plan_2026KW40.csv"
    q.write_bytes(b"")
    w = pl.parse_csv(q)
    assert w.rows == {}
    assert len(w.bad_rows) == 1 and "空" in w.bad_rows[0][1]
    # 不牵连他文件
    q2 = _csv_path(tmp_path, "week_plan_2026KW40_b.csv", HDR + "9/28,11,11,900,,\n")
    w2 = pl.load_plan(tmp_path)
    assert date(2026, 9, 28) in w2.rows


# ====================================================================== #
# T1.2 监听 + 持久化（假 cursor 模式同 test_w3_bootstrap）
# ====================================================================== #
KW36 = ROOT / "plan_inbox" / "week_plan_2026KW36.csv"


class FakeCursor:
    """SELECT 按 batches 弹（FIFO）；INSERT/CREATE 无返回；占位符数校验。"""
    def __init__(self, batches=()):
        self._batches = list(batches)
        self.queries = []
        self._last = []

    def execute(self, sql, params=None):
        if params is not None:
            assert sql.count("%s") == len(params), (
                f"SQL 占位符数 {sql.count('%s')} != 参数数 {len(params)}: {sql[:80]}")
        self.queries.append((sql, params))
        self._last = self._batches.pop(0) if (self._batches and sql.lstrip().upper().startswith("SELECT")) else []
        return self

    def fetchall(self):
        return self._last

    def fetchone(self):
        return self._last[0] if self._last else None


class FakeConn:
    def __init__(self, cursor):
        self._c = cursor
        self.committed = 0
        self.rolled_back = 0

    def cursor(self):
        return self._c

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        pass


# --- T1.2-13 命名识别（.xls 识别但不支持 → 留痕不崩溃） ---
def test_naming_recognition(tmp_path):
    (tmp_path / "week_plan_2026KW40.csv").write_text(HDR + "9/28,11,11,900,," + chr(10), encoding="utf-8")
    from openpyxl import Workbook
    for name in ("week_plan_KW40.xlsx", "week_plan_2026KW40_sample.xlsx"):
        wb = Workbook(); ws = wb.active
        ws.append(["日期", "日班工时", "中班工时", "出车计划", "进车计划", "备注"])
        wb.save(tmp_path / name)
    (tmp_path / "week_plan_2026KW40_b.xls").write_bytes(b"fake")   # 不支持格式
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "week_plan_2026KW40.csv.bak").write_text("x", encoding="utf-8")
    (tmp_path / "other_week.csv").write_text("x", encoding="utf-8")
    w = pl.load_plan(tmp_path)
    assert set(w.file_hashes) == {
        "week_plan_2026KW40.csv", "week_plan_KW40.xlsx",
        "week_plan_2026KW40_sample.xlsx", "week_plan_2026KW40_b.xls"}
    # .xls 留痕（识别但不支持）；其他文件忽略不报错
    assert any("xls" in r[0] and "不支持" in r[1] for r in w.bad_rows)
    assert w.rows[date(2026, 9, 28)].total == 900


# --- T1.2-14 幂等：同内容二次 persist → 0 写入 ---
def test_persist_idempotent(tmp_path):
    _csv_path(tmp_path, "week_plan_2026KW40.csv", HDR + "9/28,11,11,900,,\n")
    w = pl.load_plan(tmp_path)
    h = w.file_hashes["week_plan_2026KW40.csv"]
    conn1 = FakeConn(FakeCursor(batches=[[]]))
    assert pl.persist_plan(conn1, w) == 1 and conn1.committed == 1
    c2 = FakeCursor(batches=[[(h,)]])
    conn2 = FakeConn(c2)
    assert pl.persist_plan(conn2, w) == 0
    assert not any("INSERT INTO dss.dim_weekly_plan" in s for s, _ in c2.queries)


# --- T1.2-15 内容变更：仅该文件行重写 ---
def test_persist_content_change_only_touches_file(tmp_path):
    a = _csv_path(tmp_path, "week_plan_2026KW40.csv", HDR + "9/28,11,11,900,,\n")
    _csv_path(tmp_path, "week_plan_2026KW40_b.csv", HDR + "9/29,11,11,800,,\n")
    w = pl.load_plan(tmp_path)
    ha = w.file_hashes["week_plan_2026KW40.csv"]
    hb = w.file_hashes["week_plan_2026KW40_b.csv"]
    assert pl.persist_plan(FakeConn(FakeCursor(batches=[[], []])), w) == 2
    a.write_text(HDR + "9/28,11,11,950,,\n", encoding="utf-8")
    w2 = pl.load_plan(tmp_path)
    assert w2.file_hashes["week_plan_2026KW40.csv"] != ha
    c2 = FakeCursor(batches=[[(ha,)], [(hb,)]])   # A 变 → 重写；B 未变 → 跳过
    n = pl.persist_plan(FakeConn(c2), w2)
    ins = [p for s, p in c2.queries if s.startswith("INSERT INTO dss.dim_weekly_plan")]
    assert n == 1 and len(ins) == 1 and ins[0][0] == date(2026, 9, 28)


# --- T1.2-16 DDL / persist SQL 纪律 ---
def test_ddl_and_persist_sql_discipline(tmp_path):
    cur = FakeCursor()
    pl.ensure_weekly_plan_tables(FakeConn(cur))
    t1, t2 = [s for s, _ in cur.queries]
    assert t1.startswith("CREATE TABLE IF NOT EXISTS dss.dim_weekly_plan")
    assert t2.startswith("CREATE TABLE IF NOT EXISTS dss.work_plan_file_meta")
    for col in ("plan_date", "day_hours", "mid_hours", "total_plan", "entry_plan",
                "note", "week_label", "source", "source_file", "loaded_at"):
        assert col in t1
    assert "PRIMARY KEY" in t1 and "PRIMARY KEY" in t2
    for col in ("file_name", "sha256"):
        assert col in t2
    assert "ods." not in t1 + t2
    _csv_path(tmp_path, "week_plan_2026KW40.csv", HDR + "9/28,11,11,900,,\n")
    w = pl.load_plan(tmp_path)
    cur2 = FakeCursor(batches=[[]])
    pl.persist_plan(FakeConn(cur2), w)
    for s, _ in cur2.queries:
        assert s.lstrip().upper().startswith(("SELECT", "INSERT")), s[:60]
    ins_dim = [s for s, _ in cur2.queries if s.startswith("INSERT INTO dss.dim_weekly_plan")]
    assert ins_dim and all("ON CONFLICT (plan_date) DO UPDATE" in s for s in ins_dim)
    ins_meta = [s for s, _ in cur2.queries if s.startswith("INSERT INTO dss.work_plan_file_meta")]
    assert ins_meta and all("ON CONFLICT (file_name) DO UPDATE" in s for s in ins_meta)


# --- T1.2-17 missing：KW36 部分周 ---
def test_missing_kw36_partial_week():
    w = pl.parse_csv(KW36)
    assert w.missing(date(2026, 9, 5), date(2026, 9, 11)) == [
        date(2026, 9, 7), date(2026, 9, 8), date(2026, 9, 9),
        date(2026, 9, 10), date(2026, 9, 11)]


# --- T1.2-18 空目录 ---
def test_empty_folder(tmp_path):
    w = pl.load_plan(tmp_path)
    assert w.rows == {} and w.bad_rows == [] and w.file_hashes == {}


# --- T1.2-18a 改名 upsert（agy M1） ---
def test_persist_rename_upsert(tmp_path):
    a = tmp_path / "week_plan_2026KW40.csv"
    a.write_text(HDR + "9/28,11,11,900,,\n", encoding="utf-8")
    w = pl.load_plan(tmp_path)
    c1 = FakeCursor(batches=[[]])
    pl.persist_plan(FakeConn(c1), w)
    b = tmp_path / "week_plan_2026KW40_renamed.csv"
    b.write_bytes(a.read_bytes()); a.unlink()          # 改名（同内容）
    w2 = pl.load_plan(tmp_path)
    c2 = FakeCursor(batches=[[]])                       # 新名不在 meta
    pl.persist_plan(FakeConn(c2), w2)
    ins = [(s, p) for s, p in c2.queries if s.startswith("INSERT INTO dss.dim_weekly_plan")]
    assert len(ins) == 1 and ins[0][1][8] == "week_plan_2026KW40_renamed.csv"
    assert not any("DELETE" in s.upper() for s, _ in c1.queries + c2.queries)


# --- T1.2-18b file_hashes 填充 + persist 幂等判读 ---
def test_file_hashes_populated_and_used(tmp_path):
    q = _csv_path(tmp_path, "week_plan_2026KW40.csv", HDR + "9/28,11,11,900,,\n")
    w = pl.load_plan(tmp_path)
    h = w.file_hashes["week_plan_2026KW40.csv"]
    assert h == hashlib.sha256(q.read_bytes()).hexdigest()
    cur = FakeCursor(batches=[[]])
    pl.persist_plan(FakeConn(cur), w)
    sels = [p for s, p in cur.queries if s.startswith("SELECT")]
    assert sels and sels[0][0] == "week_plan_2026KW40.csv"
    assert any("work_plan_file_meta" in s for s, _ in cur.queries)


# ====================================================================== #
# T1.3 因果兜底（纯函数 + 假 cursor SQL 纪律）
# ====================================================================== #
from datetime import time as _t
from spike_w3.common import CST as _CST   # 同 spike_w3 其余测试


def _dt(d, h, m):
    return datetime.combine(d, _t(h, m), tzinfo=_CST)


# --- T1.3-19 众数 + 平票取较新 ---
def test_infer_mode_and_tie():
    # 周一：2026-09-07/14/21/28
    hist = [(date(2026, 9, 7), 11, 11), (date(2026, 9, 14), 11, 11),
            (date(2026, 9, 21), 10, 10), (date(2026, 9, 28), 11, 10)]
    assert pl.infer_shift_causal(hist, 0) == (11, 10, "inferred")   # 日 11 三比一；中 11:10 平票 → 较新 09-28 = 10
    hist2 = [(date(2026, 9, 7), 10, 11), (date(2026, 9, 14), 11, 11)]
    assert pl.infer_shift_causal(hist2, 0)[0] == 11                 # 日 10:11 平票 → 较新 09-14 = 11
    # 非同 weekday 不参与
    assert pl.infer_shift_causal([(date(2026, 9, 8), 10, 8)], 0) == (11, 11, "inferred")


# --- T1.3-20 缺数据默认 11 / 一侧缺 ---
def test_infer_defaults():
    assert pl.infer_shift_causal([], 0) == (11, 11, "inferred")
    hist = [(date(2026, 9, 7), 11, None), (date(2026, 9, 14), 11, None)]
    assert pl.infer_shift_causal(hist, 0) == (11, 11, "inferred")   # 中缺 → 11
    hist3 = [(date(2026, 9, 7), None, 10), (date(2026, 9, 14), None, 10)]
    assert pl.infer_shift_causal(hist3, 0) == (11, 10, "inferred")  # 日缺 → 11，中众数 10


# --- T1.3-21 全休众数 → 休 ---
def test_infer_all_rest():
    hist = [(date(2026, 9, 6), None, None), (date(2026, 9, 13), None, None),
            (date(2026, 9, 20), None, None)]
    assert pl.infer_shift_causal(hist, 6) == (None, None, "inferred")
    # 休 + 生产日混合：生产日值胜出（休不进侧投票）
    hist2 = [(date(2026, 9, 6), None, None), (date(2026, 9, 13), 11, 10)]
    assert pl.infer_shift_causal(hist2, 6) == (11, 10, "inferred")


# --- T1.3-22 纯函数纪律：无 DB / 无 W2 detect_* ---
def test_causal_pure_discipline():
    src = Path("spike_w3/plan_loader.py").read_text(encoding="utf-8")
    assert "detect_day_type" not in src and "detect_mid_type" not in src
    import inspect
    params = list(inspect.signature(pl.infer_shift_causal).parameters)
    assert "conn" not in params


# --- T1.3-23 recent_shift_history SQL 纪律 ---
def test_recent_history_sql_discipline():
    now = _dt(date(2026, 9, 27), 12, 0)
    batches = [[(date(2026, 9, 26),)], [], []]     # max_date=09-26；日事件空；下线空
    cur = FakeCursor(batches=batches)
    res = pl.recent_shift_history(FakeConn(cur), now)
    sqls = [s for s, _ in cur.queries]
    assert "SET TIME ZONE 'Asia/Shanghai'" in sqls[0]
    assert 'max("DATE_EVT")' in sqls[1] and "ods.carbody_history" in sqls[1]
    assert any("LIKE '78202%%'" in s for s in sqls)          # 产品车前缀
    assert any(all(x in s for x in ("'K3IS140'", "'1J440RB'", "'K2IS075'", "'K1IS135'")) for s in sqls)
    # 因果上界钉 now：事件查询参数末位 = now
    ev_q = [p for s, p in cur.queries if p is not None and "DATE_EVT" in s and "max(" not in s]
    assert ev_q and all(p[-1] == now for p in ev_q)
    # upper = min(now.date()-1, max_date) = 09-26；14 天
    assert len(res) == 14 and res[-1][0] == date(2026, 9, 26) and res[0][0] == date(2026, 9, 13)


# --- T1.3-24 因果边界：now 当日未完不入 ---
def test_recent_history_now_day_excluded():
    now = _dt(date(2026, 9, 24), 23, 59)
    batches = [[(date(2026, 9, 24),)], [], []]
    res = pl.recent_shift_history(FakeConn(FakeCursor(batches=batches)), now)
    assert res[-1][0] == date(2026, 9, 23)      # 09-24 当日中班未完 → 排除


# --- T1.3-24a 数据上界钳制（agy M4） ---
def test_recent_history_max_date_clamp():
    now = _dt(date(2026, 9, 27), 12, 0)
    batches = [[(date(2026, 9, 24),)], [], []]   # 断流：数据止 09-24
    res = pl.recent_shift_history(FakeConn(FakeCursor(batches=batches)), now)
    assert len(res) == 14 and res[-1][0] == date(2026, 9, 24)
    assert date(2026, 9, 25) not in [r[0] for r in res] and date(2026, 9, 26) not in [r[0] for r in res]


# --- T1.3-24b 日/中班型事后判定（合成） ---
def test_classify_completed_day():
    D = date(2026, 9, 21)
    day_11h = [_dt(D, 5, i % 15) for i in range(520)]          # 05:00 桶 520 ≥ 30
    day_10h = [_dt(D, 6, i % 15) for i in range(520)]
    D1 = D + timedelta(days=1)
    assert pl.classify_completed_day(day_11h, [_dt(D1, 3, 0)])[0] == 11
    assert pl.classify_completed_day(day_10h, [_dt(D1, 3, 0)])[0] == 10
    assert len(day_11h) >= 500
    # 中班：02:45 → 615min = 10h；03:45 → 675 = 11h；00:30 → 480 = 8h
    from datetime import timedelta as _td
    r10 = pl.classify_completed_day([], [_dt(D + _td(days=1), 2, 45)])
    r11 = pl.classify_completed_day([], [_dt(D + _td(days=1), 3, 45)])
    r8 = pl.classify_completed_day([], [_dt(D + _td(days=1), 0, 30)])
    assert (r10[1], r11[1], r8[1]) == (10, 11, 8)
    # 零下线零事件 = 休
    assert pl.classify_completed_day([], []) == (None, None)
    # 截断（末下线 19:00 = 150min < 300）→ 中 None
    assert pl.classify_completed_day([], [_dt(D, 19, 0)])[1] is None


# ====================================================================== #
# T1.4 接线（01_boot_online）+ 回归钉死
# ====================================================================== #
import importlib.util as _ilu
import sys as _sys
from spike_w3.common import DSSConfig as _DSSConfig


def _load_boot():
    p = ROOT / "spike_w3" / "01_boot_online.py"
    spec = _ilu.spec_from_file_location("w3_boot_t1", p)
    m = _ilu.module_from_spec(spec)
    _sys.modules["w3_boot_t1"] = m
    spec.loader.exec_module(m)
    return m


# --- T1.4-25 load_week_plan 三模式 ---
def test_load_week_plan_modes(tmp_path, monkeypatch):
    boot = _load_boot()
    _csv_path(tmp_path, "week_plan_2026KW40.csv", HDR + "9/28,11,11,900,," + chr(10))
    cfg = _DSSConfig(plan_folder=str(tmp_path))
    w = boot.load_week_plan(cfg)
    assert w is not None and date(2026, 9, 28) in w.rows
    # DSS_PLAN_CSV 单文件覆盖（replay 干跑显式覆盖）
    single = _csv_path(tmp_path, "week_plan_2026KW40_single.csv", HDR + "9/29,10,10,700,," + chr(10))
    monkeypatch.setenv("DSS_PLAN_CSV", str(single))
    w2 = boot.load_week_plan(cfg)
    assert w2 is not None and set(w2.rows) == {date(2026, 9, 29)}
    # 空目录 / 文件缺失 → None（兜底自学模式）
    monkeypatch.delenv("DSS_PLAN_CSV")
    empty = tmp_path / "empty"
    empty.mkdir()
    assert boot.load_week_plan(_DSSConfig(plan_folder=str(empty))) is None
    assert boot.load_week_plan(_DSSConfig(plan_folder=str(tmp_path / "nope"))) is None


# --- T1.4-26 resolve_shift 编排（docs/02 契约 source 双值） ---
def test_resolve_shift_orchestration(tmp_path):
    q = _csv_path(tmp_path, "week_plan_2026KW40.csv", HDR + "9/28,11,10,900,," + chr(10))
    w = pl.parse_csv(q)
    hist = [(date(2026, 9, 7), 8, 8), (date(2026, 9, 14), 8, 8)]
    assert pl.resolve_shift(date(2026, 9, 28), w, hist) == (11, 10, "plan")
    assert pl.resolve_shift(date(2026, 10, 5), w, hist) == (8, 8, "inferred")


# --- T1.4-27 plan_note 三态 ---
def test_plan_note_three_states(tmp_path):
    boot = _load_boot()
    now = datetime(2026, 9, 5, 12, 0, tzinfo=_CST)
    assert "兜底" in boot.plan_note_text(None, now)
    q = _csv_path(tmp_path, "week_plan_2026KW36.csv",
                  HDR.replace("日期", "日期") + "2026-09-05,11,11,927,," + chr(10)
                  + "2026-09-06,休,休,0,," + chr(10))
    w = pl.parse_csv(q)
    note = boot.plan_note_text(w, now)
    assert "1 文件" in note and "缺" in note          # 部分周 → 缺日标注
    # W36 = 08-31~09-06（09-05 是周六！）——7 天全覆盖
    d0 = date(2026, 8, 31)
    full = _csv_path(tmp_path, "week_plan_2026KW36_full.csv", HDR +
                     "".join(f"{d0 + timedelta(days=i)},11,11,900,," + chr(10) for i in range(6))
                     + f"{d0 + timedelta(days=6)},休,休,0,," + chr(10))
    # 7 天全覆盖（09-05~09-11）→ 无缺日（删旧文件避免同日期冲突）
    q.unlink()
    wfull = pl.load_plan(tmp_path)
    note_full = boot.plan_note_text(wfull, now)
    assert "缺" not in note_full


# --- T1.4-28 DSS_DIM_TABLES 13 项 ---
def test_dim_tables_13():
    boot = _load_boot()
    assert len(boot.DSS_DIM_TABLES) == 13
    assert "dim_weekly_plan" in boot.DSS_DIM_TABLES


# --- T1.4-29 replay 休息日等价（09-05~09-13 窗回归钉死） ---
def test_replay_rest_days_window_equivalence(monkeypatch):
    boot = _load_boot()
    cfg = _DSSConfig()                                   # plan_folder 默认 plan_inbox（现状 4 文件）
    rest = boot.replay_rest_days(cfg)
    win = {date(2026, 9, 5) + timedelta(days=i) for i in range(9)}
    assert rest & win == {date(2026, 9, 6), date(2026, 9, 13)}
    # DSS_PLAN_CSV 单文件模式 → rest 只来自该文件（与旧行为一致）
    monkeypatch.setenv("DSS_PLAN_CSV", str(ROOT / "plan_inbox" / "week_plan_2026KW36.csv"))
    assert boot.replay_rest_days(cfg) == {date(2026, 9, 6)}
    monkeypatch.delenv("DSS_PLAN_CSV")


# --- T1.4-30 persist 失败回滚 ---
def test_persist_failure_rollback(tmp_path):
    _csv_path(tmp_path, "week_plan_2026KW40.csv", HDR + "9/28,11,11,900,," + chr(10))
    w = pl.load_plan(tmp_path)

    class BoomCursor(FakeCursor):
        def execute(self, sql, params=None):
            if sql.startswith("INSERT INTO dss.dim_weekly_plan"):
                raise RuntimeError("boom")
            return super().execute(sql, params)

    conn = FakeConn(BoomCursor(batches=[[]]))
    try:
        pl.persist_plan(conn, w)
        assert False, "应抛错"
    except RuntimeError:
        pass
    assert conn.rolled_back == 1 and conn.committed == 0


# --- T1.4-30a load_rest_days 垫片存在且等价（agy M3） ---
def test_load_rest_days_shim():
    boot = _load_boot()
    src = (ROOT / "spike_w3" / "01_boot_online.py").read_text(encoding="utf-8")
    assert "def load_rest_days" in src
    rest = set()
    for f in sorted((ROOT / "plan_inbox").glob("week_plan_*.csv")):
        rest |= boot.load_rest_days(str(f))
        assert rest & {f.date for f in ()} == set()       # no-op（保持循环形态 = 02 调用方式）
        w = pl.parse_csv(f)
        assert boot.load_rest_days(str(f)) == w.rest_days()
    assert rest & {date(2026, 9, 6), date(2026, 9, 13)} == {date(2026, 9, 6), date(2026, 9, 13)}


# --- T1.4-30b boot_sequence 顺序（静态钉死，agy L3） ---
def test_boot_sequence_order_static():
    src = (ROOT / "spike_w3" / "01_boot_online.py").read_text(encoding="utf-8")
    seg = src[src.index("def boot_sequence"):src.index("def render_boot_report")]
    i_ensure = seg.index("ensure_weekly_plan_tables")
    i_check = seg.index("check_dim_tables(conn)")
    i_persist = seg.index("persist_plan")
    assert i_ensure < i_check and i_ensure < i_persist
