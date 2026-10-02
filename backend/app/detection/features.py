"""Feature engineering shared by training, live detection and evaluation.

Normal HVAC data changes a lot during the day (night vs. office hours, start-up at
07:00, hot afternoons). A raw threshold would fire every morning. So each signal
is compared with what is NORMAL FOR THIS DEVICE AT THIS TIME OF DAY:

    z = (value - mean[device, 15-min slot]) / std[device, 15-min slot]

The baseline (mean, std) is learned from normal training data only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Signals compared with the baseline. coil_dt_c (zone - supply) is derived.
SIGNALS = [
    "zone_temp_c",
    "supply_temp_c",
    "humidity_pct",
    "airflow_cfm",
    "power_kw",
    "co2_ppm",
    "coil_dt_c",
]

# Signals judged by the z-score method. Supply temperature and coil temperature drop
# are controller OUTPUTS that swing whenever occupancy changes, so they are left to
# the Isolation Forest (which sees them together with power and airflow).
Z_SIGNALS = ["zone_temp_c", "humidity_pct", "airflow_cfm", "power_kw", "co2_ppm"]

# Minimum std per signal, so a very stable slot does not turn tiny noise into a huge z.
STD_FLOOR = {
    "zone_temp_c": 0.30,
    "supply_temp_c": 0.60,
    "humidity_pct": 2.0,
    "airflow_cfm": 60.0,
    "power_kw": 0.40,
    "co2_ppm": 40.0,
    "coil_dt_c": 0.60,
}

SLOT_MIN = 15          # baseline resolution (minutes)
STARTUP_ROWS = 80      # minutes after switch-on while the room is still being pulled down
SMOOTH_ROWS = 3        # rolling mean before computing z (reduces sensor noise)
SLOPE_ROWS = 10        # zone temperature change over the last 10 readings
FLAT_ROWS = 10         # rolling std window used to spot a frozen sensor
Z_CLIP = 25.0

# Signals not judged while the unit is OFF (power and airflow still are).
OFF_IGNORED = ["zone_temp_c", "supply_temp_c", "humidity_pct", "co2_ppm", "coil_dt_c"]
# Not judged during the start-up pull-down either (power depends on how hot the morning is).
STARTUP_IGNORED = OFF_IGNORED + ["power_kw"]
MIN_Z_THRESHOLD = 3.0  # never alarm on less than 3 standard deviations

FEATURE_COLUMNS =[f"z_{s}" for s in SIGNALS] + ["temp_slope_10", "temp_logstd_10"]


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Parse time, sort, add derived columns. Works on one or many devices."""
    out = df.copy()
    if not pd.api.types.is_datetime64_any_dtype(out["ts"]):
        out["ts"] = pd.to_datetime(out["ts"], utc=True)
    if getattr(out["ts"].dt, "tz", None) is not None:
        out["ts"] = out["ts"].dt.tz_localize(None)
    out = out.sort_values(["device_id", "ts"]).reset_index(drop=True)
    out["coil_dt_c"] = out["zone_temp_c"] - out["supply_temp_c"]
    minute = out["ts"].dt.hour * 60 + out["ts"].dt.minute
    out["minute_of_day"] = minute
    out["slot"] = (minute // SLOT_MIN).astype(int)
    return out


def smooth(df: pd.DataFrame) -> pd.DataFrame:
    """Rolling mean per device (expects prepare() output)."""
    g = df.groupby("device_id", sort=False)[SIGNALS]
    sm = g.rolling(SMOOTH_ROWS, min_periods=1).mean().reset_index(level=0, drop=True)
    return sm.sort_index()


def fit_baseline(train: pd.DataFrame) -> pd.DataFrame:
    """Mean and std of each smoothed signal per (device, slot) on normal data."""
    df = prepare(train)
    sm = smooth(df)
    sm[["device_id", "slot"]] = df[["device_id", "slot"]]
    agg = sm.groupby(["device_id", "slot"])[SIGNALS].agg(["mean", "std"])
    agg.columns = [f"{s}_{stat}" for s, stat in agg.columns]
    for s in SIGNALS:
        agg[f"{s}_std"] = agg[f"{s}_std"].fillna(0).clip(lower=STD_FLOOR[s])
    return agg


def nominal_airflow_ratio(train: pd.DataFrame) -> dict[str, float]:
    """Median CFM per % of fan speed while running: used by the low-airflow rule."""
    on = train[(train["status"] == "ON") & (train["fan_speed_pct"] > 0)]
    ratio = on["airflow_cfm"] / on["fan_speed_pct"]
    return ratio.groupby(on["device_id"]).median().round(2).to_dict()


def compute_features(df: pd.DataFrame, baseline: pd.DataFrame,
                     minutes_on_last: dict[str, int] | None = None) -> pd.DataFrame:
    """Return prepare()d rows plus z-scores, baselines and extra features.

    `df` may hold one or many devices. For live use pass the last ~60 readings
    of a device; the result for the last row equals the batch result.
    """
    d = prepare(df)
    sm = smooth(d)
    keys = pd.MultiIndex.from_frame(d[["device_id", "slot"]])
    base = baseline.reindex(keys)
    base.index = d.index
    for s in SIGNALS:
        mean, std = base[f"{s}_mean"], base[f"{s}_std"]
        d[f"base_{s}"] = mean
        z = (sm[s] - mean) / std
        d[f"z_{s}"] = z.fillna(0.0).clip(-Z_CLIP, Z_CLIP)

    # When the unit is OFF nothing controls the room: it simply drifts with the
    # weather (all zones together). Right after switch-on the room is still being
    # pulled down from that weather-dependent temperature. In both cases only check
    # power and airflow (is the unit really off / running normally?).
    is_on = (d["status"] == "ON").astype(int)
    run_id = (is_on.groupby(d["device_id"], sort=False).diff().fillna(0) != 0).groupby(d["device_id"], sort=False).cumsum()
    d["minutes_on"] = is_on.groupby([d["device_id"], run_id], sort=False).cumsum()
    if minutes_on_last:
        # live mode: the buffer is short, so the caller tells us how long each unit
        # has really been running at its latest reading
        last_idx = d.groupby("device_id", sort=False).tail(1).index
        for i in last_idx:
            dev = d.at[i, "device_id"]
            if dev in minutes_on_last:
                d.at[i, "minutes_on"] = minutes_on_last[dev]
    off = (is_on == 0).to_numpy()
    startup = ((is_on == 1) & (d["minutes_on"] <= STARTUP_ROWS)).to_numpy()
    for s in OFF_IGNORED:
        d.loc[off, f"z_{s}"] = 0.0
    for s in STARTUP_IGNORED:
        d.loc[startup, f"z_{s}"] = 0.0

    g = d.groupby("device_id", sort=False)["zone_temp_c"]
    d["temp_slope_10"] = (d["zone_temp_c"] - g.shift(SLOPE_ROWS)).fillna(0.0)
    roll_std = g.rolling(FLAT_ROWS, min_periods=FLAT_ROWS).std().reset_index(level=0, drop=True).sort_index()
    d["temp_std_10"] = roll_std
    d["temp_logstd_10"] = np.log10(roll_std.fillna(0.1) + 1e-3)
    return d
