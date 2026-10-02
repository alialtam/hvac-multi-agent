"""MQTT -> SQLite -> detection. The entry point Person 2 uses inside FastAPI.

    from app.ingestion.subscriber import Ingestion

    ingestion = Ingestion(on_event=supervisor.handle,        # anomaly events (contract format)
                          on_reading=websocket_broadcast)    # optional: every reading, for /ws/live
    ingestion.start()      # in FastAPI's startup / lifespan
    ...
    ingestion.stop()       # on shutdown

    ingestion.store.latest_readings()          # for GET /devices
    ingestion.store.get_telemetry("AHU-4", 60) # for GET /devices/{id}/telemetry
    ingestion.pipeline.health("AHU-4")         # 'ok' | 'warning' | 'critical' | 'offline'

Callbacks run on the MQTT thread. Keep them quick (put work on a queue) or the
next readings wait. on_reading receives the reading WITHOUT fault_label.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Callable

from ..detection.pipeline import DetectionPipeline
from .db import TelemetryStore

log = logging.getLogger("ingestion")

try:
    import paho.mqtt.client as mqtt
except ImportError:  # pragma: no cover
    mqtt = None


class Ingestion:
    def __init__(self, on_event: Callable[[dict], None] | None = None,
                 on_reading: Callable[[dict], None] | None = None,
                 host: str | None = None, port: int | None = None,
                 topic: str = "hvac/+/telemetry", store: TelemetryStore | None = None,
                 pipeline: DetectionPipeline | None = None):
        self.host = host or os.getenv("MQTT_HOST", "localhost")
        self.port = int(port or os.getenv("MQTT_PORT", 1883))
        self.topic = topic
        self.store = store or TelemetryStore()
        self.on_event = on_event
        self.on_reading = on_reading
        self.pipeline = pipeline or DetectionPipeline(sink=self._handle_event)
        self.pipeline.sink = self._handle_event
        self.readings = 0
        self._stop = threading.Event()
        self._client = None

    # --------------------------------------------------------------- events
    def _handle_event(self, event: dict) -> None:
        self.store.add_event(event)
        log.warning("ANOMALY %s %s %s rules=%s", event["device_id"], event["severity"], event["method"],
                    event["rule_hits"])
        if self.on_event:
            try:
                self.on_event(event)
            except Exception:  # never let a downstream bug stop ingestion
                log.exception("on_event callback failed")

    def handle_message(self, payload: bytes | str) -> dict | None:
        """Process one raw MQTT payload. Public so it can be tested without a broker."""
        try:
            reading = json.loads(payload)
        except (ValueError, TypeError):
            log.warning("ignoring non-JSON message")
            return None
        if not isinstance(reading, dict) or "device_id" not in reading or "ts" not in reading:
            log.warning("ignoring message without device_id/ts")
            return None
        self.store.add_reading(reading)
        self.readings += 1
        if self.on_reading:
            try:
                self.on_reading({k: v for k, v in reading.items() if k != "fault_label"})
            except Exception:
                log.exception("on_reading callback failed")
        return self.pipeline.process(reading)

    # ------------------------------------------------------------------ mqtt
    def start(self) -> None:
        if mqtt is None:
            raise RuntimeError("paho-mqtt is not installed: pip install paho-mqtt")
        try:
            c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="hvac-ingestion")
        except AttributeError:  # paho 1.x
            c = mqtt.Client(client_id="hvac-ingestion")

        def on_connect(client, userdata, flags, reason_code, properties=None):
            log.info("connected to mqtt://%s:%s, subscribing to %s", self.host, self.port, self.topic)
            client.subscribe(self.topic)

        def on_message(client, userdata, msg):
            try:
                self.handle_message(msg.payload)
            except Exception:
                log.exception("failed to process message on %s", msg.topic)

        c.on_connect = on_connect
        c.on_message = on_message
        c.reconnect_delay_set(min_delay=1, max_delay=10)
        c.connect_async(self.host, self.port, keepalive=30)
        c.loop_start()
        self._client = c
        threading.Thread(target=self._offline_loop, daemon=True, name="offline-watch").start()

    def _offline_loop(self) -> None:
        while not self._stop.wait(5):
            for e in self.pipeline.check_offline():
                log.warning("%s went offline", e["device_id"])

    def stop(self) -> None:
        self._stop.set()
        if self._client:
            self._client.loop_stop()
            self._client.disconnect()
