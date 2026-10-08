r"""D0 快查（内网部署机用）：① DB 连通 + ETL 活性  ② dss 表盘点。

用法（在 D:\dss 下）：
    D:\dss\env\python.exe d0_check.py

判读：
- ETL 时差 < 5min = 正常；>30min = 别开跑，找内网数据负责人
- 学完表后再跑一次：13 必填表应全在（dim_* / v2_* / dim_expected_delay / dim_weekly_plan 启动时自动建）
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "spike_w1"))
from common import connect  # noqa: E402

conn = connect()
cur = conn.cursor()
cur.execute("SET TIME ZONE 'Asia/Shanghai'")
cur.execute('SELECT MAX("DATE_EVT") FROM ods.carbody_history')
mx = cur.fetchone()[0]
now = datetime.now(timezone(timedelta(hours=8)))
lag = now - mx
print(f"ETL: MAX(DATE_EVT)={mx}  时差={lag}")
print("      判读: <5min 正常 / >30min 别开跑，找数据负责人")
cur.execute(
    "SELECT table_name FROM information_schema.tables "
    "WHERE table_schema='dss' ORDER BY 1")
tables = [r[0] for r in cur.fetchall()]
print(f"dss 表（{len(tables)} 张）: {tables}")
conn.close()
