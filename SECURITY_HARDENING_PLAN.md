# Security Hardening Plan — Remaining Work

Shipped so far, both on 2026-09-01 and documented in
`cluster-setup/README-docker.md` rather than repeated here:

- **Phase 0 — internal ports closed.** Ten host mappings removed, ZooKeeper's
  4LW whitelist narrowed, Grafana's dev datasources moved onto container names,
  plus the opt-in `container-compose.debug-ports.yml` escape hatch.
  See **Port binding**.
- **Phase 1 — TLS entrypoint.** Caddy on 80/443 fronting the four
  browser-facing services on `map.` / `bi.` / `ops.` / `pinot.` subdomains,
  with basic auth on the Pinot controller, `ENABLE_PROXY_FIX` in Superset, and
  Grafana's root URL and secure cookie set. `BASE_DOMAIN` + `CADDY_TLS` switch
  between the local CA and Let's Encrypt. See **HTTPS and the reverse proxy**.

**Correction to what this plan previously said:** it called for flipping
Superset's `force_https` and `session_cookie_secure` to `True` alongside the
proxy. Both must stay `False`. Caddy already redirects HTTP to HTTPS at the
edge, and the web-app mints guest tokens over plain `http://superset:8088`
inside the Docker network — Talisman would redirect that internal call (it
carries no `X-Forwarded-Proto` to exempt it) and a `Secure` cookie would never
come back on that hop. TLS protects browser traffic at Caddy, which is the only
place the session cookie crosses a network. The reasoning now sits in
`superset_config.py` next to both settings.

## Where that leaves us

Caddy is the only entrypoint that needs to be open. The four `${BIND}` mappings
that remain (web-app 3001, Superset 8088, Grafana 3000, Pinot controller 9000)
are loopback-only in prod and exist for SSH tunnelling and debugging — Caddy
reaches those containers over the Docker network instead.

Still outstanding: the Pinot controller has no authentication *of its own* (only
the proxy's), all internal traffic is still plaintext and unauthenticated, and
the containers run without hardening.

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

## Already sound — leave alone

- The only SQL string interpolation in the web-app (`route`, in the delay
  histogram) is validated against `^[A-Za-z0-9]{1,10}$` before use; every other
  query is a constant. No injection surface found.
- Secrets are file-based compose secrets, never env vars, and `secrets/` is
  gitignored.
- `app/http.py` never echoes exception text back to the client.
- The Superset CSP is explicit and already env-driven for `frame-ancestors`.

## Order of work

Phase 2 (Pinot auth, rate limits) → Phase 3 (container hardening). Each is
independently shippable.
