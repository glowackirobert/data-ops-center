import os

SECRET_KEY = open('/run/secrets/superset_secret_key').read().strip()

FEATURE_FLAGS = {
    "ENABLE_JAVASCRIPT_CONTROLS": True,
    "EMBEDDED_SUPERSET": True,
    # Only fetch data for charts the viewer can actually see. Without this,
    # DASHBOARD_VIRTUALIZATION (on by default) skips *rendering* off-screen
    # charts but still runs their queries: Chart.runQuery() checks
    # isInView only when this flag is set. Both native filters are scoped to
    # all ten charts across all four tabs, so changing the Route filter fired
    # ten Pinot queries once every tab had been opened - eight of them for
    # charts nobody was looking at. With no result cache (see
    # DATA_CACHE_CONFIG below) each one was a live broker query.
    "DASHBOARD_VIRTUALIZATION_DEFER_DATA": True,
}

# Embedded dashboards (web-app on port 3001 iframes the dashboard via the
# Embedded SDK). Guest tokens are minted by the web-app backend; the
# EmbeddedGuest role is created/synced by superset-init.sh.
GUEST_ROLE_NAME = "EmbeddedGuest"
GUEST_TOKEN_JWT_SECRET = SECRET_KEY + "-guest-token"

# Single-instance deployment: in-memory rate-limit storage is sufficient.
# Set explicitly to silence the Flask-Limiter default-storage warning.
# Switch to redis:// if Superset is ever scaled to multiple containers.
RATELIMIT_STORAGE_URI = "memory://"

# Query results are deliberately NOT cached: every chart render re-runs its
# query against Pinot (the chart YAMLs also pin cache_timeout: -1 = bypass, so
# re-enabling a data cache backend later won't silently bring caching back).
# Costs the query + pandas pipeline (~1-3 s per chart) on each dashboard view.
DATA_CACHE_CONFIG = {"CACHE_TYPE": "NullCache"}

# UI-state caches (dashboard/dataset metadata, native-filter state, Explore
# permalinks) hold no query results and stay on FileSystemCache: shared across
# gunicorn workers (SimpleCache is per-process), no Redis dependency, and
# /app/superset_home is the persisted volume so they survive restarts.
_FS_CACHE = {
    "CACHE_TYPE": "FileSystemCache",
    "CACHE_DEFAULT_TIMEOUT": 3600,
    "CACHE_THRESHOLD": 2000,
}
CACHE_CONFIG = {**_FS_CACHE, "CACHE_DIR": "/app/superset_home/cache/metadata"}
FILTER_STATE_CACHE_CONFIG = {**_FS_CACHE, "CACHE_DIR": "/app/superset_home/cache/filter_state"}
EXPLORE_FORM_DATA_CACHE_CONFIG = {**_FS_CACHE, "CACHE_DIR": "/app/superset_home/cache/explore_form_data"}
MAPBOX_API_KEY = open('/run/secrets/superset_mapbox_api_key').read().strip() if os.path.exists('/run/secrets/superset_mapbox_api_key') else ""

_SELF = "'self'"
_UNSAFE_INLINE = "'unsafe-inline'"
_MAPBOX = "https://api.mapbox.com"
_MAPBOX_WILDCARD = "https://*.mapbox.com"

TALISMAN_CONFIG = {
    "content_security_policy": {
        "base-uri": [_SELF],
        "default-src": [_SELF],
        "img-src": [_SELF, "blob:", "data:", _MAPBOX, _MAPBOX_WILDCARD],
        "worker-src": ["blob:", "https://dygraphs.com"],
        "connect-src": [_SELF, _MAPBOX, _MAPBOX_WILDCARD, "https://events.mapbox.com"],
        "object-src": ["'none'"],
        "style-src": [_SELF, _UNSAFE_INLINE],
        "script-src": [_SELF, _UNSAFE_INLINE, "'unsafe-eval'"],
        "frame-src": [_SELF],
        # Allow the web-app to iframe embedded dashboards. WEBAPP_ORIGIN must
        # be the browser-visible web-app origin (e.g. http://<ec2-host>:3001).
        "frame-ancestors": [_SELF, os.environ.get("WEBAPP_ORIGIN", "http://localhost:3001")],
    },
    "force_https": False,
    # Stack runs over plain HTTP; a Secure-flagged session cookie would never
    # be sent back by non-browser clients (breaks the web-app guest-token flow).
    "session_cookie_secure": False,
}
