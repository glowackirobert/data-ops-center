import json
import os
import signal
import sys
import time
import requests
from confluent_kafka import Producer

TOPIC = 'gdansk-public-transport'
API_URL = 'https://ckan2.multimediagdansk.pl/gpsPositions?v=2'
# The Gdansk API recomputes vehicle positions roughly every 20 s, but on a
# phase we neither know nor control, so polling at half that interval halves
# the worst-case age of a position by the time it reaches Kafka. The cost is
# re-reading an unchanged payload about every other poll — harmless here,
# since every vehicle is republished unconditionally anyway to keep
# gdansk_public_transport_latest's per-vehicle heartbeat going.
POLL_INTERVAL_SECONDS = 10

def fetch_vehicles():
    response = requests.get(API_URL, timeout=10)
    response.raise_for_status()
    return response.json().get('vehicles', [])

def delivery_report(err, msg):
    if err:
        print(f"Delivery failed for vehicleId {msg.key()}: {err}", file=sys.stderr)

def publish_to_kafka(producer, vehicles):
    for vehicle in vehicles:
        key = str(vehicle.get('vehicleId', ''))
        value = json.dumps(vehicle, ensure_ascii=False).encode('utf-8')
        producer.produce(TOPIC, key=key, value=value, callback=delivery_report)

    remaining = producer.flush(timeout=30)
    if remaining:
        print(f"Warning: {remaining} messages were not delivered.", file=sys.stderr)

    return len(vehicles)

def main():
    bootstrap_servers = os.environ.get('KAFKA_BOOTSTRAP_SERVERS', 'localhost:29092')
    # idempotence implies acks=all and retry-safe delivery, so broker restarts
    # cannot duplicate or reorder messages within a partition
    producer = Producer({
        'bootstrap.servers': bootstrap_servers,
        'enable.idempotence': True,
    })

    def shutdown(signum, frame):
        print("Received shutdown signal, flushing producer...", flush=True)
        producer.flush(timeout=10)
        sys.exit(0)

    # docker stop sends SIGTERM; without a handler the poll sleep would be
    # killed mid-cycle and any queued messages lost
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    while True:
        try:
            vehicles = fetch_vehicles()
            # No freshness/dedup filtering: every vehicle is republished every
            # cycle so the gdansk_public_transport_latest upsert table always
            # has a current row per vehicleId, even when its GPS payload is
            # unchanged since the last poll.
            sent = publish_to_kafka(producer, vehicles)
            print(f"Published {sent} vehicles")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
        time.sleep(POLL_INTERVAL_SECONDS)

if __name__ == '__main__':
    main()
