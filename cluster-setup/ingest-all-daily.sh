#!/usr/bin/env bash
# Batch-ingest every compacted daily file of a year into the Pinot offline table.
#
# Lists s3://gdansk-public-transport/daily/<YEAR>/*.json.gz, prints what it
# found, then runs the pinot-ingestion-runner init container once per date —
# the same command as the single-day run in README-docker.md, with
# INGESTION_DATE switched for each file. Safe to re-run: segments are named
# by date and overwritten, not duplicated.
#
# Requires a running cluster (uses `run --no-deps`) and the AWS CLI on the
# host; credentials are read from cluster-setup/container/secrets/.
#
# Usage:
#   bash cluster-setup/ingest-all-daily.sh [--year YYYY] [--env dev|prod] [--dry-run]
#
#   --year     year prefix to list under daily/ (default: current year)
#   --env      which env file to use, env.dev or env.prod (default: prod)
#   --dry-run  only list the available daily files, ingest nothing
#
# Environment:
#   AWS_EXTRA_ARGS  extra flags passed to `aws s3 ls` (e.g. --no-verify-ssl
#                   behind a TLS-intercepting proxy)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BUCKET=gdansk-public-transport
YEAR="$(date +%Y)"
ENV=prod
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --year)    YEAR="$2"; shift 2 ;;
    --env)     ENV="$2";  shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    *) echo "Unknown argument: $1" >&2; exit 1 ;;
  esac
done

CREDS="$SCRIPT_DIR/container/secrets/aws_credentials"
if [[ ! -f "$CREDS" ]]; then
  echo "Missing $CREDS — populate the secrets directory first (see README-docker.md)" >&2
  exit 1
fi

echo "Listing s3://$BUCKET/daily/$YEAR/ ..."
mapfile -t DATES < <(
  AWS_SHARED_CREDENTIALS_FILE="$CREDS" aws s3 ls "s3://$BUCKET/daily/$YEAR/" ${AWS_EXTRA_ARGS:-} \
    | awk '{print $NF}' \
    | grep -E '^[0-9]{4}-[0-9]{2}-[0-9]{2}\.json\.gz$' \
    | sed 's/\.json\.gz$//' \
    | sort
)

if [[ ${#DATES[@]} -eq 0 ]]; then
  echo "No daily .json.gz files found under daily/$YEAR/" >&2
  exit 1
fi

echo "Found ${#DATES[@]} daily files:"
printf '  %s\n' "${DATES[@]}"

if [[ $DRY_RUN -eq 1 ]]; then
  echo "Dry run — nothing ingested."
  exit 0
fi

cd "$REPO_ROOT"
FAILED=()
for d in "${DATES[@]}"; do
  echo "=== Ingesting $d ==="
  if ! INGESTION_DATE="$d" docker compose \
      --env-file cluster-setup/env/versions.env \
      --env-file "cluster-setup/env/env.$ENV" \
      -f cluster-setup/container/container-compose.yml \
      --profile init \
      run --rm --no-deps pinot-ingestion-runner; then
    FAILED+=("$d")
    echo "!!! Ingestion failed for $d — continuing with next date" >&2
  fi
done

if [[ ${#FAILED[@]} -gt 0 ]]; then
  echo "Failed dates (${#FAILED[@]}): ${FAILED[*]}" >&2
  exit 1
fi
echo "All ${#DATES[@]} days ingested successfully."
