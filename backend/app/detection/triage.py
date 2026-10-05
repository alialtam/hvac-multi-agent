"""Anomaly triage agent: the AI step of the Anomaly Detection agent.

The detectors (rules, z-score, Isolation Forest) say THAT something is abnormal.
The triage agent decides WHAT KIND of problem it is and whether it is real,
before the Supervisor spends effort on it:

  verdict          equipment_fault | sensor_fault | operational_waste | building_wide | false_alarm | unclear
  suspected_area   airflow | cooling | sensor | schedule | building | unknown
  recommend_next   diagnosis | energy | maintenance | human_review   (a hint for the Supervisor's routing)

How it works: an LLM receives the event and decides which checks to run, calling
the tools in triage_tools.py (sensor health, other units, schedule, trend,
history). When it has enough evidence it calls `submit_triage`. Every tool call
and LLM call is recorded as a trace step.

It never blocks the alarm: it does not diagnose root causes (Diagnosis agent)
or decide repairs (Maintenance agent), and the incident always reaches a person.

Failure handling: invalid answer -> asked once to fix it; LLM error, timeout,
no API key or too many steps -> the deterministic rules triage below, which
uses the same tools. The rules verdict is also stored with every LLM verdict,
so disagreements are visible.

    report = triage_event(event, provider="openai", store=..., pipeline=...)
    worker = TriageWorker(on_done=lambda event, report: ..., store=..., pipeline=...)
    worker.submit(event)          # returns at once; runs in a background thread
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Literal

from pydantic import BaseModel, Field, ValidationError

from .llm_chat import ChatClient, assistant_message, make_client, parse_args
from .triage_tools import LABEL, TOOL_NAMES, TOOL_SPECS, TriageTools

log = logging.getLogger("triage")

AGENT = "anomaly_detection"
MAX_STEPS = 6          # LLM rounds before we force an answer
MAX_FIX_ATTEMPTS = 1   # invalid submit_triage -> ask once to correct it

Verdict = Literal["equipment_fault", "sensor_fault", "operational_waste", "building_wide", "false_alarm", "unclear"]
Area = Literal["airflow", "cooling", "sensor", "schedule", "building", "unknown"]
Next = Literal["diagnosis", "energy", "maintenance", "human_review"]


class TriageDecision(BaseModel):
    verdict: Verdict
    suspected_area: Area
    severity: Literal["LOW", "MEDIUM", "HIGH"]
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=10, max_length=400)
    key_evidence: list[str] = Field(min_length=1, max_length=5)
    recommend_next: Next


SUBMIT_SPEC = {"type": "function", "function": {
    "name": "submit_triage",
    "description": "Submit the final triage decision. Call exactly once, after checking the evidence.",
    "parameters": {
        "type": "object",
        "required": list(TriageDecision.model_fields),
        "properties": {
            "verdict": {"type": "string", "enum": list(Verdict.__args__)},
            "suspected_area": {"type": "string", "enum": list(Area.__args__)},
            "severity": {"type": "string", "enum": ["LOW", "MEDIUM", "HIGH"]},
            "confidence": {"type": "number", "description": "0 to 1"},
            "summary": {"type": "string", "description": "One or two plain sentences for the facility manager"},
            "key_evidence": {"type": "array", "items": {"type": "string"},
                             "description": "2-4 short facts with numbers from the tool results"},
            "recommend_next": {"type": "string", "enum": list(Next.__args__)},
        }}}}

SYSTEM_PROMPT = """You are the triage step of the Anomaly Detection agent in an HVAC monitoring system.
A building has 6 air handling units (AHUs). Statistical detectors (per-signal z-scores against this
unit's normal for the time of day, an Isolation Forest, and fixed rules) have flagged an anomaly.

Your job: decide whether the anomaly is real and what kind of problem it is, so the Supervisor can
route it. You do NOT name the root cause and you do NOT recommend repairs; other agents do that.

Work like an engineer: call the tools you need (usually 3 or 4; each tool at most ONCE, the results do
not change; sensor_health and other_units are usually worth checking) and then call submit_triage
exactly once. You may call several tools in one turn.

Verdicts:
- equipment_fault: the unit itself behaves abnormally (airflow, cooling or power out of line)
- sensor_fault: a sensor reading is frozen, impossible or missing while the unit seems fine
- operational_waste: the unit runs when it should not (e.g. outside schedule, empty zone)
- building_wide: several units are abnormal together, pointing to weather or a shared cause
- false_alarm: the evidence does not hold up (small deviations, nothing sustained)
- unclear: real but you cannot tell which kind

HVAC knowledge (apply in this order):
1. Outside the schedule with the unit ON and an empty zone: operational_waste, area schedule. Airflow
   and power look extreme then only because the normal value at night is 0; it is NOT an airflow fault.
2. A frozen, impossible or missing reading: sensor_fault, area sensor. The rule device_offline means
   the unit sent no data at all (network, controller or power): sensor_fault, area sensor, maintenance.
3. Less airflow at the same fan speed (airflow z clearly negative), often with colder supply air:
   equipment_fault, area airflow.
4. Warmer supply air, a smaller coil temperature drop, a warming room, or power collapsing:
   equipment_fault, area cooling.
5. Power clearly HIGHER (z >= 3) while airflow and room temperature are still normal: the compressor
   works harder for the same cooling, an early loss of cooling capacity (e.g. a slow refrigerant
   leak): equipment_fault, area cooling. Do not answer unknown for this pattern.
6. building_wide ONLY when other_units says a building-wide cause is possible (3+ other units
   abnormal AND this unit's deviation is mild). Units often have separate faults at the same time;
   other units being abnormal never explains a large deviation on this unit.
Use area unknown only when none of these patterns fits.

recommend_next: diagnosis for equipment faults, energy for operational waste, maintenance for
sensor faults, human_review for building_wide, false_alarm or anything you are unsure about.
Keep the severity of the event unless the evidence clearly says otherwise. Cite numbers."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _step(type_: str, to: str, content: dict, provider: str, duration_ms: int = 0) -> dict:
    """One execution-trace line (same shape as backend/app/agents/README.md)."""
    return {"ts": _now(), "incident_id": None, "from": AGENT, "to": to, "type": type_,
            "content": content, "llm_provider": provider, "duration_ms": duration_ms}


def _event_brief(event: dict, tools: TriageTools) -> str:
    keep = {k: event.get(k) for k in ("event_id", "device_id", "zone", "ts_start", "ts_detected",
                                       "method", "score", "severity", "rule_hits", "signals")}
    cfg = tools.config
    t = event["ts_detected"][11:16]
    inside = cfg.schedule_on <= t < cfg.schedule_off
    return (f"New anomaly event, detected at {t} building time ({'inside' if inside else 'OUTSIDE'} the "
            f"{cfg.schedule_on}-{cfg.schedule_off} operating schedule). The window of raw readings is "
            "available through the tools.\n" + json.dumps(keep))


# ------------------------------------------------------------------- rules
def rules_triage(event: dict, tools: TriageTools) -> tuple[dict, list[dict]]:
    """Deterministic triage: same tools, fixed decision order. Used as fallback and as a cross-check."""
    steps, res = [], {}
    for name in ("signal_deviations", "sensor_health", "other_units", "schedule_context", "recent_trend"):
        t0 = time.perf_counter()
        res[name] = tools.run(name)
        steps.append(_step("tool_call", f"tool:{name}", {"args": {}, "result": res[name]}, "rules",
                           round((time.perf_counter() - t0) * 1000)))

    z = {s["key"]: s["z"] for s in event.get("signals", [])}
    rules = set(event.get("rule_hits", []))
    sev = event.get("severity", "MEDIUM")
    health, others, sched = res["sensor_health"], res["other_units"], res["schedule_context"]
    ev = [res["signal_deviations"]["note"]]

    if "device_offline" in rules:
        # no data at all: a network, controller or power problem, not something the sensors can explain
        d = dict(verdict="sensor_fault", suspected_area="sensor", recommend_next="maintenance", confidence=0.9)
        ev = [f"{event['device_id']} stopped sending data; last reading at {event['ts_detected'][11:16]}"] + \
             ([others["note"]] if others.get("checked") else [])
    elif health.get("frozen") or health.get("out_of_range") or "sensor_flatline" in rules:
        d = dict(verdict="sensor_fault", suspected_area="sensor", recommend_next="maintenance", confidence=0.8)
        ev = [health["note"]] + ev
    elif others.get("building_wide_possible") and not rules & {"zone_too_warm", "fast_temp_rise"}:
        d = dict(verdict="building_wide", suspected_area="building", recommend_next="human_review",
                 confidence=0.6)
        sev = "LOW"
        ev = [others["note"]] + ev
    elif "running_unoccupied" in rules or (sched.get("inside_schedule") is False and sched.get("status") == "ON"
                                           and not sched.get("occupancy")):
        d = dict(verdict="operational_waste", suspected_area="schedule", recommend_next="energy", confidence=0.85)
        ev = [sched["note"]] + ev
    elif "low_airflow" in rules or z.get("airflow_cfm", 0) <= -3:
        d = dict(verdict="equipment_fault", suspected_area="airflow", recommend_next="diagnosis", confidence=0.8)
        ev += [res["recent_trend"]["note"]]
    elif (rules & {"zone_too_warm", "fast_temp_rise"} or z.get("supply_temp_c", 0) >= 3
          or z.get("coil_dt_c", 0) <= -3 or z.get("power_kw", 0) <= -3):
        d = dict(verdict="equipment_fault", suspected_area="cooling", recommend_next="diagnosis", confidence=0.75)
        ev += [res["recent_trend"]["note"]]
    elif z.get("power_kw", 0) >= 3 and abs(z.get("airflow_cfm", 0)) < 3:
        # more power for the same airflow and room temperature: the cooling side is less efficient
        d = dict(verdict="equipment_fault", suspected_area="cooling", recommend_next="diagnosis", confidence=0.6)
        ev += [res["recent_trend"]["note"]]
    elif not rules and event.get("score", 1) < 0.6:
        d = dict(verdict="false_alarm", suspected_area="unknown", recommend_next="human_review", confidence=0.4)
    else:
        d = dict(verdict="unclear", suspected_area="unknown", recommend_next="diagnosis", confidence=0.5)

    if d["verdict"] != "building_wide" and others.get("checked") and others["note"] not in ev:
        ev.append(others["note"])
    decision = TriageDecision(severity=sev, summary=_summary(event, d), key_evidence=ev[:4], **d)
    steps.append(_step("decision", "supervisor", decision.model_dump(), "rules"))
    return decision.model_dump(), steps


AREA_TEXT = {"airflow": "an airflow problem", "cooling": "a loss of cooling", "sensor": "a faulty sensor",
             "schedule": "the unit running when it is not needed", "building": "a building-wide change",
             "unknown": "an unclear problem"}


def _summary(event: dict, d: dict) -> str:
    dev = event["device_id"]
    if d["verdict"] == "false_alarm":
        return f"The deviation on {dev} is small and not backed by any rule; probably a false alarm."
    if "device_offline" in event.get("rule_hits", []):
        return f"{dev} stopped sending data. Check its network connection, controller and power supply."
    if d["verdict"] == "building_wide":
        return f"Several units changed at the same time as {dev}; this looks like {AREA_TEXT['building']}, not a fault on one unit."
    top = [s for s in event.get("signals", []) if abs(s.get("z", 0)) >= 3][:2]
    detail = " and ".join(f"{LABEL.get(s['key'], s['key'])} {'up' if s['z'] > 0 else 'down'} "
                          f"{abs(s['z']):.0f}σ" for s in top)
    return f"{dev} shows {AREA_TEXT[d['suspected_area']]}" + (f" ({detail})." if detail else ".")


# --------------------------------------------------------------------- LLM
def llm_triage(event: dict, tools: TriageTools, client: ChatClient) -> tuple[dict, list[dict]]:
    """LLM decides which tools to call, then submits. Raises on failure (caller falls back)."""
    provider = client.provider
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": _event_brief(event, tools)}]
    steps: list[dict] = []
    fixes = repeats = 0
    done: set[str] = set()
    for round_ in range(MAX_STEPS):
        # last round, every tool used, or going in circles: it must decide now
        last = round_ == MAX_STEPS - 1 or len(done) == len(TOOL_NAMES) or repeats >= 2
        choice = {"type": "function", "function": {"name": "submit_triage"}} if last else "auto"
        reply = client.chat(messages, TOOL_SPECS + [SUBMIT_SPEC], tool_choice=choice)
        steps.append(_step("llm_call", "llm", {
            "model": client.model, "round": round_ + 1, "usage": reply.get("usage", {}),
            "tool_calls": [c["name"] for c in reply.get("tool_calls", [])],
            "text": (reply.get("content") or "")[:300]}, provider, reply.get("duration_ms", 0)))
        calls = reply.get("tool_calls") or []
        if not calls:
            # some small models answer in plain JSON instead of calling the tool
            decision = _decision_from_text(reply.get("content"))
            if decision:
                steps.append(_step("decision", "supervisor", decision, provider))
                return decision, steps
            messages.append(assistant_message(reply))
            messages.append({"role": "user", "content": "Use the tools, then call submit_triage."})
            continue

        messages.append(assistant_message(reply))
        for call in calls:
            args = parse_args(call["arguments"])
            if call["name"] == "submit_triage":
                try:
                    decision = TriageDecision(**args).model_dump()
                except ValidationError as exc:
                    if fixes >= MAX_FIX_ATTEMPTS:
                        raise ValueError(f"invalid triage after retry: {exc.errors()[:2]}") from exc
                    fixes += 1
                    steps.append(_step("retry", "llm", {"reason": "invalid submit_triage",
                                                        "errors": str(exc.errors()[:3])}, provider))
                    messages.append({"role": "tool", "tool_call_id": call["id"],
                                     "content": f"Invalid: {exc.errors()[:3]}. Fix the fields and call submit_triage again."})
                    continue
                steps.append(_step("decision", "supervisor", decision, provider))
                return decision, steps
            if call["name"] in done:
                # the same check twice adds nothing: say so instead of running and logging it again
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(
                    {"note": f"You already ran {call['name']}; use that result. "
                             f"Not yet checked: {', '.join(t for t in TOOL_NAMES if t not in done) or 'nothing'}."})})
                repeats += 1
                continue
            done.add(call["name"])
            t0 = time.perf_counter()
            result = tools.run(call["name"], args)
            steps.append(_step("tool_call", f"tool:{call['name']}", {"args": args, "result": result}, provider,
                               round((time.perf_counter() - t0) * 1000)))
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result, default=str)})
    raise TimeoutError(f"no decision after {MAX_STEPS} rounds")


def _decision_from_text(text: str | None) -> dict | None:
    if not text or "{" not in text:
        return None
    try:
        raw = json.loads(text[text.index("{"): text.rindex("}") + 1])
        return TriageDecision(**raw).model_dump()
    except (ValueError, ValidationError):
        return None


# ----------------------------------------------------------------- facade
def triage_event(event: dict, provider: str = "rules", client: ChatClient | None = None,
                 store=None, pipeline=None, peers=None, history=None) -> dict:
    """Triage one anomaly event. Never raises: on any LLM problem it falls back to rules."""
    t0 = time.perf_counter()
    tools = TriageTools(event, store=store, pipeline=pipeline, peers=peers, history=history)
    rules_decision, rules_steps = rules_triage(event, tools)

    steps, used, decision = [], "rules", None
    if provider in ("openai", "ollama"):
        client = client or make_client(provider)
        if client is None:
            steps.append(_step("fallback", "rules", {"reason": f"{provider} is not configured (no API key)"}, provider))
        else:
            try:
                decision, steps = llm_triage(event, tools, client)
                used = client.provider
            except Exception as exc:  # timeout, network, bad output: the demo must go on
                log.warning("LLM triage failed (%s), using rules", exc)
                steps.append(_step("fallback", "rules", {"reason": f"{type(exc).__name__}: {exc}"[:300]}, provider))

    if decision is None:
        decision, steps = rules_decision, steps + rules_steps
    tokens = sum(s["content"].get("usage", {}).get("prompt_tokens", 0)
                 + s["content"].get("usage", {}).get("completion_tokens", 0) for s in steps if s["type"] == "llm_call")
    return {
        **decision,
        "provider": used,
        "rules_verdict": rules_decision["verdict"],
        "agrees_with_rules": decision["verdict"] == rules_decision["verdict"],
        "tools_used": list(dict.fromkeys(s["to"].split(":", 1)[1] for s in steps if s["type"] == "tool_call")),
        "tokens": tokens,
        "duration_ms": round((time.perf_counter() - t0) * 1000),
        "steps": steps,
    }


class TriageWorker:
    """Runs triage in a background thread so ingestion is never slowed down.

        worker = TriageWorker(on_done=fn, store=ingestion.store, pipeline=ingestion.pipeline,
                              provider_fn=lambda: settings["provider"])
        Ingestion(on_event=worker.submit)       # or call worker.submit(event) yourself
        fn(event, report) is called when the triage is finished.
    """

    def __init__(self, on_done: Callable[[dict, dict], None], store=None, pipeline=None,
                 provider_fn: Callable[[], str] = lambda: "rules"):
        self.on_done, self.store, self.pipeline, self.provider_fn = on_done, store, pipeline, provider_fn
        self.q: queue.Queue = queue.Queue()
        threading.Thread(target=self._loop, daemon=True, name="triage").start()

    def submit(self, event: dict) -> None:
        self.q.put(event)

    def _loop(self) -> None:
        while True:
            event = self.q.get()
            try:
                report = triage_event(event, provider=self.provider_fn(), store=self.store, pipeline=self.pipeline)
                log.info("TRIAGE %s %s/%s via %s in %d ms", event["device_id"], report["verdict"],
                         report["suspected_area"], report["provider"], report["duration_ms"])
                self.on_done(event, report)
            except Exception:
                log.exception("triage failed for %s", event.get("event_id"))
