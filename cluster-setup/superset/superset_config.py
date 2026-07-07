import os

SECRET_KEY = open('/run/secrets/superset_secret_key').read().strip()

FEATURE_FLAGS = {
    "ENABLE_JAVASCRIPT_CONTROLS": True,
    "EMBEDDED_SUPERSET": True,
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
