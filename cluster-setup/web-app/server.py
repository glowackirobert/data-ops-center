"""Vehicle map dev server.

Serves static/index.html and proxies position queries to the Pinot broker so
the browser never talks to Pinot directly (avoids CORS). Stdlib only.

    python web-app/server.py
    # then open http://localhost:3001
"""
import mimetypes
import os
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from app import gtfs, pinot, superset, geo
from app.config import BASE, PORT, PINOT_BROKER_URL, MAPBOX_KEY_FILE, read_secret
from app.http import send, send_json, send_error_response

STATIC_DIR = os.path.join(BASE, 'static')


class Handler(BaseHTTPRequestHandler):

    def _send(self, code, body, content_type):
        send(self, code, body, content_type)

    def _send_json(self, code, payload, time_used_ms=None):
        send_json(self, code, payload, time_used_ms=time_used_ms)

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
            '/api/heatmap': self._serve_heatmap,
            '/api/route-delay-histogram': self._serve_route_delay_histogram,
        }
        parsed = urllib.parse.urlparse(self.path)
        self.query = urllib.parse.parse_qs(parsed.query)
        route = routes.get(parsed.path)
        try:
            if route:
                route()
            elif parsed.path.startswith('/static/'):
                self._serve_static(parsed.path)
            else:
                self._send(404, 'not found', 'text/plain')
        except Exception:  # keep the dev server alive on any request error
            send_error_response(self, parsed.path)

    def _serve_index(self):
        with open(os.path.join(STATIC_DIR, 'index.html'), 'rb') as f:
            self._send(200, f.read(), 'text/html; charset=utf-8')

    def _serve_static(self, path):
        # path is always under /static/ (checked by the caller); strip that
        # prefix and resolve against STATIC_DIR, rejecting any attempt to
        # escape it via "..".
        rel = urllib.parse.unquote(path[len('/static/'):])
        full = os.path.normpath(os.path.join(STATIC_DIR, rel))
        if not (full == STATIC_DIR or full.startswith(STATIC_DIR + os.sep)) \
                or not os.path.isfile(full):
            self._send(404, 'not found', 'text/plain')
            return
        content_type = mimetypes.guess_type(full)[0] or 'application/octet-stream'
        with open(full, 'rb') as f:
            self._send(200, f.read(), content_type)

    def _serve_config(self):
        self._send_json(200, {'mapboxToken': read_secret(MAPBOX_KEY_FILE)})

    def _serve_guest_token(self):
        self._send_json(200, superset.get_guest_token())

    def _serve_positions(self):
        try:
            rows, ms = pinot.get_positions()
        except pinot.PinotQueryError as e:
            self._send_json(502, {'error': e.exceptions})
            return
        trip_ends = gtfs.get_trip_ends()
        for row in rows:
            row['atTerminus'] = gtfs.at_terminus(row, trip_ends)
        self._send_json(200, rows, time_used_ms=ms)

    def _serve_stats(self):
        data, ms = pinot.table_stats()
        self._send_json(200, data, time_used_ms=ms)

    def _serve_heatmap(self):
        rows, ms = pinot.heatmap_cells()
        self._send_json(200, rows, time_used_ms=ms)

    def _serve_route_delay_histogram(self):
        route = self.query.get('route', [''])[0]
        if not route:
            self._send_json(400, {'error': 'route query parameter required'})
            return
        try:
            rows, ms = pinot.route_hourly_delay(route)
        except ValueError:
            self._send_json(400, {'error': 'invalid route'})
            return
        except pinot.PinotQueryError as e:
            self._send_json(502, {'error': e.exceptions})
            return
        self._send_json(200, rows, time_used_ms=ms)

    def _serve_route_shape(self):
        route_id = self.query.get('routeId', [''])[0]
        trip_id = self.query.get('tripId', [''])[0]
        if not route_id or not trip_id:
            self._send_json(
                400, {'error': 'routeId and tripId query parameters required'})
            return
        path = geo.route_shape(route_id, trip_id)
        if path is None:
            self._send_json(404, {'error': 'no shape for this trip today'})
            return
        last_stop = gtfs.get_trip_last_stop(route_id, trip_id)
        if last_stop and len(path) >= 2:
            path = geo.truncate_path_at(path, last_stop)
        self._send_json(200, {'path': path})

    def _serve_stops(self):
        if not gtfs.is_loaded():
            self._send_json(503, {'error': 'GTFS not loaded yet, retry shortly'})
            return
        self._send_json(200, gtfs.get_stops())

    def _serve_departures(self):
        stop_id = self.query.get('stopId', [''])[0]
        if not stop_id:
            self._send_json(400, {'error': 'stopId query parameter required'})
            return
        if not gtfs.is_loaded():
            self._send_json(503, {'error': 'GTFS not loaded yet, retry shortly'})
            return
        deps = gtfs.get_departures(stop_id)
        now = int(time.time() * 1000)
        delays, ms = pinot.live_delays()
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
        }, time_used_ms=ms)

    def log_message(self, fmt, *args):
        pass  # silence per-request noise


if __name__ == '__main__':
    threading.Thread(target=gtfs.gtfs_refresher, daemon=True).start()
    print(f'Serving on http://localhost:{PORT} (Pinot broker: {PINOT_BROKER_URL})')
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
