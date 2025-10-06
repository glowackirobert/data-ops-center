import boto3
import subprocess
import time

# Configuration
s3_bucket = 'gdansk-public-transport'
s3_prefix = ''  # if all folders in bucket, keep empty
pinot_admin_path = '/path/to/pinot-admin.sh'
base_job_spec_file = '/opt/pinot/scripts/gdansk_public_transport_ingestion_job_spec.yaml'

# Initialize boto3 S3 client
s3_client = boto3.client(
    's3',
    aws_access_key_id='YOUR_ACCESS_KEY',
    aws_secret_access_key='YOUR_SECRET_KEY',
    region_name='YOUR_REGION'
)

def list_s3_directories(bucket, prefix):
    paginator = s3_client.get_paginator('list_objects_v2')
    result = paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter='/')
    prefixes = []
    for page in result:
        if 'CommonPrefixes' in page:
            for cp in page['CommonPrefixes']:
                prefixes.append(cp['Prefix'])
    return prefixes

def launch_ingestion_job(folder):
    # Assuming job spec uses ${folder} as placeholder for input directory
    print(f"Launching ingestion job for folder: {folder}")

    # Construct the command with -values to override the folder variable
    command = [
        pinot_admin_path,
        'LaunchDataIngestionJob',
        '-jobSpecFile', base_job_spec_file,
        '-values', f'folder={folder}'
    ]

    # Run the command and wait for it to complete
    process = subprocess.run(command, capture_output=True, text=True)
    if process.returncode != 0:
        print(f"Error launching job for {folder}: {process.stderr}")
        return False

    print(f"Job launched successfully for folder: {folder}")
    return True

def main():
    folders = list_s3_directories(s3_bucket, s3_prefix)
    print(f"Found {len(folders)} folders to ingest.")

    # for folder in folders:
        # Ensure each ingestion is sequential
        # success = launch_ingestion_job(folder)
        # if not success:
            # print(f"Terminating on failure for folder: {folder}")
            # break
        # Optionally add a wait or job status tracking here to ensure ingestion completion

if __name__ == "__main__":
    main()
