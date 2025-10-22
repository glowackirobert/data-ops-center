# This is set of instruction that needs to be performed in order to prepare zipped lambda function that could be manually uploaded into AWS Lambda 

rm -rf aws_lambda
mkdir -p aws_lambda
cd aws_lambda
pip install requests -t .
pip install schedule -t .
pip install boto3 -t .
cp ../cluster-setup/py/gdansk_public_transport.py .
Compress-Archive -Path * -DestinationPath function.zip