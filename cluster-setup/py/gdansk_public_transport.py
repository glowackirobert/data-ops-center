import requests
import json
import time
from datetime import datetime, timezone

def fetch_and_save_vehicles_data(filename):
    url = 'https://ckan2.multimediagdansk.pl/gpsPositions?v=2'
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        vehicles = data.get('vehicles', [])

        if vehicles:
            with open(filename, 'a', encoding='utf-8') as f:
                for vehicle in vehicles:
                    f.write(json.dumps(vehicle, ensure_ascii=False) + '\n')
            print(f"Data appended to {filename} at {datetime.now(timezone.utc).strftime('%H:%M:%S')}", flush=True)
        else:
            print("No vehicles data found.")
    except Exception as e:
        print(f"Error occurred: {e}")

if __name__ == '__main__':
    filename = datetime.now(timezone.utc).strftime('%Y-%m-%d-%H') + '.txt'
    fetch_and_save_vehicles_data(filename)