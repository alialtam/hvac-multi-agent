"""Supervisor: a LangGraph graph where the LLM picks the next agent among LEGAL moves only.
It pauses for the operator with interrupt() and replans when the operator rejects."""
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional, TypedDict

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from app.agents.diagnosis import diagnose, strip_label
from app.agents.energy import analyse_energy, check_after_hours, critique_diagnosis
from app.agents.schemas import Diagnosis, EnergyImpact, Recommendation, Route
from app.knowledge.rules import diagnose_rules
from app.llm import llm

log = logging.getLogger("supervisor")

MAX_REVISIONS = 2        # Diagnosis <-> Energy revision rounds
MAX_EVIDENCE_ROUNDS = 1  # Maintenance asking Diagnosis for more evidence
MAX_REPLANS = 2          # operator rejections before the incident is closed as rejected
MAX_STEPS = 14           # safety stop for one supervisor cycle


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
    replans: int
    operator_feedback: Optional[str]
    decision: Optional[dict]
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
        "replans": s.get("replans", 0), "operator_feedback": s.get("operator_feedback"),
        "legal": legal,
    }


def rules_route(ctx: dict) -> Route:
    """Rules fallback for the supervisor: take the first legal move."""
    nxt = ctx["legal"][0]
    if nxt == "diagnosis":
        why = ("Maintenance needs more evidence" if ctx["need_more_evidence"]
               else "Energy critique is open, revise the diagnosis" if ctx["critique_open"]
               else "Operator rejected the last recommendation, diagnose again with their reason"
               if ctx["operator_feedback"] else "No diagnosis yet")
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


def get_checkpointer(path: Optional[str] = None):
    """SQLite checkpoints survive a restart. Falls back to memory if the package is missing."""
    path = path or os.getenv("CHECKPOINT_DB", "checkpoints.sqlite")
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
        return SqliteSaver(sqlite3.connect(path, check_same_thread=False))
    except ImportError:
        log.warning("langgraph-checkpoint-sqlite is not installed; using in-memory checkpoints")
        return MemorySaver()


# ---------- graph ----------
def build_graph(diagnose_fn=diagnose, energy_fn=analyse_energy,
                critique_fn=critique_diagnosis, maintenance_fn=None, checkpointer=None):
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
        parts = []
        if revising and s.get("critique"):
            c = s["critique"]
            t = "; ".join(c["issues"])
            if c.get("suggested_cause"):
                t += f". Energy suggests: {c['suggested_cause']}"
            parts.append(t)
        if s.get("operator_feedback"):
            parts.append(f"The operator rejected the previous recommendation: {s['operator_feedback']}")
        text = " ".join(parts) or None
        more = bool(s.get("need_more_evidence"))
        d, meta = diagnose_fn(s["event"], more_evidence=more, critique=text)
        upd = {"diagnosis": d.model_dump(), "need_more_evidence": False,
               "critique_open": False, "recommendation": None}
        if revising:
            upd["revisions"] = s.get("revisions", 0) + 1
            upd["critiqued_cause"] = None       # Energy must critique the revised diagnosis
        tag = (" (revision after critique)" if revising else " (with extra evidence)" if more
               else " (replanned after operator rejection)" if s.get("operator_feedback") else "")
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

    def n_request_approval(s: SupState):
        """Records why the case goes to the operator. The graph pauses in the next node."""
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

    def n_wait_approval(s: SupState):
        """Pauses with interrupt(). Resumed by resume_incident() with the operator's decision."""
        payload = {"incident_id": s["incident_id"], "diagnosis": s.get("diagnosis"),
                   "energy": s.get("energy"), "critique": s.get("critique"),
                   "recommendation": s.get("recommendation"), "escalated": bool(s.get("escalated"))}
        d = interrupt(payload) or {}
        operator = d.get("operator") or "operator"
        if d.get("decision") == "approve":
            return {"state": "approved", "decision": d, "operator_feedback": None,
                    "trace": _tr(s, operator, "supervisor", "approval", d.get("note") or "approved")}
        reason = d.get("reason") or "no reason given"
        done = s.get("replans", 0)
        if done >= MAX_REPLANS:
            return {"state": "rejected", "decision": d, "operator_feedback": reason,
                    "trace": _tr(s, operator, "supervisor", "rejected",
                                 f"Rejected again after {done} replans, closing the incident: {reason}")}
        return {"state": "investigating", "decision": d, "operator_feedback": reason, "replans": done + 1,
                "diagnosis": None, "energy": None, "recommendation": None, "critique": None, "critique_open": False,
                "critiqued_cause": None, "need_more_evidence": False, "evidence_rounds": 0,
                "revisions": 0, "escalated": False, "steps": 0,
                "trace": _tr(s, operator, "supervisor", "replan",
                             f"Operator rejected the recommendation: {reason}. "
                             f"Replanning (round {done + 1} of {MAX_REPLANS}), the reason is new evidence.")}

    g = StateGraph(SupState)
    g.add_node("supervisor", n_supervisor)
    g.add_node("diagnosis_agent", n_diagnosis)   # node names must differ from state keys
    g.add_node("energy_agent", n_energy)
    g.add_node("maintenance_agent", n_maintenance)
    g.add_node("approval_request", n_request_approval)
    g.add_node("approval_wait", n_wait_approval)
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", lambda s: s["next"], {
        "diagnosis": "diagnosis_agent", "energy": "energy_agent",
        "maintenance": "maintenance_agent", "wait_approval": "approval_request"})
    for w in ("diagnosis_agent", "energy_agent", "maintenance_agent"):
        g.add_edge(w, "supervisor")
    g.add_edge("approval_request", "approval_wait")
    g.add_conditional_edges("approval_wait",
                            lambda s: "end" if s["state"] in ("approved", "rejected") else "supervisor",
                            {"end": END, "supervisor": "supervisor"})
    return g.compile(checkpointer=checkpointer or MemorySaver())


# ---------- running and resuming ----------
def _cfg(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}

def _drive(g, inp, cfg, on_update=None) -> dict:
    """Runs the graph step by step so the API can show each agent's result as it finishes."""
    for _ in g.stream(inp, cfg, stream_mode="updates"):
        if on_update:
            try:
                on_update(g.get_state(cfg).values)
            except Exception:
                log.exception("on_update failed")
    return g.get_state(cfg).values

def run_incident(event: dict, graph=None, thread_id: Optional[str] = None, on_update=None) -> dict:
    """Runs until the graph pauses for the operator. Returns the saved incident state."""
    ev = strip_label(event)                      # fault_label never enters the graph
    tid = thread_id or ev.get("event_id", "incident")
    init = {"incident_id": tid, "event": ev, "state": "investigating",
            "revisions": 0, "replans": 0, "operator_feedback": None, "evidence_rounds": 0,
            "steps": 0, "escalated": False, "critique_open": False,
            "need_more_evidence": False, "trace": []}
    g = graph or build_graph()
    return _drive(g, init, _cfg(tid), on_update)


def get_incident(graph, thread_id: str) -> Optional[dict]:
    return graph.get_state(_cfg(thread_id)).values or None


def resume_incident(graph, thread_id: str, decision: str, operator: str,
                    reason: Optional[str] = None, note: Optional[str] = None, on_update=None) -> dict:
    """Operator approves or rejects. A rejection needs a reason and triggers a replan."""
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be 'approve' or 'reject'")
    if decision == "reject" and not (reason or "").strip():
        raise ValueError("a rejection needs a reason")
    cfg = _cfg(thread_id)
    if "approval_wait" not in (graph.get_state(cfg).next or ()):
        raise ValueError("this incident is not waiting for approval")
    return _drive(graph, Command(resume={"decision": decision, "operator": operator,
                                         "reason": reason, "note": note}), cfg, on_update)