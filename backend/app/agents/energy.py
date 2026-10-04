"""Energy agent: numbers are computed in code, the LLM only writes the explanation."""
import os

from app.agents.diagnosis import strip_label
from app.agents.schemas import Critique, Diagnosis, EnergyImpact
from app.llm import llm

DEFAULT_TARIFF_INR = 8.5   # INR per kWh (see knowledge/manuals/energy-tariff.md)
HOURS_FULL_DAY = 24        # assumes the fault lasts a full day
HOURS_AFTER_HOURS = 12     # assumed unoccupied hours per day


def _signal(event: dict, key: str):
    for s in event.get("signals", []):
        if s["key"] == key:
            return s
    return None


def _z(event: dict, key: str) -> float:
    s = _signal(event, key)
    return s["z"] if s else 0.0


# ---- tools ----
def get_tariff() -> float:
    try:
        return float(os.getenv("ENERGY_TARIFF_INR", DEFAULT_TARIFF_INR))
    except ValueError:
        return DEFAULT_TARIFF_INR


def check_after_hours(event: dict):
    """True/False from the last reading, or None if there is no window to check."""
    win = event.get("window", [])
    if not win:
        return None
    last = win[-1]
    return bool(last.get("status") == "ON" and last.get("occupancy") == 0
                and last.get("fan_speed_pct", 0) >= 60)


def compute_extra_kwh(event: dict, cause: str | None = None, hours: float | None = None) -> dict:
    """extra kWh = (actual kW - baseline kW) x hours. Negative means the unit draws LESS than baseline."""
    tariff = get_tariff()
    s = _signal(event, "power_kw")
    if s is None:
        return {"delta_kw": 0.0, "hours": 0, "extra_kwh_per_day": 0.0,
                "cost_per_day": 0.0, "tariff": tariff, "has_power": False}
    if hours is None:
        hours = HOURS_AFTER_HOURS if cause == "after_hours_waste" else HOURS_FULL_DAY
    delta = s["value"] - s["baseline"]
    kwh = round(delta * hours, 2)
    return {"delta_kw": round(delta, 2), "hours": hours, "extra_kwh_per_day": kwh,
            "cost_per_day": round(kwh * tariff, 2), "tariff": tariff, "has_power": True}


def critique_diagnosis(event: dict, diagnosis: Diagnosis) -> Critique:
    """Energy agent checks whether the power numbers fit the diagnosed cause."""
    event = strip_label(event)
    cause = diagnosis.cause
    power, air = _z(event, "power_kw"), _z(event, "airflow_cfm")
    issues, suggested = [], None

    if cause == "compressor_failure" and power >= 2:
        issues.append(f"Power is above baseline (z={power}); a failed compressor draws less, not more.")
        suggested = "filter_blockage" if air <= -2 else None
    elif cause == "refrigerant_leak" and power >= 3:
        issues.append(f"Power rose sharply (z={power}); a refrigerant leak lowers or keeps power.")
        suggested = "filter_blockage" if air <= -2 else None
    elif cause == "filter_blockage" and power <= -3:
        issues.append(f"Power fell sharply (z={power}); a blocked filter keeps or raises power, "
                      "so a cooling failure is more likely.")
        suggested = "compressor_failure"
    elif cause == "after_hours_waste" and check_after_hours(event) is False:
        issues.append("The last reading does not show the unit ON with zero occupancy at high fan speed.")
    elif cause == "sensor_stuck" and abs(power) >= 3:
        issues.append(f"Power also changed sharply (z={power}); a stuck sensor alone does not change power.")

    return Critique(agrees=not issues, issues=issues, suggested_cause=suggested)


# ---- rules fallback (used when no LLM works) ----
def energy_rules(context: dict) -> EnergyImpact:
    kwh = context.get("extra_kwh_per_day", 0.0)
    cost = context.get("cost_per_day", 0.0)
    delta, hours = context.get("delta_kw", 0.0), context.get("hours", 0)
    if not context.get("has_power", True):
        text = "No power reading is available, so the energy impact could not be calculated."
    elif kwh > 0:
        text = (f"The unit draws {delta} kW above its baseline. Over {hours} h that is about "
                f"{kwh} kWh extra per day, costing about INR {cost}.")
    elif kwh < 0:
        text = (f"Power is {abs(delta)} kW below baseline, so the unit uses less energy, but only "
                "because it is not doing its job. The real cost is lost cooling and equipment risk, "
                "not the electricity bill.")
    else:
        text = "No measurable energy impact against baseline."
    return EnergyImpact(extra_kwh_per_day=kwh, cost_per_day=cost, currency="INR", explanation=text)


llm.register_rules("energy", energy_rules)


# ---- agent ----
def analyse_energy(event: dict, diagnosis: Diagnosis):
    """Return (EnergyImpact, meta). Numbers always come from code, never from the LLM."""
    event = strip_label(event)
    calc = compute_extra_kwh(event, diagnosis.cause)
    ctx = {**calc, "cause": diagnosis.cause}
    prompt = f"""You are the Energy agent for an HVAC air handling unit.
Write a short explanation (2 sentences) of the energy impact for an operator.
Do NOT change or recompute any number. Use exactly these facts:
Diagnosed cause: {diagnosis.cause_text}
Power vs baseline: {calc['delta_kw']} kW, over {calc['hours']} h per day
Extra energy per day: {calc['extra_kwh_per_day']} kWh (negative means the unit draws less than baseline)
Cost per day: INR {calc['cost_per_day']} at INR {calc['tariff']} per kWh
Put these same numbers in extra_kwh_per_day and cost_per_day, currency INR."""
    obj, meta = llm.complete(prompt, EnergyImpact, "energy", context=ctx)
    obj = obj.model_copy(update={"extra_kwh_per_day": calc["extra_kwh_per_day"],
                                 "cost_per_day": calc["cost_per_day"], "currency": "INR"})
    return obj, meta