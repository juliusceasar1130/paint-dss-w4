# -*- coding: utf-8 -*-
"""DB 连接与口径常量（W1 单一事实源，值见 BM 笔记 2026-09-19 拍板记录）。"""
import psycopg2

DSN = dict(host="127.0.0.1", port=5432, dbname="analytics_db",
           user="root", password="root")

# 3 上线点（2026-09-19 用户提供 + 数据验证：98.9% 车恰经其一）
ENTRY_STATIONS = ("01IS205", "1L360RB", "01IS045")
# 4 下线点（= dim_vehicle_profile.retention_checkpoint，2026-09-19 确认）
EXIT_STATIONS = ("K3IS140", "1J440RB", "K2IS075", "K1IS135")

# MDS_DATA 第 139 位（1-based）返修/黑顶标志
FLAG_NORMAL, FLAG_REWORK, FLAG_BLACKTOP = "0", "1", "2"


def connect():
    """analytics_db 连接（psycopg2.extensions.connection）：autocommit + 会话时区 Asia/Shanghai（工厂时 = UTC+8，必须）。"""
    conn = psycopg2.connect(**DSN)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("SET TIME ZONE 'Asia/Shanghai'")
    return conn
