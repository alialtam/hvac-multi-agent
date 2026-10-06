"""HVAC API with the agent backend: dev_api.py plus the Supervisor graph.

    cd backend
    uvicorn app.api.main:app --port 8000 --host 0.0.0.0

Same endpoints as dev_api.py (devices, telemetry, /ws/live, /simulation*, /ingestion/rejected,
/settings/llm, /auth/check, demo-token middleware, triage), and in addition:
each confirmed anomaly runs the Supervisor graph in a background thread (handle(event) returns at once),
POST /incidents/{id}/approve and /reject resume or replan the graph, /tickets and /incidents/{id}/trace are real.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from app.api.runtime import AgentRuntime
from app.config import env
from app.detection.triage import TriageWorker
from app.ingestion.sim_control import SimCommand, SimControl, SimNotRunning
from app.ingestion.subscriber import Ingestion
from app.llm import llm
from app.security import auth_status, demo_token_guard

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
ROOT = Path(__file__).resolve().parents[3]
EXAMPLES = ROOT / "contracts" / "api_examples"
try:  # OPENAI_API_KEY etc. from the .env file in the repo root
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass


class Hub:
    """Fan-out of live messages to every connected dashboard."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def publish(self, kind: str, data: dict) -> None:
        # called from the MQTT and agent threads: hand over to the event loop
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._send(json.dumps({"type": kind, "data": data}, default=str)),
                                             self.loop)

    async def _send(self, text: str) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                self.clients.discard(ws)


hub = Hub()
incidents: dict[str, dict] = {}
activity: list[dict] = []
ticket_store: list[dict] = []
settings = {"provider": env("LLM_PROVIDER", "openai"),
            "available": ["openai", "ollama", "rules"],
            "models": {"openai": env("OPENAI_MODEL", "gpt-4o-mini"),
                       "ollama": env("OLLAMA_MODEL", "qwen2.5:1.5b"), "rules": None}}
traces: dict[str, list[dict]] = {}     # full execution trace per incident (JSON lines format)
by_event: dict[str, str] = {}          # event_id -> incident id
try:
    llm.set_provider(settings["provider"])
except ValueError:
    pass


def on_event(e: dict) -> None:
    """handle(event): show the incident at once, then triage and the agents update it."""
    e = {k: v for k, v in e.items() if k != "fault_label"}      # agents never see the label
    n = len(incidents) + 1
    inc_id = f"inc_{n:04d}"
    rule_names = ", ".join(e["rule_hits"]) or e["method"]
    summary = {
        "id": inc_id, "device_id": e["device_id"], "zone": e["zone"],
        "title": f"Anomaly on {e['device_id']} ({rule_names})", "severity": e["severity"],
        "state": "investigating", "confidence": e["score"], "created_at": e["ts_detected"],
        "updated_at": e["ts_detected"], "llm_provider": settings["provider"],
        "energy_cost_per_day": 0.0, "currency": "INR",
    }
    incidents[inc_id] = {
        **summary,
        "anomaly_event": {k: e[k] for k in ("event_id", "method", "score", "ts_start", "ts_detected", "rule_hits", "signals")},
        "trace": [{"ts": e["ts_detected"], "agent": "anomaly_detection",
                   "message": f"Anomaly confirmed ({e['method']}, score {e['score']:.2f}, {e['severity']})"}],
        "ticket_id": None,
    }
    step = {"ts": e["ts_detected"], "agent": "anomaly_detection", "incident_id": inc_id,
            "device_id": e["device_id"], "message": f"Anomaly confirmed ({e['severity']}): {rule_names}"}
    activity.insert(0, step)
    by_event[e["event_id"]] = inc_id
    hub.publish("incident", summary)
    hub.publish("agent_step", step)
    triage.submit(e)


TITLE = {"airflow": "Airflow problem", "cooling": "Cooling problem", "sensor": "Sensor fault",
         "schedule": "Running out of hours", "building": "Building-wide change"}
NEXT = {"diagnosis": "Diagnosis agent", "energy": "Energy agent", "maintenance": "Maintenance agent",
        "human_review": "a person"}


def _step_message(s: dict) -> str | None:
    c = s["content"]
    if s["type"] == "tool_call":
        return f"Checked {s['to'].split(':', 1)[1].replace('_', ' ')}: {c['result'].get('note', '')}"
    if s["type"] == "fallback":
        return f"LLM not used ({c['reason']}); triaged with rules."
    if s["type"] == "retry":
        return "LLM answer was invalid; asked it to correct it."
    return None


def on_triage(e: dict, report: dict) -> None:
    inc_id = by_event.get(e["event_id"])
    if not inc_id:
        return
    inc = incidents[inc_id]
    for s in report["steps"]:
        s["incident_id"] = inc_id
    runtime.add_triage(inc_id, report["steps"])          # triage steps come first in the trace
    inc["triage"] = {k: v for k, v in report.items() if k != "steps"}
    inc["llm_provider"] = report["provider"]
    if "device_offline" in e.get("rule_hits", []):
        inc["title"] = f"{e['device_id']} stopped reporting"
    elif report["verdict"] == "false_alarm":
        inc["title"] = f"Possible false alarm on {e['device_id']}"
    elif report["suspected_area"] in TITLE:
        inc["title"] = f"{TITLE[report['suspected_area']]} on {e['device_id']}"
    inc["severity"] = report["severity"]
    ts = e["ts_detected"]
    msgs = [m for m in map(_step_message, report["steps"]) if m]
    verdict = report["verdict"].replace("_", " ")
    msgs.append(f"Triage: {verdict}, {report['suspected_area']} ({report['confidence']:.0%} confident). "
                f"Hand over to {NEXT[report['recommend_next']]}.")
    inc["trace"] += [{"ts": ts, "agent": "anomaly_detection", "message": m} for m in msgs]
    step = {"ts": ts, "agent": "anomaly_detection", "incident_id": inc_id, "device_id": e["device_id"],
            "message": f"Triage ({report['provider']}): {verdict}, {report['summary']}"}
    activity.insert(0, step)
    dismissed = report["verdict"] == "false_alarm"       # triage dismissed it: the agents are not needed
    if dismissed:
        inc["state"] = "resolved"
    hub.publish("incident", {k: v for k, v in inc.items() if k not in ("anomaly_event", "trace", "ticket_id", "triage")})
    hub.publish("agent_step", step)
    if not dismissed:
        runtime.submit(inc_id, {**e, "triage": {k: v for k, v in report.items() if k != "steps"}})


ingestion = Ingestion(on_event=on_event, on_reading=lambda r: hub.publish("telemetry", r),
                      on_reject=lambda r: hub.publish("rejected", r))
simulator = SimControl(on_status=lambda s: hub.publish("simulation", s))
triage = TriageWorker(on_done=on_triage, store=ingestion.store, pipeline=ingestion.pipeline,
                      provider_fn=lambda: settings["provider"])
runtime = AgentRuntime(incidents, traces, activity, ticket_store, publish=hub.publish,
                       store=ingestion.store, pipeline=ingestion.pipeline)


@asynccontextmanager
async def lifespan(_: FastAPI):
    hub.loop = asyncio.get_running_loop()
    ingestion.start()
    runtime.start()
    simulator.start()
    yield
    simulator.stop()
    runtime.stop()
    ingestion.stop()


app = FastAPI(title="HVAC multi-agent API", lifespan=lifespan)
app.middleware("http")(demo_token_guard)     # write actions need the demo key when DEMO_TOKEN is set
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/auth/check")
def auth_check(request: Request):
    """Does this server need a demo key for write actions, and is the one sent valid?"""
    return auth_status(request)


@app.get("/health")
def health():
    return {"ok": True, "readings": ingestion.readings, "rejected": ingestion.rejected.count,
            "simulator_connected": simulator.status()["connected"]}


@app.get("/simulation")
def simulation_status():
    """Simulator clock, speed, active faults and offline units (for the Simulator page)."""
    return simulator.status()


@app.post("/simulation/command")
def simulation_command(cmd: SimCommand):
    """Validated command to the simulator. Bad input -> 422 before anything is sent."""
    try:
        out = simulator.send(cmd)
    except SimNotRunning as e:
        raise HTTPException(503, str(e))
    except TimeoutError as e:
        raise HTTPException(504, str(e))
    if not out["ok"]:
        raise HTTPException(400, out["message"])
    return out


@app.get("/ingestion/rejected")
def rejected_readings():
    """Readings that failed input validation, newest first, with the reason."""
    return ingestion.rejected.summary()


@app.get("/devices")
def devices():
    open_by_dev = {i["device_id"]: i["id"] for i in incidents.values() if i["state"] in ("investigating", "awaiting_approval")}
    out = []
    for r in ingestion.store.latest_readings():
        out.append({**r, "health": ingestion.pipeline.health(r["device_id"]),
                    "open_incident_id": open_by_dev.get(r["device_id"])})
    return out


@app.get("/devices/{device_id}/telemetry")
def telemetry(device_id: str, minutes: int = 60):
    return {"device_id": device_id, "minutes": minutes,
            "readings": ingestion.store.get_telemetry(device_id.upper(), minutes)}


@app.get("/incidents")
def list_incidents():
    return sorted(({k: v for k, v in i.items() if k not in ("anomaly_event", "trace", "ticket_id", "triage")}
                   for i in incidents.values()), key=lambda x: x["created_at"], reverse=True)


@app.get("/incidents/{inc_id}")
def get_incident(inc_id: str):
    if inc_id not in incidents:
        raise HTTPException(404, f"Incident {inc_id} not found")
    return incidents[inc_id]


@app.get("/incidents/{inc_id}/trace")
def get_trace(inc_id: str):
    """Full execution trace: triage steps first, then the Supervisor trace in the agreed format."""
    if inc_id not in incidents:
        raise HTTPException(404, f"Incident {inc_id} not found")
    return traces.get(inc_id, [])


class ApproveBody(BaseModel):
    operator: str = Field(min_length=1)
    note: str | None = None


class RejectBody(BaseModel):
    operator: str = Field(min_length=1)
    reason: str = Field(min_length=1, pattern=r"\S")


@app.post("/incidents/{inc_id}/approve")
def approve_incident(inc_id: str, body: ApproveBody):
    if inc_id not in incidents:
        raise HTTPException(404, f"Incident {inc_id} not found")
    try:
        return runtime.approve(inc_id, body.operator, body.note)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.post("/incidents/{inc_id}/reject")
def reject_incident(inc_id: str, body: RejectBody):
    if inc_id not in incidents:
        raise HTTPException(404, f"Incident {inc_id} not found")
    try:
        return runtime.reject(inc_id, body.operator, body.reason)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/agents/activity")
def get_activity(limit: int = 50):
    return activity[:limit]


@app.get("/tickets")
def tickets():
    return ticket_store


@app.get("/energy/summary")
def energy():
    return json.loads((EXAMPLES / "energy_summary.json").read_text())   # placeholder until it is computed from incidents


@app.get("/settings/llm")
def get_settings():
    return settings


@app.post("/settings/llm")
def set_settings(body: dict):
    if body.get("provider") not in settings["available"]:
        raise HTTPException(400, f"provider must be one of {settings['available']}")
    settings["provider"] = body["provider"]
    llm.set_provider(body["provider"])               # the agents switch too, not only triage
    hub.publish("settings", settings)
    return settings


@app.websocket("/ws/live")
async def ws_live(ws: WebSocket):
    await ws.accept()
    hub.clients.add(ws)
    try:
        while True:
            await ws.receive_text()   # keep the connection open; the client never needs to send
    except WebSocketDisconnect:
        hub.clients.discard(ws)