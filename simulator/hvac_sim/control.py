"""Remote control of the live simulator over MQTT, used by the dashboard's Simulator page.

    hvac/sim/control  <- {"id": "abc", "cmd": "inject AHU-4 filter_blockage"}   (from the backend)
    hvac/sim/reply    -> {"id": "abc", "ok": true, "message": "..."}
    hvac/sim/status   -> clock, speed, active faults, offline units   (retained, every reading)

Only a fixed set of commands is accepted from the network (no quit, no file paths):
the same commands as the terminal, so terminal and dashboard always behave the same.
"""
from __future__ import annotations

import json
import re
import uuid

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None

PREFIX = "hvac/sim"
CONTROL, REPLY, STATUS = f"{PREFIX}/control", f"{PREFIX}/reply", f"{PREFIX}/status"
ALLOWED = {"inject", "reset", "offline", "online", "jump", "speed", "status", "corrupt", "run"}
SCENARIO_RE = re.compile(r"^[a-z0-9_]+$")


def check_remote_command(cmd: str, scenarios: list[str]) -> str | None:
    """None if the command may run, otherwise why not."""
    parts = cmd.strip().split()
    if not parts:
        return "empty command"
    if parts[0].lower() not in ALLOWED:
        return f"'{parts[0]}' is not allowed remotely"
    if parts[0].lower() == "run":
        name = parts[1] if len(parts) > 1 else ""
        if not SCENARIO_RE.match(name) or name not in scenarios:
            return f"unknown scenario '{name}'. Available: {', '.join(scenarios)}"
    return None


def is_error(message: str) -> bool:
    return message.startswith(("error", "unknown", "usage"))


class RemoteControl:
    def __init__(self, sim, host: str, port: int):
        if mqtt is None:
            raise RuntimeError("paho-mqtt is not installed")
        self.sim = sim
        cid = f"hvac-sim-control-{uuid.uuid4().hex[:8]}"
        try:
            self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid)
        except AttributeError:  # paho 1.x
            self.client = mqtt.Client(client_id=cid)
        self.client.on_connect = lambda c, *a: c.subscribe(CONTROL)
        self.client.on_message = self._on_message
        # if the simulator dies, the broker tells the dashboard it is gone
        self.client.will_set(STATUS, json.dumps({"running": False}), retain=True)
        self.client.reconnect_delay_set(1, 10)
        self.client.connect(host, port, keepalive=30)
        self.client.loop_start()

    def _on_message(self, client, userdata, msg) -> None:
        try:
            body = json.loads(msg.payload)
            cmd, rid = str(body.get("cmd", "")), body.get("id")
        except (ValueError, AttributeError):
            return
        problem = check_remote_command(cmd, self.sim.scenario_names())
        if problem:
            reply = {"id": rid, "ok": False, "message": f"error: {problem}"}
        else:
            out = self.sim.handle(cmd)
            print(f"dashboard> {cmd} -> {out}")
            reply = {"id": rid, "ok": not is_error(out), "message": out}
        reply["status"] = self.sim.status_dict()          # so the caller sees the effect at once
        client.publish(REPLY, json.dumps(reply))
        self.publish_status()

    def publish_status(self) -> None:
        self.client.publish(STATUS, json.dumps(self.sim.status_dict()), retain=True)

    def close(self) -> None:
        self.client.publish(STATUS, json.dumps({"running": False}), retain=True).wait_for_publish(2)
        self.client.loop_stop()
        self.client.disconnect()
