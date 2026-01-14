#!/bin/bash
set -e

# Wait for controller
until curl -f http://pinot-controller:9000/health; do
  echo "Waiting for controller..."
  sleep 5
done

# Add gdansk table
pinot-admin.sh AddTable \
  -schemaFile /opt/pinot/scripts/gdansk_public_transport_table_schema.json \
  -tableConfigFile /opt/pinot/scripts/gdansk_public_transport_table_config.json \
  -controllerHost pinot-controller -controllerPort 9000 -exec

# Add trade table
./bin/pinot-admin.sh AddTable \
  -schemaFile /opt/pinot/scripts/trade_realtime_schema.json \
  -tableConfigFile /opt/pinot/scripts/trade_realtime_config.json \
  -controllerHost pinot-controller -controllerPort 9000 -exec

echo "Tables added successfully"
