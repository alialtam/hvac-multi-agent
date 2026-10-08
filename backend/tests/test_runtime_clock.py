"""The dashboard timeline uses the building clock, like the detection steps."""
from app.agents.supervisor import build_graph
from app.api.runtime import AgentRuntime


def _runtime(clock):
    return AgentRuntime({}, {}, [], [], publish=lambda k, d: None, graph=build_graph(), clock=clock)


def test_timeline_uses_building_clock():
    rt = _runtime(lambda: "2026-10-07T10:32:00Z")
    assert rt._shown_time("2026-10-07T09:41:00+00:00") == "2026-10-07T10:32:00Z"


def test_falls_back_without_building_clock():
    rt = _runtime(lambda: None)                     # no reading yet
    assert rt._shown_time("2026-10-07T09:41:00+00:00") == "2026-10-07T09:41:00+00:00"
    assert _runtime(None)._shown_time()             # no clock at all: real time
