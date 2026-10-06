import pytest

from app.agents.schemas import Critique, Diagnosis, EnergyImpact, Recommendation
from app.agents.supervisor import (build_graph, get_checkpointer, get_incident,
                                   resume_incident, run_incident, _cfg)
from app.llm import llm

META = {"provider": "fake", "ms": 1, "tokens": 0}
REC = Recommendation(priority="HIGH", action="Fix it", checklist=["a"], estimated_downtime_min=60)


@pytest.fixture(autouse=True)
def rules_supervisor():
    llm.set_provider("rules")
    yield
    llm.set_provider("ollama")


def event():
    return {"event_id": "evt1", "device_id": "AHU-1", "severity": "HIGH",
            "rule_hits": [], "signals": [], "window": []}


class Fakes:
    def __init__(self):
        self.order, self.diag_calls = [], []

    def diagnose(self, ev, more_evidence=False, critique=None):
        self.order.append("diagnosis")
        self.diag_calls.append({"critique": critique})
        return Diagnosis(cause="compressor_failure", cause_text="c", confidence=0.9,
                         evidence=["e"], sources=["s"]), META

    def energy(self, ev, d):
        self.order.append("energy")
        return EnergyImpact(extra_kwh_per_day=1.0, cost_per_day=8.5, explanation="x"), META

    def critique(self, ev, d):
        return Critique(agrees=True)

    def maintenance(self, ev, d, e):
        self.order.append("maintenance")
        return REC, None, META

    def graph(self, checkpointer=None):
        return build_graph(self.diagnose, self.energy, self.critique, self.maintenance,
                           checkpointer=checkpointer)


def waiting(g):
    return "approval_wait" in (g.get_state(_cfg("evt1")).next or ())


def test_graph_pauses_for_the_operator():
    g = Fakes().graph()
    final = run_incident(event(), g)
    assert final["state"] == "awaiting_approval" and waiting(g)
    assert any(t["type"] == "escalate_to_human" for t in final["trace"])


def test_approve_finishes_the_incident():
    g = Fakes().graph()
    run_incident(event(), g)
    final = resume_incident(g, "evt1", "approve", "Ali", note="go ahead")
    assert final["state"] == "approved" and not waiting(g)
    appr = [t for t in final["trace"] if t["type"] == "approval"]
    assert appr and appr[0]["sender"] == "Ali"


def test_reject_replans_with_the_reason_as_evidence():
    f = Fakes()
    g = f.graph()
    run_incident(event(), g)
    final = resume_incident(g, "evt1", "reject", "Ali", reason="filter was replaced yesterday")
    assert final["state"] == "awaiting_approval" and final["replans"] == 1 and waiting(g)
    assert "filter was replaced yesterday" in f.diag_calls[-1]["critique"]
    assert f.order.count("diagnosis") == 2 and final["recommendation"]
    assert any(t["type"] == "replan" for t in final["trace"])


def test_reject_then_approve():
    g = Fakes().graph()
    run_incident(event(), g)
    resume_incident(g, "evt1", "reject", "Ali", reason="wrong unit")
    final = resume_incident(g, "evt1", "approve", "Ali")
    assert final["state"] == "approved" and final["replans"] == 1 and final["operator_feedback"] is None


def test_third_rejection_closes_the_incident():
    g = Fakes().graph()
    run_incident(event(), g)
    resume_incident(g, "evt1", "reject", "Ali", reason="no 1")
    resume_incident(g, "evt1", "reject", "Ali", reason="no 2")
    final = resume_incident(g, "evt1", "reject", "Ali", reason="no 3")
    assert final["state"] == "rejected" and final["replans"] == 2 and not waiting(g)
    assert any(t["type"] == "rejected" for t in final["trace"])


def test_rejection_needs_a_reason_and_keeps_waiting():
    g = Fakes().graph()
    run_incident(event(), g)
    with pytest.raises(ValueError):
        resume_incident(g, "evt1", "reject", "Ali", reason="  ")
    assert waiting(g) and get_incident(g, "evt1")["state"] == "awaiting_approval"


def test_unknown_decision_and_double_resume_are_refused():
    g = Fakes().graph()
    run_incident(event(), g)
    with pytest.raises(ValueError):
        resume_incident(g, "evt1", "maybe", "Ali")
    resume_incident(g, "evt1", "approve", "Ali")
    with pytest.raises(ValueError):
        resume_incident(g, "evt1", "approve", "Ali")


def test_waiting_incident_survives_a_restart(tmp_path):
    pytest.importorskip("langgraph.checkpoint.sqlite")
    db = str(tmp_path / "cp.sqlite")
    run_incident(event(), Fakes().graph(get_checkpointer(db)))
    restarted = Fakes().graph(get_checkpointer(db))      # a new process, same database file
    assert waiting(restarted)
    final = resume_incident(restarted, "evt1", "approve", "Ali")
    assert final["state"] == "approved"

def test_on_update_reports_each_step():
    seen = []
    run_incident(event(), Fakes().graph(), on_update=lambda s: seen.append(s.get("state")))
    assert len(seen) >= 4 and seen[-1] == "awaiting_approval"    

def test_replan_clears_stale_energy():
    f = Fakes()
    causes = iter(["compressor_failure", "sensor_stuck"])

    def diag(ev, more_evidence=False, critique=None):
        c = next(causes)
        return Diagnosis(cause=c, cause_text=c, confidence=0.9, evidence=["e"], sources=["s"]), META

    g = build_graph(diag, f.energy, f.critique, f.maintenance)
    run_incident(event(), g)
    final = resume_incident(g, "evt1", "reject", "Ali", reason="it is the sensor")
    assert final["diagnosis"]["cause"] == "sensor_stuck" and final["energy"] is None
