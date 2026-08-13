#!/bin/bash
set -e

BOOTSTRAP_SERVER="${KAFKA_BOOTSTRAP_SERVER:-kafka:9092}"

create_topic() {
  local topic="$1"
  local partitions="${2:-1}"
  echo "Creating topic $topic ($partitions partitions)..."
  env -u KAFKA_OPTS /opt/kafka/bin/kafka-topics.sh \
    --bootstrap-server "$BOOTSTRAP_SERVER" \
    --create --if-not-exists \
    --topic "$topic" \
    --partitions "$partitions" \
    --replication-factor 1
  echo "Topic $topic is ready"
}

create_topic trade 3
create_topic gdansk-public-transport

echo "Kafka topics are up to date"
