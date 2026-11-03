# LactoGrid

LactoGrid sells solar-powered cooling as a service to dairy collection centres. Farmers deliver milk in the early morning and the evening. The sun is strongest at midday. A thermal cold store is charged while the sun is out and used at dawn, dusk, and overnight. Grid power and diesel are a last resort.

The software watches the site, decides where solar power goes, and calculates the milk-safety figures behind a yearly service guarantee.

This repository is Phase 1: a rule-based controller driven entirely by a simulated site. No hardware is required.

## Architecture

```
sensors or simulator
        |
        v
   message bus (in-process, or MQTT)
        |
        v
   site gateway (edge)
        |- controller
        |- SQLite
        |- actuator driver
        '- sync agent  ---->  cloud API (Postgres)
                                  |- dashboard
                                  |- alerts
                                  '- SLA reports
```

The edge keeps controlling and logging with no internet. When the link returns, the sync agent upserts each record by `site_id + timestamp + type`, so retries do not duplicate a row and a gap is not left behind.

## Status

| Milestone | What | State |
|---|---|---|
| 1 | Shared schemas, database model, simulator, scenarios, fault injection | Done |
| 2 | Edge controller, local database, decision log, safety rules | Done |
| 3 | Cloud API and offline sync | Done |
| 4 | Dashboard | Done |
| 5 | SLA engine, PDF and CSV reports | Done. Fee left blank until a pilot prices it |
| 6 | Alert acknowledgement, forecast mode, hardware skeletons, full docs | Forecast mode and acknowledgement are in. Hardware skeletons and a live weather feed are not |

Decisions that were not fixed by the brief are in [docs/DECISIONS.md](docs/DECISIONS.md).

## Layout

```
config/          YAML: defaults, the demo site, scenarios
shared/          schemas, physics, SQLAlchemy models, in-process bus
simulator/       site physics, deliveries, faults
edge/            controller, local SQLite, sync agent
cloud/           API and the demo seed
dashboard/       mobile web UI (overview, decisions, alerts)
tests/
docs/
```

## Run the tests

Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

## Run a scenario

Open-loop (no controller yet, so the tank is not actively cooled):

```bash
python -m simulator --scenario config/scenarios/sunny_day.yaml --steps 20
```

Scenarios in `config/scenarios/`:

- `sunny_day` — clear day, demo hub
- `cloudy_day` — overcast
- `undersized_hub` — solar and store too small for the milk
- `evening_milk_spike` — most of the milk arrives after sunset
- `comms_outage` — internet down for six hours

Every limit (setpoints, COP, prices, tank size) is in `config/defaults.yaml` or the site file. Anything marked `TODO(verify)` is a placeholder, not a measured fact.

## How to add a site

1. Copy `config/sites/demo.yaml` to a new file.
2. Set a new `id`, name, location, timezone, and the real equipment ratings once they are known.
3. Leave prices and anything you have not measured as `TODO(verify)`. Do not invent a tariff or a Modbus map.
4. Point a scenario at the file with `site_file`, or register the site through the cloud API once that milestone is in place. The API key for ingest is per site and is not committed.

## Run the controller on a scenario

```bash
python -m edge --scenario config/scenarios/sunny_day.yaml
```

This closes the rule-based controller around the simulator and prints the milk temperature and backup energy. Nothing is sent to a server.

## What works today

- Message schemas for readings, control actions, events, alerts, milk batches, and SLA periods.
- One database model that creates on SQLite now and is the schema Postgres will use.
- A deterministic simulator: solar curve, ambient, milk deliveries at 30 °C, collection at 09:00, heat leak, compressor COP, cold store, battery, and backup.
- Fault injection: sensor dropout, stuck sensor, compressor failure, internet outage, grid outage, heavy cloud.
- Idempotency keys that treat a retry as the same row and keep two different events that share a timestamp.
- Rule-based controller with a written reason for every decision, safe mode, manual override, and a watchdog fallback that leaves the compressor allowed to run.
- Local SQLite log. A repeated record is ignored. A different payload for the same key is rejected.
- Sync agent. With the link down, rows stay in the outbox. When the link returns they are upserted in the cloud. A second sync does not duplicate them.
- Cloud API with per-site API keys, admin / manager / viewer tokens, versioned config, and OpenAPI at `/docs`. See [docs/API.md](docs/API.md).
- Dashboard at `/` after the demo database is seeded. Overview, decision log, alerts a manager can acknowledge, and day, week, and month guarantee reports with CSV and PDF. The annual fee is blank. Light and dark themes.

## Open the dashboard

```bash
python -m cloud.seed
LACTOGRID_DATABASE_URL=sqlite:///./data/demo.sqlite LACTOGRID_SECRET=dev-only-change-me \
  uvicorn cloud.app:build_app --factory --port 8000
```

Open http://127.0.0.1:8000. The sign-in screen has the presentation accounts. Click one, or type the name in any case.

| Name | Password | Role |
|---|---|---|
| Ada | `dev-only-admin` | Admin |
| Baraka | `dev-only-manager` | Manager |
| Chao | `dev-only-viewer` | Viewer |

These passwords are placeholders for a laptop, not a site. The seed is seven demo days from 1 June 2026 for each collection centre, run in forecast mode. The page marks the last sample **Stale** because it is older than the clock on the computer. That is the demo data, not a live site. Reports can be downloaded as CSV and PDF. Baraka or Ada can acknowledge an alarm. Chao cannot.
