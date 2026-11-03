# Decisions

Conflicts in the brief, and choices that were not specified, are recorded here. Where the brief disagreed with itself, the option that keeps the milk colder was taken.

## Stack

Python, FastAPI, Pydantic, SQLAlchemy, and Alembic, as suggested. SQLite on the edge, PostgreSQL in the cloud. The unit tests talk to SQLite only, through the same models, so CI does not need a database service. Docker Compose will run Postgres for the cloud service.

The message bus is a small interface. Tests and the single-process demo use an in-process bus. MQTT (Paho, against Mosquitto) is the other implementation and is wired when the edge and the simulator run as separate processes.

The dashboard will be static HTML, CSS, and a small script served by the API. There is no Node build. A phone on a slow link should not have to download a framework. Chart colours come from the design tokens in the brief.

## Layout

The repository root is the project. `shared`, `simulator`, `edge`, `cloud`, `dashboard`, `tests`, `docs`, and `config` sit at the top.

## Numbers live in YAML

`config/defaults.yaml` holds physics, controller, SLA, and alert numbers. A site file holds the equipment that varies by centre. A scenario can override either. Python fails fast if a key is missing or unknown.

Values taken from the brief are not marked. Values that were not given are marked `TODO(verify)` in the YAML and are demo assumptions, not measurements:

- Pump and light load, 0.4 kW.
- Battery charge and discharge power limits, 1.5 kW, and a 10% floor on state of charge.
- Discount tiers below the 98% placeholder, and a price of 1.0 in currency `XXX`. That is demo arithmetic, not a Kenya Power or diesel tariff.
- Demo hub size: 12 kWp, 60 kWh thermal store, 1,000 litres a day, 2,000 litre tank. This is a sizing assumption so the sunny-day scenario has a chance of meeting the guarantee. It is not a survey.
- Forecast mode trims pumps and lights to half when the store is under 50%. The brief says "trimmed" and does not say by how much.
- Freeze cut-out at 1.0 °C inside the plant model.
- Stuck-sensor window, 45 minutes.

Discharge efficiency and cold-store charge efficiency default to 1.0 because the brief does not specify a loss. Inventing one would make the store look smaller than the equipment is.

## Idempotency

Records are keyed by `site_id + timestamp (UTC, whole seconds) + type`. The type for an event is `event:<event_type>` and for an alert `alert:<channel>:<dedup_key>`. A retried copy of the same fact collapses. Two different facts at the same second both stay. A bare type of `event` would have dropped one of them.

There is no foreign key from a reading to its site. A replayed batch can arrive in either order. The API still checks that the API key belongs to the site named in the body.

## Tank mixing

The brief defines effective thermal mass as milk litres plus 150 litre-equivalents, and writes the mixing formula as `(T × mass + 30 × added) / (mass + added)`. Mass is the effective mass, including the shell. An empty tank at 4 °C does not jump to 30 °C when the first litres arrive, and energy is conserved. Litres that do not fit in the tank are spilled and flagged. They are not silently cooled.

## What `solar_kw` means

The simulator publishes available PV power, before curtailment. If the sensor were the inverter output after a limit, the controller would not see spare sun. `solar_used_kw` is what the site actually took. Reports that say "solar used" integrate `solar_used_kw`.

Phase 1 models the cold store as stored thermal kWh and a state of charge. It does not invent a temperature-to-charge curve for an ice bank. `store_temp_c` stays empty until a partner curve exists. `TODO(verify)`.

## Power priority

The compressor does not draw the small battery. At night the tank is cooled from the cold store. Solar runs the compressor when the tank needs it or when the store is short of its target. Pumps and lights take what solar remains, then the battery, and backup only if both fall short. Leftover solar charges the battery.

When both the grid and diesel are available, backup power is taken from the grid. Diesel is used when the grid is down. This is an operating choice, not a claim about tariffs.

## Controller timing

The brief asks for a decision every 15 minutes, and also immediately on a safety event. A 15-minute-only loop can freeze a nearly empty tank (the shell is only 150 litre-equivalents) or sit above 6 °C for most of that interval. Milk protection (the thermostat, the freeze cut-out, sensor faults, backup) is evaluated on every sample. Store charging and the auxiliary split are recomputed at least every `controller.interval_minutes`, and sooner when milk protection changes state. Running more often than every 15 minutes is allowed.

The plant itself will not cool the tank below `tank.low_cutout_c` (1.0 °C). That is a local interlock, like a thermostat in the contactor, in case the controller is late.

## Safe mode and overrides

A missing, implausible, or stuck tank, ambient, or store reading enters safe mode. The compressor relay stays allowed to run. Cooling uses solar and the store. Backup is allowed in safe mode when the store reading is missing or the store is below the backup threshold, because staying hot in order to save fuel is the worse failure. This is the conflict between "backup is last resort" and "never turn the compressor off because a reading is missing". Milk wins.

A manual override is logged with who and why, and it expires. It does not override safe mode.

## Simulator step

Each step is explicit Euler and at most 60 seconds. Milk is mixed at the start of the step and cooling then acts for the whole step, so a delivery is cooled about one minute early. Collection empties the tank at 09:00 local. Sensor faults change the published reading only. The plant keeps the true temperature, so a stuck sensor does not freeze the physics.

The cloud multiplier is a seeded sum of slow sine waves. It is deterministic. It is not a climate dataset.

## Backup chatter

Backup cooling starts only above 6.0 °C with the store below 0.5 kWh thermal, as specified. It is allowed to stop once the tank is down to 5.5 °C or the store has recovered above 1.0 kWh, so the contactor does not chatter. Both margins are config.

## SLA source of truth

Every figure in a report is computed from stored readings, not from the simulator's private truth. A data-completeness figure is shown beside the uptime. Missing tank temperature while milk is present counts as not safe, and it lowers completeness. It is not treated as cold.

## Secrets

No production secrets in the repository. Local Docker will use a development API key that is obvious in `.env.example` and is not a production credential.

## Controller scan

`controller.interval_minutes` is the longest allowed gap between decisions. The gateway calls the controller on every one-minute sample, which is inside that gap. Waiting a full 15 minutes between scans can freeze the shell (only 150 litre-equivalents) or leave milk above 6 °C for most of the interval.

The compressor request is variable, up to its electrical maximum, because the inverter can set a power limit. The request is the heat needed to reach 4 °C over the next minute, capped at 5.5 kW thermal. A small gap does not dump the full 5.5 kW into the tank. `TODO(verify)` if the real compressor is on/off only.

## Demo sizing

The tank cannot reject heat faster than 5.5 kW. A delivery faster than that climbs above 6 °C even when the cold store is full, and the backup rule does not start backup while the store still has cold. The demo site is therefore 600 litres a day, so a sunny day stays at or below 6 °C. That is a sizing assumption, not a survey. `TODO(verify)`.

The placeholder pump and light load is 0.15 kW, not 0.4 kW. A 3 kWh battery with a 10% floor cannot carry 0.4 kW through a 12-hour night (4.8 kWh), and the sunny-day acceptance test allows under 2 kWh of backup a day. Milk cooling does not use the battery. `TODO(verify)` the real auxiliary load; if it is higher, the battery or the backup budget has to change.

## Safe mode sensors

Missing, implausible, or stuck tank temperature, ambient temperature, or cold-store level enters safe mode. The compressor is enabled and the store may discharge. Backup is allowed only when the store is unknown or below 0.5 kWh. A missing solar reading is treated as zero sun, not as safe mode, when the tank and the store are still believable. A known-good cold tank is not heated up just because the pyranometer failed.

A stuck tank trace is latched until the published temperature moves. The cooling that safe mode then commands must not clear the fault by itself. The expected movement uses the tank cooling cap once (store discharge fills that cap) and includes heat from milk that arrived in the window. Leaving the delivery out made a normal morning intake look like a stuck sensor.

## Configuration reaches the edge by pull

The brief says configuration is pushed to the edge. The gateway is often offline, so a push would fail and the site would keep an old file with no record of the miss. The cloud stores a versioned, audited config. The edge pulls it on the next sync. The pull is the reliable form of that push.

## Dashboard

The operations view is one HTML page, CSS, and a script. There is no Node build, so a low-end phone downloads one small page. Colours are the CSS variables from the brief. Charts read those variables when they draw, so a theme change repaints in the same colours. Status is a word plus an icon. The word uses the main text colour, because the warning amber is below 4.5:1 as text on white. The icon carries the status colour.

A sample older than 30 minutes is labelled Stale. The milk status stays Safe, Warning, or At risk from the temperature, not from the age of the file. The demo day is 1 June 2026, so a laptop in November shows Stale. That is the seed, not a live outage.

The site menu lists collection centres. Each one is a place farmers deliver milk in the morning and the evening. The figures are demo data from the closed-loop scenarios, labelled as demo data. Day, week, and month reports add up the stored readings in that window and say how many days are on record. They do not invent the missing days. The weather outlook uses the same solar curve as the simulator and the forecast store-target rule (75% plus up to 25% when cloud is expected). Forecast mode runs those rules with the raised target, and it trims pumps and lights while the store is under half. Safety mode and a manual override are applied before the strategy, so the forecast cannot turn cooling off. It is not a live weather feed. `TODO(verify)` the weather provider before a real site uses it.

The guarantee report is recomputed from stored readings for a day, the week that contains the latest recorded day, or that calendar month. Morning is 06:00–08:00 and evening is 17:00–19:00, local. Litres are the rises in tank volume inside those windows. A missing temperature while milk is present counts as not safe. The credit share uses the configured uptime tiers on the milking windows. The annual fee stays blank. `TODO(verify)` the price before a credit is an amount of money.

A warning or critical site event opens one console alarm, then stays quiet for the configured rate limit. A manager or admin acknowledges it, and the row stores their name. SMS is not connected.

Backup that is already on, while the tank has fallen back to 6 °C or below, is described as staying on until 5.5 °C. The start sentence is reserved for a tank that is still above 6 °C.

## Hardware

Modbus register addresses will not be invented. The driver skeleton reads a register map file full of `TODO(verify)` and does not talk to a bus until a partner map exists.
