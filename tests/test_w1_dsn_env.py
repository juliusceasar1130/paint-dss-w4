# -*- coding: utf-8 -*-
"""M5 准备（agy 阻断 F1）：spike_w1.common DSN 支持 DSS_DB_* 环境变量覆盖。

默认值 = 开发库 127.0.0.1/root/root（零行为变化）；内网生产库用
DSS_DB_HOST/PORT/NAME/USER/PASSWORD 覆盖。connect() 每次现解析（不冻结在导入时）。
"""
from __future__ import annotations

import os

from spike_w1 import common as w1c


def test_dsn_defaults():
    for k in ("DSS_DB_HOST", "DSS_DB_PORT", "DSS_DB_NAME", "DSS_DB_USER", "DSS_DB_PASSWORD"):
        os.environ.pop(k, None)
    d = w1c._dsn()
    assert d == dict(host="127.0.0.1", port=5432, dbname="analytics_db",
                     user="root", password="root")


def test_dsn_env_override(monkeypatch):
    monkeypatch.setenv("DSS_DB_HOST", "db.prod.intranet")
    monkeypatch.setenv("DSS_DB_PORT", "5433")
    monkeypatch.setenv("DSS_DB_NAME", "analytics_prod")
    monkeypatch.setenv("DSS_DB_USER", "dss_ro")
    monkeypatch.setenv("DSS_DB_PASSWORD", "s3cret")
    d = w1c._dsn()
    assert d == dict(host="db.prod.intranet", port=5433, dbname="analytics_prod",
                     user="dss_ro", password="s3cret")


def test_dsn_port_is_int(monkeypatch):
    monkeypatch.setenv("DSS_DB_PORT", "6432")
    assert w1c._dsn()["port"] == 6432


def test_connect_uses_fresh_dsn(monkeypatch):
    """connect() 现解析 _dsn（不真连库：monkeypatch psycopg2.connect 捕获 kwargs）。"""
    import psycopg2
    captured = {}

    def _fake_connect(**kw):
        captured.update(kw)
        class _Conn:
            autocommit = False
            def cursor(self):
                class _Cur:
                    def __enter__(self): return self
                    def __exit__(self, *a): pass
                    def execute(self, sql): pass
                return _Cur()
        return _Conn()

    monkeypatch.setenv("DSS_DB_HOST", "db.prod.intranet")
    monkeypatch.setattr(psycopg2, "connect", _fake_connect)
    w1c.connect()
    assert captured.get("host") == "db.prod.intranet"      # 运行时 env 生效（非导入时冻结）
