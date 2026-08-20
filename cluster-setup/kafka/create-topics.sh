#!/bin/bash
# Creates the Kafka topics the cluster consumes from (see CLAUDE.md § Kafka).
# Idempotent: re-running it leaves existing topics untouched.
set -euo pipefail

# Overridable so the script can be run from the host (localhost:9092)
BOOTSTRAP_SERVER="${KAFKA_BOOTSTRAP_SERVER:-kafka:9092}"
KAFKA_TOPICS_CMD="${KAFKA_TOPICS_CMD:-/opt/kafka/bin/kafka-topics.sh}"
# Single-broker cluster — matches KAFKA_*_REPLICATION_FACTOR in container-compose.yml
REPLICATION_FACTOR="${KAFKA_REPLICATION_FACTOR:-1}"
MAX_RETRIES="${KAFKA_TOPIC_MAX_RETRIES:-5}"
RETRY_DELAY="${KAFKA_TOPIC_RETRY_DELAY:-5}"

# "name:partitions", the single source of truth for what this cluster expects.
TOPICS=(
  "trade:3"
  "gdansk-public-transport:1"
)

# The Kafka image sets KAFKA_OPTS to the JMX javaagent bound to port 19092; a CLI
# tool inheriting it would try to bind that port a second time and die.
unset KAFKA_OPTS

kafka_topics() {
  "$KAFKA_TOPICS_CMD" --bootstrap-server "$BOOTSTRAP_SERVER" "$@"
}

# Echoes the partition count of topic $1, or nothing if it does not exist yet
# (also nothing if the broker is unreachable — the caller retries either way).
partition_count() {
  local described
  described=$(kafka_topics --describe --topic "$1" 2>/dev/null) || return 0
  if [[ $described =~ PartitionCount:[[:space:]]*([0-9]+) ]]; then
    printf '%s' "${BASH_REMATCH[1]}"
  fi
  return 0
}

create_topic() {
  local topic="$1" partitions="$2" attempt=1

  while [ "$attempt" -le "$MAX_RETRIES" ]; do
    echo "Creating topic $topic ($partitions partitions, attempt $attempt/$MAX_RETRIES)..."
    # --if-not-exists keeps a concurrent run from turning into an error
    if kafka_topics --create --if-not-exists \
        --topic "$topic" \
        --partitions "$partitions" \
        --replication-factor "$REPLICATION_FACTOR"; then
      return 0
    fi
    echo "Attempt $attempt failed. Retrying in ${RETRY_DELAY}s..."
    sleep "$RETRY_DELAY"
    attempt=$((attempt + 1))
  done

  echo "ERROR: creating topic $topic failed after $MAX_RETRIES attempts" >&2
  return 1
}

ensure_topic() {
  local topic="$1" partitions="$2" existing
  existing=$(partition_count "$topic")

  if [ -z "$existing" ]; then
    create_topic "$topic" "$partitions"
    echo "Topic $topic is ready ($partitions partitions)"
    return 0
  fi

  # Never repartitioned automatically: gdansk-public-transport is keyed by
  # vehicleId, and changing the partition count remaps key -> partition, which
  # breaks per-key ordering and with it the FULL upsert on
  # gdansk_public_transport_latest. Repartition by hand if that is really meant.
  if [ "$existing" != "$partitions" ]; then
    echo "WARNING: topic $topic has $existing partitions, expected $partitions - leaving it as is" >&2
  fi
  echo "Topic $topic already exists ($existing partitions)"
}

for spec in "${TOPICS[@]}"; do
  ensure_topic "${spec%%:*}" "${spec##*:}"
done

echo "Kafka topics are up to date"
