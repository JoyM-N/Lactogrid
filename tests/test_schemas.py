"""Schema and idempotency-key tests."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from shared.enums import EventType
from shared.keys import alert_record_type, event_record_type, idempotency_key
from shared.schemas import FaultSpec, Reading
from shared.enums import FaultType


def test_idempotency_key_is_stable_across_offsets() -> None:
    nairobi = datetime.fromisoformat("2026-06-01T12:00:00+03:00")
    utc = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    assert idempotency_key("demo-site", nairobi, "reading") == idempotency_key(
        "demo-site", utc, "reading"
    )


def test_same_second_different_event_types_do_not_collapse() -> None:
    stamp = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    backup = idempotency_key("demo-site", stamp, event_record_type(EventType.BACKUP_STARTED.value))
    sensor = idempotency_key("demo-site", stamp, event_record_type(EventType.SENSOR_LOST.value))
    assert backup != sensor


def test_retry_of_the_same_event_collapses() -> None:
    stamp = datetime(2026, 6, 1, 9, 0, 0, 400000, tzinfo=timezone.utc)
    later = stamp + timedelta(milliseconds=200)
    kind = event_record_type("backup_started")
    assert idempotency_key("demo-site", stamp, kind) == idempotency_key("demo-site", later, kind)


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Reading(site_id="demo-site", timestamp=datetime(2026, 6, 1, 9, 0))


def test_implausible_temperature_is_still_stored() -> None:
    """Safe mode has to see a bad reading. The schema must not drop it."""
    reading = Reading(
        site_id="demo-site",
        timestamp=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
        tank_temp_c=80,
    )
    assert reading.tank_temp_c == 80


def test_fault_spec_requires_a_known_sensor() -> None:
    stamp = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)
    with pytest.raises(ValidationError):
        FaultSpec(type=FaultType.SENSOR_DROPOUT, start=stamp, end=stamp + timedelta(hours=1))


def test_alert_record_type_keeps_the_channel() -> None:
    assert alert_record_type("sms", "tank-hot") == "alert:sms:tank-hot"
