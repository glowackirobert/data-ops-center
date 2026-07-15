#!/bin/bash
set -e

BOOTSTRAP_SERVER="${KAFKA_BOOTSTRAP_SERVER:-kafka:9092}"

create_topic() {
  local topic="$1"
  echo "Creating topic $topic..."
  env -u KAFKA_OPTS /opt/kafka/bin/kafka-topics.sh \
    --bootstrap-server "$BOOTSTRAP_SERVER" \
    --create --if-not-exists \
    --topic "$topic" \
    --partitions 1 \
    --replication-factor 1
  echo "Topic $topic is ready"
}

create_topic trade
create_topic gdansk-public-transport

echo "Kafka topics are up to date"
