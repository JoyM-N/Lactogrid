"""Idempotency keys for store-and-forward sync.

The brief keys records by ``site_id + timestamp + type``. The type includes
the event or alert discriminator, so two different events at the same second
are both kept, and a retried copy of the same event collapses to one row.
"""

from __future__ import annotations

from datetime import datetime, timezone


def normalise_ts(timestamp: datetime) -> datetime:
    """UTC, truncated to the second. Naive datetimes are rejected."""
    if timestamp.tzinfo is None or timestamp.tzinfo.utcoffset(timestamp) is None:
        raise ValueError("timestamp must be timezone-aware")
    return timestamp.astimezone(timezone.utc).replace(microsecond=0)


def idempotency_key(site_id: str, timestamp: datetime, record_type: str) -> str:
    """Stable key. ``record_type`` must not contain ``|``."""
    if not site_id or "|" in site_id:
        raise ValueError("site_id must be a non-empty string without '|'")
    if not record_type or "|" in record_type:
        raise ValueError("record_type must be a non-empty string without '|'")
    stamp = normalise_ts(timestamp).strftime("%Y-%m-%dT%H:%M:%SZ")
    return f"{site_id}|{stamp}|{record_type}"


def event_record_type(event_type: str) -> str:
    return f"event:{event_type}"


def alert_record_type(channel: str, dedup_key: str) -> str:
    safe = dedup_key.replace("|", "/")
    return f"alert:{channel}:{safe}"
