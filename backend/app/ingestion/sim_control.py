"""Backend side of the simulator remote control (the dashboard's Simulator page).

    sim = SimControl(); sim.start()
    sim.status()                                  # last status the simulator published
    sim.send(SimCommand(action="inject", device_id="AHU-4", fault="filter_blockage"))

Commands are validated here (Pydantic) BEFORE anything is sent: unknown actions,
devices, faults or malformed times are refused with a clear message (HTTP 422).
The simulator checks them again on its side.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ..config import env

log = logging.getLogger("simcontrol")

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None

CONTROL, REPLY, STATUS = "hvac/sim/control", "hvac/sim/reply", "hvac/sim/status"
FAULTS = ("filter_blockage", "compressor_failure", "refrigerant_leak", "sensor_stuck", "after_hours_waste")
STALE_S = 20.0   # no status for this long (real seconds) = simulator not running


class SimCommand(BaseModel):
    action: Literal["inject", "reset", "offline", "online", "jump", "speed", "corrupt", "scenario"]
    device_id: str | None = Field(default=None, pattern=r"^(AHU-[0-9]+|all)$")
    fault: Literal[FAULTS] | None = None                       # type: ignore[valid-type]
    ramp_min: float | None = Field(default=None, ge=1, le=600)
    time: str | None = Field(default=None, pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    seconds_per_reading: float | None = Field(default=None, ge=0.2, le=30)
    kind: Literal["spike", "missing", "text", "negative"] | None = None
    scenario: str | None = Field(default=None, pattern=r"^[a-z0-9_]+$")

    @model_validator(mode="after")
    def _needs(self):
        need = {"inject": ["device_id", "fault"], "reset": [], "offline": ["device_id"], "online": ["device_id"],
                "jump": ["time"], "speed": ["seconds_per_reading"], "corrupt": ["device_id"],
                "scenario": ["scenario"]}[self.action]
        missing = [f for f in need if getattr(self, f) is None]
        if missing:
            raise ValueError(f"'{self.action}' needs {', '.join(missing)}")
        if self.action in ("inject", "offline", "online", "corrupt") and self.device_id == "all":
            raise ValueError(f"'{self.action}' needs one unit, not 'all'")
        return self

    def to_cmd(self) -> str:
        a = self.action
        if a == "inject":
            return f"inject {self.device_id} {self.fault}" + (f" {self.ramp_min:g}" if self.ramp_min else "")
        if a == "reset":
            return f"reset {self.device_id or 'all'}"
        if a in ("offline", "online"):
            return f"{a} {self.device_id}"
        if a == "jump":
            return f"jump {self.time}"
        if a == "speed":
            return f"speed {self.seconds_per_reading:g}"
        if a == "corrupt":
            return f"corrupt {self.device_id} {self.kind or 'spike'}"
        return f"run {self.scenario}"


class SimNotRunning(RuntimeError):
    pass


class SimControl:
    def __init__(self, host: str | None = None, port: int | None = None, on_status=None):
        self.host = host or env("MQTT_HOST", "localhost")
        self.port = int(port or env("MQTT_PORT", "1883"))
        self.on_status = on_status
        self._status: dict = {}
        self._status_at = 0.0
        self._waiting: dict[str, tuple[threading.Event, dict]] = {}
        self._client = None

    def start(self) -> None:
        if mqtt is None:
            return
        cid = f"hvac-backend-simctl-{uuid.uuid4().hex[:8]}"
        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        except AttributeError:  # paho 1.x
            c = mqtt.Client(client_id=cid)
        c.on_connect = lambda cl, *a: (cl.subscribe(STATUS), cl.subscribe(REPLY))
        c.on_message = self._on_message
        c.reconnect_delay_set(1, 10)
        c.connect_async(self.host, self.port, keepalive=30)
        c.loop_start()
        self._client = c

    def stop(self) -> None:
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()

    def _on_message(self, client, userdata, msg) -> None:
        try:
            body = json.loads(msg.payload)
        except ValueError:
            return
        if msg.topic == STATUS:
            self._status, self._status_at = body, time.monotonic()
            if self.on_status:
                self.on_status(self.status())
        elif msg.topic == REPLY and body.get("id") in self._waiting:
            if isinstance(body.get("status"), dict):
                self._status, self._status_at = body["status"], time.monotonic()
            ev, box = self._waiting[body["id"]]
            box.update(body)
            ev.set()

    def status(self) -> dict:
        fresh = time.monotonic() - self._status_at < STALE_S
        connected = bool(self._status) and fresh and self._status.get("running", True)
        return {**self._status, "connected": connected}

    def send(self, command: SimCommand, timeout: float = 15.0) -> dict:
        if not self._client or not self.status()["connected"]:
            raise SimNotRunning("The simulator is not running or not connected to the broker. "
                                "Start it with: cd simulator, then python main.py")
        rid = uuid.uuid4().hex[:12]
        ev, box = threading.Event(), {}
        self._waiting[rid] = (ev, box)
        try:
            cmd = command.to_cmd()
            self._client.publish(CONTROL, json.dumps({"id": rid, "cmd": cmd}))
            if not ev.wait(timeout):
                raise TimeoutError(f"The simulator did not answer '{cmd}' within {timeout:g} s")
            log.info("simulator command %r -> %s", cmd, box.get("message"))
            return {"command": cmd, "ok": bool(box.get("ok")), "message": box.get("message", ""),
                    "status": self.status()}
        finally:
            self._waiting.pop(rid, None)
