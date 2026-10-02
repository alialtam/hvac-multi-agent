"""Physics-based model of one air handling unit (AHU) serving one zone.

The model is a simple heat balance, solved once per simulated minute:

    C * dT_zone/dt = Q_gain - Q_cool

    Q_gain = UA * (T_out - T_zone) + people + equipment + solar      [kW]
    Q_cool = 0.00057 * airflow_cfm * (T_zone - T_supply)              [kW]

0.00057 comes from the standard sensible-heat formula Q[BTU/h] = 1.08 * CFM * dT[F],
converted to kW and degrees C. A PI controller sets the compressor load so the
measured zone temperature tracks the setpoint. Faults change the physical
parameters (filter, compressor, refrigerant) or the sensor, never the labels.

Everything is deterministic for a given seed, so datasets are reproducible.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

FAULTS = (
    "filter_blockage",
    "compressor_failure",
    "refrigerant_leak",
    "sensor_stuck",
    "after_hours_waste",
)

# Default ramp (minutes) from healthy to fully developed fault.
DEFAULT_RAMP_MIN = {
    "filter_blockage": 20,
    "compressor_failure": 0,
    "refrigerant_leak": 180,
    "sensor_stuck": 0,
    "after_hours_waste": 0,
}

SENSIBLE_KW_PER_CFM_C = 0.00057


@dataclass
class AHUParams:
    device_id: str
    zone: str
    setpoint_c: float = 23.0
    max_cfm: float = 3000.0          # airflow at 100% fan speed with a clean filter
    fan_rated_kw: float = 1.8        # fan power at 100% speed
    comp_rated_kw: float = 7.5       # compressor electrical power at full load
    coil_dt_max_c: float = 14.0      # max air temperature drop across the coil
    outdoor_air_fraction: float = 0.10  # share of fresh outdoor air in the supply
    ua_kw_per_c: float = 0.35        # envelope heat transfer
    capacitance_kwh_per_c: float = 3.0  # air + furniture + slab thermal mass
    max_occupancy: int = 24
    equipment_kw_occupied: float = 1.2
    equipment_kw_unoccupied: float = 0.3
    solar_peak_kw: float = 1.5
    fan_speed_on_pct: float = 75.0
    schedule_on: str = "07:00"
    schedule_off: str = "18:30"
    occupied_start: str = "08:00"
    occupied_end: str = "18:00"


@dataclass
class Fault:
    name: str
    start: datetime
    ramp_min: float
    stuck_value: float | None = None

    def severity(self, now: datetime) -> float:
        """0 at injection, rising linearly to 1 after ramp_min."""
        if self.ramp_min <= 0:
            return 1.0
        elapsed = (now - self.start).total_seconds() / 60.0
        return float(min(1.0, max(0.0, elapsed / self.ramp_min)))


def _hhmm_to_min(s: str) -> int:
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def outdoor_temp_c(now: datetime, day_offset_c: float = 0.0) -> float:
    """Hot-climate daily cycle: about 27 C at 05:00, about 38 C at 15:00."""
    minute = now.hour * 60 + now.minute
    # cosine with its peak at 15:00
    phase = 2 * math.pi * (minute - 15 * 60) / 1440.0
    return 32.5 + 5.5 * math.cos(phase) + day_offset_c


def solar_factor(now: datetime) -> float:
    minute = now.hour * 60 + now.minute
    if minute < 6 * 60 or minute > 18 * 60:
        return 0.0
    return math.sin(math.pi * (minute - 6 * 60) / (12 * 60))


@dataclass
class AHU:
    params: AHUParams
    seed: int = 0
    zone_temp_c: float = 27.0
    humidity_pct: float = 55.0
    co2_ppm: float = 450.0
    integral: float = 0.0
    fault: Fault | None = None
    forced_on: bool = False
    rng: np.random.Generator = field(init=False)
    _occ_noise: float = field(init=False, default=0.0)
    _last_measured_temp: float = field(init=False, default=27.0)

    def __post_init__(self) -> None:
        self.rng = np.random.default_rng(self.seed)

    # ------------------------------------------------------------------ faults
    def inject(self, name: str, now: datetime, ramp_min: float | None = None) -> None:
        if name not in FAULTS:
            raise ValueError(f"Unknown fault '{name}'. Choose from: {', '.join(FAULTS)}")
        ramp = DEFAULT_RAMP_MIN[name] if ramp_min is None else ramp_min
        stuck = round(self._last_measured_temp, 2) if name == "sensor_stuck" else None
        self.fault = Fault(name=name, start=now, ramp_min=ramp, stuck_value=stuck)

    def reset(self) -> None:
        self.fault = None

    def _fault_level(self, name: str, now: datetime) -> float:
        if self.fault is None or self.fault.name != name:
            return 0.0
        return self.fault.severity(now)

    # --------------------------------------------------------------- schedule
    def occupancy(self, now: datetime) -> int:
        p = self.params
        m = now.hour * 60 + now.minute
        start, end = _hhmm_to_min(p.occupied_start), _hhmm_to_min(p.occupied_end)
        if m < start or m >= end:
            return 0
        if m < start + 60:            # people arriving
            frac = (m - start) / 60.0
        elif m >= end - 60:           # people leaving
            frac = (end - m) / 60.0
        elif 13 * 60 <= m < 14 * 60:  # lunch dip
            frac = 0.6
        else:
            frac = 0.95
        # slow random walk so occupancy is not perfectly regular
        self._occ_noise = 0.9 * self._occ_noise + self.rng.normal(0, 0.03)
        frac = min(1.0, max(0.0, frac + self._occ_noise))
        return int(round(frac * p.max_occupancy))

    def scheduled_on(self, now: datetime) -> bool:
        m = now.hour * 60 + now.minute
        return _hhmm_to_min(self.params.schedule_on) <= m < _hhmm_to_min(self.params.schedule_off)

    # ------------------------------------------------------------------- step
    def step(self, now: datetime, dt_min: float = 1.0, day_offset_c: float = 0.0) -> dict:
        p = self.params
        dt_h = dt_min / 60.0
        t_out = outdoor_temp_c(now, day_offset_c) + self.rng.normal(0, 0.2)
        occ = self.occupancy(now)

        after_hours = self.fault is not None and self.fault.name == "after_hours_waste"
        is_on = self.scheduled_on(now) or after_hours or self.forced_on

        filter_factor = 1.0 - 0.40 * self._fault_level("filter_blockage", now)
        capacity_factor = 1.0 - 0.50 * self._fault_level("refrigerant_leak", now)
        compressor_ok = self._fault_level("compressor_failure", now) == 0.0

        # --- what the controller sees (the sensor may be stuck)
        measured_temp = self._measure_zone_temp(now)

        if is_on:
            fan_speed = 100.0 if after_hours else p.fan_speed_on_pct
            speed = fan_speed / 100.0
            airflow = p.max_cfm * speed * filter_factor
            airflow_ratio = airflow / (p.max_cfm * speed)

            # PI controller on the measured temperature
            error = measured_temp - p.setpoint_c
            self.integral = float(np.clip(self.integral + 0.02 * error * dt_min, -0.6, 0.6))
            load = float(np.clip(0.45 + 0.35 * error + self.integral, 0.0, 1.0))

            # return air mixed with a little hot outdoor air before the coil
            mixed = self.zone_temp_c + p.outdoor_air_fraction * (t_out - self.zone_temp_c)
            if compressor_ok:
                # slower air through the coil gets a bit colder
                coil_dt = load * capacity_factor * p.coil_dt_max_c * (1 + 0.35 * (1 - airflow_ratio))
                comp_kw = p.comp_rated_kw * (load ** 0.9) + (0.3 if load > 0.02 else 0.0)
            else:
                coil_dt = -0.3                 # fan heat only, no cooling
                comp_kw = 0.05                 # tripped
            supply = mixed - coil_dt
            q_cool = SENSIBLE_KW_PER_CFM_C * airflow * (self.zone_temp_c - supply)
            # a clogged filter moves less air, so a fixed-speed fan draws less power
            fan_kw = p.fan_rated_kw * speed ** 3 * (0.55 + 0.45 * filter_factor)
            power = fan_kw + comp_kw
            dehum = load * capacity_factor * airflow_ratio if compressor_ok else 0.0
            ventilation = airflow_ratio
            status = "ON"
        else:
            fan_speed, airflow, load, q_cool = 0.0, 0.0, 0.0, 0.0
            supply = self.zone_temp_c + 0.5   # still air in the duct
            power = 0.05                      # controls standby
            dehum, ventilation = 0.0, 0.15    # infiltration only
            status = "OFF"
            self.integral *= 0.95

        # --- zone heat balance
        equipment = p.equipment_kw_occupied if occ > 0 else p.equipment_kw_unoccupied
        q_gain = (
            p.ua_kw_per_c * (t_out - self.zone_temp_c)
            + 0.08 * occ
            + equipment
            + p.solar_peak_kw * solar_factor(now)
        )
        self.zone_temp_c += dt_h * (q_gain - q_cool) / p.capacitance_kwh_per_c

        # --- humidity and CO2 relax towards a target (first-order lag)
        rh_target = 58.0 + 0.3 * occ - 16.0 * dehum
        self.humidity_pct += (rh_target - self.humidity_pct) * min(1.0, dt_min / 20.0)
        co2_target = 420.0 + 32.0 * occ / max(ventilation, 0.15)
        self.co2_ppm += (co2_target - self.co2_ppm) * min(1.0, dt_min / 15.0)

        reading_temp = self._measure_zone_temp(now)
        self._last_measured_temp = reading_temp

        r = self.rng.normal
        return {
            "device_id": p.device_id,
            "zone": p.zone,
            "ts": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "zone_temp_c": round(reading_temp, 2),
            "supply_temp_c": round(supply + r(0, 0.15), 2),
            "setpoint_c": p.setpoint_c,
            "humidity_pct": round(float(np.clip(self.humidity_pct + r(0, 0.8), 0, 100)), 1),
            "airflow_cfm": round(max(0.0, airflow + (r(0, 25) if airflow > 0 else 0.0)), 0),
            "fan_speed_pct": round(fan_speed, 1),
            "power_kw": round(max(0.0, power + r(0, 0.06)), 2),
            "occupancy": occ,
            "co2_ppm": round(max(380.0, self.co2_ppm + r(0, 8)), 0),
            "status": status,
            "outdoor_temp_c": round(t_out, 1),
            "fault_label": self.fault.name if self.fault else "none",
        }

    def _measure_zone_temp(self, now: datetime) -> float:
        if self.fault is not None and self.fault.name == "sensor_stuck":
            return float(self.fault.stuck_value)
        return float(self.zone_temp_c + self.rng.normal(0, 0.08))
