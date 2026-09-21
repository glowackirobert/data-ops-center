"""Vehicle map server: serves static/ and proxies Pinot queries so the
browser never talks to Pinot directly. Stdlib only.

    python web-app/server.py
    # then open http://localhost:3001
"""
import json
import mimetypes
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import agent, gtfs, pinot, superset, geo
from app.config import BASE, PORT, PINOT_BROKER_URL, MAPBOX_KEY_FILE, read_secret
from app.http import send, send_json, send_error_response

STATIC_DIR = os.path.join(BASE, 'static')
GTFS_NOT_LOADED = {'error': 'GTFS not loaded yet, retry shortly'}
NOT_FOUND = 'not found'
TEXT_PLAIN = 'text/plain'


class Handler(BaseHTTPRequestHandler):

    # Shared by do_GET and do_POST: a Pinot failure gets the same response
    # whichever route raised it.
    def _run_route(self, path, fn):
        try:
            fn()
        except pinot.PinotQueryError as e:
            # Pinot answered and rejected the query (bad SQL, server-side timeout).
            send_json(self, 502, {'error': e.exceptions})
        except pinot.PinotUnavailableError as e:
            # Pinot never answered — transient, so one log line, not a traceback.
            print(f'WARN: {path}: {e}')
            send_json(self, 504, {'error': f'Pinot unavailable: {e}'})
        except Exception:  # keep the server alive on any request error
            send_error_response(self, path)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        self.query = urllib.parse.parse_qs(parsed.query)
        route = self.ROUTES.get(parsed.path)
        if route:
            self._run_route(parsed.path, lambda: route(self))
        elif parsed.path.startswith('/static/'):
            self._run_route(parsed.path, lambda: self._serve_static(parsed.path))
        else:
            send(self, 404, NOT_FOUND, TEXT_PLAIN)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        route = self.POST_ROUTES.get(parsed.path)
        if route:
            self._run_route(parsed.path, lambda: route(self))
        else:
            send(self, 404, NOT_FOUND, TEXT_PLAIN)

    def _serve_index(self):
        with open(os.path.join(STATIC_DIR, 'index.html'), 'rb') as f:
            send(self, 200, f.read(), 'text/html; charset=utf-8')

    # Static files revalidate by ETag (mtime + size): the browser re-sends
    # If-None-Match and gets a 304 instead of re-downloading every bundle
    # on each page load, while still seeing new code right after a deploy.
    def _serve_static(self, path):
        rel = urllib.parse.unquote(path[len('/static/'):])
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not (full == STATIC_DIR or full.startswith(STATIC_DIR + os.sep)) \
                or not os.path.isfile(full):
            send(self, 404, NOT_FOUND, TEXT_PLAIN)
            return
        st = os.stat(full)
        etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        if self.headers.get('If-None-Match') == etag:
            self.send_response(304)
            self.send_header('ETag', etag)
            self.send_header('Cache-Control', 'no-cache')
            self.end_headers()
            return
        content_type = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        with open(full, 'rb') as f:
            send(self, 200, f.read(), content_type, cache_control='no-cache', etag=etag)

    def _serve_config(self):
        send_json(self, 200, {'mapboxToken': read_secret(MAPBOX_KEY_FILE)})

    def _serve_guest_token(self):
        send_json(self, 200, superset.get_guest_token())

    def _serve_positions(self):
        rows, ms, stats = pinot.get_positions()
        trip_ends = gtfs.get_trip_ends()
        for row in rows:
            row['atTerminus'] = gtfs.at_terminus(row, trip_ends)
            row['inService'] = row['tripId'] != pinot.NULL_INT
        send_json(self, 200, rows, time_used_ms=ms, stats=stats)

    def _serve_stats(self):
        data, ms, stats = pinot.table_stats()
        send_json(self, 200, data, time_used_ms=ms, stats=stats)

    def _serve_network_hourly(self):
        rows, ms, stats = pinot.network_hourly()
        send_json(self, 200, rows, time_used_ms=ms, stats=stats)

    def _serve_heatmap(self):
        rows, ms, stats = pinot.heatmap_cells()
        send_json(self, 200, rows, time_used_ms=ms, stats=stats)

    def _serve_route_delay_histogram(self):
        route = self.query.get('route', [''])[0]
        if not route:
            send_json(self, 400, {'error': 'route query parameter required'})
            return
        try:
            rows, ms, stats = pinot.route_hourly_delay(route)
        except ValueError:  # route failed the pattern check in pinot.py
            send_json(self, 400, {'error': 'invalid route'})
            return
        send_json(self, 200, rows, time_used_ms=ms, stats=stats)

    def _serve_route_shape(self):
        route_id = self.query.get('routeId', [''])[0]
        trip_id = self.query.get('tripId', [''])[0]
        if not route_id or not trip_id:
            send_json(self, 400, {'error': 'routeId and tripId query parameters required'})
            return
        path = geo.route_shape(route_id, trip_id)
        if path is None:
            send_json(self, 404, {'error': 'no shape for this trip today'})
            return
        last_stop = gtfs.get_trip_last_stop(route_id, trip_id)
        if last_stop and len(path) >= 2:
            path = geo.truncate_path_at(path, last_stop)
        send_json(self, 200, {'path': path})

    # One per-IP token bucket (app/agent.py) shared by every AI-backed POST
    # route: one API-spend budget, not one per endpoint.
    def _rate_limited(self):
        if agent.RATE_LIMITER.allow(self.client_address[0]):
            return False
        send_json(self, 429, {'error': 'rate limit exceeded, try again shortly'})
        return True

    # Returns None (having already sent the 400) on a body that isn't JSON.
    def _read_json_body(self):
        length = int(self.headers.get('Content-Length', 0))
        try:
            return json.loads(self.rfile.read(length)) if length else {}
        except json.JSONDecodeError:
            send_json(self, 400, {'error': 'invalid JSON body'})
            return None

    def _serve_ask(self):
        if self._rate_limited():
            return
        body = self._read_json_body()
        if body is None:
            return
        question = (body.get('question') or '').strip()
        if not question:
            send_json(self, 400, {'error': 'question is required'})
            return
        send_json(self, 200, agent.ask(question))

    def _serve_map_filter(self):
        if self._rate_limited():
            return
        body = self._read_json_body()
        if body is None:
            return
        text = (body.get('text') or '').strip()
        if not text:
            send_json(self, 400, {'error': 'text is required'})
            return
        send_json(self, 200, agent.parse_map_filter(text))

    # The feed is downloaded by a background thread on startup; every
    # schedule-backed route answers 503 until it is parsed.
    def _require_gtfs(self):
        if gtfs.is_loaded():
            return True
        send_json(self, 503, GTFS_NOT_LOADED)
        return False

    def _serve_stops(self):
        if not self._require_gtfs():
            return
        send_json(self, 200, gtfs.get_stops())

    def _serve_routes(self):
        if not self._require_gtfs():
            return
        send_json(self, 200, gtfs.get_routes())

    def _serve_departures(self):
        stop_id = self.query.get('stopId', [''])[0]
        # The map's selected line; compared against GTFS names in memory,
        # never interpolated into SQL, so it needs no pattern check.
        route_filter = self.query.get('route', [''])[0]
        if not stop_id:
            send_json(self, 400, {'error': 'stopId query parameter required'})
            return
        if not self._require_gtfs():
            return
        now = int(time.time() * 1000)
        delays, ms, stats = pinot.live_delays()
        upcoming = gtfs.upcoming_departures(
            gtfs.get_departures(stop_id), delays, now)
        within_hour = [d for d in upcoming if d['estimated'] <= now + 3_600_000]
        mode = 'hour' if within_hour else 'next'
        shown = within_hour[:30] if within_hour else upcoming[:3]
        payload = {
            'stopId': stop_id,
            'mode': mode,  # 'hour' = next 60 min; 'next' = next 3 fallback
            'departures': shown,
        }
        # A line served only at rare hours (tram 2 at "Wczasy 01", four dawn
        # departures) can be absent from the whole 60-minute window: say when
        # it next departs. `routeNext: null` = nothing left in the loaded schedule.
        if route_filter and not any(d['route'] == route_filter for d in shown):
            payload['routeNext'] = next(
                (d for d in upcoming if d['route'] == route_filter), None)
        send_json(self, 200, payload, time_used_ms=ms, stats=stats)

    def log_message(self, fmt, *args):
        pass  # silence per-request noise

    # At the foot of the class body: a class body only sees names bound above
    # it, so the handlers have to exist first.
    ROUTES = {
        '/': _serve_index,
        '/index.html': _serve_index,
        '/api/config': _serve_config,
        '/api/guest-token': _serve_guest_token,
        '/api/positions': _serve_positions,
        '/api/stops': _serve_stops,
        '/api/routes': _serve_routes,
        '/api/departures': _serve_departures,
        '/api/route-shape': _serve_route_shape,
        '/api/stats': _serve_stats,
        '/api/network-hourly': _serve_network_hourly,
        '/api/heatmap': _serve_heatmap,
        '/api/route-delay-histogram': _serve_route_delay_histogram,
    }
    POST_ROUTES = {
        '/api/ask': _serve_ask,
        '/api/map-filter': _serve_map_filter,
    }


if __name__ == '__main__':
    threading.Thread(target=gtfs.gtfs_refresher, daemon=True).start()
    print(f'Serving on http://localhost:{PORT} (Pinot broker: {PINOT_BROKER_URL})')
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
