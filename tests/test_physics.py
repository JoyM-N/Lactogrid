"""Unit tests for the audited plant formulas."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from shared.enums import CompressorTarget
from shared.physics import (
    PlantState,
    ambient_c,
    cloud_multiplier,
    coefficient_of_performance,
    heat_leak_kw,
    milk_arriving_litres,
    mix_delivery,
    solar_kw,
    step_plant,
    temperature_after_heat,
)
from shared.schemas import ActuatorCommand

NAIROBI = ZoneInfo("Africa/Nairobi")


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 1, hour, minute, tzinfo=NAIROBI)


def test_solar_curve_matches_the_brief(defaults) -> None:
    physics = defaults.physics
    assert solar_kw(_at(12), 10, 1, physics) == pytest.approx(8.5)
    assert solar_kw(_at(3), 10, 1, physics) == 0
    assert solar_kw(_at(6), 10, 1, physics) == 0
    assert solar_kw(_at(18), 10, 1, physics) == 0
    # 15:00 is halfway from noon to sunset: sin(0.75 * pi) ** 1.3 * 0.85 * kwp.
    sine = pytest.approx(solar_kw(_at(15), 10, 1, physics))
    assert sine == pytest.approx(10 * 0.85 * (0.70710678118 ** 1.3), rel=1e-4)


def test_ambient_curve_matches_the_brief(defaults) -> None:
    physics = defaults.physics
    assert ambient_c(_at(9), physics) == pytest.approx(22)
    assert ambient_c(_at(15), physics) == pytest.approx(28)
    assert ambient_c(_at(3), physics) == pytest.approx(16)


def test_cloud_factor_is_deterministic_and_respects_cloudiness(defaults) -> None:
    moment = _at(12)
    first = cloud_multiplier(moment, seed=7, cloudiness=0.1, physics=defaults.physics)
    second = cloud_multiplier(moment, seed=7, cloudiness=0.1, physics=defaults.physics)
    overcast = cloud_multiplier(moment, seed=7, cloudiness=1, physics=defaults.physics)
    assert first == second
    assert 0.02 <= first <= 1
    assert overcast < first


def test_cop_formula(defaults) -> None:
    compressor = defaults.compressor
    assert coefficient_of_performance(20, compressor) == pytest.approx(3.0)
    assert coefficient_of_performance(25, compressor) == pytest.approx(2.8)
    assert coefficient_of_performance(60, compressor) == pytest.approx(1.8)


def test_mixing_uses_the_shell_as_thermal_mass() -> None:
    # 150 L shell at 4 C plus 100 L already in the tank, then 50 L at 30 C.
    temp, milk, spilled = mix_delivery(4, 100, 50, 150, 30, tank_capacity_l=2000)
    assert milk == 150
    assert spilled == 0
    assert temp == pytest.approx((4 * 250 + 30 * 50) / 300)


def test_delivery_spills_when_the_tank_is_full() -> None:
    _temp, milk, spilled = mix_delivery(4, 190, 20, 150, 30, tank_capacity_l=200)
    assert milk == 200
    assert spilled == pytest.approx(10)


def test_heat_leak_and_temperature_change(defaults) -> None:
    assert heat_leak_kw(24, 4, defaults.physics.heat_leak_kw_per_c) == pytest.approx(0.6)
    # 0.6 kWh into 1150 litre-equivalents.
    warmed = temperature_after_heat(4, 1150, 0.6, defaults.physics.milk_specific_heat_wh_per_l_c)
    assert warmed == pytest.approx(4 + 600 / (1.083 * 1150))


def test_tank_cooling_is_capped_and_surplus_cold_charges_the_store(defaults) -> None:
    state = PlantState(tank_temp_c=20, milk_volume_l=1000, store_kwh=30, battery_kwh=2)
    command = ActuatorCommand(
        compressor_kw_electric=3,
        compressor_target=CompressorTarget.TANK,
        aux_kw=0,
        charge_battery_from_surplus=False,
    )
    new, energy = step_plant(
        state,
        ambient=20,
        solar_kw_available=10,
        grid_available=True,
        diesel_available=True,
        compressor_failed=False,
        milk_added_l=0,
        collect=False,
        command=command,
        physics=defaults.physics,
        compressor=defaults.compressor,
        cold_store=defaults.cold_store,
        tank=defaults.tank,
        battery=defaults.battery,
        store_capacity_kwh=60,
        tank_capacity_l=2000,
        step_hours=1,
    )
    # COP 3 * 3 kW = 9 kW thermal, tank accepts 5.5, store receives 3.5.
    assert energy.to_tank_kwh == pytest.approx(5.5)
    assert energy.to_store_kwh == pytest.approx(3.5)
    assert new.store_kwh == pytest.approx(33.5)
    assert new.tank_temp_c == pytest.approx(20 - 5500 / (1.083 * 1150))
    assert energy.allocation.compressor_from_backup_kw == 0


def test_store_draw_cannot_exceed_the_tank_limit(defaults) -> None:
    state = PlantState(tank_temp_c=20, milk_volume_l=1000, store_kwh=30, battery_kwh=2)
    command = ActuatorCommand(store_draw_kw_thermal=6, aux_kw=0, charge_battery_from_surplus=False)
    new, energy = step_plant(
        state,
        ambient=20,
        solar_kw_available=0,
        grid_available=False,
        diesel_available=False,
        compressor_failed=False,
        milk_added_l=0,
        collect=False,
        command=command,
        physics=defaults.physics,
        compressor=defaults.compressor,
        cold_store=defaults.cold_store,
        tank=defaults.tank,
        battery=defaults.battery,
        store_capacity_kwh=60,
        tank_capacity_l=2000,
        step_hours=1,
    )
    assert energy.from_store_kwh == pytest.approx(5.5)
    assert new.store_kwh == pytest.approx(24.5)
    assert energy.allocation.backup_kw == 0


def test_backup_tops_up_the_compressor_only_when_allowed(defaults) -> None:
    state = PlantState(tank_temp_c=10, milk_volume_l=500, store_kwh=10, battery_kwh=1)
    allowed = ActuatorCommand(
        compressor_kw_electric=3,
        compressor_target=CompressorTarget.TANK,
        allow_backup_compressor=True,
        aux_kw=0,
        charge_battery_from_surplus=False,
    )
    refused = allowed.model_copy(update={"allow_backup_compressor": False})
    common = dict(
        ambient=20,
        solar_kw_available=1,
        grid_available=True,
        diesel_available=True,
        compressor_failed=False,
        milk_added_l=0,
        collect=False,
        physics=defaults.physics,
        compressor=defaults.compressor,
        cold_store=defaults.cold_store,
        tank=defaults.tank,
        battery=defaults.battery,
        store_capacity_kwh=60,
        tank_capacity_l=2000,
        step_hours=1,
    )
    _cooled, with_backup = step_plant(state, command=allowed, **common)
    _held, without = step_plant(state, command=refused, **common)
    assert with_backup.allocation.compressor_kw == pytest.approx(3)
    assert with_backup.allocation.compressor_from_solar_kw == pytest.approx(1)
    assert with_backup.allocation.backup_from_grid_kw == pytest.approx(2)
    assert without.allocation.compressor_kw == pytest.approx(1)
    assert without.allocation.backup_kw == 0


def test_diesel_is_used_when_the_grid_is_down(defaults) -> None:
    state = PlantState(tank_temp_c=10, milk_volume_l=500, store_kwh=10, battery_kwh=0.3)
    command = ActuatorCommand(
        compressor_kw_electric=3,
        compressor_target=CompressorTarget.TANK,
        allow_backup_compressor=True,
        aux_kw=0.4,
        allow_battery_aux=True,
        allow_backup_aux=True,
        charge_battery_from_surplus=False,
    )
    _state, energy = step_plant(
        state,
        ambient=20,
        solar_kw_available=0,
        grid_available=False,
        diesel_available=True,
        compressor_failed=False,
        milk_added_l=0,
        collect=False,
        command=command,
        physics=defaults.physics,
        compressor=defaults.compressor,
        cold_store=defaults.cold_store,
        tank=defaults.tank,
        battery=defaults.battery,
        store_capacity_kwh=60,
        tank_capacity_l=2000,
        step_hours=1,
    )
    assert energy.allocation.backup_from_grid_kw == 0
    assert energy.allocation.backup_from_diesel_kw == pytest.approx(3.4)


def test_battery_charge_uses_the_configured_efficiency(defaults) -> None:
    state = PlantState(tank_temp_c=4, milk_volume_l=0, store_kwh=10, battery_kwh=1.5)
    command = ActuatorCommand(aux_kw=0, charge_battery_from_surplus=True)
    new, energy = step_plant(
        state,
        ambient=4,
        solar_kw_available=5,
        grid_available=True,
        diesel_available=True,
        compressor_failed=False,
        milk_added_l=0,
        collect=False,
        command=command,
        physics=defaults.physics,
        compressor=defaults.compressor,
        cold_store=defaults.cold_store,
        tank=defaults.tank,
        battery=defaults.battery,
        store_capacity_kwh=60,
        tank_capacity_l=2000,
        step_hours=1,
    )
    assert energy.allocation.battery_charge_ac_kw == pytest.approx(1.5)
    assert new.battery_kwh == pytest.approx(1.5 + 1.5 * 0.9)


def test_compressor_failure_produces_no_cold(defaults) -> None:
    state = PlantState(tank_temp_c=20, milk_volume_l=1000, store_kwh=30, battery_kwh=2)
    command = ActuatorCommand(
        compressor_kw_electric=3,
        compressor_target=CompressorTarget.TANK,
        allow_backup_compressor=True,
        aux_kw=0,
        charge_battery_from_surplus=False,
    )
    new, energy = step_plant(
        state,
        ambient=20,
        solar_kw_available=10,
        grid_available=True,
        diesel_available=True,
        compressor_failed=True,
        milk_added_l=0,
        collect=False,
        command=command,
        physics=defaults.physics,
        compressor=defaults.compressor,
        cold_store=defaults.cold_store,
        tank=defaults.tank,
        battery=defaults.battery,
        store_capacity_kwh=60,
        tank_capacity_l=2000,
        step_hours=1,
    )
    assert energy.allocation.compressor_kw == 0
    assert new.tank_temp_c == pytest.approx(20)
    assert new.store_kwh == pytest.approx(30)


def test_freeze_cutout_stops_the_tank_going_below_the_configured_floor(defaults) -> None:
    state = PlantState(tank_temp_c=1.2, milk_volume_l=0, store_kwh=20, battery_kwh=1)
    command = ActuatorCommand(
        compressor_kw_electric=3,
        compressor_target=CompressorTarget.TANK,
        aux_kw=0,
        charge_battery_from_surplus=False,
    )
    new, _energy = step_plant(
        state,
        ambient=1.2,
        solar_kw_available=10,
        grid_available=True,
        diesel_available=False,
        compressor_failed=False,
        milk_added_l=0,
        collect=False,
        command=command,
        physics=defaults.physics,
        compressor=defaults.compressor,
        cold_store=defaults.cold_store,
        tank=defaults.tank,
        battery=defaults.battery,
        store_capacity_kwh=60,
        tank_capacity_l=2000,
        step_hours=1,
    )
    assert new.tank_temp_c == pytest.approx(defaults.tank.low_cutout_c)


def test_one_day_of_deliveries_sums_to_the_daily_total(defaults) -> None:
    total = 0.0
    moment = _at(0)
    for _ in range(24 * 60):
        total += milk_arriving_litres(moment, 1 / 60, 1000, defaults.physics)
        moment = moment + __import__("datetime").timedelta(minutes=1)
    assert total == pytest.approx(1000)
