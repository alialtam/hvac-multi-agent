"""Live detection pipeline: one telemetry reading in, an anomaly event out (sometimes).

    from app.detection.pipeline import DetectionPipeline

    pipeline = DetectionPipeline(sink=supervisor.handle)   # Person 2 passes their function
    pipeline.process(reading_dict)                           # call for every MQTT message

The pipeline keeps the last 40 readings per AHU, scores the newest one with the
Monitoring rules, z-score and Isolation Forest, and calls `sink(event)` when an
anomaly is confirmed. It gives exactly the same events as the batch evaluation
(tests/test_pipeline.py checks this).
"""
from __future__ import annotations

import logging
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from .anomaly_agent import AnomalyDetector, EventTracker
from .features import STARTUP_ROWS
from .monitoring import RULES, MonitoringConfig, OfflineWatcher, _hhmm

log = logging.getLogger("detection")

DEFAULT_MODEL = Path(__file__).resolve().parents[3] / "data" / "models" / "detector.joblib"
BUFFER_ROWS = 40     # longest rule window is 15 readings; events carry the last 30
REQUIRED = ["device_id", "ts", "zone_temp_c", "supply_temp_c", "setpoint_c", "humidity_pct", "airflow_cfm",
            "fan_speed_pct", "power_kw", "occupancy", "co2_ppm", "status"]


class DetectionPipeline:
    def __init__(self, model_path: str | Path | None = None, sink: Callable[[dict], None] | None = None,
                 mode: str = "full", offline_timeout_s: float = 30.0):
        path = Path(model_path or DEFAULT_MODEL)
        if not path.exists():
            raise FileNotFoundError(f"No trained model at {path}. Run: python -m app.detection.train")
        self.detector = AnomalyDetector.load(path)
        self.tracker = EventTracker(mode)
        self.sink = sink or (lambda e: log.info("event %s", e["event_id"]))
        self.buffers: dict[str, deque] = {}
        self.minutes_on: dict[str, int] = {}
        self.last_flags: dict[str, tuple[datetime, bool, bool]] = {}
        self.latest: dict[str, dict] = {}       # latest scored row per device (for dashboards)
        self.offline = OfflineWatcher(offline_timeout_s)
        self.lock = threading.Lock()
        self.schedule_on = _hhmm(MonitoringConfig().schedule_on)

    # ------------------------------------------------------------------ main
    def process(self, reading: dict) -> dict | None:
        missing = [k for k in REQUIRED if k not in reading]
        if missing:
            log.warning("dropping reading without %s", missing)
            return None
        r = {k: v for k, v in reading.items() if k != "fault_label"}   # never look at ground truth
        dev = r["device_id"]
        with self.lock:
            came_back = self.offline.seen(dev)
            buf = self.buffers.setdefault(dev, deque(maxlen=BUFFER_ROWS))
            buf.append(r)
            ts = pd.Timestamp(r["ts"]).tz_localize(None) if pd.Timestamp(r["ts"]).tz else pd.Timestamp(r["ts"])
            self._update_minutes_on(dev, r["status"], ts)

            df = pd.DataFrame(buf)
            scored = self.detector.score(df, common_mode=False, minutes_on_last={dev: self.minutes_on[dev]},
                                         last_only=True)
            row = scored.iloc[-1].to_dict()

            # building-wide check: how many OTHER units were suspicious one minute ago?
            prev = ts - timedelta(minutes=1)
            others = [(z, f) for d, (t, z, f) in self.last_flags.items() if d != dev and t == prev]
            row["others_z"] = sum(z for z, _ in others)
            row["others_if"] = sum(f for _, f in others)
            row["others_any"] = sum(z or f for z, f in others)
            self.last_flags[dev] = (ts, row["s_z"] >= 0.5, row["s_if"] >= 0.5)

            self.latest[dev] = {"ts": r["ts"], "s_z": float(row["s_z"]), "s_if": float(row["s_if"]),
                                "rules": [x for x in RULES if row[f"rule_{x}"]],
                                "open_event": self.tracker.state.get(dev) is not None
                                and self.tracker.state[dev].open_event}
            event = self.tracker.update(row, window=df)
            if event:
                self.latest[dev]["open_event"] = True
        if came_back:
            log.info("%s is back online", dev)
        if event:
            self.sink(event)
        return event

    def _update_minutes_on(self, dev: str, status: str, ts: pd.Timestamp) -> None:
        if status != "ON":
            self.minutes_on[dev] = 0
        elif dev in self.minutes_on:
            self.minutes_on[dev] += 1
        else:
            # first reading after the pipeline started and the unit is already running:
            # estimate from the schedule (normally switched on at 07:00)
            since = ts.hour * 60 + ts.minute - self.schedule_on
            self.minutes_on[dev] = since if since > 0 else STARTUP_ROWS + 1

    # --------------------------------------------------------------- offline
    def check_offline(self) -> list[dict]:
        """Call every few seconds. Emits a 'device_offline' event for silent devices."""
        events = []
        for dev in self.offline.check():
            last = self.buffers.get(dev, [None])[-1] or {}
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            ts = last.get("ts", now)
            event = {
                "event_id": f"evt_{dev}_offline_{ts.replace('-', '').replace(':', '')}",
                "device_id": dev, "zone": last.get("zone", ""),
                "ts_start": ts, "ts_detected": ts, "method": "rule", "score": 1.0,
                "severity": "HIGH", "signals": [], "rule_hits": ["device_offline"],
                "window": [{k: v for k, v in x.items()} for x in list(self.buffers.get(dev, []))[-30:]],
            }
            events.append(event)
            self.sink(event)
        return events

    # ----------------------------------------------------------- dashboards
    def health(self, device_id: str) -> str:
        """'offline' | 'critical' | 'warning' | 'ok' for the device grid."""
        if device_id in self.offline.offline:
            return "offline"
        x = self.latest.get(device_id)
        if not x:
            return "offline"
        if x["open_event"]:
            return "critical"
        if x["rules"] or max(x["s_z"], x["s_if"]) >= 0.5:
            return "warning"
        return "ok"
