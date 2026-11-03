"""Map validated messages onto database rows."""

from __future__ import annotations

from shared.enums import RecordType
from shared.keys import alert_record_type, event_record_type, idempotency_key
from shared.models import (
    AlertRow,
    ControlActionRow,
    EventRow,
    ReadingRow,
)
from shared.schemas import Alert, ControlAction, Event, Reading


def reading_to_row(reading: Reading, record_type: str = RecordType.READING.value) -> ReadingRow:
    payload = reading.model_dump()
    return ReadingRow(
        idempotency_key=idempotency_key(reading.site_id, reading.timestamp, record_type),
        record_type=record_type,
        **payload,
    )


def reading_from_row(row: ReadingRow) -> Reading:
    payload = {name: getattr(row, name) for name in Reading.model_fields}
    return Reading.model_validate(payload)


def action_to_row(action: ControlAction) -> ControlActionRow:
    record_type = RecordType.CONTROL_ACTION.value
    return ControlActionRow(
        idempotency_key=idempotency_key(action.site_id, action.timestamp, record_type),
        site_id=action.site_id,
        timestamp=action.timestamp,
        record_type=record_type,
        mode=action.mode.value,
        action=action.action.value,
        reason=action.reason,
        inputs_snapshot=action.inputs_snapshot,
        command=action.command.model_dump(mode="json"),
    )


def event_to_row(event: Event) -> EventRow:
    record_type = event_record_type(event.event_type.value)
    return EventRow(
        idempotency_key=idempotency_key(event.site_id, event.timestamp, record_type),
        site_id=event.site_id,
        timestamp=event.timestamp,
        record_type=record_type,
        event_type=event.event_type.value,
        severity=event.severity.value,
        message=event.message,
        details=event.details,
    )


def alert_to_row(alert: Alert) -> AlertRow:
    record_type = alert_record_type(alert.channel.value, alert.dedup_key)
    return AlertRow(
        idempotency_key=idempotency_key(alert.site_id, alert.timestamp, record_type),
        site_id=alert.site_id,
        timestamp=alert.timestamp,
        record_type=record_type,
        event_type=alert.event_type.value,
        channel=alert.channel.value,
        recipient=alert.recipient,
        status=alert.status.value,
        message=alert.message,
        dedup_key=alert.dedup_key,
        event_key=alert.event_key,
    )
