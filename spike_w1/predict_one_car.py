# -*- coding: utf-8 -*-
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from spike_w1 import common, eta, replay_10_sample_cars as r

conn = common.connect()
body = "78202664007604"
df = r.fetch_vehicle_spans(conn, [body])
tables = r.load_tables(conn)

bt = df["bt"].iloc[0]
entry_t = df["entry_t"].iloc[0]
exit_t = df["exit_t"].iloc[0]
exit_epoch = int(exit_t.timestamp())

evs = [r.eta.Event(int(row.t.timestamp()), row.station, row.flag) for row in df.itertuples()]
estimates = r.eta.estimate_vehicle(evs, exit_epoch, tables, bt)

records = []
for i, est in enumerate(estimates):
    t_dt = pd.to_datetime(est.t, unit="s", utc=True).tz_convert("Asia/Shanghai")
    eta_direct_dt = pd.to_datetime(est.eta_direct, unit="s", utc=True).tz_convert("Asia/Shanghai") if est.eta_direct else None
    eta_med_dt = pd.to_datetime(est.eta_sum_median, unit="s", utc=True).tz_convert("Asia/Shanghai") if est.eta_sum_median else None
    eta_mean_dt = pd.to_datetime(est.eta_sum_mean, unit="s", utc=True).tz_convert("Asia/Shanghai") if est.eta_sum_mean else None

    err_direct = (est.eta_direct - exit_epoch) if est.eta_direct else None
    err_med = (est.eta_sum_median - exit_epoch) if est.eta_sum_median else None
    err_mean = (est.eta_sum_mean - exit_epoch) if est.eta_sum_mean else None

    records.append({
        "step": i + 1,
        "station": est.station,
        "flag": est.flag,
        "t": t_dt,
        "rem_actual_s": est.remaining_actual,
        "rem_actual_h": est.remaining_actual / 3600.0,
        "eta_direct": eta_direct_dt,
        "err_direct_m": err_direct / 60.0 if err_direct else None,
        "err_direct_h": err_direct / 3600.0 if err_direct else None,
        "eta_med": eta_med_dt,
        "err_med_m": err_med / 60.0 if err_med else None,
        "eta_mean": eta_mean_dt,
        "err_mean_m": err_mean / 60.0 if err_mean else None,
    })

res = pd.DataFrame(records)

# 打印基本信息
print(f"Vehicle: {body} ({bt})")
print(f"Entry: {entry_t} @ {df.iloc[0]['station']}")
print(f"Exit:  {exit_t} @ {df.iloc[-1]['station']}")
print(f"Total Duration: {(exit_t - entry_t).total_seconds()/3600.0:.2f} h")
print(f"Total events: {len(res)}")

# 查找 L3ACC21IS02
l3 = res[res["station"] == "L3ACC21IS02"]
if not l3.empty:
    row = l3.iloc[0]
    print("\n--- At Station L3ACC21IS02 ---")
    print(f"Step: {row['step']}")
    print(f"Pass Time: {row['t']}")
    print(f"Actual Remaining: {row['rem_actual_h']:.2f} h ({row['rem_actual_s']} s)")
    print(f"Predicted ETA (Direct): {row['eta_direct']}")
    print(f"Error (Direct): {row['err_direct_h']:+.2f} h ({row['err_direct_m']:+.1f} min)")
    print(f"Predicted ETA (Sum Median): {row['eta_med']} (Error: {row['err_med_m']:+.1f} min)")
    print(f"Predicted ETA (Sum Mean):   {row['eta_mean']} (Error: {row['err_mean_m']:+.1f} min)")

# 打印前后 5 个工位的收敛轨迹
idx = l3.index[0]
start_i = max(0, idx - 3)
print("\n--- Trajectory from L3ACC21IS02 to exit ---")
sub = res.iloc[start_i:][["step", "station", "flag", "t", "rem_actual_h", "eta_direct", "err_direct_m", "eta_med", "err_med_m"]]
print(sub.to_string(index=False))
