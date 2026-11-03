"""Load YAML configuration. Every tunable number lives here, not in Python literals."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.paths import defaults_path, repo_root
from shared.schemas import FaultSpec, Site


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PhysicsConfig(StrictModel):
    milk_specific_heat_wh_per_l_c: float = Field(gt=0)
    tank_shell_equivalent_l: float = Field(ge=0)
    milk_arrival_temp_c: float
    heat_leak_kw_per_c: float = Field(ge=0)
    collection_hour_local: int = Field(ge=0, le=23)
    collection_minute_local: int = Field(ge=0, le=59)
    morning_milk_fraction: float = Field(ge=0, le=1)
    morning_start_hour: float = Field(ge=0, lt=24)
    morning_end_hour: float = Field(ge=0, le=24)
    evening_milk_fraction: float = Field(ge=0, le=1)
    evening_start_hour: float = Field(ge=0, lt=24)
    evening_end_hour: float = Field(ge=0, le=24)
    solar_sunrise_hour: float = Field(ge=0, lt=24)
    solar_sunset_hour: float = Field(gt=0, le=24)
    solar_peak_factor: float = Field(gt=0)
    solar_sin_exponent: float = Field(gt=0)
    ambient_base_c: float
    ambient_amplitude_c: float
    ambient_phase_hour: float
    ambient_period_hours: float = Field(gt=0)
    cloud_noise_amplitudes: list[float]
    cloud_noise_period_hours: list[float]

    @model_validator(mode="after")
    def _check(self) -> PhysicsConfig:
        if abs(self.morning_milk_fraction + self.evening_milk_fraction - 1.0) > 1e-6:
            raise ValueError("morning and evening milk fractions must sum to 1")
        if self.morning_end_hour <= self.morning_start_hour:
            raise ValueError("morning window must have positive length")
        if self.evening_end_hour <= self.evening_start_hour:
            raise ValueError("evening window must have positive length")
        if self.solar_sunset_hour <= self.solar_sunrise_hour:
            raise ValueError("sunset must be after sunrise")
        if len(self.cloud_noise_amplitudes) != len(self.cloud_noise_period_hours):
            raise ValueError("cloud noise amplitudes and periods must be the same length")
        return self


class CompressorConfig(StrictModel):
    max_kw_electric: float = Field(gt=0)
    cop_floor: float = Field(gt=0)
    cop_intercept: float
    cop_slope_per_c: float
    cop_reference_ambient_c: float


class ColdStoreConfig(StrictModel):
    max_draw_kw_thermal: float = Field(gt=0)
    charge_efficiency: float = Field(gt=0, le=1)


class TankConfig(StrictModel):
    setpoint_c: float
    hysteresis_c: float = Field(ge=0)
    max_heat_removal_kw_thermal: float = Field(gt=0)
    low_cutout_c: float
    backup_start_temp_c: float
    backup_clear_temp_c: float


class BatteryConfig(StrictModel):
    capacity_kwh: float = Field(ge=0)
    charge_efficiency: float = Field(gt=0, le=1)
    discharge_efficiency: float = Field(gt=0, le=1)
    max_charge_kw: float = Field(ge=0)
    max_discharge_kw: float = Field(ge=0)
    min_soc_pct: float = Field(ge=0, lt=100)


class AuxConfig(StrictModel):
    load_kw: float = Field(ge=0)
    backup_allowed: bool


class ControllerConfig(StrictModel):
    interval_minutes: float = Field(gt=0)
    store_target_soc_pct: float = Field(gt=0, le=100)
    store_target_hysteresis_pct: float = Field(ge=0)
    backup_store_max_kwh: float = Field(ge=0)
    backup_store_clear_kwh: float = Field(ge=0)
    sensor_min_plausible_c: float
    sensor_max_plausible_c: float
    stuck_window_minutes: float = Field(gt=0)
    stuck_resolution_c: float = Field(gt=0)
    stuck_expected_move_c: float = Field(ge=0)
    safe_mode_compressor_fraction: float = Field(gt=0, le=1)
    manual_override_default_minutes: float = Field(gt=0)
    forecast_store_base_soc_pct: float
    forecast_store_extra_soc_pct: float
    forecast_cloud_gain: float
    forecast_aux_trim_store_soc_pct: float
    forecast_aux_trim_fraction: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _bounds(self) -> ControllerConfig:
        if self.sensor_max_plausible_c <= self.sensor_min_plausible_c:
            raise ValueError("sensor plausible range is empty")
        return self


class SensorConfig(StrictModel):
    raw_retention_days: int = Field(ge=1)
    rollup_minutes: int = Field(ge=1)


class DiscountTier(StrictModel):
    min_uptime_pct: float = Field(ge=0, le=100)
    discount_pct: float = Field(ge=0, le=100)


class SlaConfig(StrictModel):
    milk_safe_temp_c: float
    cool_target_temp_c: float
    uptime_threshold_pct: float = Field(ge=0, le=100)
    discount_tiers: list[DiscountTier]
    prices_are_placeholders: bool
    grid_price_per_kwh: float | None = Field(default=None, ge=0)
    diesel_price_per_kwh: float | None = Field(default=None, ge=0)
    price_currency: str

    @model_validator(mode="after")
    def _tiers(self) -> SlaConfig:
        if not self.discount_tiers:
            raise ValueError("discount tiers are required")
        if not any(tier.min_uptime_pct == 0 for tier in self.discount_tiers):
            raise ValueError("discount tiers need a floor at 0% uptime")
        return self


class AlertConfig(StrictModel):
    tank_over_temp_c: float
    tank_over_temp_minutes: float = Field(gt=0)
    edge_offline_minutes: float = Field(gt=0)
    rate_limit_minutes: float = Field(gt=0)
    store_empty_kwh: float = Field(ge=0)
    recipients: list[str]


class SyncConfig(StrictModel):
    batch_size: int = Field(gt=0)
    retry_seconds: float = Field(gt=0)


class WatchdogConfig(StrictModel):
    timeout_seconds: float = Field(gt=0)


class RootConfig(StrictModel):
    physics: PhysicsConfig
    compressor: CompressorConfig
    cold_store: ColdStoreConfig
    tank: TankConfig
    battery: BatteryConfig
    aux: AuxConfig
    controller: ControllerConfig
    sensors: SensorConfig
    sla: SlaConfig
    alerts: AlertConfig
    sync: SyncConfig
    watchdog: WatchdogConfig


class WeatherConfig(StrictModel):
    """0 is a clear day, 1 is a heavily overcast day, before the 0.85 peak factor."""

    cloudiness: float = Field(ge=0, le=1)


class InitialState(StrictModel):
    tank_temp_c: float
    milk_volume_l: float = Field(ge=0)
    store_soc_pct: float = Field(ge=0, le=100)
    battery_soc_pct: float = Field(ge=0, le=100)


class ScenarioExpect(StrictModel):
    """Acceptance hints. Numbers stay in the scenario file, not in the test source."""

    max_tank_temp_with_milk_c: float | None = None
    max_backup_kwh_per_day: float | None = None
    min_backup_kwh: float | None = None
    min_samples_above_safe_c: int | None = None
    safe_temp_c: float | None = None
    link_down_hours: float | None = None
    requires_controller: bool = False
    expects_safe_mode: bool = False


class Scenario(StrictModel):
    name: str
    description: str
    seed: int
    start: datetime
    duration_hours: float = Field(gt=0)
    step_seconds: int = Field(ge=1, le=60)
    timezone: str
    weather: WeatherConfig
    initial_state: InitialState
    faults: list[FaultSpec] = Field(default_factory=list)
    physics_overrides: dict[str, float] = Field(default_factory=dict)
    site_file: str | None = None
    site_overrides: dict[str, Any] = Field(default_factory=dict)
    site: Site
    expect: ScenarioExpect = Field(default_factory=ScenarioExpect)

    @model_validator(mode="before")
    @classmethod
    def _assemble_site(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        if isinstance(data.get("site"), Site):
            return data
        data = dict(data)
        site: dict[str, Any] = dict(data.get("site") or {})
        site_file = data.get("site_file")
        if site_file:
            loaded = load_yaml(repo_root() / site_file)
            site = {**loaded, **site}
        overrides = data.get("site_overrides") or {}
        if overrides:
            site = {**site, **overrides}
        if site:
            data["site"] = site
        return data

    @field_validator("start")
    @classmethod
    def _start(cls, value: datetime) -> datetime:
        from shared.keys import normalise_ts

        return normalise_ts(value)

    @model_validator(mode="after")
    def _timezone(self) -> Scenario:
        from zoneinfo import ZoneInfo

        ZoneInfo(self.timezone)
        if self.site.timezone != self.timezone:
            raise ValueError("scenario timezone and site timezone must match")
        unknown = set(self.physics_overrides) - set(PhysicsConfig.model_fields)
        if unknown:
            raise ValueError(f"unknown physics overrides: {sorted(unknown)}")
        return self


@dataclass(frozen=True)
class LoadedScenario:
    """A scenario plus the config it should run with. Not a YAML document."""

    scenario: Scenario
    config: RootConfig
    path: Path | None = None


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return data


def load_root_config(path: Path | None = None) -> RootConfig:
    return RootConfig.model_validate(load_yaml(path or defaults_path()))


def apply_physics_overrides(config: RootConfig, overrides: dict[str, float]) -> RootConfig:
    if not overrides:
        return config
    data = config.model_dump()
    data["physics"].update(overrides)
    return RootConfig.model_validate(data)


def apply_site_equipment(config: RootConfig, site: Site) -> RootConfig:
    """Installed compressor and battery ratings come from the site, not the global file."""
    data = config.model_dump()
    data["compressor"]["max_kw_electric"] = site.compressor_kw_electric
    data["battery"]["capacity_kwh"] = site.battery_kwh
    return RootConfig.model_validate(data)


def load_scenario(path: Path, defaults: RootConfig | None = None) -> LoadedScenario:
    scenario = Scenario.model_validate(load_yaml(path))
    config = defaults or load_root_config()
    config = apply_physics_overrides(config, scenario.physics_overrides)
    config = apply_site_equipment(config, scenario.site)
    return LoadedScenario(scenario=scenario, config=config, path=path)
