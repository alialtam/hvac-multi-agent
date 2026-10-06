import pytest

from app.agents.schemas import Critique, Diagnosis, EnergyImpact, Recommendation
from app.agents.supervisor import _cfg, build_graph, get_incident
from app.api.runtime import AgentRuntime
from app.llm import llm

META = {"provider": "fake", "ms": 1, "tokens": 0}
REC = Recommendation(priority="HIGH", action="Fix it", checklist=["a"], estimated_downtime_min=60)
TRIAGE = [{"ts": "t0", "incident_id": "inc_0001", "from": "anomaly_detection", "to": "tool:other_units",
           "type": "tool_call", "content": {"result": {"note": "x"}}, "llm_provider": "rules", "duration_ms": 1}]


def event(**extra):
    return {"event_id": "e1", "device_id": "AHU-1", "severity": "HIGH", "rule_hits": [],
            "signals": [], "window": [], **extra}


class Fakes:
    def __init__(self, agrees=True, flaky=False):
        self.agrees, self.flaky, self.n = agrees, flaky, 0

    def diagnose(self, ev, more_evidence=False, critique=None):
        self.n += 1
        if self.flaky and self.n == 1:
            raise RuntimeError("boom")
        return Diagnosis(cause="compressor_failure", cause_text="c", confidence=0.9,
                         evidence=["e"], sources=["s"]), META

    def energy(self, ev, d):
        return EnergyImpact(extra_kwh_per_day=1.0, cost_per_day=8.5, explanation="x"), META

    def critique(self, ev, d):
        return Critique(agrees=self.agrees, issues=[] if self.agrees else ["power went up"])

    def maintenance(self, ev, d, e):
        return REC, None, META


@pytest.fixture
def mk():
    llm.set_provider("rules")
    made = []

    def build(**kw):
        f = Fakes(**kw)
        inc, traces, activity, tickets, pub = {}, {}, [], [], []
        rt = AgentRuntime(inc, traces, activity, tickets, lambda k, d: pub.append((k, d)),
                          graph=build_graph(f.diagnose, f.energy, f.critique, f.maintenance))
        rt.start()
        made.append(rt)
        return rt, inc, traces, tickets, pub

    yield build
    for rt in made:
        rt.stop()
    llm.set_provider("ollama")


def add(inc, i="inc_0001"):
    inc[i] = {"id": i, "device_id": "AHU-1", "state": "investigating", "trace": [], "ticket_id": None}


def test_run_fills_incident_and_trace(mk):
    rt, inc, traces, _, pub = mk()
    add(inc)
    rt.add_triage("inc_0001", TRIAGE)
    rt.submit("inc_0001", event())
    rt.wait_idle()
    i = inc["inc_0001"]
    assert i["state"] == "awaiting_approval" and i["diagnosis"]["cause"] == "compressor_failure"
    assert i["recommendation"] and i["energy_cost_per_day"] == 8.5
    lines = traces["inc_0001"]
    assert lines[0]["to"] == "tool:other_units"                    # triage steps come first
    assert all({"from", "to", "content", "type"} <= set(x) and "sender" not in x for x in lines)
    assert any(x["type"] == "route" for x in lines)
    assert all({"ts", "agent", "message"} <= set(m) for m in i["trace"])
    assert {"incident", "agent_step"} <= {k for k, _ in pub}


def test_fault_label_is_stripped(mk):
    rt, inc, *_ = mk()
    add(inc)
    rt.submit("inc_0001", event(fault_label="SECRET"))
    rt.wait_idle()
    assert "fault_label" not in get_incident(rt.graph, "inc_0001")["event"]


def test_approve_creates_a_ticket(mk):
    rt, inc, _, tickets, _ = mk()
    add(inc)
    rt.submit("inc_0001", event())
    rt.wait_idle()
    out = rt.approve("inc_0001", "Ali", "go ahead")
    assert out["state"] == "approved" and out["ticket_id"] == "TCK-0001"
    assert tickets[0]["incident_id"] == "inc_0001" and tickets[0]["priority"] == "HIGH"
    with pytest.raises(ValueError):
        rt.approve("inc_0001", "Ali")


def test_reject_needs_a_reason_and_keeps_waiting(mk):
    rt, inc, *_ = mk()
    add(inc)
    rt.submit("inc_0001", event())
    rt.wait_idle()
    with pytest.raises(ValueError):
        rt.reject("inc_0001", "Ali", "   ")
    assert inc["inc_0001"]["state"] == "awaiting_approval"


def test_reject_replans_and_waits_again(mk):
    rt, inc, traces, *_ = mk()
    add(inc)
    rt.submit("inc_0001", event())
    rt.wait_idle()
    assert rt.reject("inc_0001", "Ali", "filter was replaced")["state"] == "investigating"
    rt.wait_idle()
    assert inc["inc_0001"]["state"] == "awaiting_approval" and inc["inc_0001"]["replans"] == 1
    assert any(x["type"] == "replan" for x in traces["inc_0001"])


def test_cannot_approve_without_a_recommendation(mk):
    rt, inc, *_ = mk(agrees=False)
    add(inc)
    rt.submit("inc_0001", event())
    rt.wait_idle()
    assert inc["inc_0001"]["state"] == "awaiting_approval" and inc["inc_0001"]["recommendation"] is None
    with pytest.raises(ValueError):
        rt.approve("inc_0001", "Ali")


def test_worker_survives_a_crash(mk):
    rt, inc, *_ = mk(flaky=True)
    add(inc, "inc_0001")
    add(inc, "inc_0002")
    rt.submit("inc_0001", event(event_id="a"))
    rt.submit("inc_0002", event(event_id="b"))
    rt.wait_idle()
    assert inc["inc_0002"]["state"] == "awaiting_approval"
    assert any("failed" in m["message"] for m in inc["inc_0001"]["trace"])

def test_full_flow_through_http(monkeypatch):
    from fastapi.testclient import TestClient
    import app.api.main as main
    llm.set_provider("rules")
    f = Fakes()
    rt = AgentRuntime(main.incidents, main.traces, main.activity, main.ticket_store, lambda k, d: None,
                      graph=build_graph(f.diagnose, f.energy, f.critique, f.maintenance))
    rt.start()
    monkeypatch.setattr(main, "runtime", rt)
    try:
        main.incidents["inc_9001"] = {"id": "inc_9001", "device_id": "AHU-1", "state": "investigating",
                                      "trace": [], "ticket_id": None}
        rt.submit("inc_9001", event())
        rt.wait_idle()
        c = TestClient(main.app)                      # no "with": the lifespan (MQTT) is not started
        assert c.get("/incidents/inc_9001").json()["state"] == "awaiting_approval"
        assert c.post("/incidents/nope/approve", json={"operator": "Ali"}).status_code == 404
        assert c.post("/incidents/inc_9001/reject", json={"operator": "Ali", "reason": " "}).status_code == 422
        r = c.post("/incidents/inc_9001/approve", json={"operator": "Ali"})
        assert r.status_code == 200 and r.json()["ticket_id"]
        assert c.post("/incidents/inc_9001/approve", json={"operator": "Ali"}).status_code == 409
        assert c.get("/tickets").json()[0]["incident_id"] == "inc_9001"
        assert c.get("/incidents/inc_9001/trace").json()
    finally:
        rt.stop()
        main.incidents.pop("inc_9001", None)
        main.traces.pop("inc_9001", None)
        main.ticket_store.clear()
        llm.set_provider("ollama")