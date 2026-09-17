"""Tests for mcp_server.py: each tool function is a thin JSON-serializing
wrapper around an already-tested app/ function (agent._guard_select,
pinot._query, pinot.get_schema, ingestion.ingestion_gaps,
observability.get_logs/get_metric) — these tests check the wrapping (guard
applied, arguments passed through, JSON shape) with those functions mocked,
not the underlying logic itself.
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mcp_server
from anthropic.lib.tools import ToolError
from app import ingestion, observability, pinot


class ToolRegistrationTests(unittest.TestCase):

    def test_all_eight_track_1_tools_are_registered(self):
        names = {t.name for t in mcp_server.mcp._tool_manager.list_tools()}
        self.assertEqual(names, {
            'run_pinot_sql', 'explain_sql', 'find_stops', 'get_schema',
            'cluster_health', 'ingestion_gaps', 'get_logs', 'get_metric',
        })


class RunPinotSqlTests(unittest.TestCase):

    def setUp(self):
        self._orig_query = pinot._query

    def tearDown(self):
        pinot._query = self._orig_query

    def test_guards_before_querying(self):
        def boom(sql, timeout=15):
            raise AssertionError('guard should have rejected before querying')
        pinot._query = boom
        with self.assertRaises(ToolError):
            mcp_server.run_pinot_sql('SELECT * FROM some_other_table')

    def test_returns_the_guarded_sql_rows_and_stats(self):
        pinot._query = lambda sql, timeout=15: (
            [{'routeShortName': '8'}], 12, {'docsScanned': 1})
        result = json.loads(mcp_server.run_pinot_sql(
            'SELECT routeShortName FROM gdansk_public_transport'))
        self.assertIn('LIMIT', result['sql'])  # the guard's own addition
        self.assertEqual(result['rows'], [{'routeShortName': '8'}])
        self.assertEqual(result['rowCount'], 1)
        self.assertEqual(result['timeUsedMs'], 12)
        self.assertEqual(result['stats'], {'docsScanned': 1})

    def test_caps_returned_rows_but_reports_the_real_count(self):
        many_rows = [{'vehicleId': i} for i in range(200)]
        pinot._query = lambda sql, timeout=15: (many_rows, 5, {})
        result = json.loads(mcp_server.run_pinot_sql(
            'SELECT vehicleId FROM gdansk_public_transport_latest'))
        self.assertEqual(len(result['rows']), 50)
        self.assertEqual(result['rowCount'], 200)

    def test_a_pinot_query_error_propagates_for_the_mcp_framework_to_translate(self):
        # FastMCP's own dispatch layer (not exercised at this level — see
        # mcp_server.py's module docstring) turns an uncaught exception into
        # a clean isError tool result; this just checks it has something to
        # catch, i.e. the exception isn't swallowed or masked here.
        def boom(sql, timeout=15):
            raise pinot.PinotQueryError([{'message': 'bad SQL'}])
        pinot._query = boom
        with self.assertRaises(pinot.PinotQueryError):
            mcp_server.run_pinot_sql('SELECT * FROM gdansk_public_transport')

    def test_a_pinot_unavailable_error_propagates(self):
        def boom(sql, timeout=15):
            raise pinot.PinotUnavailableError('broker unreachable: refused')
        pinot._query = boom
        with self.assertRaises(pinot.PinotUnavailableError):
            mcp_server.run_pinot_sql('SELECT * FROM gdansk_public_transport')


class ExplainSqlTests(unittest.TestCase):

    def setUp(self):
        self._orig_query = pinot._query

    def tearDown(self):
        pinot._query = self._orig_query

    def test_a_pinot_error_propagates_for_the_mcp_framework_to_translate(self):
        def boom(sql, timeout=15):
            raise pinot.PinotQueryError([{'message': 'bad SQL'}])
        pinot._query = boom
        with self.assertRaises(pinot.PinotQueryError):
            mcp_server.explain_sql('SELECT * FROM gdansk_public_transport')

    def test_wraps_the_sql_in_explain_plan_for(self):
        seen = {}

        def fake_query(sql, timeout=15):
            seen['sql'] = sql
            return [{'operator': 'FILTER_SORTED_INDEX'}], 5, {}
        pinot._query = fake_query
        result = json.loads(mcp_server.explain_sql(
            'SELECT * FROM gdansk_public_transport_latest'))
        self.assertTrue(seen['sql'].startswith('EXPLAIN PLAN FOR SELECT'))
        self.assertEqual(result, [{'operator': 'FILTER_SORTED_INDEX'}])


class FindStopsTests(unittest.TestCase):

    def setUp(self):
        self._orig_find_stops = mcp_server.agent._find_stops

    def tearDown(self):
        mcp_server.agent._find_stops = self._orig_find_stops

    def test_returns_agents_find_stops_result_as_json(self):
        mcp_server.agent._find_stops = lambda query: [{'stopId': 'S1', 'name': 'Wrzeszcz'}]
        result = json.loads(mcp_server.find_stops('Wrzeszcz'))
        self.assertEqual(result, [{'stopId': 'S1', 'name': 'Wrzeszcz'}])


class GetSchemaToolTests(unittest.TestCase):

    def setUp(self):
        self._orig_get_schema = pinot.get_schema

    def tearDown(self):
        pinot.get_schema = self._orig_get_schema

    def test_passes_the_table_through_and_returns_json(self):
        pinot.get_schema = lambda table: {'table': table, 'columns': []}
        result = json.loads(mcp_server.get_schema('gdansk_public_transport'))
        self.assertEqual(result['table'], 'gdansk_public_transport')

    def test_an_unknown_table_raises_rather_than_returning_empty_json(self):
        def boom(table):
            raise ValueError(f'unknown table: {table}')
        pinot.get_schema = boom
        with self.assertRaises(ValueError):
            mcp_server.get_schema('some_other_table')


class ClusterHealthToolTests(unittest.TestCase):

    def setUp(self):
        self._orig_cluster_health = pinot.cluster_health

    def tearDown(self):
        pinot.cluster_health = self._orig_cluster_health

    def test_returns_cluster_healths_result_as_json(self):
        pinot.cluster_health = lambda: {'controllerHealthy': True, 'tables': {}}
        result = json.loads(mcp_server.cluster_health())
        self.assertTrue(result['controllerHealthy'])


class IngestionGapsToolTests(unittest.TestCase):

    def setUp(self):
        self._orig_ingestion_gaps = ingestion.ingestion_gaps

    def tearDown(self):
        ingestion.ingestion_gaps = self._orig_ingestion_gaps

    def test_parses_the_iso_date_string_before_delegating(self):
        seen = {}

        def fake(day):
            seen['day'] = day
            return {'date': day.isoformat(), 'gaps': []}
        ingestion.ingestion_gaps = fake
        result = json.loads(mcp_server.ingestion_gaps('2026-09-01'))
        self.assertEqual(seen['day'].isoformat(), '2026-09-01')
        self.assertEqual(result['date'], '2026-09-01')


class GetLogsToolTests(unittest.TestCase):

    def setUp(self):
        self._orig_get_logs = observability.get_logs

    def tearDown(self):
        observability.get_logs = self._orig_get_logs

    def test_passes_container_since_and_pattern_through(self):
        seen = {}

        def fake(container, since, pattern):
            seen.update(container=container, since=since, pattern=pattern)
            return []
        observability.get_logs = fake
        mcp_server.get_logs('pinot-broker', since='2h', pattern='ERROR')
        self.assertEqual(seen, {'container': 'pinot-broker', 'since': '2h', 'pattern': 'ERROR'})

    def test_defaults_since_to_1h_and_pattern_to_none(self):
        seen = {}
        observability.get_logs = lambda container, since, pattern: seen.update(
            since=since, pattern=pattern)
        mcp_server.get_logs('kafka')
        self.assertEqual(seen, {'since': '1h', 'pattern': None})


class GetMetricToolTests(unittest.TestCase):

    def setUp(self):
        self._orig_get_metric = observability.get_metric

    def tearDown(self):
        observability.get_metric = self._orig_get_metric

    def test_passes_promql_and_range_through(self):
        seen = {}
        observability.get_metric = lambda promql, range_: seen.update(
            promql=promql, range_=range_) or [{'metric': {}, 'values': []}]
        result = json.loads(mcp_server.get_metric('up', range='30m'))
        self.assertEqual(seen, {'promql': 'up', 'range_': '30m'})
        self.assertEqual(result, [{'metric': {}, 'values': []}])


if __name__ == '__main__':
    unittest.main()
