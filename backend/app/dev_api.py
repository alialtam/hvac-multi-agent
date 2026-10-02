"""Stand-in API for Person 1: serves LIVE simulator data in the contract format.

    cd backend
    uvicorn app.dev_api:app --port 8000 --host 0.0.0.0

Implements the data endpoints (devices, telemetry, WebSocket) and turns each
anomaly event into a simple incident, so the dashboard can be rehearsed with
the real simulator before the agent backend exists. It has NO agents: incidents
stay in "Agents investigating" and there is no diagnosis.

Person 2: the device, telemetry and WebSocket parts can be copied into the real
backend as they are. Everything about incidents, tickets, energy and settings
here is a placeholder for your agents.
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from .ingestion.subscriber import Ingestion

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
EXAMPLES = Path(__file__).resolve().parents[2] / "contracts" / "api_examples"


class Hub:
    """Fan-out of live messages to every connected dashboard."""

    def __init__(self):
        self.clients: set[WebSocket] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def publish(self, kind: str, data: dict) -> None:
        # called from the MQTT thread: hand over to the event loop
        if self.loop:
            asyncio.run_coroutine_threadsafe(self._send(json.dumps({"type": kind, "data": data})), self.loop)

    async def _send(self, text: str) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_text(text)
            except Exception:
                self.clients.discard(ws)


hub = Hub()
incidents: dict[str, dict] = {}
activity: list[dict] = []
settings = json.loads((EXAMPLES / "settings_llm.json").read_text())


def on_event(e: dict) -> None:
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
    hub.publish("incident", summary)
    hub.publish("agent_step", step)


ingestion = Ingestion(on_event=on_event, on_reading=lambda r: hub.publish("telemetry", r))


@asynccontextmanager
async def lifespan(_: FastAPI):
    hub.loop = asyncio.get_running_loop()
    ingestion.start()
    yield
    ingestion.stop()


app = FastAPI(title="HVAC stand-in API (Person 1)", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
def health():
    return {"ok": True, "readings": ingestion.readings}


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
    return sorted(({k: v for k, v in i.items() if k not in ("anomaly_event", "trace", "ticket_id")}
                   for i in incidents.values()), key=lambda x: x["created_at"], reverse=True)


@app.get("/incidents/{inc_id}")
def get_incident(inc_id: str):
    if inc_id not in incidents:
        raise HTTPException(404, f"Incident {inc_id} not found")
    return incidents[inc_id]


@app.get("/agents/activity")
def get_activity(limit: int = 50):
    return activity[:limit]


@app.get("/tickets")
def tickets():
    return []


@app.get("/energy/summary")
def energy():
    return json.loads((EXAMPLES / "energy_summary.json").read_text())   # placeholder until the Energy agent exists


@app.get("/settings/llm")
def get_settings():
    return settings


@app.post("/settings/llm")
def set_settings(body: dict):
    if body.get("provider") not in settings["available"]:
        raise HTTPException(400, f"provider must be one of {settings['available']}")
    settings["provider"] = body["provider"]
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
