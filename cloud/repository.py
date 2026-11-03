"""Idempotent writes and the reads the API serves.

Same key and same payload: duplicate, no second row.
Same key and a different payload: conflict, the stored row is left as it was.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from shared.enums import AlertChannelKind, AlertStatus, EventType, RecordType, Severity
from shared.keys import idempotency_key
from shared.models import (
    AlertRow,
    AuditRow,
    ControlActionRow,
    EventRow,
    ReadingRow,
    SiteRow,
)
from shared.schemas import Alert, ControlAction, Event, Reading
from shared.storage import action_to_row, alert_to_row, event_to_row, reading_from_row, reading_to_row
from edge.store import _action_signature, _event_signature, _reading_signature


class IngestTally:
    def __init__(self) -> None:
        self.inserted = 0
        self.duplicates = 0
        self.conflicts: list[str] = []

    def as_dict(self) -> dict:
        return {"inserted": self.inserted, "duplicates": self.duplicates, "conflicts": self.conflicts}


def _note(tally: IngestTally, outcome: str, key: str) -> None:
    if outcome == "inserted":
        tally.inserted += 1
    elif outcome == "duplicate":
        tally.duplicates += 1
    else:
        tally.conflicts.append(key)


def _put(session: Session, row, model, signature) -> str:  # noqa: ANN001
    existing = session.get(model, row.idempotency_key)
    if existing is None:
        session.add(row)
        return "inserted"
    if signature(existing) == signature(row):
        return "duplicate"
    return "conflict"


def upsert_reading(session: Session, reading: Reading, record_type: str = RecordType.READING.value) -> tuple[str, str]:
    row = reading_to_row(reading, record_type)
    return _put(session, row, ReadingRow, _reading_signature), row.idempotency_key


def upsert_action(session: Session, action: ControlAction) -> tuple[str, str]:
    row = action_to_row(action)
    return _put(session, row, ControlActionRow, _action_signature), row.idempotency_key


def upsert_event(session: Session, event: Event) -> tuple[str, str]:
    row = event_to_row(event)
    outcome = _put(session, row, EventRow, _event_signature)
    if outcome == "inserted":
        _open_alarm(session, event, row.idempotency_key)
    return outcome, row.idempotency_key


_ALARM_TYPES = {
    EventType.TEMP_LIMIT_EXCEEDED.value,
    EventType.BACKUP_STARTED.value,
    EventType.SAFE_MODE_ENTERED.value,
    EventType.SENSOR_STUCK.value,
    EventType.SENSOR_LOST.value,
    EventType.COMPRESSOR_FAULT.value,
    EventType.STORE_EMPTY.value,
    EventType.COMMS_LOST.value,
}


def _open_alarm(session: Session, event: Event, event_key: str) -> None:
    """One console alarm per event type inside the configured quiet period.

    A text message waits on a provider. The record is what a manager acknowledges.
    """
    if event.severity == Severity.INFO or event.event_type.value not in _ALARM_TYPES:
        return
    from shared.config import load_root_config

    alerts = load_root_config().alerts
    window_start = event.timestamp - timedelta(minutes=alerts.rate_limit_minutes)
    existing = session.scalar(
        select(AlertRow).where(
            AlertRow.site_id == event.site_id,
            AlertRow.event_type == event.event_type.value,
            AlertRow.timestamp >= window_start,
            AlertRow.timestamp <= event.timestamp,
        )
    )
    if existing is not None:
        return
    recipient = alerts.recipients[0] if alerts.recipients else "console:site-manager"
    alert = Alert(
        site_id=event.site_id,
        timestamp=event.timestamp,
        event_type=event.event_type,
        channel=AlertChannelKind.CONSOLE,
        recipient=recipient,
        status=AlertStatus.SENT,
        message=event.message,
        dedup_key=f"{event.event_type.value}:{event.timestamp.strftime('%Y%m%dT%H%M%S')}",
        event_key=event_key,
    )
    session.add(alert_to_row(alert))


def list_readings(
    session: Session,
    site_id: str,
    start: datetime | None,
    end: datetime | None,
    resolution: str,
) -> list[Reading]:
    record_type = RecordType.READING_RAW.value if resolution == "raw" else RecordType.READING.value
    stmt = select(ReadingRow).where(ReadingRow.site_id == site_id, ReadingRow.record_type == record_type)
    if start is not None:
        stmt = stmt.where(ReadingRow.timestamp >= start)
    if end is not None:
        stmt = stmt.where(ReadingRow.timestamp < end)
    stmt = stmt.order_by(ReadingRow.timestamp)
    return [reading_from_row(row) for row in session.scalars(stmt)]


def write_audit(
    session: Session,
    site_id: str,
    actor: str,
    action: str,
    reason: str,
    details: dict,
    when: datetime | None = None,
) -> None:
    when = when or datetime.now(timezone.utc)
    record_type = f"audit:{action}:{when.strftime('%Y%m%dT%H%M%S')}"
    session.add(
        AuditRow(
            idempotency_key=idempotency_key(site_id, when, record_type),
            site_id=site_id,
            timestamp=when,
            actor=actor,
            action=action,
            reason=reason,
            details=details,
        )
    )


def site_or_404(session: Session, site_id: str) -> SiteRow:
    from fastapi import HTTPException

    row = session.get(SiteRow, site_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown site")
    return row


def alert_or_404(session: Session, alert_id: str) -> AlertRow:
    from fastapi import HTTPException

    row = session.get(AlertRow, alert_id)
    if row is None:
        raise HTTPException(status_code=404, detail="unknown alert")
    return row
