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
    print("Script started. Waiting for the next full minute...", flush=True)
    # Wait until the next full minute
    now = datetime.now()
    seconds_to_next_minute = 60 - now.second
    time.sleep(seconds_to_next_minute)


    while True:
        fetch_and_save_vehicles_data()
        # Sleep until the next full minute
        now = datetime.now()
        seconds_to_next_minute = 60 - now.second
        time.sleep(seconds_to_next_minute)
