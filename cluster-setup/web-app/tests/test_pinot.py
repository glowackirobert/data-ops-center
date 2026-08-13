"""Tests for app.pinot: SQL response shaping and TTL-cache behavior.

The broker/controller are never actually contacted — urllib.request.urlopen
is monkeypatched to return canned responses, so these test the response
parsing and caching logic in isolation.
"""
import io
import json
import os
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.pinot as pinot


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _pinot_result(cols, rows, time_used_ms=0):
    return json.dumps({
        'resultTable': {'dataSchema': {'columnNames': cols}, 'rows': rows},
        'timeUsedMs': time_used_ms,
    }).encode('utf-8')


class QueryShapingTests(unittest.TestCase):

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def test_query_zips_columns_and_rows_into_dicts(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['a', 'b'], [[1, 'x'], [2, 'y']], time_used_ms=42))
        rows, ms = pinot._query('SELECT a, b FROM t')
        self.assertEqual(rows, [{'a': 1, 'b': 'x'}, {'a': 2, 'b': 'y'}])
        self.assertEqual(ms, 42)

    def test_query_raises_on_exceptions_in_response(self):
        body = json.dumps({'exceptions': [{'errorCode': 200, 'message': 'boom'}]})
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            body.encode('utf-8'))
        with self.assertRaises(pinot.PinotQueryError):
            pinot._query('SELECT 1')


class LiveDelaysCacheTests(unittest.TestCase):

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen
        pinot._delays_cache = pinot.TTLCache(pinot.DELAYS_TTL_S)

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def test_live_delays_keys_by_route_and_trip_as_strings(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['routeShortName', 'tripId', 'delay'],
                          [['8', 12, 45]]))
        data, _ = pinot.live_delays()
        self.assertEqual(data, {('8', '12'): 45})

    def test_live_delays_degrades_to_stale_cache_on_failure(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['routeShortName', 'tripId', 'delay'], [['8', 12, 45]]))
        first, _ = pinot.live_delays()
        self.assertEqual(first, {('8', '12'): 45})

        # force a fresh fetch attempt (bypass TTL) that fails outright
        pinot._delays_cache._at = 0
        def boom(req, timeout=None):
            raise OSError('network down')
        urllib.request.urlopen = boom
        second, _ = pinot.live_delays()
        self.assertEqual(second, {('8', '12'): 45})  # stale data, not a crash


class RouteHourlyDelayTests(unittest.TestCase):

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def test_route_hourly_delay_returns_rows_and_time(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['hour', 'avgDelaySec', 'snapshots'],
                          [['08:00', 42, 100]], time_used_ms=7))
        rows, ms = pinot.route_hourly_delay('8')
        self.assertEqual(rows, [{'hour': '08:00', 'avgDelaySec': 42, 'snapshots': 100}])
        self.assertEqual(ms, 7)

    def test_route_hourly_delay_rejects_invalid_route_without_querying(self):
        def boom(req, timeout=None):
            raise AssertionError('should not query Pinot for an invalid route')
        urllib.request.urlopen = boom
        with self.assertRaises(ValueError):
            pinot.route_hourly_delay("8' OR '1'='1")


class NetworkHourlyTests(unittest.TestCase):

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen
        pinot._network_hourly_cache = pinot.TTLCache(pinot.NETWORK_HOURLY_TTL_S)

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def test_network_hourly_returns_rows_and_time(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['hour', 'activeVehicles'],
                          [['07:00', 350], ['08:00', 412]], time_used_ms=19))
        rows, ms = pinot.network_hourly()
        self.assertEqual(rows, [{'hour': '07:00', 'activeVehicles': 350},
                                {'hour': '08:00', 'activeVehicles': 412}])
        self.assertEqual(ms, 19)

    def test_network_hourly_is_cached(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['hour', 'activeVehicles'], [['07:00', 350]]))
        first, _ = pinot.network_hourly()

        def boom(req, timeout=None):
            raise AssertionError('cached call must not hit the broker')
        urllib.request.urlopen = boom
        second, _ = pinot.network_hourly()
        self.assertEqual(second, first)


if __name__ == '__main__':
    unittest.main()
