import requests
import json
import time
from datetime import datetime

def fetch_and_save_vehicles_data():
    url = 'https://ckan2.multimediagdansk.pl/gpsPositions?v=2'
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        vehicles = data.get('vehicles', [])

        if vehicles:
            filename = datetime.now().strftime('%Y-%m-%d-%H') + '.txt'
            with open(filename, 'a', encoding='utf-8') as f:
                for vehicle in vehicles:
                    f.write(json.dumps(vehicle, ensure_ascii=False) + '\n')
            print(f"Data appended to {filename} at {datetime.now().strftime('%H:%M:%S')}", flush=True)
        else:
            print("No vehicles data found.")
    except Exception as e:
        print(f"Error occurred: {e}")

if __name__ == '__main__':
# Create filename for current UTC hour
    current_hour = datetime.utcnow().replace(minute=0, second=0, microsecond=0)
    filename = current_hour.strftime('%Y-%m-%d-%H') + '.txt'

    # Calculate end time (HH:59)
    start_time = datetime.utcnow()
    end_time = start_time.replace(minute=59, second=0, microsecond=0)

    while datetime.utcnow() < end_time:
        next_run = datetime.utcnow() + timedelta(seconds=60)
        fetch_and_save_vehicles_data(filename)

        # Sleep until next full minute
        sleep_time = (next_run - datetime.utcnow()).total_seconds()
        if sleep_time > 0:
            time.sleep(sleep_time)
    # Final fetch at HH:59 before script exits
    fetch_and_save_vehicles_data(filename)