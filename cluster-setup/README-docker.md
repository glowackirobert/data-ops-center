# Cluster Setup

Docker Compose based setup for the Data Ops Center cluster.

## Services

### Core services (always running)

Only the rows marked **host** publish a port; the rest are reachable only
from inside the `pinot-network` bridge, by container name. See
[Port binding](#port-binding) for why, and for how to reach an internal port
from the host when debugging.

| Service               | Port    | Reachable from  | Description                                                                                                                                                                                                                        |
|-----------------------|---------|-----------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Zookeeper             | 2181    | internal        | Coordination service required by Apache Pinot.                                                                                                                                                                                     |
| Kafka                 | 9092    | internal        | Message broker in KRaft mode.                                                                                                                                                                                                      |
| Schema Registry       | 8081    | internal        | Confluent Schema Registry for Avro schema management.                                                                                                                                                                              |
| Pinot Controller /UI/ | 9000    | internal        | Manages cluster metadata, table configs and segment assignment. Browser access goes through Caddy (`pinot.` subdomain, basic auth); the debug override re-publishes it on `127.0.0.1` in dev.                                                                                                                                                                    |
| Pinot Broker          | 8099    | internal        | Accepts SQL queries and routes them to the appropriate servers.                                                                                                                                                                    |
| Pinot Server /API/    | 8097    | internal        | REST admin API for health checks and segment management, called by the Controller.                                                                                                                                                 |
| Pinot Server /query/  | 8098    | internal        | Internal netty port the Broker uses to send queries to and read results from the server.                                                                                                                                           |
| Pinot Minion          | 7500    | internal        | Background task executor, driven by Controller-scheduled jobs, responsible for segment lifecycle operations.                                                                                                                       |
| Prometheus            | 9090    | internal        | Scrapes JMX metrics from Kafka and all Pinot components.                                                                                                                                                                           |
| Grafana               | 3000    | **host**        | Dashboards over Prometheus metrics and Loki logs.                                                                                                                                                                                  |
| Loki                  | 3100    | internal        | Log storage/query backend (LogQL).                                                                                                                                                                                                 |
| Alloy                 | 12345   | internal        | Tails every container's stdout/stderr via the Docker socket and ships it to Loki; `12345` serves its debug UI (component graph, live pipeline state).                                                                             |
| Superset              | 8088    | **host**        | BI and data exploration UI connected to Pinot via `pinotdb`.                                                                                                                                                                       |
| Caddy                 | 80, 443 | **host**        | TLS reverse proxy; the only public entrypoint. Serves the four browser-facing services on `map.` / `bi.` / `ops.` / `pinot.` subdomains. |
| Web app               | 3001    | **host**        | Live vehicle map (flicker-free 10 s refresh) + Analytics tab embedding the Superset dashboard                                                                                                                                      |
| Gdansk GPS producer   | -       | n/a             | Polls the Gdansk public transport API every 10 s (twice as often as the API's own ~20 s refresh, whose phase is unknown) and publishes every vehicle position as JSON to the `gdansk-public-transport` topic. Listens on nothing.  |

### Init containers (run once, `--profile init`)

| Container                                | Description                                                                                                                                                                                                            |
|------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `kafka-topic-init`                       | Creates Kafka topics.                                                                                                                                                                                                  |
| `kafka-producer`                         | Publishes Avro-serialized `trade` events to Kafka.                                                                                                                                                                     |
| `pinot-table-registrar`                  | Registers schemas and tables with the Pinot Controller.                                                                                                                                                                |
| `pinot-ingestion-runner`                 | Runs a batch ingestion job that reads from S3 and pushes segments to Pinot.                                                                                                                                            |
| `superset-init`                          | Runs DB migrations, creates the admin user, initialises Superset roles, registers the Apache Pinot database connection, imports dashboards, creates the `EmbeddedGuest` role and registers dashboards for embedding    |

Init containers are one-shot — they exit after completing their task. Run them once on first setup, or whenever you need to re-seed the cluster.

`gdansk-public-transport-kafka-producer` is **not** one of them: it has no
`profiles:` key, so it is an ordinary core service. A plain `up` starts the
GPS feed — the map has vehicles without ever running `--profile init` — and a
plain `down` removes it. `restart: unless-stopped` keeps it polling across
crashes and Docker daemon restarts.

### Logs (Loki + Alloy)

Alloy discovers containers via the Docker socket (scoped to this compose
project only, via the `com.docker.compose.project=container` label filter —
otherwise it would also pick up unrelated containers on the same host) and
ships stdout/stderr to Loki, labeled by `container` (container name) and
`stream` (`stdout`/`stderr`).

Grafana's home page (`http://localhost:3000/`) is the provisioned **"Container
Logs"** dashboard (`cluster-setup/grafana/dashboards/logs_dashboard.json`,
set via `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH`) — a full-width Logs panel
with `container` and `stream` dropdown variables (both default to "All") to
switch between containers without writing LogQL. For anything beyond
filtering by container/stream, use Grafana → Explore → Loki datasource, e.g.:

```logql
{container="pinot-server"}
{container=~"pinot-.*"} |= "ERROR"
```

Alloy's own debug UI (component graph, live pipeline state) listens on 12345
but is not published — bring it up on `http://localhost:12345` with the debug
override in [Port binding](#port-binding). Log retention in Loki is 168h
(7 days), matching Kafka's `KAFKA_LOG_RETENTION_HOURS`.



## Requirements

- Docker with Docker Compose v2.17+.



## Secrets

Populate the following files before the first run:

```
cluster-setup/container/secrets/aws_credentials            # AWS credentials for S3/Pinot access
cluster-setup/container/secrets/grafana_admin_password     # Grafana admin password
cluster-setup/container/secrets/superset_secret_key        # Long random string for Flask session signing
cluster-setup/container/secrets/superset_admin_password    # Superset admin password
cluster-setup/container/secrets/superset_admin_username    # Superset admin username
cluster-setup/container/secrets/superset_admin_email       # Superset admin email
cluster-setup/container/secrets/superset_mapbox_api_key    # Mapbox API key for map visualisations
cluster-setup/container/secrets/pinot_basic_auth_hash      # bcrypt hash guarding the Pinot controller UI (see below)
```

The `secrets/` directory is gitignored — these files must be created manually on every machine.

`pinot_basic_auth_hash` is the only one that is not typed by hand — it holds a
bcrypt hash, not a password. Generate it from the password you want to use:

```bash
docker run --rm caddy:2.10-alpine caddy hash-password --plaintext 'your-password' > cluster-setup/container/secrets/pinot_basic_auth_hash
```

The username is `PINOT_AUTH_USER` in the env files (default `admin`).



## Image Versions

All image versions (third-party and custom-built) live in a single file:
`cluster-setup/env/versions.env`. It is read by the compose commands (as the
first `--env-file`), by the build commands below (`source` it first), and by
the CI workflow. To bump a version, edit only that file.

The one exception is `kafka-producer-app`, whose version is owned by Maven in
`kafka-producer-app/pom.xml`.

To see which images have newer stable releases on Docker Hub, run (no
dependencies beyond Python 3):

```bash
python cluster-setup/scripts/check_image_tags.py
```

It reads the current versions from `versions.env` and from the base-image
`FROM` lines in `kafka-producer-app/Dockerfile.kafka-producer-app`, and prints
the newest stable tags next to each.

## Custom Images

Services that use custom-built images must be built before first run in dev mode:

```bash
# Image versions — single source of truth
source cluster-setup/env/versions.env

# Apache Pinot — adds table configs, JMX exporter and ingestion scripts
docker build --build-arg APACHE_PINOT_VERSION=$APACHE_PINOT_VERSION -t apache-pinot:$APACHE_PINOT_VERSION -f cluster-setup/container/Dockerfile.apache-pinot .

# Kafka producer app — Maven multi-stage build of the Java Avro producer (version from pom.xml)
docker build -t kafka-producer-app:1.0.0 -f kafka-producer-app/Dockerfile.kafka-producer-app .

# Apache Superset — adds pinotdb driver on top of the official image
docker build --build-arg SUPERSET_VERSION=$SUPERSET_VERSION -t superset:$SUPERSET_VERSION -f cluster-setup/container/Dockerfile.superset .

# Web app — stdlib-only Python server serving the vehicle map UI
docker build --build-arg PYTHON_VERSION=$PYTHON_VERSION -t web-app:$WEB_APP_VERSION -f cluster-setup/container/Dockerfile.web-app .
```

For prod - images are pulled from Docker Hub `robertglowacki83/` — 
Images are built and pushed automatically by `.github/workflows/docker-ci.yml` on pushes to `master` that touch image inputs.



## Running the Cluster

All commands use two env files: `versions.env` (image versions, shared) plus `env.dev`/`env.prod` 
to switch between dev and prod behaviour (port bindings, image sources, JVM heap sizes). 
Multiple `--env-file` flags require Docker Compose v2.17+.

JVM heaps (Zookeeper, Kafka, Schema Registry, all Pinot components and runners) 
are set per environment via the `*_HEAP` variables in the env files — dev 
uses small laptop-friendly sizes, prod the full sizes. 
Unset variables fall back to prod-sized defaults in `container-compose.yml`. 
Zookeeper's is `ZOOKEEPER_HEAP_MB` (a number in MB, its image's convention); 
all others take JVM flags like `-Xms256M -Xmx1G`.

### Development

```bash
# Start core services
docker compose --env-file cluster-setup/env/versions.env --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml up

# First-time setup — also run init containers
docker compose --env-file cluster-setup/env/versions.env --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml --profile init up

# Start core services plus the debug-port override
docker compose --env-file cluster-setup/env/versions.env --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml -f cluster-setup/container/container-compose.debug-ports.yml up

# First-time setup — also run init containers plus the debug-port override
docker compose --env-file cluster-setup/env/versions.env --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml -f cluster-setup/container/container-compose.debug-ports.yml --profile init up

# Stop
docker compose --env-file cluster-setup/env/versions.env --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml down
```

### Production

```bash
# Start core services, pull latest images
docker compose --env-file cluster-setup/env/versions.env --env-file cluster-setup/env/env.prod -f cluster-setup/container/container-compose.yml up --pull always -d

# First-time setup — also run init containers
docker compose --env-file cluster-setup/env/versions.env --env-file cluster-setup/env/env.prod -f cluster-setup/container/container-compose.yml --profile init up --pull always

# Stop
docker compose --env-file cluster-setup/env/versions.env --env-file cluster-setup/env/env.prod -f cluster-setup/container/container-compose.yml down
```

### Verifying Kafka messages

```bash
MSYS_NO_PATHCONV=1 docker exec -it kafka /bin/bash -c "env -u KAFKA_OPTS /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic trade --from-beginning"
```

## Pinot Tables

Schemas and table configs live in `cluster-setup/table_config/` and are registered
automatically by the `pinot-table-registrar` init container (`add-tables.sh`). The script
is idempotent: it POSTs missing schemas/tables and PUTs existing ones (schemas with
`?reload=true`), so re-running init never drops ingested segments. To apply config/schema
edits to a running controller directly, run the same script from the host — port 9000
is only published with the debug override (see [Port binding](#port-binding)), so bring
the stack up with it first, or point `PINOT_CONTROLLER_URL` at
`https://pinot.<BASE_DOMAIN>` and pass the basic-auth credentials:

```bash
PINOT_CONTROLLER_URL=http://localhost:9000 TABLE_CONFIG_DIR=cluster-setup/table_config bash cluster-setup/table_config/add-tables.sh
```

> **Note — backward-incompatible schema changes are rejected.** On update, the only schema
> edit Pinot accepts is **adding new columns**. Anything else — removing or renaming a
> column, changing a type, changing a time field — makes `PUT /schemas/{name}` fail with
> HTTP 400 (`"Backward incompatible schema ... Only allow adding new columns"`); the script
> retries and exits non-zero, leaving the old schema in place. That failure is deliberate:
> existing segments were built against the old schema. To force such a change, delete the
> table and schema (`DELETE /tables/{name}`, `DELETE /schemas/{name}`) and re-run the
> script — accepting that all ingested data is dropped and must be re-ingested
> (S3 batch + Kafka replay).

## S3 Batch Ingestion

The `gdansk_public_transport_ingestion_job_spec.yaml` spec reads every compacted file under
`s3://gdansk-public-transport/squashed/` and pushes one segment per file to the offline Pinot
table, named `gdansk_public_transport_<file stem>`. `squashed/` holds a mix of granularities —
day files (`YYYY/YYYY-MM-DD.json.gz` → `gdansk_public_transport_YYYY-MM-DD`) for recent,
not-yet-rolled-up history, and month files (`YYYY/YYYY-MM.json.gz` → `gdansk_public_transport_YYYY-MM`)
for fully-elapsed months — but never both for the same range: the `monthly` compaction
subcommand deletes a month's day files once it's squashed them into one month file,
so ingestion never has to know or care which granularity a given file is.

`INGESTION_DATE` is a **filename glob** passed to the job spec's `${DATE}` template placeholder (via `LaunchDataIngestionJob -values`):
an exact day (`2026-02-01`) or exact month (`2026-02`) each match exactly one file; unset/empty,
a partial month (`2026-06-*`), or a year (`2025-*`) match many — whatever mix of day/month files
currently exists for that range.

> **Only the exact-single-file case works as a direct `docker compose run`.**
> Pinot's `IngestionJobLauncher` pre-resolves the `inputFile` segment name
> generator's output by matching `file.path.pattern` against
> `includeFileNamePattern` *before* it lists any real S3 files — correct only
> when that pattern matches exactly one file. A wildcard match makes every
> file resolve to the same broken literal segment name (e.g.
> `gdansk_public_transport_*`) and the job fails on the first segment. Both
> `env.dev` and `env.prod` ship `INGESTION_DATE` empty, so **a plain
> `--profile init up` currently fails to ingest anything** — the
> `pinot-ingestion-runner` init container exits non-zero (the other init
> containers are unaffected). For a full backfill or any multi-day/month range, use
> `backfill_pinot_offline.py` below instead of a bare wildcard `INGESTION_DATE`
> (daily only currently — see its `--help`).

Ingestion is idempotent for a given file — segments are named after it and
overwritten, not duplicated — so retrying one failed day/month is simply
re-running it for that date/month.

> **Caveat — ingesting a month file after its days were already ingested.**
> Squashing a month deletes its day files from S3, but doesn't touch Pinot:
> if those days were already batch-ingested as individual day segments,
> ingesting the new month file adds a differently-named segment *alongside*
> them rather than replacing them — double-counting. Delete the superseded
> day OFFLINE segments for that range via the controller API after
> ingesting the month replacement.

```bash
# Single day (against an already-running cluster; skip init dependencies)
INGESTION_DATE=2026-07 docker compose \
  --env-file cluster-setup/env/versions.env \
  --env-file cluster-setup/env/env.prod \
  -f cluster-setup/container/container-compose.yml \
  --profile init \
  run --no-deps pinot-ingestion-runner

# Single month (once squashed, it's just another file in squashed/)
INGESTION_DATE=2026-02 docker compose \
  --env-file cluster-setup/env/versions.env \
  --env-file cluster-setup/env/env.prod \
  -f cluster-setup/container/container-compose.yml \
  --profile init \
  run --no-deps pinot-ingestion-runner
```

Segments are staged under `cluster-setup/volumes/pinot/ingestion-staging/` on
the host and the staging dir is cleaned at the start of each run. Heap for the
runner is `PINOT_INGESTION_RUNNER_HEAP` (default `-Xmx2G`).
`INGESTION_JOB_PARALLELISM` (default `2`) sets both the segment-creation and
push thread counts in the job spec — raise it together with the heap, since
each parallel segment-build thread shares the same `-Xmx`.

### Backfilling a range of days

`cluster-setup/scripts/backfill_pinot_offline.py` loops the ingestion job one
explicit date at a time (working around the wildcard limitation above),
listing candidate dates from `s3://gdansk-public-transport/squashed/` via the AWS
CLI (day files only — `YYYY-MM-DD.json.gz`; month files already squashed are
skipped by the filename match). It tracks which dates it has already ingested in a local JSON manifest
(`cluster-setup/volumes/pinot/ingested_dates.json`) rather than by inspecting
Pinot segment state, since MergeRollup renames/deletes day segments on its own
schedule independent of the script — skipping already-ingested days by
default avoids both wasted re-work and the double-counting caveat above.

> **This script cannot backfill a month that's already been squashed.**
> The monthly compaction job deletes a month's day files from S3 once it
> rolls them into `squashed/YYYY/YYYY-MM.json.gz` — at that point no day
> files remain for the script to find. A `--start-date`/`--end-date` range
> falling inside such a month prints `0 daily file(s) in range` and exits
> without ingesting anything (not an error — check the printed count).
> Check which form a month is in first (`aws s3 ls
> s3://gdansk-public-transport/squashed/YYYY/ | grep YYYY-MM`), and if it's
> already a single month file, ingest it directly with the single-month
> `INGESTION_DATE=YYYY-MM` command above instead of this script.

```bash
# Full backfill of everything in s3://gdansk-public-transport/squashed/
python cluster-setup/scripts/backfill_pinot_offline.py --env dev

# Narrow to a range
python cluster-setup/scripts/backfill_pinot_offline.py --env prod \
  --start-date 2026-01-01 --end-date 2026-01-31

# Preview the plan without ingesting anything
python cluster-setup/scripts/backfill_pinot_offline.py --dry-run
```

`--force` re-ingests dates already in the manifest (same double-counting risk
as re-ingesting an already-merged day, now opt-in); `--continue-on-error`
keeps going past a failed day instead of stopping immediately;
`--parallelism N` overrides `INGESTION_JOB_PARALLELISM` for each day's run.
Requires the AWS CLI and Docker Compose; run with `-h` for the full flag list.

### S3 deep store

Pushed and completed segments are stored in S3 (`controller.data.dir`), not on
the controller's local disk — servers and the minion fetch them from S3
directly (`pinot.*.segment.fetcher.protocols=file,http,s3`). The path is
per-environment, set via `PINOT_CONTROLLER_DATA_DIR` (`dynamic.env.config` in
`pinot-controller.conf`): `s3://gdansk-public-transport/pinot/deep-store/dev`
for `env.dev`, `s3://gdansk-public-transport/pinot/deep-store/prod` for
`env.prod` — kept separate so dev ingestion/segment churn never touches prod
data. The controller, server, minion and ingestion-runner containers
therefore all need the `aws_credentials` secret (already wired in the
compose file). 

### Load testing the broker

`cluster-setup/scripts/load_test_broker.py` fires N concurrent copies of a
representative analytical query (the all-routes punctuality aggregation behind
the per-category "Punctuality by route" dashboard charts)
against the broker and reports both client-observed wall-clock latency
and Pinot's own `timeUsedMs`, at p50/p95/p99/max:

```bash
python cluster-setup/scripts/load_test_broker.py --concurrency 100 --rounds 5
```

Watch Grafana's "Table Query Latency" panel
(`pinot_broker_queryExecution_{50,75,95,99,999}thPercentile`, already
provisioned) while it runs for the server-side view of the same thing.
`--broker` overrides the base URL (default `http://localhost:8099`); run with
`-h` for the full flag list. The broker port is not published by default —
bring it up on `127.0.0.1` first with the debug override in
[Port binding](#port-binding).

## Superset Dashboards

Dashboard definitions are version-controlled as YAML files under `cluster-setup/superset/dashboards/`. The Mapbox API key is **not** stored in those files — map charts use built-in Mapbox styles (e.g. `mapbox://styles/mapbox/streets-v9`), and Superset authenticates them at runtime via `MAPBOX_API_KEY` in `superset_config.py`, read from the `superset_mapbox_api_key` secret.

At init time, `superset-init` (via `superset-init.sh`) copies the YAML directory to a temp location, normalizes `metadata.yaml` to `type: assets` (UI exports write `type: Dashboard`, which the assets importer rejects), zips the result, imports it into Superset, and discards the zip. No zip file needs to exist before running init.

To add or update a dashboard:
1. Export from the Superset UI (Settings → Export).
2. Unzip the export and place the directory under `cluster-setup/superset/dashboards/`.
3. Commit the YAML directory. Do **not** commit the zip (it is gitignored).

### Re-importing after editing the dashboard YAML

The dashboards directory is volume-mounted into `superset-init`, so YAML changes need no image rebuild — just re-run the one-shot init container (swap `env.dev` → `env.prod` for prod):

```bash
docker compose --env-file cluster-setup/env/versions.env --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml --profile init run --rm -e SUPERSET_IMPORT_OVERWRITE=1 superset-init
```

`superset-init.sh` skips the import when the dashboard UUID already exists in Superset, so
an already-initialized deployment needs `SUPERSET_IMPORT_OVERWRITE=1` to re-import over it.
The variable is not declared in `container-compose.yml`'s `environment:` block, which is why
the command above uses `run --rm -e` rather than `up` — a shell-exported variable would not
reach the container. (Deleting the dashboard in the Superset UI first, then running plain
`up superset-init`, works too, but discards anything else attached to it.)

The container output should show `Importing dashboard.zip ... Imported successfully`;
`Skipping — dashboards already exist` means the old copy is still in place. Re-registering
for embedding is idempotent — the embedded UUID survives a re-import, so the web-app's
cached `_embedded_uuid` (`web-app/app/superset.py`) stays valid and needs no restart.

## Web App (port 3001)

The `web-app` service serves a single-page UI with two tabs:

- **Live Map** — Mapbox GL base map with a deck.gl scatter layer of current
  vehicle positions. The map is created once; every 10 s only the dot layer is
  refreshed from Pinot (latest position per vehicle over a 10-minute window),
  so the base map never re-renders and pan/zoom is preserved. This is the
  reason the map lives here rather than in a Superset chart — Superset remounts
  the whole map on every dashboard refresh.
  A route dropdown filters the markers to one line. While a line is selected
  the camera follows it: it fits all of the line's vehicles on selection and
  re-fits after every refresh (manual pan/zoom is overridden). Selecting
  "All vehicles" flies back to the initial center/zoom and the camera is
  hands-off again.
  Clicking a vehicle draws the trajectory of the trip it is serving, split at
  the vehicle: covered part grey, part ahead light blue. The split advances
  with each refresh; clicking the same vehicle again, another vehicle, or
  empty map clears/replaces the path (a vehicle between trips has no shape —
  the status line says so).
- **Analytics** — the "Gdansk Public Transport" Superset dashboard embedded via
  the Superset Embedded SDK (no Superset chrome, no login prompt).

The backend (stdlib Python, no dependencies) exposes:

| Endpoint           | Purpose                                                                                       |
|--------------------|-----------------------------------------------------------------------------------------------|
| `/api/positions`   | Proxies the positions query to the Pinot broker (avoids CORS)                                 |
| `/api/config`      | Hands the Mapbox token (from the `superset_mapbox_api_key` secret) to the browser             |
| `/api/guest-token` | Logs into Superset with the admin secrets and mints a guest token for the embedded dashboard  |
| `/api/stops`       | All stop poles (id, name, code, lat/lon) from the ZTM GTFS feed                               |
| `/api/departures`  | `?stopId=` — scheduled departures in the next 60 min (or the next 3 if none), adjusted by live delays from the GPS feed: a delayed vehicle stays listed until its estimated time passes |
| `/api/route-shape` | `?routeId=&tripId=` — today's trip trajectory (GeoJSON LineString coordinates) proxied from the ZTM shapes API, cached in memory per day; 404 if the trip has no shape today |
| `/api/stats`       | Total docs (broker `COUNT(*)` over the hybrid table), segment count and reported size (controller API) — feeds the header scale strip |
| `/api/heatmap`     | 24 h GPS ping density on a ~100 m grid, for the map's heatmap toggle |

The GTFS feed (`gtfsgoogle.zip`, ~20 MB) is downloaded on startup and every 6 h
by a background thread; only today's and tomorrow's service days are kept in
memory. Stops appear on the map from zoom 13; clicking one opens the
departures box. Until the first download finishes (seconds to ~a minute) the
two GTFS endpoints answer 503 and the UI retries quietly.

Embedding requires on the Superset side (all set up automatically):
`EMBEDDED_SUPERSET` feature flag, the `EmbeddedGuest` role (Gamma permissions +
datasource access, created by `superset-init.sh`), and CSP `frame-ancestors`
allowing the web-app origin.

### Browser-visible origins

Two env-file variables must be reachable from the **user's browser** (container
names like `superset:8088` never work there):

| Variable          | Used for                                  | Local default           |
|-------------------|-------------------------------------------|-------------------------|
| `SUPERSET_DOMAIN` | `src` of the embedded dashboard iframe    | `http://localhost:8088` |
| `WEBAPP_ORIGIN`   | Superset CSP `frame-ancestors` allowlist  | `http://localhost:3001` |

On EC2 (or any remote host) set both to the instance's public DNS/IP — see the
comments in `cluster-setup/env/env.prod`. If you reach the host through SSH
tunnels for ports 8088 and 3001, the localhost defaults are already correct.

### Running tests

Backend (`app/*` domain modules, no Pinot/Superset network access required —
everything is mocked):

```bash
python -m unittest discover -s cluster-setup/web-app/tests
```

Frontend geometry helpers (`static/js/geo.js`, Node's built-in test runner,
no dependencies):

```bash
node --test cluster-setup/web-app/tests/test_geo.js
```

### HTTPS and the reverse proxy

Caddy is the public entrypoint. It terminates TLS on 80/443 and forwards to the
four browser-facing services over the Docker network, so they need no host port
of their own:

| URL                          | Service          | Login |
|------------------------------|------------------|-------|
| `https://map.<domain>`       | web app          | none (public) |
| `https://bi.<domain>`        | Superset         | Superset's own |
| `https://ops.<domain>`       | Grafana          | Grafana's own |
| `https://pinot.<domain>`     | Pinot controller | HTTP basic auth at the proxy |

Two variables in the env files are the entire difference between a laptop and a
public host:

| Variable      | Local                 | EC2 / public                       |
|---------------|-----------------------|------------------------------------|
| `BASE_DOMAIN` | `localhost`           | a domain you own, e.g. `example.com` |
| `CADDY_TLS`   | `internal`            | your email, e.g. `you@example.com`  |

`internal` makes Caddy sign certificates with its own local certificate
authority; an email address switches the same `tls` directive to Let's Encrypt.
`SUPERSET_DOMAIN` and `WEBAPP_ORIGIN` must be changed to match — they are the
iframe `src` and the CSP `frame-ancestors` allowlist, and the browser has to be
able to reach both.

#### Local: no DNS, no hosts file

Browsers resolve any `*.localhost` name to 127.0.0.1 themselves, so
`https://map.localhost` works with no setup. The certificates are real but
signed by Caddy's local CA, which your browser does not know yet — so it shows
a warning, and, more importantly, **the Analytics tab stays blank**, because a
browser refuses to render an iframe whose certificate it does not trust.

Import the CA once to fix both (PowerShell as Administrator):

```powershell
Import-Certificate -FilePath cluster-setup\volumes\caddy\data\caddy\pki\authorities\local\root.crt -CertStoreLocation Cert:\LocalMachine\Root
oot.crt -CertStoreLocation Cert:\LocalMachine\Root
```

Then restart the browser. The CA lives in the `caddy-data` volume and survives
restarts, so this is a one-time step per machine — unless you delete that
volume, which regenerates the CA and requires re-importing.

#### EC2

1. Point an A record for `map`, `bi`, `ops` and `pinot` at the instance.
2. Set `BASE_DOMAIN`, `CADDY_TLS`, `SUPERSET_DOMAIN` and `WEBAPP_ORIGIN` in
   `env/env.prod`.
3. Security group: allow 80 and 443 only. Port 80 must stay open — Let's
   Encrypt's HTTP-01 challenge uses it, and Caddy redirects HTTP to HTTPS on it.

Let's Encrypt refuses to issue certificates for EC2's own
`*.compute.amazonaws.com` hostname, so a domain you own is not optional.

### Port binding

Caddy publishes 80 and 443 and is the only port that needs to be open to the
outside (see [HTTPS and the reverse proxy](#https-and-the-reverse-proxy)).
Three more services keep a `${BIND}` mapping for SSH tunnelling and debugging —
Caddy itself reaches them over the Docker network, not through those ports.
Everything else is called exclusively from inside the `pinot-network` bridge,
by container name (`zookeeper:2181`, `kafka:9092`, `pinot-broker:8099`, ...),
and has **no host mapping at all**:

| Published (see `BIND` below) | Not published                                       |
|------------------------------|-----------------------------------------------------|
| Web app 3001                 | ZooKeeper 2181, Kafka 9092, Schema Registry 8081    |
| Superset 8088                | Pinot Controller 9000, Broker 8099, Server 8097/8098, Minion 7500 |
| Grafana 3000                 | Prometheus 9090, Loki 3100, Alloy 12345             |
| —                            | JMX exporters 19092, 19000, 18099, 18098, 17500     |

None of the unpublished services has any authentication, which is exactly why
they stay inside the network. The JMX exporter ports have never been published
— Prometheus scrapes them over the bridge.

Where the three published ports listen is controlled by the `BIND` variable in
the env files:

| Env  | `BIND`      | Effect                                                             |
|------|-------------|--------------------------------------------------------------------|
| dev  | `0.0.0.0`   | Reachable from any machine on the network                          |
| prod | `127.0.0.1` | Bound to localhost only (SSH tunnel, or sit behind a reverse proxy) |

#### Reaching an unpublished port from the host

Four ports are covered by the debug override, which re-publishes them on
`127.0.0.1` only — Prometheus 9090 (its `/targets` page, the only view of
whether a JMX scrape is up), Alloy 12345 (the only view of whether Docker log
discovery is working), the Pinot broker 8099 (what
`scripts/load_test_broker.py` connects to by default) and the Pinot controller
9000 (its cluster UI, and what a host-run `add-tables.sh` or `server.py` points
`PINOT_CONTROLLER_URL` at). The controller is deliberately dev-only: its API
can delete tables and segments and has no authentication of its own, so the
only way in on a shared host is `https://pinot.<domain>` through Caddy, which puts
basic auth in front of it:

```bash
docker compose --env-file cluster-setup/env/versions.env --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml -f cluster-setup/container/container-compose.debug-ports.yml up -d
```

Never use it on a shared or internet-facing host.

Nothing else is in the override by design. Loki is already queryable through
Grafana, which is published; ZooKeeper, Schema Registry and the Pinot
server/minion expose CLI-shaped admin APIs, reachable with `docker exec` or
from a throwaway container on the bridge:

```bash
docker run --rm --network pinot-network curlimages/curl -s http://pinot-server:8097/health
```

Kafka is deliberately absent from that override: `KAFKA_ADVERTISED_LISTENERS`
is `INTERNAL://kafka:9092`, so a host client is handed an address it cannot
resolve — publishing 9092 never gave working external access. Use
`docker exec kafka ...` instead (see [Verifying Kafka messages](#verifying-kafka-messages)).
