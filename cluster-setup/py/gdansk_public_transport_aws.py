import json
import requests
from datetime import datetime, timezone
import boto3
from botocore.config import Config
from io import StringIO

s3 = boto3.client('s3', config=Config(connect_timeout=5, read_timeout=10))
S3_BUCKET_NAME = 'gdansk-public-transport'

# Errors deliberately propagate out of the handler: a raised exception marks
# the invocation as failed, which feeds the Lambda Errors metric and enables
# CloudWatch alarms + automatic retries.
def fetch_vehicles_data():
    url = 'https://ckan2.multimediagdansk.pl/gpsPositions?v=2'
    response = requests.get(url, timeout=10)
    response.raise_for_status()
    return response.json().get('vehicles', [])

def upload_vehicles_data_to_s3(vehicles, bucket_name):
    if not vehicles:
        print("No vehicles data to upload.")
        return

    now = datetime.now(timezone.utc)
    s3_key = now.strftime('raw/%Y/%m/%d/%Y-%m-%d-%H-%M.txt')

    # Serialize vehicles JSON lines to a string buffer
    buffer = StringIO()
    for vehicle in vehicles:
        buffer.write(json.dumps(vehicle, ensure_ascii=False) + '\n')
    buffer.seek(0)

    s3.put_object(
        Bucket=bucket_name,
        Key=s3_key,
        Body=buffer.getvalue().encode('utf-8')
    )
    print(f"Uploaded data to s3://{bucket_name}/{s3_key}")

def lambda_handler(event, context):
    vehicles = fetch_vehicles_data()
    upload_vehicles_data_to_s3(vehicles, S3_BUCKET_NAME)

    return {
        'statusCode': 200,
        'body': 'Fetch and upload completed'
    }
