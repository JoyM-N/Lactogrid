"""SQLAlchemy models shared by the edge (SQLite) and the cloud (Postgres).

Types stay dialect-neutral. There is no foreign key from readings to sites so a
replayed batch can land in either order. The API still checks that an ingest
key belongs to the site it names.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, Float, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON, TypeDecorator


class UtcDateTime(TypeDecorator):
    """Store timezone-aware datetimes and always return UTC."""

    impl = String(40)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect) -> str | None:  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError("timestamp must be timezone-aware")
        return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def process_result_value(self, value: str | None, dialect) -> datetime | None:  # noqa: ANN001
        if value is None:
            return None
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return parsed


class Base(DeclarativeBase):
    pass


class SiteRow(Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    location: Mapped[str] = mapped_column(String(200))
    timezone: Mapped[str] = mapped_column(String(64))
    cooperative_id: Mapped[str] = mapped_column(String(64), index=True)
    solar_kwp: Mapped[float] = mapped_column(Float)
    compressor_kw_electric: Mapped[float] = mapped_column(Float)
    cold_store_kwh_thermal: Mapped[float] = mapped_column(Float)
    battery_kwh: Mapped[float] = mapped_column(Float)
    tank_litres: Mapped[float] = mapped_column(Float)
    daily_milk_litres: Mapped[float] = mapped_column(Float)
    config_version: Mapped[int] = mapped_column(Integer)
    grid_connected: Mapped[bool] = mapped_column(Boolean, default=True)
    diesel_available: Mapped[bool] = mapped_column(Boolean, default=True)
    config_json: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(UtcDateTime)


class ReadingRow(Base):
    __tablename__ = "readings"
    __table_args__ = (
        UniqueConstraint("site_id", "timestamp", "record_type", name="uq_reading_site_ts_type"),
        Index("ix_readings_site_ts", "site_id", "timestamp"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime)
    record_type: Mapped[str] = mapped_column(String(64))
    tank_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    store_temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    store_soc_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    store_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    ambient_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    solar_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    solar_used_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    compressor_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    compressor_from_solar_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    compressor_from_backup_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    load_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    aux_from_solar_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    aux_from_battery_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    aux_from_backup_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_soc_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_kwh: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    grid_available: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    backup_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    backup_from_grid_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    backup_from_diesel_kw: Mapped[float | None] = mapped_column(Float, nullable=True)
    store_draw_kw_thermal: Mapped[float | None] = mapped_column(Float, nullable=True)
    store_charge_kw_thermal: Mapped[float | None] = mapped_column(Float, nullable=True)
    milk_volume_l: Mapped[float | None] = mapped_column(Float, nullable=True)
    door_open: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    fault_flags: Mapped[list] = mapped_column(JSON)


class ControlActionRow(Base):
    __tablename__ = "control_actions"
    __table_args__ = (
        UniqueConstraint("site_id", "timestamp", "record_type", name="uq_action_site_ts_type"),
        Index("ix_actions_site_ts", "site_id", "timestamp"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime)
    record_type: Mapped[str] = mapped_column(String(64), default="control_action")
    mode: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(2000))
    inputs_snapshot: Mapped[dict] = mapped_column(JSON)
    command: Mapped[dict] = mapped_column(JSON)


class EventRow(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("site_id", "timestamp", "record_type", name="uq_event_site_ts_type"),
        Index("ix_events_site_ts", "site_id", "timestamp"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime)
    record_type: Mapped[str] = mapped_column(String(128))
    event_type: Mapped[str] = mapped_column(String(64))
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(String(2000))
    details: Mapped[dict] = mapped_column(JSON)


class AlertRow(Base):
    __tablename__ = "alerts"
    __table_args__ = (
        UniqueConstraint("site_id", "timestamp", "record_type", name="uq_alert_site_ts_type"),
        Index("ix_alerts_site_ts", "site_id", "timestamp"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime)
    record_type: Mapped[str] = mapped_column(String(200))
    event_type: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(32))
    recipient: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(String(2000))
    dedup_key: Mapped[str] = mapped_column(String(200))
    event_key: Mapped[str | None] = mapped_column(String(300), nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class MilkBatchRow(Base):
    __tablename__ = "milk_batches"
    __table_args__ = (
        UniqueConstraint("site_id", "delivery_start", name="uq_batch_site_start"),
        Index("ix_batches_site", "site_id"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64))
    delivery_start: Mapped[datetime] = mapped_column(UtcDateTime)
    litres: Mapped[float] = mapped_column(Float)
    arrival_temp_c: Mapped[float] = mapped_column(Float)
    time_to_4c_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    reached_4c_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    collection_time: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)


class SLAPeriodRow(Base):
    __tablename__ = "sla_periods"
    __table_args__ = (
        UniqueConstraint("site_id", "period_start", "period_end", name="uq_sla_site_period"),
    )

    idempotency_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64), index=True)
    period_start: Mapped[datetime] = mapped_column(UtcDateTime)
    period_end: Mapped[datetime] = mapped_column(UtcDateTime)
    milk_safe_uptime_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    data_completeness_pct: Mapped[float] = mapped_column(Float)
    litres_cooled: Mapped[float] = mapped_column(Float)
    solar_kwh_used: Mapped[float] = mapped_column(Float)
    battery_kwh_discharged: Mapped[float] = mapped_column(Float)
    backup_kwh: Mapped[float] = mapped_column(Float)
    solar_cooling_share_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    backup_hours: Mapped[float] = mapped_column(Float)
    backup_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_currency: Mapped[str] = mapped_column(String(16))
    prices_are_placeholders: Mapped[bool] = mapped_column(Boolean)
    discount_pct: Mapped[float] = mapped_column(Float)
    uptime_threshold_pct: Mapped[float] = mapped_column(Float)
    incidents: Mapped[list] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(String(4000))
    sample_count: Mapped[int] = mapped_column(Integer)
    expected_samples: Mapped[int] = mapped_column(Integer)


class AuditRow(Base):
    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_site_ts", "site_id", "timestamp"),)

    idempotency_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64))
    timestamp: Mapped[datetime] = mapped_column(UtcDateTime)
    actor: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(64))
    reason: Mapped[str] = mapped_column(String(2000))
    details: Mapped[dict] = mapped_column(JSON)


class SyncOutboxRow(Base):
    """Edge only in spirit. Created on both databases so one metadata set migrates."""

    __tablename__ = "sync_outbox"
    __table_args__ = (Index("ix_outbox_unsynced", "synced_at"),)

    idempotency_key: Mapped[str] = mapped_column(String(300), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64), index=True)
    record_type: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    synced_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(32))
    cooperative_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)


class ApiKeyRow(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    site_id: Mapped[str] = mapped_column(String(64), index=True)
    key_hash: Mapped[str] = mapped_column(String(128))
    label: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UtcDateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime, nullable=True)
