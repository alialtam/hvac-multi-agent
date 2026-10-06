# Deploying to Render (free, no card, about 15 minutes)

One free **web service** runs everything in one container: the MQTT broker, the simulator, the
backend with the agents, and the dashboard. One link, for example `https://hvac-ahd.onrender.com`.

| | |
| --- | --- |
| Dashboard | `https://<name>.onrender.com` |
| API | `https://<name>.onrender.com/api/...` (e.g. `/api/health`, `/api/ws/live`) |
| Memory | about 250 MB of the 512 MB free limit (measured) |

**Free-tier behaviour.** The service sleeps after 15 minutes without visitors; the next visit wakes
it in about a minute and the building restarts fresh (simulated clock at `SIM_START`, no old
incidents). Keep it awake for the demo and the grading period with a free uptime monitor (step 5):
one always-on service uses about 744 of Render's 750 free hours a month.

## 1. Create the service from the blueprint

1. render.com → sign in with GitHub (the account that owns `alialtam/hvac-multi-agent`).
2. **New +** → **Blueprint** → choose the repository `hvac-multi-agent` → **Connect**.
3. Render reads `render.yaml` and shows one service, `hvac-ahd`. It asks for **OPENAI_API_KEY**:
   paste your key (stored only in Render, never in GitHub). Click **Apply**.
4. The first build takes 5-10 minutes (dashboard build, Python packages, detector training).
   Follow it under the service → **Logs**. It is ready when the log shows
   `Uvicorn running on http://0.0.0.0:10000` and the status turns **Live**.

If the name `hvac-ahd` is taken, Render adds a few letters; the real link is at the top of the
service page.

## 2. Get the demo access key

Service → **Environment** → `DEMO_TOKEN` → click the eye icon. Render generated it. Viewing the
dashboard is public; injecting faults, approving incidents and switching the AI need this key
(**Unlock controls** at the top of the dashboard).

## 3. Check

- `https://<name>.onrender.com/api/health` returns `{"ok": true, ..., "simulator_connected": true}`.
- Open the dashboard: the top bar says **Live** and the building time moves.
- **Unlock controls** → key → **Simulator** → inject a fault → an incident opens within a few minutes.

## 4. Settings you may change (service → Environment)

| Key | Meaning |
| --- | --- |
| `LLM_PROVIDER` | `openai` (default), `rules` (no AI, free), `ollama` (not available on Render) |
| `OPENAI_MODEL` | default `gpt-4o-mini` |
| `SIM_START` | building clock after each start, e.g. `09:00` or `10:30` |
| `DEMO_TOKEN` | the access key; change it any time |

Saving a change restarts the service. New merges to `main` deploy automatically (`autoDeploy`).

## 5. Keep it awake (free)

uptimerobot.com → **New monitor** → HTTP(s) → URL `https://<name>.onrender.com/api/health`,
interval **5 minutes**, email alerts on. This also tells you if the site goes down.

## Laptop or Azure instead

The same system runs on a laptop (README) or on a VM with Docker (`deploy/README.md`).
