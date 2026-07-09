"""Vehicle map dev server.

Serves index.html and proxies position queries to the Pinot broker so the
browser never talks to Pinot directly (avoids CORS). Stdlib only.

    python web-app/server.py
    # then open http://localhost:3001
"""
import http.cookiejar
import json
import os
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
SECRETS_DIR = os.environ.get(
    'SECRETS_DIR',
    '/run/secrets' if os.path.isdir('/run/secrets')
    else os.path.join(BASE, '..', 'cluster-setup', 'container', 'secrets'),
)
PORT = int(os.environ.get('PORT', '3001'))
PINOT_BROKER_URL = os.environ.get('PINOT_BROKER_URL', 'http://localhost:8099')
MAPBOX_KEY_FILE = os.environ.get(
    'MAPBOX_KEY_FILE', os.path.join(SECRETS_DIR, 'superset_mapbox_api_key'))
# Internal URL for server-to-server Superset API calls vs. the browser-visible
# origin baked into the embedded iframe src.
SUPERSET_INTERNAL_URL = os.environ.get('SUPERSET_INTERNAL_URL', 'http://localhost:8088')
SUPERSET_DOMAIN = os.environ.get('SUPERSET_DOMAIN', 'http://localhost:8088')
DASHBOARD_TITLE = os.environ.get('DASHBOARD_TITLE', 'Gdansk Public Transport')
APPLICATION_JSON = 'application/json'

# Latest position per vehicle seen in the last 10 minutes. The Kafka feed only
# publishes changed positions, so a 10-minute window keeps parked vehicles
# visible while LASTWITHTIME dedupes to the freshest row.
# _REALTIME suffix: skips the OFFLINE side of the hybrid table — its many
# batch-ingested segments add broker planning overhead, and a 10-minute window
# is always within realtime retention (7 days).
POSITIONS_SQL = """
SELECT vehicleId,
       LASTWITHTIME(lat, generatedTransformed, 'DOUBLE')             AS lat,
       LASTWITHTIME(lon, generatedTransformed, 'DOUBLE')             AS lon,
       LASTWITHTIME(routeShortName, generatedTransformed, 'STRING')  AS route,
       LASTWITHTIME(headsign, generatedTransformed, 'STRING')        AS headsign,
       LASTWITHTIME(delay, generatedTransformed, 'INT')              AS delay,
       LASTWITHTIME(speed, generatedTransformed, 'FLOAT')            AS speed,
       LASTWITHTIME(direction, generatedTransformed, 'INT')          AS direction,
       MAX(generatedTransformed)                                     AS lastSeen
FROM gdansk_public_transport_REALTIME
WHERE generatedTransformed > ago('PT10M')
GROUP BY vehicleId
LIMIT 2000
"""


_embedded_uuid = None
_embedded_lock = threading.Lock()


def _read_secret(name):
    with open(os.path.join(SECRETS_DIR, name)) as f:
        return f.read().strip()


def fetch_guest_token():
    """Mint a Superset guest token for the embedded dashboard.

    Logs in as admin, resolves (and caches) the dashboard's embedded UUID,
    creating the embedded registration if superset-init has not run yet.
    """
    global _embedded_uuid
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))

    def call(method, path, body=None, headers=None):
        req = urllib.request.Request(
            SUPERSET_INTERNAL_URL + path,
            data=json.dumps(body).encode('utf-8') if body is not None else None,
            headers={'Content-Type': APPLICATION_JSON, **(headers or {})},
            method=method,
        )
        with opener.open(req, timeout=20) as resp:
            return json.load(resp)

    login = call('POST', '/api/v1/security/login', {
        'username': _read_secret('superset_admin_username'),
        'password': _read_secret('superset_admin_password'),
        'provider': 'db',
        'refresh': True,
    })
    auth = {'Authorization': 'Bearer ' + login['access_token']}
    csrf = call('GET', '/api/v1/security/csrf_token/', None, auth)['result']
    write_headers = {**auth, 'X-CSRFToken': csrf, 'Referer': SUPERSET_INTERNAL_URL}

    with _embedded_lock:
        if _embedded_uuid is None:
            dashboards = call('GET', '/api/v1/dashboard/', None, auth)['result']
            dash = next(d for d in dashboards if d['dashboard_title'] == DASHBOARD_TITLE)
            try:
                emb = call('GET', f"/api/v1/dashboard/{dash['id']}/embedded", None, auth)
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                emb = call('POST', f"/api/v1/dashboard/{dash['id']}/embedded",
                           {'allowed_domains': []}, write_headers)
            _embedded_uuid = emb['result']['uuid']

    guest = call('POST', '/api/v1/security/guest_token/', {
        'user': {'username': 'embedded-guest'},
        'resources': [{'type': 'dashboard', 'id': _embedded_uuid}],
        'rls': [],
    }, write_headers)
    return {'token': guest['token'], 'embeddedUuid': _embedded_uuid,
            'supersetDomain': SUPERSET_DOMAIN}


class Handler(BaseHTTPRequestHandler):

    def _send(self, code, body, content_type):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, code, payload):
        self._send(code, json.dumps(payload), APPLICATION_JSON)

    def do_GET(self):
        routes = {
            '/': self._serve_index,
            '/index.html': self._serve_index,
            '/api/config': self._serve_config,
            '/api/guest-token': self._serve_guest_token,
            '/api/positions': self._serve_positions,
        }
        route = routes.get(self.path)
        try:
            if route:
                route()
            else:
                self._send(404, 'not found', 'text/plain')
        except Exception as e:  # keep the dev server alive on any request error
            self._send_json(500, {'error': str(e)})

    def _serve_index(self):
        with open(os.path.join(BASE, 'index.html'), 'rb') as f:
            self._send(200, f.read(), 'text/html; charset=utf-8')

    def _serve_config(self):
        with open(MAPBOX_KEY_FILE) as f:
            self._send_json(200, {'mapboxToken': f.read().strip()})

    def _serve_guest_token(self):
        # Docker's embedded DNS occasionally fails a lookup; retry.
        for attempt in range(3):
            try:
                payload = fetch_guest_token()
                break
            except urllib.error.URLError:
                if attempt == 2:
                    raise
                time.sleep(1)
        self._send_json(200, payload)

    def _serve_positions(self):
        req = urllib.request.Request(
            PINOT_BROKER_URL + '/query/sql',
            data=json.dumps({'sql': POSITIONS_SQL}).encode('utf-8'),
            headers={'Content-Type': APPLICATION_JSON},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.load(resp)
        if result.get('exceptions'):
            self._send_json(502, {'error': result['exceptions']})
            return
        cols = result['resultTable']['dataSchema']['columnNames']
        rows = [dict(zip(cols, r)) for r in result['resultTable']['rows']]
        self._send_json(200, rows)

    def log_message(self, fmt, *args):
        pass  # silence per-request noise


if __name__ == '__main__':
    print(f'Serving on http://localhost:{PORT} (Pinot broker: {PINOT_BROKER_URL})')
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
