import pytest

from app.agents.energy import analyse_energy  # noqa: F401  (keeps the import chain honest)
from app.agents.maintenance import (LOCKOUT, draft_ticket, recommend, request_more_evidence,
                                    search_procedures, set_priority)
from app.agents.schemas import Critique, Diagnosis, EnergyImpact, Recommendation
from app.agents.supervisor import build_graph, run_incident
from app.llm import FakeProvider, llm

META = {"provider": "fake", "ms": 1, "tokens": 0}


@pytest.fixture(autouse=True)
def reset():
    llm.set_provider("rules")
    yield
    llm.set_provider("ollama")


def diag(cause="compressor_failure", conf=0.9):
    return Diagnosis(cause=cause, cause_text=f"{cause} text", confidence=conf, evidence=["e"], sources=[])


def event(severity="HIGH"):
    return {"event_id": "evt1", "device_id": "AHU-1", "zone": "Zone-1", "severity": severity,
            "rule_hits": [], "signals": [], "window": []}


def test_refuses_below_threshold():
    rec, reason, meta = recommend(event(), diag(conf=0.59))
    assert rec is None and "0.6" in reason and meta["provider"] == "rules"


def test_accepts_exactly_threshold():
    rec, reason, _ = recommend(event(), diag(conf=0.6))
    assert reason is None and rec is not None


@pytest.mark.parametrize("cause,severity,expected", [
    ("compressor_failure", "HIGH", "HIGH"), ("compressor_failure", "LOW", "HIGH"),
    ("filter_blockage", "MEDIUM", "MEDIUM"), ("filter_blockage", "HIGH", "HIGH"),
    ("sensor_stuck", "LOW", "LOW"), ("sensor_stuck", "HIGH", "MEDIUM")])
def test_priority_rules(cause, severity, expected):
    assert set_priority(event(severity), diag(cause)) == expected


def test_rules_fallback_gives_full_recommendation():
    rec, reason, meta = recommend(event(), diag("compressor_failure"))
    assert meta["provider"] == "rules" and reason is None
    assert rec.priority == "HIGH" and rec.estimated_downtime_min == 240
    assert rec.checklist[0] == LOCKOUT


def test_code_overrides_llm_priority_and_downtime(monkeypatch):
    fake = FakeProvider([{"priority": "LOW", "action": "Do it", "checklist": ["Step one"],
                          "estimated_downtime_min": 1}])
    monkeypatch.setattr(llm, "providers", {"fake": fake})
    llm.set_provider("fake")
    rec, _, meta = recommend(event(), diag("compressor_failure"))
    assert meta["provider"] == "fake" and rec.action == "Do it"
    assert rec.priority == "HIGH" and rec.estimated_downtime_min == 240
    assert rec.checklist[0] == LOCKOUT and "Step one" in rec.checklist


def test_sensor_stuck_has_no_lockout_step():
    rec, _, _ = recommend(event("LOW"), diag("sensor_stuck"))
    assert LOCKOUT not in rec.checklist and rec.estimated_downtime_min == 30


def test_search_procedures_returns_manuals_only():
    hits = search_procedures("filter_blockage")
    assert hits and all(h["source"].startswith("manual") for h in hits)


def test_draft_ticket_fields():
    d = diag("filter_blockage")
    rec, _, _ = recommend(event(), d)
    t = draft_ticket(event(), rec, d)
    assert t["device_id"] == "AHU-1" and t["priority"] == rec.priority and t["checklist"]
    assert t["estimated_downtime_min"] == 45


def test_request_more_evidence_none_when_confident():
    assert request_more_evidence(diag(conf=0.9)) is None


def test_label_never_reaches_prompt(monkeypatch):
    seen = {}

    def fake(prompt, schema, agent, context=None):
        seen["prompt"], seen["context"] = prompt, context
        return Recommendation(priority="LOW", action="a", checklist=["c"], estimated_downtime_min=1), META

    monkeypatch.setattr(llm, "complete", fake)
    ev = event()
    ev["fault_label"] = "SECRET"
    recommend(ev, diag())
    assert "SECRET" not in seen["prompt"] and "fault_label" not in str(seen["context"])


# ---- with the real Supervisor ----
def _graph(conf_first, conf_after):
    calls = {"n": 0}

    def fake_diagnose(ev, more_evidence=False, critique=None):
        calls["n"] += 1
        return diag("compressor_failure", conf_after if more_evidence else conf_first), META

    def fake_energy(ev, d):
        return EnergyImpact(extra_kwh_per_day=1.0, cost_per_day=8.5, explanation="x"), META

    def fake_critique(ev, d):
        return Critique(agrees=True)

    return build_graph(fake_diagnose, fake_energy, fake_critique), calls


def test_supervisor_with_real_maintenance_happy_path():
    graph, _ = _graph(0.9, 0.9)
    final = run_incident(event(), graph)
    assert final["recommendation"]["priority"] == "HIGH" and final["state"] == "awaiting_approval"


def test_supervisor_evidence_loop_with_real_maintenance():
    graph, calls = _graph(0.4, 0.9)
    final = run_incident(event(), graph)
    kinds = [t["type"] for t in final["trace"]]
    assert "request_more_evidence" in kinds and calls["n"] == 2
    assert final["recommendation"] is not None and not final["escalated"]