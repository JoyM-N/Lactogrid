"""Sensor checks. Safe mode overrides the strategy and any manual command.

A missing or unbelievable tank or store reading must not be a reason to stop
cooling. Ambient is included because the COP request depends on it. Solar is
not: if the pyranometer drops out we treat solar as zero and still trust a
good tank temperature. See docs/DECISIONS.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from shared.config import RootConfig
from shared.schemas import Reading, Site


@dataclass
class SafetyAssessment:
    active: bool
    reasons: list[str] = field(default_factory=list)

    @property
    def signature(self) -> tuple[str, ...]:
        return tuple(self.reasons)


def store_kwh_of(reading: Reading, site: Site) -> float | None:
    if reading.store_kwh is not None:
        return reading.store_kwh
    if reading.store_soc_pct is not None:
        return reading.store_soc_pct / 100.0 * site.cold_store_kwh_thermal
    return None


def battery_kwh_of(reading: Reading, site: Site) -> float | None:
    if reading.battery_kwh is not None:
        return reading.battery_kwh
    if reading.battery_soc_pct is not None and site.battery_kwh > 0:
        return reading.battery_soc_pct / 100.0 * site.battery_kwh
    return None


def _temperature_problem(name: str, value: float | None, low: float, high: float) -> str | None:
    if value is None:
        return f"{name} is missing"
    if value < low or value > high:
        return f"{name} is {value:.1f}°C, outside {low:.0f} to {high:.0f}°C"
    return None


def assess_sensors(reading: Reading, config: RootConfig, site: Site) -> SafetyAssessment:
    """Safe mode when the tank, the store, or ambient cannot be trusted."""
    low = config.controller.sensor_min_plausible_c
    high = config.controller.sensor_max_plausible_c
    reasons: list[str] = []
    for name, value in (
        ("Tank temperature", reading.tank_temp_c),
        ("Ambient temperature", reading.ambient_c),
    ):
        problem = _temperature_problem(name, value, low, high)
        if problem:
            reasons.append(problem)

    store = store_kwh_of(reading, site)
    if store is None and reading.store_soc_pct is None:
        reasons.append("Cold store level is missing")
    elif reading.store_soc_pct is not None and not 0 <= reading.store_soc_pct <= 100:
        reasons.append(f"Cold store state of charge is {reading.store_soc_pct:.0f}%, outside 0 to 100%")
    elif store is not None and (store < 0 or store > site.cold_store_kwh_thermal * 1.05):
        reasons.append(f"Cold store energy is {store:.1f} kWh, outside the store rating")

    for flag in reading.fault_flags:
        if flag.startswith("stuck_sensor:"):
            sensor = flag.split(":", 1)[1]
            if sensor in {"tank_temp_c", "ambient_c", "store_soc_pct", "store_kwh"}:
                reasons.append(f"Sensor looks stuck ({sensor})")
        elif flag.startswith("sensor_dropout:"):
            sensor = flag.split(":", 1)[1]
            if sensor in {"tank_temp_c", "ambient_c", "store_soc_pct", "store_kwh"}:
                reasons.append(f"Sensor dropped out ({sensor})")

    # De-duplicate while keeping order. Dropout and a None value say the same thing.
    unique: list[str] = []
    for reason in reasons:
        if reason not in unique:
            unique.append(reason)
    return SafetyAssessment(active=bool(unique), reasons=unique)


@dataclass
class TempSample:
    timestamp: datetime
    tank_temp_c: float
    commanded_tank_kwh: float
    milk_volume_l: float = 0.0
    ambient_c: float | None = None


def tank_sensor_is_stuck(
    history: list[TempSample],
    config: RootConfig,
    ambient_c: float | None,
    milk_litres: float | None,
) -> bool:
    """True when the tank trace is flat but the physics says it should have moved.

    The expected movement is heat leak minus the cooling that was commanded.
    A healthy hold cancels the leak, so the expected movement is small and this
    returns False. Milk arriving during the window is part of that expectation:
    warm milk can hold the trace almost flat while the store is doing real work.
    A flat trace with a large expected movement is stuck. The controller latches
    that result until the published temperature moves, so the cooling command
    issued in safe mode does not clear the fault by itself.
    """
    if ambient_c is None or milk_litres is None or len(history) < 2:
        return False
    window = timedelta(minutes=config.controller.stuck_window_minutes)
    newest = history[-1].timestamp
    recent = [sample for sample in history if newest - sample.timestamp <= window]
    if len(recent) < 2:
        return False
    span = newest - recent[0].timestamp
    if span < window * 0.95:
        return False
    resolution = config.controller.stuck_resolution_c
    rounded = [round(sample.tank_temp_c / resolution) * resolution for sample in recent]
    if max(rounded) - min(rounded) > resolution * 0.51:
        return False

    from shared.physics import effective_mass_l, heat_leak_kw

    specific = config.physics.milk_specific_heat_wh_per_l_c
    shell = config.physics.tank_shell_equivalent_l
    arrival = config.physics.milk_arrival_temp_c
    coeff = config.physics.heat_leak_kw_per_c
    if specific <= 0:
        return False
    # Each command has had time to act by the next sample. Milk mixed in that
    # same step shows up as a volume increase on the later sample.
    expected_change = 0.0
    for prev, nxt in zip(recent, recent[1:]):
        hours = (nxt.timestamp - prev.timestamp).total_seconds() / 3600.0
        if hours <= 0:
            continue
        mass = effective_mass_l(prev.milk_volume_l, shell)
        added = max(0.0, nxt.milk_volume_l - prev.milk_volume_l)
        temp = prev.tank_temp_c
        if added > 0 and mass > 0:
            mixed = (temp * mass + arrival * added) / (mass + added)
            mass_after = mass + added
        else:
            mixed = temp
            mass_after = mass
        if mass_after <= 0:
            continue
        step_ambient = prev.ambient_c if prev.ambient_c is not None else ambient_c
        leak_kwh = heat_leak_kw(step_ambient, temp, coeff) * hours
        next_temp = mixed + (leak_kwh - prev.commanded_tank_kwh) * 1000.0 / (specific * mass_after)
        expected_change += next_temp - temp
    actual_change = recent[-1].tank_temp_c - recent[0].tank_temp_c
    return abs(expected_change - actual_change) >= config.controller.stuck_expected_move_c
