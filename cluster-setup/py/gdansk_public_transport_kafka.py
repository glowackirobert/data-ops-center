import json
import os
import sys
import time
import requests
from datetime import datetime, timezone
from confluent_kafka import Producer

TOPIC = 'gdansk-public-transport'
API_URL = 'https://ckan2.multimediagdansk.pl/gpsPositions?v=2'

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
    producer = Producer({'bootstrap.servers': bootstrap_servers})

    while True:
        try:
            vehicles = fetch_vehicles()
            if not vehicles:
                print("No vehicles data returned.")
            else:
                sent = publish_to_kafka(producer, vehicles)
                print(f"Published {sent} messages to '{TOPIC}' at {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
        time.sleep(1)

if __name__ == '__main__':
    main()
