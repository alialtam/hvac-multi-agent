from app.agents.schemas import Diagnosis

CAUSE_TEXT = {
    "filter_blockage": "Air filter blockage restricting airflow",
    "compressor_failure": "Compressor failure, cooling has stopped",
    "refrigerant_leak": "Refrigerant leak, cooling capacity fading",
    "sensor_stuck": "Room temperature sensor stuck (flatlined)",
    "after_hours_waste": "Unit running at full load in an empty building",
    "unknown": "Cause unclear from the available signals",
}
CAUSE_QUERY = {
    "filter_blockage": "filter blockage airflow drop",
    "compressor_failure": "compressor failure cooling stopped",
    "refrigerant_leak": "refrigerant leak cooling capacity coil",
    "sensor_stuck": "sensor stuck flatline temperature",
    "after_hours_waste": "after hours unoccupied schedule waste",
    "unknown": "commissioning baseline",
}
KEYS = ["airflow_cfm", "power_kw", "supply_temp_c", "coil_dt_c", "zone_temp_c"]


def _z(event: dict, key: str) -> float:
    for s in event.get("signals", []):
        if s["key"] == key:
            return s["z"]
    return 0.0


def _std(vals: list[float]) -> float:
    m = sum(vals) / len(vals)
    return (sum((v - m) ** 2 for v in vals) / len(vals)) ** 0.5


def _evidence(event: dict, keys: list[str]) -> list[str]:
    out = []
    for s in event.get("signals", []):
        if s["key"] in keys:
            out.append(f"{s['key']} is {s['value']} against a baseline of {s['baseline']} (z={s['z']})")
    return out


def diagnose_rules(event: dict) -> Diagnosis:
    hits = set(event.get("rule_hits", []))
    win = event.get("window", [])[-10:]
    zone = [r["zone_temp_c"] for r in win if "zone_temp_c" in r]
    last = win[-1] if win else {}
    air, power, supply, coil = (_z(event, k) for k in ("airflow_cfm", "power_kw", "supply_temp_c", "coil_dt_c"))

    if "sensor_flatline" in hits or (len(zone) >= 8 and _std(zone) < 0.005):
        cause, conf, ev = "sensor_stuck", 0.85, [f"zone_temp_c flat at {zone[-1] if zone else '?'} for the last readings"]
    elif "running_unoccupied" in hits or (last.get("occupancy") == 0 and last.get("fan_speed_pct", 0) >= 60 and last.get("status") == "ON"):
        cause, conf, ev = "after_hours_waste", 0.85, ["Unit is ON at high fan speed while occupancy is 0"]
    elif ("low_airflow" in hits or air <= -3) and power > -3:
        cause, conf = "filter_blockage", min(0.95, 0.7 + abs(air) / 100)
        ev = _evidence(event, ["airflow_cfm", "power_kw", "supply_temp_c"])
    elif power <= -3 and (coil <= -3 or supply >= 3):
        cause, conf = "compressor_failure", min(0.95, 0.7 + abs(power) / 100)
        ev = _evidence(event, ["power_kw", "coil_dt_c", "supply_temp_c"])
    elif coil <= -2 and supply >= 2 and air > -3:
        cause, conf = "refrigerant_leak", 0.7
        ev = _evidence(event, ["coil_dt_c", "supply_temp_c", "power_kw"])
    else:
        cause, conf, ev = "unknown", 0.3, _evidence(event, KEYS)

    from .search import search_knowledge
    sources = [h["source"] for h in search_knowledge(CAUSE_QUERY[cause], 2)]
    return Diagnosis(cause=cause, cause_text=CAUSE_TEXT[cause], confidence=round(conf, 2),
                     evidence=ev or ["Rule matched on the event signals"], sources=sources)