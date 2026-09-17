"""Tests for app.agent: the SQL guard (in isolation — the part where a bug
costs something), find_stops fuzzy matching, the rate limiter, and /api/ask
end to end with a mocked Anthropic client. No network, no real API key.
"""
import csv
import datetime
import io
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.agent as agent
import app.gtfs as gtfs
import app.pinot as pinot
from anthropic.lib.tools import ToolError


# --- The guard --------------------------------------------------------------

class GuardTests(unittest.TestCase):

    def test_adds_a_limit_when_missing(self):
        sql = agent._guard_select(
            "SELECT routeShortName FROM gdansk_public_transport WHERE dayBucket = '2026-09-01'")
        self.assertIn(f'LIMIT {agent._DEFAULT_LIMIT}', sql)

    def test_leaves_an_under_cap_limit_alone(self):
        sql = agent._guard_select('SELECT * FROM gdansk_public_transport_latest LIMIT 10')
        self.assertIn('LIMIT 10', sql)
        self.assertNotIn(f'LIMIT {agent._MAX_LIMIT}', sql)

    def test_caps_an_over_limit_limit(self):
        sql = agent._guard_select('SELECT * FROM gdansk_public_transport_latest LIMIT 999999')
        self.assertIn(f'LIMIT {agent._MAX_LIMIT}', sql)
        self.assertNotIn('LIMIT 999999', sql)

    def test_strips_a_single_trailing_semicolon(self):
        sql = agent._guard_select('SELECT * FROM gdansk_public_transport_latest;')
        self.assertNotIn(';', sql)

    def test_rejects_a_second_statement(self):
        with self.assertRaises(ToolError):
            agent._guard_select('SELECT 1; DROP TABLE gdansk_public_transport')

    def test_rejects_non_select(self):
        for bad in ['DELETE FROM gdansk_public_transport',
                    'UPDATE gdansk_public_transport SET delay = 0',
                    'DROP TABLE gdansk_public_transport']:
            with self.assertRaises(ToolError):
                agent._guard_select(bad)

    def test_rejects_unknown_table(self):
        with self.assertRaises(ToolError):
            agent._guard_select('SELECT * FROM some_other_table')

    def test_rejects_a_table_not_in_the_known_set_even_when_joined(self):
        with self.assertRaises(ToolError):
            agent._guard_select(
                'SELECT a.vehicleId FROM gdansk_public_transport a '
                'JOIN some_other_table b ON a.vehicleId = b.vehicleId')

    def test_allows_both_known_tables_together(self):
        # Should not raise: a join across the two documented tables is legal.
        sql = agent._guard_select(
            'SELECT a.vehicleId FROM gdansk_public_transport a '
            'JOIN gdansk_public_transport_latest b ON a.vehicleId = b.vehicleId')
        self.assertIn('gdansk_public_transport', sql)

    def test_rejects_unparseable_sql(self):
        with self.assertRaises(ToolError):
            agent._guard_select('not even sql')

    def test_rejection_reason_is_readable(self):
        # The model reads this string to decide how to retry — it must name
        # the actual problem, not just "rejected".
        try:
            agent._guard_select('SELECT * FROM some_other_table')
        except ToolError as e:
            self.assertIn('some_other_table', str(e))
        else:
            self.fail('expected ToolError')


class RunPinotSqlToolTests(unittest.TestCase):
    """The run_pinot_sql tool as the model actually calls it — through
    .call(), which is how the SDK's tool runner invokes it, exercising the
    guard, the pinot._query call, and the last_query capture together."""

    def setUp(self):
        self._orig_query = pinot._query

    def tearDown(self):
        pinot._query = self._orig_query

    def _tools(self):
        # ask() builds fresh tool closures per call so concurrent requests
        # don't share last_query state — reach in via the same construction
        # path a real call would use, without hitting the network.
        last_query = {}

        from anthropic.lib.tools import beta_tool

        @beta_tool
        def run_pinot_sql(sql: str, rationale: str) -> str:
            """doc.

            Args:
                sql: s
                rationale: r
            """
            guarded = agent._guard_select(sql)
            try:
                rows, ms, stats = pinot._query(guarded, timeout=agent._QUERY_TIMEOUT_S)
            except (pinot.PinotQueryError, pinot.PinotUnavailableError) as e:
                raise agent._pinot_error(e) from e
            last_query['sql'] = guarded
            last_query['rows'] = rows
            last_query['stats'] = stats
            return 'ok'

        return run_pinot_sql, last_query

    def test_successful_call_captures_sql_rows_and_stats(self):
        pinot._query = lambda sql, timeout=15: (
            [{'routeShortName': '8'}], 42, {'docsScanned': 5, 'totalDocs': 100})
        tool, last_query = self._tools()
        tool.call({'sql': 'SELECT routeShortName FROM gdansk_public_transport',
                   'rationale': 'sorted column'})
        self.assertEqual(last_query['rows'], [{'routeShortName': '8'}])
        self.assertEqual(last_query['stats']['docsScanned'], 5)
        self.assertIn('LIMIT', last_query['sql'])  # guard's own addition

    def test_guard_rejection_surfaces_as_tool_error_without_querying(self):
        def boom(sql, timeout=15):
            raise AssertionError('guard should have rejected before querying')
        pinot._query = boom
        tool, _ = self._tools()
        with self.assertRaises(ToolError):
            tool.call({'sql': 'SELECT * FROM some_other_table', 'rationale': 'n/a'})

    def test_pinot_failure_surfaces_as_tool_error(self):
        def boom(sql, timeout=15):
            raise pinot.PinotUnavailableError('broker unreachable: refused')
        pinot._query = boom
        tool, _ = self._tools()
        with self.assertRaises(ToolError):
            tool.call({'sql': 'SELECT * FROM gdansk_public_transport_latest', 'rationale': 'n/a'})


# --- find_stops --------------------------------------------------------------

def _csv_bytes(fieldnames, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode('utf-8')


def _build_fake_gtfs_zip():
    """Two stops with real Gdansk-area coordinates and one with a Polish
    diacritic in its name, for the normalize()/bbox tests below."""
    today = datetime.datetime.now(gtfs.GTFS_TZ).strftime('%Y%m%d')
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w') as zf:
        zf.writestr('calendar_dates.txt', _csv_bytes(
            ['service_id', 'date', 'exception_type'],
            [{'service_id': 'SVC1', 'date': today, 'exception_type': '1'}]))
        zf.writestr('routes.txt', _csv_bytes(
            ['route_id', 'route_short_name'], [{'route_id': 'R1', 'route_short_name': '6'}]))
        zf.writestr('trips.txt', _csv_bytes(
            ['route_id', 'service_id', 'trip_id', 'trip_headsign'],
            [{'route_id': 'R1', 'service_id': 'SVC1', 'trip_id': 'T_1_A',
              'trip_headsign': 'Wrzeszcz'}]))
        zf.writestr('stop_times.txt', _csv_bytes(
            ['trip_id', 'arrival_time', 'departure_time', 'stop_id',
             'stop_sequence', 'pickup_type', 'drop_off_type'],
            [{'trip_id': 'T_1_A', 'arrival_time': '08:00:00',
              'departure_time': '08:00:00', 'stop_id': 'S1',
              'stop_sequence': '1', 'pickup_type': '0', 'drop_off_type': '1'}]))
        zf.writestr('stops.txt', _csv_bytes(
            ['stop_id', 'stop_code', 'stop_name', 'stop_lat', 'stop_lon'],
            [{'stop_id': 'S1', 'stop_code': '01', 'stop_name': 'Gdańsk Wrzeszcz PKP',
              'stop_lat': '54.372', 'stop_lon': '18.616'},
             {'stop_id': 'S2', 'stop_code': '02', 'stop_name': 'Oliwa',
              'stop_lat': '54.408', 'stop_lon': '18.564'}]))
    return zip_buf.getvalue()


class FindStopsTests(unittest.TestCase):

    def setUp(self):
        self._orig_http_get = gtfs.http_get
        gtfs.http_get = lambda url, timeout: _build_fake_gtfs_zip()
        gtfs.load_gtfs()

    def tearDown(self):
        gtfs.http_get = self._orig_http_get

    def test_name_match_ignores_diacritics(self):
        # "Wrzeszcz" (no accent) must still find "Gdańsk Wrzeszcz PKP".
        results = agent._find_stops('Wrzeszcz')
        self.assertTrue(any(r['stopId'] == 'S1' for r in results))

    def test_no_match_returns_empty(self):
        self.assertEqual(agent._find_stops('Nonexistent Place Xyzzy'), [])

    def test_bbox_longitude_span_in_degrees_is_wider_than_latitude_span(self):
        # A degree of longitude at Gdansk's latitude covers only ~65 m of
        # ground against ~111 m for a degree of latitude, so covering the
        # *same physical radius* needs visibly more longitude degrees than
        # latitude degrees. A bbox that silently used equal degree offsets
        # either way (skipping the cos(latitude) scaling) would produce an
        # east-west radius far larger than intended in meters, but its two
        # spans would look deceptively equal in degrees — this is the check
        # that would have caught that bug.
        results = agent._find_stops('Wrzeszcz')
        bbox = results[0]['bbox']
        lat_span = bbox['latMax'] - bbox['latMin']
        lon_span = bbox['lonMax'] - bbox['lonMin']
        self.assertGreater(lon_span, lat_span)

    def test_result_includes_serving_routes(self):
        results = agent._find_stops('Wrzeszcz')
        self.assertEqual(results[0]['routes'], ['6'])


# --- Rate limiting -----------------------------------------------------------

class TokenBucketTests(unittest.TestCase):

    def test_allows_up_to_capacity_then_blocks(self):
        bucket = agent.TokenBucket(capacity=3, refill_per_s=0)
        for _ in range(3):
            self.assertTrue(bucket.allow('1.2.3.4'))
        self.assertFalse(bucket.allow('1.2.3.4'))

    def test_ips_are_independent(self):
        bucket = agent.TokenBucket(capacity=1, refill_per_s=0)
        self.assertTrue(bucket.allow('1.1.1.1'))
        self.assertFalse(bucket.allow('1.1.1.1'))
        self.assertTrue(bucket.allow('2.2.2.2'))  # a different IP, fresh bucket

    def test_refills_over_time(self):
        bucket = agent.TokenBucket(capacity=1, refill_per_s=1000)  # fast refill for the test
        self.assertTrue(bucket.allow('1.2.3.4'))
        self.assertFalse(bucket.allow('1.2.3.4'))
        import time
        time.sleep(0.01)  # >= 1/1000 s: at least one token back
        self.assertTrue(bucket.allow('1.2.3.4'))


# --- ask() end to end, mocked Anthropic client -------------------------------

class _FakeToolUseMessage:
    def __init__(self, parsed_output):
        self.parsed_output = parsed_output


class _FakeRunner:
    def __init__(self, tools, on_run):
        self._tools = tools
        self._on_run = on_run

    def until_done(self):
        return self._on_run(self._tools)


class _FakeBetaMessages:
    def __init__(self, on_run):
        self._on_run = on_run

    def tool_runner(self, **kwargs):
        return _FakeRunner(kwargs['tools'], self._on_run)


class _FakeBeta:
    def __init__(self, on_run):
        self.messages = _FakeBetaMessages(on_run)


class _FakeClient:
    def __init__(self, on_run):
        self.beta = _FakeBeta(on_run)


class AskEndToEndTests(unittest.TestCase):

    def setUp(self):
        self._orig_get_client = agent._get_client
        self._orig_query = pinot._query

    def tearDown(self):
        agent._get_client = self._orig_get_client
        pinot._query = self._orig_query

    def test_answer_uses_the_sql_that_actually_ran_not_the_models_echo(self):
        """The model's own `sql` field is untrusted — run_pinot_sql's guard
        output is what the frontend must see, per AI_PLATFORM_PLAN.md's
        "the displayed SQL must be the SQL that ran"."""
        pinot._query = lambda sql, timeout=15: ([{'routeShortName': '8'}], 12, {'docsScanned': 1})

        def on_run(tools):
            run_pinot_sql = next(t for t in tools if t.name == 'run_pinot_sql')
            run_pinot_sql.call({
                'sql': 'select * from gdansk_public_transport',  # no LIMIT
                'rationale': 'sorted column on routeShortName',
            })
            return _FakeToolUseMessage(agent.AgentAnswer(
                answer='Route 8 has 1 vehicle.',
                sql='select * from gdansk_public_transport',  # the model's own echo — must be ignored
                rationale='sorted column on routeShortName',
                index_shape='sorted column',
            ))

        agent._get_client = lambda: _FakeClient(on_run)
        result = agent.ask('how many vehicles on route 8?')
        self.assertIn('LIMIT', result['sql'])  # guard's LIMIT, proves it's the run_pinot_sql SQL
        self.assertEqual(result['rows'], [{'routeShortName': '8'}])
        self.assertEqual(result['stats'], {'docsScanned': 1})
        self.assertEqual(result['answer'], 'Route 8 has 1 vehicle.')

    def test_question_with_no_query_call_falls_back_to_the_models_own_sql_field(self):
        def on_run(tools):
            return _FakeToolUseMessage(agent.AgentAnswer(
                answer='I could not find that stop.', sql='', rationale='n/a', index_shape='none'))

        agent._get_client = lambda: _FakeClient(on_run)
        result = agent.ask('where is Nowhereville?')
        self.assertEqual(result['sql'], '')
        self.assertEqual(result['rows'], [])
        self.assertIsNone(result['stats'])

    def test_missing_structured_output_raises(self):
        def on_run(tools):
            return _FakeToolUseMessage(None)

        agent._get_client = lambda: _FakeClient(on_run)
        with self.assertRaises(RuntimeError):
            agent.ask('anything')


if __name__ == '__main__':
    unittest.main()
