"""The live pipeline must produce exactly the events the batch evaluation reports."""
import json
from pathlib import Path

import pandas as pd
import pytest

from app.detection.anomaly_agent import AnomalyDetector, detect_batch
from app.detection.pipeline import DEFAULT_MODEL, DetectionPipeline

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "data" / "sample_day.csv"
SCHEMA = json.loads((ROOT / "contracts" / "anomaly_event.schema.json").read_text())

pytestmark = pytest.mark.skipif(not DEFAULT_MODEL.exists() or not SAMPLE.exists(),
                                reason="run generate_dataset.py and app.detection.train first")


@pytest.fixture(scope="module")
def morning():
    """06:30-11:30 of the sample day: unit start-up, a refrigerant leak (AHU-5, 09:00),
    a compressor failure (AHU-1, 10:00) and a filter blockage (AHU-4, 11:00)."""
    day = pd.read_csv(SAMPLE)
    return day[(day.ts >= "2026-09-30T06:30") & (day.ts < "2026-09-30T11:30")].reset_index(drop=True)


@pytest.fixture(scope="module")
def live_events(morning):
    events = []
    pipe = DetectionPipeline(sink=events.append)
    for rec in morning.sort_values(["ts", "device_id"]).to_dict("records"):
        pipe.process(rec)
    return events


def test_live_equals_batch(morning, live_events):
    _, batch_events = detect_batch(AnomalyDetector.load(DEFAULT_MODEL), morning)
    key = lambda e: (e["device_id"], e["ts_detected"], e["severity"], e["method"])  # noqa: E731
    assert sorted(map(key, live_events)) == sorted(map(key, batch_events))


def test_event_matches_contract(live_events):
    assert live_events, "expected at least one event"
    for e in live_events:
        for field in SCHEMA["required"]:
            assert field in e, field
        assert e["severity"] in SCHEMA["properties"]["severity"]["enum"]
        assert e["method"] in SCHEMA["properties"]["method"]["enum"]
        assert 0 <= e["score"] <= 1
        assert len(e["signals"]) <= 5 and len(e["window"]) <= 30
        assert all("fault_label" not in w for w in e["window"])
        json.dumps(e)   # must be JSON serialisable for the API / WebSocket


def test_every_sample_fault_is_detected():
    day = pd.read_csv(SAMPLE)
    _, events = detect_batch(AnomalyDetector.load(DEFAULT_MODEL), day)
    faulty = day[day.fault_label != "none"].groupby("device_id").ts.min()
    for dev, start in faulty.items():
        assert any(e["device_id"] == dev and e["ts_detected"] >= start for e in events), dev
