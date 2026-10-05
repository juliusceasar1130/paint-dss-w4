import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from spike_w1 import common, replay_10_sample_cars as r

conn = common.connect()
df = r.fetch_vehicle_spans(conn, r.DEFAULT_BODIES)
tables = r.load_tables(conn)
rows = []
for body, g in df.groupby('body', sort=False):
    bt = g['bt'].iloc[0]
    exit_t = g['exit_t'].iloc[0]
    exit_epoch = int(exit_t.timestamp())
    evs = [r.eta.Event(int(row.t.timestamp()), row.station, row.flag) for row in g.itertuples()]
    estimates = r.eta.estimate_vehicle(evs, exit_epoch, tables, bt)
    for est in estimates:
        if est.station == 'L3ACC21IS02':
            t_dt = pd.to_datetime(est.t, unit='s', utc=True).tz_convert('Asia/Shanghai')
            eta_dt = pd.to_datetime(est.eta_direct, unit='s', utc=True).tz_convert('Asia/Shanghai') if est.eta_direct else None
            err_min = (est.eta_direct - exit_epoch)/60.0 if est.eta_direct else None
            rows.append({
                'body': body, 'bt': bt, 'flag': est.flag,
                'pass_t': t_dt.strftime('%m-%d %H:%M'),
                'exit_t': exit_t.strftime('%m-%d %H:%M'),
                'actual_rem_h': round(est.remaining_actual/3600.0, 2),
                'eta': eta_dt.strftime('%m-%d %H:%M') if pd.notna(eta_dt) else 'N/A',
                'err_min': round(err_min, 1) if err_min is not None else None,
                'abs_err_min': round(abs(err_min), 1) if err_min is not None else None
            })
res = pd.DataFrame(rows).sort_values('pass_t')
print(res.to_string(index=False))
