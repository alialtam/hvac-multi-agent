"""Choose detection thresholds on the VALIDATION set (never on the test set).

    python evaluation/tune_detection.py

Tries alarm quantiles for z-score and Isolation Forest and the confirmation rule
(k suspicious readings out of 5), and ranks by: detect everything first, then
fewest false alarms, then fastest detection. Writes evaluation/results/tuning.csv.
"""
from __future__ import annotations

import itertools

import pandas as pd

from metrics import ROOT, AnomalyDetector, event_metrics, load_log, run_events

DATA = ROOT / "data"
OUT = ROOT / "evaluation" / "results"


def main() -> None:
    train = pd.read_csv(DATA / "train_normal.csv.gz")
    val = pd.read_csv(DATA / "val_faults.csv.gz")
    log = load_log(DATA / "val_fault_log.csv")
    days = val["ts"].str[:10].nunique()

    rows = []
    for zq, iq in itertools.product([0.999, 0.9995, 0.9999], [0.999, 0.9995, 0.9999]):
        det = AnomalyDetector.fit(train, quantile=zq, if_quantile=iq)
        scored = det.score(val)
        for k in (3, 4):
            ev = run_events(scored, "full", confirm_k=k)
            s, _, _ = event_metrics(ev, log, days)
            rows.append({"z_quantile": zq, "if_quantile": iq, "confirm_k": k, **s})
            print(f"z={zq} if={iq} k={k}: detected {s['detected']}/{s['injections']}, "
                  f"false alarms {s['false_alarms']}, median delay {s['median_delay_min']:.0f} min")
    res = pd.DataFrame(rows).sort_values(["detected", "false_alarms", "median_delay_min"],
                                         ascending=[False, True, True])
    OUT.mkdir(parents=True, exist_ok=True)
    res.to_csv(OUT / "tuning.csv", index=False)
    best = res.iloc[0]
    print(f"\nBest on validation: z_quantile={best.z_quantile}, if_quantile={best.if_quantile}, "
          f"confirm_k={int(best.confirm_k)}")


if __name__ == "__main__":
    main()
