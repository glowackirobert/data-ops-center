# Security Hardening Plan — Remaining Work

Phases 0 (internal ports closed) and 1 (Caddy TLS entrypoint) shipped on
2026-09-01 and are documented in `cluster-setup/README-docker.md`.

Outstanding: the Pinot controller has no authentication *of its own* (only the
proxy's), all internal traffic is still plaintext and unauthenticated, and the
containers run without hardening.

## Phase 2 — authentication and abuse limits (next)

1. **Pinot controller** — Caddy basic auth is the perimeter; add Pinot's own
   `BasicAuthAccessControlFactory` for defence in depth. Not free: every
   internal caller then needs an `Authorization` header —
   `table_config/add-tables.sh`, `web-app/app/pinot.py` (`_query` and
   `/api/stats`), the `SUPERSET_PINOT_URI` connection string, the ingestion job
   spec's controller push, and the minion.
2. **`/api/guest-token`** (web-app) is unauthenticated by design — anyone who can
   load the map can mint a Superset guest token. Acceptable for a public
   read-only dashboard, but rate-limit it at Caddy, and set the embedded
   registration's `allowed_domains` to the web-app origin instead of the current
   `[]` (`app/superset.py`), which today permits embedding from any site.
3. **Kafka / ZooKeeper / Schema Registry** stay PLAINTEXT and unauthenticated.
   Acceptable *only* while they stay unpublished. If external Kafka access is
   ever needed it means a second `EXTERNAL` listener with `SASL_SSL` — not
   re-publishing 9092, whose advertised listener would hand a host client an
   unresolvable address anyway.

## Phase 3 — container hardening

- `alloy` mounts `/var/run/docker.sock:ro`. The `:ro` does **not** make the
  Docker API read-only — it marks the socket file read-only while the API stays
  bidirectional, so socket access remains equivalent to host root. Put
  `tecnativa/docker-socket-proxy` in front of it, exposing only `CONTAINERS=1`
  and `INFO=1`.
- `web-app` runs as root (`python:` base, no `USER`). Add a non-root `USER` to
  `Dockerfile.web-app`.
- Add to every service: `security_opt: ["no-new-privileges:true"]` and
  `cap_drop: [ALL]`; `read_only: true` where the container writes nothing
  outside its volumes.
- On prod, replace the long-lived `secrets/aws_credentials` key file with an EC2
  instance role — the AWS SDK picks up IMDS credentials and there is no file to
  leak. It is currently mounted into four containers.
- Linux host note: Docker publishes ports through its own iptables chain, which
  **bypasses UFW rules**. Only the EC2 security group and the absence of a
  `ports:` mapping actually close a port.

## Order of work

Phase 2 (Pinot auth, rate limits) → Phase 3 (container hardening). Each is
independently shippable.
