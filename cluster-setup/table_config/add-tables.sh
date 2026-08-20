#!/bin/bash
# Registers the Pinot schemas and tables with the controller. Idempotent: every
# object is upserted (POST when missing, PUT when present), never deleted.
set -euo pipefail

# Overridable so the script can be run from the host (localhost:9000)
CONTROLLER="${PINOT_CONTROLLER_URL:-http://pinot-controller:9000}"
CONFIG_DIR="${TABLE_CONFIG_DIR:-/opt/pinot/scripts}"
MAX_RETRIES="${PINOT_MAX_RETRIES:-5}"
RETRY_DELAY="${PINOT_RETRY_DELAY:-5}"
# Caps a single controller call, so a wedged controller fails a retry instead of
# hanging the whole init container.
REQUEST_TIMEOUT="${PINOT_REQUEST_TIMEOUT:-30}"

send_with_retry() {
  local description="$1" method="$2" url="$3" file="$4" attempt=1

  while [ "$attempt" -le "$MAX_RETRIES" ]; do
    echo "$description (attempt $attempt/$MAX_RETRIES)..."
    local response http_code
    response=$(curl -s --max-time "$REQUEST_TIMEOUT" -w "\n%{http_code}" -X "$method" \
        -H "Content-Type: application/json" \
        -d @"$file" \
        "$url") || response=$'\n000'
    http_code=$(echo "$response" | tail -n1)
    if [ "$http_code" = "200" ]; then
      echo "$response" | sed '$d'
      echo "$description succeeded"
      return 0
    fi
    echo "Attempt $attempt failed (HTTP $http_code): $(echo "$response" | sed '$d')"
    echo "Retrying in ${RETRY_DELAY}s..."
    sleep "$RETRY_DELAY"
    attempt=$((attempt + 1))
  done

  echo "ERROR: $description failed after $MAX_RETRIES attempts" >&2
  return 1
}

# Echoes "yes"/"no" for $1 existing. A controller that is up but not yet serving
# answers neither 200 nor 404; guessing "no" there would send a POST for an
# object that exists and burn every retry on the resulting 409, so retry the
# probe itself until the controller gives a definite answer.
exists() {
  local url="$1" attempt=1 http_code

  while [ "$attempt" -le "$MAX_RETRIES" ]; do
    http_code=$(curl -s --max-time "$REQUEST_TIMEOUT" -o /dev/null -w "%{http_code}" "$url") || http_code=000
    case "$http_code" in
      200) echo yes; return 0 ;;
      404) echo no;  return 0 ;;
    esac
    echo "Existence check for $url returned HTTP $http_code, retrying in ${RETRY_DELAY}s..." >&2
    sleep "$RETRY_DELAY"
    attempt=$((attempt + 1))
  done

  echo "ERROR: could not determine whether $url exists after $MAX_RETRIES attempts" >&2
  return 1
}

config_file() {
  local file="$CONFIG_DIR/$1"
  # Fail here rather than letting curl POST an empty body and Pinot answer 400
  [ -f "$file" ] || { echo "ERROR: config file not found: $file" >&2; return 1; }
  echo "$file"
}

# Create the schema if missing, otherwise update it in place. Never DELETE:
# dropping a schema/table discards every ingested segment. ?reload=true makes
# existing segments pick up schema changes (new columns arrive as defaults).
upsert_schema() {
  local name="$1" file present
  file=$(config_file "$2")
  # Separate assignment, so an inconclusive probe fails the script here instead
  # of being read as "missing" inside an if-condition (where set -e does not fire)
  present=$(exists "$CONTROLLER/schemas/$name")
  if [ "$present" = yes ]; then
    send_with_retry "Updating schema $name" PUT "$CONTROLLER/schemas/$name?reload=true" "$file"
  else
    send_with_retry "Adding schema $name" POST "$CONTROLLER/schemas" "$file"
  fi
}

# $1 is the type-suffixed name (foo_OFFLINE / foo_REALTIME) so the existence
# check and PUT hit the right half of a hybrid table pair.
upsert_table() {
  local name="$1" file present
  file=$(config_file "$2")
  present=$(exists "$CONTROLLER/tables/$name")
  if [ "$present" = yes ]; then
    send_with_retry "Updating table $name" PUT "$CONTROLLER/tables/$name" "$file"
  else
    send_with_retry "Adding table $name" POST "$CONTROLLER/tables" "$file"
  fi
}

upsert_schema gdansk_public_transport gdansk_public_transport_table_schema.json
upsert_table gdansk_public_transport_OFFLINE gdansk_public_transport_offline_table_config.json
upsert_table gdansk_public_transport_REALTIME gdansk_public_transport_realtime_table_config.json

# Second table consuming the same gdansk-public-transport Kafka topic as the
# pair above, via its own independent consumer group (no producer changes
# needed). Upsert-enabled on vehicleId, so a plain SELECT returns one row per
# vehicle - its latest position - instead of the LASTWITHTIME/GROUP BY dance
# the other table's REALTIME half requires.
upsert_schema gdansk_public_transport_latest gdansk_public_transport_latest_table_schema.json
upsert_table gdansk_public_transport_latest_REALTIME gdansk_public_transport_latest_realtime_table_config.json

upsert_schema trade trade_table_schema.json
upsert_table trade_REALTIME trade_table_config.json

echo "Schemas and tables are up to date"
