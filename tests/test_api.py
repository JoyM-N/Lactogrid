"""Cloud ingest, roles, and offline sync."""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from shared.config import load_scenario
from shared.enums import (
    AlertChannelKind,
    AlertStatus,
    ControlActionKind,
    ControlMode,
    EventType,
    Role,
    Severity,
)
from shared.models import AlertRow
from shared.paths import scenarios_dir
from shared.schemas import ActuatorCommand, Alert, ControlAction, Event, Reading
from shared.storage import alert_to_row
from cloud.app import add_user, create_app
from cloud.db import session_for
from edge.loop import run_closed_loop
from edge.store import EdgeStore
from edge.sync import SyncAgent

UTC = timezone.utc


@pytest.fixture
def api():
    application = create_app(secret="test-secret")
    with session_for(application.state.engine) as session:
        add_user(session, "admin", "Ada", Role.ADMIN, "admin-pass", None)
        add_user(session, "manager", "Baraka", Role.MANAGER, "manager-pass", "demo-coop")
        add_user(session, "viewer", "Chao", Role.VIEWER, "viewer-pass", "demo-coop")
        add_user(session, "other", "Dadi", Role.MANAGER, "other-pass", "other-coop")
    client = TestClient(application)
    return application, client


def test_login_accepts_different_case_and_surrounding_space(api) -> None:
    _, client = api
    response = client.post("/login", json={"name": "  ada ", "password": "admin-pass"})
    assert response.status_code == 200
    assert response.json()["name"] == "Ada"


def _token(client: TestClient, name: str, password: str) -> str:
    response = client.post("/login", json={"name": name, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _demo_site() -> dict:
    return {
        "id": "demo-site",
        "name": "Demo Collection Centre",
        "location": "Kenya (fictional demo site)",
        "timezone": "Africa/Nairobi",
        "cooperative_id": "demo-coop",
        "solar_kwp": 12,
        "compressor_kw_electric": 3,
        "cold_store_kwh_thermal": 60,
        "battery_kwh": 3,
        "tank_litres": 2000,
        "daily_milk_litres": 600,
        "config_version": 1,
        "grid_connected": True,
        "diesel_available": True,
    }


def _reading(minute: int = 0, tank: float = 4.2) -> dict:
    reading = Reading(
        site_id="demo-site",
        timestamp=datetime(2026, 6, 1, 6, 0, tzinfo=UTC) + timedelta(minutes=minute),
        tank_temp_c=tank,
        ambient_c=20,
        solar_kw=1.5,
        milk_volume_l=100,
        store_soc_pct=70,
        store_kwh=42,
        battery_soc_pct=80,
        grid_available=True,
    )
    return {"record_type": "reading", "reading": reading.model_dump(mode="json")}


def test_ingest_is_idempotent_and_rejects_a_changed_payload(api) -> None:
    application, client = api
    admin = _token(client, "Ada", "admin-pass")
    created = client.post("/sites", headers=_auth(admin), json={"site": _demo_site(), "api_key": "site-key-1"})
    assert created.status_code == 201, created.text
    headers = {"X-API-Key": "site-key-1"}
    first = client.post("/ingest/readings", headers=headers, json={"readings": [_reading()]})
    assert first.status_code == 200, first.text
    assert first.json()["inserted"] == 1
    again = client.post("/ingest/readings", headers=headers, json={"readings": [_reading()]})
    assert again.json()["duplicates"] == 1
    changed = _reading()
    changed["reading"]["tank_temp_c"] = 11
    conflict = client.post("/ingest/readings", headers=headers, json={"readings": [changed]})
    assert conflict.json()["conflicts"]
    listed = client.get("/sites/demo-site/readings", headers=_auth(admin))
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["tank_temp_c"] == pytest.approx(4.2)
    assert client.post("/ingest/readings", json={"readings": [_reading(minute=1)]}).status_code == 401
    foreign = _reading(minute=2)
    foreign["reading"]["site_id"] = "someone-else"
    assert client.post("/ingest/readings", headers=headers, json={"readings": [foreign]}).status_code == 403


def test_roles_config_and_alert_ack(api) -> None:
    _application, client = api
    admin = _token(client, "Ada", "admin-pass")
    manager = _token(client, "Baraka", "manager-pass")
    viewer = _token(client, "Chao", "viewer-pass")
    other = _token(client, "Dadi", "other-pass")
    assert client.post("/sites", headers=_auth(admin), json={"site": _demo_site(), "api_key": "site-key-2"}).status_code == 201
    assert client.post("/sites", headers=_auth(manager), json={"site": _demo_site(), "api_key": "nope-nope"}).status_code == 403

    forbidden = client.put(
        "/sites/demo-site/config",
        headers=_auth(manager),
        json={"reason": "raise the store target", "config": {"store_target_soc_pct": 90}},
    )
    assert forbidden.status_code == 403
    updated = client.put(
        "/sites/demo-site/config",
        headers=_auth(admin),
        json={"reason": "raise the store target", "config": {"store_target_soc_pct": 90}},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["config_version"] == 2
    pulled = client.get("/sites/demo-site/config", headers={"X-API-Key": "site-key-2"})
    assert pulled.status_code == 200
    assert pulled.json()["config_version"] == 2
    assert pulled.json()["config"]["store_target_soc_pct"] == 90

    visible = {site["id"] for site in client.get("/sites", headers=_auth(other)).json()}
    assert "demo-site" not in visible
    assert client.get("/sites/demo-site/status", headers=_auth(other)).status_code == 403
    assert client.get("/sites/demo-site/status", headers=_auth(viewer)).status_code == 200

    with session_for(client.app.state.engine) as session:
        alert = Alert(
            site_id="demo-site",
            timestamp=datetime(2026, 6, 1, 7, 0, tzinfo=UTC),
            event_type=EventType.BACKUP_STARTED,
            channel=AlertChannelKind.CONSOLE,
            recipient="console:site-manager",
            status=AlertStatus.SENT,
            message="Backup started",
            dedup_key="backup",
        )
        row: AlertRow = alert_to_row(alert)
        session.add(row)
        session.commit()
        alert_id = row.idempotency_key

    assert client.post(f"/alerts/{alert_id}/ack", headers=_auth(viewer)).status_code == 403
    assert client.post(f"/alerts/{alert_id}/ack", headers=_auth(other)).status_code == 403
    acked = client.post(f"/alerts/{alert_id}/ack", headers=_auth(manager))
    assert acked.status_code == 200, acked.text
    assert acked.json()["status"] == "acked"
    assert acked.json()["acknowledged_by"] == "Baraka"


def test_actions_round_trip(api) -> None:
    _application, client = api
    admin = _token(client, "Ada", "admin-pass")
    client.post("/sites", headers=_auth(admin), json={"site": _demo_site(), "api_key": "site-key-3"})
    action = ControlAction(
        site_id="demo-site",
        timestamp=datetime(2026, 6, 1, 6, 15, tzinfo=UTC),
        mode=ControlMode.NORMAL,
        action=ControlActionKind.COOL_TANK,
        reason="Tank is 5.2°C, above the 4.0°C band, so it is being cooled.",
        command=ActuatorCommand(compressor_kw_electric=1.5, compressor_target="tank", tank_cooling_kw_thermal=4),
    )
    event = Event(
        site_id="demo-site",
        timestamp=datetime(2026, 6, 1, 6, 15, tzinfo=UTC),
        event_type=EventType.TEMP_LIMIT_EXCEEDED,
        severity=Severity.CRITICAL,
        message="Tank is hot",
    )
    headers = {"X-API-Key": "site-key-3"}
    assert client.post("/ingest/actions", headers=headers, json={"actions": [action.model_dump(mode="json")]}).status_code == 200
    assert client.post("/ingest/events", headers=headers, json={"events": [event.model_dump(mode="json")]}).status_code == 200
    reasons = client.get("/sites/demo-site/actions", headers=_auth(admin)).json()
    assert reasons[0]["reason"].startswith("Tank is 5.2°C")
    events = client.get("/sites/demo-site/events", headers=_auth(admin)).json()
    assert events[0]["event_type"] == "temp_limit_exceeded"
    alarms = client.get("/sites/demo-site/alerts", headers=_auth(admin)).json()
    assert alarms[0]["message"] == "Tank is hot"
    guarantee = client.get("/sites/demo-site/guarantee?period=day", headers=_auth(admin))
    assert guarantee.status_code == 200, guarantee.text
    assert guarantee.json()["fee"] is None
    pdf = client.get("/sites/demo-site/guarantee.pdf?period=day", headers=_auth(admin))
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF")
    csv_body = client.get("/sites/demo-site/guarantee.csv?period=day", headers=_auth(admin))
    assert csv_body.status_code == 200
    assert "Morning delivery" in csv_body.text


def test_six_hour_outage_syncs_without_duplicates() -> None:
    loaded = load_scenario(scenarios_dir() / "comms_outage.yaml")
    loaded.scenario.duration_hours = 15
    application = create_app(secret="test-secret")
    client = TestClient(application)
    with session_for(application.state.engine) as session:
        add_user(session, "admin", "Ada", Role.ADMIN, "admin-pass", None)
    token = _token(client, "Ada", "admin-pass")
    site = loaded.scenario.site.model_dump(mode="json")
    created = client.post("/sites", headers=_auth(token), json={"site": site, "api_key": "sync-key-1"})
    assert created.status_code == 201, created.text

    store = EdgeStore()
    run_closed_loop(loaded, store)
    produced = store.reading_count()
    assert produced > 6 * 60

    agent = SyncAgent(store, client, "sync-key-1", batch_size=100)
    agent.online = False
    assert agent.sync_once()["skipped"] is True
    during = client.get(f"/sites/{loaded.scenario.site.id}/readings", headers=_auth(token))
    assert during.json() == []

    agent.online = True
    agent.drain()
    after = client.get(f"/sites/{loaded.scenario.site.id}/readings", headers=_auth(token)).json()
    assert len(after) == produced
    agent.drain()
    replayed = client.get(f"/sites/{loaded.scenario.site.id}/readings", headers=_auth(token)).json()
    assert len(replayed) == produced
    store.close()
