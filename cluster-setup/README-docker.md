# Cluster Setup

Docker Compose based setup for the Data Ops Center cluster.

## Services

### Core services (always running)

| Service               | Port | Description                                                                                   |
|-----------------------|------|-----------------------------------------------------------------------------------------------|
| Zookeeper             | 2181 | Coordination service required by Apache Pinot.                                                |
| Kafka                 | 9092 | Message broker in KRaft mode.                                                                 |
| Schema Registry       | 8081 | Confluent Schema Registry for Avro schema management.                                         |
| Pinot Controller /UI/ | 9000 | Manages cluster metadata, table configs and segment assignment.                               |
| Pinot Broker          | 8099 | Accepts SQL queries and routes them to the appropriate servers.                               |
| Pinot Server /API/    | 8097 | Stores and serves segments.                                                                   |
| Pinot Server /query/  | 8098 | Stores and serves segments.                                                                   |
| Pinot Minion          | 7500 | Background task executor for offline segment operations.                                      |
| Prometheus            | 9090 | Scrapes JMX metrics from Kafka and all Pinot components.                                      |
| Grafana               | 3000 | Dashboards over Prometheus metrics.                                                           |
| Superset              | 8088 | BI and data exploration UI connected to Pinot via `pinotdb`.                                  |
| Web app               | 3001 | Live vehicle map (flicker-free 30 s refresh) + Analytics tab embedding the Superset dashboard |

### Init containers (run once, `--profile init`)

| Container                                | Description                                                                                                                                                                                                         |
|------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `kafka-topic-init`                       | Creates Kafka topics.                                                                                                                                                                                               |
| `kafka-producer`                         | Publishes Avro-serialized `trade` events to Kafka.                                                                                                                                                                  |
| `pinot-command-runner`                   | Registers schemas and tables with the Pinot Controller.                                                                                                                                                             |
| `gdansk-public-transport-kafka-producer` | Fetches current GPS positions from the Gdansk public transport API every 10 s and publishes changed vehicle positions as JSON to the `gdansk-public-transport` topic. Keeps running (`restart: unless-stopped`).    |
| `pinot-ingestion-runner`                 | Runs a batch ingestion job that reads from S3 and pushes segments to Pinot.                                                                                                                                         |
| `superset-init`                          | Runs DB migrations, creates the admin user, initialises Superset roles, registers the Apache Pinot database connection, imports dashboards, creates the `EmbeddedGuest` role and registers dashboards for embedding |

Init containers are one-shot — they exit after completing their task. Run them once on first setup, or whenever you need to re-seed the cluster.

The exception is `gdansk-public-transport-kafka-producer`: it starts with the
init profile but then polls forever, and `restart: unless-stopped` keeps the
container alive across daemon restarts — even when compose is later run
without the profile. Consequences: a plain `up` on a fresh machine will not
start the GPS feed (the map shows no vehicles until `--profile init up` has
run once), and a plain `down` will not remove it — stop it with
`docker stop gdansk-public-transport-kafka-producer` or run `down` with
`--profile init`.



## Requirements

- Docker with Docker Compose



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



## Custom Images

Services that use custom-built images must be built before first run in dev mode:

```bash
# Apache Pinot — adds table configs, JMX exporter and ingestion scripts
docker build -t apache-pinot:1.4.0 -f cluster-setup/container/Dockerfile.apache-pinot .

# Apache Superset — adds pinotdb driver on top of the official image
docker build -t superset:4.1.2 -f cluster-setup/container/Dockerfile.superset .

# Kafka producer app — Maven multi-stage build of the Java Avro producer
docker build -t kafka-producer-app:1.0.0 -f kafka-producer-app/Dockerfile.kafka-producer-app .

# Web app — stdlib-only Python server serving the vehicle map UI
docker build -t web-app:1.0.0 -f cluster-setup/container/Dockerfile.web-app .
```

For prod - images are pulled from Docker Hub `robertglowacki83/` — 
Images are built and pushed automatically by `.github/workflows/docker-ci.yml` on pushes to `master` that touch image inputs.



## Running the Cluster

All commands use an env file to switch between dev and prod behaviour (port bindings, image sources, JVM heap sizes).

JVM heaps (Zookeeper, Kafka, Schema Registry, all Pinot components and runners) are set per environment via the `*_HEAP` variables in the env files — dev uses small laptop-friendly sizes, prod the full sizes. Unset variables fall back to prod-sized defaults in `container-compose.yml`. Zookeeper's is `ZOOKEEPER_HEAP_MB` (a number in MB, its image's convention); all others take JVM flags like `-Xms256M -Xmx1G`.

### Development

```bash
# Start core services
docker compose --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml up

# First-time setup — also run init containers
docker compose --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml --profile init up

# Stop
docker compose --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml down
```

### Production

```bash
# Start core services, pull latest images
docker compose --env-file cluster-setup/env/env.prod -f cluster-setup/container/container-compose.yml up --pull always -d

# First-time setup — also run init containers
docker compose --env-file cluster-setup/env/env.prod -f cluster-setup/container/container-compose.yml --profile init up --pull always

# Stop
docker compose --env-file cluster-setup/env/env.prod -f cluster-setup/container/container-compose.yml down
```

### Verifying Kafka messages

```bash
docker exec -it kafka /bin/bash -c "env -u KAFKA_OPTS /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic trade --from-beginning"
```

## Pinot Tables

Schemas and table configs live in `cluster-setup/table_config/` and are registered
automatically by the `pinot-command-runner` init container (`add-tables.sh`).
To re-register them against a running controller directly:

```bash
curl -X POST -H "Content-Type: application/json" -d @cluster-setup/table_config/gdansk_public_transport_table_schema.json http://localhost:9000/schemas
curl -X POST -H "Content-Type: application/json" -d @cluster-setup/table_config/gdansk_public_transport_offline_table_config.json http://localhost:9000/tables
curl -X POST -H "Content-Type: application/json" -d @cluster-setup/table_config/gdansk_public_transport_realtime_table_config.json http://localhost:9000/tables
curl -X POST -H "Content-Type: application/json" -d @cluster-setup/table_config/trade_table_schema.json http://localhost:9000/schemas
curl -X POST -H "Content-Type: application/json" -d @cluster-setup/table_config/trade_table_config.json http://localhost:9000/tables
```

## S3 Batch Ingestion

The `gdansk_public_transport_ingestion_job_spec.json` spec reads the compacted daily file `s3://gdansk-public-transport/daily/YYYY/YYYY-MM-DD.json.gz` and pushes one segment per day, named `gdansk_public_transport_YYYY-MM-DD`, to the offline Pinot table.

Pass the date via `INGESTION_DATE` (format `YYYY-MM-DD`). The compose command substitutes it into the spec before running. The daily file is produced by the nightly S3 compaction workflow, so only completed (already-compacted) days can be ingested.

```bash
# Run against an already-running cluster (skip init dependencies)
INGESTION_DATE=2026-06-29 docker compose \
  --env-file cluster-setup/env/env.prod \
  -f cluster-setup/container/container-compose.yml \
  --profile init \
  run --no-deps pinot-ingestion-runner
```

To change the default date, edit `INGESTION_DATE` in `cluster-setup/env/env.prod`.

It is safe to re-run — segments are named by date and overwritten, not duplicated.

## Superset Dashboards

Dashboard definitions are version-controlled as YAML files under `cluster-setup/superset/dashboards/`. The Mapbox API key is **not** stored in those files — map charts use built-in Mapbox styles (e.g. `mapbox://styles/mapbox/streets-v9`), and Superset authenticates them at runtime via `MAPBOX_API_KEY` in `superset_config.py`, read from the `superset_mapbox_api_key` secret.

At init time, `superset-init` (via `superset-init.sh`) copies the YAML directory to a temp location, normalizes `metadata.yaml` to `type: assets` (UI exports write `type: Dashboard`, which the assets importer rejects), zips the result, imports it into Superset, and discards the zip. No zip file needs to exist before running init.

To add or update a dashboard:
1. Export from the Superset UI (Settings → Export).
2. Unzip the export and place the directory under `cluster-setup/superset/dashboards/`.
3. Commit the YAML directory. Do **not** commit the zip (it is gitignored).

## Web App (port 3001)

The `web-app` service serves a single-page UI with two tabs:

- **Live Map** — Mapbox GL base map with a deck.gl scatter layer of current
  vehicle positions. The map is created once; every 30 s only the dot layer is
  refreshed from Pinot (latest position per vehicle over a 10-minute window),
  so the base map never re-renders and pan/zoom is preserved. This is the
  reason the map lives here rather than in a Superset chart — Superset remounts
  the whole map on every dashboard refresh.
- **Analytics** — the "Gdansk Public Transport" Superset dashboard embedded via
  the Superset Embedded SDK (no Superset chrome, no login prompt).

The backend (stdlib Python, no dependencies) exposes:

| Endpoint           | Purpose                                                                                       |
|--------------------|-----------------------------------------------------------------------------------------------|
| `/api/positions`   | Proxies the positions query to the Pinot broker (avoids CORS)                                 |
| `/api/config`      | Hands the Mapbox token (from the `superset_mapbox_api_key` secret) to the browser             |
| `/api/guest-token` | Logs into Superset with the admin secrets and mints a guest token for the embedded dashboard  |

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

### Port binding

Controlled by the `BIND` variable in the env files:

| Env  | `BIND`      | Effect                                                         |
|------|-------------|----------------------------------------------------------------|
| dev  | `0.0.0.0`   | All ports reachable from any machine on the network            |
| prod | `127.0.0.1` | All ports bound to localhost only (sit behind a reverse proxy) |
