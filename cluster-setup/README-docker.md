# Cluster Setup

Docker Compose based setup for the Data Ops Center cluster.

## Services

### Core services (always running)

| Service               | Port | Description                                                                                                  |
|-----------------------|------|--------------------------------------------------------------------------------------------------------------|
| Zookeeper             | 2181 | Coordination service required by Apache Pinot.                                                               |
| Kafka                 | 9092 | Message broker in KRaft mode.                                                                                |
| Schema Registry       | 8081 | Confluent Schema Registry for Avro schema management.                                                        |
| Pinot Controller /UI/ | 9000 | Manages cluster metadata, table configs and segment assignment.                                              |
| Pinot Broker          | 8099 | Accepts SQL queries and routes them to the appropriate servers.                                              |
| Pinot Server /API/    | 8097 | REST admin API for health checks and segment management, called by the Controller.                           |
| Pinot Server /query/  | 8098 | Internal netty port the Broker uses to send queries to and read results from the server.                     |
| Pinot Minion          | 7500 | Background task executor, driven by Controller-scheduled jobs, responsible for segment lifecycle operations. |
| Prometheus            | 9090 | Scrapes JMX metrics from Kafka and all Pinot components.                                                     |
| Grafana               | 3000 | Dashboards over Prometheus metrics and Loki logs.                                                            |
| Loki                  | 3100 | Log storage/query backend (LogQL).                                                                           |
| Alloy                 | 12345 | Tails every container's stdout/stderr via the Docker socket and ships it to Loki; `12345` serves its debug UI (component graph, live pipeline state). |
| Superset              | 8088 | BI and data exploration UI connected to Pinot via `pinotdb`.                                                 |
| Web app               | 3001 | Live vehicle map (flicker-free 30 s refresh) + Analytics tab embedding the Superset dashboard                |

### Init containers (run once, `--profile init`)

| Container                                | Description                                                                                                                                                                                                           |
|------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `kafka-topic-init`                       | Creates Kafka topics.                                                                                                                                                                                                 |
| `kafka-producer`                         | Publishes Avro-serialized `trade` events to Kafka.                                                                                                                                                                    |
| `pinot-table-registrar`                   | Registers schemas and tables with the Pinot Controller.                                                                                                                                                               |
| `gdansk-public-transport-kafka-producer` | Fetches current GPS positions from the Gdansk public transport API every <br/>10 s and publishes changed vehicle positions as JSON to the `gdansk-public-transport` topic. Keeps running (`restart: unless-stopped`). |
| `pinot-ingestion-runner`                 | Runs a batch ingestion job that reads from S3 and pushes segments to Pinot.                                                                                                                                           |
| `superset-init`                          | Runs DB migrations, creates the admin user, initialises Superset roles, registers the Apache Pinot database connection, imports dashboards, creates the `EmbeddedGuest` role and registers dashboards for embedding   |

Init containers are one-shot — they exit after completing their task. Run them once on first setup, or whenever you need to re-seed the cluster.

The exception is `gdansk-public-transport-kafka-producer`: it starts with the
init profile but then polls forever, and `restart: unless-stopped` keeps the
container alive across daemon restarts — even when compose is later run
without the profile. Consequences: a plain `up` on a fresh machine will not
start the GPS feed (the map shows no vehicles until `--profile init up` has
run once), and a plain `down` will not remove it — stop it with
`docker stop gdansk-public-transport-kafka-producer` or run `down` with
`--profile init`.

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

Alloy's own debug UI (component graph, live pipeline state) is at
`http://localhost:12345`. Log retention in Loki is 168h (7 days), matching
Kafka's `KAFKA_LOG_RETENTION_HOURS`.



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
```

The `secrets/` directory is gitignored — these files must be created manually on every machine.



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
edits to a running controller directly, run the same script from the host:

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

The `gdansk_public_transport_ingestion_job_spec.json` spec reads every compacted file under
`s3://gdansk-public-transport/squashed/` and pushes one segment per file to the offline Pinot
table, named `gdansk_public_transport_<file stem>`. `squashed/` holds a mix of granularities —
day files (`YYYY/YYYY-MM-DD.json.gz` → `gdansk_public_transport_YYYY-MM-DD`) for recent,
not-yet-rolled-up history, and month files (`YYYY/YYYY-MM.json.gz` → `gdansk_public_transport_YYYY-MM`)
for fully-elapsed months — but never both for the same range: the `monthly` compaction
subcommand deletes a month's day files once it's squashed them into one month file,
so ingestion never has to know or care which granularity a given file is.

`INGESTION_DATE` is a **filename glob** substituted into the job spec's `${DATE}` placeholder:
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
representative analytical query (the P3 "Route punctuality" dashboard chart's
shape) against the broker and reports both client-observed wall-clock latency
and Pinot's own `timeUsedMs`, at p50/p95/p99/max:

```bash
python cluster-setup/scripts/load_test_broker.py --concurrency 100 --rounds 5
```

Watch Grafana's "Table Query Latency" panel
(`pinot_broker_queryExecution_{50,75,95,99,999}thPercentile`, already
provisioned) while it runs for the server-side view of the same thing.
`--broker` overrides the base URL (default `http://localhost:8099`); run with
`-h` for the full flag list.

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
docker compose --env-file cluster-setup/env/versions.env --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml --profile init up superset-init
```

`superset-init.sh` skips the import when the dashboard UUID already exists in Superset, 
so on an already-initialized deployment first delete the dashboard in the Superset UI 
(Dashboards → trash icon). The container output should show `Importing dashboard.zip 
... Imported successfully`; `Skipping — dashboards already exist` means the old 
copy is still in place.

## Web App (port 3001)

The `web-app` service serves a single-page UI with two tabs:

- **Live Map** — Mapbox GL base map with a deck.gl scatter layer of current
  vehicle positions. The map is created once; every 30 s only the dot layer is
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
| `/api/stats`       | Total docs (broker `COUNT(*)` over the hybrid table), segment count and reported size (controller API) — feeds the header scale strip; cached 60 s |
| `/api/heatmap`     | 24 h GPS ping density on a ~100 m grid, for the map's heatmap toggle; cached 5 min |

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

### Port binding

Controlled by the `BIND` variable in the env files:

| Env  | `BIND`      | Effect                                                         |
|------|-------------|----------------------------------------------------------------|
| dev  | `0.0.0.0`   | All ports reachable from any machine on the network            |
| prod | `127.0.0.1` | All ports bound to localhost only (sit behind a reverse proxy) |
