"""Each fault must leave its physical signature in the data."""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from hvac_sim.building import Building, load_config

SCHEMA = json.loads((Path(__file__).resolve().parents[2] / "contracts" / "telemetry.schema.json").read_text())


def run(fault=None, at_h=11, minutes=90, start=datetime(2026, 9, 1)):
    b = Building(load_config())
    b.warm_up(start)
    t, rows = start, []
    while t < start + timedelta(hours=24):
        if fault and t == start + timedelta(hours=at_h):
            b.get("AHU-4").inject(fault, t)
        rows += b.step(t)
        t += timedelta(minutes=1)
    d = pd.DataFrame(rows)
    d = d[d.device_id == "AHU-4"].reset_index(drop=True)
    i = at_h * 60
    return d.iloc[i - 30:i], d.iloc[i + minutes - 30:i + minutes]


def test_reading_matches_contract():
    b = Building(load_config())
    r = b.step(datetime(2026, 9, 1, 10))[0]
    assert set(SCHEMA["required"]) <= set(r)
    assert set(r) <= set(SCHEMA["properties"])
    assert r["fault_label"] in SCHEMA["properties"]["fault_label"]["enum"]


def test_normal_day_holds_setpoint():
    before, _ = run()
    assert abs(before.zone_temp_c.mean() - 23.0) < 0.5


def test_filter_blockage_signature():
    before, after = run("filter_blockage")
    assert after.airflow_cfm.mean() < 0.7 * before.airflow_cfm.mean()
    assert after.power_kw.mean() > before.power_kw.mean()          # compressor compensates


def test_compressor_failure_signature():
    before, after = run("compressor_failure", minutes=60)
    assert after.power_kw.mean() < 0.3 * before.power_kw.mean()
    assert after.zone_temp_c.mean() > before.zone_temp_c.mean() + 2


def test_sensor_stuck_signature():
    _, after = run("sensor_stuck")
    assert after.zone_temp_c.nunique() == 1


def test_after_hours_signature():
    before, after = run("after_hours_waste", at_h=20, minutes=60)
    assert (before.status == "OFF").all() and (after.status == "ON").all()
    assert (after.occupancy == 0).all()


def test_unknown_fault_rejected():
    b = Building(load_config())
    with pytest.raises(ValueError):
        b.get("AHU-1").inject("melted", datetime(2026, 9, 1))
