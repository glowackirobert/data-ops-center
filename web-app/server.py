"""Vehicle map dev server.

Serves index.html and proxies position queries to the Pinot broker so the
browser never talks to Pinot directly (avoids CORS). Stdlib only.

    python web-app/server.py
    # then open http://localhost:3001
"""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get('PORT', '3001'))
PINOT_BROKER_URL = os.environ.get('PINOT_BROKER_URL', 'http://localhost:8099')
MAPBOX_KEY_FILE = os.environ.get(
    'MAPBOX_KEY_FILE',
    os.path.join(BASE, '..', 'cluster-setup', 'container', 'secrets', 'superset_mapbox_api_key'),
)

# Latest position per vehicle seen in the last 10 minutes. The Kafka feed only
# publishes changed positions (120 s poll), so a 10-minute window keeps parked
# vehicles visible while LASTWITHTIME dedupes to the freshest row.
POSITIONS_SQL = """
SELECT vehicleId,
       LASTWITHTIME(lat, generatedTransformed, 'DOUBLE')             AS lat,
       LASTWITHTIME(lon, generatedTransformed, 'DOUBLE')             AS lon,
       LASTWITHTIME(routeShortName, generatedTransformed, 'STRING')  AS route,
       LASTWITHTIME(headsign, generatedTransformed, 'STRING')        AS headsign,
       LASTWITHTIME(delay, generatedTransformed, 'INT')              AS delay,
       LASTWITHTIME(speed, generatedTransformed, 'FLOAT')            AS speed,
       MAX(generatedTransformed)                                     AS lastSeen
FROM gdansk_public_transport
WHERE generatedTransformed > ago('PT10M')
GROUP BY vehicleId
LIMIT 2000
"""


class Handler(BaseHTTPRequestHandler):

    def _send(self, code, body, content_type):
        data = body if isinstance(body, bytes) else body.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        try:
            if self.path in ('/', '/index.html'):
                with open(os.path.join(BASE, 'index.html'), 'rb') as f:
                    self._send(200, f.read(), 'text/html; charset=utf-8')
            elif self.path == '/api/config':
                with open(MAPBOX_KEY_FILE) as f:
                    token = f.read().strip()
                self._send(200, json.dumps({'mapboxToken': token}), 'application/json')
            elif self.path == '/api/positions':
                req = urllib.request.Request(
                    PINOT_BROKER_URL + '/query/sql',
                    data=json.dumps({'sql': POSITIONS_SQL}).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    result = json.load(resp)
                if result.get('exceptions'):
                    self._send(502, json.dumps({'error': result['exceptions']}), 'application/json')
                    return
                cols = result['resultTable']['dataSchema']['columnNames']
                rows = [dict(zip(cols, r)) for r in result['resultTable']['rows']]
                self._send(200, json.dumps(rows), 'application/json')
            else:
                self._send(404, 'not found', 'text/plain')
        except Exception as e:  # keep the dev server alive on any request error
            self._send(500, json.dumps({'error': str(e)}), 'application/json')

    def log_message(self, fmt, *args):
        pass  # silence per-request noise


if __name__ == '__main__':
    print(f'Serving on http://localhost:{PORT} (Pinot broker: {PINOT_BROKER_URL})')
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
