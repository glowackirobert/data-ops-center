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

| Container | Description |
|---|---|
| `kafka-topic-init` | Creates the `trade` Kafka topic |
| `kafka-producer` | Publishes Avro-serialized trade events to Kafka |
| `pinot-command-runner` | Registers schemas and tables with the Pinot Controller |
| `pinot-ingestion-runner` | Runs a batch ingestion job that reads from S3 and pushes segments to Pinot |
| `superset-init` | Runs DB migrations, creates the admin user, and initialises Superset roles |

Init containers are one-shot — they exit after completing their task. Run them once on first setup, or whenever you need to re-seed the cluster.



## Requirements

- Docker with Docker Compose



## Secrets

Populate the following files before the first run:

```
cluster-setup/container/secrets/aws_credentials          # AWS credentials for S3/Pinot access
cluster-setup/container/secrets/grafana_admin_password   # Grafana admin password
cluster-setup/container/secrets/superset_secret_key      # Long random string for Flask session signing
cluster-setup/container/secrets/superset_admin_password  # Superset admin password
cluster-setup/container/secrets/superset_admin_username  # Superset admin username
cluster-setup/container/secrets/superset_admin_email     # Superset admin email
```



## Custom Images

Two services use custom-built images that must be built before first run in dev mode:

```bash
# Apache Pinot — adds table configs, JMX exporter and ingestion scripts
docker build -t apache-pinot:1.4.0 -f cluster-setup/container/Dockerfile.apache-pinot .

# Apache Superset — adds pinotdb driver on top of the official image
docker build -t superset:4.1.2 -f cluster-setup/container/Dockerfile.superset .
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

### Port binding

Controlled by the `BIND` variable in the env files:

| Env m  | `BIND`      | Effect                                                         |
|--------|-------------|----------------------------------------------------------------|
| dev    | `0.0.0.0`   | All ports reachable from any machine on the network            |
| prod   | `127.0.0.1` | All ports bound to localhost only (sit behind a reverse proxy) |
