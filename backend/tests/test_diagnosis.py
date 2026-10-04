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