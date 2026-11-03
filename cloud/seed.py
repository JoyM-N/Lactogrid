"""Create a local demo database and fill it from the simulator.

Each scenario becomes its own site so the dashboard can switch between them.
Passwords and API keys below are development placeholders. Run:

    python -m cloud.seed
"""

from __future__ import annotations

import os

from fastapi.testclient import TestClient

from cloud.app import add_user, build_app
from cloud.db import session_for
from edge.forecast import FixedOutlook, ForecastStrategy
from edge.loop import run_closed_loop
from edge.store import EdgeStore
from edge.sync import SyncAgent
from shared.config import load_scenario
from shared.enums import Role
from shared.paths import repo_root, scenarios_dir

# name, file, optional site id/name so two scenarios do not share demo-site
# Each file is one fictional collection centre. The physics differ so a
# presentation can show a healthy hub, cloud, a small plant, an evening
# delivery, and a link outage. Names are centres, not scenario titles.
SCENARIOS = (
    ("sunny_day.yaml", {"name": "Demo Collection Centre", "location": "Demo data · morning and evening intake"}),
    ("cloudy_day.yaml", {"id": "cloudy-site", "name": "West Gate Collection Centre", "location": "Demo data · morning and evening intake"}),
    ("undersized_hub.yaml", {"name": "East Gate Collection Centre", "location": "Demo data · morning and evening intake"}),
    ("evening_milk_spike.yaml", {"name": "South Gate Collection Centre", "location": "Demo data · morning and evening intake"}),
    ("comms_outage.yaml", {"name": "North Gate Collection Centre", "location": "Demo data · morning and evening intake"}),
)


def main() -> None:
    data_dir = repo_root() / "data"
    data_dir.mkdir(exist_ok=True)
    db_file = data_dir / "demo.sqlite"
    edge_file = data_dir / "edge.sqlite"
    for path in (db_file, edge_file):
        if path.exists():
            path.unlink()
    os.environ["LACTOGRID_DATABASE_URL"] = "sqlite:///" + str(db_file)
    os.environ["LACTOGRID_SECRET"] = "dev-only-change-me"
    application = build_app()
    with session_for(application.state.engine) as session:
        add_user(session, "admin", "Ada", Role.ADMIN, "dev-only-admin", None)
        add_user(session, "manager", "Baraka", Role.MANAGER, "dev-only-manager", "demo-coop")
        add_user(session, "viewer", "Chao", Role.VIEWER, "dev-only-viewer", "demo-coop")
    client = TestClient(application)
    token = client.post("/login", json={"name": "Ada", "password": "dev-only-admin"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    for filename, rename in SCENARIOS:
        loaded = load_scenario(scenarios_dir() / filename)
        loaded.scenario.duration_hours = 24 * 7
        site = loaded.scenario.site
        if rename:
            site = site.model_copy(update=rename)
            loaded.scenario.site = site
        api_key = "dev-only-site-key" if site.id == "demo-site" else f"dev-only-{site.id}"
        created = client.post(
            "/sites",
            headers=headers,
            json={"site": site.model_dump(mode="json"), "api_key": api_key},
        )
        if created.status_code != 201:
            raise SystemExit(f"{filename}: {created.text}")
        scenario_edge = data_dir / f"edge-{site.id}.sqlite"
        if scenario_edge.exists():
            scenario_edge.unlink()
        store = EdgeStore("sqlite:///" + str(scenario_edge))
        print(f"Simulating {loaded.scenario.name} ({site.name})...")
        outlook = FixedOutlook(loaded.scenario.weather.cloudiness)
        run_closed_loop(loaded, store, strategy=ForecastStrategy(outlook))
        report = SyncAgent(store, client, api_key, batch_size=500).drain(max_rounds=400)
        store.close()
        print(f"  inserted={report['inserted']} duplicates={report['duplicates']} conflicts={len(report['conflicts'])}")
    print(f"Demo database: {db_file}")
    print("Sign in as Ada / dev-only-admin and pick a site")


if __name__ == "__main__":
    main()
