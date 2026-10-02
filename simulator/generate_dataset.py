"""Generate the labelled datasets used for training and evaluating detection.

Outputs (in ../data/):
    train_normal.csv.gz   14 normal days, all 6 AHUs           (training the detectors)
    val_faults.csv.gz     5 days: 4 days with 5 injections each + 1 normal day (tuning thresholds)
    val_fault_log.csv     ground truth for the validation set
    test_faults.csv.gz    12 days: 10 days with 5 injections each + 2 normal days (final results)
    fault_log.csv         ground truth: one row per injection (device, fault, start, end)
    sample_day.csv        1 small day with all 5 faults (quick tests for Person 2)

Run:  python generate_dataset.py            (takes about 1 minute)
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from hvac_sim.building import Building, load_config

DATA = Path(__file__).resolve().parents[1] / "data"

# (fault, earliest start hour, latest start hour, min duration, max duration) in minutes
FAULT_WINDOWS = {
    "filter_blockage":    (9 * 60, 14 * 60, 120, 240),
    "compressor_failure": (9 * 60, 15 * 60, 60, 120),
    "refrigerant_leak":   (8 * 60 + 30, 11 * 60, 300, 420),
    "sensor_stuck":       (9 * 60, 15 * 60, 120, 180),
    "after_hours_waste":  (18 * 60 + 45, 20 * 60, 180, 240),
}


def simulate(cfg: dict, start: datetime, days: int, injections: list[dict], seed: int) -> pd.DataFrame:
    """Run the building minute by minute and apply injections at their times."""
    b = Building(cfg, seed=seed)
    b.warm_up(start)
    events: dict[datetime, list[tuple[str, str]]] = {}
    for inj in injections:
        events.setdefault(inj["start"], []).append(("inject", inj))
        events.setdefault(inj["end"], []).append(("reset", inj))
    rows: list[dict] = []
    t, end = start, start + timedelta(days=days)
    while t < end:
        for action, inj in events.get(t, []):
            ahu = b.get(inj["device_id"])
            if action == "inject":
                ahu.inject(inj["fault"], t)
            elif ahu.fault and ahu.fault.name == inj["fault"]:
                ahu.reset()
        rows.extend(b.step(t))
        t += timedelta(minutes=1)
    return pd.DataFrame(rows)


def plan_injections(cfg: dict, start: datetime, fault_days: int, rng: np.random.Generator) -> list[dict]:
    """Each fault day gets every fault once, each on a different AHU (one AHU stays healthy)."""
    devices = [d["device_id"] for d in cfg["devices"]]
    plan = []
    for day in range(fault_days):
        day0 = start + timedelta(days=day)
        chosen = rng.permutation(devices)[: len(FAULT_WINDOWS)]
        for dev, (fault, (lo, hi, dmin, dmax)) in zip(chosen, FAULT_WINDOWS.items()):
            s = day0 + timedelta(minutes=int(rng.integers(lo, hi)))
            e = s + timedelta(minutes=int(rng.integers(dmin, dmax)))
            e = min(e, day0 + timedelta(hours=23, minutes=59))
            plan.append({"device_id": str(dev), "fault": fault, "start": s, "end": e})
    return plan


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train-days", type=int, default=14)
    ap.add_argument("--fault-days", type=int, default=10)
    ap.add_argument("--normal-test-days", type=int, default=2)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--test-seed", type=int, default=2027,
                    help="separate seed for the final test set (never used while tuning)")
    args = ap.parse_args()

    cfg = load_config()
    DATA.mkdir(exist_ok=True)
    rng = np.random.default_rng(args.seed)

    print(f"Training set: {args.train_days} normal days...")
    train = simulate(cfg, datetime(2026, 8, 15), args.train_days, [], seed=args.seed)
    train.to_csv(DATA / "train_normal.csv.gz", index=False)

    def write_log(plan, name):
        log = pd.DataFrame(plan)
        log["start"] = log["start"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        log["end"] = log["end"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        log.sort_values("start").to_csv(DATA / name, index=False)

    val_start = datetime(2026, 9, 1)
    val_plan = plan_injections(cfg, val_start, 4, np.random.default_rng(args.seed + 100))
    print(f"Validation set: 5 days, {len(val_plan)} fault injections...")
    val = simulate(cfg, val_start, 5, val_plan, seed=args.seed + 50)
    val.to_csv(DATA / "val_faults.csv.gz", index=False)
    write_log(val_plan, "val_fault_log.csv")

    test_start = datetime(2026, 9, 10)
    plan = plan_injections(cfg, test_start, args.fault_days, np.random.default_rng(args.test_seed))
    days = args.fault_days + args.normal_test_days
    print(f"Test set: {days} days, {len(plan)} fault injections...")
    test = simulate(cfg, test_start, days, plan, seed=args.test_seed)
    test.to_csv(DATA / "test_faults.csv.gz", index=False)

    write_log(plan, "fault_log.csv")

    print("Sample day for quick tests...")
    d0 = datetime(2026, 9, 30)
    sample_plan = [
        {"device_id": "AHU-1", "fault": "compressor_failure", "start": d0 + timedelta(hours=10), "end": d0 + timedelta(hours=11, minutes=30)},
        {"device_id": "AHU-4", "fault": "filter_blockage", "start": d0 + timedelta(hours=11), "end": d0 + timedelta(hours=14)},
        {"device_id": "AHU-3", "fault": "sensor_stuck", "start": d0 + timedelta(hours=13), "end": d0 + timedelta(hours=15)},
        {"device_id": "AHU-5", "fault": "refrigerant_leak", "start": d0 + timedelta(hours=9), "end": d0 + timedelta(hours=16)},
        {"device_id": "AHU-2", "fault": "after_hours_waste", "start": d0 + timedelta(hours=19), "end": d0 + timedelta(hours=23)},
    ]
    sample = simulate(cfg, d0, 1, sample_plan, seed=args.seed + 2)
    sample.to_csv(DATA / "sample_day.csv", index=False)

    print("\nRows: train", len(train), "| test", len(test), "| sample", len(sample))
    print("Labelled minutes in test set:")
    print(test["fault_label"].value_counts().to_string())


if __name__ == "__main__":
    main()
