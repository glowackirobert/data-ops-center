import json
import os
import sys
import time
import requests
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
    last_generated: dict[str, str] = {}

    while True:
        try:
            vehicles = fetch_vehicles()
            fresh = [
                v for v in vehicles
                if v.get('generated') != last_generated.get(str(v.get('vehicleId')))
            ]
            if fresh:
                sent = publish_to_kafka(producer, fresh)
                for v in fresh:
                    last_generated[str(v.get('vehicleId'))] = v.get('generated')
                print(f"Published {sent} updated vehicles out of {len(vehicles)} total")
            else:
                print("No vehicle positions changed, skipping.")
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
        time.sleep(4)

if __name__ == '__main__':
    main()
