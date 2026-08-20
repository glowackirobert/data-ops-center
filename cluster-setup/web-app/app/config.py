"""Environment-derived configuration: paths, URLs, and tunable constants.

Kept free of any request-handling or domain logic so every other module can
import it without pulling in HTTP, GTFS, or Pinot dependencies.
"""
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECRETS_DIR = os.environ.get(
    'SECRETS_DIR',
    '/run/secrets' if os.path.isdir('/run/secrets')
    else os.path.join(BASE, '..', 'container', 'secrets'),
)
PORT = int(os.environ.get('PORT', '3001'))
PINOT_BROKER_URL = os.environ.get('PINOT_BROKER_URL', 'http://localhost:8099')
PINOT_CONTROLLER_URL = os.environ.get('PINOT_CONTROLLER_URL', 'http://localhost:9000')
MAPBOX_KEY_FILE = os.environ.get(
    'MAPBOX_KEY_FILE', os.path.join(SECRETS_DIR, 'superset_mapbox_api_key'))
# Internal URL for server-to-server Superset API calls vs. the browser-visible
# origin baked into the embedded iframe src.
SUPERSET_INTERNAL_URL = os.environ.get('SUPERSET_INTERNAL_URL', 'http://localhost:8088')
SUPERSET_DOMAIN = os.environ.get('SUPERSET_DOMAIN', 'http://localhost:8088')
DASHBOARD_TITLE = os.environ.get('DASHBOARD_TITLE', 'Gdansk Public Transport')
APPLICATION_JSON = 'application/json'

# --- GTFS ---
GTFS_URL = os.environ.get(
    'GTFS_URL',
    'https://ckan.multimediagdansk.pl/dataset/c24aa637-3619-4dc2-a171-a23eec8f2172'
    '/resource/30e783e4-2bec-4a7d-bb22-ee3e3b26ca96/download/gtfsgoogle.zip')
GTFS_REFRESH_S = int(os.environ.get('GTFS_REFRESH_S', str(6 * 3600)))

# A vehicle standing within this range of its course's scheduled first or last
# stop is "at the terminus" (loops and adjacent depot yards are large).
TERMINUS_RADIUS_M = 300

# --- Route shapes ---
SHAPES_URL = os.environ.get('SHAPES_URL', 'https://ckan2.multimediagdansk.pl/shapes')


def read_secret(path):
    """Read and strip a secret file.

    Absolute paths (e.g. MAPBOX_KEY_FILE, which may be overridden wholesale
    via env var) are used as-is; bare names are resolved under SECRETS_DIR.
    Single entry point so callers don't each open()/strip() independently.
    """
    full = path if os.path.isabs(path) else os.path.join(SECRETS_DIR, path)
    with open(full) as f:
        return f.read().strip()
