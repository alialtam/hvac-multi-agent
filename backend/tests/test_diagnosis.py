from app.llm import FakeProvider, ProviderError
import pytest
import app.knowledge  # noqa: F401  (registers the rules fallback)
from app.agents.diagnosis import diagnose, strip_label
from app.agents.schemas import Diagnosis
from app.llm import llm


@pytest.fixture(autouse=True)
def reset_provider():
    yield
    llm.set_provider("ollama")


def ev(sig, hits=()):
    return {"device_id": "AHU-1", "fault_label": "SECRET",
            "signals": [{"key": k, "value": 1.0, "baseline": 2.0, "z": z} for k, z in sig.items()],
            "rule_hits": list(hits), "window": []}


def test_strip_label():
    assert "fault_label" not in strip_label(ev({}))


def test_rules_fallback_diagnoses_compressor():
    llm.set_provider("rules")
    d, meta = diagnose(ev({"power_kw": -12, "coil_dt_c": -13, "supply_temp_c": 13}))
    assert meta["provider"] == "rules" and d.cause == "compressor_failure" and d.sources


def test_label_never_reaches_prompt_or_context(monkeypatch):
    seen = {}

    def fake(prompt, schema, agent, context=None):
        seen["prompt"], seen["context"] = prompt, context
        return Diagnosis(cause="unknown", cause_text="x", confidence=0.3,
                         evidence=["e"], sources=[]), {"provider": "fake"}

    monkeypatch.setattr(llm, "complete", fake)
    d, _ = diagnose(ev({"airflow_cfm": -15}, ["low_airflow"]))
    assert "SECRET" not in seen["prompt"] and "fault_label" not in seen["context"]
    assert d.sources  # filled from the knowledge search


def test_critique_and_more_evidence_reach_prompt(monkeypatch):
    seen = {}

    def fake(prompt, schema, agent, context=None):
        seen["prompt"] = prompt
        return Diagnosis(cause="unknown", cause_text="x", confidence=0.3,
                         evidence=["e"], sources=["s"]), {"provider": "fake"}

    monkeypatch.setattr(llm, "complete", fake)
    diagnose(ev({}), more_evidence=True, critique="power went up, not down")
    assert "power went up" in seen["prompt"]

GOOD = {"cause": "filter_blockage", "cause_text": "Air filter blockage",
        "confidence": 0.9, "evidence": ["airflow low"], "sources": ["manual: Filter maintenance"]}


def use_fake(monkeypatch, responses):
    fake = FakeProvider(responses)
    monkeypatch.setattr(llm, "providers", {"fake": fake})
    llm.set_provider("fake")
    return fake


def test_bad_json_is_retried_then_succeeds(monkeypatch):
    fake = use_fake(monkeypatch, ["this is not json", GOOD])
    d, meta = diagnose(ev({"airflow_cfm": -15}, ["low_airflow"]))
    assert meta["provider"] == "fake" and meta["attempts"] == 2 and fake.calls == 2
    assert d.cause == "filter_blockage"


def test_provider_failure_falls_back_to_rules_and_is_logged(monkeypatch):
    use_fake(monkeypatch, [ProviderError("timeout"), ProviderError("timeout")])
    records = []
    monkeypatch.setattr(llm, "log_hook", records.append)
    d, meta = diagnose(ev({}, ["sensor_flatline"]))
    assert meta["provider"] == "rules" and "fake" in meta["fallbacks"]
    assert d.cause == "sensor_stuck"
    assert any(r.get("ok") is False and r.get("provider") == "fake" for r in records)


from app.agents.diagnosis import get_peer_units


def _peers(event):
    return [{"device_id": f"AHU-{i}", "state": "critical"} for i in (2, 3, 4)] + \
           [{"device_id": "AHU-5", "state": "normal"}]


def test_get_peer_units_uses_a_peers_function():
    out = get_peer_units(ev({"airflow_cfm": -2.5}), peers=_peers)
    assert out["abnormal"] == ["AHU-2", "AHU-3", "AHU-4"] and out["building_wide_possible"] is True


def test_peer_note_reaches_the_prompt(monkeypatch):
    seen = {}

    def fake(prompt, schema, agent, context=None):
        seen["prompt"] = prompt
        return Diagnosis(cause="unknown", cause_text="x", confidence=0.3,
                         evidence=["e"], sources=["s"]), {"provider": "fake"}

    monkeypatch.setattr(llm, "complete", fake)
    diagnose(ev({"airflow_cfm": -2.5}), peers=_peers)
    assert "AHU-2" in seen["prompt"]

def test_prompt_contains_signature_table(monkeypatch):
    seen = {}

    def fake(prompt, schema, agent, context=None):
        seen["prompt"] = prompt
        return Diagnosis(cause="unknown", cause_text="x", confidence=0.3,
                         evidence=["e"], sources=["s"]), {"provider": "fake"}

    monkeypatch.setattr(llm, "complete", fake)
    diagnose(ev({}))
    p = seen["prompt"]
    assert "COLLAPSES" in p and "RISES" in p and "occupancy is 0" in p


from app.agents.diagnosis import schedule_note


def _win(ts, status="ON", fan=100.0, occ=0):
    return {"window": [{"ts": ts, "status": status, "fan_speed_pct": fan, "occupancy": occ}]}


def test_schedule_note_outside_hours():
    n = schedule_note(_win("2026-09-01T20:02:00Z"))
    assert "20:02" in n and "OUTSIDE" in n and "occupancy 0" in n and "fan 100.0%" in n


def test_schedule_note_inside_hours():
    assert "INSIDE" in schedule_note(_win("2026-09-01T10:03:00Z", occ=21))


def test_schedule_note_without_window():
    assert "no recent readings" in schedule_note({"window": []})


def test_time_check_is_first_and_after_hours_comes_first(monkeypatch):
    seen = {}

    def fake(prompt, schema, agent, context=None):
        seen["prompt"] = prompt
        return Diagnosis(cause="unknown", cause_text="x", confidence=0.3,
                         evidence=["e"], sources=["s"]), {"provider": "fake"}

    monkeypatch.setattr(llm, "complete", fake)
    diagnose({**ev({}), **_win("2026-09-01T20:02:00Z")})
    p = seen["prompt"]
    assert p.index("Time check:") < p.index("Pick the most likely cause")
    assert p.index("- after_hours_waste") < p.index("- filter_blockage:")
    assert "never filter_blockage" in p
