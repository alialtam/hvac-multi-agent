"""Converts the Supervisor's trace to the agreed execution-trace format (agents/README.md)."""

OPERATOR_TYPES = {"approval", "rejected", "replan"}


def to_trace_lines(incident_id: str, supervisor_trace: list[dict]) -> list[dict]:
    """sender->from, receiver->to, detail->content, provider->llm_provider, ms->duration_ms."""
    lines = []
    for t in supervisor_trace:
        content = {"text": t.get("detail", "")}
        if t.get("override"):
            content["override"] = True
        if t.get("fallbacks"):
            content["fallbacks"] = t["fallbacks"]
        if t.get("tokens"):
            content["tokens"] = t["tokens"]
        lines.append({"ts": t["ts"], "incident_id": incident_id, "from": t["sender"], "to": t["receiver"],
                      "type": t["type"], "content": content,
                      "llm_provider": t.get("provider"), "duration_ms": t.get("ms")})
    return lines


def timeline_entry(t: dict) -> dict:
    """One short {agent, message} line for the dashboard timeline."""
    kind, sender, receiver, detail = t["type"], t["sender"], t["receiver"], t.get("detail", "")
    if kind == "route":
        return {"agent": "supervisor", "message": f"Supervisor → {receiver}: {detail}"}
    if kind in OPERATOR_TYPES:
        return {"agent": "operator", "message": f"{sender}: {detail}"}
    if kind == "critique":
        return {"agent": sender, "message": f"Critique: {detail}"}
    if kind == "request_more_evidence":
        return {"agent": sender, "message": f"Asks {receiver} for more evidence: {detail}"}
    return {"agent": sender, "message": detail}