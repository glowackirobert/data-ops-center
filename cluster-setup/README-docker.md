# Cluster Setup

Docker Compose based setup for the Data Ops Center cluster.

## Services

### Core services (always running)

| Service          | Port         | Description                                                                                                                      |
|------------------|--------------|----------------------------------------------------------------------------------------------------------------------------------|
| Zookeeper        | 2181         | Coordination service required by Apache Pinot                                                                                    |
| Kafka            | 9092 / 29092 | Message broker in KRaft mode. Port 9092 is internal (container network), 29092 is for external clients (e.g. local producer app) |
| Schema Registry  | 8081         | Confluent Schema Registry for Avro schema management                                                                             |
| Pinot Controller | 9000         | Manages cluster metadata, table configs and segment assignment. Web UI available at `:9000`                                      |
| Pinot Broker     | 8099         | Accepts SQL queries and routes them to the appropriate servers                                                                   |
| Pinot Server     | 8097 / 8098  | Stores and serves segments. Admin API on 8097, query port on 8098                                                                |
| Pinot Minion     | 7500         | Background task executor for offline segment operations                                                                          |
| Prometheus       | 9090         | Scrapes JMX metrics from Kafka and all Pinot components                                                                          |
| Grafana          | 3000         | Dashboards over Prometheus metrics. Admin password set via secret file                                                           |
| Superset         | 8088         | BI and data exploration UI connected to Pinot via `pinotdb`. Admin credentials set via secret files                              |

### Init containers (run once, `--profile init`)

| Container                | Description                                                                                                                                |
|--------------------------|--------------------------------------------------------------------------------------------------------------------------------------------|
| `kafka-topic-init`       | Creates the `trade` and `gdansk-public-transport` Kafka topics                                                                             |
| `kafka-producer`         | Publishes Avro-serialized trade events to Kafka                                                                                            |
| `pinot-command-runner`   | Registers schemas and tables with the Pinot Controller (trade REALTIME, gdansk_public_transport OFFLINE + REALTIME)                        |
| `gdansk-kafka-producer`  | Fetches current GPS positions from the Gdansk public transport API and publishes them as JSON to the `gdansk-public-transport` Kafka topic |
| `pinot-ingestion-runner` | Runs a batch ingestion job that reads from S3 and pushes segments to Pinot                                                                 |
| `superset-init`          | Runs DB migrations, creates the admin user, initialises Superset roles, registers the Apache Pinot database connection, and imports dashboards with the MapTiler API key injected at runtime |

Init containers are one-shot — they exit after completing their task. Run them once on first setup, or whenever you need to re-seed the cluster.



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
cluster-setup/container/secrets/superset_maptiler_api_key  # MapTiler API key for map visualisations
```

The `secrets/` directory is gitignored — these files must be created manually on every machine.



## Custom Images

Three services use custom-built images that must be built before first run in dev mode:

```bash
# Apache Pinot — adds table configs, JMX exporter and ingestion scripts
docker build -t apache-pinot:1.4.0 -f cluster-setup/container/Dockerfile.apache-pinot .

# Apache Superset — adds pinotdb driver on top of the official image
docker build -t superset:4.1.2 -f cluster-setup/container/Dockerfile.superset .

# Kafka producer app — Maven multi-stage build of the Java Avro producer
docker build -t kafka-producer-app:1.0.0 -f kafka-producer-app/Dockerfile.kafka-producer-app .
```

For prod, push these images to Docker Hub under `robertglowacki83/` and they will be pulled automatically.



## Running the Cluster

All commands use an env file to switch between dev and prod behaviour (port bindings, image sources).

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

## Superset Dashboards

Dashboard definitions are version-controlled as YAML files under `cluster-setup/superset/dashboards/`. The MapTiler API key is **not** stored in those files — the placeholder `__MAPTILER_API_KEY__` is used instead.

At init time, `superset-init` (via `superset-init.sh`) copies the YAML directory to a temp location, substitutes the placeholder with the value from the `superset_maptiler_api_key` secret, zips the result, imports it into Superset, and discards the zip. No zip file needs to exist before running init.

To add or update a dashboard:
1. Export from the Superset UI (Settings → Export).
2. Unzip the export and place the directory under `cluster-setup/superset/dashboards/`.
3. In any chart YAML that contains a MapTiler URL, replace the key value with `__MAPTILER_API_KEY__`.
4. Commit the YAML directory. Do **not** commit the zip (it is gitignored).

### Port binding

Controlled by the `BIND` variable in the env files:

| Env  | `BIND`      | Effect                                                         |
|------|-------------|----------------------------------------------------------------|
| dev  | `0.0.0.0`   | All ports reachable from any machine on the network            |
| prod | `127.0.0.1` | All ports bound to localhost only (sit behind a reverse proxy) |
