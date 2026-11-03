# Cloud API

Interactive docs are at `/docs` when the API is running. All timestamps are UTC.

## Auth

Ingest routes take `X-API-Key`. The key is stored as a SHA-256 hash and belongs to one site. A body whose `site_id` does not match the key is rejected.

People call `POST /login` with a name and password and then send `Authorization: Bearer <token>`. Roles:

- **admin** — every site, can change configuration and create sites
- **manager** — one cooperative, can read and acknowledge alerts
- **viewer** — one cooperative, read only

The development secret is `LACTOGRID_SECRET` in the environment. It is not a production credential.

## Ingest

`POST /ingest/readings`, `POST /ingest/events`, and `POST /ingest/actions` take a batch. Each record is upserted on `site_id + timestamp + type`.

The response is `{inserted, duplicates, conflicts}`. A duplicate is the same payload again and is not stored twice. A conflict is the same key with a different payload; the stored row is left unchanged and the key is listed.

## Reads

- `GET /sites`
- `GET /sites/{id}/status` — latest reading, current mode, reason, active alert count, and how old the sample is
- `GET /sites/{id}/readings?from=&to=&resolution=` — `resolution` is `1m` (default) or `raw`
- `GET /sites/{id}/actions` — controller decisions and the reason text
- `GET /sites/{id}/events`
- `GET /sites/{id}/config` — API key or bearer token
- `PUT /sites/{id}/config` — admin only. Body is `{reason, config}`. The version increments and an audit row is written.
- `POST /alerts/{id}/ack` — admin or manager of that cooperative
- `GET /health`

The edge pulls configuration when it syncs. The site is often unreachable, so the cloud does not open a connection to the gateway. The version on the edge updates on the next successful sync.

`GET /sites/{id}/sla` and the monthly PDF are added with the reporting engine.
