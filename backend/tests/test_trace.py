from app.agents.trace import timeline_entry, to_trace_lines

ROUTE = {"ts": "t1", "sender": "supervisor", "receiver": "diagnosis", "type": "route",
         "detail": "No diagnosis yet", "provider": "rules", "ms": 3, "tokens": 0,
         "fallbacks": [], "override": False}
REPLAN = {"ts": "t2", "sender": "Ali", "receiver": "supervisor", "type": "replan", "detail": "wrong unit"}


def test_converts_to_the_agreed_format():
    line = to_trace_lines("inc_1", [ROUTE])[0]
    assert line == {"ts": "t1", "incident_id": "inc_1", "from": "supervisor", "to": "diagnosis",
                    "type": "route", "content": {"text": "No diagnosis yet"},
                    "llm_provider": "rules", "duration_ms": 3}


def test_override_and_fallbacks_are_kept():
    raw = {**ROUTE, "override": True, "fallbacks": ["openai"], "tokens": 50}
    c = to_trace_lines("inc_1", [raw])[0]["content"]
    assert c["override"] is True and c["fallbacks"] == ["openai"] and c["tokens"] == 50


def test_timeline_entries():
    assert timeline_entry(ROUTE) == {"agent": "supervisor", "message": "Supervisor → diagnosis: No diagnosis yet"}
    assert timeline_entry(REPLAN) == {"agent": "operator", "message": "Ali: wrong unit"}