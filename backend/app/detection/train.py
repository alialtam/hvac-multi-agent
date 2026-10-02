"""Train the anomaly detector on normal data.

    cd backend
    python -m app.detection.train            # uses ../data/train_normal.csv.gz

Writes ../data/models/detector.joblib (about 2 MB, takes ~20 s).
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd

from .anomaly_agent import AnomalyDetector

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TRAIN = ROOT / "data" / "train_normal.csv.gz"
DEFAULT_MODEL = ROOT / "data" / "models" / "detector.joblib"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", default=str(DEFAULT_TRAIN))
    ap.add_argument("--out", default=str(DEFAULT_MODEL))
    ap.add_argument("--quantile", type=float, default=0.999,
                    help="share of normal readings below the z-score alarm thresholds")
    ap.add_argument("--if-quantile", type=float, default=0.9999,
                    help="same for Isolation Forest")
    args = ap.parse_args()

    t0 = time.time()
    train = pd.read_csv(args.train)
    det = AnomalyDetector.fit(train, quantile=args.quantile, if_quantile=args.if_quantile)
    det.save(args.out)
    print(f"Trained on {len(train):,} normal readings in {time.time() - t0:.1f} s")
    print("  z-score thresholds     " + ", ".join(f"{k}={v:.1f}" for k, v in det.z_thr.items()))
    print(f"  Isolation Forest thr.  score >= {det.if_thr:.3f} (median {det.if_mid:.3f})")
    print(f"  nominal airflow ratio  {det.nominal_ratio}")
    print(f"Saved {args.out}")


if __name__ == "__main__":
    main()
