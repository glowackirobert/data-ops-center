# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Data Operations Center is a data engineering platform that:
- streams data through Kafka into realtime Apache Pinot tables,
- ingests offline data from S3 bucket into Apache Pinot offline tables. 

BI dashboards are served by Apache Superset (connected to Pinot). 
Monitoring is provided by Prometheus + Grafana.

Runnable commands live in the READMEs — do not duplicate them here:
- `cluster-setup/README-docker.md` — secrets setup, custom image builds, dev/prod start/stop, Kafka verification, Pinot table registration, S3 batch ingestion, Superset dashboards, web app
- `k8s/README.md` — Kubernetes deployment (Kustomize)
- `cluster-setup/README-lambda.md` — AWS Lambda packaging/deployment
- `kafka-producer-app/README-docker.md` — building and running the Kafka producer app
- `BUSINESS_OVERVIEW.md` — architecture and use cases

## Modules

| Directory             | Language               | Purpose                                                                                                                                                           |
|-----------------------|------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `kafka-producer-app/` | Java 21 / Maven        | Kafka producer that publishes Avro-serialized `Trade` events                                                                                                      |
| `cluster-setup/`      | Docker Compose, shell  | Infrastructure: Kafka, Schema Registry, Pinot, Superset, Prometheus, Grafana                                                                                      |
| `k8s/`                | Kubernetes / Kustomize | Kubernetes manifests for Zookeeper, Kafka (KRaft mode), Apache Pinot                                                                                              |
| `cluster-setup/py/`   | Python                 | Gdansk GPS fetchers: `_aws.py` (Lambda → S3), `_kafka.py` (streams to Kafka, runs as compose service), `_s3_compaction.py` (merges raw S3 files into daily gzips) |
| `cluster-setup/web-app/` | Python (stdlib), HTML | Live vehicle map (Mapbox GL + deck.gl); flicker-free 30 s refresh — only the dot layer updates. Proxies queries to the Pinot broker                            |

## Running the Cluster (Docker Compose)

All commands are in `cluster-setup/README-docker.md`. Key facts:

- All Docker image versions live in `cluster-setup/env/versions.env` — the single source of truth, read by the compose commands (first `--env-file`), the README build commands, and the CI workflow. Exception: `kafka-producer-app` is versioned by its `pom.xml`. The k8s manifests do **not** read it.
- The `secrets/` directory (`cluster-setup/container/secrets/`) is gitignored and must be populated before first run — the README lists the required files.
- Custom images (`apache-pinot`, `superset`, `kafka-producer-app`, `web-app`) must be built locally before first start in **dev mode** (`env.dev`, `DOCKER_IMAGE_BASE_PATH` empty). **Prod mode** (`env.prod`) pulls them from Docker Hub `robertglowacki83/` — built and pushed automatically by `.github/workflows/docker-ci.yml` on pushes to `master` that touch image inputs.
- Files are baked into the custom images at build time: `cluster-setup/table_config/` and `cluster-setup/pinot/` into `apache-pinot`, `cluster-setup/web-app/server.py`, `cluster-setup/web-app/app/`, and `cluster-setup/web-app/static/` into `web-app` — rebuild the image after changing them.
- `--profile init` additionally runs the one-shot seeding containers: `kafka-topic-init`, `pinot-table-registrar` (registers schemas/tables), `kafka-producer`, `pinot-ingestion-runner` (S3 batch ingestion), `superset-init`.

Service ports and dev/prod bind behaviour are documented in `cluster-setup/README-docker.md`.

## Kafka

Two topics, created by the `kafka-topic-init` init container:

- **`trade`** — Avro-serialized trade events from `kafka-producer-app` (Schema Registry holds the schema)
- **`gdansk-public-transport`** — JSON vehicle positions from the Gdansk GPS producer

The `gdansk-public-transport-kafka-producer` compose service runs `cluster-setup/py/gdansk_public_transport_kafka.py`: it polls the Gdansk API every 10 s and publishes only changed vehicle positions, keyed by `vehicleId`. It starts with the init profile but is not one-shot — `restart: unless-stopped` keeps it running afterwards, even across daemon restarts. Consequences: a plain `up` on a fresh machine will not start the GPS feed (the map stays empty until `--profile init up` has run once), and a plain `down` will not remove it — stop it with `docker stop gdansk-public-transport-kafka-producer` or run `down` with `--profile init`.

## Kafka Producer App (`kafka-producer-app/`)

Build with `mvn package` (`-DskipTests` to skip tests), from repo root or `kafka-producer-app/`.

- The Avro Maven plugin auto-generates Java classes from `src/main/avro/Trade.avsc` into `src/main/java/avro/` during the `generate-sources` phase. Never edit these generated files directly — edit the `.avsc` schema instead.
- The JAR entry point is `KafkaProducerApp`. Dependencies land in `target/lib/`. The app runs in container mode only — configuration is loaded from `kafka-producer.properties` on the classpath.
- It runs 5 iterations of 1,000,000 messages each across 2 threads, flushing every 10,000 messages. Message volume is controlled by constants in `KafkaCustomTopicProducer.java`.

## Apache Pinot Tables

Three tables are managed by `cluster-setup/table_config/add-tables.sh`, run automatically by the `pinot-table-registrar` init container (manual re-registration curls are in `cluster-setup/README-docker.md`):

- **`trade` (REALTIME)** — consumes from the Kafka `trade` topic; schema in `trade_table_schema.json`
- **`gdansk_public_transport` (REALTIME)** — consumes from the Kafka `gdansk-public-transport` topic; JSON-decoded; 1-day retention; schema in `gdansk_public_transport_table_schema.json`
- **`gdansk_public_transport` (OFFLINE)** — batch-ingested from S3; schema in `gdansk_public_transport_table_schema.json`

Segments live in the **S3 deep store** (`controller.data.dir=s3://gdansk-public-transport/pinot/deep-store`); the controller, server and minion all have the S3 filesystem + `s3` segment fetcher configured and receive the `aws_credentials` secret.

### S3 Batch Ingestion

The `pinot-ingestion-runner` init container launches a batch ingestion job over every file under `squashed/` (a mix of day and month files — see above). `INGESTION_DATE` is an optional filename glob substituted into the `${DATE}` placeholder in the job spec: unset = full backfill of everything under `squashed/`, or narrow to a day/month/year (`2026-02-01`, `2026-02`, `2026-06-*`, `2025-*`). Segment names are derived from the input file name, so re-runs overwrite rather than duplicate — except ingesting a month file whose days were already ingested individually, which produces a differently-named segment alongside the old day segments and must have those deleted manually to avoid double-counting. S3 paths, staging volume, and manual run commands are in `cluster-setup/README-docker.md`.

## Superset

- Dashboards are version-controlled as YAML under `cluster-setup/superset/dashboards/`. Map charts use built-in Mapbox styles authenticated at runtime via `MAPBOX_API_KEY` (from the `superset_mapbox_api_key` secret) — no key is stored in the YAML.
- `superset-init.sh` normalizes `metadata.yaml` to `type: assets` before import, since UI exports write `type: Dashboard`, which the assets importer rejects. It also creates the `EmbeddedGuest` role (Gamma permissions + datasource access) and registers dashboards for embedding.
- Sample Pinot queries live in `cluster-setup/table_config/gdansk_public_transport_queries.sql`.

## Web App (`cluster-setup/web-app/`)

Serves two tabs:

- **Live Map** — Mapbox GL base map created once; every 30 s only the deck.gl dot layer refreshes from Pinot, so pan/zoom is preserved. This is the reason the map lives here and not in a Superset chart. Exception: while a route is selected in the dropdown, the camera fits that line's vehicles on selection and re-fits on every refresh; picking "All vehicles" flies back to the initial center/zoom. Selecting a route also loads a small "avg delay by hour" bar chart (whole table history, uncached — a showcase of `routeShortName` being the sorted column keeping a full-history query fast) in the bottom-left panel. Clicking a vehicle draws its current trip's trajectory split at the vehicle (covered grey, ahead light blue), recomputed each refresh, truncated to the trip's scheduled last stop (the raw ZTM shape commonly overshoots it — loop-back curves, depot access roads); an off-route vehicle (>150 m from the shape, e.g. at a depot) gets no covered part and the line starts at its route's nearest stop. A pulsing magenta ring highlights the selected vehicle's badge. Heading arrows hide only for vehicles standing at a terminus of their own course (schedule-derived: within 300 m of the trip's first/last stop — the `atTerminus` flag on `/api/positions`); stops with no scheduled departures render grey ("Stop not in use"). A heatmap toggle swaps in a 24 h GPS-density layer in place of live vehicles (shown together, the hottest spots — usually the same busy clusters — buried the vehicle badges). Every panel backed by a Pinot query shows a latency badge (Pinot's own `timeUsedMs`, sent as an `X-Pinot-Time-Ms` response header rather than folded into the JSON body, so no consumer's parsing has to change).
- **Analytics** — the Superset dashboard embedded via the Superset Embedded SDK.

Backend endpoints: `/api/positions` (proxies the Pinot broker query, avoids CORS), `/api/config` (hands the Mapbox token to the browser), `/api/guest-token` (logs into Superset with the admin secrets and mints a guest token for the embedded dashboard), `/api/stops` and `/api/departures?stopId=` (stop poles and scheduled departures from the ZTM GTFS feed, downloaded by a background thread on startup and every 6 h; 503 until first load), `/api/route-shape?routeId=&tripId=` (today's trip trajectory from the ZTM shapes API, cached per day, truncated to the trip's scheduled last stop), `/api/stats` (total docs / segments / size for the Analytics-tab storage strip, from the broker + controller APIs, cached 60 s), `/api/heatmap` (24 h GPS ping density on a ~100 m grid, cached 5 min), `/api/route-delay-histogram?route=` (avg delay per hour-of-day for one route, whole table history, not cached — validates `route` against `^[A-Za-z0-9]{1,10}$` before interpolating into SQL, since the Pinot broker's SQL endpoint has no parameterized-query option). Every endpoint backed by a live broker query sends its Pinot `timeUsedMs` as an `X-Pinot-Time-Ms` response header.

Embedding requires the `EMBEDDED_SUPERSET` feature flag, the `EmbeddedGuest` role, and CSP `frame-ancestors` allowing the web-app origin — all set up automatically.

Backend code is split into `server.py` (thin HTTP routing) and `app/` (domain modules: `config`, `http`, `http_client`, `cache`, `gtfs`, `pinot`, `superset`, `geo`); frontend assets live under `static/` (`index.html` shell, `css/styles.css`, `js/{app,map,dashboard,geo,utils}.js`). Tests are under `tests/` — `python -m unittest discover -s cluster-setup/web-app/tests` for the Python modules, `node --test cluster-setup/web-app/tests/test_geo.js cluster-setup/web-app/tests/test_utils.js` for the frontend geometry and latency-badge helpers (list both files explicitly — the directory also holds the Python tests, which trips up Node's test-file auto-discovery).

Two env-file variables must be reachable from the **user's browser** (container names never work there): `SUPERSET_DOMAIN` (`src` of the embedded dashboard iframe, local default `http://localhost:8088`) and `WEBAPP_ORIGIN` (Superset CSP `frame-ancestors` allowlist, local default `http://localhost:3001`). On EC2 or any remote host set both to the instance's public DNS/IP — see the comments in `cluster-setup/env/env.prod`; the localhost defaults are already correct when tunneling ports 8088 and 3001 over SSH.

## Monitoring

Prometheus scrapes JMX metrics from Kafka (port 19092) and from each Pinot component via their respective JMX exporter ports. The JMX config lives in `cluster-setup/jmx_exporter/kafka_jmx_config.yml`. Grafana dashboards are provisioned from `cluster-setup/grafana/`.

Logs are handled separately from metrics: **Grafana Alloy** (`cluster-setup/alloy/config.alloy`) discovers containers via the Docker socket — scoped to this compose project via a `com.docker.compose.project` filter — and ships stdout/stderr to **Loki** (`cluster-setup/loki/loki-config.yml`, filesystem storage, 7-day retention), which Grafana queries via LogQL through a second provisioned datasource (`cluster-setup/grafana/provisioning/datasources/loki.yaml`). Alloy was chosen over Promtail (EOL March 2026) specifically because it also has native Kubernetes service discovery — when the monitoring stack moves to `k8s/`, only the discovery block changes (`discovery.kubernetes` instead of `discovery.docker`); the `loki.write` sink is unaffected. Grafana's default home dashboard (`GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH`, set in `container-compose.yml`) is the provisioned `cluster-setup/grafana/dashboards/logs_dashboard.json` — a Logs panel with `container`/`stream` dropdown variables, so opening Grafana lands directly on a browsable, filterable log view instead of the Pinot metrics dashboard.

## Kubernetes

The `k8s/` directory uses Kustomize with base + overlays (dev/prod); deployment commands are in `k8s/README.md`. Kafka runs in KRaft mode. The Kafka pods use a custom `kafka-jmx:4.3.1` image that has the JMX agent baked in (built by `k8s/02-kafka/build.sh`). `KAFKA_NODE_ID` and `KAFKA_ADVERTISED_LISTENERS` are injected per-pod by an init container at runtime, based on the pod's ordinal index.

## AWS Lambda & S3 Data (Gdansk Public Transport)

- The Lambda (`cluster-setup/py/gdansk_public_transport_aws.py`, handler `lambda_handler`) fetches GPS positions from the Gdansk public transport API and stores JSON-lines files in S3 bucket `gdansk-public-transport` under `raw/YYYY/MM/DD/YYYY-MM-DD-HH-MM.txt`. Deployment is automated by `.github/workflows/lambda-function.yaml`; manual packaging steps are in `cluster-setup/README-lambda.md`.
- The `gdansk_public_transport_s3_compaction.py` script has two subcommands, both run by `.github/workflows/compact-s3.yml` (nightly for `daily`, monthly on the 1st for `monthly`; manual dispatch picks the subcommand and a date/month range for backfills) and both writing into the same `squashed/` prefix: `daily` merges each day's raw S3 files into a single `squashed/YYYY/YYYY-MM-DD.json.gz` object; `monthly` rolls a fully-elapsed month's `squashed/` day files into one `squashed/YYYY/YYYY-MM.json.gz` object **and deletes the day files it just combined**, so `squashed/` never holds both a month and its now-redundant days — batch ingestion can read every file there as-is, one segment per file, without a separate step to reconcile granularities. This only prevents day/month files from coexisting in *S3*; if a month's days were already batch-ingested into Pinot before being squashed, ingesting the new month file still adds a second, differently-named segment alongside the old day segments — delete those manually via the controller API to avoid double-counting.
