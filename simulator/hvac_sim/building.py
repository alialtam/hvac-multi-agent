"""A building = a set of AHUs that share the same clock and weather."""
from __future__ import annotations

import zlib
from dataclasses import fields
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import yaml

from .model import AHU, AHUParams

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config.yaml"


def load_config(path: str | Path | None = None) -> dict:
    with open(path or CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


class Building:
    def __init__(self, config: dict, seed: int | None = None):
        self.config = config
        self.seed = config.get("seed", 0) if seed is None else seed
        allowed = {f.name for f in fields(AHUParams)}
        defaults = {k: v for k, v in config.get("defaults", {}).items() if k in allowed}
        self.ahus: dict[str, AHU] = {}
        for i, dev in enumerate(config["devices"]):
            params = AHUParams(**{**defaults, **{k: v for k, v in dev.items() if k in allowed}})
            self.ahus[params.device_id] = AHU(params=params, seed=self.seed * 100 + i)

    def day_offset(self, now: datetime) -> float:
        """Each day is a little hotter or cooler (same for all zones, reproducible)."""
        key = zlib.crc32(f"{self.seed}-{now.date().isoformat()}".encode())
        return float(np.random.default_rng(key).uniform(-2.0, 2.0))

    def step(self, now: datetime, dt_min: float = 1.0) -> list[dict]:
        off = self.day_offset(now)
        return [ahu.step(now, dt_min, off) for ahu in self.ahus.values()]

    def warm_up(self, start: datetime, hours: int = 24) -> None:
        """Run the model before `start` so zone temperatures are realistic at t=0."""
        t = start - timedelta(hours=hours)
        while t < start:
            self.step(t)
            t += timedelta(minutes=1)

    def get(self, device_id: str) -> AHU:
        key = device_id.upper()
        if key not in self.ahus:
            raise KeyError(f"Unknown device '{device_id}'. Devices: {', '.join(self.ahus)}")
        return self.ahus[key]
