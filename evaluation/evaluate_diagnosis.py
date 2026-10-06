"""Evaluate the Diagnosis agent: rules vs Ollama vs OpenAI on the labelled faults.

    python evaluation/evaluate_diagnosis.py --provider rules                 # validation set (default)
    python evaluation/evaluate_diagnosis.py --provider ollama --limit 20     # local model
    python evaluation/evaluate_diagnosis.py --provider openai --limit 25     # needs OPENAI_API_KEY
    python evaluation/evaluate_diagnosis.py --provider rules --split test    # final numbers (run once)

Same events, labels and peers function as evaluate_triage.py. Ground truth comes from the fault log,
never from the event. Only events inside an injected fault are scored. The chosen provider is the only
one allowed to answer; if it fails, the agent falls back to rules and the row is marked as a fallback.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict

import pandas as pd

from evaluate_triage import DATA, MODEL, OUT, SPLITS, label_events   # also puts backend/ on sys.path
from metrics import ROOT, load_log

from app.agents.diagnosis import diagnose  # noqa: E402
from app.detection.anomaly_agent import AnomalyDetector, detect_batch  # noqa: E402
from app.llm import llm  # noqa: E402

CAUSES = ["filter_blockage", "compressor_failure", "refrigerant_leak", "sensor_stuck", "after_hours_waste"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=SPLITS, default="val")
    ap.add_argument("--provider", default="rules", choices=["rules", "openai", "ollama"])
    ap.add_argument("--limit", type=int, default=0, help="diagnose at most N events (LLM cost control)")
    args = ap.parse_args()
    if args.provider != "rules":
        try:
            from dotenv import load_dotenv
            load_dotenv(ROOT / ".env")
        except ImportError:
            pass
        llm.providers = {k: v for k, v in llm.providers.items() if k == args.provider}
    llm.set_provider(args.provider)

    data_file, log_file = SPLITS[args.split]
    df = pd.read_csv(DATA / data_file)
    log = load_log(DATA / log_file)
    det = AnomalyDetector.load(MODEL)
    t0 = time.time()
    scored, events = detect_batch(det, df, "full", with_window=True)
    print(f"{args.split}: {len(events)} events from the detector ({time.time() - t0:.0f} s)")

    sus = scored[(scored.s_z >= 0.5) | (scored.s_if >= 0.5)]
    flagged = sus.groupby(sus.ts.dt.strftime("%Y-%m-%dT%H:%M:%SZ"))["device_id"].apply(set).to_dict()
    devices = sorted(df.device_id.unique())

    def peers(e):
        prev = (pd.Timestamp(e["ts_detected"]) - pd.Timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        bad = flagged.get(prev, set())
        return [{"device_id": d, "state": "warning" if d in bad else "ok"} for d in devices if d != e["device_id"]]

    labels = label_events(events, log)
    pairs = [(e, t) for e, t in zip(events, labels) if t in CAUSES]      # only events inside a real fault
    print(f"{len(pairs)} of them fall inside an injected fault and are scored")
    if args.limit:                                                       # spread over the fault types
        by = defaultdict(list)
        for p in pairs:
            by[p[1]].append(p)
        pairs = []
        while len(pairs) < args.limit and any(by.values()):
            for k in list(by):
                if by[k] and len(pairs) < args.limit:
                    pairs.append(by[k].pop(0))

    rows = []
    for e, truth in pairs:
        t1 = time.perf_counter()
        try:
            d, meta = diagnose(e, peers=peers)
            pred, conf, used, tokens = d.cause, d.confidence, meta.get("provider"), meta.get("tokens", 0)
        except Exception as exc:                                         # a crash counts as a miss, not a stop
            pred, conf, used, tokens = "error", 0.0, f"error: {exc}"[:60], 0
        ms = int((time.perf_counter() - t1) * 1000)
        ok = pred == truth
        rows.append({"event_id": e["event_id"], "truth": truth, "predicted": pred, "correct": ok,
                     "confidence": conf, "requested": args.provider, "answered_by": used,
                     "fell_back": used != args.provider, "tokens": tokens, "ms": ms})
        if args.provider != "rules":
            print(f"  {e['event_id']:32s} {truth:20s} -> {pred:20s} {'OK ' if ok else 'BAD'} via {used} ({ms} ms)")
    res = pd.DataFrame(rows)
    if res.empty:
        print("No scored events.")
        return

    print(f"\nDiagnosis ({args.provider}) on {len(res)} events, split={args.split}")
    print(res.groupby("truth").agg(events=("correct", "size"), correct=("correct", "sum")).to_string())
    print("\nConfusion (rows = truth, columns = predicted)")
    print(pd.crosstab(res.truth, res.predicted).to_string())
    llm_rows = res[~res.fell_back]
    summary = {
        "split": args.split, "provider": args.provider, "events": len(res),
        "accuracy": round(res.correct.mean(), 3),
        "answered_by_requested_provider": len(llm_rows),
        "accuracy_when_requested_provider_answered": round(llm_rows.correct.mean(), 3) if len(llm_rows) else None,
        "fell_back_to_rules": int(res.fell_back.sum()),
        "mean_confidence": round(res.confidence.mean(), 3),
        "median_ms": float(res.ms.median()), "total_tokens": int(res.tokens.sum()),
    }
    print(json.dumps(summary, indent=2))
    OUT.mkdir(parents=True, exist_ok=True)
    tag = f"diagnosis_{args.provider}_{args.split}"
    res.to_csv(OUT / f"{tag}.csv", index=False)
    (OUT / f"{tag}_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"Saved {OUT / (tag + '.csv')}")


if __name__ == "__main__":
    sys.exit(main())