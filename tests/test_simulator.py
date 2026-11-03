"""Simulator schedule, determinism, and fault injection."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from shared.enums import CompressorTarget, FaultType
from shared.paths import scenarios_dir
from shared.physics import ambient_c, cloud_multiplier, solar_kw
from shared.schemas import ActuatorCommand
from simulator.engine import SiteSimulator
from tests.conftest import build_loaded

NAIROBI = ZoneInfo("Africa/Nairobi")


def _at(results, hour: int, minute: int = 0, day: int = 1):
    target = datetime(2026, 6, day, hour, minute, tzinfo=NAIROBI).astimezone(
        __import__("datetime").timezone.utc
    )
    target = target.replace(microsecond=0)
    for result in results:
        if result.reading.timestamp == target:
            return result
    raise AssertionError(f"no sample at {hour:02d}:{minute:02d}")


def test_deliveries_and_collection_follow_the_clock(defaults, demo_site) -> None:
    loaded = build_loaded(defaults, demo_site, hours=24, daily_milk_litres=1000)
    results = SiteSimulator(loaded).run()
    assert sum(step.milk_added_l for step in results) == pytest.approx(1000)
    assert _at(results, 8, 30).reading.milk_volume_l == pytest.approx(550)
    assert _at(results, 9, 0).reading.milk_volume_l == pytest.approx(0)
    assert _at(results, 19, 0).reading.milk_volume_l == pytest.approx(450)


def test_solar_and_ambient_follow_the_formulas(defaults, demo_site) -> None:
    loaded = build_loaded(defaults, demo_site, hours=18, cloudiness=0.05, seed=11)
    results = SiteSimulator(loaded).run()
    noon = _at(results, 12)
    local = noon.reading.timestamp.astimezone(NAIROBI)
    cloud = cloud_multiplier(noon.reading.timestamp, 11, 0.05, defaults.physics)
    assert noon.reading.solar_kw == pytest.approx(
        solar_kw(local, demo_site.solar_kwp, cloud, defaults.physics)
    )
    assert noon.reading.ambient_c == pytest.approx(ambient_c(local, defaults.physics))
    assert _at(results, 3).reading.solar_kw == 0


def test_runs_with_the_same_seed_match(defaults, demo_site) -> None:
    first = SiteSimulator(build_loaded(defaults, demo_site, hours=6, seed=4)).run()
    second = SiteSimulator(build_loaded(defaults, demo_site, hours=6, seed=4)).run()
    assert [step.reading.model_dump() for step in first] == [
        step.reading.model_dump() for step in second
    ]


def test_sensor_dropout_hides_the_tank_but_physics_continues(defaults, demo_site) -> None:
    start = datetime(2026, 6, 1, 0, 0, tzinfo=NAIROBI)
    fault = {
        "type": FaultType.SENSOR_DROPOUT.value,
        "start": start + timedelta(hours=1),
        "end": start + timedelta(hours=2),
        "sensor": "tank_temp_c",
    }
    healthy = SiteSimulator(build_loaded(defaults, demo_site, hours=3, initial_temp=20)).run()
    blind = SiteSimulator(
        build_loaded(defaults, demo_site, hours=3, initial_temp=20, faults=[fault])
    ).run()
    hidden = _at(blind, 1, 30)
    assert hidden.reading.tank_temp_c is None
    assert any(flag.startswith("sensor_dropout:") for flag in hidden.reading.fault_flags)
    assert hidden.truth_tank_temp_c == pytest.approx(_at(healthy, 1, 30).truth_tank_temp_c)
    assert _at(blind, 2, 30).reading.tank_temp_c is not None


def test_stuck_sensor_freezes_the_published_value(defaults, demo_site) -> None:
    start = datetime(2026, 6, 1, 0, 0, tzinfo=NAIROBI)
    fault = {
        "type": FaultType.STUCK_SENSOR.value,
        "start": start + timedelta(minutes=30),
        "end": start + timedelta(hours=2),
        "sensor": "tank_temp_c",
        "value": 3.2,
    }
    results = SiteSimulator(
        build_loaded(defaults, demo_site, hours=3, initial_temp=15, faults=[fault])
    ).run()
    assert _at(results, 1).reading.tank_temp_c == pytest.approx(3.2)
    assert _at(results, 1).truth_tank_temp_c != pytest.approx(3.2)


def test_compressor_failure_stops_cooling(defaults, demo_site) -> None:
    start = datetime(2026, 6, 1, 10, 0, tzinfo=NAIROBI)
    fault = {
        "type": FaultType.COMPRESSOR_FAILURE.value,
        "start": start,
        "end": start + timedelta(hours=2),
    }
    command = ActuatorCommand(
        compressor_kw_electric=3,
        compressor_target=CompressorTarget.TANK,
        aux_kw=0,
        charge_battery_from_surplus=False,
    )
    healthy = SiteSimulator(
        build_loaded(defaults, demo_site, hours=1, start=start, initial_temp=20, initial_milk=800)
    ).run(lambda _reading: command)
    failed = SiteSimulator(
        build_loaded(
            defaults,
            demo_site,
            hours=1,
            start=start,
            initial_temp=20,
            initial_milk=800,
            faults=[fault],
        )
    ).run(lambda _reading: command)
    assert failed[-1].reading.compressor_kw == 0
    assert "compressor_failure" in failed[-1].reading.fault_flags
    assert failed[-1].truth_tank_temp_c > healthy[-1].truth_tank_temp_c


def test_grid_outage_and_heavy_cloud(defaults, demo_site) -> None:
    start = datetime(2026, 6, 1, 11, 0, tzinfo=NAIROBI)
    faults = [
        {
            "type": FaultType.GRID_OUTAGE.value,
            "start": start,
            "end": start + timedelta(hours=2),
        },
        {
            "type": FaultType.HEAVY_CLOUD.value,
            "start": start,
            "end": start + timedelta(hours=2),
            "factor": 0.1,
        },
    ]
    clear = SiteSimulator(
        build_loaded(defaults, demo_site, hours=2, start=start, cloudiness=0)
    ).run()
    covered = SiteSimulator(
        build_loaded(defaults, demo_site, hours=2, start=start, cloudiness=0, faults=faults)
    ).run()
    noon = _at(covered, 12)
    assert noon.reading.grid_available is False
    assert noon.reading.solar_kw < _at(clear, 12).reading.solar_kw * 0.5


def test_scenario_files_load() -> None:
    from shared.config import load_scenario

    names = [
        "sunny_day.yaml",
        "cloudy_day.yaml",
        "undersized_hub.yaml",
        "evening_milk_spike.yaml",
        "comms_outage.yaml",
    ]
    loaded = [load_scenario(scenarios_dir() / name) for name in names]
    assert [item.scenario.name for item in loaded] == [
        "sunny_day",
        "cloudy_day",
        "undersized_hub",
        "evening_milk_spike",
        "comms_outage",
    ]
    assert loaded[2].scenario.site.solar_kwp == pytest.approx(0.8)
    assert loaded[3].config.physics.evening_milk_fraction == pytest.approx(0.8)


def test_comms_outage_lasts_six_hours(defaults) -> None:
    from shared.config import load_scenario

    loaded = load_scenario(scenarios_dir() / "comms_outage.yaml")
    # Only the first 16 hours are needed to cover the 08:00-14:00 outage.
    loaded.scenario.duration_hours = 16
    results = SiteSimulator(loaded).run()
    down_minutes = sum(1 for step in results if not step.link_up)
    assert down_minutes == 6 * 60
