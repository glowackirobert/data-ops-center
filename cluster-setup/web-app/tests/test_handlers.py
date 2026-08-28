"""Endpoint status-code and JSON-contract tests, run against the real
Handler/routing in server.py over an actual (ephemeral-port) HTTP server —
only app.pinot/app.gtfs/app.geo/app.superset are monkeypatched, so this
exercises real request parsing, dispatch, and error handling.

Includes the route-shape truncation integration cases flagged in the
refactor review notes: no truncation when trip_last_stop has no match, and
the len(path) >= 2 guard.
"""
import json
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import gtfs, pinot, geo
import server


class HandlerTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.thread.join(timeout=5)

    def setUp(self):
        # Save/restore every monkeypatchable entry point so tests don't leak
        # into each other.
        self._orig = {
            'get_positions': pinot.get_positions,
            'is_loaded': gtfs.is_loaded,
            'get_stops': gtfs.get_stops,
            'get_trip_ends': gtfs.get_trip_ends,
            'get_departures': gtfs.get_departures,
            'get_trip_last_stop': gtfs.get_trip_last_stop,
            'route_shape': geo.route_shape,
            'live_delays': pinot.live_delays,
            'route_hourly_delay': pinot.route_hourly_delay,
            'table_stats': pinot.table_stats,
            'network_hourly': pinot.network_hourly,
        }

    def tearDown(self):
        pinot.get_positions = self._orig['get_positions']
        gtfs.is_loaded = self._orig['is_loaded']
        gtfs.get_stops = self._orig['get_stops']
        gtfs.get_trip_ends = self._orig['get_trip_ends']
        gtfs.get_departures = self._orig['get_departures']
        gtfs.get_trip_last_stop = self._orig['get_trip_last_stop']
        geo.route_shape = self._orig['route_shape']
        pinot.live_delays = self._orig['live_delays']
        pinot.route_hourly_delay = self._orig['route_hourly_delay']
        pinot.table_stats = self._orig['table_stats']
        pinot.network_hourly = self._orig['network_hourly']

    def _get(self, path):
        url = f'http://127.0.0.1:{self.port}{path}'
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_positions_returns_rows_with_at_terminus(self):
        pinot.get_positions = lambda: ([
            {'vehicleId': 1, 'lat': 54.35, 'lon': 18.64, 'route': '8', 'tripId': 1}], 12)
        gtfs.get_trip_ends = lambda: {}
        status, body = self._get('/api/positions')
        self.assertEqual(status, 200)
        self.assertFalse(body[0]['atTerminus'])

    def test_positions_includes_latency_header(self):
        pinot.get_positions = lambda: ([], 37)
        gtfs.get_trip_ends = lambda: {}
        url = f'http://127.0.0.1:{self.port}/api/positions'
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.headers.get('X-Pinot-Time-Ms'), '37')

    def test_stats_returns_data_with_latency_header(self):
        pinot.table_stats = lambda: (
            {'totalDocs': 5, 'segments': 2, 'sizeBytes': 1024}, 15)
        url = f'http://127.0.0.1:{self.port}/api/stats'
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.headers.get('X-Pinot-Time-Ms'), '15')
            body = json.loads(resp.read())
        self.assertEqual(body, {'totalDocs': 5, 'segments': 2, 'sizeBytes': 1024})

    def test_stats_502_on_pinot_query_error(self):
        """Every Pinot-backed route reports a rejected query the same way.

        /api/stats and /api/heatmap used to lack the local except clause the
        other routes carried and answered 500 'internal error' instead; the
        handling now lives once in do_GET, so there is nothing to leave out.
        """
        def boom():
            raise pinot.PinotQueryError([{'message': 'bad query'}])
        pinot.table_stats = boom
        status, body = self._get('/api/stats')
        self.assertEqual(status, 502)
        self.assertIn('error', body)

    def test_positions_502_on_pinot_query_error(self):
        def boom():
            raise pinot.PinotQueryError([{'message': 'bad query'}])
        pinot.get_positions = boom
        status, body = self._get('/api/positions')
        self.assertEqual(status, 502)
        self.assertIn('error', body)

    def test_network_hourly_504_when_pinot_does_not_answer(self):
        """A busy/unreachable broker is a known transient condition — 504
        with a real message, not the generic 500 an unexpected error gets."""
        def boom():
            raise pinot.PinotUnavailableError('broker did not respond within 17s')
        pinot.network_hourly = boom
        status, body = self._get('/api/network-hourly')
        self.assertEqual(status, 504)
        self.assertIn('Pinot unavailable', body['error'])

    def test_stats_504_when_pinot_does_not_answer(self):
        def boom():
            raise pinot.PinotUnavailableError('controller unreachable: refused')
        pinot.table_stats = boom
        status, _ = self._get('/api/stats')
        self.assertEqual(status, 504)

    def test_stops_503_before_gtfs_loaded(self):
        gtfs.is_loaded = lambda: False
        status, _ = self._get('/api/stops')
        self.assertEqual(status, 503)

    def test_stops_200_after_gtfs_loaded(self):
        gtfs.is_loaded = lambda: True
        gtfs.get_stops = lambda: [{'stopId': 'S1'}]
        status, body = self._get('/api/stops')
        self.assertEqual(status, 200)
        self.assertEqual(body, [{'stopId': 'S1'}])

    def test_route_delay_histogram_requires_route_param(self):
        status, _ = self._get('/api/route-delay-histogram')
        self.assertEqual(status, 400)

    def test_route_delay_histogram_400_on_invalid_route(self):
        def boom(route):
            raise ValueError('invalid route')
        pinot.route_hourly_delay = boom
        status, _ = self._get('/api/route-delay-histogram?route=8')
        self.assertEqual(status, 400)

    def test_route_delay_histogram_502_on_pinot_query_error(self):
        def boom(route):
            raise pinot.PinotQueryError([{'message': 'bad query'}])
        pinot.route_hourly_delay = boom
        status, body = self._get('/api/route-delay-histogram?route=8')
        self.assertEqual(status, 502)
        self.assertIn('error', body)

    def test_route_delay_histogram_returns_rows_with_latency_header(self):
        pinot.route_hourly_delay = lambda route: (
            [{'hour': '08:00', 'avgDelaySec': 42, 'snapshots': 100}], 9)
        url = f'http://127.0.0.1:{self.port}/api/route-delay-histogram?route=8'
        with urllib.request.urlopen(url, timeout=5) as resp:
            self.assertEqual(resp.headers.get('X-Pinot-Time-Ms'), '9')
            body = json.loads(resp.read())
        self.assertEqual(body, [{'hour': '08:00', 'avgDelaySec': 42, 'snapshots': 100}])

    def test_route_shape_requires_both_query_params(self):
        status, _ = self._get('/api/route-shape?routeId=1')
        self.assertEqual(status, 400)

    def test_route_shape_404_when_no_shape_today(self):
        geo.route_shape = lambda route_id, trip_id: None
        status, _ = self._get('/api/route-shape?routeId=1&tripId=2')
        self.assertEqual(status, 404)

    def test_route_shape_untruncated_when_no_last_stop_match(self):
        # review-note case: trip_last_stop has no entry for this (route, trip)
        path = [[18.0, 54.0], [18.1, 54.1], [18.2, 54.2]]
        geo.route_shape = lambda route_id, trip_id: path
        gtfs.get_trip_last_stop = lambda route_id, trip_id: None
        status, body = self._get('/api/route-shape?routeId=1&tripId=2')
        self.assertEqual(status, 200)
        self.assertEqual(body['path'], path)

    def test_route_shape_untruncated_when_path_too_short(self):
        # review-note case: len(path) >= 2 guard
        path = [[18.0, 54.0]]
        geo.route_shape = lambda route_id, trip_id: path
        gtfs.get_trip_last_stop = lambda route_id, trip_id: (54.0, 18.0)
        status, body = self._get('/api/route-shape?routeId=1&tripId=2')
        self.assertEqual(status, 200)
        self.assertEqual(body['path'], path)

    def test_route_shape_truncated_when_last_stop_matches(self):
        path = [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]]
        geo.route_shape = lambda route_id, trip_id: path
        gtfs.get_trip_last_stop = lambda route_id, trip_id: (0.0, 1.0)
        status, body = self._get('/api/route-shape?routeId=1&tripId=2')
        self.assertEqual(status, 200)
        self.assertEqual(body['path'], [[0.0, 0.0], [1.0, 0.0]])

    def test_departures_requires_stop_id(self):
        status, _ = self._get('/api/departures')
        self.assertEqual(status, 400)

    def test_departures_503_before_gtfs_loaded(self):
        gtfs.is_loaded = lambda: False
        status, _ = self._get('/api/departures?stopId=S1')
        self.assertEqual(status, 503)

    def test_departures_delay_shifts_estimated_time_and_reports_delay_min(self):
        # Real wall clock as the reference (no global time.time() patching,
        # which would risk interfering with the live HTTP server thread) —
        # departure times are built relative to "now" instead of frozen.
        gtfs.is_loaded = lambda: True
        now_ms = int(__import__('time').time() * 1000)
        gtfs.get_departures = lambda stop_id: [(now_ms + 60_000, '8', 'Jelitkowo', '1')]
        pinot.live_delays = lambda: ({('8', '1'): 120}, 5)  # +120s delay
        status, body = self._get('/api/departures?stopId=S1')
        self.assertEqual(status, 200)
        dep = body['departures'][0]
        self.assertEqual(dep['estimated'], now_ms + 60_000 + 120_000)
        self.assertEqual(dep['delayMin'], 2)

    def test_departures_excludes_ones_whose_delayed_estimate_already_passed(self):
        gtfs.is_loaded = lambda: True
        now_ms = int(__import__('time').time() * 1000)
        # scheduled 30s in the future, but a 60s delay pushes the estimate
        # into the past relative to "now" -> should be dropped, not listed
        gtfs.get_departures = lambda stop_id: [(now_ms + 30_000, '8', 'X', '1')]
        pinot.live_delays = lambda: ({('8', '1'): -60}, 5)
        _, body = self._get('/api/departures?stopId=S1')
        self.assertEqual(body['departures'], [])

    def test_departures_mode_is_next_when_nothing_within_the_hour(self):
        gtfs.is_loaded = lambda: True
        now_ms = int(__import__('time').time() * 1000)
        far_future = now_ms + 5 * 3_600_000  # 5 h out — outside the 1 h/3 h windows
        gtfs.get_departures = lambda stop_id: [(far_future, '8', 'X', None)]
        pinot.live_delays = lambda: ({}, 0)
        _, body = self._get('/api/departures?stopId=S1')
        self.assertEqual(body['mode'], 'next')
        self.assertEqual(len(body['departures']), 1)

    def test_departures_route_filter_reports_that_lines_next_departure(self):
        # The reported case: a pole whose next 60 minutes belong to other
        # lines entirely, while the filtered line calls there at dawn.
        gtfs.is_loaded = lambda: True
        now_ms = int(__import__('time').time() * 1000)
        gtfs.get_departures = lambda stop_id: [
            (now_ms + 60_000, '5', 'Oliwa', None),
            (now_ms + 9 * 3_600_000, '2', 'Lawendowe Wzgorze', None),
        ]
        pinot.live_delays = lambda: ({}, 0)
        _, body = self._get('/api/departures?stopId=S1&route=2')
        self.assertEqual([d['route'] for d in body['departures']], ['5'])
        self.assertEqual(body['routeNext']['route'], '2')
        self.assertEqual(body['routeNext']['estimated'], now_ms + 9 * 3_600_000)

    def test_departures_route_filter_silent_when_that_line_is_already_shown(self):
        gtfs.is_loaded = lambda: True
        now_ms = int(__import__('time').time() * 1000)
        gtfs.get_departures = lambda stop_id: [(now_ms + 60_000, '2', 'X', None)]
        pinot.live_delays = lambda: ({}, 0)
        _, body = self._get('/api/departures?stopId=S1&route=2')
        self.assertNotIn('routeNext', body)

    def test_departures_route_filter_null_when_that_line_has_nothing_left(self):
        # Distinguishable from "not asked": the key is present, the value null.
        gtfs.is_loaded = lambda: True
        now_ms = int(__import__('time').time() * 1000)
        gtfs.get_departures = lambda stop_id: [(now_ms + 60_000, '5', 'X', None)]
        pinot.live_delays = lambda: ({}, 0)
        _, body = self._get('/api/departures?stopId=S1&route=2')
        self.assertIn('routeNext', body)
        self.assertIsNone(body['routeNext'])

    def test_departures_without_route_filter_omits_the_note(self):
        gtfs.is_loaded = lambda: True
        now_ms = int(__import__('time').time() * 1000)
        gtfs.get_departures = lambda stop_id: [(now_ms + 60_000, '5', 'X', None)]
        pinot.live_delays = lambda: ({}, 0)
        _, body = self._get('/api/departures?stopId=S1')
        self.assertNotIn('routeNext', body)

    def test_unknown_route_is_404(self):
        status, _ = self._get_raw('/nope')
        self.assertEqual(status, 404)

    def _get_raw(self, path):
        url = f'http://127.0.0.1:{self.port}{path}'
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    def test_unexpected_exception_returns_generic_500_not_raw_text(self):
        def boom():
            raise ValueError('/etc/some/internal/path leaked')
        pinot.get_positions = boom
        status, body = self._get('/api/positions')
        self.assertEqual(status, 500)
        self.assertNotIn('/etc/some/internal/path', json.dumps(body))
        self.assertEqual(body, {'error': 'internal error'})

    def test_static_path_traversal_is_rejected(self):
        status, _ = self._get_raw('/static/../server.py')
        self.assertEqual(status, 404)

    def test_static_serves_known_asset(self):
        status, body = self._get_raw('/static/js/utils.js')
        self.assertEqual(status, 200)
        self.assertIn(b'colorForRoute', body)


if __name__ == '__main__':
    unittest.main()
