# Agents (Ahmed: schemas, Supervisor, Diagnosis, trace · Abdulelah: Energy, Maintenance)

This folder is empty on purpose: you build it. This file says what it must do so the rest of
the system (detection, API, dashboard) fits around it. Framework: **LangGraph**.

## Suggested files

```
agents/
  schemas.py        # shared Pydantic models: write together on day 1 (Ahmed + Abdulelah)
  graph.py          # LangGraph StateGraph: nodes, routing, revision loop, human interrupt (Ahmed)
  supervisor.py     # Supervisor agent: LLM decides the next step (Ahmed)
  diagnosis.py      # Diagnosis agent + its tools (Ahmed)
  energy.py         # Energy agent + its tools (Abdulelah)
  maintenance.py    # Maintenance agent + its tools (Abdulelah)
  trace.py          # execution trace logger (Ahmed)
  tools/            # plain Python functions the agents can call
```

## Entry point the data side calls

```python
def handle(event: dict) -> None:
    """Called once per confirmed anomaly event (contracts/anomaly_event.schema.json).
    Must return quickly: queue the event and run the graph in the background."""
```

Wire it with `Ingestion(on_event=handle)` (see `docs/HANDOVER_PERSON2.md`).
Real events to develop with: `contracts/sample_events/*.json` (one per fault type).

## Shared models (`schemas.py`): agree these first

| Model | Fields (suggestion) |
| --- | --- |
| `Diagnosis` | `cause` (one of the 5 fault names or `unknown`), `cause_text`, `confidence` 0..1, `evidence: list[str]`, `sources: list[str]` |
| `EnergyImpact` | `extra_kwh_per_day`, `cost_per_day`, `currency`, `explanation`, `consistent_with_diagnosis: bool`, `conflict_note: str \| None` |
| `Recommendation` | `priority` LOW/MEDIUM/HIGH, `action`, `checklist: list[str]`, `estimated_downtime_min`, `assign_to` |
| `Critique` | `from_agent`, `to_agent`, `issue`, `request` (what the other agent should do) |
| `IncidentState` | `incident_id`, `event`, `diagnosis`, `energy`, `recommendation`, `critiques`, `revision_count`, `next_agent`, `human_decision`, `status` |

The incident page in the dashboard shows exactly these (see `contracts/api_examples/incident_detail.json`).

## Behaviours the evaluators look for (must be visible in the execution trace)

1. **Dynamic routing**: the Supervisor's LLM picks the next agent from the evidence
   (after-hours waste → Energy first; frozen sensor → Maintenance without an energy estimate).
2. **Conflict resolution**: Energy critiques a diagnosis its numbers contradict; the Supervisor
   sends the case back to Diagnosis (max 2 revisions), then escalates both views to the human.
3. **Asking for more evidence**: Maintenance refuses to draft a ticket below 0.6 confidence;
   Diagnosis calls extra tools and tries again.
4. **Replanning after rejection**: the operator's reject reason becomes new evidence.
5. **Failure handling**: LLM/tool failure → retry → other provider → rules, logged in the trace.

## Execution trace

Every message, tool call and decision is one JSON line:

```json
{"ts": "...", "incident_id": "inc_0007", "from": "energy", "to": "supervisor",
 "type": "critique", "content": {...}, "llm_provider": "openai", "duration_ms": 812}
```

Save to the database and expose `GET /incidents/{id}/trace`. Never edit a trace by hand;
the submitted trace must be a real captured run.
