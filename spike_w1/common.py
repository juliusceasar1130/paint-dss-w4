# -*- coding: utf-8 -*-
"""DB 连接与口径常量（W1 单一事实源，值见 BM 笔记 2026-09-19 拍板记录）。

M5 准备（2026-09-29 agy 审核阻断 F1）：DSN 支持 DSS_DB_* 环境变量覆盖
（.env 由本模块 load_dotenv 加载）——默认值 = 开发库 127.0.0.1/root/root 不变，
内网生产库用 DSS_DB_HOST/PORT/NAME/USER/PASSWORD 覆盖。"""
import os

import psycopg2

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:      # dotenv 缺失时退化为纯 os.environ（开发环境已固化，不会发生）
    pass


def _dsn() -> dict:
    """DSN 解析（纯函数，可单测）：DSS_DB_* 覆盖 > 开发库默认。"""
    return dict(
        host=os.environ.get("DSS_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DSS_DB_PORT", "5432")),
        dbname=os.environ.get("DSS_DB_NAME", "analytics_db"),
        user=os.environ.get("DSS_DB_USER", "root"),
        password=os.environ.get("DSS_DB_PASSWORD", "root"))


DSN = _dsn()

# 3 上线点（2026-09-19 用户提供 + 数据验证：98.9% 车恰经其一）
ENTRY_STATIONS = ("01IS205", "1L360RB", "01IS045")
# 4 下线点（= dim_vehicle_profile.retention_checkpoint，2026-09-19 确认）
EXIT_STATIONS = ("K3IS140", "1J440RB", "K2IS075", "K1IS135")

# MDS_DATA 第 139 位（1-based）返修/黑顶标志
FLAG_NORMAL, FLAG_REWORK, FLAG_BLACKTOP = "0", "1", "2"


def connect():
    """analytics_db 连接（psycopg2.extensions.connection）：autocommit + 会话时区 Asia/Shanghai（工厂时 = UTC+8，必须）。
    每次调用现解析 _dsn()（DSS_DB_* 运行时生效，不冻结在模块导入时）。"""
    conn = psycopg2.connect(**_dsn())
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Asia/Shanghai'")
    return conn
