"""Supervisor: a LangGraph graph where the LLM picks the next agent among LEGAL moves only."""
import json
from datetime import datetime, timezone
from typing import Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.diagnosis import diagnose, strip_label
from app.agents.energy import analyse_energy, check_after_hours, critique_diagnosis
from app.agents.schemas import Diagnosis, EnergyImpact, Recommendation, Route
from app.knowledge.rules import diagnose_rules
from app.llm import llm

MAX_REVISIONS = 2        # Diagnosis <-> Energy revision rounds
MAX_EVIDENCE_ROUNDS = 1  # Maintenance asking Diagnosis for more evidence
MAX_STEPS = 14           # safety stop for the supervisor loop


class SupState(TypedDict, total=False):
    incident_id: str
    event: dict
    state: str
    diagnosis: Optional[dict]
    energy: Optional[dict]
    critique: Optional[dict]
    recommendation: Optional[dict]
    critique_open: bool
    critiqued_cause: Optional[str]
    need_more_evidence: bool
    evidence_reason: Optional[str]
    evidence_rounds: int
    revisions: int
    escalated: bool
    steps: int
    next: str
    trace: list


# ---------- helpers ----------
def _tr(s, sender, receiver, kind, detail, meta=None, **extra):
    e = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
         "sender": sender, "receiver": receiver, "type": kind, "detail": detail, **extra}
    if meta:
        e.update(provider=meta.get("provider"), ms=meta.get("ms"), tokens=meta.get("tokens"),
                 fallbacks=meta.get("fallbacks", []))
    return s.get("trace", []) + [e]


def _after_hours_hint(event: dict) -> bool:
    return "running_unoccupied" in event.get("rule_hits", []) or check_after_hours(event) is True


def legal_moves(s: dict) -> list[str]:
    """Moves the Supervisor may choose now. The first one is the default."""
    if s.get("escalated"):
        return ["wait_approval"]
    d = s.get("diagnosis")
    if d is None:
        if _after_hours_hint(s["event"]) and s.get("energy") is None:
            return ["energy", "diagnosis"]      # after-hours waste: check energy first
        return ["diagnosis"]
    if s.get("need_more_evidence"):
        return ["diagnosis"]
    if s.get("critique_open"):
        return ["diagnosis"] if s.get("revisions", 0) < MAX_REVISIONS else ["wait_approval"]
    moves = []
    if d["cause"] != "sensor_stuck" and s.get("critiqued_cause") != d["cause"]:
        moves.append("energy")                  # a frozen sensor has no energy cost
    if s.get("recommendation") is None:
        moves.append("maintenance")
    return moves or ["wait_approval"]


def _context(s: dict, legal: list[str]) -> dict:
    d, e, c = s.get("diagnosis"), s.get("energy"), s.get("critique")
    ev = s["event"]
    return {
        "device_id": ev.get("device_id"), "severity": ev.get("severity"),
        "rule_hits": ev.get("rule_hits", []),
        "triage_hint_advice_only": (ev.get("triage") or {}).get("recommend_next"),
        "diagnosis": {"cause": d["cause"], "confidence": d["confidence"]} if d else None,
        "energy": {"extra_kwh_per_day": e["extra_kwh_per_day"]} if e else None,
        "critique_open": bool(s.get("critique_open")),
        "critique_issues": c["issues"] if c else [],
        "need_more_evidence": bool(s.get("need_more_evidence")),
        "revisions": s.get("revisions", 0), "has_recommendation": bool(s.get("recommendation")),
        "legal": legal,
    }


def rules_route(ctx: dict) -> Route:
    """Rules fallback for the supervisor: take the first legal move."""
    nxt = ctx["legal"][0]
    if nxt == "diagnosis":
        why = ("Maintenance needs more evidence" if ctx["need_more_evidence"]
               else "Energy critique is open, revise the diagnosis" if ctx["critique_open"]
               else "No diagnosis yet")
    elif nxt == "energy":
        why = ("After-hours pattern, check the energy impact first" if ctx["diagnosis"] is None
               else "Check energy impact and critique the diagnosis")
    elif nxt == "maintenance":
        why = "Draft the ticket and priority"
    else:
        why = "Ready for the operator"
    return Route(next=nxt, reason=why)


llm.register_rules("supervisor", rules_route)


def _default_maintenance(event, diagnosis, energy):
    try:
        from app.agents.maintenance import recommend
    except ImportError as e:
        raise RuntimeError("Maintenance agent is not built yet") from e
    return recommend(event, diagnosis, energy)


# ---------- graph ----------
def build_graph(diagnose_fn=diagnose, energy_fn=analyse_energy,
                critique_fn=critique_diagnosis, maintenance_fn=None):
    """Workers are injected so tests can use fakes. maintenance_fn(event, diagnosis, energy)
    must return (Recommendation | None, reason_if_refused | None, meta)."""
    maintenance_fn = maintenance_fn or _default_maintenance

    def n_supervisor(s: SupState):
        legal = legal_moves(s)
        steps = s.get("steps", 0) + 1
        if steps > MAX_STEPS:
            return {"next": "wait_approval", "steps": steps, "escalated": True,
                    "trace": _tr(s, "supervisor", "wait_approval", "route",
                                 "Step limit reached, escalating to the operator", override=False)}
        ctx = _context(s, legal)
        prompt = (
            "You are the Supervisor of an HVAC multi-agent system.\n"
            f"Choose the next agent. You MUST pick one of: {legal}.\n"
            "Hints: after-hours waste is cheapest to check with the energy agent first; a stuck sensor "
            "has no energy cost; do not draft a ticket while the diagnosis is being revised.\n"
            f"State: {json.dumps(ctx, default=str)}\n"
            "Reply with next and a one-sentence reason.")
        route, meta = llm.complete(prompt, Route, "supervisor", context=ctx)
        choice, reason, override = route.next, route.reason, False
        if choice not in legal:
            override = True
            reason = f"LLM chose '{route.next}', which is not legal now; used '{legal[0]}' instead"
            choice = legal[0]
        return {"next": choice, "steps": steps,
                "trace": _tr(s, "supervisor", choice, "route", reason, meta, override=override)}

    def n_diagnosis(s: SupState):
        revising = bool(s.get("critique_open"))
        text = None
        if revising and s.get("critique"):
            c = s["critique"]
            text = "; ".join(c["issues"])
            if c.get("suggested_cause"):
                text += f". Energy suggests: {c['suggested_cause']}"
        more = bool(s.get("need_more_evidence"))
        d, meta = diagnose_fn(s["event"], more_evidence=more, critique=text)
        upd = {"diagnosis": d.model_dump(), "need_more_evidence": False,
               "critique_open": False, "recommendation": None}
        if revising:
            upd["revisions"] = s.get("revisions", 0) + 1
            upd["critiqued_cause"] = None       # Energy must critique the revised diagnosis
        tag = " (revision after critique)" if revising else " (with extra evidence)" if more else ""
        upd["trace"] = _tr(s, "diagnosis", "supervisor", "result",
                           f"cause={d.cause} confidence={d.confidence}{tag}", meta)
        return upd

    def n_energy(s: SupState):
        ev, d = s["event"], s.get("diagnosis")
        if d is None:                            # energy-first path: preliminary cause from rules
            prelim = diagnose_rules(ev)
            e, meta = energy_fn(ev, prelim)
            return {"energy": e.model_dump(),
                    "trace": _tr(s, "energy", "supervisor", "result",
                                 f"preliminary: {e.extra_kwh_per_day} kWh/day (cause from rules: {prelim.cause})",
                                 meta)}
        diag = Diagnosis.model_validate(d)
        e, meta = energy_fn(ev, diag)
        c = critique_fn(ev, diag)
        tr = _tr(s, "energy", "supervisor", "result", f"{e.extra_kwh_per_day} kWh/day, cost {e.cost_per_day}", meta)
        tr = _tr({"trace": tr}, "energy", "supervisor" if c.agrees else "diagnosis", "critique",
                 "agrees with the diagnosis" if c.agrees else "; ".join(c.issues))
        return {"energy": e.model_dump(), "critique": c.model_dump(), "critique_open": not c.agrees,
                "critiqued_cause": diag.cause, "trace": tr}

    def n_maintenance(s: SupState):
        diag = Diagnosis.model_validate(s["diagnosis"])
        en = EnergyImpact.model_validate(s["energy"]) if s.get("energy") else None
        rec, reason, meta = maintenance_fn(s["event"], diag, en)
        if rec is not None:
            return {"recommendation": rec.model_dump(),
                    "trace": _tr(s, "maintenance", "supervisor", "result",
                                 f"priority={rec.priority}, downtime={rec.estimated_downtime_min} min", meta)}
        rounds = s.get("evidence_rounds", 0)
        if rounds < MAX_EVIDENCE_ROUNDS:
            return {"need_more_evidence": True, "evidence_reason": reason, "evidence_rounds": rounds + 1,
                    "trace": _tr(s, "maintenance", "diagnosis", "request_more_evidence", reason, meta)}
        return {"escalated": True, "evidence_reason": reason,
                "trace": _tr(s, "maintenance", "supervisor", "refused", f"still not enough evidence: {reason}", meta)}

    def n_gate(s: SupState):
        if s.get("critique_open"):
            c = s["critique"]
            detail = (f"Diagnosis ({s['diagnosis']['cause']}) and Energy still disagree after "
                      f"{s.get('revisions', 0)} revisions. Both views go to the operator: {'; '.join(c['issues'])}")
        elif s.get("escalated"):
            detail = f"Escalated to the operator: {s.get('evidence_reason') or 'step limit reached'}"
        else:
            detail = "Recommendation ready, waiting for operator approval"
        return {"state": "awaiting_approval",
                "escalated": bool(s.get("critique_open") or s.get("escalated")),
                "trace": _tr(s, "supervisor", "operator", "escalate_to_human", detail)}

    g = StateGraph(SupState)
    g.add_node("supervisor", n_supervisor)
    g.add_node("diagnosis_agent", n_diagnosis)   # node names must differ from state keys
    g.add_node("energy_agent", n_energy)
    g.add_node("maintenance_agent", n_maintenance)
    g.add_node("approval_gate", n_gate)
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", lambda s: s["next"], {
        "diagnosis": "diagnosis_agent", "energy": "energy_agent",
        "maintenance": "maintenance_agent", "wait_approval": "approval_gate"})
    for w in ("diagnosis_agent", "energy_agent", "maintenance_agent"):
        g.add_edge(w, "supervisor")
    g.add_edge("approval_gate", END)
    return g.compile()


def run_incident(event: dict, graph=None) -> dict:
    ev = strip_label(event)                      # fault_label never enters the graph
    init = {"incident_id": ev.get("event_id", "incident"), "event": ev, "state": "investigating",
            "revisions": 0, "evidence_rounds": 0, "steps": 0, "escalated": False,
            "critique_open": False, "need_more_evidence": False, "trace": []}
    return (graph or build_graph()).invoke(init, config={"recursion_limit": 60})