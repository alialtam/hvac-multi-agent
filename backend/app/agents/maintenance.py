"""Maintenance agent: priority and downtime come from code; the LLM only writes the action and checklist."""
from typing import Optional

from app.agents.diagnosis import strip_label
from app.agents.schemas import Diagnosis, EnergyImpact, Recommendation
from app.knowledge import search_knowledge
from app.knowledge.rules import CAUSE_QUERY
from app.llm import llm

MIN_CONFIDENCE = 0.6
LEVELS = ["LOW", "MEDIUM", "HIGH"]
BASE_PRIORITY = {"compressor_failure": "HIGH", "refrigerant_leak": "MEDIUM", "filter_blockage": "MEDIUM",
                 "after_hours_waste": "MEDIUM", "sensor_stuck": "LOW", "unknown": "LOW"}
DOWNTIME_MIN = {"compressor_failure": 240, "refrigerant_leak": 180, "filter_blockage": 45,
                "sensor_stuck": 30, "after_hours_waste": 15, "unknown": 60}
HARDWARE = {"filter_blockage", "compressor_failure", "refrigerant_leak"}
LOCKOUT = "Switch the unit to maintenance mode, isolate power and apply lockout tags"
ACTIONS = {
    "compressor_failure": "Lock out the unit and have a technician inspect the compressor, contactor and overload.",
    "refrigerant_leak": "Leak-test the coil and joints, repair the leak, then recharge refrigerant.",
    "filter_blockage": "Replace the clogged air filters and confirm airflow returns to normal.",
    "sensor_stuck": "Compare the zone sensor with a handheld thermometer, then recalibrate or replace it.",
    "after_hours_waste": "Clear the manual override, restore the setback schedule and check the building system.",
    "unknown": "Inspect the unit on site, because the cause is not clear from the signals.",
}
CHECKLISTS = {
    "compressor_failure": ["Check the compressor contactor and overload", "Check refrigerant pressure",
                           "Call the HVAC technician", "Test cooling after repair"],
    "refrigerant_leak": ["Leak-test the coil and joints", "Repair the leak", "Recharge refrigerant only after repair",
                         "Confirm the coil delta T returns to about 7 C"],
    "filter_blockage": ["Replace the clogged filters", "Confirm airflow is above 2100 CFM at 75% fan speed",
                        "Review the filter replacement interval"],
    "sensor_stuck": ["Compare with a handheld thermometer", "Recalibrate or replace the sensor",
                     "Do not trust control decisions made on the frozen reading"],
    "after_hours_waste": ["Clear the manual override", "Enable setback outside occupied hours",
                          "Check the building management schedule"],
    "unknown": ["Inspect the unit on site", "Compare signals with the peer units", "Collect a longer history"],
}


# ---- tools ----
def search_procedures(cause: str, k: int = 2) -> list[dict]:
    hits = search_knowledge(CAUSE_QUERY.get(cause, CAUSE_QUERY["unknown"]), 4)
    return [h for h in hits if h["source"].startswith("manual")][:k]


def request_more_evidence(diagnosis: Diagnosis) -> Optional[str]:
    """Return a reason when the diagnosis is too uncertain to act on, otherwise None."""
    if diagnosis.confidence < MIN_CONFIDENCE:
        return (f"Diagnosis confidence {diagnosis.confidence} is below {MIN_CONFIDENCE}; "
                "need a longer history window and peer-unit readings before drafting a ticket.")
    return None


def set_priority(event: dict, diagnosis: Diagnosis) -> str:
    level = LEVELS.index(BASE_PRIORITY.get(diagnosis.cause, "LOW"))
    if event.get("severity") == "HIGH":
        level += 1
    return LEVELS[min(level, 2)]


def draft_ticket(event: dict, rec: Recommendation, diagnosis: Diagnosis) -> dict:
    """Ticket body. The API assigns the ticket id and stores it when the operator approves."""
    return {"title": f"{event.get('device_id')}: {diagnosis.cause_text}",
            "device_id": event.get("device_id"), "zone": event.get("zone"),
            "event_id": event.get("event_id"), "cause": diagnosis.cause,
            "priority": rec.priority, "description": rec.action, "checklist": rec.checklist,
            "estimated_downtime_min": rec.estimated_downtime_min, "assign_to": rec.assign_to}


def _ensure_lockout(checklist: list[str], cause: str) -> list[str]:
    items = [c for c in checklist if c.strip()]
    if cause in HARDWARE and not any("lockout" in c.lower() for c in items):
        items.insert(0, LOCKOUT)
    return items


# ---- rules fallback (used when no LLM works) ----
def maintenance_rules(ctx: dict) -> Recommendation:
    return Recommendation(priority=ctx["priority"], action=ctx["action"], checklist=ctx["checklist"],
                          estimated_downtime_min=ctx["downtime"])


llm.register_rules("maintenance", maintenance_rules)


# ---- agent ----
def recommend(event: dict, diagnosis: Diagnosis, energy: Optional[EnergyImpact] = None):
    """Return (Recommendation | None, refusal reason | None, meta)."""
    event = strip_label(event)
    why = request_more_evidence(diagnosis)
    if why:
        return None, why, {"provider": "rules", "ms": 0, "tokens": 0, "fallbacks": []}

    cause = diagnosis.cause
    priority = set_priority(event, diagnosis)
    downtime = DOWNTIME_MIN.get(cause, 60)
    default_list = _ensure_lockout(CHECKLISTS.get(cause, CHECKLISTS["unknown"]), cause)
    ctx = {"cause": cause, "device": event.get("device_id"), "priority": priority, "downtime": downtime,
           "action": ACTIONS.get(cause, ACTIONS["unknown"]), "checklist": default_list}
    notes = [h["text"][:300] for h in search_procedures(cause)]
    prompt = f"""You are the Maintenance agent for an HVAC air handling unit.
Write the repair action (one sentence) and a checklist of 3 to 6 short steps for a technician.
Use only the facts and procedure notes below. Do not change the priority or the downtime.
Unit: {event.get('device_id')}
Diagnosed cause: {diagnosis.cause_text} (confidence {diagnosis.confidence})
Priority: {priority}
Estimated downtime: {downtime} minutes
Extra energy per day: {energy.extra_kwh_per_day if energy else 'unknown'} kWh
Procedure notes: {notes}
Return priority={priority} and estimated_downtime_min={downtime}."""
    rec, meta = llm.complete(prompt, Recommendation, "maintenance", context=ctx)
    rec = rec.model_copy(update={"priority": priority, "estimated_downtime_min": downtime,
                                 "checklist": _ensure_lockout(rec.checklist, cause) or default_list})
    return rec, None, meta