"""SQLite storage for telemetry and anomaly events (Person 1's tables).

Person 2 adds their own tables (incidents, tickets, ...) to the same database file.
Set DATABASE_URL to switch to PostgreSQL later; nothing else changes.

Helper queries for the API:
    latest_readings()                  -> one dict per device (newest reading)
    get_telemetry("AHU-4", minutes=60) -> list of dicts, oldest first
    recent_events(limit=50)            -> anomaly events, newest first

NOTE: "minutes" is SIMULATED time (relative to the device's newest reading),
because the simulator clock runs 12x faster than the wall clock.
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import (JSON, Column, DateTime, Float, Integer, MetaData, String, Table, create_engine, event,
                        func, insert, select)

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_URL = f"sqlite:///{(ROOT / 'data' / 'hvac.db').as_posix()}"

metadata = MetaData()

telemetry = Table(
    "telemetry", metadata,
    Column("id", Integer, primary_key=True),
    Column("device_id", String(16), index=True, nullable=False),
    Column("zone", String(32)),
    Column("ts", DateTime, index=True, nullable=False),      # simulated time (UTC, naive)
    Column("zone_temp_c", Float), Column("supply_temp_c", Float), Column("setpoint_c", Float),
    Column("humidity_pct", Float), Column("airflow_cfm", Float), Column("fan_speed_pct", Float),
    Column("power_kw", Float), Column("occupancy", Integer), Column("co2_ppm", Float),
    Column("status", String(4)), Column("outdoor_temp_c", Float),
    Column("fault_label", String(32)),                        # ground truth, for evaluation only
    Column("received_at", DateTime, server_default=func.current_timestamp()),
)

anomaly_events = Table(
    "anomaly_events", metadata,
    Column("id", Integer, primary_key=True),
    Column("event_id", String(64), unique=True, nullable=False),
    Column("device_id", String(16), index=True),
    Column("ts_detected", DateTime, index=True),
    Column("severity", String(8)),
    Column("method", String(24)),
    Column("payload", JSON),
)

TELEMETRY_FIELDS = [c.name for c in telemetry.columns if c.name not in ("id", "received_at")]
PUBLIC_FIELDS = [f for f in TELEMETRY_FIELDS if f != "fault_label"]


def _parse_ts(ts: str) -> datetime:
    return datetime.strptime(ts.replace("Z", ""), "%Y-%m-%dT%H:%M:%S")


def _fmt_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def get_engine(url: str | None = None):
    url = url or os.getenv("DATABASE_URL", DEFAULT_URL)
    if url.startswith("sqlite:///"):
        Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _wal(dbapi_conn, _):   # readers (API) and the writer (ingestion) do not block each other
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA synchronous=NORMAL")
    metadata.create_all(engine)
    return engine


class TelemetryStore:
    def __init__(self, engine=None):
        self.engine = engine or get_engine()

    # ------------------------------------------------------------- writes
    def add_reading(self, r: dict) -> None:
        row = {k: r.get(k) for k in TELEMETRY_FIELDS}
        row["ts"] = _parse_ts(r["ts"])
        with self.engine.begin() as c:
            c.execute(insert(telemetry), row)

    def add_event(self, e: dict) -> None:
        with self.engine.begin() as c:
            exists = c.execute(select(anomaly_events.c.id).where(anomaly_events.c.event_id == e["event_id"])).first()
            if exists:
                return
            c.execute(insert(anomaly_events), {
                "event_id": e["event_id"], "device_id": e["device_id"], "ts_detected": _parse_ts(e["ts_detected"]),
                "severity": e["severity"], "method": e["method"], "payload": e})

    # -------------------------------------------------------------- reads
    def latest_readings(self) -> list[dict]:
        newest = (select(telemetry.c.device_id, func.max(telemetry.c.id).label("mid"))
                  .group_by(telemetry.c.device_id).subquery())
        q = select(telemetry).join(newest, telemetry.c.id == newest.c.mid).order_by(telemetry.c.device_id)
        with self.engine.connect() as c:
            return [self._public(row) for row in c.execute(q).mappings()]

    def get_telemetry(self, device_id: str, minutes: int = 60) -> list[dict]:
        with self.engine.connect() as c:
            last = c.execute(select(func.max(telemetry.c.ts)).where(telemetry.c.device_id == device_id)).scalar()
            if last is None:
                return []
            q = (select(telemetry).where(telemetry.c.device_id == device_id,
                                         telemetry.c.ts > last - timedelta(minutes=minutes))
                 .order_by(telemetry.c.ts))
            return [self._public(row) for row in c.execute(q).mappings()]

    def recent_events(self, limit: int = 50) -> list[dict]:
        q = select(anomaly_events.c.payload).order_by(anomaly_events.c.ts_detected.desc()).limit(limit)
        with self.engine.connect() as c:
            return [row[0] for row in c.execute(q)]

    @staticmethod
    def _public(row) -> dict:
        d = {k: row[k] for k in PUBLIC_FIELDS}
        d["ts"] = _fmt_ts(d["ts"])
        return d


