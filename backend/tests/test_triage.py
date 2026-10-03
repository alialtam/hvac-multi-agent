"""Triage agent: rules fallback, LLM tool loop (with a fake LLM), failure handling, worker."""
import json
import threading
from pathlib import Path

import pytest

from app.detection.triage import TriageWorker, triage_event
from app.detection.triage_tools import TriageTools

EVENTS = Path(__file__).resolve().parents[2] / "contracts" / "sample_events"


def load(device: str) -> dict:
    return json.loads(next(EVENTS.glob(f"evt_{device}_*.json")).read_text())


class FakeLLM:
    """Plays back scripted replies, records what it was sent. No network, no key."""

    provider, model = "fake", "fake-1"

    def __init__(self, replies):
        self.replies, self.sent = list(replies), []

    def chat(self, messages, tools, tool_choice="auto"):
        self.sent.append({"messages": list(messages), "tool_choice": tool_choice})
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return {"content": r.get("content"), "duration_ms": 5,
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
                "tool_calls": [{"id": f"c{i}", "name": n, "arguments": json.dumps(a)}
                               for i, (n, a) in enumerate(r.get("calls", []))]}


GOOD = {"verdict": "equipment_fault", "suspected_area": "airflow", "severity": "HIGH", "confidence": 0.85,
        "summary": "Airflow on AHU-4 dropped while the fan speed stayed the same.",
        "key_evidence": ["airflow 1,866 vs 2,250 CFM", "other units normal"], "recommend_next": "diagnosis"}


@pytest.mark.parametrize("device,area,next_", [
    ("AHU-1", "cooling", "diagnosis"),      # compressor failure
    ("AHU-2", "schedule", "energy"),        # after-hours waste
    ("AHU-3", "sensor", "maintenance"),     # stuck sensor
    ("AHU-4", "airflow", "diagnosis"),      # filter blockage
    ("AHU-5", "cooling", "diagnosis"),      # refrigerant leak
])
def test_rules_triage_on_sample_events(device, area, next_):
    r = triage_event(load(device), provider="rules")
    assert r["suspected_area"] == area and r["recommend_next"] == next_
    assert r["provider"] == "rules" and r["agrees_with_rules"]
    assert r["key_evidence"] and r["summary"]
    assert {"signal_deviations", "sensor_health"} <= set(r["tools_used"])


def test_llm_chooses_tools_then_submits():
    llm = FakeLLM([{"calls": [("sensor_health", {}), ("other_units", {})]},
                   {"calls": [("recent_trend", {"minutes": 20})]},
                   {"calls": [("submit_triage", GOOD)]}])
    r = triage_event(load("AHU-4"), provider="openai", client=llm)
    assert r["provider"] == "fake" and r["verdict"] == "equipment_fault"
    assert r["tools_used"] == ["sensor_health", "other_units", "recent_trend"]
    assert r["agrees_with_rules"] and r["tokens"] == 360
    # tool results were sent back to the LLM, one per call
    tool_msgs = [m for m in llm.sent[-1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 3 and "note" in json.loads(tool_msgs[0]["content"])
    types = [s["type"] for s in r["steps"]]
    assert types.count("llm_call") == 3 and types[-1] == "decision"
    assert all(s["from"] == "anomaly_detection" for s in r["steps"])


def test_invalid_answer_is_retried_once():
    bad = {**GOOD, "confidence": 3}
    llm = FakeLLM([{"calls": [("submit_triage", bad)]}, {"calls": [("submit_triage", GOOD)]}])
    r = triage_event(load("AHU-4"), provider="openai", client=llm)
    assert r["provider"] == "fake" and r["confidence"] == 0.85
    assert any(s["type"] == "retry" for s in r["steps"])


def test_llm_failure_falls_back_to_rules():
    llm = FakeLLM([TimeoutError("read timed out")])
    r = triage_event(load("AHU-4"), provider="openai", client=llm)
    assert r["provider"] == "rules" and r["suspected_area"] == "airflow"
    assert r["steps"][0]["type"] == "fallback" and "TimeoutError" in r["steps"][0]["content"]["reason"]


def test_two_invalid_answers_fall_back_to_rules():
    bad = {**GOOD, "verdict": "broken"}
    llm = FakeLLM([{"calls": [("submit_triage", bad)]}, {"calls": [("submit_triage", bad)]}])
    r = triage_event(load("AHU-4"), provider="openai", client=llm)
    assert r["provider"] == "rules"


def test_missing_api_key_uses_rules(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    r = triage_event(load("AHU-2"), provider="openai")
    assert r["provider"] == "rules" and r["verdict"] == "operational_waste"
    assert r["steps"][0]["type"] == "fallback"


def test_plain_json_answer_is_accepted():
    llm = FakeLLM([{"content": "Here you go: " + json.dumps(GOOD)}])
    assert triage_event(load("AHU-4"), provider="ollama", client=llm)["provider"] == "fake"


def test_repeated_checks_are_not_rerun_and_force_a_decision():
    # seen live: the model asked for the same tools again and again
    llm = FakeLLM([{"calls": [("signal_deviations", {}), ("recent_trend", {})]},
                   {"calls": [("signal_deviations", {}), ("recent_trend", {"minutes": 20})]},
                   {"calls": [("submit_triage", GOOD)]}])
    r = triage_event(load("AHU-4"), provider="openai", client=llm)
    assert r["provider"] == "fake"
    assert r["tools_used"] == ["signal_deviations", "recent_trend"]
    assert sum(s["type"] == "tool_call" for s in r["steps"]) == 2          # repeats are not logged twice
    assert "already ran" in llm.sent[-1]["messages"][-1]["content"]
    assert llm.sent[-1]["tool_choice"]["function"]["name"] == "submit_triage"   # 2 repeats -> must decide


def test_last_round_forces_a_decision():
    tools = ["signal_deviations", "recent_trend", "sensor_health", "other_units", "schedule_context"]
    llm = FakeLLM([{"calls": [(t, {})]} for t in tools] + [{"calls": [("submit_triage", GOOD)]}])
    r = triage_event(load("AHU-4"), provider="openai", client=llm)
    assert r["provider"] == "fake" and len(r["tools_used"]) == 5
    assert llm.sent[-1]["tool_choice"]["function"]["name"] == "submit_triage"


def test_building_wide_when_many_units_abnormal():
    peers = lambda e: [{"device_id": d, "state": "warning"} for d in ("AHU-1", "AHU-2", "AHU-3")]  # noqa: E731
    r = triage_event(load("AHU-4"), provider="rules", peers=peers)
    assert r["verdict"] == "building_wide" and r["severity"] == "LOW"


def test_tools_are_safe():
    t = TriageTools(load("AHU-3"))
    assert t.run("sensor_health")["frozen"] == ["zone_temp_c"]
    assert "error" in t.run("delete_database")
    assert t.run("recent_trend", {"minutes": 999})["minutes"] <= 60
    assert t.run("other_units")["checked"] == 0      # no live system attached: says so, does not crash


def test_worker_runs_in_background():
    done = threading.Event()
    got = {}

    def on_done(event, report):
        got.update(report=report, event=event)
        done.set()

    TriageWorker(on_done=on_done).submit(load("AHU-3"))
    assert done.wait(5)
    assert got["report"]["verdict"] == "sensor_fault" and got["event"]["device_id"] == "AHU-3"
