#this is a test suite for the energy analysis module of the HVAC system. It includes tests for computing extra energy consumption, checking after-hours operation, critiquing diagnoses, and ensuring that sensitive information does not reach the language model prompt.
import json
from pathlib import Path

import pytest

from app.agents.energy import (analyse_energy, check_after_hours, compute_extra_kwh,
                               critique_diagnosis, get_tariff)
from app.agents.schemas import Diagnosis, EnergyImpact
from app.llm import FakeProvider, llm

SAMPLE = Path(__file__).parents[2] / "contracts/sample_events/evt_AHU-1_20260930T100300.json"


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.delenv("ENERGY_TARIFF_INR", raising=False)
    yield
    llm.set_provider("ollama")


def diag(cause):
    return Diagnosis(cause=cause, cause_text="x", confidence=0.9, evidence=["e"], sources=[])


def ev(sig, window=None):
    return {"device_id": "AHU-1", "rule_hits": [], "window": window or [],
            "signals": [{"key": k, "value": v, "baseline": b, "z": z} for k, (v, b, z) in sig.items()]}


def test_real_compressor_event_numbers():
    r = compute_extra_kwh(json.loads(SAMPLE.read_text()), "compressor_failure")
    assert r["extra_kwh_per_day"] == pytest.approx(-115.68, abs=0.01)
    assert r["cost_per_day"] == pytest.approx(-983.28, abs=0.05)


def test_tariff_from_env(monkeypatch):
    assert get_tariff() == 8.5
    monkeypatch.setenv("ENERGY_TARIFF_INR", "10")
    assert get_tariff() == 10.0
    monkeypatch.setenv("ENERGY_TARIFF_INR", "abc")
    assert get_tariff() == 8.5


def test_after_hours_uses_12_hours():
    win = [{"status": "ON", "occupancy": 0, "fan_speed_pct": 100}]
    e = ev({"power_kw": (5.5, 1.0, 9)}, win)
    assert check_after_hours(e) is True
    assert compute_extra_kwh(e, "after_hours_waste")["extra_kwh_per_day"] == pytest.approx(54.0)


def test_check_after_hours_false_or_none():
    assert check_after_hours(ev({}, [{"status": "ON", "occupancy": 12, "fan_speed_pct": 100}])) is False
    assert check_after_hours(ev({})) is None


def test_critique_agrees_on_real_compressor_event():
    c = critique_diagnosis(json.loads(SAMPLE.read_text()), diag("compressor_failure"))
    assert c.agrees and not c.issues


def test_critique_compressor_but_power_went_up():
    e = ev({"power_kw": (7.0, 5.0, 6.0), "airflow_cfm": (1500, 2250, -8.0)})
    c = critique_diagnosis(e, diag("compressor_failure"))
    assert not c.agrees and c.suggested_cause == "filter_blockage" and c.issues


def test_critique_filter_but_power_collapsed():
    c = critique_diagnosis(ev({"power_kw": (0.8, 5.6, -12.0)}), diag("filter_blockage"))
    assert not c.agrees and c.suggested_cause == "compressor_failure"


def test_critique_refrigerant_leak_high_power_is_accepted():
    c = critique_diagnosis(ev({"power_kw": (7.0, 5.0, 6.0)}), diag("refrigerant_leak"))
    assert c.agrees


def test_critique_refrigerant_leak_but_power_collapsed():
    c = critique_diagnosis(ev({"power_kw": (0.8, 5.6, -12.0)}), diag("refrigerant_leak"))
    assert not c.agrees and c.suggested_cause == "compressor_failure"


def test_critique_after_hours_but_occupied():
    e = ev({}, [{"status": "ON", "occupancy": 15, "fan_speed_pct": 100}])
    assert not critique_diagnosis(e, diag("after_hours_waste")).agrees


def test_critique_unknown_always_agrees():
    assert critique_diagnosis(ev({}), diag("unknown")).agrees


def test_llm_only_writes_the_explanation(monkeypatch):
    fake = FakeProvider([{"extra_kwh_per_day": 999, "cost_per_day": 999, "currency": "USD",
                          "explanation": "Cooling lost, power fell."}])
    monkeypatch.setattr(llm, "providers", {"fake": fake})
    llm.set_provider("fake")
    out, meta = analyse_energy(json.loads(SAMPLE.read_text()), diag("compressor_failure"))
    assert meta["provider"] == "fake" and out.explanation == "Cooling lost, power fell."
    assert out.extra_kwh_per_day == pytest.approx(-115.68, abs=0.01) and out.currency == "INR"


def test_rules_fallback_still_gives_numbers():
    llm.set_provider("rules")
    out, meta = analyse_energy(json.loads(SAMPLE.read_text()), diag("compressor_failure"))
    assert meta["provider"] == "rules" and out.explanation
    assert out.extra_kwh_per_day == pytest.approx(-115.68, abs=0.01)


def test_label_never_reaches_prompt(monkeypatch):
    seen = {}

    def fake(prompt, schema, agent, context=None):
        seen["prompt"], seen["context"] = prompt, context
        return EnergyImpact(extra_kwh_per_day=0, cost_per_day=0, explanation="x"), {"provider": "fake"}

    monkeypatch.setattr(llm, "complete", fake)
    e = json.loads(SAMPLE.read_text())
    e["fault_label"] = "SECRET"
    analyse_energy(e, diag("compressor_failure"))
    assert "SECRET" not in seen["prompt"] and "fault_label" not in str(seen["context"])