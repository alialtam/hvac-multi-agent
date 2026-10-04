# REST API contract

Base URL: `http://<laptop-B-ip>:8000` (served by FastAPI, Person 2).
All times are ISO 8601 UTC strings. All responses are JSON.
Example responses live in `api_examples/<file>.json` (file name in the table).

| Method | Path | Example file | Notes |
| --- | --- | --- | --- |
| GET | `/devices` | `devices.json` | Latest reading + health per AHU. `health`: `ok` \| `warning` \| `critical` \| `offline` |
| GET | `/devices/{id}/telemetry?minutes=60` | `device_telemetry.json` | Oldest first. Same fields as the telemetry contract, without `fault_label` |
| GET | `/incidents` | `incidents.json` | Newest first. `state`: `investigating` \| `awaiting_approval` \| `approved` \| `rejected` \| `resolved` |
| GET | `/incidents/{id}` | `incident_detail.json` | Full incident incl. triage, diagnosis, energy, recommendation, agent trace |
| GET | `/incidents/{id}/trace` | (list of trace lines) | Full execution trace: every LLM call, tool call and decision, format in `backend/app/agents/README.md` |
| GET | `/simulation` | `simulation.json` | Simulator clock, speed, active faults, offline units. `connected: false` when the simulator is not running |
| POST | `/simulation/command` | `simulation_command.json` | Body: `{"action": "inject"\|"reset"\|"offline"\|"online"\|"jump"\|"speed"\|"corrupt"\|"scenario", ...}`. Validated: bad input -> 422, simulator not running -> 503, simulator refused -> 400 |
| GET | `/ingestion/rejected` | `rejected_readings.json` | Readings that failed input validation, newest first, with the reason |
| POST | `/incidents/{id}/approve` | `incident_approve.json` | Body: `{"operator": "name", "note": "optional"}`. Returns the updated incident + ticket id |
| POST | `/incidents/{id}/reject` | `incident_reject.json` | Body: `{"operator": "name", "reason": "required"}` |
| GET | `/tickets` | `tickets.json` | Newest first |
| GET | `/energy/summary` | `energy_summary.json` | kWh today per AHU, baseline, waste, cost |
| GET | `/agents/activity?limit=50` | `agents_activity.json` | Newest first |
| GET | `/settings/llm` | `settings_llm.json` | Current provider + available providers |
| POST | `/settings/llm` | `settings_llm.json` | Body: `{"provider": "openai" \| "ollama" \| "rules"}` |
| WS | `/ws/live` | `ws_messages.json` | Server pushes `{"type": ..., "data": ...}` messages |

## WebSocket message types

| `type` | `data` |
| --- | --- |
| `telemetry` | one telemetry reading (no `fault_label`) |
| `incident` | an incident summary (same shape as one item of `/incidents`) when created or its state changes |
| `agent_step` | one item of `/agents/activity` |
| `settings` | the `/settings/llm` object after a switch |

## CORS

The backend must allow the dashboard origin (`http://localhost:5173` in development,
or any origin during the demo).
