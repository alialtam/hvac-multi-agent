# Integration contracts

These files are the agreement between Person 1 (IoT data, detection, dashboard)
and Person 2 (agents, LLM, backend). **Do not change them without telling the other person.**

| File | Producer | Consumer |
| --- | --- | --- |
| `telemetry.schema.json` + `sample_telemetry.json` | Person 1 (simulator) | Ingestion, detection, dashboard |
| `anomaly_event.schema.json` + `sample_anomaly_event.json` | Person 1 (detection) | Person 2 (`supervisor.handle(event)`) |
| `api.md` + `api_examples/*.json` | Person 2 (FastAPI) | Person 1 (dashboard) |

## 1. Telemetry message

- MQTT topic: `hvac/{device_id}/telemetry` (e.g. `hvac/AHU-4/telemetry`)
- One message per AHU per simulated minute (every 5 real seconds at the default 12x speed)
- `fault_label` is ground truth for evaluation only. **Agents and detection must never read it.**

## 2. Anomaly event

Produced by `backend/app/detection` and passed as a plain Python dict to a sink
function. In the full system the sink is `supervisor.handle(event)` (Person 2).

- `severity`: `LOW` | `MEDIUM` | `HIGH`
- `method`: `zscore` | `isolation_forest` | `combined` | `rule`
- `rule_hits`: names of the Monitoring agent rules that fired (may be empty)
- `signals`: the sensors that deviate most, sorted by |z|, at most 5
- `window`: the last 30 readings of the device, so the Diagnosis agent does not have to query them again

## 3. REST API

See `api.md`. Each endpoint has one example response in `api_examples/`.
The dashboard is built against these files until the real backend is ready.
