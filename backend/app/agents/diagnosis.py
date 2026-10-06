"""Diagnosis agent: gathers evidence with tools, asks the LLM, falls back to rules."""
from app.agents.schemas import Diagnosis
from app.knowledge import search_knowledge
from app.llm import llm

CAUSES = ["filter_blockage", "compressor_failure", "refrigerant_leak",
          "sensor_stuck", "after_hours_waste", "unknown"]


SIGNATURES = """How each cause looks in the signals (z = standard deviations from this unit's normal). Use the DIRECTION of the change:
- filter_blockage: airflow_cfm falls well below normal at the SAME fan speed (z -3 or lower); power_kw stays normal or RISES; supply air gets colder.
- compressor_failure: power_kw COLLAPSES (z -3 or lower) while supply_temp_c rises and coil_dt_c falls toward zero or negative; airflow stays normal.
- refrigerant_leak: airflow stays normal; power_kw RISES moderately (z about +3 to +6) as the compressor works harder; supply_temp_c and coil_dt_c may drift only slightly. Power does NOT collapse.
- sensor_stuck: zone_temp_c has exactly the same value in the last 8 or more readings while the other signals still change.
- after_hours_waste: the unit is ON at 60% fan speed or more while occupancy is 0, outside the 07:00-18:30 schedule (check ts and occupancy in the recent readings). Signals can look normal.
Power direction separates compressor_failure (collapses) from refrigerant_leak (rises). If no signature fits, answer unknown with confidence below 0.5."""


def strip_label(event: dict) -> dict:
    return {k: v for k, v in event.items() if k != "fault_label"}


# ---- tools ----
def get_recent_readings(event: dict, n: int = 10) -> list[dict]:
    return event.get("window", [])[-n:]


def compare_with_baseline(event: dict) -> list[dict]:
    return [{"key": s["key"], "value": s["value"], "baseline": s["baseline"], "z": s["z"]}
            for s in event.get("signals", [])]


def get_peer_units(event: dict, peers=None, store=None, pipeline=None) -> dict:
    """Are the other units abnormal too? Live: pass store and pipeline. Tests and evaluation:
    pass peers, a function event -> [{"device_id", "state"}]."""
    from app.detection.triage_tools import TriageTools
    return TriageTools(event, store=store, pipeline=pipeline, peers=peers).run("other_units")


def get_incident_history(query: str, k: int = 3) -> list[dict]:
    return [h for h in search_knowledge(query, 6) if h["source"].startswith("incident")][:k]


# ---- agent ----
def diagnose(event: dict, more_evidence: bool = False, critique: str | None = None,
             peers=None, store=None, pipeline=None):
    """Return (Diagnosis, meta). more_evidence=True pulls a longer window and peer units."""
    event = strip_label(event)
    signals = compare_with_baseline(event)
    recent = get_recent_readings(event, 30 if more_evidence else 10)
    query = " ".join([s["key"] for s in signals if abs(s["z"]) >= 2] + event.get("rule_hits", []))
    kb = search_knowledge(query or "commissioning baseline", 3)
    history = get_incident_history(query or "incident", 2)

    prompt = f"""You are the Diagnosis agent for an HVAC air handling unit.
Pick the most likely cause from: {CAUSES}.
Only use the evidence below. Do not invent measurements.
{SIGNATURES}
Unit: {event.get('device_id')}  Rule hits: {event.get('rule_hits', [])}
Signals vs baseline: {signals}
Recent readings: {recent}
Peer units: {get_peer_units(event, peers, store, pipeline).get('note')}
Knowledge notes: {[h['text'][:300] for h in kb]}
Similar past incidents: {[h['text'][:200] for h in history]}
{('Another agent disagrees: ' + critique) if critique else ''}
Return cause, cause_text, confidence (0-1), evidence (list of facts you used), sources."""

    diag, meta = llm.complete(prompt, Diagnosis, "diagnosis", context=event)
    if not diag.sources:
        diag = diag.model_copy(update={"sources": [h["source"] for h in kb]})
    return diag, meta