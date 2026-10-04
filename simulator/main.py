"""Live HVAC simulator: publishes 6 AHUs to MQTT and accepts commands while running.

Usage:
    python main.py                       # publish to MQTT (config.yaml)
    python main.py --console             # no broker needed, print readings
    python main.py --scenario scenarios/demo_filter.yaml
    python main.py --start 19:00 --speed 2

Commands (type into the running terminal, then Enter):
    inject AHU-4 filter_blockage [ramp_min]   start a fault
    reset AHU-4 | reset all                   clear faults
    offline AHU-4 | online AHU-4              stop / resume publishing for a device
    jump 19:00                                move the simulated clock (same day)
    speed 5                                   real seconds per reading
    status                                    show clock, faults and offline devices
    corrupt AHU-3 [spike|missing|text|negative]  send ONE broken reading (tests input validation)
    run scenarios/demo_filter.yaml            run a scripted scenario
    help | quit
"""
from __future__ import annotations

import argparse
import shlex
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import json
import random

import yaml

from hvac_sim.building import Building, load_config
from hvac_sim.model import FAULTS
from hvac_sim.control import RemoteControl
from hvac_sim.publisher import ConsolePublisher, MqttPublisher, ThingsBoardPublisher

HERE = Path(__file__).resolve().parent
CORRUPT_KINDS = ("spike", "missing", "text", "negative")


class LiveSimulator:
    def __init__(self, config: dict, publishers: list, start: datetime, seconds_per_step: float,
                 minutes_per_step: float):
        self.building = Building(config)
        self.publishers = publishers
        self.now = start
        self.seconds_per_step = seconds_per_step
        self.minutes_per_step = minutes_per_step
        self.offline: set[str] = set()
        self.scheduled: list[tuple[datetime, str]] = []   # (sim time, command)
        self.lock = threading.Lock()
        self.running = True
        self.last: dict[str, dict] = {}                  # last reading per device (for 'corrupt')
        self.on_step = None                              # called after each published step
        print(f"Warming up the building model (24 simulated hours before {start:%H:%M})...")
        self.building.warm_up(start)

    # ------------------------------------------------------------- commands
    def handle(self, line: str) -> str:
        parts = shlex.split(line.strip())
        if not parts:
            return ""
        cmd, args = parts[0].lower(), parts[1:]
        with self.lock:
            try:
                if cmd == "inject":
                    if len(args) < 2:
                        return f"usage: inject <device> <fault> [ramp_min]. Faults: {', '.join(FAULTS)}"
                    ramp = float(args[2]) if len(args) > 2 else None
                    self.building.get(args[0]).inject(args[1], self.now, ramp)
                    return f"[{self.now:%H:%M}] injected {args[1]} on {args[0].upper()}"
                if cmd == "reset":
                    targets = self.building.ahus.values() if (not args or args[0] == "all") else [self.building.get(args[0])]
                    for ahu in targets:
                        ahu.reset()
                    return f"[{self.now:%H:%M}] reset {'all devices' if not args or args[0] == 'all' else args[0].upper()}"
                if cmd in ("offline", "online"):
                    dev = self.building.get(args[0]).params.device_id
                    (self.offline.add if cmd == "offline" else self.offline.discard)(dev)
                    return f"[{self.now:%H:%M}] {dev} is now {cmd}"
                if cmd == "jump":
                    h, m = map(int, args[0].split(":"))
                    target = self.now.replace(hour=h, minute=m)
                    if target <= self.now:
                        target += timedelta(days=1)
                    print(f"Fast-forwarding the model to {target:%Y-%m-%d %H:%M} (no publishing)...")
                    while self.now < target:
                        self.building.step(self.now)
                        self.now += timedelta(minutes=1)
                    return f"clock is now {self.now:%Y-%m-%d %H:%M}"
                if cmd == "speed":
                    self.seconds_per_step = max(0.05, float(args[0]))
                    return f"one reading every {self.seconds_per_step} s"
                if cmd == "status":
                    faults = {d: a.fault.name for d, a in self.building.ahus.items() if a.fault}
                    return (f"clock {self.now:%Y-%m-%d %H:%M} | faults {faults or 'none'} | "
                            f"offline {sorted(self.offline) or 'none'} | {self.seconds_per_step}s/reading")
                if cmd == "corrupt":
                    return self._corrupt(args[0], args[1] if len(args) > 1 else "spike")
                if cmd == "run":
                    return self._load_scenario(args[0])
                if cmd == "help":
                    return __doc__.split("Commands")[1]
                if cmd in ("quit", "exit"):
                    self.running = False
                    return "stopping"
                return f"unknown command '{cmd}' (type help)"
            except (KeyError, ValueError, IndexError, FileNotFoundError) as e:
                return f"error: {e}"

    def _corrupt(self, device: str, kind: str) -> str:
        dev = self.building.get(device).params.device_id
        if kind not in CORRUPT_KINDS:
            return f"error: kind must be one of {', '.join(CORRUPT_KINDS)}"
        r = dict(self.last.get(dev) or {"device_id": dev, "ts": f"{self.now:%Y-%m-%dT%H:%M:%SZ}"})
        if kind == "spike":
            r["zone_temp_c"] = 999.0                      # a sensor glitch no real room can reach
        elif kind == "missing":
            r.pop("airflow_cfm", None)
        elif kind == "negative":
            r["power_kw"] = -round(random.uniform(1, 5), 2)
        payload = "#@! not json" if kind == "text" else json.dumps(r)
        for p in self.publishers:
            p.publish_raw(dev, payload)
        return f"[{self.now:%H:%M}] sent one broken reading ({kind}) for {dev}"

    def scenario_names(self) -> list[str]:
        return sorted(p.stem for p in (HERE / "scenarios").glob("*.yaml"))

    def status_dict(self) -> dict:
        return {
            "running": self.running,
            "clock": f"{self.now:%Y-%m-%dT%H:%M:%SZ}",
            "seconds_per_reading": self.seconds_per_step,
            "devices": sorted(self.building.ahus),
            "faults": [{"device_id": d, "fault": a.fault.name, "since": f"{a.fault.start:%Y-%m-%dT%H:%M:%SZ}"}
                       for d, a in sorted(self.building.ahus.items()) if a.fault],
            "offline": sorted(self.offline),
            "fault_types": list(FAULTS),
            "corrupt_kinds": list(CORRUPT_KINDS),
            "scenarios": self.scenario_names(),
            "pending_scenario_steps": len(self.scheduled),
        }

    def _load_scenario(self, path: str) -> str:
        p = Path(path)
        if not p.is_absolute() and not p.exists():
            p = HERE / path
        if not p.exists() and not p.suffix:
            p = HERE / "scenarios" / f"{path}.yaml"     # 'run demo_filter' works too
        with open(p, encoding="utf-8") as f:
            scenario = yaml.safe_load(f)
        for step in scenario["steps"]:
            self.scheduled.append((self.now + timedelta(minutes=step["after_min"]), step["cmd"]))
        self.scheduled.sort()
        return f"scenario '{scenario.get('name', p.name)}' loaded: {len(scenario['steps'])} steps"

    # ----------------------------------------------------------------- loop
    def run(self) -> None:
        while self.running:
            t0 = time.monotonic()
            due = []
            with self.lock:
                while self.scheduled and self.scheduled[0][0] <= self.now:
                    due.append(self.scheduled.pop(0)[1])
            for c in due:
                print("scenario>", c, "->", self.handle(c))
            with self.lock:
                readings = self.building.step(self.now, self.minutes_per_step)
                self.now += timedelta(minutes=self.minutes_per_step)
            for r in readings:
                if r["device_id"] in self.offline:
                    continue
                self.last[r["device_id"]] = r
                for p in self.publishers:
                    p.publish(r)
            if self.on_step:
                try:
                    self.on_step()
                except Exception as e:  # never stop the building because of the dashboard link
                    print("status publish failed:", e)
            time.sleep(max(0.0, self.seconds_per_step - (time.monotonic() - t0)))
        for p in self.publishers:
            p.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Live HVAC simulator")
    ap.add_argument("--config", default=None)
    ap.add_argument("--console", action="store_true", help="print readings instead of MQTT")
    ap.add_argument("--watch", default=None, help="with --console, only print this device (e.g. AHU-4)")
    ap.add_argument("--start", default=None, help="simulated start time HH:MM (default from config)")
    ap.add_argument("--speed", type=float, default=None, help="real seconds per reading")
    ap.add_argument("--scenario", default=None, help="scenario YAML to run on start")
    ap.add_argument("--host", default=None, help="MQTT broker host (overrides config)")
    args = ap.parse_args()

    cfg = load_config(args.config)
    live = cfg.get("live", {})
    hh, mm = map(int, (args.start or live.get("start_time", "09:00")).split(":"))
    start = datetime.now(timezone.utc).replace(hour=hh, minute=mm, second=0, microsecond=0, tzinfo=None)

    publishers = []
    if args.console:
        publishers.append(ConsolePublisher({args.watch.upper()} if args.watch else None))
    else:
        m = cfg["mqtt"]
        host = args.host or m["host"]
        try:
            publishers.append(MqttPublisher(host, m["port"], m["topic"], m.get("qos", 0)))
        except OSError as e:
            sys.exit(f"Cannot reach the MQTT broker at {host}:{m['port']} ({e}). "
                     f"Is Mosquitto running? Or use --console.")
        print(f"Publishing to mqtt://{host}:{m['port']}  topic {m['topic']}")
        tb = cfg.get("thingsboard", {})
        if tb.get("enabled"):
            publishers.append(ThingsBoardPublisher(tb["host"], tb["port"], tb["tokens"]))
            print("Also publishing to ThingsBoard")

    sim = LiveSimulator(cfg, publishers, start,
                        args.speed or live.get("seconds_per_step", 5),
                        live.get("minutes_per_step", 1))
    remote = None
    if not args.console:
        try:
            remote = RemoteControl(sim, host, cfg["mqtt"]["port"])
            sim.on_step = remote.publish_status
            print("Dashboard control enabled (MQTT topic hvac/sim/control)")
        except (OSError, RuntimeError) as e:
            print(f"Dashboard control not available ({e}); terminal commands still work")
    if args.scenario:
        print(sim.handle(f"run {args.scenario}"))

    threading.Thread(target=sim.run, daemon=True).start()
    print("Simulator running. Type 'help' for commands.")
    try:
        while sim.running:
            line = sys.stdin.readline()
            if not line:            # stdin closed (e.g. started in background): keep running
                while sim.running:
                    time.sleep(1)
                break
            out = sim.handle(line)
            if out:
                print(out)
    except KeyboardInterrupt:
        sim.running = False
    time.sleep(0.2)
    if remote:
        remote.close()


if __name__ == "__main__":
    main()
