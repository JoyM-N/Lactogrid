"""Controller decisions, safety, overrides, the local log, and the 48-hour scenarios."""

from datetime import datetime, timedelta, timezone

import pytest

from shared.config import load_scenario
from shared.enums import CompressorTarget, ControlActionKind, ControlMode, EventType
from shared.paths import scenarios_dir
from shared.schemas import ActuatorCommand, ManualOverride, Reading
from edge.controller import Controller
from edge.loop import run_closed_loop
from edge.store import Conflict, EdgeStore
from edge.watchdog import Watchdog

UTC = timezone.utc


def _reading(site, minute: int = 0, **overrides) -> Reading:
    payload = dict(
        site_id=site.id,
        timestamp=datetime(2026, 6, 1, 9, 0, tzinfo=UTC) + timedelta(minutes=minute),
        tank_temp_c=4.0,
        ambient_c=24.0,
        solar_kw=6.0,
        store_kwh=45.0,
        store_soc_pct=75.0,
        battery_kwh=2.4,
        battery_soc_pct=80.0,
        milk_volume_l=200.0,
        grid_available=True,
        fault_flags=[],
    )
    payload.update(overrides)
    return Reading(**payload)


def _controller(defaults, site) -> Controller:
    return Controller(defaults, site, step_hours=1 / 60)


def test_hot_tank_is_cooled_from_solar_and_the_store_without_backup(defaults, demo_site) -> None:
    decision = _controller(defaults, demo_site).step(_reading(demo_site, tank_temp_c=5.2, solar_kw=2.0))
    assert decision.mode == ControlMode.NORMAL
    assert decision.action == ControlActionKind.COOL_TANK
    assert decision.command.compressor_target == CompressorTarget.TANK
    assert decision.command.allow_backup_compressor is False
    assert decision.command.compressor_kw_electric > 0
    assert "5.2°C" in decision.reason
    assert "backup" not in decision.reason.lower() or "Backup stays off" not in decision.reason


def test_no_solar_cools_the_tank_from_the_store_not_the_battery(defaults, demo_site) -> None:
    decision = _controller(defaults, demo_site).step(
        _reading(demo_site, tank_temp_c=5.5, solar_kw=0.0, battery_kwh=2.5)
    )
    assert decision.action == ControlActionKind.COOL_TANK
    assert decision.command.compressor_kw_electric == 0
    assert decision.command.store_draw_kw_thermal > 0
    assert decision.command.allow_backup_compressor is False


def test_satisfied_tank_charges_a_low_store_from_solar_only(defaults, demo_site) -> None:
    decision = _controller(defaults, demo_site).step(
        _reading(demo_site, tank_temp_c=3.6, store_kwh=30.0, store_soc_pct=50.0, solar_kw=5.0)
    )
    assert decision.action == ControlActionKind.CHARGE_STORE
    assert decision.command.compressor_target == CompressorTarget.STORE
    assert decision.command.allow_backup_compressor is False
    assert decision.command.compressor_kw_electric == pytest.approx(3.0)
    assert "solar only" in decision.reason


def test_full_store_and_cold_tank_runs_pumps_and_lights(defaults, demo_site) -> None:
    decision = _controller(defaults, demo_site).step(
        _reading(demo_site, tank_temp_c=3.6, store_kwh=50.0, store_soc_pct=83.0, solar_kw=4.0)
    )
    assert decision.action == ControlActionKind.RUN_AUX
    assert decision.command.compressor_kw_electric == 0
    assert "Pumps and lights" in decision.reason


def test_backup_starts_only_when_the_tank_is_hot_and_the_store_is_nearly_empty(defaults, demo_site) -> None:
    controller = _controller(defaults, demo_site)
    hot_but_stored = controller.step(_reading(demo_site, tank_temp_c=6.8, store_kwh=20.0))
    assert hot_but_stored.command.allow_backup_compressor is False
    assert hot_but_stored.action == ControlActionKind.COOL_TANK

    empty = _controller(defaults, demo_site).step(
        _reading(demo_site, tank_temp_c=6.8, store_kwh=0.2, store_soc_pct=0.3)
    )
    assert empty.action == ControlActionKind.BACKUP_COOLING
    assert empty.command.allow_backup_compressor is True
    assert any(event.event_type == EventType.BACKUP_STARTED for event in empty.events)
    assert any(event.event_type == EventType.TEMP_LIMIT_EXCEEDED for event in empty.events)


def test_thermostat_hysteresis(defaults, demo_site) -> None:
    controller = _controller(defaults, demo_site)
    assert controller.step(_reading(demo_site, minute=0, tank_temp_c=4.0)).action != ControlActionKind.COOL_TANK
    assert controller.step(_reading(demo_site, minute=1, tank_temp_c=4.05)).action != ControlActionKind.COOL_TANK
    assert controller.step(_reading(demo_site, minute=2, tank_temp_c=4.2)).action == ControlActionKind.COOL_TANK
    assert controller.step(_reading(demo_site, minute=3, tank_temp_c=4.0)).action == ControlActionKind.COOL_TANK
    stopped = controller.step(_reading(demo_site, minute=4, tank_temp_c=3.9))
    assert stopped.action != ControlActionKind.COOL_TANK


def test_missing_tank_sensor_enters_safe_mode_and_leaves_the_compressor_enabled(defaults, demo_site) -> None:
    decision = _controller(defaults, demo_site).step(_reading(demo_site, tank_temp_c=None))
    assert decision.mode == ControlMode.SAFE
    assert decision.action == ControlActionKind.SAFE_MODE_COOL
    assert decision.command.compressor_kw_electric > 0
    assert decision.command.compressor_target == CompressorTarget.TANK
    assert any(event.event_type == EventType.SAFE_MODE_ENTERED for event in decision.events)
    assert any(event.event_type == EventType.SENSOR_LOST for event in decision.events)


def test_implausible_temperature_enters_safe_mode(defaults, demo_site) -> None:
    decision = _controller(defaults, demo_site).step(_reading(demo_site, tank_temp_c=80))
    assert decision.mode == ControlMode.SAFE
    assert "80.0°C" in decision.reason


def test_stuck_flag_enters_safe_mode_immediately(defaults, demo_site) -> None:
    decision = _controller(defaults, demo_site).step(
        _reading(demo_site, tank_temp_c=3.2, fault_flags=["stuck_sensor:tank_temp_c"])
    )
    assert decision.mode == ControlMode.SAFE
    assert any(event.event_type == EventType.SENSOR_STUCK for event in decision.events)


def test_flat_trace_with_no_cooling_is_treated_as_stuck(defaults, demo_site) -> None:
    controller = _controller(defaults, demo_site)
    early = None
    last = None
    for minute in range(50):
        last = controller.step(
            _reading(
                demo_site,
                minute=minute,
                tank_temp_c=3.2,
                ambient_c=30.0,
                solar_kw=0.0,
                milk_volume_l=400.0,
            )
        )
        if minute == 5:
            early = last
    assert early is not None and early.mode == ControlMode.NORMAL
    assert last is not None and last.mode == ControlMode.SAFE


def test_manual_override_is_logged_and_loses_to_safety(defaults, demo_site) -> None:
    controller = _controller(defaults, demo_site)
    start = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    event = controller.set_override(
        ManualOverride(
            site_id=demo_site.id,
            actor="Amina",
            reason="Cleaning the condenser",
            started_at=start,
            expires_at=start + timedelta(minutes=30),
            command=ActuatorCommand(),
        )
    )
    assert event.event_type == EventType.MANUAL_OVERRIDE
    assert event.details["actor"] == "Amina"
    held = controller.step(_reading(demo_site, minute=5, tank_temp_c=5.5))
    assert held.mode == ControlMode.MANUAL_OVERRIDE
    assert held.command.compressor_kw_electric == 0
    assert "Amina" in held.reason

    safe = controller.step(_reading(demo_site, minute=6, tank_temp_c=None))
    assert safe.mode == ControlMode.SAFE
    assert safe.command.compressor_kw_electric > 0
    assert "safety rules take priority" in safe.reason

    expired = controller.step(_reading(demo_site, minute=40, tank_temp_c=3.6, store_kwh=50.0))
    assert expired.mode == ControlMode.NORMAL
    assert any(item.event_type == EventType.MANUAL_OVERRIDE_EXPIRED for item in expired.events)


def test_watchdog_fallback_allows_the_compressor(defaults) -> None:
    watchdog = Watchdog(defaults.watchdog.timeout_seconds)
    start = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
    watchdog.beat(start)
    assert watchdog.expired(start + timedelta(seconds=30)) is False
    assert watchdog.expired(start + timedelta(seconds=defaults.watchdog.timeout_seconds + 1)) is True
    command = watchdog.fallback_command(defaults)
    assert command.compressor_kw_electric > 0
    assert command.compressor_target == CompressorTarget.TANK


def test_local_log_is_idempotent_and_purges_old_raw_samples(defaults, demo_site) -> None:
    store = EdgeStore()
    reading = _reading(demo_site)
    assert store.add_reading(reading) == "inserted"
    assert store.add_reading(reading) == "duplicate"
    assert store.reading_count() == 1
    changed = reading.model_copy(update={"tank_temp_c": 9.0})
    with pytest.raises(Conflict):
        store.add_reading(changed)

    old = _reading(demo_site, minute=1, timestamp=datetime(2026, 1, 1, tzinfo=UTC))
    assert store.add_reading(old, record_type="reading_raw") == "inserted"
    removed = store.purge_raw(datetime(2026, 5, 1, tzinfo=UTC))
    assert removed == 1
    store.close()


def test_sunny_day_keeps_milk_at_or_below_6c_and_backup_under_2kwh() -> None:
    loaded = load_scenario(scenarios_dir() / "sunny_day.yaml")
    result = run_closed_loop(loaded)
    _assert_scenario(loaded, result)


def test_undersized_hub_uses_backup_and_raises_a_temperature_event() -> None:
    loaded = load_scenario(scenarios_dir() / "undersized_hub.yaml")
    result = run_closed_loop(loaded)
    assert _backup_kwh(result, loaded.scenario.step_seconds) >= loaded.scenario.expect.min_backup_kwh
    hot = [
        reading.tank_temp_c
        for reading in result.readings
        if (reading.milk_volume_l or 0) > 0 and reading.tank_temp_c is not None and reading.tank_temp_c > 6
    ]
    assert len(hot) >= loaded.scenario.expect.min_samples_above_safe_c
    assert any(event.event_type == EventType.BACKUP_STARTED for event in result.events)
    assert any(event.event_type == EventType.TEMP_LIMIT_EXCEEDED for event in result.events)


def test_closed_loop_survives_a_strategy_crash(monkeypatch) -> None:
    from edge.rule import RuleStrategy

    loaded = load_scenario(scenarios_dir() / "sunny_day.yaml")
    loaded.scenario.duration_hours = 0.05
    original = RuleStrategy.decide
    calls = {"n": 0}

    def boom(self, ctx):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("scan failed")
        return original(self, ctx)

    monkeypatch.setattr(RuleStrategy, "decide", boom)
    result = run_closed_loop(loaded)
    assert any(event.event_type == EventType.WATCHDOG_RESTART for event in result.events)
    assert result.decisions[1].command.compressor_kw_electric > 0


def _assert_scenario(loaded, result) -> None:
    expect = loaded.scenario.expect
    with_milk = [
        reading.tank_temp_c
        for reading in result.readings
        if (reading.milk_volume_l or 0) > 0 and reading.tank_temp_c is not None
    ]
    assert with_milk, "expected milk in the tank"
    assert max(with_milk) <= expect.max_tank_temp_with_milk_c
    per_day = _backup_kwh_per_day(result, loaded.scenario.timezone, loaded.scenario.step_seconds)
    assert per_day, "expected at least one day"
    assert max(per_day.values()) < expect.max_backup_kwh_per_day


def _backup_kwh(result, step_seconds: int) -> float:
    hours = step_seconds / 3600.0
    return sum((reading.backup_kw or 0.0) * hours for reading in result.readings)


def _backup_kwh_per_day(result, timezone_name: str, step_seconds: int) -> dict:
    from zoneinfo import ZoneInfo

    hours = step_seconds / 3600.0
    zone = ZoneInfo(timezone_name)
    totals: dict = {}
    for reading in result.readings:
        day = reading.timestamp.astimezone(zone).date()
        totals[day] = totals.get(day, 0.0) + (reading.backup_kw or 0.0) * hours
    return totals
