"""Local SQLite log. Inserts are idempotent on ``site_id + timestamp + type``.

A second copy of the same payload is a duplicate and is ignored. A different
payload for the same key is a conflict: we do not silently overwrite history.
New rows are also copied to the sync outbox for the cloud agent.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from shared.enums import RecordType
from shared.keys import idempotency_key
from shared.models import (
    AuditRow,
    Base,
    ControlActionRow,
    EventRow,
    ReadingRow,
    SyncOutboxRow,
)
from shared.schemas import ControlAction, Event, Reading
from shared.storage import action_to_row, event_to_row, reading_to_row


class Conflict(Exception):
    """The same key arrived again with a different payload."""


class EdgeStore:
    def __init__(self, url: str = "sqlite:///:memory:") -> None:
        self.engine = create_engine(url)
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)

    def close(self) -> None:
        self.session.close()

    def add_reading(self, reading: Reading, record_type: str = RecordType.READING.value) -> str:
        row = reading_to_row(reading, record_type)
        return self._put(row, ReadingRow, _reading_signature, reading.model_dump(mode="json"))

    def add_action(self, action: ControlAction) -> str:
        row = action_to_row(action)
        payload = action.model_dump(mode="json")
        return self._put(row, ControlActionRow, _action_signature, payload)

    def add_event(self, event: Event) -> str:
        row = event_to_row(event)
        return self._put(row, EventRow, _event_signature, event.model_dump(mode="json"))

    def add_audit(self, site_id: str, timestamp: datetime, actor: str, action: str, reason: str, details: dict) -> str:
        record_type = f"audit:{action}:{actor}"
        key = idempotency_key(site_id, timestamp, record_type)
        row = AuditRow(
            idempotency_key=key,
            site_id=site_id,
            timestamp=timestamp,
            actor=actor,
            action=action,
            reason=reason,
            details=details,
        )
        return self._put(row, AuditRow, _audit_signature, {"actor": actor, "action": action, "reason": reason, "details": details})

    def reading_count(self) -> int:
        return len(self.session.scalars(select(ReadingRow)).all())

    def pending_outbox(self, limit: int) -> list[SyncOutboxRow]:
        return list(
            self.session.scalars(
                select(SyncOutboxRow)
                .where(SyncOutboxRow.synced_at.is_(None), SyncOutboxRow.attempts < 10)
                .order_by(SyncOutboxRow.created_at)
                .limit(limit)
            ).all()
        )

    def mark_synced(self, keys: list[str], when: datetime) -> None:
        if not keys:
            return
        rows = self.session.scalars(select(SyncOutboxRow).where(SyncOutboxRow.idempotency_key.in_(keys))).all()
        for row in rows:
            row.synced_at = when
        self.session.commit()

    def note_attempt(self, keys: list[str]) -> None:
        if not keys:
            return
        rows = self.session.scalars(select(SyncOutboxRow).where(SyncOutboxRow.idempotency_key.in_(keys))).all()
        for row in rows:
            row.attempts += 1
        self.session.commit()

    def purge_raw(self, older_than: datetime) -> int:
        """Drop raw samples past the retention window. One-minute rollups stay."""
        rows = self.session.scalars(
            select(ReadingRow).where(
                ReadingRow.record_type == RecordType.READING_RAW.value,
                ReadingRow.timestamp < older_than,
            )
        ).all()
        for row in rows:
            self.session.delete(row)
        self.session.commit()
        return len(rows)

    def _put(self, row, model, signature, payload: dict) -> str:  # noqa: ANN001
        existing = self.session.get(model, row.idempotency_key)
        if existing is not None:
            if signature(existing) == signature(row):
                return "duplicate"
            raise Conflict(row.idempotency_key)
        self.session.add(row)
        self.session.add(
            SyncOutboxRow(
                idempotency_key=row.idempotency_key,
                site_id=row.site_id,
                record_type=row.record_type,
                payload=payload,
                created_at=datetime.now(timezone.utc),
                synced_at=None,
                attempts=0,
            )
        )
        self.session.commit()
        return "inserted"


def _reading_signature(row: ReadingRow) -> tuple:
    data = {name: getattr(row, name) for name in Reading.model_fields}
    data["fault_flags"] = tuple(data["fault_flags"] or [])
    return tuple(sorted(data.items()))


def _action_signature(row: ControlActionRow) -> tuple:
    return (row.mode, row.action, row.reason, _freeze(row.command), _freeze(row.inputs_snapshot))


def _event_signature(row: EventRow) -> tuple:
    return (row.event_type, row.severity, row.message, _freeze(row.details))


def _audit_signature(row: AuditRow) -> tuple:
    return (row.actor, row.action, row.reason, _freeze(row.details))


def _freeze(value):
    if isinstance(value, dict):
        return tuple(sorted((key, _freeze(item)) for key, item in value.items()))
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def retention_cutoff(now: datetime, days: int) -> datetime:
    return now - timedelta(days=days)
