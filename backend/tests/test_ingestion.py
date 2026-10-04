"""Ingestion without a broker: messages -> SQLite -> detection, plus offline alerts."""
import json
import time
from pathlib import Path

import pandas as pd
import pytest

from app.detection.pipeline import DEFAULT_MODEL, DetectionPipeline
from app.ingestion.db import TelemetryStore, get_engine
from app.ingestion.subscriber import Ingestion

SAMPLE = Path(__file__).resolve().parents[2] / "data" / "sample_day.csv"
pytestmark = pytest.mark.skipif(not DEFAULT_MODEL.exists() or not SAMPLE.exists(),
                                reason="run generate_dataset.py and app.detection.train first")


@pytest.fixture
def ingestion(tmp_path):
    events, readings = [], []
    store = TelemetryStore(get_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}"))
    ing = Ingestion(on_event=events.append, on_reading=readings.append, store=store,
                    pipeline=DetectionPipeline(offline_timeout_s=0.2))
    return ing, events, readings


def test_messages_are_stored_and_ground_truth_hidden(ingestion):
    ing, _, readings = ingestion
    day = pd.read_csv(SAMPLE)
    for rec in day[day.ts < "2026-09-30T00:20"].sort_values(["ts", "device_id"]).to_dict("records"):
        ing.handle_message(json.dumps(rec))
    assert ing.readings == 120
    assert len(ing.store.latest_readings()) == 6
    assert len(ing.store.get_telemetry("AHU-1", minutes=10)) == 10
    assert all("fault_label" not in r for r in readings)
    assert all("fault_label" not in r for r in ing.store.latest_readings())


def test_bad_messages_are_ignored(ingestion):
    ing, _, _ = ingestion
    assert ing.handle_message(b"not json") is None
    assert ing.handle_message(json.dumps({"hello": 1})) is None
    assert ing.readings == 0


def test_offline_device_raises_event(ingestion):
    ing, events, _ = ingestion
    rec = pd.read_csv(SAMPLE).iloc[0].to_dict()
    ing.handle_message(json.dumps(rec))
    time.sleep(0.3)
    ing.pipeline.check_offline()
    assert events and events[-1]["rule_hits"] == ["device_offline"]
    assert ing.pipeline.health(rec["device_id"]) == "offline"
    assert ing.store.recent_events()[0]["rule_hits"] == ["device_offline"]


def test_empty_database_url_uses_default(monkeypatch):
    # .env.example ships "DATABASE_URL=" (empty): that must mean "use the SQLite default", not crash
    from app.ingestion.db import DEFAULT_URL
    for value in ("", "   ", "# empty = SQLite file data/hvac.db"):
        monkeypatch.setenv("DATABASE_URL", value)
        # compare the file path, not the URL text: on Windows the URL text escapes "C:" as "C%3A"
        assert get_engine().url.database == DEFAULT_URL.removeprefix("sqlite:///")
