"""Input validation: every MQTT reading is checked before it is stored or analysed.

A broken sensor or a bad message must not reach the detectors (a temperature of
999 °C would look like the worst fault ever) and must not crash ingestion.
Rejected readings are counted and kept with the reason, so the dashboard can
show them (GET /ingestion/rejected).

Rules follow contracts/telemetry.schema.json, plus physical limits: values a real
AHU sensor cannot report are rejected, not "detected".
"""
from __future__ import annotations

import math
import re
from collections import deque
from datetime import datetime, timezone

REQUIRED_NUMBERS = ["zone_temp_c", "supply_temp_c", "setpoint_c", "humidity_pct", "airflow_cfm",
                    "fan_speed_pct", "power_kw", "occupancy", "co2_ppm"]
# physically possible ranges (wider than normal operation: faults stay inside them)
LIMITS = {
    "zone_temp_c": (-10.0, 60.0), "supply_temp_c": (-10.0, 60.0), "setpoint_c": (10.0, 35.0),
    "humidity_pct": (0.0, 100.0), "airflow_cfm": (0.0, 10000.0), "fan_speed_pct": (0.0, 100.0),
    "power_kw": (0.0, 100.0), "occupancy": (0, 1000), "co2_ppm": (0.0, 10000.0),
    "outdoor_temp_c": (-40.0, 60.0),
}
DEVICE_RE = re.compile(r"^AHU-[0-9]+$")


def validate_reading(r) -> str | None:
    """None if the reading is usable, otherwise a short reason a person can read."""
    if not isinstance(r, dict):
        return "message is not a JSON object"
    dev = r.get("device_id")
    if not isinstance(dev, str) or not DEVICE_RE.match(dev):
        return f"invalid device_id {dev!r}"
    ts = r.get("ts")
    if not isinstance(ts, str):
        return "missing timestamp"
    try:
        datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return f"unreadable timestamp {ts!r}"
    if r.get("status") not in ("ON", "OFF"):
        return f"status must be ON or OFF, got {r.get('status')!r}"
    missing = [k for k in REQUIRED_NUMBERS if k not in r or r[k] is None]
    if missing:
        return f"missing {', '.join(missing)}"
    for k, (lo, hi) in LIMITS.items():
        if k not in r or r[k] is None:
            continue
        v = r[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return f"{k} is not a number ({v!r})"
        if math.isnan(v) or math.isinf(v):
            return f"{k} is not a finite number"
        if not lo <= v <= hi:
            return f"{k} = {v} is outside the possible range {lo:g} to {hi:g}"
    return None


class RejectLog:
    """Counts rejected messages and keeps the last few for the dashboard."""

    def __init__(self, keep: int = 50):
        self.count = 0
        self.recent: deque[dict] = deque(maxlen=keep)

    def add(self, reason: str, reading=None, topic_device: str | None = None,
            building_time: str | None = None) -> dict:
        self.count += 1
        dev = reading.get("device_id") if isinstance(reading, dict) else None
        if not isinstance(dev, str) or not DEVICE_RE.match(dev):
            dev = topic_device if topic_device and DEVICE_RE.match(topic_device) else None
        entry = {"received_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 "device_id": dev,
                 "ts": reading.get("ts") if isinstance(reading, dict) else None,
                 "building_time": building_time,   # simulated clock when it arrived (for the dashboard)
                 "reason": reason}
        self.recent.appendleft(entry)
        return entry

    def summary(self) -> dict:
        return {"count": self.count, "recent": list(self.recent)}
