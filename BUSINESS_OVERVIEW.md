# Data Operations Center — Business Overview

## What It Is

A **real-time + batch data analytics platform** that collects, streams, stores, and visualizes operational data — built entirely on open-source, cloud-ready components.

---

## Two Live Use Cases

| Use Case                    | Data Type                                                      | Ingestion Mode                                    |
|-----------------------------|----------------------------------------------------------------|---------------------------------------------------|
| **Financial Trade Events**  | Stock trade activity (symbol, side, quantity, timestamps)      | Real-time streaming via Kafka                     |
| **Gdansk Public Transport** | GPS positions, delays, speeds, routes for all city buses/trams | Both: live stream (Kafka) + historical batch (S3) |

---

## Architecture Diagram

```mermaid
%%{init: {"flowchart": {"padding": 20, "nodeSpacing": 60, "rankSpacing": 60}}}%%
flowchart TD
    subgraph Sources["Data Sources"]
        GT["Gdansk Transport API\n(public GPS feed)"]
        TE["Trade Events\n(Java producer app)"]
    end

    subgraph Ingestion["Ingestion Layer"]
        LA["AWS Lambda /\nGitHub Action"]
        S3["AWS S3\n(gdansk-public-transport)"]
        SR["Schema Registry\n(Avro schemas)"]
        KF["Apache Kafka\n(KRaft mode)"]
    end

    subgraph Analytics["Apache Pinot"]
        PC["Pinot Controller\n(metadata & tables)"]
        PB["Pinot Broker\n(SQL routing)"]
        PS["Pinot Server\n(segment storage)"]
        PM["Pinot Minion\n(background tasks)"]
    end

    subgraph Visualization["Visualization & BI"]
        SS["Apache Superset\n(self-service dashboards)"]
        GR["Grafana\n(infra & ops dashboards)"]
    end

    subgraph Observability["Observability"]
        PR["Prometheus\n(metrics scraping)"]
    end

    GT -->|"real-time GPS"| LA
    LA -->|"batch → S3"| S3
    LA -->|"stream → Kafka"| KF
    TE -->|"Avro trade events"| SR
    SR --> KF

    S3 -->|"offline batch ingestion"| PC
    KF -->|"REALTIME table"| PS

    PC --> PS
    PC --> PM
    PB --> PS

    SS -->|"SQL queries"| PB
    GR -->|"PromQL"| PR
    PR -->|"JMX scrape"| KF
    PR -->|"JMX scrape"| PC
    PR -->|"JMX scrape"| PS

    style Sources fill:#dbeafe,stroke:#3b82f6
    style Ingestion fill:#fef9c3,stroke:#eab308
    style Analytics fill:#dcfce7,stroke:#22c55e
    style Visualization fill:#f3e8ff,stroke:#a855f7
    style Observability fill:#ffe4e6,stroke:#f43f5e
```

---

## Key Business Advantages

**1. Sub-second query latency on millions of rows**
Apache Pinot is purpose-built for OLAP at scale — queries that would take minutes in a traditional database return in milliseconds, even over tens of millions of GPS snapshots.

**2. Unified real-time + historical data**
The same SQL interface queries both live streaming data (what buses are doing *right now*) and historical batch data from S3 — no separate pipelines or tools needed.

**3. Schema-governed data contracts**
Kafka Schema Registry with Avro enforces that every producer writes data in the agreed format. Broken or malformed events are rejected at the source, not discovered weeks later in a report.

**4. Self-service analytics for business users**
Apache Superset gives non-technical stakeholders a browser-based SQL and dashboard interface — no need to involve engineering for every data question.

**5. Full observability stack included**
Prometheus scrapes metrics from every component; Grafana dashboards give operations teams visibility into Kafka throughput, Pinot segment health, and query latency — all in one place.

**6. Cloud-portable and Kubernetes-ready**
The entire platform runs in Docker Compose (for dev/demo) or Kubernetes (for production). Moving from laptop to cloud is a configuration change, not a rewrite.

**7. Cost-effective open-source stack**
Every component (Kafka, Pinot, Superset, Grafana, Prometheus) is 100% open-source with no per-seat or per-query licensing fees — unlike managed SaaS alternatives (Snowflake, Splunk, etc.).

---

## What Kind of Analytics Does It Enable?

From the Gdansk transport use case, examples of business questions it can answer in real time:
- Which routes have the worst on-time performance? (by %, P90/P99 delay)
- Which individual vehicles are chronic offenders?
- Where are GPS congestion hotspots on the city map?
- How does rush-hour traffic volume compare hour by hour?
- How has average delay per route trended day by day?

---

## Deployment Modes

| Mode | How | Who For |
|---|---|---|
| Dev / Demo | Docker Compose, single machine | Local testing, demos |
| Production | Kubernetes (Kustomize), container images on Docker Hub | Cloud or on-prem production |

---

## Core Message

This platform turns raw event streams into queryable, dashboarded insights in under a second, at any scale, without vendor lock-in.