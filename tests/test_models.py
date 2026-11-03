"""The shared schema can be created on SQLite and rejects duplicate keys."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from shared.enums import AlertChannelKind, AlertStatus, ControlActionKind, ControlMode, EventType, Severity
from shared.models import Base, EventRow
from shared.schemas import Alert, ControlAction, Event, Reading
from shared.storage import alert_to_row, action_to_row, event_to_row, reading_from_row, reading_to_row


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine)


def _reading(minute: int = 0, tank: float | None = 4.2) -> Reading:
    return Reading(
        site_id="demo-site",
        timestamp=datetime(2026, 6, 1, 6, minute, tzinfo=timezone.utc),
        tank_temp_c=tank,
        ambient_c=18,
        solar_kw=0,
        milk_volume_l=100,
        store_soc_pct=70,
        battery_soc_pct=80,
        grid_available=True,
        fault_flags=["door"],
    )


def test_reading_round_trip_keeps_utc() -> None:
    session = _session()
    row = reading_to_row(_reading())
    session.add(row)
    session.commit()
    loaded = reading_from_row(session.get(type(row), row.idempotency_key))
    assert loaded.timestamp == datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
    assert loaded.tank_temp_c == pytest.approx(4.2)
    assert loaded.fault_flags == ["door"]


def test_duplicate_reading_key_is_rejected() -> None:
    session = _session()
    session.add(reading_to_row(_reading()))
    session.commit()
    session.add(reading_to_row(_reading()))
    with pytest.raises(IntegrityError):
        session.commit()


def test_two_event_types_at_one_timestamp_both_fit() -> None:
    session = _session()
    stamp = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
    session.add(
        event_to_row(
            Event(
                site_id="demo-site",
                timestamp=stamp,
                event_type=EventType.BACKUP_STARTED,
                severity=Severity.WARNING,
                message="Backup started",
            )
        )
    )
    session.add(
        event_to_row(
            Event(
                site_id="demo-site",
                timestamp=stamp,
                event_type=EventType.SENSOR_LOST,
                severity=Severity.CRITICAL,
                message="Tank sensor lost",
            )
        )
    )
    session.commit()
    assert session.query(EventRow).count() == 2


def test_action_and_alert_rows_insert() -> None:
    session = _session()
    stamp = datetime(2026, 6, 1, 6, 0, tzinfo=timezone.utc)
    session.add(
        action_to_row(
            ControlAction(
                site_id="demo-site",
                timestamp=stamp,
                mode=ControlMode.NORMAL,
                action=ControlActionKind.IDLE,
                reason="Tank is in band and the store is full.",
            )
        )
    )
    session.add(
        alert_to_row(
            Alert(
                site_id="demo-site",
                timestamp=stamp,
                event_type=EventType.BACKUP_STARTED,
                channel=AlertChannelKind.CONSOLE,
                recipient="console:site-manager",
                status=AlertStatus.PENDING,
                message="Backup started",
                dedup_key="backup",
            )
        )
    )
    session.commit()
