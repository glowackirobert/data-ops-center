# Apache Pinot Improvement Plan

1. Indexing
2. Minion task scaling
   - MergeRollupTask
   - RealtimeToOfflineSegmentsTask
   - SegmentGenerationAndPushTask
   - UpsertCompactionTask
3. Multi-stage query engine (v2)
   - Joining
   - Window functions
   - Multistage enabled
4. Ingestion & stream tuning
   - Increase Kafka to 3 partitions
   - Transport flush thresholds
   - `ingestionConfig.filterConfig` to drop bad GPS rows at ingest
5. Query-side features
   - `DISTINCTCOUNTHLL(vehicleId)` (or Theta sketches) instead of `COUNT(DISTINCT vehicleId)`
   - `LASTWITHTIME` examples for latest-position queries
   - Broker config cleanup (routing table builder, query cancellation, timeout guardrail)
6. Operational hygiene
   - Replication factor == 3
   - Measure performance (`load_test_broker.py`)
   - Query console guardrails (readOnly / auth)
7. Stops dimension table + ingestion enrichment
8. Kubernetes HA deployment
   - `03-pinot/` manifests
   - Replication end to end
   - Superset for scale-out
   - Security & ops
9. Route termini ("home depot") per vehicle
10. Scheduled trips ingestion (timetable per day/night)
11. No auth or TLS on any service — see `SECURITY_HARDENING_PLAN.md`
Kafka, ZooKeeper, Schema Registry and the Pinot broker/server/minion no longer
publish a host port, so their plaintext, unauthenticated traffic never leaves
the Docker network. Still open: TLS everywhere, and authentication on the Pinot
controller UI/API (port 9000), whose API can drop tables and segments. 
12. Real domain + email in env.prod (also SUPERSET_DOMAIN/WEBAPP_ORIGIN — they must be browser-reachable)
13. Postgres for Superset metadata 
14. mem_limit/cpus on the JVM services, especially pinot-ingestion-runner 
15. rate_limit in the Caddyfile for map. 
16. Prometheus rules + Alertmanager 
17. Replication ≥ 2 — needs more nodes, which is really the k8s path, not compose  