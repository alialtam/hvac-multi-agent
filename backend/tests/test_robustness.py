"""Input validation, simulator remote control and the broken-reading command."""
import json
from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.ingestion.sim_control import SimCommand
from app.ingestion.validation import validate_reading

ROOT = Path(__file__).resolve().parents[2]
GOOD = json.loads((ROOT / "contracts" / "sample_telemetry.json").read_text())


def test_good_reading_passes():
    assert validate_reading(GOOD) is None
    assert validate_reading({k: v for k, v in GOOD.items() if k not in ("fault_label", "outdoor_temp_c")}) is None


@pytest.mark.parametrize("change,expected", [
    ({"zone_temp_c": 999.0}, "outside the possible range"),
    ({"power_kw": -3.0}, "outside the possible range"),
    ({"humidity_pct": 140}, "outside the possible range"),
    ({"airflow_cfm": None}, "missing airflow_cfm"),
    ({"co2_ppm": "high"}, "not a number"),
    ({"occupancy": True}, "not a number"),
    ({"zone_temp_c": float("nan")}, "not a finite number"),
    ({"status": "MAYBE"}, "status must be ON or OFF"),
    ({"device_id": "AHU-4; DROP TABLE"}, "invalid device_id"),
    ({"ts": "yesterday"}, "unreadable timestamp"),
])
def test_bad_readings_are_rejected(change, expected):
    assert expected in validate_reading({**GOOD, **change})


def test_not_a_dict():
    assert validate_reading([1, 2, 3]) == "message is not a JSON object"


def test_ingestion_rejects_without_storing(tmp_path):
    from app.detection.pipeline import DEFAULT_MODEL
    if not DEFAULT_MODEL.exists():
        pytest.skip("train the detector first")
    from app.detection.pipeline import DetectionPipeline
    from app.ingestion.db import TelemetryStore, get_engine
    from app.ingestion.subscriber import Ingestion

    rejects = []
    store = TelemetryStore(get_engine(f"sqlite:///{(tmp_path / 't.db').as_posix()}"))
    ing = Ingestion(on_reject=rejects.append, store=store, pipeline=DetectionPipeline())
    ing.handle_message(json.dumps(GOOD), "hvac/AHU-4/telemetry")
    ing.handle_message(json.dumps({**GOOD, "zone_temp_c": 999.0}), "hvac/AHU-4/telemetry")
    ing.handle_message(b"#@! not json", "hvac/AHU-3/telemetry")
    ing.handle_message(b"\xff\xfe", "hvac/AHU-3/telemetry")
    assert ing.readings == 1 and len(store.get_telemetry("AHU-4", 60)) == 1
    s = ing.rejected.summary()
    assert s["count"] == 3 and len(rejects) == 3
    assert s["recent"][1]["device_id"] == "AHU-3"                    # garbage: device taken from the topic
    assert s["recent"][2]["building_time"] == GOOD["ts"]            # shown in building time


# ------------------------------------------------------------ remote control
@pytest.mark.parametrize("body,cmd", [
    ({"action": "inject", "device_id": "AHU-4", "fault": "filter_blockage"}, "inject AHU-4 filter_blockage"),
    ({"action": "inject", "device_id": "AHU-5", "fault": "refrigerant_leak", "ramp_min": 90}, "inject AHU-5 refrigerant_leak 90"),
    ({"action": "reset"}, "reset all"),
    ({"action": "reset", "device_id": "AHU-2"}, "reset AHU-2"),
    ({"action": "offline", "device_id": "AHU-6"}, "offline AHU-6"),
    ({"action": "jump", "time": "19:00"}, "jump 19:00"),
    ({"action": "speed", "seconds_per_reading": 2}, "speed 2"),
    ({"action": "corrupt", "device_id": "AHU-3", "kind": "text"}, "corrupt AHU-3 text"),
    ({"action": "scenario", "scenario": "demo_filter"}, "run demo_filter"),
])
def test_valid_commands(body, cmd):
    assert SimCommand(**body).to_cmd() == cmd


@pytest.mark.parametrize("body", [
    {"action": "quit"},                                                     # not allowed at all
    {"action": "inject", "device_id": "AHU-4", "fault": "meltdown"},       # unknown fault
    {"action": "inject", "device_id": "AHU-4 && del *", "fault": "filter_blockage"},
    {"action": "inject", "device_id": "all", "fault": "filter_blockage"},  # one unit only
    {"action": "inject"},                                                   # missing fields
    {"action": "jump", "time": "25:99"},
    {"action": "speed", "seconds_per_reading": 0},
    {"action": "scenario", "scenario": "../../secrets"},
    {"action": "inject", "device_id": "AHU-4", "fault": "filter_blockage", "ramp_min": 99999},
])
def test_invalid_commands_never_reach_the_simulator(body):
    with pytest.raises(ValidationError):
        SimCommand(**body)


def test_simulator_refuses_unsafe_remote_commands():
    from hvac_sim.control import check_remote_command
    scen = ["demo_filter"]
    assert check_remote_command("inject AHU-4 filter_blockage", scen) is None
    assert check_remote_command("run demo_filter", scen) is None
    assert "not allowed" in check_remote_command("quit", scen)
    assert "unknown scenario" in check_remote_command("run ../config", scen)


def test_corrupt_command_and_status():
    import main as sim_main
    from hvac_sim.building import load_config

    class Capture:
        def __init__(self):
            self.raw = []

        def publish(self, r):
            pass

        def publish_raw(self, dev, payload):
            self.raw.append((dev, payload))

        def close(self):
            pass

    cap = Capture()
    sim = sim_main.LiveSimulator(load_config(), [cap], datetime(2026, 10, 5, 10, 0), 5, 1)
    for kind in ("spike", "missing", "text", "negative"):
        assert "broken reading" in sim.handle(f"corrupt AHU-3 {kind}")
    assert sim.handle("corrupt AHU-3 explode").startswith("error")
    reasons = []
    for dev, payload in cap.raw:
        try:
            reasons.append(validate_reading(json.loads(payload)))
        except ValueError:
            reasons.append("not json")
    assert all(reasons), "every broken reading must fail validation"

    sim.handle("inject AHU-4 filter_blockage")
    sim.handle("offline AHU-6")
    st = sim.status_dict()
    assert st["faults"][0]["device_id"] == "AHU-4" and st["offline"] == ["AHU-6"]
    assert "demo_filter" in st["scenarios"] and st["clock"].startswith("2026-10-05T10:00")
