"""Evaluate the anomaly triage agent on the detector's real events.

    python evaluation/evaluate_triage.py                       # rules triage, validation set
    python evaluation/evaluate_triage.py --split test          # final numbers (run once)
    python evaluation/evaluate_triage.py --provider openai --limit 25   # LLM (needs OPENAI_API_KEY)

Every event the full detector raises is triaged. Ground truth comes from the
fault log: the injected fault's AREA (filter -> airflow, compressor/refrigerant
-> cooling, sensor_stuck -> sensor, after_hours -> schedule). Events outside any
fault are false alarms: the triage should call them false_alarm or building_wide.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

import pandas as pd

from metrics import DETECT_GRACE, ROOT, load_log   # also puts backend/ on sys.path

from app.detection.anomaly_agent import AnomalyDetector, detect_batch  # noqa: E402
from app.detection.triage import triage_event  # noqa: E402

DATA, OUT = ROOT / "data", ROOT / "evaluation" / "results"
MODEL = DATA / "models" / "detector.joblib"
SPLITS = {"val": ("val_faults.csv.gz", "val_fault_log.csv"), "test": ("test_faults.csv.gz", "fault_log.csv")}
AREA = {"filter_blockage": "airflow", "compressor_failure": "cooling", "refrigerant_leak": "cooling",
        "sensor_stuck": "sensor", "after_hours_waste": "schedule"}
DISMISS = {"false_alarm", "building_wide"}


def label_events(events: list[dict], log: pd.DataFrame) -> list[str]:
    labels = []
    for e in events:
        t = pd.Timestamp(e["ts_detected"]).tz_localize(None)
        m = log[(log.device_id == e["device_id"]) & (log.start <= t) & (t <= log.end + DETECT_GRACE)]
        labels.append(m.iloc[0].fault if len(m) else "none")
    return labels


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=SPLITS, default="val")
    ap.add_argument("--provider", default="rules", choices=["rules", "openai", "ollama"])
    ap.add_argument("--limit", type=int, default=0, help="triage at most N events (LLM cost control)")
    args = ap.parse_args()
    if args.provider != "rules":
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass

    data_file, log_file = SPLITS[args.split]
    df = pd.read_csv(DATA / data_file)
    log = load_log(DATA / log_file)
    det = AnomalyDetector.load(MODEL)
    t0 = time.time()
    scored, events = detect_batch(det, df, "full", with_window=True)
    print(f"{args.split}: {len(events)} events from the detector ({time.time() - t0:.0f} s)")

    # what the other units looked like one minute before each reading (same as the live check)
    sus = scored[(scored.s_z >= 0.5) | (scored.s_if >= 0.5)]
    flagged = sus.groupby(sus.ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ"))["device_id"].apply(set).to_dict()
    devices = sorted(df.device_id.unique())

    def peers(e):
        prev = (pd.Timestamp(e["ts_detected"]) - pd.Timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        bad = flagged.get(prev, set())
        return [{"device_id": d, "state": "warning" if d in bad else "ok"} for d in devices if d != e["device_id"]]

    def history(e):
        return [x for x in events if x["device_id"] == e["device_id"] and x["ts_detected"] < e["ts_detected"]]

    labels = label_events(events, log)
    pairs = list(zip(events, labels))
    if args.limit:
        # keep every false alarm and spread the rest over the fault types
        by = defaultdict(list)
        for p in pairs:
            by[p[1]].append(p)
        pairs, i = [], 0
        while len(pairs) < args.limit and any(by.values()):
            for k in list(by):
                if by[k] and len(pairs) < args.limit:
                    pairs.append(by[k].pop(0))
            i += 1

    rows = []
    for e, truth in pairs:
        r = triage_event(e, provider=args.provider, peers=peers, history=history)
        want = AREA.get(truth)
        ok = (r["verdict"] in DISMISS) if truth == "none" else (r["suspected_area"] == want)
        rows.append({"event_id": e["event_id"], "truth": truth, "expected_area": want or "dismiss",
                     "verdict": r["verdict"], "area": r["suspected_area"], "next": r["recommend_next"],
                     "confidence": r["confidence"], "correct": ok, "provider": r["provider"],
                     "agrees_with_rules": r["agrees_with_rules"], "tools": len(r["tools_used"]),
                     "tokens": r["tokens"], "ms": r["duration_ms"]})
        if args.provider != "rules":
            print(f"  {e['event_id']:32s} {truth:20s} -> {r['verdict']:18s} {r['suspected_area']:9s} "
                  f"{'OK ' if ok else 'BAD'} via {r['provider']} ({r['duration_ms']} ms)")
    res = pd.DataFrame(rows)

    print(f"\nTriage ({args.provider}) on {len(res)} events, split={args.split}")
    per = res.groupby("truth").agg(events=("correct", "size"), correct=("correct", "sum"))
    print(per.to_string())
    real = res[res.truth != "none"]
    fa = res[res.truth == "none"]
    summary = {
        "split": args.split, "provider": args.provider, "events": len(res),
        "area_accuracy_on_real_faults": round(real.correct.mean(), 3) if len(real) else None,
        "false_alarms": len(fa), "false_alarms_flagged": int(fa.correct.sum()) if len(fa) else 0,
        "real_faults_wrongly_dismissed": int(real.verdict.isin(DISMISS).sum()),
        "agreement_with_rules": round(res.agrees_with_rules.mean(), 3),
        "median_ms": float(res.ms.median()), "total_tokens": int(res.tokens.sum()),
    }
    print(json.dumps(summary, indent=2))
    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"triage_{args.provider}_{args.split}"
    res.to_csv(OUT / f"{tag}.csv", index=False)
    (OUT / f"{tag}_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"Saved {OUT / (tag + '.csv')}")


if __name__ == "__main__":
    sys.exit(main())
