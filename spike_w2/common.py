# -*- coding: utf-8 -*-
"""W2 基座：再导出 W1 common（DSN/connect/上下线点/flag 常量），保证 W1/W2 口径单一来源。
班次窗口常量在 spike_w2.shifts（2026-09-19 拍板 + 细化，见 BM 笔记）。
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spike_w1.common import DSN, connect, ENTRY_STATIONS, EXIT_STATIONS  # noqa: F401
from spike_w1.common import FLAG_NORMAL, FLAG_REWORK, FLAG_BLACKTOP      # noqa: F401
