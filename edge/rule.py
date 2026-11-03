"""Rule-based power allocation.

Priority, evaluated together on each call:

1. Protect the milk. If the thermostat says the tank needs cooling, remove heat
   up to the tank limit. Use the compressor when solar (or backup, if the
   controller has allowed it) is available, and take the rest from the cold store.
2. Charge the cold store from spare solar once the tank is satisfied, up to the
   rule-mode target (75% unless config says otherwise). No grid and no diesel.
3. Pumps and lights take remaining solar, then the battery.
4. Backup is a last resort. The controller decides when cooling backup is
   allowed (tank above 6°C and store below 0.5 kWh). This function only honours
   that flag. Auxiliary backup runs when solar and the battery cannot cover the
   pumps and lights.

The compressor request is variable up to its electrical maximum. The inverter
can limit power, and a small temperature gap must not dump 5.5 kW into a
nearly empty tank. ``TODO(verify)`` if the installed compressor is on/off only.
"""

from __future__ import annotations

from shared.enums import CompressorTarget, ControlActionKind
from shared.physics import (
    coefficient_of_performance,
    effective_mass_l,
    heat_leak_kw,
    realize_power,
)
from shared.schemas import ActuatorCommand
from edge.strategy import StrategyContext, StrategyResult


class RuleStrategy:
    """Phase 1 strategy. No forecast. Store target comes from config."""

    name = "rule"

    def decide(self, ctx: StrategyContext) -> StrategyResult:
        config = ctx.config
        reading = ctx.reading
        solar = max(0.0, reading.solar_kw or 0.0)
        ambient = reading.ambient_c if reading.ambient_c is not None else config.compressor.cop_reference_ambient_c
        cop = coefficient_of_performance(ambient, config.compressor)
        max_electric = config.compressor.max_kw_electric
        store_draw_cap = config.cold_store.max_draw_kw_thermal

        tank_need = 0.0
        store_draw = 0.0
        compressor_kw = 0.0
        target = CompressorTarget.OFF

        if ctx.tank_cooling and reading.tank_temp_c is not None and reading.milk_volume_l is not None:
            tank_need = _thermal_kw_for_tank(ctx, ambient)
            electric_for_tank = tank_need / cop if cop > 0 else max_electric
            if ctx.backup_cooling:
                compressor_kw = min(max_electric, max(electric_for_tank, 0.0))
            else:
                compressor_kw = min(max_electric, max(electric_for_tank, 0.0), solar)
            # Spare solar charges the store while the tank is being held,
            # but only when the store is below its target.
            if ctx.store_charging and not ctx.backup_cooling:
                compressor_kw = min(max_electric, solar)
            thermal_from_compressor = compressor_kw * cop
            store_draw = min(store_draw_cap, max(0.0, tank_need - thermal_from_compressor))
            target = CompressorTarget.TANK
        elif ctx.store_charging and not ctx.backup_cooling:
            compressor_kw = min(max_electric, solar)
            target = CompressorTarget.STORE if compressor_kw > 0 else CompressorTarget.OFF

        command = ActuatorCommand(
            compressor_kw_electric=compressor_kw,
            compressor_target=target,
            tank_cooling_kw_thermal=tank_need if target == CompressorTarget.TANK else 0.0,
            store_draw_kw_thermal=store_draw,
            aux_kw=self.aux_kw(ctx),
            allow_backup_compressor=ctx.backup_cooling,
            allow_battery_aux=True,
            allow_backup_aux=config.aux.backup_allowed,
            charge_battery_from_surplus=True,
        )
        allocation = realize_power(
            solar_kw_available=solar,
            battery_kwh=ctx.battery_kwh or 0.0,
            grid_available=bool(reading.grid_available),
            diesel_available=ctx.site.diesel_available,
            compressor_failed="compressor_failure" in reading.fault_flags,
            command=command,
            compressor_max_kw=max_electric,
            battery=config.battery,
            step_hours=ctx.step_hours,
        )
        action = _primary_action(ctx, allocation.aux_from_backup_kw, allocation.load_kw, target)
        reason = _reason(ctx, allocation, tank_need, cop)
        return StrategyResult(action=action, command=command, reason=reason)

    def store_target_pct(self, ctx: StrategyContext) -> float:
        """Rule mode holds the configured store target. Forecast mode can raise it."""
        return ctx.config.controller.store_target_soc_pct

    def aux_kw(self, ctx: StrategyContext) -> float:
        return ctx.config.aux.load_kw


def _thermal_kw_for_tank(ctx: StrategyContext, ambient: float) -> float:
    """kW thermal that brings the tank to the setpoint over this step, plus leak.

    Capped at the equipment limit. A large gap therefore runs at the limit.
    A tenth of a degree does not.
    """
    config = ctx.config
    assert ctx.reading.tank_temp_c is not None
    assert ctx.reading.milk_volume_l is not None
    mass = effective_mass_l(ctx.reading.milk_volume_l, config.physics.tank_shell_equivalent_l)
    specific = config.physics.milk_specific_heat_wh_per_l_c
    gap_kwh = max(0.0, (ctx.reading.tank_temp_c - config.tank.setpoint_c) * specific * mass / 1000.0)
    leak_kw = heat_leak_kw(ambient, ctx.reading.tank_temp_c, config.physics.heat_leak_kw_per_c)
    leak_kwh = max(0.0, leak_kw) * ctx.step_hours
    if ctx.step_hours <= 0:
        return config.tank.max_heat_removal_kw_thermal
    requested = (gap_kwh + leak_kwh) / ctx.step_hours
    return min(config.tank.max_heat_removal_kw_thermal, max(0.0, requested))


def _primary_action(ctx: StrategyContext, aux_backup_kw: float, load_kw: float, target: CompressorTarget) -> ControlActionKind:
    if ctx.backup_cooling:
        return ControlActionKind.BACKUP_COOLING
    if ctx.tank_cooling and target == CompressorTarget.TANK:
        return ControlActionKind.COOL_TANK
    if ctx.store_charging and target == CompressorTarget.STORE:
        return ControlActionKind.CHARGE_STORE
    if aux_backup_kw > 0:
        return ControlActionKind.BACKUP_AUX
    if load_kw > 0:
        return ControlActionKind.RUN_AUX
    return ControlActionKind.IDLE


def _reason(ctx: StrategyContext, allocation, tank_need: float, cop: float) -> str:
    reading = ctx.reading
    target_soc = ctx.store_target_pct
    store_txt = _store_phrase(ctx, target_soc)
    solar_txt = _kw(allocation.solar_kw_available)

    if ctx.backup_cooling:
        start = ctx.config.tank.backup_start_temp_c
        clear = ctx.config.tank.backup_clear_temp_c
        above = reading.tank_temp_c is not None and reading.tank_temp_c > start
        if above:
            return (
                f"Backup cooling starts: tank is {_c(reading.tank_temp_c)}, "
                f"past {start:.1f}°C, and {store_txt}. Solar is {solar_txt}."
            )
        return (
            f"Backup cooling stays on: tank is {_c(reading.tank_temp_c)} and {store_txt}. "
            f"It stops at {clear:.1f}°C or when the store has cold again. Solar is {solar_txt}."
        )
    if ctx.tank_cooling:
        return (
            f"Tank is {_c(reading.tank_temp_c)}, above the {ctx.config.tank.setpoint_c:.1f}°C band, "
            f"so it is being cooled (about {tank_need:.1f} kW thermal, COP {cop:.1f}). "
            f"Solar is {solar_txt}. {_sentence(store_txt)}."
        )
    if ctx.store_charging and allocation.compressor_kw > 0:
        return (
            f"Tank is {_c(reading.tank_temp_c)}, inside the band. "
            f"{_sentence(store_txt)}, so spare solar charges the store. "
            f"The compressor is on solar only ({_kw(allocation.compressor_from_solar_kw)})."
        )
    if ctx.store_charging and allocation.compressor_kw == 0:
        return (
            f"Tank is {_c(reading.tank_temp_c)}, inside the band. "
            f"{_sentence(store_txt)}, but there is no spare solar "
            f"({solar_txt}), so the store waits. {_aux_phrase(allocation)}"
        )
    return (
        f"Tank is {_c(reading.tank_temp_c)}, inside the band. "
        f"{_sentence(store_txt)}. {_aux_phrase(allocation)}"
    )


def _store_phrase(ctx: StrategyContext, target_soc: float) -> str:
    if ctx.store_kwh is None or ctx.site.cold_store_kwh_thermal <= 0:
        return "the cold store level is unknown"
    soc = 100.0 * ctx.store_kwh / ctx.site.cold_store_kwh_thermal
    return f"the store is at {soc:.0f}% of capacity (target {target_soc:.0f}%, {ctx.store_kwh:.1f} kWh)"


def _aux_phrase(allocation) -> str:
    if allocation.load_kw <= 0:
        return "Pumps and lights are off."
    parts: list[str] = []
    if allocation.aux_from_solar_kw > 0:
        parts.append(f"solar {_kw(allocation.aux_from_solar_kw)}")
    if allocation.aux_from_battery_kw > 0:
        parts.append(f"the battery {_kw(allocation.aux_from_battery_kw)}")
    if allocation.aux_from_backup_kw > 0:
        parts.append(f"backup {_kw(allocation.aux_from_backup_kw)}")
    if not parts:
        return "Pumps and lights are not covered."
    return "Pumps and lights draw " + " and ".join(parts) + "."


def _sentence(text: str) -> str:
    if not text:
        return text
    return text[0].upper() + text[1:]


def _c(value: float | None) -> str:
    if value is None:
        return "unknown"
    return f"{value:.1f}°C"


def _kw(value: float) -> str:
    return f"{value:.1f} kW"
