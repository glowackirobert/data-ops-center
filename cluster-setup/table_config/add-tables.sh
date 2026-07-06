#!/bin/bash
set -e

MAX_RETRIES=5
RETRY_DELAY=5

post_with_retry() {
  local description="$1"
  local url="$2"
  local file="$3"
  local attempt=1

  while [ $attempt -le $MAX_RETRIES ]; do
    echo "Adding $description (attempt $attempt/$MAX_RETRIES)..."
    local response http_code
    response=$(curl -s -w "\n%{http_code}" -X POST \
        -H "Content-Type: application/json" \
        -d @"$file" \
        "$url")
    http_code=$(echo "$response" | tail -n1)
    if [ "$http_code" = "200" ]; then
      echo "$response" | sed '$d'
      echo "$description added successfully"
      return 0
    fi
    echo "Attempt $attempt failed (HTTP $http_code): $(echo "$response" | sed '$d')"
    echo "Retrying in ${RETRY_DELAY}s..."
    sleep $RETRY_DELAY
    attempt=$((attempt + 1))
  done

  echo "ERROR: Failed to add $description after $MAX_RETRIES attempts"
  return 1
}

GDANSK_PUBLIC_TRANSPORT_TABLE="gdansk_public_transport_OFFLINE"
URL="http://pinot-controller:9000/tables/$GDANSK_PUBLIC_TRANSPORT_TABLE"
if [ "$(curl -s -o /dev/null -w "%{http_code}" "$URL")" = "200" ]; then
  echo "Deleting table: $GDANSK_PUBLIC_TRANSPORT_TABLE"
  curl -X DELETE "$URL"
  echo
else
  echo "Table $GDANSK_PUBLIC_TRANSPORT_TABLE does not exist, skipping"
fi

GDANSK_PUBLIC_TRANSPORT_REALTIME_TABLE="gdansk_public_transport_REALTIME"
URL="http://pinot-controller:9000/tables/$GDANSK_PUBLIC_TRANSPORT_REALTIME_TABLE"
if [ "$(curl -s -o /dev/null -w "%{http_code}" "$URL")" = "200" ]; then
  echo "Deleting table: $GDANSK_PUBLIC_TRANSPORT_REALTIME_TABLE"
  curl -X DELETE "$URL"
  echo
else
  echo "Table $GDANSK_PUBLIC_TRANSPORT_REALTIME_TABLE does not exist, skipping"
fi


GDANSK_PUBLIC_TRANSPORT_SCHEMA="gdansk_public_transport"
URL="http://pinot-controller:9000/schemas/$GDANSK_PUBLIC_TRANSPORT_SCHEMA"
if [ "$(curl -s -o /dev/null -w "%{http_code}" "$URL")" = "200" ]; then
  echo "Deleting schema: $GDANSK_PUBLIC_TRANSPORT_SCHEMA"
  curl -X DELETE "$URL"
  echo
else
  echo "Schema $GDANSK_PUBLIC_TRANSPORT_SCHEMA does not exist, skipping"
fi


TRADE_TABLE="trade_REALTIME"
URL="http://pinot-controller:9000/tables/$TRADE_TABLE"
if [ "$(curl -s -o /dev/null -w "%{http_code}" "$URL")" = "200" ]; then
  echo "Deleting table: $TRADE_TABLE"
  curl -X DELETE "$URL"
  echo
else
  echo "Table $TRADE_TABLE does not exist, skipping"
fi


TRADE_SCHEMA="trade"
URL="http://pinot-controller:9000/schemas/$TRADE_SCHEMA"
if [ "$(curl -s -o /dev/null -w "%{http_code}" "$URL")" = "200" ]; then
  echo "Deleting schema: $TRADE_SCHEMA"
  curl -X DELETE "$URL"
  echo
else
  echo "Schema $TRADE_SCHEMA does not exist, skipping"
fi


post_with_retry "gdansk_public_transport schema" \
  "http://pinot-controller:9000/schemas" \
  "/opt/pinot/scripts/gdansk_public_transport_table_schema.json"

post_with_retry "gdansk_public_transport offline table" \
  "http://pinot-controller:9000/tables" \
  "/opt/pinot/scripts/gdansk_public_transport_offline_table_config.json"

post_with_retry "gdansk_public_transport realtime table" \
  "http://pinot-controller:9000/tables" \
  "/opt/pinot/scripts/gdansk_public_transport_realtime_table_config.json"

post_with_retry "trade schema" \
  "http://pinot-controller:9000/schemas" \
  "/opt/pinot/scripts/trade_table_schema.json"

post_with_retry "trade table" \
  "http://pinot-controller:9000/tables" \
  "/opt/pinot/scripts/trade_table_config.json"

echo "Tables added successfully"