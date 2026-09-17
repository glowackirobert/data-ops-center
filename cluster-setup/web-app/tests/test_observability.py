"""Tests for app.observability: Loki/Prometheus query construction and
response shaping. Loki and Prometheus are never actually contacted —
urllib.request.urlopen is monkeypatched, same convention as test_pinot.py.
"""
import io
import json
import os
import sys
import unittest
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.observability as observability


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


class DurationParsingTests(unittest.TestCase):

    def test_parses_each_supported_unit(self):
        self.assertEqual(observability._duration_seconds('30s'), 30)
        self.assertEqual(observability._duration_seconds('30m'), 1800)
        self.assertEqual(observability._duration_seconds('1h'), 3600)
        self.assertEqual(observability._duration_seconds('2d'), 172800)

    def test_rejects_an_unsupported_shape(self):
        for bad in ['1w', 'abc', '1', '-1h', '1.5h']:
            with self.assertRaises(ValueError):
                observability._duration_seconds(bad)


class GetLogsTests(unittest.TestCase):

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def test_builds_a_container_scoped_logql_query(self):
        seen = {}

        def fake_urlopen(url, timeout=None):
            seen['url'] = url
            return _FakeResponse(json.dumps({'data': {'result': []}}).encode())

        urllib.request.urlopen = fake_urlopen
        observability.get_logs('pinot-broker')
        query = urllib.parse.parse_qs(urllib.parse.urlparse(seen['url']).query)['query'][0]
        self.assertEqual(query, '{container="pinot-broker"}')

    def test_appends_an_escaped_line_match_pattern(self):
        seen = {}

        def fake_urlopen(url, timeout=None):
            seen['url'] = url
            return _FakeResponse(json.dumps({'data': {'result': []}}).encode())

        urllib.request.urlopen = fake_urlopen
        observability.get_logs('web-app', pattern='say "hi"')
        query = urllib.parse.parse_qs(urllib.parse.urlparse(seen['url']).query)['query'][0]
        self.assertEqual(query, '{container="web-app"} |= "say \\"hi\\""')

    def test_flattens_streams_into_sorted_lines(self):
        urllib.request.urlopen = lambda url, timeout=None: _FakeResponse(json.dumps({
            'data': {'result': [
                {'stream': {'container': 'kafka'}, 'values': [['200', 'second'], ['100', 'first']]},
            ]},
        }).encode())
        lines = observability.get_logs('kafka')
        self.assertEqual([l['line'] for l in lines], ['first', 'second'])
        self.assertEqual(lines[0]['labels'], {'container': 'kafka'})

    def test_raises_unavailable_when_loki_is_unreachable(self):
        def boom(url, timeout=None):
            raise urllib.error.URLError('connection refused')
        urllib.request.urlopen = boom
        with self.assertRaises(observability.ObservabilityUnavailableError):
            observability.get_logs('kafka')


class GetMetricTests(unittest.TestCase):

    def setUp(self):
        self._orig_urlopen = urllib.request.urlopen

    def tearDown(self):
        urllib.request.urlopen = self._orig_urlopen

    def test_sends_the_promql_query_and_returns_the_result_series(self):
        seen = {}
        series = [{'metric': {}, 'values': [[1, '0.5']]}]

        def fake_urlopen(url, timeout=None):
            seen['url'] = url
            return _FakeResponse(json.dumps(
                {'status': 'success', 'data': {'result': series}}).encode())

        urllib.request.urlopen = fake_urlopen
        result = observability.get_metric('up{job="pinot-broker"}')
        query = urllib.parse.parse_qs(urllib.parse.urlparse(seen['url']).query)['query'][0]
        self.assertEqual(query, 'up{job="pinot-broker"}')
        self.assertEqual(result, series)

    def test_step_is_never_finer_than_15_seconds(self):
        seen = {}

        def fake_urlopen(url, timeout=None):
            seen['url'] = url
            return _FakeResponse(json.dumps(
                {'status': 'success', 'data': {'result': []}}).encode())

        urllib.request.urlopen = fake_urlopen
        observability.get_metric('up', range_='1m')  # 60s / 120 rounds to 0
        step = urllib.parse.parse_qs(urllib.parse.urlparse(seen['url']).query)['step'][0]
        self.assertEqual(step, '15')

    def test_raises_value_error_on_a_failed_query(self):
        urllib.request.urlopen = lambda url, timeout=None: _FakeResponse(json.dumps(
            {'status': 'error', 'error': 'bad promql'}).encode())
        with self.assertRaises(ValueError):
            observability.get_metric('not valid promql (')


if __name__ == '__main__':
    unittest.main()
