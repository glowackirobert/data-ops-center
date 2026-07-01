import os

SECRET_KEY = open('/run/secrets/superset_secret_key').read().strip()
MAPBOX_API_KEY = open('/run/secrets/superset_maptiler_api_key').read().strip() if os.path.exists('/run/secrets/superset_maptiler_api_key') else ""
