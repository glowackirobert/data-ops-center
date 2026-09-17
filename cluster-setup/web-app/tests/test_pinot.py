"""Tests for app.pinot: SQL response shaping.

The broker/controller are never actually contacted — urllib.request.urlopen
is monkeypatched to return canned responses, so these test the response
parsing logic in isolation.
"""
import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.pinot as pinot


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def _pinot_result(cols, rows, time_used_ms=0, **stats_fields):
    return json.dumps({
        'resultTable': {'dataSchema': {'columnNames': cols}, 'rows': rows},
        'timeUsedMs': time_used_ms,
        **stats_fields,
    }).encode('utf-8')


class QueryShapingTests(unittest.TestCase):

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def test_query_zips_columns_and_rows_into_dicts(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['a', 'b'], [[1, 'x'], [2, 'y']], time_used_ms=42))
        rows, ms, _ = pinot._query('SELECT a, b FROM t')
        self.assertEqual(rows, [{'a': 1, 'b': 'x'}, {'a': 2, 'b': 'y'}])
        self.assertEqual(ms, 42)

    def test_query_returns_scan_stats_from_response(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['a'], [[1]], numDocsScanned=5279, totalDocs=60417459,
                          numSegmentsProcessed=32, numSegmentsQueried=33,
                          numSegmentsPrunedByValue=1, numServersQueried=2))
        _, _, stats = pinot._query('SELECT a FROM t')
        self.assertEqual(stats, {
            'docsScanned': 5279, 'totalDocs': 60417459,
            'segmentsProcessed': 32, 'segmentsQueried': 33,
            'segmentsPruned': 1, 'serversQueried': 2,
        })

    def test_query_defaults_stats_fields_to_zero_when_absent(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['a'], [[1]]))
        _, _, stats = pinot._query('SELECT a FROM t')
        self.assertEqual(stats, {
            'docsScanned': 0, 'totalDocs': 0,
            'segmentsProcessed': 0, 'segmentsQueried': 0,
            'segmentsPruned': 0, 'serversQueried': 0,
        })

    def test_query_raises_on_exceptions_in_response(self):
        body = json.dumps({'exceptions': [{'errorCode': 200, 'message': 'boom'}]})
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            body.encode('utf-8'))
        with self.assertRaises(pinot.PinotQueryError):
            pinot._query('SELECT 1')

    def test_query_sends_pinot_side_timeout_below_the_socket_timeout(self):
        """Pinot must give up first, so an overloaded broker comes back as a
        structured error instead of a bare socket TimeoutError."""
        seen = {}

        def fake_urlopen(req, timeout=None):
            seen['options'] = json.loads(req.data)['queryOptions']
            seen['socket_timeout'] = timeout
            return _FakeResponse(_pinot_result(['a'], [[1]]))

        urllib.request.urlopen = fake_urlopen
        pinot._query('SELECT 1', timeout=10)
        self.assertEqual(seen['options'], 'timeoutMs=10000')
        self.assertGreater(seen['socket_timeout'], 10)

    def test_query_raises_unavailable_on_socket_timeout(self):
        def fake_urlopen(req, timeout=None):
            raise TimeoutError('timed out')

        urllib.request.urlopen = fake_urlopen
        with self.assertRaises(pinot.PinotUnavailableError):
            pinot._query('SELECT 1')

    def test_query_raises_unavailable_when_broker_unreachable(self):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.URLError('connection refused')

        urllib.request.urlopen = fake_urlopen
        with self.assertRaises(pinot.PinotUnavailableError):
            pinot._query('SELECT 1')


class LiveDelaysTests(unittest.TestCase):

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def test_live_delays_keys_by_route_and_trip_as_strings(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['routeShortName', 'tripId', 'delay'],
                          [['8', 12, 45]]))
        data, _, _ = pinot.live_delays()
        self.assertEqual(data, {('8', '12'): 45})

    def test_live_delays_degrades_to_empty_on_failure(self):
        def boom(req, timeout=None):
            raise OSError('network down')
        urllib.request.urlopen = boom
        data, ms, stats = pinot.live_delays()
        self.assertEqual(data, {})  # schedule-only fallback, not a crash
        self.assertEqual(ms, 0)
        self.assertIsNone(stats)


class RouteHourlyDelayTests(unittest.TestCase):

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def test_route_hourly_delay_labels_int_hour_buckets(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['hourOfDay', 'avgDelaySec', 'snapshots'],
                          [[8, 42, 100]], time_used_ms=7))
        rows, ms, _ = pinot.route_hourly_delay('8')
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

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def test_network_hourly_labels_int_hour_buckets(self):
        urllib.request.urlopen = lambda req, timeout=None: _FakeResponse(
            _pinot_result(['hourOfDay', 'activeVehicles'],
                          [[7, 350], [8, 412]], time_used_ms=19))
        rows, ms, _ = pinot.network_hourly()
        self.assertEqual(rows, [{'hour': '07:00', 'activeVehicles': 350},
                                {'hour': '08:00', 'activeVehicles': 412}])
        self.assertEqual(ms, 19)

    def test_network_hourly_queries_broker_every_call(self):
        calls = []
        def counting(req, timeout=None):
            calls.append(req)
            return _FakeResponse(
                _pinot_result(['hourOfDay', 'activeVehicles'], [[7, 350]]))
        urllib.request.urlopen = counting
        pinot.network_hourly()
        pinot.network_hourly()
        self.assertEqual(len(calls), 2)  # uncached: every call is live


def _url_of(target):
    """urlopen's arg is a plain URL string for a GET, or a Request object
    for _query's POST — the new controller-calling functions only ever pass
    plain strings, but normalize anyway so a routing fake works either way."""
    return target if isinstance(target, str) else target.full_url


class GetSchemaTests(unittest.TestCase):

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def _route(self, responses):
        def fake_urlopen(target, timeout=None):
            url = _url_of(target)
            for suffix, body in responses.items():
                if url.endswith(suffix):
                    return _FakeResponse(json.dumps(body).encode('utf-8'))
            raise AssertionError(f'unexpected URL: {url}')
        urllib.request.urlopen = fake_urlopen

    def test_rejects_a_table_outside_known_tables(self):
        with self.assertRaises(ValueError):
            pinot.get_schema('some_other_table')

    def test_collects_columns_from_all_three_field_spec_kinds(self):
        self._route({
            '/schemas/gdansk_public_transport': {
                'schemaName': 'gdansk_public_transport',
                'dimensionFieldSpecs': [{'name': 'vehicleId', 'dataType': 'INT'}],
                'metricFieldSpecs': [{'name': 'lat', 'dataType': 'DOUBLE'}],
                'dateTimeFieldSpecs': [{'name': 'generatedTransformed', 'dataType': 'TIMESTAMP'}],
            },
            '/tables/gdansk_public_transport': {},
        })
        result = pinot.get_schema('gdansk_public_transport')
        self.assertEqual(result['columns'], [
            {'name': 'vehicleId', 'dataType': 'INT'},
            {'name': 'lat', 'dataType': 'DOUBLE'},
            {'name': 'generatedTransformed', 'dataType': 'TIMESTAMP'},
        ])

    def test_pulls_sorted_column_and_star_tree_out_of_the_table_config(self):
        self._route({
            '/schemas/gdansk_public_transport': {},
            '/tables/gdansk_public_transport': {
                'REALTIME': {
                    'tableIndexConfig': {
                        'sortedColumn': ['routeShortName'],
                        'invertedIndexColumns': ['speed'],
                        'rangeIndexColumns': ['delay'],
                        'starTreeIndexConfigs': [{
                            'dimensionsSplitOrder': ['dayBucket', 'routeShortName'],
                            'functionColumnPairs': ['SUM__isOnTime', 'COUNT__*'],
                        }],
                    },
                },
            },
        })
        result = pinot.get_schema('gdansk_public_transport')
        realtime = result['tableTypes']['REALTIME']
        self.assertEqual(realtime['sortedColumn'], ['routeShortName'])
        self.assertEqual(realtime['rangeIndexColumns'], ['delay'])
        self.assertEqual(realtime['starTree'], [{
            'dimensionsSplitOrder': ['dayBucket', 'routeShortName'],
            'functionColumnPairs': ['SUM__isOnTime', 'COUNT__*'],
        }])

    def test_tolerates_a_table_config_with_no_star_tree(self):
        self._route({
            '/schemas/gdansk_public_transport_latest': {},
            '/tables/gdansk_public_transport_latest': {
                'REALTIME': {'tableIndexConfig': {'sortedColumn': ['vehicleId']}},
            },
        })
        result = pinot.get_schema('gdansk_public_transport_latest')
        self.assertEqual(result['tableTypes']['REALTIME']['starTree'], [])


class ClusterHealthTests(unittest.TestCase):

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def _route(self, health_ok=True, segments=None, external_views=None, sizes=None):
        segments = segments or {}
        external_views = external_views or {}
        sizes = sizes or {}

        def fake_urlopen(target, timeout=None):
            url = _url_of(target)
            if url.endswith('/health'):
                return _FakeResponse(b'OK' if health_ok else b'not ok')
            for table, size in sizes.items():
                if url.endswith(f'/tables/{table}/size?detailed=false'):
                    return _FakeResponse(json.dumps({'reportedSizeInBytes': size}).encode())
            for table, entries in segments.items():
                if url.endswith(f'/segments/{table}'):
                    return _FakeResponse(json.dumps(entries).encode())
            for table_with_type, view in external_views.items():
                if url.endswith(f'/tables/{table_with_type}/externalview'):
                    return _FakeResponse(json.dumps(view).encode())
            raise AssertionError(f'unexpected URL: {url}')
        urllib.request.urlopen = fake_urlopen

    def _all_tables(self, **overrides):
        base = {
            'health_ok': True,
            'sizes': {t: 1000 for t in pinot.KNOWN_TABLES},
            'segments': {t: [] for t in pinot.KNOWN_TABLES},
            'external_views': {},
        }
        base.update(overrides)
        return base

    def test_unhealthy_when_the_health_endpoint_says_so(self):
        self._route(**self._all_tables(health_ok=False))
        result = pinot.cluster_health()
        self.assertFalse(result['controllerHealthy'])

    def test_unhealthy_when_the_controller_is_unreachable(self):
        def boom(target, timeout=None):
            raise urllib.error.URLError('connection refused')
        urllib.request.urlopen = boom
        with self.assertRaises(pinot.PinotUnavailableError):
            # /health degrades gracefully, but the per-table size call right
            # after it does not — same "controller answered nothing at all"
            # signal the rest of the app already treats as a 504.
            pinot.cluster_health()

    def test_reports_segment_states_from_the_external_view(self):
        self._route(**self._all_tables(
            segments={
                'gdansk_public_transport': [{'REALTIME': ['seg1', 'seg2']}, {'OFFLINE': []}],
                'gdansk_public_transport_latest': [{'REALTIME': ['seg3']}],
            },
            external_views={
                'gdansk_public_transport_REALTIME': {
                    'seg1': {'server1': 'ONLINE'}, 'seg2': {'server1': 'CONSUMING'}},
                'gdansk_public_transport_latest_REALTIME': {
                    'seg3': {'server1': 'ONLINE'}},
            },
        ))
        result = pinot.cluster_health()
        realtime = result['tables']['gdansk_public_transport']['segmentStates']['REALTIME']
        self.assertEqual(realtime, {'ONLINE': 1, 'CONSUMING': 1})

    def test_consuming_is_true_only_when_a_segment_is_consuming(self):
        self._route(**self._all_tables(
            segments={
                'gdansk_public_transport': [{'REALTIME': ['seg1']}],
                'gdansk_public_transport_latest': [{'REALTIME': ['seg2']}],
            },
            external_views={
                'gdansk_public_transport_REALTIME': {'seg1': {'server1': 'ONLINE'}},
                'gdansk_public_transport_latest_REALTIME': {'seg2': {'server1': 'CONSUMING'}},
            },
        ))
        result = pinot.cluster_health()
        self.assertFalse(result['tables']['gdansk_public_transport']['consuming'])
        self.assertTrue(result['tables']['gdansk_public_transport_latest']['consuming'])

    def test_skips_the_external_view_call_for_a_table_type_with_no_segments(self):
        self._route(**self._all_tables(
            segments={
                'gdansk_public_transport': [{'REALTIME': []}, {'OFFLINE': ['seg1']}],
                'gdansk_public_transport_latest': [{'REALTIME': ['seg2']}],
            },
            external_views={
                'gdansk_public_transport_OFFLINE': {'seg1': {'server1': 'ONLINE'}},
                'gdansk_public_transport_latest_REALTIME': {'seg2': {'server1': 'ONLINE'}},
            },
        ))
        # No entry for gdansk_public_transport_REALTIME's externalview — if
        # the empty REALTIME list were queried anyway, _route's fallthrough
        # AssertionError would fail this test.
        result = pinot.cluster_health()
        self.assertNotIn('REALTIME', result['tables']['gdansk_public_transport']['segmentStates'])


if __name__ == '__main__':
    unittest.main()
