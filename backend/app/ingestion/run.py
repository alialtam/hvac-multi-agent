"""Run ingestion + detection on its own (before the FastAPI backend exists).

    cd backend
    python -m app.ingestion.run                 # broker on localhost
    python -m app.ingestion.run --host 192.168.1.20

Stores telemetry and events in ../data/hvac.db and prints every anomaly event.
Use --events-dir to also save each event as a JSON file (handy for Person 2).
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from .subscriber import Ingestion


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=None)
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--events-dir", default=None, help="save each anomaly event as JSON here")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    out = Path(args.events_dir) if args.events_dir else None
    if out:
        out.mkdir(parents=True, exist_ok=True)

    def on_event(e: dict) -> None:
        sig = ", ".join(f"{s['key']} {s['value']} (z {s['z']:+.1f})" for s in e["signals"][:3])
        print(f"\n>>> {e['severity']} anomaly on {e['device_id']} at {e['ts_detected']} "
              f"[{e['method']}] rules={e['rule_hits']}\n    {sig}\n", flush=True)
        if out:
            (out / f"{e['event_id']}.json").write_text(json.dumps(e, indent=2))

    ing = Ingestion(on_event=on_event, host=args.host, port=args.port)
    ing.start()
    print("Ingestion running. Ctrl+C to stop.")
    try:
        last = 0
        while True:
            time.sleep(10)
            if ing.readings != last:
                status = {d: ing.pipeline.health(d) for d in sorted(ing.pipeline.buffers)}
                print(f"{ing.readings} readings stored | health {status}", flush=True)
                last = ing.readings
    except KeyboardInterrupt:
        ing.stop()


if __name__ == "__main__":
    main()
