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

- The `secrets/` directory (`cluster-setup/container/secrets/`) is gitignored and must be populated before first run — the README lists the required files.
- Custom images (`apache-pinot`, `superset`, `kafka-producer-app`, `web-app`) must be built locally before first start in **dev mode** (`env.dev`, `DOCKER_IMAGE_BASE_PATH` empty). **Prod mode** (`env.prod`) pulls them from Docker Hub `robertglowacki83/` — built and pushed automatically by `.github/workflows/docker-ci.yml` on pushes to `master` that touch image inputs.
- Files are baked into the custom images at build time: `cluster-setup/table_config/` and `cluster-setup/pinot/` into `apache-pinot`, `cluster-setup/web-app/server.py` and `cluster-setup/web-app/index.html` into `web-app` — rebuild the image after changing them.
- `--profile init` additionally runs the one-shot seeding containers: `kafka-topic-init`, `pinot-command-runner` (registers schemas/tables), `kafka-producer`, `pinot-ingestion-runner` (S3 batch ingestion), `superset-init`.

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

Three tables are managed by `cluster-setup/table_config/add-tables.sh`, run automatically by the `pinot-command-runner` init container (manual re-registration curls are in `cluster-setup/README-docker.md`):

- **`trade` (REALTIME)** — consumes from the Kafka `trade` topic; schema in `trade_table_schema.json`
- **`gdansk_public_transport` (REALTIME)** — consumes from the Kafka `gdansk-public-transport` topic; JSON-decoded; 7-day retention; schema in `gdansk_public_transport_table_schema.json`
- **`gdansk_public_transport` (OFFLINE)** — batch-ingested from S3; schema in `gdansk_public_transport_table_schema.json`

The OFFLINE table config contains a minion task schedule, so the controller rejects it until a Pinot minion has registered — `pinot-command-runner` therefore depends on `pinot-minion` being healthy in the compose file.

### S3 Batch Ingestion

The `pinot-ingestion-runner` init container launches a batch ingestion job for the single day selected by the `INGESTION_DATE` env var (substituted into the `${DATE}` placeholder in the job spec). S3 paths, segment naming, and manual run commands are in `cluster-setup/README-docker.md`.

## Superset

- Dashboards are version-controlled as YAML under `cluster-setup/superset/dashboards/`. Map charts use built-in Mapbox styles authenticated at runtime via `MAPBOX_API_KEY` (from the `superset_mapbox_api_key` secret) — no key is stored in the YAML.
- `superset-init.sh` normalizes `metadata.yaml` to `type: assets` before import, since UI exports write `type: Dashboard`, which the assets importer rejects. It also creates the `EmbeddedGuest` role (Gamma permissions + datasource access) and registers dashboards for embedding.
- Sample Pinot queries live in `cluster-setup/table_config/gdansk_public_transport_queries.sql`.

## Web App (`cluster-setup/web-app/`)

Serves two tabs:

- **Live Map** — Mapbox GL base map created once; every 30 s only the deck.gl dot layer refreshes from Pinot, so pan/zoom is preserved. This is the reason the map lives here and not in a Superset chart.
- **Analytics** — the Superset dashboard embedded via the Superset Embedded SDK.

Backend endpoints: `/api/positions` (proxies the Pinot broker query, avoids CORS), `/api/config` (hands the Mapbox token to the browser), `/api/guest-token` (logs into Superset with the admin secrets and mints a guest token for the embedded dashboard).

Embedding requires the `EMBEDDED_SUPERSET` feature flag, the `EmbeddedGuest` role, and CSP `frame-ancestors` allowing the web-app origin — all set up automatically.

Two env-file variables must be reachable from the **user's browser** (container names never work there): `SUPERSET_DOMAIN` (`src` of the embedded dashboard iframe, local default `http://localhost:8088`) and `WEBAPP_ORIGIN` (Superset CSP `frame-ancestors` allowlist, local default `http://localhost:3001`). On EC2 or any remote host set both to the instance's public DNS/IP — see the comments in `cluster-setup/env/env.prod`; the localhost defaults are already correct when tunneling ports 8088 and 3001 over SSH.

## Monitoring

Prometheus scrapes JMX metrics from Kafka (port 19092) and from each Pinot component via their respective JMX exporter ports. The JMX config lives in `cluster-setup/jmx_exporter/kafka_jmx_config.yml`. Grafana dashboards are provisioned from `cluster-setup/grafana/`.

## Kubernetes

The `k8s/` directory uses Kustomize with base + overlays (dev/prod); deployment commands are in `k8s/README.md`. Kafka runs in KRaft mode. The Kafka pods use a custom `kafka-jmx:4.1.1` image that has the JMX agent baked in (built by `k8s/02-kafka/build.sh`). `KAFKA_NODE_ID` and `KAFKA_ADVERTISED_LISTENERS` are injected per-pod by an init container at runtime, based on the pod's ordinal index.

## AWS Lambda & S3 Data (Gdansk Public Transport)

- The Lambda (`cluster-setup/py/gdansk_public_transport_aws.py`, handler `lambda_handler`) fetches GPS positions from the Gdansk public transport API and stores JSON-lines files in S3 bucket `gdansk-public-transport` under `raw/YYYY/MM/DD/HH-MM.txt`. Deployment is automated by `.github/workflows/lambda-function.yaml`; manual packaging steps are in `cluster-setup/README-lambda.md`.
- The `gdansk_public_transport_s3_compaction.py` script merges each day's raw S3 files into a single `daily/YYYY/YYYY-MM-DD.json.gz` object; it runs nightly via `.github/workflows/compact-s3-daily.yml` (manual dispatch with a date range for backfills).
