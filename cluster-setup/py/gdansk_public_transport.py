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
            print(f"Data appended to {filename} at {datetime.now().strftime('%H:%M:%S')}", flush=True)
        else:
            print("No vehicles data found.")
    except Exception as e:
        print(f"Error occurred: {e}")

if __name__ == '__main__':
    # Wait until HH:MM:00 before starting execution
    now = datetime.now(timezone.utc)
    target_time = now.replace(second=0, microsecond=0)

    if now.second > 0:
        wait_time = (60 - now.second)
        time.sleep(wait_time)

    # Create filename for current UTC hour
    filename = now.strftime('%Y-%m-%d-%H') + '.txt'

    # Run fetch every minute until HH:59
    while datetime.now(timezone.utc).minute < 59:
        fetch_and_save_vehicles_data(filename)
        time.sleep(60)  # Wait exactly one minute

    # Final fetch at HH:59 before exiting
    fetch_and_save_vehicles_data(filename)