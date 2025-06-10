import requests
import schedule
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
            filename = datetime.now().strftime('%Y-%m-%d-%H') + '.txt'  # Add hour to filename for uniqueness
            with open(filename, 'a', encoding='utf-8') as f:
                for vehicle in vehicles:
                    f.write(json.dumps(vehicle, ensure_ascii=False) + '\n')

            print(f"Data appended to {filename} at {datetime.now().strftime('%H:%M:%S')}", flush=True)
        else:
            print("No vehicles data found.")

    except Exception as e:
        print(f"Error occurred: {e}")

if __name__ == '__main__':
    # Schedule the job to run at the start of every hour (hh:00:00)
    schedule.every().hour.at(":00").do(fetch_and_save_vehicles_data)
    print("Script started. Waiting for the next full hour...", flush=True)

    # Run the job once immediately if the current time is at :00 minutes and :00 seconds
    now = datetime.now()
    if now.minute == 0 and now.second == 0:
        fetch_and_save_vehicles_data()

    # Continuously check for pending jobs
    while True:
        schedule.run_pending()
        time.sleep(1)  # Check every second (avoids timing drift)