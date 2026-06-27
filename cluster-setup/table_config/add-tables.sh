#!/bin/bash
set -e

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


# Add gdansk public transport schema
curl --fail -X POST \
  -H "Content-Type: application/json" \
  -d @/opt/pinot/scripts/gdansk_public_transport_table_schema.json \
  http://pinot-controller:9000/schemas
echo

# Add gdansk public transport offline table
curl --fail -X POST \
  -H "Content-Type: application/json" \
  -d @/opt/pinot/scripts/gdansk_public_transport_table_config.json \
  http://pinot-controller:9000/tables
echo

# Add gdansk public transport realtime table
curl --fail -X POST \
  -H "Content-Type: application/json" \
  -d @/opt/pinot/scripts/gdansk_public_transport_realtime_table_config.json \
  http://pinot-controller:9000/tables
echo

# Add trade schema
curl --fail -X POST \
  -H "Content-Type: application/json" \
  -d @/opt/pinot/scripts/trade_table_schema.json \
  http://pinot-controller:9000/schemas
echo

# Add trade table
curl --fail -X POST \
  -H "Content-Type: application/json" \
  -d @/opt/pinot/scripts/trade_table_config.json \
  http://pinot-controller:9000/tables
echo

echo "Tables added successfully"
