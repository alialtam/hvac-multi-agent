"""Anomaly Detection agent: two ML methods + the Monitoring agent's rules.

Method 1, z-score: how many standard deviations each signal is from the
          device's normal value for this time of day (see features.py).
Method 2, Isolation Forest: an unsupervised model trained on normal data only.
          It scores how easy a reading is to isolate from normal readings, so it
          catches unusual COMBINATIONS (e.g. normal temperature but too much power).

Both scores are mapped to 0..1 where 0.5 is the alarm threshold, calibrated on
normal training data (z: 99.9th percentile per signal, never below 3 sigma;
Isolation Forest: 99.99th percentile). Thresholds were chosen on a separate
validation set (evaluation/tune_detection.py).

A reading is "suspicious" when a score passes 0.5. It is ignored when 3+ other
units were also suspicious a minute earlier (the whole building moving together
is weather, not a fault). An EVENT is confirmed when 4 of the last 5 readings are
suspicious or a Monitoring rule fires. LOW-severity events are only raised if the
abnormality has lasted 20 minutes (short blips while people arrive are dropped);
MEDIUM and HIGH are raised at once. One event per device until it has been normal
for 30 minutes.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from .features import (FEATURE_COLUMNS, MIN_Z_THRESHOLD, OFF_IGNORED, SIGNALS, STARTUP_IGNORED,
                       STARTUP_ROWS, Z_SIGNALS, compute_features, fit_baseline, nominal_airflow_ratio)
from .monitoring import RULES, MonitoringAgent

MODES = ("rules", "zscore", "iforest", "full")
WINDOW_ROWS = 30
HIGH_RULES = {"zone_too_warm", "fast_temp_rise"}
MEDIUM_RULES = {"low_airflow", "running_unoccupied", "sensor_flatline"}


COMMON_MODE_MIN = 3   # this many OTHER units abnormal the minute before -> weather, not a fault
LOW_HOLD_MIN = 20     # a LOW-severity anomaly must last this long before an event is raised


def add_common_mode(d: pd.DataFrame) -> pd.DataFrame:
    """For each reading, count OTHER devices that were suspicious one minute earlier.

    Columns: others_z, others_if, others_any. The live pipeline fills the same
    columns from its own memory, so batch and live results are identical.
    """
    z = (d["s_z"] >= 0.5).astype(int)
    f = (d["s_if"] >= 0.5).astype(int)
    a = (z | f).astype(int)
    flags = pd.DataFrame({"ts": d["ts"], "device_id": d["device_id"], "z": z, "f": f, "a": a})
    per_ts = flags.groupby("ts")[["z", "f", "a"]].sum()
    prev_ts = d["ts"] - pd.Timedelta(minutes=1)
    own_prev = flags.set_index(["device_id", "ts"])[["z", "f", "a"]]
    own = own_prev.reindex(pd.MultiIndex.from_arrays([d["device_id"], prev_ts])).fillna(0).to_numpy()
    tot = per_ts.reindex(prev_ts).fillna(0).to_numpy()
    others = tot - own
    d["others_z"], d["others_if"], d["others_any"] = others[:, 0], others[:, 1], others[:, 2]
    return d


class AnomalyDetector:
    """Holds the trained baseline, Isolation Forest and calibrated thresholds."""

    def __init__(self, baseline, nominal_ratio, iforest, z_thr: dict, if_thr, if_mid, quantile):
        self.baseline = baseline
        self.nominal_ratio = nominal_ratio
        self.iforest = iforest
        self.z_thr = z_thr                  # per-signal |z| threshold
        self.z_signals = list(Z_SIGNALS)
        self.if_thr, self.if_mid = if_thr, if_mid
        self.quantile = quantile
        self.monitoring = MonitoringAgent(nominal_ratio)

    # ------------------------------------------------------------ training
    @classmethod
    def fit(cls, train: pd.DataFrame, quantile: float = 0.999, if_quantile: float | None = 0.9999,
            seed: int = 0) -> "AnomalyDetector":
        baseline = fit_baseline(train)
        ratio = nominal_airflow_ratio(train)
        feats = compute_features(train, baseline)
        X = feats[FEATURE_COLUMNS].to_numpy()
        iforest = IsolationForest(n_estimators=200, max_samples=1024, contamination="auto",
                                  random_state=seed, n_jobs=-1).fit(X)
        iforest.set_params(n_jobs=1)   # scoring one reading at a time: threads only add overhead
        # each signal gets its own threshold: supply air swings more than room air
        # (only over readings where the signal is actually judged, see features.py)
        settled = (feats["status"] == "ON") & (feats["minutes_on"] > STARTUP_ROWS)
        not_startup = ~((feats["status"] == "ON") & (feats["minutes_on"] <= STARTUP_ROWS))
        z_thr = {}
        for s in SIGNALS:
            rows = settled if s in OFF_IGNORED else (not_startup if s in STARTUP_IGNORED else feats.index == feats.index)
            z_thr[s] = max(MIN_Z_THRESHOLD, float(np.quantile(np.abs(feats.loc[rows, f"z_{s}"]), quantile)))
        a = -iforest.score_samples(X)
        return cls(baseline, ratio, iforest,
                   z_thr=z_thr,
                   if_thr=float(np.quantile(a, if_quantile or quantile)),
                   if_mid=float(np.median(a)),
                   quantile=quantile)

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.__dict__ | {"monitoring": None}, path)

    @classmethod
    def load(cls, path: str | Path) -> "AnomalyDetector":
        state = joblib.load(path)
        obj = cls.__new__(cls)
        obj.__dict__.update(state)
        obj.__dict__.setdefault("z_signals", list(Z_SIGNALS))
        obj.monitoring = MonitoringAgent(obj.nominal_ratio)
        return obj

    # ------------------------------------------------------------- scoring
    def score(self, df: pd.DataFrame, common_mode: bool = True,
              minutes_on_last: dict[str, int] | None = None, last_only: bool = False) -> pd.DataFrame:
        """Features + z score (s_z) + Isolation Forest score (s_if) + rule flags.

        last_only=True (live mode) runs the Isolation Forest on the newest row only.
        """
        d = compute_features(df, self.baseline, minutes_on_last)
        ratios = np.column_stack([np.abs(d[f"z_{s}"].to_numpy()) / self.z_thr[s] for s in self.z_signals])
        d["s_z"] = np.clip(0.5 * ratios.max(axis=1), 0, 1)
        X = d[FEATURE_COLUMNS].to_numpy()
        a = np.full(len(d), np.nan)
        if last_only:
            a[-1:] = -self.iforest.score_samples(X[-1:])
        else:
            a = -self.iforest.score_samples(X)
        d["s_if"] = np.clip(0.5 + 0.5 * (a - self.if_thr) / max(self.if_thr - self.if_mid, 1e-6), 0, 1)
        rules = self.monitoring.apply_rules(d)
        for r in RULES:
            d[f"rule_{r}"] = rules[r].to_numpy()
        if common_mode:
            d = add_common_mode(d)
        return d


@dataclass
class _DeviceState:
    recent: deque = field(default_factory=lambda: deque(maxlen=5))
    first_flag_ts: object = None
    open_event: bool = False
    calm_rows: int = 0


class EventTracker:
    """Turns per-reading scores into confirmed anomaly events (contract format)."""

    def __init__(self, mode: str = "full", confirm_k: int = 4, confirm_n: int = 5, close_after: int = 30,
                 common_mode: bool = True, low_hold_min: int = LOW_HOLD_MIN):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode, self.k, self.n, self.close_after = mode, confirm_k, confirm_n, close_after
        self.common_mode = common_mode
        self.low_hold_min = low_hold_min
        self.state: dict[str, _DeviceState] = {}

    def _flags(self, row) -> tuple[bool, bool, list[str]]:
        z = self.mode in ("zscore", "full") and row["s_z"] >= 0.5
        f = self.mode in ("iforest", "full") and row["s_if"] >= 0.5
        rules = [r for r in RULES if row[f"rule_{r}"]] if self.mode in ("rules", "full") else []
        if self.common_mode and (z or f):
            others = row.get({"zscore": "others_z", "iforest": "others_if"}.get(self.mode, "others_any"), 0)
            if others is not None and others >= COMMON_MODE_MIN:
                z = f = False          # the whole building moved together: weather, not this unit
        return bool(z), bool(f), rules

    def update(self, row, window: pd.DataFrame | None = None) -> dict | None:
        """Feed one scored row (a pandas Series). Returns an event dict or None."""
        dev = row["device_id"]
        st = self.state.setdefault(dev, _DeviceState(recent=deque(maxlen=self.n)))
        z, f, rules = self._flags(row)
        suspicious = z or f
        st.recent.append(suspicious)
        if suspicious or rules:
            st.calm_rows = 0
            if st.first_flag_ts is None:
                st.first_flag_ts = row["ts"]
        else:
            st.calm_rows += 1
            if st.calm_rows >= self.close_after:
                st.open_event = False
            if st.calm_rows >= self.n and not st.open_event:
                st.first_flag_ts = None

        confirmed = sum(st.recent) >= self.k or bool(rules)
        if not confirmed or st.open_event:
            return None
        event = self._build_event(row, st, z, f, rules, window)
        if event["severity"] == "LOW" and self.low_hold_min > 0:
            # weak signals must persist before anyone is bothered (short blips are dropped)
            lasted = (pd.Timestamp(row["ts"]) - pd.Timestamp(st.first_flag_ts)).total_seconds() / 60
            if lasted < self.low_hold_min:
                return None
        st.open_event = True
        return event

    def _build_event(self, row, st, z, f, rules, window) -> dict:
        score_parts = []
        if self.mode in ("zscore", "full"):
            score_parts.append(row["s_z"])
        if self.mode in ("iforest", "full"):
            score_parts.append(row["s_if"])
        score = float(max(score_parts)) if score_parts else 0.0
        if rules and score < 0.6:
            score = 0.6

        if score >= 0.85 or HIGH_RULES & set(rules):
            severity = "HIGH"
        elif score >= 0.65 or MEDIUM_RULES & set(rules):
            severity = "MEDIUM"
        else:
            severity = "LOW"

        if z and f:
            method = "combined"
        elif z:
            method = "zscore"
        elif f:
            method = "isolation_forest"
        else:
            method = "rule"

        signals = []
        for s in SIGNALS:
            zval = float(row[f"z_{s}"])
            base = row[f"base_{s}"]
            signals.append({"key": s, "value": round(float(row[s]), 2),
                            "baseline": None if pd.isna(base) else round(float(base), 2),
                            "z": round(zval, 2)})
        signals = sorted(signals, key=lambda x: -abs(x["z"]))[:5]

        ts = pd.Timestamp(row["ts"])
        start = pd.Timestamp(st.first_flag_ts or row["ts"])
        win = []
        if window is not None:
            keep = [c for c in window.columns if c in _RAW_FIELDS]
            w = window[keep].tail(WINDOW_ROWS).copy()
            w["ts"] = pd.to_datetime(w["ts"]).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            win = w.to_dict(orient="records")
        return {
            "event_id": f"evt_{row['device_id']}_{ts:%Y%m%dT%H%M%S}",
            "device_id": row["device_id"],
            "zone": row.get("zone", ""),
            "ts_start": f"{start:%Y-%m-%dT%H:%M:%SZ}",
            "ts_detected": f"{ts:%Y-%m-%dT%H:%M:%SZ}",
            "method": method,
            "score": round(score, 3),
            "severity": severity,
            "signals": signals,
            "rule_hits": rules,
            "window": win,
        }


_RAW_FIELDS = ["device_id", "zone", "ts", "zone_temp_c", "supply_temp_c", "setpoint_c", "humidity_pct",
               "airflow_cfm", "fan_speed_pct", "power_kw", "occupancy", "co2_ppm", "status", "outdoor_temp_c"]


def detect_batch(detector: AnomalyDetector, df: pd.DataFrame, mode: str = "full",
                 with_window: bool = False) -> tuple[pd.DataFrame, list[dict]]:
    """Run detection over a whole recorded dataset (used by evaluation).

    Gives the same events as the live pipeline, but much faster.
    """
    scored = detector.score(df)
    tracker = EventTracker(mode)
    events = []
    for dev, g in scored.groupby("device_id", sort=False):
        for i, (_, row) in enumerate(g.iterrows()):
            window = g.iloc[max(0, i - WINDOW_ROWS + 1): i + 1] if with_window else None
            ev = tracker.update(row, window)
            if ev:
                events.append(ev)
    events.sort(key=lambda e: e["ts_detected"])
    return scored, events
