"""Detection metrics shared by tuning and the final evaluation.

Event level (what matters for operators):
  * a fault injection is DETECTED if an event fires on the same AHU between the
    fault start and 15 min after it ends; time to detect = event time - fault start
  * a FALSE ALARM is an event that is not inside any fault (plus 60 min of
    recovery after the fault is cleared, when the unit is still settling)

Reading level (per minute):
  * a reading is predicted abnormal if it is suspicious or a rule fires
  * precision / recall / F1 against fault_label != "none"
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.detection.anomaly_agent import AnomalyDetector, EventTracker  # noqa: E402
from app.detection.monitoring import RULES  # noqa: E402

DETECT_GRACE = pd.Timedelta("15min")
RECOVERY_GRACE = pd.Timedelta("60min")


def load_log(path: Path) -> pd.DataFrame:
    log = pd.read_csv(path)
    for c in ("start", "end"):
        log[c] = pd.to_datetime(log[c], utc=True).dt.tz_localize(None)
    return log


def run_events(scored: pd.DataFrame, mode: str, confirm_k: int = 4, common_mode: bool = True,
               low_hold_min: int | None = None) -> pd.DataFrame:
    kw = {} if low_hold_min is None else {"low_hold_min": low_hold_min}
    tracker = EventTracker(mode, confirm_k=confirm_k, common_mode=common_mode, **kw)
    cols = ["device_id", "zone", "ts", "s_z", "s_if", "others_z", "others_if", "others_any"] + [f"rule_{r}" for r in RULES]
    events = []
    # itertuples is much faster than iterrows; EventTracker only needs item access
    for dev, g in scored.groupby("device_id", sort=False):
        sub = g[cols + [c for c in g.columns if c.startswith(("z_", "base_"))]
                + ["zone_temp_c", "supply_temp_c", "humidity_pct", "airflow_cfm", "power_kw", "co2_ppm", "coil_dt_c"]]
        for rec in sub.to_dict("records"):
            ev = tracker.update(rec)
            if ev:
                events.append(ev)
    ev = pd.DataFrame(events)
    if len(ev):
        ev["t"] = pd.to_datetime(ev["ts_detected"], utc=True).dt.tz_localize(None)
    return ev


def event_metrics(ev: pd.DataFrame, log: pd.DataFrame, days: float, n_devices: int = 6):
    rows = []
    for _, f in log.iterrows():
        m = ev[(ev.device_id == f.device_id) & (ev.t >= f.start) & (ev.t <= f.end + DETECT_GRACE)] if len(ev) else ev
        hit = len(m) > 0
        rows.append({
            "device_id": f.device_id, "fault": f.fault, "start": f.start, "end": f.end,
            "detected": hit,
            "delay_min": (m.t.min() - f.start).total_seconds() / 60 if hit else np.nan,
            "method": m.sort_values("t").iloc[0]["method"] if hit else None,
            "severity": m.sort_values("t").iloc[0]["severity"] if hit else None,
        })
    per_fault = pd.DataFrame(rows)
    false_alarms = []
    for _, e in ev.iterrows():
        inside = log[(log.device_id == e.device_id) & (e.t >= log.start) & (e.t <= log.end + RECOVERY_GRACE)]
        if inside.empty:
            false_alarms.append(e)
    fa = pd.DataFrame(false_alarms)
    summary = {
        "injections": len(per_fault),
        "detected": int(per_fault.detected.sum()),
        "event_recall": per_fault.detected.mean(),
        "median_delay_min": per_fault.delay_min.median(),
        "events": len(ev),
        "false_alarms": len(fa),
        "false_alarms_per_ahu_day": len(fa) / (days * n_devices),
        "event_precision": (len(ev) - len(fa)) / len(ev) if len(ev) else np.nan,
    }
    return summary, per_fault, fa


def point_metrics(scored: pd.DataFrame, mode: str) -> dict:
    flags = np.zeros(len(scored), dtype=bool)
    if mode in ("zscore", "full"):
        flags |= scored["s_z"].to_numpy() >= 0.5
    if mode in ("iforest", "full"):
        flags |= scored["s_if"].to_numpy() >= 0.5
    if mode in ("rules", "full"):
        for r in RULES:
            flags |= scored[f"rule_{r}"].to_numpy().astype(bool)
    truth = (scored["fault_label"] != "none").to_numpy()
    tp = int((flags & truth).sum()); fp = int((flags & ~truth).sum()); fn = int((~flags & truth).sum())
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    return {"point_precision": p, "point_recall": r, "point_f1": 2 * p * r / (p + r) if p + r else 0.0}
