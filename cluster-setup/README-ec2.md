# Deploying on AWS EC2

## Decide how the browser will reach the stack

In prod all three main published ports bind to `127.0.0.1` (`BIND`) until you
pick one of these. Each mode's settings live in their own env file —
`cluster-setup/env/env.mode-{a,b,c}` — layered on top of `env.prod` as a
third `--env-file`, so switching modes never means hand-editing `env.prod`
itself:

|                 | Mode A — Caddy TLS                                  | Mode B — SSH tunnels                                                                                                                                       | Mode C — fully open (temporary)                                                                   |
|-----------------|-----------------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------|
| Needs           | a domain you own                                    | nothing                                                                                                                                                    | nothing — but lock the security group to your own IP                                              |
| Security group  | 22, 80, 443                                         | 22                                                                                                                                                         | 22, 3001, 8088, 3000, 9000, 8099, 9090, 12345 — source restricted to your IP, never `0.0.0.0/0`   |
| Compose profile | `--profile tls`                                     | none                                                                                                                                                       | none                                                                                              |
| Env file        | `env.mode-a`                                        | `env.mode-b`, plus `-f container-compose.debug-ports.yml` (needed for the `pinot-controller` tunnel below; `DEBUG_BIND` stays unset so it's loopback-only) | `env.mode-c`, plus `-f container-compose.debug-ports.yml` (`DEBUG_BIND=0.0.0.0`)                  |
| URLs            | `https://map.<domain>`, `bi.`, `ops.`, `pinot.`     | `http://localhost:3001`, `:8088`, `:3000`                                                                                                                  | `http://<public-ip>:3001`, `:8088`, `:3000`, `:9000`, `:8099`, `:9090`, `:12345`                  |
| Good for        | anything others should reach                        | a private demo from your laptop                                                                                                                            | quick testing before you have a domain                                                            |



## 1. Launch the instance

- **AMI**: Ubuntu Server 24.04 LTS
- **Type**: `t3.2xlarge` (8 vCPU, 32 GB). The prod heaps in `env.prod` alone
  reserve ~10 GB. If the instance runs out of CPU credits under sustained ingestion,
  switch to `m6i.2xlarge` (dedicated, not burstable).
- **Storage**: 80 GB gp3 root volume
- **Key pair**: create or reuse a `.pem`.
- **Security group** (inbound): per the table above.

Allocate an **Elastic IP** if you intend to stop and start the instance;
otherwise the public IP changes on every start and DNS and tunnels break.

## 2. Connect

PowerShell /win/:

```powershell
icacls key.pem /inheritance:r /grant:r "$($env:USERNAME):(R)"
ssh -i key.pem ubuntu@16.170.37.11
```

Bash from location where .pem is stored:
```bash
chmod 400 key.pem
ssh -i key.pem ubuntu@<PUBLIC_IP>
```

```bash
## 3. Install Docker

```bash
sudo apt-get update && sudo apt-get upgrade -y
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker ubuntu && newgrp docker
docker compose version    # must report v2.17 or newer
```


## 4. Clone the repository

```bash
git clone https://github.com/glowackirobert/data-ops-center.git
cd data-ops-center
```

For a private repo prefer `gh auth login` or a read-only deploy key. A personal
access token embedded in the clone URL is stored in `.git/config` and in shell
history in plain text; if you use one anyway, strip it afterwards with
`git remote set-url origin https://github.com/glowackirobert/data-ops-center.git`.

## 5. Create the secrets

Nine files, all gitignored, none of them ever committed:

```bash
mkdir -p cluster-setup/container/secrets
cd cluster-setup/container/secrets

cat > aws_credentials <<'CREDS'
[default]
aws_access_key_id = ...
aws_secret_access_key = ...
CREDS

echo '' > grafana_admin_password
echo '' > superset_admin_password
echo 'admin' > superset_admin_username
echo 'admin@wp.pl' > superset_admin_email
echo '' > superset_mapbox_api_key
echo '' > anthropic_api_key
openssl rand -base64 42  > superset_secret_key

docker run --rm caddy:2.10-alpine caddy hash-password \
  --plaintext 'pinot-ui-password' > pinot_basic_auth_hash

chmod 600 *
chmod 644 grafana_admin_password
cd ../../../..
```

`anthropic_api_key` is what `/api/ask` and the **Filter** box's natural-language
parsing use (`AI_PLATFORM_PLAN.md`); without it those two features 500 but the
rest of the stack is unaffected.

`aws_credentials` is what the Pinot controller, server, minion and ingestion
runner use for the S3 deep store — without it the controller never becomes
healthy. `pinot_basic_auth_hash` holds a bcrypt hash, not a password; the
username beside it is `PINOT_AUTH_USER` in `env.prod` (default `admin`), and
Caddy checks the pair on `pinot.<domain>`. Trailing newlines are stripped by
every consumer, so plain `echo` is fine.

`chmod 600` makes each file readable only by the host user that ran it —
usually `ubuntu`. That's fine for secrets consumed by a container running as
root (Pinot) or as a UID that happens to match (Kafka, Superset — UID 1000,
same as `ubuntu` on the standard AMI), but Grafana's container runs entirely
as UID 472: with a `ubuntu`-owned 600 file it gets `Permission denied`
reading `grafana_admin_password`, silently falls back to its built-in
default password, and `admin` + the secret you set stops working. Fix with
`sudo chown 472:472 grafana_admin_password` (keep the 600 mode), then
`docker compose ... up -d --force-recreate grafana` to make it re-read the
file.

### Create the volume directories

Compose auto-creates a missing bind-mount source directory on first `up` —
but the Docker daemon runs as root, so it creates it `root`-owned, which then
blocks any container that doesn't itself run as root (`permission denied on a
mounted volume` in Troubleshooting below). Creating them yourself first, as
`ubuntu`, sidesteps that:

```bash
mkdir -p cluster-setup/volumes/{zoo-data,zoo-log,kafka-data,pinot/controller,pinot/controller-logs,pinot/broker-logs,pinot/server,pinot/server-logs,pinot/minion-logs,pinot/ingestion-logs,pinot/ingestion-staging,caddy/data,loki,superset}
sudo chown -R 1000:1000   cluster-setup/volumes/zoo-data
sudo chown -R 1000:1000   cluster-setup/volumes/zoo-log
sudo chown -R 1000:1000   cluster-setup/volumes/kafka-data
sudo chown -R 10001:10001 cluster-setup/volumes/loki
sudo chown -R 1000:1000   cluster-setup/volumes/superset
```

`zookeeper`, `apache/kafka` and the custom `superset` image all run as UID
1000 — same as `ubuntu` on the standard AMI, so these four `chown`s only
matter if the `mkdir` above ran under `sudo`. `loki` is the one image here
that runs as a different UID (10001), so its `chown` is required regardless.
Pinot (`pinot/controller`, `pinot/controller-logs`, `pinot/broker-logs`,
`pinot/server`, `pinot/server-logs`, `pinot/minion-logs`,
`pinot/ingestion-logs`, `pinot/ingestion-staging`) and Caddy (`caddy/data`)
both run as root, so
those directories need no `chown` no matter who creates them.

## 6. Point a mode file at this host

`cluster-setup/env/env.prod` itself needs no edits — it's shared across all
three modes. Instead, edit the one file matching your mode:

**Mode A** — `cluster-setup/env/env.mode-a`: your domain, and Caddy switched
from its local CA to Let's Encrypt (any email address in `CADDY_TLS` does
that):

```properties
BASE_DOMAIN=example.com
CADDY_TLS=you@example.com
SUPERSET_DOMAIN=https://bi.example.com
WEBAPP_ORIGIN=https://map.example.com
GRAFANA_ROOT_URL=https://ops.example.com
```

**Mode B** — `cluster-setup/env/env.mode-b`: no proxy, so the browser sees
the tunnelled localhost ports. no edits.

**Mode C** — `cluster-setup/env/env.mode-c`: no proxy, no tunnel, browser
hits the instance directly. Replace `<PUBLIC_IP_OR_DNS>` with the instance's
actual public IP/DNS:

```properties
SUPERSET_DOMAIN=http://13.50.235.210:8088
WEBAPP_ORIGIN=http://13.50.235.210:3001
GRAFANA_ROOT_URL=http://13.50.235.210:3000
```

`SUPERSET_DOMAIN`/`WEBAPP_ORIGIN` must match the URL the browser actually
uses, or Superset's CSP `frame-ancestors` rejects the Analytics-tab iframe —
see *Browser-visible origins* in [README-docker.md](README-docker.md).
`GRAFANA_ROOT_URL` matches it for the same reason. `GRAFANA_COOKIE_SECURE`
is `true` in `env.mode-a` and `env.mode-b` (needed once Caddy TLS fronts
Grafana in Mode A; still fine in Mode B since a tunnel lands on `localhost`,
which browsers treat as secure even over plain HTTP) and `false` in
`env.mode-c`, whose origin is a bare public IP/DNS name over plain HTTP —
that gets no such exception, so a `Secure` cookie there is silently dropped
by the browser and login bounces straight back to the login page with no
error.

**Mode A only** — add four DNS A records pointing at the Elastic IP: `map`,
`bi`, `ops`, `pinot`.

## 7. Start the stack

All commands run from the repository root and pass three env files: the
shared two plus the mode file from step 6.

**Mode A**:

```bash
docker compose \
  --env-file cluster-setup/env/versions.env \
  --env-file cluster-setup/env/env.prod \
  --env-file cluster-setup/env/env.mode-a \
  -f cluster-setup/container/container-compose.yml \
  --profile tls --profile init up -d
```

**Mode B and Mode C** — same shape for both, just swap `env.mode-b` for
`env.mode-c`; add `-f container-compose.debug-ports.yml` so
`pinot-controller`/`pinot-broker`/`prometheus`/`alloy` publish their ports at
all (dropped `--profile tls` — neither mode runs Caddy). `pinot-controller`'s
port is what step 8's SSH tunnel forwards in Mode B; Mode C additionally
opens all four beyond loopback via `DEBUG_BIND=0.0.0.0` in `env.mode-c`:

```bash
docker compose \
  --env-file cluster-setup/env/versions.env \
  --env-file cluster-setup/env/env.prod \
  --env-file cluster-setup/env/env.mode-c \
  -f cluster-setup/container/container-compose.yml \
  -f cluster-setup/container/container-compose.debug-ports.yml \
  --profile init up -d
```

`pinot-ingestion-runner` (part of `--profile init`) does a full backfill of
everything under `squashed/` at start — `INGESTION_DATE` ships empty, which is
a wildcard, and that works fine in one shot. Later, once the cluster has been
running a while, use `backfill_pinot_offline.py` to catch up on new days
cheaply instead of re-running the full wildcard backfill each time:

```bash
sudo apt-get install -y awscli python3
python3 cluster-setup/scripts/backfill_pinot_offline.py --env prod \
  --start-date 2026-01-01 --end-date 2026-01-31
```


## 8. Reach the UIs

**Mode A** — straight from a browser:

| URL                      | Service                   | Login                                       |
|--------------------------|---------------------------|---------------------------------------------|
| `https://map.<domain>`   | Web app (map + analytics) | none                                        |
| `https://bi.<domain>`    | Superset                  | the `superset_admin_*` secrets              |
| `https://ops.<domain>`   | Grafana                   | `admin` / `grafana_admin_password`          |
| `https://pinot.<domain>` | Pinot controller UI       | `PINOT_AUTH_USER` + the password you hashed |

**Mode B** — open the tunnels from your laptop, then use `http://localhost:…`:

```powershell
ssh -i key.pem -L 3001:localhost:3001 -L 9000:localhost:9000 -L 8088:localhost:8088 -L 3000:localhost:3000 ubuntu@16.16.156.69
```

Multi-laptop access: the tunnel is per-machine. From another laptop use
`ssh -L ...` with a copy of `key.pem`.


**Mode C** — straight from a browser, no tunnel (the `docker compose` command
is the Mode B/C one from step 7 with `--env-file env.mode-c`):

| URL                          | Service             | Login                              |
|------------------------------|---------------------|------------------------------------|
| `http://13.50.235.210:3001`   | Web app             | none                               |
| `http://13.50.235.210:8088`   | Superset            | the `superset_admin_*` secrets     |
| `http://13.50.235.210:3000`   | Grafana             | `admin` / `grafana_admin_password` |
| `http://13.50.235.210:9000`   | Pinot controller UI | **none — no authentication**       |
| `http://13.50.235.210:8099`   | Pinot broker        | none                               |
| `http://13.50.235.210:9090`   | Prometheus          | none                               |
| `http://13.50.235.210:12345`  | Alloy debug UI      | none                               |

The last four have zero authentication of their own; the security-group rule
from step 1 (source restricted to your IP) is the only thing in front of them.


## 9. Stop and start

```bash
aws ec2 stop-instances  --instance-ids <INSTANCE_ID>
aws ec2 start-instances --instance-ids <INSTANCE_ID>
```