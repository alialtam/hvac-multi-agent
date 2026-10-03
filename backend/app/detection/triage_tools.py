"""Tools the triage agent can call to check an anomaly event.

Each tool is a plain function over the event (and, when available, the live
system). Each returns a small JSON-able dict with a `note`: one plain-English
sentence that the dashboard shows and the LLM reads.

Live use:   TriageTools(event, store=ingestion.store, pipeline=ingestion.pipeline)
Batch use:  TriageTools(event, peers=..., history=...)   (evaluation, tests)
"""
from __future__ import annotations

from typing import Callable

import numpy as np

from .monitoring import MonitoringConfig, _hhmm

TREND_KEYS = ["zone_temp_c", "supply_temp_c", "airflow_cfm", "fan_speed_pct", "power_kw",
              "humidity_pct", "co2_ppm", "occupancy"]
STUCK_KEYS = ["zone_temp_c", "supply_temp_c", "humidity_pct", "airflow_cfm", "co2_ppm"]
RANGES = {"zone_temp_c": (5, 45), "supply_temp_c": (2, 40), "humidity_pct": (0, 100),
          "airflow_cfm": (0, 5000), "power_kw": (0, 30), "co2_ppm": (300, 5000)}
LABEL = {"zone_temp_c": "room temperature", "supply_temp_c": "supply air temperature",
         "airflow_cfm": "airflow", "fan_speed_pct": "fan speed", "power_kw": "power",
         "humidity_pct": "humidity", "co2_ppm": "CO2", "occupancy": "occupancy", "coil_dt_c": "coil temperature drop"}
UNIT = {"zone_temp_c": "°C", "supply_temp_c": "°C", "airflow_cfm": "CFM", "fan_speed_pct": "%",
        "power_kw": "kW", "humidity_pct": "%", "co2_ppm": "ppm", "occupancy": "people", "coil_dt_c": "°C"}

TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "signal_deviations",
        "description": "Sensors that deviate most from this unit's normal value for this time of day "
                       "(value, normal value, z = standard deviations away), plus the rules that fired.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "recent_trend",
        "description": "How each sensor changed over the last minutes before detection (first, last, change, "
                       "min, max), and airflow per % fan speed at the start and end.",
        "parameters": {"type": "object", "properties": {
            "minutes": {"type": "integer", "description": "Look-back in minutes (5-60, default 30)"}}}}},
    {"type": "function", "function": {
        "name": "sensor_health",
        "description": "Checks whether any sensor is frozen (identical readings), out of physical range, or missing.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "other_units",
        "description": "Whether the other air handling units in the building look abnormal at the same time "
                       "(several at once usually means weather or a building-wide cause, not this unit).",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "schedule_context",
        "description": "Time of day, whether it is inside the operating schedule, occupancy and unit status.",
        "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {
        "name": "device_history",
        "description": "Earlier anomaly events on this same unit (how many, when, which rules).",
        "parameters": {"type": "object", "properties": {}}}},
]
TOOL_NAMES = [t["function"]["name"] for t in TOOL_SPECS]


def _fmt(key: str, v) -> str:
    if v is None:
        return "n/a"
    digits = 0 if key in ("airflow_cfm", "co2_ppm", "occupancy", "fan_speed_pct") else 1
    return f"{v:,.{digits}f} {UNIT.get(key, '')}".strip()


class TriageTools:
    def __init__(self, event: dict, store=None, pipeline=None,
                 peers: Callable[[dict], list[dict]] | None = None,
                 history: Callable[[dict], list[dict]] | None = None,
                 config: MonitoringConfig | None = None):
        self.event = event
        self.window = [r for r in event.get("window", []) if isinstance(r, dict)]
        self.store, self.pipeline = store, pipeline
        self._peers, self._history = peers, history
        self.config = config or MonitoringConfig()

    def run(self, name: str, args: dict | None = None) -> dict:
        if name not in TOOL_NAMES:
            return {"error": f"unknown tool {name}", "note": f"There is no tool called {name}."}
        fn = getattr(self, name)
        try:
            return fn(**(args or {})) if name == "recent_trend" else fn()
        except Exception as exc:  # a broken tool must not stop the triage
            return {"error": str(exc), "note": f"The {name} check failed: {exc}"}

    # ---------------------------------------------------------------- tools
    def signal_deviations(self) -> dict:
        sig = [s for s in self.event.get("signals", []) if abs(s.get("z", 0)) >= 2]
        parts = [f"{LABEL.get(s['key'], s['key'])} {_fmt(s['key'], s['value'])} vs normal "
                 f"{_fmt(s['key'], s.get('baseline'))} ({s['z']:+.1f}σ)" for s in sig[:4]]
        rules = self.event.get("rule_hits", [])
        note = ("; ".join(parts) or "No sensor is far from normal") + (
            f". Rules fired: {', '.join(rules)}." if rules else ". No rule fired.")
        return {"signals": sig, "rule_hits": rules, "method": self.event.get("method"),
                "score": self.event.get("score"), "severity": self.event.get("severity"), "note": note}

    def recent_trend(self, minutes: int = 30) -> dict:
        minutes = int(max(5, min(60, minutes or 30)))
        rows = self.window
        if self.store is not None and minutes > len(rows):
            rows = self.store.get_telemetry(self.event["device_id"], minutes) or rows
        rows = rows[-minutes:]
        if len(rows) < 2:
            return {"minutes": 0, "note": "Not enough recent readings to show a trend."}
        out = {}
        for k in TREND_KEYS:
            vals = [r[k] for r in rows if r.get(k) is not None]
            if len(vals) >= 2:
                out[k] = {"first": vals[0], "last": vals[-1], "change": round(vals[-1] - vals[0], 2),
                          "min": min(vals), "max": max(vals)}
        ratio = {}
        for tag, r in (("start", rows[0]), ("end", rows[-1])):
            if r.get("fan_speed_pct"):
                ratio[tag] = round(r["airflow_cfm"] / r["fan_speed_pct"], 1)
        moved = sorted(((k, v) for k, v in out.items() if k != "occupancy" and v["first"]),
                       key=lambda kv: -abs(kv[1]["change"] / (abs(kv[1]["first"]) or 1)))[:3]
        parts = [f"{LABEL[k]} {_fmt(k, v['first'])} → {_fmt(k, v['last'])}" for k, v in moved]
        if len(ratio) == 2 and ratio["start"]:
            parts.append(f"airflow per % fan {ratio['start']} → {ratio['end']}")
        return {"minutes": len(rows), "signals": out, "airflow_per_fan_pct": ratio,
                "note": f"Over the last {len(rows)} min: " + "; ".join(parts) + "."}

    def sensor_health(self) -> dict:
        rows = self.window[-10:]
        on = [r for r in rows if r.get("status") == "ON"]
        frozen, out_of_range, missing = [], [], []
        if len(on) >= 10:
            for k in STUCK_KEYS:
                vals = [r.get(k) for r in on if r.get(k) is not None]
                if len(vals) >= 10 and float(np.std(vals)) < 0.005:
                    frozen.append(k)
        last = self.window[-1] if self.window else {}
        for k, (lo, hi) in RANGES.items():
            v = last.get(k)
            if v is None:
                missing.append(k)
            elif not lo <= v <= hi:
                out_of_range.append(k)
        if frozen:
            note = f"{', '.join(LABEL[k] for k in frozen)} has not changed for 10 readings while the unit runs: likely a frozen sensor."
        elif out_of_range:
            note = f"{', '.join(LABEL[k] for k in out_of_range)} is outside the physically possible range."
        elif missing:
            note = f"Missing values for {', '.join(LABEL[k] for k in missing)}."
        else:
            note = "All sensors are changing normally and within range."
        return {"frozen": frozen, "out_of_range": out_of_range, "missing": missing, "note": note}

    def other_units(self) -> dict:
        me = self.event["device_id"]
        if self._peers is not None:
            peers = self._peers(self.event)
        elif self.pipeline is not None:
            peers = [{"device_id": d, "state": self.pipeline.health(d)}
                     for d in sorted(self.pipeline.latest) if d != me]
        else:
            return {"checked": 0, "abnormal": [], "note": "Other units could not be checked."}
        bad = [p["device_id"] for p in peers if p.get("state") in ("warning", "critical")]
        if not peers:
            note = "No other units are reporting."
        elif len(bad) >= 3:
            note = f"{len(bad)} of {len(peers)} other units are abnormal too ({', '.join(bad)}): likely building-wide."
        elif bad:
            note = f"Only {', '.join(bad)} also looks unusual; the rest of the building is normal."
        else:
            note = f"All {len(peers)} other units look normal, so the problem is specific to {me}."
        return {"checked": len(peers), "abnormal": bad, "note": note}

    def schedule_context(self) -> dict:
        ts = self.event["ts_detected"]
        hhmm = int(ts[11:13]) * 60 + int(ts[14:16])
        on, off = _hhmm(self.config.schedule_on), _hhmm(self.config.schedule_off)
        inside = on <= hhmm < off
        last = self.window[-1] if self.window else {}
        occ, status = last.get("occupancy"), last.get("status")
        note = (f"{ts[11:16]}, {'inside' if inside else 'outside'} the {self.config.schedule_on}–"
                f"{self.config.schedule_off} schedule; unit {status or 'unknown'}, "
                f"{occ if occ is not None else 'unknown'} people in the zone.")
        if not inside and status == "ON" and not occ:
            note += " The unit is running for an empty zone."
        return {"time": ts[11:16], "inside_schedule": inside, "status": status, "occupancy": occ,
                "fan_speed_pct": last.get("fan_speed_pct"), "note": note}

    def device_history(self) -> dict:
        me, eid = self.event["device_id"], self.event.get("event_id")
        if self._history is not None:
            past = self._history(self.event)
        elif self.store is not None:
            past = [e for e in self.store.recent_events(200) if e.get("device_id") == me]
        else:
            return {"count": 0, "note": "No event history available."}
        past = [e for e in past if e.get("event_id") != eid and e.get("ts_detected", "") < self.event["ts_detected"]]
        if not past:
            return {"count": 0, "note": f"No earlier anomalies on {me}."}
        last = max(past, key=lambda e: e["ts_detected"])
        rules = sorted({r for e in past for r in e.get("rule_hits", [])})
        return {"count": len(past), "last": last["ts_detected"], "rules_seen": rules,
                "note": f"{len(past)} earlier anomal{'y' if len(past) == 1 else 'ies'} on {me}, last at "
                        f"{last['ts_detected'][:16].replace('T', ' ')}" + (f" ({', '.join(rules)})." if rules else ".")}
