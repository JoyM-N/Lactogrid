"""Store-and-forward from the edge outbox to the cloud ingest API.

When the link is down, ``online`` is false and nothing is sent. Rows stay in
the outbox. A later ``sync_once`` posts them. The cloud upserts by idempotency
key, so a retry does not create a second row.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from edge.store import EdgeStore


class SyncAgent:
    def __init__(
        self,
        store: EdgeStore,
        client: httpx.Client,
        api_key: str,
        *,
        batch_size: int = 200,
    ) -> None:
        self.store = store
        self.client = client
        self.api_key = api_key
        self.batch_size = batch_size
        self.online = True

    def sync_once(self) -> dict:
        """Send one batch. Returns counts. Does not raise if the link is down."""
        report = {"skipped": False, "inserted": 0, "duplicates": 0, "conflicts": [], "error": None}
        if not self.online:
            report["skipped"] = True
            return report
        pending = self.store.pending_outbox(self.batch_size)
        readings = [row for row in pending if row.record_type in {"reading", "reading_raw"}]
        actions = [row for row in pending if row.record_type == "control_action"]
        events = [row for row in pending if row.record_type.startswith("event:")]
        try:
            self._send(
                "/ingest/readings",
                {"readings": [{"record_type": row.record_type, "reading": row.payload} for row in readings]},
                readings,
                report,
            )
            self._send(
                "/ingest/events",
                {"events": [row.payload for row in events]},
                events,
                report,
            )
            self._send(
                "/ingest/actions",
                {"actions": [row.payload for row in actions]},
                actions,
                report,
            )
        except httpx.HTTPError as exc:
            report["error"] = type(exc).__name__
        return report

    def drain(self, max_rounds: int = 100) -> dict:
        """Keep syncing until the outbox is idle, the link drops, or the cap is hit."""
        totals = {"inserted": 0, "duplicates": 0, "conflicts": [], "rounds": 0}
        for _ in range(max_rounds):
            if not self.online:
                break
            report = self.sync_once()
            totals["rounds"] += 1
            if report.get("error") or report.get("skipped"):
                break
            totals["inserted"] += report["inserted"]
            totals["duplicates"] += report["duplicates"]
            totals["conflicts"].extend(report["conflicts"])
            if report["inserted"] == 0 and report["duplicates"] == 0 and not report["conflicts"]:
                break
        return totals

    def _send(self, path: str, body: dict, rows: list, report: dict) -> None:
        if not rows:
            return
        response = self.client.post(path, json=body, headers={"X-API-Key": self.api_key})
        if response.status_code >= 500:
            self.store.note_attempt([row.idempotency_key for row in rows])
            report["error"] = f"HTTP {response.status_code}"
            return
        response.raise_for_status()
        data = response.json()
        report["inserted"] += data["inserted"]
        report["duplicates"] += data["duplicates"]
        conflicts = set(data["conflicts"])
        report["conflicts"].extend(data["conflicts"])
        now = datetime.now(timezone.utc)
        ok = [row.idempotency_key for row in rows if row.idempotency_key not in conflicts]
        self.store.mark_synced(ok, now)
        self.store.note_attempt([key for key in conflicts])
