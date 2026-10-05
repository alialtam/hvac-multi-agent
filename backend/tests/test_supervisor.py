import pytest

from app.agents.schemas import Critique, Diagnosis, EnergyImpact, Recommendation
from app.agents.supervisor import build_graph, run_incident
from app.llm import FakeProvider, llm

META = {"provider": "fake", "ms": 1, "tokens": 0}
REC = Recommendation(priority="HIGH", action="Fix it", checklist=["a"], estimated_downtime_min=60)


@pytest.fixture(autouse=True)
def rules_supervisor():
    llm.set_provider("rules")
    yield
    llm.set_provider("ollama")


def event(hits=()):
    return {"event_id": "evt1", "device_id": "AHU-1", "severity": "HIGH",
            "rule_hits": list(hits), "signals": [], "window": []}


class Fakes:
    def __init__(self, causes=("compressor_failure",), agrees=lambda c: True, maint=None):
        self.causes, self.agrees, self.maint = list(causes), agrees, maint
        self.order, self.diag_calls = [], []

    def diagnose(self, ev, more_evidence=False, critique=None):
        self.order.append("diagnosis")
        self.diag_calls.append({"more": more_evidence, "critique": critique})
        cause = self.causes[min(len(self.diag_calls) - 1, len(self.causes) - 1)]
        return Diagnosis(cause=cause, cause_text=cause, confidence=0.9, evidence=["e"], sources=["s"]), META

    def energy(self, ev, diagnosis):
        self.order.append("energy")
        return EnergyImpact(extra_kwh_per_day=1.0, cost_per_day=8.5, explanation="x"), META

    def critique(self, ev, diagnosis):
        ok = self.agrees(diagnosis.cause)
        return Critique(agrees=ok, issues=[] if ok else ["power went up"])

    def maintenance(self, ev, diagnosis, energy):
        self.order.append("maintenance")
        if self.maint:
            return self.maint(self.order.count("maintenance"))
        return REC, None, META

    def graph(self):
        return build_graph(self.diagnose, self.energy, self.critique, self.maintenance)


def test_compressor_goes_through_all_three():
    f = Fakes()
    final = run_incident(event(), f.graph())
    assert f.order == ["diagnosis", "energy", "maintenance"]
    assert final["state"] == "awaiting_approval" and final["recommendation"]


def test_sensor_stuck_skips_energy():
    f = Fakes(causes=("sensor_stuck",))
    run_incident(event(), f.graph())
    assert f.order == ["diagnosis", "maintenance"]


def test_after_hours_goes_to_energy_first():
    f = Fakes(causes=("after_hours_waste",))
    final = run_incident(event(["running_unoccupied"]), f.graph())
    assert f.order[:2] == ["energy", "diagnosis"]
    assert final["state"] == "awaiting_approval"


def test_conflict_resolved_after_one_revision():
    f = Fakes(causes=("compressor_failure", "filter_blockage"),
              agrees=lambda c: c != "compressor_failure")
    final = run_incident(event(), f.graph())
    assert f.order == ["diagnosis", "energy", "diagnosis", "energy", "maintenance"]
    assert final["revisions"] == 1 and not final["escalated"]
    assert final["diagnosis"]["cause"] == "filter_blockage"
    assert "power went up" in f.diag_calls[1]["critique"]


def test_conflict_escalates_after_two_revisions():
    f = Fakes(agrees=lambda c: False)
    final = run_incident(event(), f.graph())
    assert f.order.count("diagnosis") == 3 and "maintenance" not in f.order
    assert final["revisions"] == 2 and final["escalated"] and final["recommendation"] is None
    assert final["state"] == "awaiting_approval"


def test_low_confidence_asks_for_more_evidence():
    f = Fakes(maint=lambda n: (None, "confidence 0.4 is below 0.6", META) if n == 1 else (REC, None, META))
    final = run_incident(event(), f.graph())
    assert f.order == ["diagnosis", "energy", "maintenance", "diagnosis", "maintenance"]
    assert f.diag_calls[1]["more"] is True and final["recommendation"]


def test_refused_twice_escalates():
    f = Fakes(maint=lambda n: (None, "not enough data", META))
    final = run_incident(event(), f.graph())
    assert f.order == ["diagnosis", "energy", "maintenance", "diagnosis", "maintenance"]
    assert final["escalated"] and final["recommendation"] is None


def test_illegal_llm_route_is_overridden(monkeypatch):
    fake = FakeProvider([{"next": "maintenance", "reason": "skip ahead"}])
    monkeypatch.setattr(llm, "providers", {"fake": fake})
    llm.set_provider("fake")
    f = Fakes()
    final = run_incident(event(), f.graph())
    routes = [t for t in final["trace"] if t["type"] == "route"]
    assert routes[0]["override"] is True and routes[0]["receiver"] == "diagnosis"
    assert f.order[0] == "diagnosis"


def test_fault_label_is_stripped():
    ev = event()
    ev["fault_label"] = "SECRET"
    final = run_incident(ev, Fakes().graph())
    assert "fault_label" not in final["event"] and "SECRET" not in str(final["trace"])