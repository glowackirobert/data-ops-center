#!/bin/bash
set -e

MAX_RETRIES=5
RETRY_DELAY=5
# Overridable so the script can be tested from the host (localhost:9000)
CONTROLLER="${PINOT_CONTROLLER_URL:-http://pinot-controller:9000}"
CONFIG_DIR="${TABLE_CONFIG_DIR:-/opt/pinot/scripts}"

send_with_retry() {
  local description="$1"
  local method="$2"
  local url="$3"
  local file="$4"
  local attempt=1

  while [ $attempt -le $MAX_RETRIES ]; do
    echo "$description (attempt $attempt/$MAX_RETRIES)..."
    local response http_code
    response=$(curl -s -w "\n%{http_code}" -X "$method" \
        -H "Content-Type: application/json" \
        -d @"$file" \
        "$url")
    http_code=$(echo "$response" | tail -n1)
    if [ "$http_code" = "200" ]; then
      echo "$response" | sed '$d'
      echo "$description succeeded"
      return 0
    fi
    echo "Attempt $attempt failed (HTTP $http_code): $(echo "$response" | sed '$d')"
    echo "Retrying in ${RETRY_DELAY}s..."
    sleep $RETRY_DELAY
    attempt=$((attempt + 1))
  done

  echo "ERROR: $description failed after $MAX_RETRIES attempts"
  return 1
}

exists() {
  [ "$(curl -s -o /dev/null -w "%{http_code}" "$1")" = "200" ]
}

# Create the schema if missing, otherwise update it in place. Never DELETE:
# dropping a schema/table discards every ingested segment. ?reload=true makes
# existing segments pick up schema changes (new columns arrive as defaults).
upsert_schema() {
  local name="$1"
  local file="$CONFIG_DIR/$2"
  if exists "$CONTROLLER/schemas/$name"; then
    send_with_retry "Updating schema $name" PUT "$CONTROLLER/schemas/$name?reload=true" "$file"
  else
    send_with_retry "Adding schema $name" POST "$CONTROLLER/schemas" "$file"
  fi
}

# $1 is the type-suffixed name (foo_OFFLINE / foo_REALTIME) so the existence
# check and PUT hit the right half of a hybrid table pair.
upsert_table() {
  local name="$1"
  local file="$CONFIG_DIR/$2"
  if exists "$CONTROLLER/tables/$name"; then
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
# vehicle — its latest position — instead of the LASTWITHTIME/GROUP BY dance
# the other table's REALTIME half requires.
upsert_schema gdansk_public_transport_latest gdansk_public_transport_latest_table_schema.json
upsert_table gdansk_public_transport_latest_REALTIME gdansk_public_transport_latest_realtime_table_config.json

upsert_schema trade trade_table_schema.json
upsert_table trade_REALTIME trade_table_config.json

echo "Schemas and tables are up to date"
