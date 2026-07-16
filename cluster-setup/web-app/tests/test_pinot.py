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


def _pinot_result(cols, rows):
    return json.dumps({
        'resultTable': {'dataSchema': {'columnNames': cols}, 'rows': rows},
    }).encode('utf-8')


class QueryShapingTests(unittest.TestCase):

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def test_query_zips_columns_and_rows_into_dicts(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['a', 'b'], [[1, 'x'], [2, 'y']]))
        rows = pinot._query('SELECT a, b FROM t')
        self.assertEqual(rows, [{'a': 1, 'b': 'x'}, {'a': 2, 'b': 'y'}])

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
        data = pinot.live_delays()
        self.assertEqual(data, {('8', '12'): 45})

    def test_live_delays_degrades_to_stale_cache_on_failure(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['routeShortName', 'tripId', 'delay'], [['8', 12, 45]]))
        first = pinot.live_delays()
        self.assertEqual(first, {('8', '12'): 45})

        # force a fresh fetch attempt (bypass TTL) that fails outright
        pinot._delays_cache._at = 0
        def boom(req, timeout=None):
            raise OSError('network down')
        urllib.request.urlopen = boom
        second = pinot.live_delays()
        self.assertEqual(second, {('8', '12'): 45})  # stale data, not a crash


if __name__ == '__main__':
    unittest.main()
