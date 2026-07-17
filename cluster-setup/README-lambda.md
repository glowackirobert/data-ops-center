# AWS Lambda (Gdansk Public Transport)

The Lambda fetches GPS positions from the Gdansk public transport API and stores JSON-lines files in the S3 bucket `gdansk-public-transport` under `raw/YYYY/MM/DD/YYYY-MM-DD-HH-MM.txt`.

Handler entry point: `gdansk_public_transport_aws.lambda_handler`.

Deployment is automated by `.github/workflows/lambda-function.yaml`. To package manually:

```bash
rm -rf aws_lambda
mkdir -p aws_lambda
cd aws_lambda
pip install 'requests~=2.32' -t .  # boto3 is provided by the Lambda runtime
cp ../cluster-setup/py/gdansk_public_transport_aws.py .
# Windows PowerShell:
Compress-Archive -Path * -DestinationPath function.zip

# Apply it to the existing function (same call the CI workflow makes):
aws lambda update-function-code \
  --function-name GdanskPublicTransportHandler \
  --zip-file fileb://function.zip \
  --region eu-north-1
```

This assumes the `GdanskPublicTransportHandler` function, its execution role (S3 write
access to `gdansk-public-transport`), and its EventBridge trigger schedule already exist —
provisioning those from scratch isn't automated by this repo.
