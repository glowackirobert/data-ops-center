import os

SECRET_KEY = open('/run/secrets/superset_secret_key').read().strip()

FEATURE_FLAGS = {
    "ENABLE_JAVASCRIPT_CONTROLS": True,
}
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
        "frame-ancestors": [_SELF],
    },
    "force_https": False,
}
