# Handover for Person 2: plugging the agents into the data side

Everything up to the anomaly event is done and tested. You receive one Python
dict per confirmed anomaly and build the agents from there.

## 1. The one line that connects us

In your FastAPI app:

```python
from contextlib import asynccontextmanager
from app.ingestion.subscriber import Ingestion

ingestion = Ingestion(
    on_event=supervisor.handle,          # your function: receives the anomaly event dict
    on_reading=broadcast_telemetry,      # optional: every reading (no fault_label), for /ws/live
)

@asynccontextmanager
async def lifespan(app):
    ingestion.start()      # connects to MQTT (env MQTT_HOST, default localhost)
    yield
    ingestion.stop()
```

Both callbacks run on the MQTT thread, once per reading or event. **Return fast**:
put the event on a queue or `asyncio.run_coroutine_threadsafe(...)` it onto your
loop; do not call the LLM inside the callback, or ingestion stalls for that long.
`backend/app/dev_api.py` shows the thread-to-asyncio hand-off (the `Hub` class).

## 2. What an anomaly event looks like

Schema: `contracts/anomaly_event.schema.json`. Real example: `contracts/sample_anomaly_event.json`.

| Field | Use it for |
| --- | --- |
| `device_id`, `zone` | which AHU |
| `ts_start`, `ts_detected` | when it started looking abnormal / when it was confirmed (simulated time) |
| `severity` | `LOW` / `MEDIUM` / `HIGH`. Suggestion: only open incidents for MEDIUM and HIGH, or treat LOW as "watch". |
| `method` | which detector fired: `zscore`, `isolation_forest`, `combined`, `rule` |
| `score` | 0..1, how abnormal |
| `signals` | up to 5 sensors furthest from normal, each with `value`, `baseline` (normal for this hour) and `z` |
| `rule_hits` | Monitoring agent rules that fired: `zone_too_warm`, `fast_temp_rise`, `low_airflow`, `sensor_flatline`, `running_unoccupied`, `device_offline` |
| `window` | the last 30 readings of this AHU, so the Diagnosis agent has context without another query |

`device_offline` events have empty `signals`; the window holds the last readings before it went silent.

**Triage (optional field `triage`).** Person 1's triage agent (`app/detection/triage.py`) checks each
event with an LLM and tools and adds a verdict, the suspected area, evidence and `recommend_next`
(`diagnosis` / `energy` / `maintenance` / `human_review`). The Supervisor can use `recommend_next`
as a routing hint and pass `key_evidence` to the Diagnosis prompt. To get events with triage attached:

```python
from app.detection.triage import TriageWorker

triage = TriageWorker(on_done=lambda event, report: supervisor.handle({**event, "triage": report}),
                      store=ingestion.store, pipeline=ingestion.pipeline,
                      provider_fn=lambda: current_provider)       # "openai" | "ollama" | "rules"
ingestion.on_event = triage.submit
```

`report["steps"]` holds its execution-trace lines (same format as yours, `from: anomaly_detection`);
add them to the incident's trace. `dev_api.py` shows the full wiring.

## 3. Fault signatures (for prompts and the rule-based baseline)

Measured from the simulator; good material for the knowledge base and for your if/then baseline.

| Fault | Airflow | Power | Supply air | Room temp | Other |
| --- | --- | --- | --- | --- | --- |
| Filter blockage | down 40% | **up** about 2 kW (compressor compensates) | colder (16 to 11 °C) | holds, rises only on the hottest afternoons | CO₂ rises (less fresh air); rule `low_airflow` |
| Compressor failure | normal | **collapses** to about 0.8 kW (fan only) | rises to room temperature | rises about 3 °C in the first hour | humidity rises |
| Refrigerant leak | normal | creeps up over hours (compressor at full load) | slowly warmer | drifts up in the afternoon | slow: detected after 1.5 to 5 hours |
| Sensor stuck | normal | drifts | drifts | **identical value every minute** | rule `sensor_flatline` |
| After-hours waste | about 3000 CFM (100% fan) at night | 3 to 6 kW instead of 0.05 | cold | held at setpoint | occupancy 0; rule `running_unoccupied` |

## 4. Data you can read for the API

```python
ingestion.store.latest_readings()           # GET /devices (add "health")
ingestion.store.get_telemetry("AHU-4", 60)  # GET /devices/{id}/telemetry
ingestion.pipeline.health("AHU-4")          # "ok" | "warning" | "critical" | "offline"
ingestion.store.recent_events(50)           # anomaly events, newest first
```

"Minutes" are simulated minutes. The simulator runs 12x faster than real time, so
never compare simulated timestamps with the wall clock (e.g. for "last hour" use the
newest reading's `ts`, which `get_telemetry` already does).

Your tables (incidents, tickets, approvals, agent logs) can live in the same SQLite
file: `from app.ingestion.db import get_engine, metadata` and define tables on `metadata`.

## 5. What the dashboard expects from you

`contracts/api.md` lists every endpoint with an example response in
`contracts/api_examples/`. The dashboard is built and tested against those files.
If you need to change a shape, tell Person 1 first.

Things the dashboard relies on:

- `GET /devices` items include `health` and `open_incident_id` (the dashboard links the zone to the incident).
- `incident.state` uses exactly: `investigating`, `awaiting_approval`, `approved`, `rejected`, `resolved`. The Approve / Reject buttons only appear in `awaiting_approval`.
- `incident.trace` is the agent hand-off timeline shown on the incident page; `/agents/activity` feeds the activity list.
- `POST /settings/llm` with `{"provider": "openai" | "ollama" | "rules"}` is the live AI switch in the top bar.
- CORS must allow the dashboard origin (any origin is fine for the demo).
- `/ws/live` is optional: without it the dashboard polls every 5 s. With it, push `{"type": "telemetry" | "incident" | "agent_step" | "settings", "data": {...}}` and the dashboard refreshes at once.

## 6. Test data without running anything

- `data/sample_day.csv`: one day, 6 AHUs, 5 faults (AHU-5 refrigerant leak 09:00, AHU-1 compressor failure 10:00, AHU-4 filter blockage 11:00, AHU-3 sensor stuck 13:00, AHU-2 after-hours waste 19:00).
- `contracts/sample_anomaly_event.json`: a real event to develop the Supervisor against.
- To get all events of the sample day as JSON files:

  ```python
  import json, pandas as pd
  from app.detection.anomaly_agent import AnomalyDetector, detect_batch
  det = AnomalyDetector.load("../data/models/detector.joblib")
  _, events = detect_batch(det, pd.read_csv("../data/sample_day.csv"), with_window=True)
  for e in events:
      open(f"{e['event_id']}.json", "w").write(json.dumps(e, indent=2))
  ```

## 7. Evaluating diagnosis (your Results section)

`data/test_faults.csv.gz` + `data/fault_log.csv` hold 50 labelled faults. Run detection
in batch (as above), feed every event to your Diagnosis agent with each provider
(`openai`, `ollama`, `rules`), and compare the predicted cause with the `fault` column of
the log for the same device and time. `evaluation/metrics.py` (`event_metrics`) matches
events to faults; reuse it so both halves of the report use the same rules.

## 8. Simulator control and input validation (copy into the real API)

Abdulelah: the real API needs three more endpoints the dashboard uses. Copy them from
`backend/app/dev_api.py` as they are (about 30 lines):

| Endpoint | What |
| --- | --- |
| `GET /simulation` | simulator status (`SimControl.status()`) |
| `POST /simulation/command` | validated command (`SimCommand` model): 422 bad input, 503 simulator not running, 400 refused |
| `GET /ingestion/rejected` | readings that failed validation (`ingestion.rejected.summary()`) |

Create `SimControl(on_status=...)` next to `Ingestion(...)` and start/stop both in the lifespan.
Pass `on_reject=` to `Ingestion` if you want rejected readings pushed over the WebSocket.
