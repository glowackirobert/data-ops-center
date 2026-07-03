# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Data Operations Center is a data engineering platform that:
- streams data through Kafka into realtime Apache Pinot tables,
- ingests offline data from S3 bucket into Apache Pinot offline tables. 

BI dashboards are served by Apache Superset (connected to Pinot). 
Monitoring is provided by Prometheus + Grafana.
Additional docs: `BUSINESS_OVERVIEW.md` (architecture and use cases), `cluster-setup/README-docker.md` (detailed Docker stack reference).

## Modules

| Directory             | Language               | Purpose                                                                                                                                      |
|-----------------------|------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|
| `kafka-producer-app/` | Java 21 / Maven        | Kafka producer that publishes Avro-serialized `Trade` events                                                                                 |
| `cluster-setup/`      | Docker Compose, shell  | Infrastructure: Kafka, Schema Registry, Pinot, Superset, Prometheus, Grafana                                                                 |
| `k8s/`                | Kubernetes / Kustomize | Kubernetes manifests for Zookeeper, Kafka (KRaft mode), Apache Pinot                                                                         |
| `cluster-setup/py/`   | Python                 | Gdansk GPS fetchers: `_aws.py` (Lambda → S3), `_github_action.py` (CI → local file), `_kafka.py` (streams to Kafka, runs as compose service) |

## Building the Kafka Producer App

```bash
# Build and package (from repo root or kafka-producer-app/)
mvn package

# Build without running tests
mvn package -DskipTests
```

The Avro Maven plugin auto-generates Java classes from `kafka-producer-app/src/main/avro/Trade.avsc` into `kafka-producer-app/src/main/java/avro/` during the `generate-sources` phase. Never edit these generated files directly — edit the `.avsc` schema instead.

The JAR entry point is `KafkaProducerApp`. Dependencies land in `target/lib/`. The app runs in container mode only — configuration is loaded from `kafka-producer.properties` on the classpath.

## Running the Cluster (Docker Compose)

Before first run, populate the secrets files (the `secrets/` directory is gitignored):
- `cluster-setup/container/secrets/aws_credentials` — AWS credentials for Pinot's S3 access
- `cluster-setup/container/secrets/grafana_admin_password` — Grafana admin password
- `cluster-setup/container/secrets/superset_secret_key` — long random string for Flask session signing
- `cluster-setup/container/secrets/superset_admin_password` — Superset admin password
- `cluster-setup/container/secrets/superset_admin_username` — Superset admin username
- `cluster-setup/container/secrets/superset_admin_email` — Superset admin email
- `cluster-setup/container/secrets/superset_maptiler_api_key` — MapTiler API key for map visualizations

Build the custom images 
(must be done before first start in dev mode):
```bash
docker build -t apache-pinot:1.4.0 -f cluster-setup/container/Dockerfile.apache-pinot .
docker build -t superset:4.1.2 -f cluster-setup/container/Dockerfile.superset .
```

**Dev mode** (uses locally built images, `DOCKER_IMAGE_BASE_PATH` is empty):
```bash
# Start core services
docker-compose --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml up

# Also run init containers (kafka-topic-init, pinot-command-runner to seed tables,
# kafka-producer, pinot-ingestion-runner for S3 batch ingestion, superset-init)
docker-compose --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml --profile init up

# Stop
docker-compose --env-file cluster-setup/env/env.dev -f cluster-setup/container/container-compose.yml down
```

**Prod mode** (pulls images from Docker Hub `robertglowacki83/` — images are built and pushed automatically by `.github/workflows/docker-ci.yml` on every push to `master`):
```bash
docker-compose --env-file cluster-setup/env/env.prod -f cluster-setup/container/container-compose.yml up --pull always -d
docker-compose --env-file cluster-setup/env/env.prod -f cluster-setup/container/container-compose.yml down
```

### Service Ports

| Service                  | Port              | Notes                                             |
|--------------------------|-------------------|---------------------------------------------------|
| Kafka (external clients) | `localhost:9092`  | For external tooling only (e.g. console consumer) |
| Schema Registry          | `localhost:8081`  |                                                   |
| Pinot Controller UI      | `0.0.0.0:9000`    | Web UI + REST API                                 |
| Pinot Broker             | `localhost:8099`  | Query endpoint                                    |
| Prometheus               | `localhost:9090`  |                                                   |
| Grafana                  | `0.0.0.0:3000`    |                                                   |
| Superset                 | `0.0.0.0:8088`    | BI dashboards over Pinot (via `pinotdb` driver)   |

### Verifying Kafka Messages

```bash
docker exec -it kafka /bin/bash -c "env -u KAFKA_OPTS /opt/kafka/bin/kafka-console-consumer.sh --bootstrap-server localhost:9092 --topic trade --from-beginning"
```

## Apache Pinot Tables

Three tables are managed by `cluster-setup/table_config/add-tables.sh`:

- **`trade` (REALTIME)** — consumes from the Kafka `trade` topic; schema in `trade_table_schema.json`
- **`gdansk_public_transport` (REALTIME)** — consumes from the Kafka `gdansk-public-transport` topic; JSON-decoded; 7-day retention; schema in `gdansk_public_transport_table_schema.json`
- **`gdansk_public_transport` (OFFLINE)** — batch-ingested from S3; schema in `gdansk_public_transport_table_schema.json`

The `pinot-command-runner` init container runs this script automatically when starting with `--profile init`. To re-register tables against a running controller directly:
```bash
curl -X POST -H "Content-Type: application/json" -d @cluster-setup/table_config/gdansk_public_transport_table_schema.json http://localhost:9000/schemas
curl -X POST -H "Content-Type: application/json" -d @cluster-setup/table_config/gdansk_public_transport_offline_table_config.json http://localhost:9000/tables
curl -X POST -H "Content-Type: application/json" -d @cluster-setup/table_config/gdansk_public_transport_realtime_table_config.json http://localhost:9000/tables
curl -X POST -H "Content-Type: application/json" -d @cluster-setup/table_config/trade_table_schema.json http://localhost:9000/schemas
curl -X POST -H "Content-Type: application/json" -d @cluster-setup/table_config/trade_table_config.json http://localhost:9000/tables
```

## Kubernetes

The `k8s/` directory uses Kustomize with base + overlays (dev/prod). Kafka runs in KRaft mode. The Kafka pods use a custom `kafka-jmx:4.1.1` image that has the JMX agent baked in.

```bash
# Create namespace
kubectl apply -f k8s/00-ns/data-ops-center-cluster-ns.yaml

# Switch context to namespace
kubectl config set-context --current --namespace=data-ops-center

# Deploy ZooKeeper
kubectl apply -k k8s/01-zk/overlays/dev

# Build + load custom Kafka JMX image, then deploy
bash k8s/02-kafka/build.sh dev
kubectl apply -k k8s/02-kafka/overlays/dev

# Quick status
kubectl get pods,svc,pvc -n data-ops-center
```

`KAFKA_NODE_ID` and `KAFKA_ADVERTISED_LISTENERS` are injected per-pod by an init container at runtime, based on the pod's ordinal index.

## AWS Lambda (Gdansk Public Transport)

The Lambda fetches GPS positions from the Gdansk public transport API and stores JSON-lines files in S3 bucket `gdansk-public-transport` under `artifacts/YYYY/MM/DD/HH-MM.txt`.

Deployment is automated by `.github/workflows/lambda-function.yaml`. To package manually:
```bash
rm -rf aws_lambda
mkdir -p aws_lambda
cd aws_lambda
pip install requests schedule boto3 -t .
cp ../cluster-setup/py/gdansk_public_transport_aws.py .
# Windows PowerShell:
Compress-Archive -Path * -DestinationPath function.zip
# Upload function.zip to AWS Lambda
```

Handler entry point: `gdansk_public_transport_aws.lambda_handler`.

## Architecture Notes

- The Kafka producer runs 5 iterations of 1,000,000 messages each across 2 threads, flushing every 10,000 messages. Message volume is controlled by constants in `KafkaCustomTopicProducer.java`.
- The custom Pinot Docker image (`Dockerfile.apache-pinot`) copies all files from `cluster-setup/table_config/` and `cluster-setup/pinot/` into the image at build time, so rebuilding is required when those configs change.
- Prometheus scrapes JMX metrics from Kafka (port 19092) and from each Pinot component via their respective JMX exporter ports. The JMX config lives in `cluster-setup/jmx_exporter/kafka_jmx_config.yml`.
- The `gdansk_public_transport_github_action.py` script is the CI/GitHub Actions variant of the Lambda fetcher — it appends to a local file instead of uploading to S3.
- The `gdansk-public-transport-kafka-producer` compose service (always on, not init-only) runs `gdansk_public_transport_kafka.py`: it polls the Gdansk API every 120 s and publishes only changed vehicle positions to the `gdansk-public-transport` topic, keyed by `vehicleId`.
- The `pinot-ingestion-runner` init container launches a batch ingestion job. The spec contains a `${DATE}` placeholder (format `YYYY/MM/DD`) that is substituted at runtime from the `INGESTION_DATE` env var.
- Superset dashboards are version-controlled as YAML under `cluster-setup/superset/dashboards/`. The MapTiler API key is never stored there — `superset-init.sh` substitutes the `__MAPTILER_API_KEY__` placeholder from the secret at import time. Sample Pinot queries live in `cluster-setup/table_config/gdansk_public_transport_queries.sql`.
