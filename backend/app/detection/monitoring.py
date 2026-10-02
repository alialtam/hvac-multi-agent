"""Monitoring agent: fast, explainable rules on the live telemetry.

The rules catch obvious problems that a human operator would recognise at once,
and problems the ML models are weak at (a frozen sensor, a unit running after
hours). Each rule has a short persistence window so one noisy reading never fires.

Rule names are part of the anomaly-event contract (`rule_hits`).
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import pandas as pd

RULES = [
    "zone_too_warm",       # occupied zone more than 1.5 C above setpoint for 5 min
    "fast_temp_rise",      # zone warmed more than 1.0 C in 10 min while the unit runs
    "low_airflow",         # airflow per % fan speed below 80% of normal for 3 min
    "sensor_flatline",     # zone temperature identical for 10 readings
    "running_unoccupied",  # unit ON outside the schedule for 15 min
]


@dataclass
class MonitoringConfig:
    schedule_on: str = "07:00"
    schedule_off: str = "18:30"
    warm_margin_c: float = 1.5
    warm_rows: int = 5
    rise_c: float = 1.0
    rise_rows: int = 10
    low_airflow_ratio: float = 0.80
    low_airflow_rows: int = 3
    flat_std: float = 0.005
    flat_rows: int = 10
    unoccupied_rows: int = 15


def _hhmm(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _sustained(flag: pd.Series, device: pd.Series, rows: int) -> pd.Series:
    """True when `flag` has been true for the last `rows` readings of the same device."""
    r = flag.astype(float).groupby(device, sort=False).rolling(rows, min_periods=rows).min()
    return r.reset_index(level=0, drop=True).sort_index().fillna(0).astype(bool)


class MonitoringAgent:
    def __init__(self, nominal_ratio: dict[str, float], config: MonitoringConfig | None = None):
        self.nominal_ratio = nominal_ratio
        self.cfg = config or MonitoringConfig()

    def apply_rules(self, d: pd.DataFrame) -> pd.DataFrame:
        """`d` is compute_features() output. Returns one boolean column per rule."""
        c = self.cfg
        dev = d["device_id"]
        on = d["status"] == "ON"
        out = pd.DataFrame(index=d.index)

        warm = on & (d["occupancy"] > 0) & (d["zone_temp_c"] > d["setpoint_c"] + c.warm_margin_c)
        out["zone_too_warm"] = _sustained(warm, dev, c.warm_rows)

        rise = d["zone_temp_c"] - d.groupby(dev, sort=False)["zone_temp_c"].shift(c.rise_rows)
        out["fast_temp_rise"] = on & (rise > c.rise_c) & _sustained(on, dev, c.rise_rows)

        nominal = dev.map(self.nominal_ratio).astype(float)
        ratio = d["airflow_cfm"] / d["fan_speed_pct"].where(d["fan_speed_pct"] > 0)
        low = on & (ratio < c.low_airflow_ratio * nominal)
        out["low_airflow"] = _sustained(low.fillna(False), dev, c.low_airflow_rows)

        flat_std = d["zone_temp_c"].groupby(dev, sort=False).rolling(c.flat_rows, min_periods=c.flat_rows).std()
        flat_std = flat_std.reset_index(level=0, drop=True).sort_index()
        out["sensor_flatline"] = (flat_std < c.flat_std).fillna(False)

        m = d["minute_of_day"]
        outside = (m < _hhmm(c.schedule_on)) | (m >= _hhmm(c.schedule_off))
        out["running_unoccupied"] = _sustained(on & outside & (d["occupancy"] == 0), dev, c.unoccupied_rows)
        return out[RULES]


class OfflineWatcher:
    """Marks a device offline when no reading arrived for `timeout_s` real seconds."""

    def __init__(self, timeout_s: float = 30.0):
        self.timeout_s = timeout_s
        self.last_seen: dict[str, float] = {}
        self.offline: set[str] = set()

    def seen(self, device_id: str) -> bool:
        """Record a reading. Returns True if the device just came back online."""
        self.last_seen[device_id] = time.monotonic()
        if device_id in self.offline:
            self.offline.discard(device_id)
            return True
        return False

    def check(self) -> list[str]:
        """Return devices that have JUST gone offline."""
        now = time.monotonic()
        newly = [d for d, t in self.last_seen.items()
                 if now - t > self.timeout_s and d not in self.offline]
        self.offline.update(newly)
        return newly
