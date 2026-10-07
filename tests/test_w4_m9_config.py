# -*- coding: utf-8 -*-
"""W4.4c：02_shadow_replay 可调参数契约测试（DSS_* 参数配置规范 2026-09-22）。

对象（最小契约扩展，W3 冻结红线内）：
- load_self_check_band()：DSS_SELF_CHECK_BAND='lo,hi' 覆盖自检固定带（严格校验）
- cross_machine_path() / cross_outdir()：DSS_M9_CROSS_MACHINE / DSS_M9_CROSS_DIR 覆盖
  交叉对拍机器与产物目录（W4 工作区对拍机器 = scratch/tmp_w4_4b_scene.py --scenario 2；
  主仓库 W2.1 机器 scratch/02_replay_kw37.py 不在本 worktree，缺省路径保留 W3 原语义）。
"""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "w4c_shadow_mod", str(ROOT / "spike_w3" / "02_shadow_replay.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture
def m(monkeypatch):
    for k in ("DSS_SELF_CHECK_BAND", "DSS_M9_CROSS_MACHINE", "DSS_M9_CROSS_DIR"):
        monkeypatch.delenv(k, raising=False)
    return _load()


# ---------------- 自检固定带（DSS_SELF_CHECK_BAND） ----------------

def test_band_default(m):
    # W4.4c D5(a) 重锚 2026-10-06：锚点 8.74%→11.63%（新栈复测），几何不变（−0.74pp/+1.76pp）
    assert m.load_self_check_band() == (0.1089, 0.1339)


def test_band_env_override(m, monkeypatch):
    monkeypatch.setenv("DSS_SELF_CHECK_BAND", "0.090,0.120")
    assert m.load_self_check_band() == (0.090, 0.120)


@pytest.mark.parametrize("raw", [
    "0.1",            # 单值
    "abc,0.2",        # 非数值
    "0.5,0.1",        # lo ≥ hi
    "-0.1,0.2",       # 负值
    "0.1,1.2",        # hi ≥ 1
    "0.0,0.5",        # lo = 0（开下界无效）
    "0.5,0.5",        # 零宽
    " 0.1 , x ",      # 空白 + 非数值
])
def test_band_invalid_raises(m, monkeypatch, raw):
    monkeypatch.setenv("DSS_SELF_CHECK_BAND", raw)
    with pytest.raises(ValueError):
        m.load_self_check_band()


# ---------------- 交叉对拍机器（DSS_M9_CROSS_*） ----------------

def test_cross_defaults(m):
    assert m.cross_machine_path() == m.ROOT / "scratch" / "02_replay_kw37.py"
    assert m.cross_outdir() == m.ROOT / "output" / "tmp_kw37_review"


def test_cross_env_override(m, monkeypatch):
    monkeypatch.setenv("DSS_M9_CROSS_MACHINE", "F:/x/machine.py")
    monkeypatch.setenv("DSS_M9_CROSS_DIR", "F:/x/out")
    assert m.cross_machine_path() == Path("F:/x/machine.py")
    assert m.cross_outdir() == Path("F:/x/out")


# ---------------- selfcheck_static 吃 band 参数 ----------------

def test_selfcheck_static_band_param(m):
    static = {"shifts": {"a": {"actual": 100, "pred": {5: 110}}}}
    assert m.selfcheck_static(static, (0.0, 1.0)) == pytest.approx(0.10)
    with pytest.raises(SystemExit):
        m.selfcheck_static(static, (0.5, 0.9))
