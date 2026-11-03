"""HTTP API. OpenAPI is served at /docs by FastAPI."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shared.config import load_root_config
from shared.enums import Role
from shared.keys import normalise_ts
from shared.models import AlertRow, ControlActionRow, EventRow, ReadingRow, SiteRow, UserRow
from shared.schemas import ControlAction, Event, Reading, Site
from cloud.auth import (
    can_read_site,
    get_session,
    hash_api_key,
    hash_password,
    issue_token,
    require_admin,
    require_api_key,
    require_user,
    require_writer,
    verify_password,
)
from cloud.db import make_engine
from cloud.guarantee import build_guarantee, render_csv, render_pdf
from cloud.repository import (
    IngestTally,
    _note,
    alert_or_404,
    list_readings,
    site_or_404,
    upsert_action,
    upsert_event,
    upsert_reading,
    write_audit,
)
from shared.models import ApiKeyRow


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReadingEnvelope(Strict):
    record_type: str = "reading"
    reading: Reading


class ReadingBatch(Strict):
    readings: list[ReadingEnvelope]


class EventBatch(Strict):
    events: list[Event]


class ActionBatch(Strict):
    actions: list[ControlAction]


class LoginBody(Strict):
    name: str
    password: str


class ConfigBody(Strict):
    reason: str = Field(min_length=1)
    config: dict


class SiteBody(Strict):
    site: Site
    api_key: str = Field(min_length=8)


def build_app() -> FastAPI:
    """Process entry point. Reads the database URL and signing secret from the environment."""

    database_url = os.environ.get("LACTOGRID_DATABASE_URL", "sqlite:///./data/demo.sqlite")
    secret = os.environ.get("LACTOGRID_SECRET", "dev-only-change-me")
    return create_app(database_url, secret)


def create_app(database_url: str = "sqlite://", secret: str = "dev-only-change-me") -> FastAPI:
    application = FastAPI(title="LactoGrid Cloud", version="0.1.0")
    application.state.engine = make_engine(database_url)
    application.state.secret = secret

    @application.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @application.post("/login")
    def login(body: LoginBody, session: Session = Depends(get_session)) -> dict:
        name = body.name.strip()
        user = session.scalar(select(UserRow).where(func.lower(UserRow.name) == name.lower()))
        if user is None or user.disabled or not verify_password(body.password.strip(), user.password_hash):
            raise HTTPException(status_code=401, detail="invalid login")
        return {
            "token": issue_token(user.id, user.role, application.state.secret),
            "role": user.role,
            "name": user.name,
        }

    @application.post("/ingest/readings")
    def ingest_readings(
        body: ReadingBatch,
        key: ApiKeyRow = Depends(require_api_key),
        session: Session = Depends(get_session),
    ) -> dict:
        tally = IngestTally()
        for item in body.readings:
            _check_site(item.reading.site_id, key.site_id)
            outcome, record_key = upsert_reading(session, item.reading, item.record_type)
            _note(tally, outcome, record_key)
        return tally.as_dict()

    @application.post("/ingest/events")
    def ingest_events(
        body: EventBatch,
        key: ApiKeyRow = Depends(require_api_key),
        session: Session = Depends(get_session),
    ) -> dict:
        tally = IngestTally()
        for event in body.events:
            _check_site(event.site_id, key.site_id)
            outcome, record_key = upsert_event(session, event)
            _note(tally, outcome, record_key)
        return tally.as_dict()

    @application.post("/ingest/actions")
    def ingest_actions(
        body: ActionBatch,
        key: ApiKeyRow = Depends(require_api_key),
        session: Session = Depends(get_session),
    ) -> dict:
        tally = IngestTally()
        for action in body.actions:
            _check_site(action.site_id, key.site_id)
            outcome, record_key = upsert_action(session, action)
            _note(tally, outcome, record_key)
        return tally.as_dict()

    @application.get("/sites")
    def list_sites(user: UserRow = Depends(require_user), session: Session = Depends(get_session)) -> list[dict]:
        rows = session.scalars(select(SiteRow).order_by(SiteRow.name)).all()
        return [_site_dict(row) for row in rows if can_read_site(user, row.cooperative_id)]

    @application.post("/sites", status_code=201)
    def create_site(
        body: SiteBody,
        user: UserRow = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict:
        require_admin(user)
        if session.get(SiteRow, body.site.id) is not None:
            raise HTTPException(status_code=409, detail="site already exists")
        session.add(_site_row(body.site))
        session.add(
            ApiKeyRow(
                id=f"key-{body.site.id}",
                site_id=body.site.id,
                key_hash=hash_api_key(body.api_key),
                label="ingest",
                created_at=datetime.now(timezone.utc),
                revoked_at=None,
            )
        )
        write_audit(session, body.site.id, user.name, "site_created", "new site", {"version": body.site.config_version})
        return {"id": body.site.id, "config_version": body.site.config_version}

    @application.get("/sites/{site_id}/status")
    def site_status(
        site_id: str,
        user: UserRow = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict:
        site = _visible_site(session, user, site_id)
        reading = session.scalar(
            select(ReadingRow)
            .where(ReadingRow.site_id == site_id, ReadingRow.record_type == "reading")
            .order_by(ReadingRow.timestamp.desc())
        )
        action = session.scalar(
            select(ControlActionRow).where(ControlActionRow.site_id == site_id).order_by(ControlActionRow.timestamp.desc())
        )
        alerts = session.scalars(
            select(AlertRow).where(AlertRow.site_id == site_id, AlertRow.status.in_(["pending", "sent"]))
        ).all()
        updated = reading.timestamp if reading is not None else None
        age = None
        if updated is not None:
            age = int((datetime.now(timezone.utc) - updated).total_seconds())
        return {
            "site_id": site.id,
            "name": site.name,
            "mode": action.mode if action is not None else None,
            "reason": action.reason if action is not None else None,
            "tank_temp_c": reading.tank_temp_c if reading is not None else None,
            "store_soc_pct": reading.store_soc_pct if reading is not None else None,
            "solar_kw": reading.solar_kw if reading is not None else None,
            "compressor_kw": reading.compressor_kw if reading is not None else None,
            "backup_kw": reading.backup_kw if reading is not None else None,
            "milk_volume_l": reading.milk_volume_l if reading is not None else None,
            "updated_at": updated,
            "age_seconds": age,
            "active_alerts": len(alerts),
        }

    @application.get("/sites/{site_id}/readings")
    def site_readings(
        site_id: str,
        user: UserRow = Depends(require_user),
        session: Session = Depends(get_session),
        start: datetime | None = Query(default=None, alias="from"),
        end: datetime | None = Query(default=None, alias="to"),
        resolution: str = Query(default="1m"),
    ) -> list[dict]:
        _visible_site(session, user, site_id)
        if resolution not in {"1m", "raw"}:
            raise HTTPException(status_code=400, detail="resolution must be 1m or raw")
        start_utc = normalise_ts(start) if start else None
        end_utc = normalise_ts(end) if end else None
        return [reading.model_dump(mode="json") for reading in list_readings(session, site_id, start_utc, end_utc, resolution)]

    @application.get("/sites/{site_id}/actions")
    def site_actions(
        site_id: str,
        user: UserRow = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> list[dict]:
        _visible_site(session, user, site_id)
        rows = session.scalars(
            select(ControlActionRow).where(ControlActionRow.site_id == site_id).order_by(ControlActionRow.timestamp)
        ).all()
        return [
            {
                "timestamp": row.timestamp,
                "mode": row.mode,
                "action": row.action,
                "reason": row.reason,
                "command": row.command,
                "inputs_snapshot": row.inputs_snapshot,
            }
            for row in rows
        ]

    @application.get("/sites/{site_id}/events")
    def site_events(
        site_id: str,
        user: UserRow = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> list[dict]:
        _visible_site(session, user, site_id)
        rows = session.scalars(select(EventRow).where(EventRow.site_id == site_id).order_by(EventRow.timestamp)).all()
        return [
            {
                "timestamp": row.timestamp,
                "event_type": row.event_type,
                "severity": row.severity,
                "message": row.message,
                "details": row.details,
            }
            for row in rows
        ]

    @application.get("/sites/{site_id}/config")
    def get_config(site_id: str, request: Request, session: Session = Depends(get_session)) -> dict:
        """Edge pulls config with its API key. People use a bearer token."""
        site = site_or_404(session, site_id)
        api_key = request.headers.get("x-api-key")
        if api_key:
            row = session.scalar(
                select(ApiKeyRow).where(
                    ApiKeyRow.key_hash == hash_api_key(api_key),
                    ApiKeyRow.revoked_at.is_(None),
                )
            )
            if row is None:
                raise HTTPException(status_code=401, detail="invalid api key")
            if row.site_id != site_id:
                raise HTTPException(status_code=403, detail="api key is for a different site")
            return _config_payload(site)
        authorization = request.headers.get("authorization")
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="missing credentials")
        from cloud.auth import parse_token

        user_id, _role = parse_token(authorization.split(" ", 1)[1], request.app.state.secret)
        user = session.get(UserRow, user_id)
        if user is None or user.disabled:
            raise HTTPException(status_code=401, detail="unknown user")
        if not can_read_site(user, site.cooperative_id):
            raise HTTPException(status_code=403, detail="outside your cooperative")
        return _config_payload(site)

    @application.put("/sites/{site_id}/config")
    def put_config(
        site_id: str,
        body: ConfigBody,
        user: UserRow = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict:
        require_admin(user)
        site = site_or_404(session, site_id)
        site.config_version += 1
        site.config_json = body.config
        site.updated_at = datetime.now(timezone.utc)
        write_audit(
            session,
            site_id,
            user.name,
            "config_change",
            body.reason,
            {"version": site.config_version},
        )
        return {"id": site.id, "config_version": site.config_version}

    @application.get("/sites/{site_id}/alerts")
    def site_alerts(
        site_id: str,
        user: UserRow = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> list[dict]:
        _visible_site(session, user, site_id)
        rows = session.scalars(select(AlertRow).where(AlertRow.site_id == site_id).order_by(AlertRow.timestamp)).all()
        return [_alert_dict(row) for row in rows]

    @application.get("/sites/{site_id}/guarantee")
    def site_guarantee(
        site_id: str,
        user: UserRow = Depends(require_user),
        session: Session = Depends(get_session),
        period: str = Query(default="day"),
    ) -> dict:
        site = _visible_site(session, user, site_id)
        if period not in {"day", "week", "month"}:
            raise HTTPException(status_code=400, detail="period must be day, week, or month")
        report = _guarantee_for(session, site, period)
        payload = report.as_dict()
        payload["site_id"] = site.id
        payload["site_name"] = site.name
        return payload

    @application.get("/sites/{site_id}/guarantee.csv")
    def site_guarantee_csv(
        site_id: str,
        user: UserRow = Depends(require_user),
        session: Session = Depends(get_session),
        period: str = Query(default="month"),
    ) -> Response:
        site = _visible_site(session, user, site_id)
        report = _guarantee_for(session, site, period)
        return Response(
            render_csv(report),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{site.id}-{period}.csv"'},
        )

    @application.get("/sites/{site_id}/guarantee.pdf")
    def site_guarantee_pdf(
        site_id: str,
        user: UserRow = Depends(require_user),
        session: Session = Depends(get_session),
        period: str = Query(default="month"),
    ) -> Response:
        site = _visible_site(session, user, site_id)
        report = _guarantee_for(session, site, period)
        return Response(
            render_pdf(site.name, report),
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{site.id}-{period}.pdf"'},
        )

    @application.post("/alerts/{alert_id}/ack")
    def ack_alert(
        alert_id: str,
        user: UserRow = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> dict:
        require_writer(user)
        alert = alert_or_404(session, alert_id)
        site = site_or_404(session, alert.site_id)
        if not can_read_site(user, site.cooperative_id):
            raise HTTPException(status_code=403, detail="outside your cooperative")
        alert.status = "acked"
        alert.acknowledged_by = user.name
        alert.acknowledged_at = datetime.now(timezone.utc)
        write_audit(session, alert.site_id, user.name, "alert_ack", "acknowledged", {"alert": alert_id})
        return _alert_dict(alert)

    dashboard = Path(__file__).resolve().parents[1] / "dashboard"
    if dashboard.is_dir():
        application.mount("/static", StaticFiles(directory=dashboard), name="static")

        @application.get("/")
        def dashboard_page() -> FileResponse:
            return FileResponse(dashboard / "index.html")

    return application


def _guarantee_for(session: Session, site: SiteRow, period: str):
    if period not in {"day", "week", "month"}:
        raise HTTPException(status_code=400, detail="period must be day, week, or month")
    readings = list_readings(session, site.id, None, None, "1m")
    return build_guarantee(readings, load_root_config(), site.timezone, period)


def _alert_dict(row: AlertRow) -> dict:
    return {
        "id": row.idempotency_key,
        "timestamp": row.timestamp,
        "event_type": row.event_type,
        "severity": "critical" if row.event_type == "temp_limit_exceeded" else "warning",
        "status": row.status,
        "message": row.message,
        "recipient": row.recipient,
        "acknowledged_by": row.acknowledged_by,
        "acknowledged_at": row.acknowledged_at,
    }


def _config_payload(site: SiteRow) -> dict:
    return {
        "id": site.id,
        "config_version": site.config_version,
        "config": site.config_json,
        "updated_at": site.updated_at,
    }


def _check_site(body_site: str, key_site: str) -> None:
    if body_site != key_site:
        raise HTTPException(status_code=403, detail="api key is for a different site")


def _visible_site(session: Session, user: UserRow, site_id: str) -> SiteRow:
    site = site_or_404(session, site_id)
    if not can_read_site(user, site.cooperative_id):
        raise HTTPException(status_code=403, detail="outside your cooperative")
    return site


def _site_dict(row: SiteRow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "location": row.location,
        "timezone": row.timezone,
        "cooperative_id": row.cooperative_id,
        "solar_kwp": row.solar_kwp,
        "compressor_kw_electric": row.compressor_kw_electric,
        "cold_store_kwh_thermal": row.cold_store_kwh_thermal,
        "battery_kwh": row.battery_kwh,
        "tank_litres": row.tank_litres,
        "daily_milk_litres": row.daily_milk_litres,
        "config_version": row.config_version,
    }


def _site_row(site: Site) -> SiteRow:
    return SiteRow(
        id=site.id,
        name=site.name,
        location=site.location,
        timezone=site.timezone,
        cooperative_id=site.cooperative_id,
        solar_kwp=site.solar_kwp,
        compressor_kw_electric=site.compressor_kw_electric,
        cold_store_kwh_thermal=site.cold_store_kwh_thermal,
        battery_kwh=site.battery_kwh,
        tank_litres=site.tank_litres,
        daily_milk_litres=site.daily_milk_litres,
        config_version=site.config_version,
        grid_connected=site.grid_connected,
        diesel_available=site.diesel_available,
        config_json=site.model_dump(mode="json"),
        updated_at=datetime.now(timezone.utc),
    )


def add_user(session: Session, user_id: str, name: str, role: Role, password: str, cooperative_id: str | None) -> None:
    session.add(
        UserRow(
            id=user_id,
            name=name,
            role=role.value,
            cooperative_id=cooperative_id,
            password_hash=hash_password(password),
            disabled=False,
        )
    )
    session.commit()
