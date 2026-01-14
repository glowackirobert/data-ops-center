#!/bin/bash
set -e

# Wait for controller
until curl -f http://pinot-controller:9000/health; do
  echo "Waiting for controller..."
  sleep 5
done

# Add gdansk public transport schema
curl -X POST -H "Content-Type: application/json" \
  -d @/opt/pinot/scripts/gdansk_public_transport_table_schema.json \
  http://pinot-controller:9000/schemas

# Add gdansk public transport table
curl -X POST -H "Content-Type: application/json" \
  -d @/opt/pinot/scripts/gdansk_public_transport_table_config.json \
  http://pinot-controller:9000/tables

# Add trade schema
curl -X POST -H "Content-Type: application/json" \
  -d @/opt/pinot/scripts/trade_table_schema.json \
  http://pinot-controller:9000/schemas

# Add trade table
curl -X POST -H "Content-Type: application/json" \
  -d @/opt/pinot/scripts/trade_table_config.json \
  http://pinot-controller:9000/tables

echo "Tables added successfully"
