"""Vehicle map dev server.

Serves index.html and proxies position queries to the Pinot broker so the
browser never talks to Pinot directly (avoids CORS). Stdlib only.

    python web-app/server.py
    # then open http://localhost:3001
"""
import csv
import datetime
import http.cookiejar
import io
import json
import os
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

BASE = os.path.dirname(os.path.abspath(__file__))
SECRETS_DIR = os.environ.get(
    'SECRETS_DIR',
    '/run/secrets' if os.path.isdir('/run/secrets')
    else os.path.join(BASE, '..', 'cluster-setup', 'container', 'secrets'),
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

# Latest position per vehicle seen in the last 10 minutes. The Kafka feed only
# publishes changed positions, so a 10-minute window keeps parked vehicles
# visible while LASTWITHTIME dedupes to the freshest row.
# _REALTIME suffix: skips the OFFLINE side of the hybrid table — its many
# batch-ingested segments add broker planning overhead, and a 10-minute window
# is always within realtime retention (1 day).
POSITIONS_SQL = """
SELECT vehicleId,
       LASTWITHTIME(lat, generatedTransformed, 'DOUBLE')             AS lat,
       LASTWITHTIME(lon, generatedTransformed, 'DOUBLE')             AS lon,
       LASTWITHTIME(routeShortName, generatedTransformed, 'STRING')  AS route,
       LASTWITHTIME(routeId, generatedTransformed, 'INT')            AS routeId,
       LASTWITHTIME(tripId, generatedTransformed, 'INT')             AS tripId,
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

# --- GTFS (stops + scheduled departures) -----------------------------------
# The ZTM GTFS feed covers the next ~14 days and is regenerated daily. We keep
# only today's and tomorrow's service days in memory: enough for a "next 60
# minutes / next 3 departures" popup, and small (~300k stop_times rows).
GTFS_URL = os.environ.get(
    'GTFS_URL',
    'https://ckan.multimediagdansk.pl/dataset/c24aa637-3619-4dc2-a171-a23eec8f2172'
    '/resource/30e783e4-2bec-4a7d-bb22-ee3e3b26ca96/download/gtfsgoogle.zip')
GTFS_REFRESH_S = int(os.environ.get('GTFS_REFRESH_S', str(6 * 3600)))
# GTFS times are local Gdansk wall clock. Windows host Pythons ship no IANA
# database — fall back to the system zone there (dev only; the Debian-based
# container image always has tzdata).
try:
    GTFS_TZ = ZoneInfo('Europe/Warsaw')
except ZoneInfoNotFoundError:
    GTFS_TZ = datetime.datetime.now().astimezone().tzinfo
    print(f'GTFS: Europe/Warsaw tzdata unavailable, using system zone {GTFS_TZ}')

_gtfs_lock = threading.Lock()
_gtfs = {'stops': [], 'departures': {}, 'loaded_at': None}


def _gtfs_rows(zf, name):
    return csv.DictReader(io.TextIOWrapper(zf.open(name), encoding='utf-8-sig'))


def _http_get(url, timeout):
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.URLError as e:
        if not isinstance(getattr(e, 'reason', None), ssl.SSLCertVerificationError):
            raise
        # TLS-intercepting proxies (dev machines) break verification; the
        # data is public, so degrade rather than lose the feature.
        print(f'HTTP: certificate verification failed for {url}, retrying unverified')
        ctx = ssl.create_default_context()  # NOSONAR — deliberate fallback documented above
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.check_hostname = False  # NOSONAR — deliberate fallback documented above
        ctx.verify_mode = ssl.CERT_NONE  # NOSONAR — deliberate fallback documented above
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return resp.read()


def load_gtfs():
    """Parse the GTFS zip into stops + per-stop sorted departure lists."""
    zf = zipfile.ZipFile(io.BytesIO(_http_get(GTFS_URL, timeout=120)))

    today = datetime.datetime.now(GTFS_TZ).date()
    days = {today.strftime('%Y%m%d'): today,
            (today + datetime.timedelta(days=1)).strftime('%Y%m%d'):
                today + datetime.timedelta(days=1)}

    service_date = {}  # service_id -> date object (today/tomorrow only)
    for r in _gtfs_rows(zf, 'calendar_dates.txt'):
        if r['exception_type'] == '1' and r['date'] in days:
            service_date[r['service_id']] = days[r['date']]

    route_name = {r['route_id']: r['route_short_name']
                  for r in _gtfs_rows(zf, 'routes.txt')}

    trips = {}  # trip_id -> (service date, route short name, headsign)
    for r in _gtfs_rows(zf, 'trips.txt'):
        d = service_date.get(r['service_id'])
        if d:
            trips[r['trip_id']] = (d, route_name.get(r['route_id'], '?'),
                                   r['trip_headsign'])

    # GTFS times are "noon minus 12 h"-relative and may exceed 24:00 for
    # after-midnight courses; anchoring at noon keeps DST days correct.
    noon = {d: datetime.datetime.combine(d, datetime.time(12), GTFS_TZ)
            for d in days.values()}
    departures = {}
    stop_routes = {}  # stop_id -> set of route short names serving it
    for r in _gtfs_rows(zf, 'stop_times.txt'):
        trip = trips.get(r['trip_id'])
        if not trip:
            continue
        d, route, headsign = trip
        stop_routes.setdefault(r['stop_id'], set()).add(route)
        if r['pickup_type'] == '1':  # 1 = no passenger pickup
            continue
        h, m, s = map(int, r['departure_time'].split(':'))
        dep = noon[d] + datetime.timedelta(seconds=(h - 12) * 3600 + m * 60 + s)
        # GTFS trip_id = <route+start datetime>_<course no>_<task>; the course
        # number is what the GPS feed publishes as tripId — the join key for
        # attaching live delays to scheduled departures.
        parts = r['trip_id'].split('_')
        trip_no = parts[1] if len(parts) == 3 else None
        departures.setdefault(r['stop_id'], []).append(
            (int(dep.timestamp() * 1000), route, headsign, trip_no))
    for lst in departures.values():
        lst.sort()

    stops = [{'stopId': r['stop_id'], 'name': r['stop_name'],
              'code': r['stop_code'],
              'lat': float(r['stop_lat']), 'lon': float(r['stop_lon']),
              'routes': sorted(stop_routes.get(r['stop_id'], ()))}
             for r in _gtfs_rows(zf, 'stops.txt')]

    with _gtfs_lock:
        _gtfs['stops'] = stops
        _gtfs['departures'] = departures
        _gtfs['loaded_at'] = time.time()
    print(f'GTFS: loaded {len(stops)} stops, '
          f'{sum(map(len, departures.values()))} departures '
          f'for {sorted(days)}')


def gtfs_refresher():
    while True:
        try:
            load_gtfs()
            time.sleep(GTFS_REFRESH_S)
        except Exception as e:  # keep last good data, retry sooner
            print(f'GTFS: refresh failed: {e}')
            time.sleep(300)


# --- Route shapes -----------------------------------------------------------
# One GeoJSON LineString per (date, routeId, tripId). Coordinates are plain
# lon/lat degrees (verified 2026-07-13; the docs' EPSG:3857 claim is wrong).
# Shapes are static within a day and a few KB each, so responses are cached in
# memory — including upstream 404s (cached as None): a trip absent from
# today's plan won't appear later.
SHAPES_URL = os.environ.get('SHAPES_URL',
                            'https://ckan2.multimediagdansk.pl/shapes')

_shapes_lock = threading.Lock()
_shapes = {'date': None, 'data': {}}  # (routeId, tripId) -> path | None


def route_shape(route_id, trip_id):
    """Path [[lon, lat], …] of today's trip, or None if upstream has none."""
    date = datetime.datetime.now(GTFS_TZ).strftime('%Y-%m-%d')
    key = (route_id, trip_id)
    with _shapes_lock:
        if _shapes['date'] != date:  # shapes are per-day; drop yesterday's
            _shapes['date'] = date
            _shapes['data'] = {}
        elif key in _shapes['data']:
            return _shapes['data'][key]
    url = SHAPES_URL + '?' + urllib.parse.urlencode(
        {'date': date, 'routeId': route_id, 'tripId': trip_id})
    try:
        path = json.loads(_http_get(url, timeout=15))['coordinates']
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        path = None  # vehicle between trips / trip not in today's plan
    with _shapes_lock:
        if _shapes['date'] == date:
            _shapes['data'][key] = path
    return path


# --- Live delays ------------------------------------------------------------
# Current delay per (route, course number) from the GPS feed, used to shift
# scheduled departures: a delayed vehicle stays listed until its *estimated*
# time passes. Cached briefly so stop clicks don't hammer the broker.
DELAYS_SQL = """
SELECT routeShortName, tripId,
       LASTWITHTIME(delay, generatedTransformed, 'INT') AS delay
FROM gdansk_public_transport_REALTIME
WHERE generatedTransformed > ago('PT10M')
GROUP BY routeShortName, tripId
LIMIT 2000
"""
DELAYS_TTL_S = 20

_delays_lock = threading.Lock()
_delays = {'at': 0.0, 'data': {}}


def live_delays():
    """(route short name, course no as str) -> current delay in seconds."""
    with _delays_lock:
        if time.time() - _delays['at'] < DELAYS_TTL_S:
            return _delays['data']
    try:
        req = urllib.request.Request(
            PINOT_BROKER_URL + '/query/sql',
            data=json.dumps({'sql': DELAYS_SQL}).encode('utf-8'),
            headers={'Content-Type': APPLICATION_JSON},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.load(resp)
        data = {(str(route), str(trip)): delay
                for route, trip, delay in result['resultTable']['rows']}
    except Exception as e:  # degrade to schedule-only rather than fail
        print(f'delays: fetch failed: {e}')
        with _delays_lock:
            return _delays['data']
    with _delays_lock:
        _delays['at'] = time.time()
        _delays['data'] = data
        return data


# --- Table stats --------------------------------------------------------------
# Headline numbers for the header strip: total GPS points (broker COUNT(*)
# over the hybrid table, realtime + offline), segment count and reported size
# (controller APIs). Cached — every visitor loads them and they move slowly.
STATS_SQL = 'SELECT COUNT(*) FROM gdansk_public_transport'
STATS_TTL_S = 60

_stats_lock = threading.Lock()
_stats = {'at': 0.0, 'data': None}


def table_stats():
    with _stats_lock:
        if _stats['data'] is not None and time.time() - _stats['at'] < STATS_TTL_S:
            return _stats['data']
    req = urllib.request.Request(
        PINOT_BROKER_URL + '/query/sql',
        data=json.dumps({'sql': STATS_SQL}).encode('utf-8'),
        headers={'Content-Type': APPLICATION_JSON},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        docs = json.load(resp)['resultTable']['rows'][0][0]
    with urllib.request.urlopen(
            PINOT_CONTROLLER_URL + '/tables/gdansk_public_transport/size?detailed=false',
            timeout=15) as resp:
        size_bytes = json.load(resp)['reportedSizeInBytes']
    with urllib.request.urlopen(
            PINOT_CONTROLLER_URL + '/segments/gdansk_public_transport',
            timeout=15) as resp:
        # [{"OFFLINE": [names…]}, {"REALTIME": [names…]}]
        segments = sum(len(names) for entry in json.load(resp)
                       for names in entry.values())
    data = {'totalDocs': docs, 'segments': segments, 'sizeBytes': size_bytes}
    with _stats_lock:
        _stats['at'] = time.time()
        _stats['data'] = data
    return data


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
            '/api/stops': self._serve_stops,
            '/api/departures': self._serve_departures,
            '/api/route-shape': self._serve_route_shape,
            '/api/stats': self._serve_stats,
        }
        parsed = urllib.parse.urlparse(self.path)
        self.query = urllib.parse.parse_qs(parsed.query)
        route = routes.get(parsed.path)
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

    def _serve_stats(self):
        self._send_json(200, table_stats())

    def _serve_route_shape(self):
        route_id = self.query.get('routeId', [''])[0]
        trip_id = self.query.get('tripId', [''])[0]
        if not route_id or not trip_id:
            self._send_json(
                400, {'error': 'routeId and tripId query parameters required'})
            return
        path = route_shape(route_id, trip_id)
        if path is None:
            self._send_json(404, {'error': 'no shape for this trip today'})
            return
        self._send_json(200, {'path': path})

    def _serve_stops(self):
        with _gtfs_lock:
            stops, loaded = _gtfs['stops'], _gtfs['loaded_at']
        if loaded is None:
            self._send_json(503, {'error': 'GTFS not loaded yet, retry shortly'})
            return
        self._send_json(200, stops)

    def _serve_departures(self):
        stop_id = self.query.get('stopId', [''])[0]
        if not stop_id:
            self._send_json(400, {'error': 'stopId query parameter required'})
            return
        with _gtfs_lock:
            deps, loaded = _gtfs['departures'].get(stop_id), _gtfs['loaded_at']
        if loaded is None:
            self._send_json(503, {'error': 'GTFS not loaded yet, retry shortly'})
            return
        now = int(time.time() * 1000)
        delays = live_delays()
        upcoming = []
        for t, route, headsign, trip_no in (deps or []):
            # Only trips around "now" can match a live vehicle — tomorrow's
            # course reuses the same number and must stay schedule-only.
            delay_s = (delays.get((route, trip_no))
                       if trip_no and abs(t - now) < 3 * 3_600_000 else None)
            est = t + (delay_s or 0) * 1000
            if est < now:  # departed (per estimate, when live; else schedule)
                continue
            upcoming.append({
                'time': t, 'estimated': est, 'route': route,
                'headsign': headsign,
                'delayMin': None if delay_s is None else round(delay_s / 60),
            })
        upcoming.sort(key=lambda d: d['estimated'])  # delays can reorder
        within_hour = [d for d in upcoming if d['estimated'] <= now + 3_600_000]
        mode = 'hour' if within_hour else 'next'
        self._send_json(200, {
            'stopId': stop_id,
            'mode': mode,  # 'hour' = next 60 min; 'next' = next 3 fallback
            'departures': within_hour[:30] if within_hour else upcoming[:3],
        })

    def log_message(self, fmt, *args):
        pass  # silence per-request noise


if __name__ == '__main__':
    threading.Thread(target=gtfs_refresher, daemon=True).start()
    print(f'Serving on http://localhost:{PORT} (Pinot broker: {PINOT_BROKER_URL})')
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
