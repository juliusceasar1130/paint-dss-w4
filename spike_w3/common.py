# -*- coding: utf-8 -*-
"""T2.0 地基：env 加载（python-dotenv + DSS_* 汇总）+ 跨包再导出（无副作用）。

跨包加载纪律（docs/03 §0 接口契约）：
- spike_w1/common.py 与 spike_w2 下文件裸 import 会撞名 → importlib 按路径加载并改名别名
  （w1_common / w2_shifts），加载后必须注册 sys.modules（spec_from_file_location 不自动
  注册，缺注册会在按类名反查/序列化时炸）。
- 单 DSN + SET TIME ZONE 'Asia/Shanghai'（L3，勿另开连接逻辑）：connect / ENTRY_STATIONS /
  EXIT_STATIONS 一律再导出，不复制值。
- 班次窗口常量只存在于 spike_w2/shifts.py（全局约束 1，B1 拍板）：shifts 全套再导出，
  W3 代码里禁止复制窗口常量。
"""
from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

_ROOT = Path(__file__).resolve().parent.parent


def _load_module(alias: str, relpath: str):
    """按路径加载模块并以 alias 注册进 sys.modules（跨包撞名隔离，见模块 docstring）。"""
    spec = importlib.util.spec_from_file_location(alias, _ROOT / relpath)
    m = importlib.util.module_from_spec(spec)
    sys.modules[alias] = m
    spec.loader.exec_module(m)
    return m


_w1_common = _load_module("w1_common", "spike_w1/common.py")
_w2_shifts = _load_module("w2_shifts", "spike_w2/shifts.py")

# --- 再导出：连接与口径常量（勿复制值） ---
connect = _w1_common.connect
ENTRY_STATIONS = _w1_common.ENTRY_STATIONS
EXIT_STATIONS = _w1_common.EXIT_STATIONS

# --- W3 专有口径常量（勿从 W1 复制；W1 底表已 SQL 截断，无出站延续站概念） ---
# 出站延续站（连廊/总装交接记录站，2026-09-24 幽灵车踩坑：T2.2 干跑 09IS360 暴露 +
# 全库 16 万行按车时间流验证 + agy 交叉核实）：K3IS140→K3IS230→09IS360 / K2IS075→K2IS165→09IS360 /
# K1IS135→K1IS225→09IS350，出站后跟随率 99.5–100%；1J440RB 无延续（终点）。R4 双保险：
# 不在池仅审计不收养（静默，不计 exits——一次物理出线过多个延续站）、在池同出站站池移除。
# 启动闸门：stream_pipeline.derive_postexit_stations 数据驱动重推比对（不一致只告警不自动改）。
POST_EXIT_STATIONS = ("09IS360", "09IS350", "K3IS230", "K2IS165", "K1IS225")
FLAG_NORMAL, FLAG_REWORK, FLAG_BLACKTOP = (
    _w1_common.FLAG_NORMAL, _w1_common.FLAG_REWORK, _w1_common.FLAG_BLACKTOP,
)

# --- 再导出：shifts 全套（委托窗口映射，勿复制窗口常量） ---
CST = _w2_shifts.CST
DAY_TYPES = _w2_shifts.DAY_TYPES
MID_END_TIMES = _w2_shifts.MID_END_TIMES
Shift = _w2_shifts.Shift
detect_day_type = _w2_shifts.detect_day_type
detect_mid_type = _w2_shifts.detect_mid_type
normalize_shift_type = _w2_shifts.normalize_shift_type
shift_windows = _w2_shifts.shift_windows
anchors = _w2_shifts.anchors
gate_anchor = _w2_shifts.gate_anchor
is_complete = _w2_shifts.is_complete
iso_week_label = _w2_shifts.iso_week_label
week_start = _w2_shifts.week_start


def _env_str(key: str, default: str) -> str:
    v = os.environ.get(key)
    return v if v not in (None, "") else default


def _env_int(key: str, default: int) -> int:
    v = os.environ.get(key)
    return int(v) if v not in (None, "") else default


def _env_float(key: str, default: float) -> float:
    v = os.environ.get(key)
    return float(v) if v not in (None, "") else default


def _env_float_opt(key: str):
    """未设置/空串 → None（供两值绑定判断「是否显式配置」）。"""
    v = os.environ.get(key)
    return float(v) if v not in (None, "") else None


@dataclass(frozen=True)
class DSSConfig:
    """DSS_* 参数汇总（python-dotenv 加载根 .env，缺失用默认；启动报告打印全部生效值）。"""
    # 系统驱动模式
    clock_mode: str = "wall_clock"        # wall_clock | replay_cursor
    poll_interval_sec: int = 300
    # B3 护栏 / M4 / M7（T2 生效）
    stale_min: int = 30                   # B3 熔断
    lag_min: int = 15                     # 数据延迟观察态
    skew_window_min: int = 15             # M4 迟到事件窗
    dead_vehicle_hours: float = 120.0     # M7 死车
    bootstrap_hours: float = 120.0        # 未显式配置时 = dead_vehicle_hours（两值绑定，B4 点 7）
    rebuild_hours: float = 24.0           # 主循环周期全量重建间隔（F-1 丢行/水位中毒自愈上限，T2.6）
    # Task 1 周计划（T1 生效）
    plan_folder: str = "plan_inbox"
    # Task 3 α（T3 再启用，此处提前载入；迟滞 0.05/2 周期为代码级常量，不入 env）
    alpha_mode: str = "off"            # M9 门基线/生产默认（2026-09-25 拍板：off；on = 实验开关，B5 原口径）
    alpha_window_hours: float = 3.0
    alpha_min_samples: int = 30
    alpha_ema_beta: float = 0.3
    alpha_clamp_min: float = 0.6
    alpha_clamp_max: float = 1.5
    # Task 3（T3.0 新增，docs/04 §2）
    eta_stale_hours: float = 6.0       # L4 stale：last_event 超此时长仅标记仍计 pred（静态无此概念，保构造性一致）
    learn_win_start: str = "2026-04-14"  # α 基准学习窗起（= W1 学习窗，纯历史、无未来泄漏）
    learn_win_end: str = "2026-08-15"    # α 基准学习窗止
    # Task 4 红黄绿告警（T4 再启用）
    alert_red: float = -0.10
    alert_yellow: float = -0.05
    alert_debounce_min: int = 10
    # Task 5 停线感知（T5 再启用）
    line_stop_min: int = 30
    observe_min: int = 15


def _env_alpha_mode() -> str:
    v = _env_str("DSS_ALPHA_MODE", "off").lower()
    if v not in ("on", "off"):
        raise ValueError(f"DSS_ALPHA_MODE 必须为 on/off，当前: {v!r}")
    return v


def load_config() -> DSSConfig:
    """加载根 .env（不存在不报错）并生成 DSSConfig。

    两值绑定（B4 点 7）：DSS_BOOTSTRAP_HOURS 未显式配置 → 取 DSS_DEAD_VEHICLE_HOURS 值。
    """
    load_dotenv(_ROOT / ".env")
    dead = _env_float("DSS_DEAD_VEHICLE_HOURS", 120.0)
    bootstrap = _env_float_opt("DSS_BOOTSTRAP_HOURS")
    if bootstrap is None:
        bootstrap = dead
    return DSSConfig(
        clock_mode=_env_str("DSS_CLOCK_MODE", "wall_clock"),
        poll_interval_sec=_env_int("DSS_POLL_INTERVAL_SEC", 300),
        stale_min=_env_int("DSS_STALE_MIN", 30),
        lag_min=_env_int("DSS_LAG_MIN", 15),
        skew_window_min=_env_int("DSS_SKEW_WINDOW_MIN", 15),
        dead_vehicle_hours=dead,
        bootstrap_hours=bootstrap,
        rebuild_hours=_env_float("DSS_REBUILD_HOURS", 24.0),
        plan_folder=_env_str("DSS_PLAN_FOLDER", "plan_inbox"),
        alpha_mode=_env_alpha_mode(),
        alpha_window_hours=_env_float("DSS_ALPHA_WINDOW_HOURS", 3.0),
        alpha_min_samples=_env_int("DSS_ALPHA_MIN_SAMPLES", 30),
        alpha_ema_beta=_env_float("DSS_ALPHA_EMA_BETA", 0.3),
        alpha_clamp_min=_env_float("DSS_ALPHA_CLAMP_MIN", 0.6),
        alpha_clamp_max=_env_float("DSS_ALPHA_CLAMP_MAX", 1.5),
        eta_stale_hours=_env_float("DSS_ETA_STALE_HOURS", 6.0),
        learn_win_start=_env_str("DSS_LEARN_WIN_START", "2026-04-14"),
        learn_win_end=_env_str("DSS_LEARN_WIN_END", "2026-08-15"),
        alert_red=_env_float("DSS_ALERT_RED", -0.10),
        alert_yellow=_env_float("DSS_ALERT_YELLOW", -0.05),
        alert_debounce_min=_env_int("DSS_ALERT_DEBOUNCE_MIN", 10),
        line_stop_min=_env_int("DSS_LINE_STOP_MIN", 30),
        observe_min=_env_int("DSS_OBSERVE_MIN", 15),
    )
