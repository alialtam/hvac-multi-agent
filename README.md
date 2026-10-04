# AI-Powered Multi-Agent IoT Monitoring and Predictive Maintenance System

Six simulated air handling units (AHUs) stream telemetry over MQTT. Five AI agents,
coordinated by a supervisor, detect faults, diagnose the likely cause, estimate the
energy cost and draft a maintenance ticket that a person approves on the dashboard.

```
simulator ──MQTT──> ingestion ──> Monitoring + Anomaly Detection agents ──event──> Supervisor (LangGraph)
 (6 AHUs)            (SQLite)        rules, z-score, Isolation Forest               Diagnosis, Energy, Maintenance
                                                                                         │
React dashboard <───────────── FastAPI REST + WebSocket <───────────────────────────────┘
```

## Who owns what

| Folder | Owner | What it is |
| --- | --- | --- |
| `contracts/` | everyone | The agreed data formats. Change only together. |
| `simulator/` | Ali | Physics-based HVAC simulator, 5 fault types, dataset generator |
| `backend/app/ingestion/` | Ali | MQTT subscriber, SQLite telemetry store |
| `backend/app/detection/` | Ali | Monitoring agent (rules) + Anomaly Detection agent (z-score, Isolation Forest, LLM triage with tools) |
| `backend/app/dev_api.py` | Ali | Stand-in API with live data until the real backend exists |
| `evaluation/` | Ali (detection), Abdulelah (diagnosis) | Experiments, metrics, charts for the report |
| `dashboard/` | Ali | React dashboard |
| `backend/app/llm/`, `backend/app/agents/` (schemas, supervisor, diagnosis), trace | Ahmed | LLM layer, LangGraph orchestration |
| `backend/app/knowledge/`, `backend/app/agents/` (energy, maintenance), `backend/app/api/` | Abdulelah | Specialist agents, knowledge base, real API |

**New to the repo? Read [`CONTRIBUTING.md`](CONTRIBUTING.md) first**, then [`docs/HANDOVER_PERSON2.md`](docs/HANDOVER_PERSON2.md) for how the agents receive data.

## Set up (Windows, once per laptop)

1. Install **Python 3.11**, **Node.js 20+**, **Git** and **Mosquitto** (mosquitto.org/download, Windows 64-bit installer).
2. Clone and create the Python environment:

   ```bat
   git clone <repo-url> hvac-agents
   cd hvac-agents
   py -3.11 -m venv venv
   venv\Scripts\activate
   pip install -r backend\requirements.txt -r simulator\requirements.txt
   ```

3. Generate the datasets and train the detector (about 1 minute):

   ```bat
   cd simulator
   python generate_dataset.py
   cd ..\backend
   python -m app.detection.train
   python -m pytest -q tests
   ```

4. Dashboard:

   ```bat
   cd dashboard
   npm install
   npm run dev
   ```

   Open http://localhost:5173. With no backend configured it runs on the example data in `contracts/` (the top bar says **Demo data**).

On macOS/Linux use `python3 -m venv venv` and `source venv/bin/activate`; everything else is the same.

## Run it live (one laptop)

Four terminals, each with `venv\Scripts\activate`:

| Terminal | Command | What you see |
| --- | --- | --- |
| 1 | `mosquitto -v` | broker log (skip if the Mosquitto service is already running) |
| 2 | `cd backend` then `uvicorn app.dev_api:app --port 8000 --host 0.0.0.0` | ingestion log, anomaly events |
| 3 | `cd simulator` then `python main.py --start 10:30` | type commands here, e.g. `inject AHU-4 filter_blockage` |
| 4 | `cd dashboard`, create `.env.local` with `VITE_API_URL=http://localhost:8000`, then `npm run dev` | dashboard, top bar says **Live** |

For AI triage, copy `.env.example` to `.env` in the repo root and set `OPENAI_API_KEY` (without a key it uses rules).
Once Person 2's backend exists, terminal 2 runs their app instead of `dev_api`.

## Run it on two laptops (the demo)

Both laptops on the same phone hotspot. Find Laptop B's IP with `ipconfig` (e.g. `192.168.43.20`).

- **Laptop B (AI + backend):** Mosquitto with `docs/mosquitto.conf` (it must listen on the network), the backend, Ollama.
- **Laptop A (data + screen):** `python main.py --host 192.168.43.20 --start 10:30` and the dashboard with `VITE_API_URL=http://192.168.43.20:8000`.
- Allow ports 1883, 8000 and 5173 in Windows Defender Firewall on Laptop B when Windows asks (tick "Private networks").

### Controlling the simulator from the dashboard

The **Simulator** page (left menu) does everything below with buttons: inject a fault on any unit,
reset, take a unit offline, change speed, jump the clock, run a scripted scenario, and send a
deliberately broken reading to show input validation. Commands go dashboard -> API -> MQTT
(`hvac/sim/control`) -> simulator, and are validated on both sides. The terminal commands still work.

### Simulator commands

| Command | Effect |
| --- | --- |
| `inject AHU-4 filter_blockage` | airflow drops 40% over 20 min; compressor works harder |
| `inject AHU-1 compressor_failure` | cooling stops at once; room warms about 3 °C in the first hour |
| `inject AHU-5 refrigerant_leak 90` | cooling capacity fades over 90 min (default 180) |
| `inject AHU-3 sensor_stuck` | room temperature reading freezes |
| `inject AHU-2 after_hours_waste` | unit runs at full fan speed in an empty building (use after 18:30) |
| `reset AHU-4`, `reset all` | clear faults |
| `offline AHU-6`, `online AHU-6` | stop / resume sending data |
| `jump 19:00` | move the building clock forward |
| `speed 2` | one reading every 2 s instead of 5 s |
| `run scenarios/demo_filter.yaml` | scripted demo (also `demo_after_hours.yaml`, `all_faults.yaml`) |
| `corrupt AHU-3 spike` | send ONE broken reading (`spike`, `missing`, `text`, `negative`): the backend must reject it |

At the default speed one reading = one simulated minute every 5 seconds (12x faster than real time).

## Detection results (test set, never used for tuning)

| Configuration | Faults detected | Median time to detect | False alarms in 12 days |
| --- | --- | --- | --- |
| Monitoring rules only | 46/50 | 14 min | 1 |
| z-score only | 45/50 | 9 min | 7 |
| Isolation Forest only | 33/50 | 50 min | 0 |
| **Full system** | **50/50** | **8 min** | **6** |

Reproduce with `python evaluation/evaluate_detection.py`. Details, charts and the method are in
`evaluation/results/` and `docs/REPORT_PERSON1.md`.

## Tests

`cd backend` then `python -m pytest -q tests` (about 2 minutes). They check the fault physics, the
contracts, ingestion, offline alerts, and that live detection gives exactly the same events as the
evaluated batch version.
