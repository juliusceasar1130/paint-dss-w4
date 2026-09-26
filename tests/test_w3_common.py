# -*- coding: utf-8 -*-
"""T2.0 地基：DSSConfig（env 加载 + 两值绑定）+ 跨包再导出（无副作用）。

全离线：不连库、不依赖 .env 存在（根目录当前无 .env，load_dotenv no-op）。
钉死：默认值 / bootstrap_hours 两值绑定（B4 点 7）/ env 覆盖 / 站点常量再导出 /
shifts 行为再导出（委托窗口映射、不复制常量）/ connect 再导出（L3 单 DSN + 时区）。
"""
import spike_w3.common as common
from spike_w3.common import DSSConfig
from datetime import date


def test_defaults():
    c = DSSConfig()
    assert (c.stale_min, c.skew_window_min, c.lag_min) == (30, 15, 15)
    assert c.dead_vehicle_hours == 120 and c.bootstrap_hours == 120
    assert c.clock_mode == "wall_clock" and c.plan_folder == "plan_inbox"
    assert c.poll_interval_sec == 300
    # T3/T4/T5 变量提前载入（默认值与 .env.example 一致）
    assert (c.alpha_window_hours, c.alpha_min_samples, c.alpha_ema_beta) == (3.0, 30, 0.3)
    assert (c.alpha_clamp_min, c.alpha_clamp_max) == (0.6, 1.5)
    assert (c.alert_red, c.alert_yellow, c.alert_debounce_min) == (-0.10, -0.05, 10)
    assert (c.line_stop_min, c.observe_min) == (30, 15)
    # 2026-09-25 拍板：M9 门基线 / 生产默认 α off（agy 审核轮 3 同推荐）
    assert c.alpha_mode == "off"


def test_bootstrap_bound_to_dead(monkeypatch):
    # 两值绑定（B4 点 7）：只改 dead、未设 bootstrap → bootstrap 跟随 dead
    monkeypatch.setenv("DSS_DEAD_VEHICLE_HOURS", "60")
    monkeypatch.delenv("DSS_BOOTSTRAP_HOURS", raising=False)
    c = common.load_config()
    assert c.dead_vehicle_hours == 60 and c.bootstrap_hours == 60
    # 显式设置优先
    monkeypatch.setenv("DSS_BOOTSTRAP_HOURS", "90")
    assert common.load_config().bootstrap_hours == 90


def test_env_override(monkeypatch):
    monkeypatch.setenv("DSS_STALE_MIN", "45")
    monkeypatch.setenv("DSS_CLOCK_MODE", "replay_cursor")
    c = common.load_config()
    assert c.stale_min == 45 and c.clock_mode == "replay_cursor"


def test_t3_new_defaults():
    # T3.0（docs/04 §2）：+3 参数 19→22（L4 stale 阈值 / α 基准学习窗 起、止，W1 同窗）
    c = DSSConfig()
    assert c.eta_stale_hours == 6.0
    assert c.learn_win_start == "2026-04-14"
    assert c.learn_win_end == "2026-08-15"


def test_t3_env_override(monkeypatch):
    monkeypatch.setenv("DSS_ETA_STALE_HOURS", "8")
    monkeypatch.setenv("DSS_LEARN_WIN_START", "2026-05-01")
    monkeypatch.setenv("DSS_LEARN_WIN_END", "2026-07-01")
    c = common.load_config()
    assert c.eta_stale_hours == 8.0
    assert c.learn_win_start == "2026-05-01"
    assert c.learn_win_end == "2026-07-01"


def test_alpha_mode_env(monkeypatch):
    """DSS_ALPHA_MODE：默认 off；on/off 大小写归一；非法值 ValueError。"""
    monkeypatch.delenv("DSS_ALPHA_MODE", raising=False)
    assert common.load_config().alpha_mode == "off"
    monkeypatch.setenv("DSS_ALPHA_MODE", "ON")
    assert common.load_config().alpha_mode == "on"
    monkeypatch.setenv("DSS_ALPHA_MODE", "banana")
    try:
        common.load_config()
        assert False, "非法 DSS_ALPHA_MODE 应 ValueError"
    except ValueError:
        pass


def test_reexport_station_constants():
    import spike_w1.common as w1c
    # 再导出 = 同值（两目录都有 common.py，跨包加载是独立模块对象，断言值不断身份）
    assert common.ENTRY_STATIONS == w1c.ENTRY_STATIONS
    assert common.EXIT_STATIONS == w1c.EXIT_STATIONS
    assert len(common.ENTRY_STATIONS) == 3 and len(common.EXIT_STATIONS) == 4


def test_reexport_shifts_and_connect():
    # shifts 再导出 = 行为正确（断言唯一计算口径的窗值：日班窗末 16:30 / 10h 中班窗末 03:00）
    got = common.shift_windows(date(2026, 9, 8), day_type="11h", mid_type="10h")
    assert len(got) == 2
    assert got[0].kind == "day" and (got[0].end.hour, got[0].end.minute) == (16, 30)
    assert got[1].kind == "mid" and (got[1].end.hour, got[1].end.day) == (3, 9)
    assert common.normalize_shift_type("11") == "11h"
    assert common.normalize_shift_type("休") is None
    # connect 再导出：单 DSN + 会话时区（L3 勿另开连接逻辑）——__module__ 证再导出而非新写函数
    assert callable(common.connect)
    assert common.connect.__module__ == "w1_common"
