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
GTFS_NOT_LOADED = {'error': 'GTFS not loaded yet, retry shortly'}


class Handler(BaseHTTPRequestHandler):

    def _send(self, code, body, content_type):
        send(self, code, body, content_type)

    def _send_json(self, code, payload, time_used_ms=None):
        send_json(self, code, payload, time_used_ms=time_used_ms)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        self.query = urllib.parse.parse_qs(parsed.query)
        route = self.ROUTES.get(parsed.path)
        try:
            if route:
                route(self)
            elif parsed.path.startswith('/static/'):
                self._serve_static(parsed.path)
            else:
                self._send(404, 'not found', 'text/plain')
        except pinot.PinotQueryError as e:
            # Pinot answered and rejected the query (bad SQL, server-side
            # timeout). Handled here rather than in each Pinot-backed route:
            # the response is identical for all of them, and writing it out
            # per endpoint meant /api/stats and /api/heatmap were left out
            # and reported a query error as a generic 500.
            self._send_json(502, {'error': e.exceptions})
        except pinot.PinotUnavailableError as e:
            # Pinot never answered — cluster down, or too busy to reply (an
            # S3 backfill building a segment on the same host is enough).
            # A known, transient condition: log one line, not a traceback,
            # and say so rather than claiming an internal error.
            print(f'WARN: {parsed.path}: {e}')
            self._send_json(504, {'error': f'Pinot unavailable: {e}'})
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
        rows, ms = pinot.get_positions()
        trip_ends = gtfs.get_trip_ends()
        for row in rows:
            row['atTerminus'] = gtfs.at_terminus(row, trip_ends)
            row['inService'] = row['tripId'] != pinot.NULL_INT
        self._send_json(200, rows, time_used_ms=ms)

    def _serve_stats(self):
        data, ms = pinot.table_stats()
        self._send_json(200, data, time_used_ms=ms)

    def _serve_network_hourly(self):
        rows, ms = pinot.network_hourly()
        self._send_json(200, rows, time_used_ms=ms)

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
        except ValueError:  # route failed the pattern check in pinot.py
            self._send_json(400, {'error': 'invalid route'})
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

    def _require_gtfs(self):
        """True if the feed is parsed; otherwise answers 503 and returns False.

        The feed is downloaded by a background thread on startup, so every
        schedule-backed route needs the same guard.
        """
        if gtfs.is_loaded():
            return True
        self._send_json(503, GTFS_NOT_LOADED)
        return False

    def _serve_stops(self):
        if not self._require_gtfs():
            return
        self._send_json(200, gtfs.get_stops())

    def _serve_routes(self):
        if not self._require_gtfs():
            return
        self._send_json(200, gtfs.get_routes())

    def _serve_departures(self):
        stop_id = self.query.get('stopId', [''])[0]
        # The line selected in the map's route filter, if any. Compared
        # against GTFS route names held in memory — never interpolated into
        # SQL — so it needs no pattern validation of its own.
        route_filter = self.query.get('route', [''])[0]
        if not stop_id:
            self._send_json(400, {'error': 'stopId query parameter required'})
            return
        if not self._require_gtfs():
            return
        now = int(time.time() * 1000)
        delays, ms = pinot.live_delays()
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
        # A stop can be served by a line only at rare hours — tram 2 calls at
        # "Wczasy 01" four times a day, all between 04:08 and 05:48 — so with
        # that line picked in the route filter the pole is marked as served
        # while its 60-minute window is filled entirely by other routes.
        # Answer the question the marking raises instead of leaving it: when
        # the selected line is absent from what we're about to show, say when
        # it does next depart. `routeNext: null` distinguishes "nothing left
        # in the loaded schedule" (today+tomorrow) from "not asked".
        if route_filter and not any(d['route'] == route_filter for d in shown):
            payload['routeNext'] = next(
                (d for d in upcoming if d['route'] == route_filter), None)
        self._send_json(200, payload, time_used_ms=ms)

    def log_message(self, fmt, *args):
        pass  # silence per-request noise

    # Built once at class-definition time, not per request. Sits at the foot
    # of the class body because a class body only sees names already bound
    # above it — the handlers have to exist first. do_GET calls the plain
    # function with an explicit `self`.
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


if __name__ == '__main__':
    threading.Thread(target=gtfs.gtfs_refresher, daemon=True).start()
    print(f'Serving on http://localhost:{PORT} (Pinot broker: {PINOT_BROKER_URL})')
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
