"""Shared fixtures."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from shared.config import LoadedScenario, apply_site_equipment, load_root_config, load_scenario
from shared.paths import scenarios_dir
from shared.schemas import Site
from simulator.engine import SiteSimulator


@pytest.fixture(scope="session")
def defaults():
    return load_root_config()


@pytest.fixture(scope="session")
def demo_site() -> Site:
    return Site(
        id="test-site",
        name="Test Centre",
        location="Kenya (test)",
        timezone="Africa/Nairobi",
        solar_kwp=12,
        compressor_kw_electric=3,
        cold_store_kwh_thermal=60,
        battery_kwh=3,
        tank_litres=2000,
        daily_milk_litres=1000,
        config_version=1,
    )


def build_loaded(
    defaults,
    site: Site,
    *,
    hours: float = 2,
    faults: list | None = None,
    cloudiness: float = 0.0,
    seed: int = 1,
    start: datetime | None = None,
    daily_milk_litres: float | None = None,
    initial_milk: float = 0,
    initial_temp: float = 4,
    store_soc: float = 75,
    battery_soc: float = 80,
) -> LoadedScenario:
    if daily_milk_litres is not None:
        site = site.model_copy(update={"daily_milk_litres": daily_milk_litres})
    start = start or datetime(2026, 6, 1, 0, 0, tzinfo=ZoneInfo("Africa/Nairobi"))
    from shared.config import Scenario

    scenario = Scenario.model_validate(
        {
            "name": "test",
            "description": "unit test scenario",
            "seed": seed,
            "start": start,
            "duration_hours": hours,
            "step_seconds": 60,
            "timezone": "Africa/Nairobi",
            "weather": {"cloudiness": cloudiness},
            "initial_state": {
                "tank_temp_c": initial_temp,
                "milk_volume_l": initial_milk,
                "store_soc_pct": store_soc,
                "battery_soc_pct": battery_soc,
            },
            "faults": faults or [],
            "site": site.model_dump(),
        }
    )
    config = apply_site_equipment(defaults, scenario.site)
    return LoadedScenario(scenario=scenario, config=config)


@pytest.fixture
def sunny_loaded():
    return load_scenario(scenarios_dir() / "sunny_day.yaml")
