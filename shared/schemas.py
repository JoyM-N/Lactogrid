"""Pydantic message schemas. These are the contract between simulator, edge, and cloud."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from shared.enums import (
    AlertChannelKind,
    AlertStatus,
    CompressorTarget,
    ControlActionKind,
    ControlMode,
    EventType,
    FaultType,
    Role,
    Severity,
)
from shared.keys import normalise_ts

# Fields a sensor fault is allowed to name.
SENSOR_FIELDS = frozenset(
    {
        "tank_temp_c",
        "store_temp_c",
        "store_soc_pct",
        "ambient_c",
        "solar_kw",
        "compressor_kw",
        "load_kw",
        "battery_soc_pct",
        "milk_volume_l",
        "store_kwh",
        "battery_kwh",
    }
)


def _utc(value: datetime) -> datetime:
    return normalise_ts(value)


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Site(APIModel):
    id: str = Field(min_length=1)
    name: str
    location: str
    timezone: str
    solar_kwp: float = Field(ge=0)
    compressor_kw_electric: float = Field(gt=0)
    cold_store_kwh_thermal: float = Field(gt=0)
    battery_kwh: float = Field(ge=0)
    tank_litres: float = Field(gt=0)
    daily_milk_litres: float = Field(ge=0)
    config_version: int = Field(ge=1)
    cooperative_id: str = "demo-coop"
    grid_connected: bool = True
    diesel_available: bool = True


class ActuatorCommand(APIModel):
    """What the controller asks the plant to do. The plant may deliver less."""

    compressor_kw_electric: float = Field(default=0, ge=0)
    compressor_target: CompressorTarget = CompressorTarget.OFF
    # None means "use the equipment cap". A number asks for less, so a small
    # temperature gap does not dump the full 5.5 kW into the tank.
    tank_cooling_kw_thermal: float | None = Field(default=None, ge=0)
    store_draw_kw_thermal: float = Field(default=0, ge=0)
    aux_kw: float = Field(default=0, ge=0)
    allow_backup_compressor: bool = False
    allow_battery_aux: bool = True
    allow_backup_aux: bool = False
    charge_battery_from_surplus: bool = True


class Reading(APIModel):
    """One sensor sample. Extended fields are filled by the simulator for audit.

    ``solar_kw`` is available PV power (the resource), not power after curtailment.
    ``solar_used_kw`` is what the site actually consumed from PV.
    ``battery_kw`` is positive when the battery is discharging.
    """

    site_id: str
    timestamp: datetime
    tank_temp_c: float | None = None
    store_temp_c: float | None = None
    store_soc_pct: float | None = None
    store_kwh: float | None = None
    ambient_c: float | None = None
    solar_kw: float | None = None
    solar_used_kw: float | None = None
    compressor_kw: float | None = None
    compressor_from_solar_kw: float | None = None
    compressor_from_backup_kw: float | None = None
    load_kw: float | None = None
    aux_from_solar_kw: float | None = None
    aux_from_battery_kw: float | None = None
    aux_from_backup_kw: float | None = None
    battery_soc_pct: float | None = None
    battery_kwh: float | None = None
    battery_kw: float | None = None
    grid_available: bool | None = None
    backup_kw: float | None = None
    backup_from_grid_kw: float | None = None
    backup_from_diesel_kw: float | None = None
    store_draw_kw_thermal: float | None = None
    store_charge_kw_thermal: float | None = None
    milk_volume_l: float | None = None
    door_open: bool | None = False
    fault_flags: list[str] = Field(default_factory=list)

    @field_validator("timestamp")
    @classmethod
    def _timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class ControlAction(APIModel):
    site_id: str
    timestamp: datetime
    mode: ControlMode
    action: ControlActionKind
    reason: str
    inputs_snapshot: dict[str, Any] = Field(default_factory=dict)
    command: ActuatorCommand = Field(default_factory=ActuatorCommand)

    @field_validator("timestamp")
    @classmethod
    def _timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class Event(APIModel):
    site_id: str
    timestamp: datetime
    event_type: EventType
    severity: Severity
    message: str
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp")
    @classmethod
    def _timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class Alert(APIModel):
    site_id: str
    timestamp: datetime
    event_type: EventType
    channel: AlertChannelKind
    recipient: str
    status: AlertStatus
    message: str
    dedup_key: str
    event_key: str | None = None

    @field_validator("timestamp")
    @classmethod
    def _timestamp(cls, value: datetime) -> datetime:
        return _utc(value)


class MilkBatch(APIModel):
    site_id: str
    delivery_start: datetime
    litres: float = Field(ge=0)
    arrival_temp_c: float
    time_to_4c_minutes: float | None = None
    reached_4c_at: datetime | None = None
    collection_time: datetime | None = None

    @field_validator("delivery_start", "reached_4c_at", "collection_time")
    @classmethod
    def _timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _utc(value)


class DowntimeIncident(APIModel):
    started_at: datetime
    ended_at: datetime | None
    cause: str
    minutes: float = Field(ge=0)
    max_tank_temp_c: float | None = None

    @field_validator("started_at", "ended_at")
    @classmethod
    def _timestamps(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return _utc(value)


class SLAPeriod(APIModel):
    site_id: str
    period_start: datetime
    period_end: datetime
    milk_safe_uptime_pct: float | None
    data_completeness_pct: float
    litres_cooled: float
    solar_kwh_used: float
    battery_kwh_discharged: float
    backup_kwh: float
    solar_cooling_share_pct: float | None
    backup_hours: float
    backup_cost: float | None
    price_currency: str
    prices_are_placeholders: bool
    discount_pct: float
    uptime_threshold_pct: float
    incidents: list[DowntimeIncident] = Field(default_factory=list)
    summary: str
    sample_count: int = 0
    expected_samples: int = 0

    @field_validator("period_start", "period_end")
    @classmethod
    def _timestamps(cls, value: datetime) -> datetime:
        return _utc(value)


class ManualOverride(APIModel):
    site_id: str
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    started_at: datetime
    expires_at: datetime
    command: ActuatorCommand

    @field_validator("started_at", "expires_at")
    @classmethod
    def _timestamps(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def _expiry_after_start(self) -> ManualOverride:
        if self.expires_at <= self.started_at:
            raise ValueError("override expiry must be after it starts")
        return self


class UserAccount(APIModel):
    id: str
    name: str
    role: Role
    cooperative_id: str | None = None
    disabled: bool = False


class FaultSpec(APIModel):
    type: FaultType
    start: datetime
    end: datetime
    sensor: str | None = None
    value: float | None = None
    factor: float | None = Field(default=None, ge=0, le=1)

    @field_validator("start", "end")
    @classmethod
    def _timestamps(cls, value: datetime) -> datetime:
        return _utc(value)

    @model_validator(mode="after")
    def _consistent(self) -> FaultSpec:
        if self.end <= self.start:
            raise ValueError("fault end must be after start")
        needs_sensor = self.type in {FaultType.SENSOR_DROPOUT, FaultType.STUCK_SENSOR}
        if needs_sensor:
            if not self.sensor:
                raise ValueError(f"{self.type.value} requires a sensor name")
            if self.sensor not in SENSOR_FIELDS:
                raise ValueError(f"unknown sensor '{self.sensor}'")
        if self.type == FaultType.HEAVY_CLOUD and self.factor is None:
            raise ValueError("heavy_cloud requires factor between 0 and 1")
        return self
