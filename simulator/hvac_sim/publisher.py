"""Where readings go: MQTT (Mosquitto), optional ThingsBoard, or the console."""
from __future__ import annotations

import json
import sys

try:
    import paho.mqtt.client as mqtt
except ImportError:  # console mode still works without paho
    mqtt = None


def _new_client(client_id: str):
    if mqtt is None:
        raise RuntimeError("paho-mqtt is not installed: pip install paho-mqtt")
    try:  # paho-mqtt 2.x
        return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id)
    except AttributeError:  # paho-mqtt 1.x
        return mqtt.Client(client_id=client_id)


class MqttPublisher:
    def __init__(self, host: str, port: int, topic: str, qos: int = 0):
        self.topic, self.qos = topic, qos
        self.client = _new_client("hvac-simulator")
        self.client.connect(host, port, keepalive=30)
        self.client.loop_start()

    def publish(self, reading: dict) -> None:
        topic = self.topic.format(device_id=reading["device_id"])
        self.client.publish(topic, json.dumps(reading), qos=self.qos)

    def close(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


class ThingsBoardPublisher:
    """One MQTT connection per device; the access token is the MQTT username."""

    FIELDS_TO_SKIP = {"device_id", "zone", "ts", "fault_label"}

    def __init__(self, host: str, port: int, tokens: dict[str, str]):
        self.clients = {}
        for device_id, token in tokens.items():
            if not token:
                continue
            c = _new_client(f"sim-tb-{device_id}")
            c.username_pw_set(token)
            c.connect(host, port, keepalive=30)
            c.loop_start()
            self.clients[device_id] = c

    def publish(self, reading: dict) -> None:
        c = self.clients.get(reading["device_id"])
        if c is None:
            return
        values = {k: v for k, v in reading.items() if k not in self.FIELDS_TO_SKIP}
        c.publish("v1/devices/me/telemetry", json.dumps(values))

    def close(self) -> None:
        for c in self.clients.values():
            c.loop_stop()
            c.disconnect()


class ConsolePublisher:
    def __init__(self, devices: set[str] | None = None):
        self.devices = devices

    def publish(self, reading: dict) -> None:
        if self.devices and reading["device_id"] not in self.devices:
            return
        r = reading
        sys.stdout.write(
            f"{r['ts']} {r['device_id']} {r['status']:>3} T={r['zone_temp_c']:5.2f} "
            f"Tsup={r['supply_temp_c']:5.2f} CFM={r['airflow_cfm']:6.0f} "
            f"kW={r['power_kw']:5.2f} occ={r['occupancy']:2d} CO2={r['co2_ppm']:5.0f} "
            f"[{r['fault_label']}]\n"
        )

    def close(self) -> None:
        pass
