"""Plant physics shared by the simulator and by controller sanity checks.

The formulas named in the brief are implemented directly. Anything not named
there (battery power limits, freeze cut-out) is read from config.

Integration is explicit Euler over one step. Scenario runs keep that step at
60 seconds or less, so a step does not both receive milk and get collected.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta

from shared.config import BatteryConfig, ColdStoreConfig, CompressorConfig, PhysicsConfig, TankConfig
from shared.schemas import ActuatorCommand


def local_hour(moment: datetime) -> float:
    """Hour of the local clock as a float. ``moment`` must already be local."""
    return moment.hour + moment.minute / 60 + moment.second / 3600 + moment.microsecond / 3_600_000_000


def solar_kw(moment_local: datetime, kwp: float, cloud_factor: float, physics: PhysicsConfig) -> float:
    """Clear-sky curve times a cloud factor.

    ``kwp * peak_factor * sin(pi * (hour - sunrise) / day_length) ** exponent * cloud``.
    Outside [sunrise, sunset) the result is 0.
    """
    hour = local_hour(moment_local)
    sunrise = physics.solar_sunrise_hour
    sunset = physics.solar_sunset_hour
    if hour < sunrise or hour >= sunset or kwp <= 0 or cloud_factor <= 0:
        return 0.0
    day_length = sunset - sunrise
    sine = math.sin(math.pi * (hour - sunrise) / day_length)
    if sine <= 0:
        return 0.0
    return kwp * physics.solar_peak_factor * (sine ** physics.solar_sin_exponent) * cloud_factor


def ambient_c(moment_local: datetime, physics: PhysicsConfig) -> float:
    """``base + amplitude * sin(pi * (hour - phase) / period)``."""
    hour = local_hour(moment_local)
    return physics.ambient_base_c + physics.ambient_amplitude_c * math.sin(
        math.pi * (hour - physics.ambient_phase_hour) / physics.ambient_period_hours
    )


def cloud_multiplier(moment: datetime, seed: int, cloudiness: float, physics: PhysicsConfig) -> float:
    """Deterministic cloud multiplier in (0, 1].

    ``cloudiness`` 0 is a clear day (multiplier near 1). ``cloudiness`` 1 is
    overcast (multiplier near 0.1). A seeded sum of slow sine waves makes the
    cloud cover vary through the day without changing between runs.
    """
    clamped = min(1.0, max(0.0, cloudiness))
    rng = random.Random(seed)
    phases = [rng.random() * 2 * math.pi for _ in physics.cloud_noise_period_hours]
    t_hours = moment.timestamp() / 3600.0
    noise = 0.0
    for amplitude, period, phase in zip(
        physics.cloud_noise_amplitudes, physics.cloud_noise_period_hours, phases, strict=True
    ):
        noise += amplitude * math.sin(2 * math.pi * t_hours / period + phase)
    base = 1.0 - 0.9 * clamped
    return min(1.0, max(0.02, base + noise))


def coefficient_of_performance(ambient: float, compressor: CompressorConfig) -> float:
    """``COP = max(floor, intercept - slope * (ambient - reference))``."""
    raw = compressor.cop_intercept - compressor.cop_slope_per_c * (
        ambient - compressor.cop_reference_ambient_c
    )
    return max(compressor.cop_floor, raw)


def cold_produced_kwh(electrical_kw: float, cop: float, step_hours: float) -> float:
    """Thermal kWh from the compressor over one step."""
    return max(0.0, electrical_kw) * max(0.0, cop) * max(0.0, step_hours)


def effective_mass_l(milk_litres: float, shell_equivalent_l: float) -> float:
    """Tank thermal mass in litre-equivalents. Shell stays even when the tank is empty."""
    return max(0.0, milk_litres) + max(0.0, shell_equivalent_l)


def mix_delivery(
    tank_temp_c: float,
    milk_litres: float,
    added_litres: float,
    shell_equivalent_l: float,
    arrival_temp_c: float,
    tank_capacity_l: float,
) -> tuple[float, float, float]:
    """Mix a delivery into the tank.

    Mass in the mixing formula is the effective mass (milk + shell), so a cold
    empty tank does not jump to the arrival temperature. Returns
    ``(new_temp, new_milk_litres, spilled_litres)``.
    """
    if added_litres < 0:
        raise ValueError("added litres must be >= 0")
    room = max(0.0, tank_capacity_l - milk_litres)
    accepted = min(added_litres, room)
    spilled = added_litres - accepted
    if accepted <= 0:
        return tank_temp_c, milk_litres, spilled
    mass = effective_mass_l(milk_litres, shell_equivalent_l)
    new_temp = (tank_temp_c * mass + arrival_temp_c * accepted) / (mass + accepted)
    return new_temp, milk_litres + accepted, spilled


def heat_leak_kw(ambient: float, tank_temp_c: float, coeff_kw_per_c: float) -> float:
    """Heat flow into the tank, kW. Negative when the tank is warmer than the air."""
    return coeff_kw_per_c * (ambient - tank_temp_c)


def temperature_after_heat(
    temp_c: float,
    effective_litres: float,
    heat_kwh: float,
    specific_heat_wh_per_l_c: float,
) -> float:
    """Apply heat to the tank. Positive heat warms it."""
    if effective_litres <= 0 or specific_heat_wh_per_l_c <= 0:
        return temp_c
    delta = (heat_kwh * 1000.0) / (specific_heat_wh_per_l_c * effective_litres)
    return temp_c + delta


def overlap_hours(
    start_local: datetime,
    end_local: datetime,
    window_start_hour: float,
    window_end_hour: float,
) -> float:
    """Hours of ``[start_local, end_local)`` that fall inside a same-day clock window."""
    if end_local <= start_local:
        return 0.0
    total = 0.0
    cursor = start_local
    while cursor < end_local:
        day_start = cursor.replace(hour=0, minute=0, second=0, microsecond=0)
        window_start = day_start + timedelta(hours=window_start_hour)
        window_end = day_start + timedelta(hours=window_end_hour)
        segment_end = min(end_local, day_start + timedelta(days=1))
        begin = max(cursor, window_start)
        finish = min(segment_end, window_end)
        if finish > begin:
            total += (finish - begin).total_seconds() / 3600.0
        cursor = segment_end
    return total


def milk_arriving_litres(
    start_local: datetime,
    step_hours: float,
    daily_litres: float,
    physics: PhysicsConfig,
) -> float:
    """Litres delivered during this step, spread evenly across each window."""
    if step_hours <= 0 or daily_litres <= 0:
        return 0.0
    end_local = start_local + timedelta(hours=step_hours)
    windows = (
        (physics.morning_start_hour, physics.morning_end_hour, physics.morning_milk_fraction),
        (physics.evening_start_hour, physics.evening_end_hour, physics.evening_milk_fraction),
    )
    total = 0.0
    for begin, finish, fraction in windows:
        span = finish - begin
        if span <= 0 or fraction <= 0:
            continue
        hours = overlap_hours(start_local, end_local, begin, finish)
        total += daily_litres * fraction * (hours / span)
    return total


def crosses_collection(previous_local: datetime, now_local: datetime, hour: int, minute: int) -> bool:
    """True when the clock passed the collection time between the two local timestamps."""
    target = hour * 60 + minute

    def minutes(moment: datetime) -> int:
        return moment.hour * 60 + moment.minute

    if previous_local.date() == now_local.date():
        return minutes(previous_local) < target <= minutes(now_local)
    # The step crossed midnight. Collection is mid-morning, so it is not inside a 60 s midnight step.
    return minutes(previous_local) < target or minutes(now_local) >= target


@dataclass(frozen=True)
class PowerAllocation:
    solar_kw_available: float = 0.0
    compressor_kw: float = 0.0
    compressor_from_solar_kw: float = 0.0
    compressor_from_backup_kw: float = 0.0
    aux_from_solar_kw: float = 0.0
    aux_from_battery_kw: float = 0.0
    aux_from_backup_kw: float = 0.0
    battery_charge_ac_kw: float = 0.0
    battery_discharge_kw: float = 0.0
    backup_kw: float = 0.0
    backup_from_grid_kw: float = 0.0
    backup_from_diesel_kw: float = 0.0
    load_kw: float = 0.0
    solar_used_kw: float = 0.0
    spilled_solar_kw: float = 0.0


@dataclass(frozen=True)
class PlantState:
    tank_temp_c: float
    milk_volume_l: float
    store_kwh: float
    battery_kwh: float


@dataclass(frozen=True)
class StepEnergy:
    milk_added_l: float
    spilled_l: float
    collected: bool
    leak_kwh: float
    to_tank_kwh: float
    to_store_kwh: float
    from_store_kwh: float
    allocation: PowerAllocation


def _battery_discharge_limit_kw(
    battery_kwh: float,
    battery: BatteryConfig,
    step_hours: float,
) -> float:
    if step_hours <= 0 or battery.capacity_kwh <= 0 or battery.discharge_efficiency <= 0:
        return 0.0
    min_kwh = battery.capacity_kwh * battery.min_soc_pct / 100.0
    available_kwh = max(0.0, battery_kwh - min_kwh)
    from_energy = available_kwh * battery.discharge_efficiency / step_hours
    return max(0.0, min(battery.max_discharge_kw, from_energy))


def _battery_charge_limit_kw(battery_kwh: float, battery: BatteryConfig, step_hours: float) -> float:
    if step_hours <= 0 or battery.capacity_kwh <= 0 or battery.charge_efficiency <= 0:
        return 0.0
    room_kwh = max(0.0, battery.capacity_kwh - battery_kwh)
    from_room = room_kwh / (battery.charge_efficiency * step_hours)
    return max(0.0, min(battery.max_charge_kw, from_room))


def realize_power(
    *,
    solar_kw_available: float,
    battery_kwh: float,
    grid_available: bool,
    diesel_available: bool,
    compressor_failed: bool,
    command: ActuatorCommand,
    compressor_max_kw: float,
    battery: BatteryConfig,
    step_hours: float,
) -> PowerAllocation:
    """Turn a controller request into power the plant can actually deliver.

    Solar feeds the compressor first, then pumps and lights, then the battery.
    Backup (grid if it is up, otherwise diesel) covers the compressor only when
    the command allows it, and covers pumps and lights only when that is allowed
    and solar plus the battery fall short.
    """
    solar_left = max(0.0, solar_kw_available)
    backup_ok = bool(grid_available or diesel_available)
    max_kw = max(0.0, compressor_max_kw)

    if compressor_failed:
        compressor_solar = 0.0
        compressor_backup = 0.0
    else:
        requested = min(command.compressor_kw_electric, max_kw)
        compressor_solar = min(requested, solar_left)
        solar_left -= compressor_solar
        compressor_backup = 0.0
        if command.allow_backup_compressor and backup_ok:
            compressor_backup = min(max_kw - compressor_solar, requested - compressor_solar)

    aux_solar = min(max(0.0, command.aux_kw), solar_left)
    solar_left -= aux_solar
    aux_left = max(0.0, command.aux_kw) - aux_solar

    aux_battery = 0.0
    if command.allow_battery_aux and aux_left > 0:
        aux_battery = min(aux_left, _battery_discharge_limit_kw(battery_kwh, battery, step_hours))
        aux_left -= aux_battery

    aux_backup = 0.0
    if command.allow_backup_aux and backup_ok and aux_left > 0:
        aux_backup = aux_left
        aux_left = 0.0

    charge = 0.0
    if command.charge_battery_from_surplus and solar_left > 0:
        charge = min(solar_left, _battery_charge_limit_kw(battery_kwh, battery, step_hours))
        solar_left -= charge

    backup = compressor_backup + aux_backup
    if grid_available:
        backup_grid = backup
        backup_diesel = 0.0
    elif diesel_available:
        backup_grid = 0.0
        backup_diesel = backup
    else:
        backup_grid = 0.0
        backup_diesel = 0.0
        backup = 0.0

    compressor = compressor_solar + compressor_backup
    load = aux_solar + aux_battery + aux_backup
    solar_used = compressor_solar + aux_solar + charge
    return PowerAllocation(
        solar_kw_available=max(0.0, solar_kw_available),
        compressor_kw=compressor,
        compressor_from_solar_kw=compressor_solar,
        compressor_from_backup_kw=compressor_backup,
        aux_from_solar_kw=aux_solar,
        aux_from_battery_kw=aux_battery,
        aux_from_backup_kw=aux_backup,
        battery_charge_ac_kw=charge,
        battery_discharge_kw=aux_battery,
        backup_kw=backup,
        backup_from_grid_kw=backup_grid,
        backup_from_diesel_kw=backup_diesel,
        load_kw=load,
        solar_used_kw=solar_used,
        spilled_solar_kw=max(0.0, solar_left),
    )


def _max_cooling_kwh(
    temp_c: float,
    leak_kwh: float,
    mass_l: float,
    specific: float,
    cutout_c: float,
) -> float:
    """Heat that can be removed without finishing below the freeze cut-out."""
    after_leak = temperature_after_heat(temp_c, mass_l, leak_kwh, specific)
    if after_leak <= cutout_c or mass_l <= 0:
        return 0.0
    return (after_leak - cutout_c) * specific * mass_l / 1000.0


def step_plant(
    state: PlantState,
    *,
    ambient: float,
    solar_kw_available: float,
    grid_available: bool,
    diesel_available: bool,
    compressor_failed: bool,
    milk_added_l: float,
    collect: bool,
    command: ActuatorCommand,
    physics: PhysicsConfig,
    compressor: CompressorConfig,
    cold_store: ColdStoreConfig,
    tank: TankConfig,
    battery: BatteryConfig,
    store_capacity_kwh: float,
    tank_capacity_l: float,
    step_hours: float,
) -> tuple[PlantState, StepEnergy]:
    """Advance the plant by one step. See the module docstring for the order."""
    milk = state.milk_volume_l
    temp = state.tank_temp_c
    collected = False
    if collect:
        milk = 0.0
        collected = True

    temp, milk, spilled = mix_delivery(
        temp,
        milk,
        milk_added_l,
        physics.tank_shell_equivalent_l,
        physics.milk_arrival_temp_c,
        tank_capacity_l,
    )

    allocation = realize_power(
        solar_kw_available=solar_kw_available,
        battery_kwh=state.battery_kwh,
        grid_available=grid_available,
        diesel_available=diesel_available,
        compressor_failed=compressor_failed,
        command=command,
        compressor_max_kw=compressor.max_kw_electric,
        battery=battery,
        step_hours=step_hours,
    )

    cop = coefficient_of_performance(ambient, compressor)
    produced = cold_produced_kwh(allocation.compressor_kw, cop, step_hours)
    requested_tank_kw = tank.max_heat_removal_kw_thermal
    if command.tank_cooling_kw_thermal is not None:
        requested_tank_kw = min(requested_tank_kw, command.tank_cooling_kw_thermal)
    tank_cap_kwh = requested_tank_kw * step_hours
    leak_kwh = heat_leak_kw(ambient, temp, physics.heat_leak_kw_per_c) * step_hours

    to_store = 0.0
    from_compressor_to_tank = 0.0
    if command.compressor_target.value == "store":
        to_store = produced
    elif command.compressor_target.value == "tank":
        from_compressor_to_tank = min(produced, tank_cap_kwh)
        to_store = produced - from_compressor_to_tank

    room = max(0.0, tank_cap_kwh - from_compressor_to_tank)
    from_store = 0.0
    if command.compressor_target.value != "store":
        wanted = max(0.0, command.store_draw_kw_thermal) * step_hours
        from_store = min(wanted, room, max(0.0, state.store_kwh))

    to_tank = from_compressor_to_tank + from_store
    mass = effective_mass_l(milk, physics.tank_shell_equivalent_l)
    allowed = _max_cooling_kwh(
        temp,
        leak_kwh,
        mass,
        physics.milk_specific_heat_wh_per_l_c,
        tank.low_cutout_c,
    )
    if to_tank > allowed:
        extra = to_tank - allowed
        reduce_store = min(from_store, extra)
        from_store -= reduce_store
        extra -= reduce_store
        extra = min(extra, from_compressor_to_tank)
        from_compressor_to_tank -= extra
        to_store += extra
        to_tank = allowed

    stored = to_store * cold_store.charge_efficiency
    store_after_draw = max(0.0, state.store_kwh - from_store)
    room_store = max(0.0, store_capacity_kwh - store_after_draw)
    stored = min(stored, room_store)
    new_store = min(store_capacity_kwh, store_after_draw + stored)

    new_temp = temperature_after_heat(
        temp,
        mass,
        leak_kwh - to_tank,
        physics.milk_specific_heat_wh_per_l_c,
    )

    battery_kwh = state.battery_kwh
    battery_kwh -= allocation.battery_discharge_kw * step_hours / max(battery.discharge_efficiency, 1e-9)
    battery_kwh += allocation.battery_charge_ac_kw * step_hours * battery.charge_efficiency
    min_kwh = battery.capacity_kwh * battery.min_soc_pct / 100.0
    if battery.capacity_kwh > 0:
        # Only enforce the floor when the pack started above it, so a low
        # reading cannot create energy.
        floor = min_kwh if state.battery_kwh >= min_kwh - 1e-9 else 0.0
        battery_kwh = min(battery.capacity_kwh, max(floor, battery_kwh))
    else:
        battery_kwh = 0.0

    new_state = PlantState(
        tank_temp_c=new_temp,
        milk_volume_l=milk,
        store_kwh=new_store,
        battery_kwh=battery_kwh,
    )
    energy = StepEnergy(
        milk_added_l=milk_added_l,
        spilled_l=spilled,
        collected=collected,
        leak_kwh=leak_kwh,
        to_tank_kwh=to_tank,
        to_store_kwh=stored,
        from_store_kwh=from_store,
        allocation=allocation,
    )
    return new_state, energy
