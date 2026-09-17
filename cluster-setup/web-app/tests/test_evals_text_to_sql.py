"""Tests for tests/evals/text_to_sql.py's pure grading logic — the part
that runs with no network, no live cluster, no API key. run_case() and
main() (the parts that actually call Pinot/Anthropic) are deliberately
untested here: they're the live half of an eval harness that is itself
never run in CI, per AI_PLATFORM_PLAN.md Track 4 ("CI stays offline").
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.evals import text_to_sql as evals


class LoadCasesTests(unittest.TestCase):

    def _write_set(self, tmp_path, lines):
        with open(tmp_path, 'w') as f:
            for line in lines:
                f.write(json.dumps(line) + '\n')

    def test_substitutes_date_in_question_and_golden_sql(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write(json.dumps({
                'id': 'x', 'question': 'delay on {date}?',
                'golden_sql': "WHERE dayBucket = '{date}'",
            }) + '\n')
            path = f.name
        try:
            cases = evals.load_cases(path, '2026-09-10')
            self.assertEqual(cases[0]['question'], 'delay on 2026-09-10?')
            self.assertEqual(cases[0]['golden_sql'], "WHERE dayBucket = '2026-09-10'")
        finally:
            os.unlink(path)

    def test_skips_blank_lines(self):
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            f.write(json.dumps({'id': 'a', 'question': 'q'}) + '\n')
            f.write('\n')
            f.write(json.dumps({'id': 'b', 'question': 'q'}) + '\n')
            path = f.name
        try:
            cases = evals.load_cases(path, '2026-01-01')
            self.assertEqual([c['id'] for c in cases], ['a', 'b'])
        finally:
            os.unlink(path)

    def test_the_checked_in_set_loads_and_has_unique_ids(self):
        default_set = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'tests', 'evals', 'text_to_sql.jsonl')
        cases = evals.load_cases(default_set, '2026-01-01')
        self.assertGreaterEqual(len(cases), 10)
        ids = [c['id'] for c in cases]
        self.assertEqual(len(ids), len(set(ids)))
        for c in cases:
            self.assertIn('question', c)
            self.assertNotIn('{date}', c['question'])


class RowsMatchTests(unittest.TestCase):

    def test_identical_rows_match(self):
        self.assertTrue(evals.rows_match(
            [{'cnt': 5}], [{'count': 5}]))  # different alias, same value

    def test_row_order_does_not_matter(self):
        a = [{'hour': 8, 'v': 1}, {'hour': 9, 'v': 2}]
        b = [{'hour': 9, 'v': 2}, {'hour': 8, 'v': 1}]
        self.assertTrue(evals.rows_match(a, b))

    def test_a_wrong_value_does_not_match(self):
        self.assertFalse(evals.rows_match([{'cnt': 5}], [{'cnt': 6}]))

    def test_a_missing_row_does_not_match(self):
        self.assertFalse(evals.rows_match(
            [{'cnt': 5}], [{'cnt': 5}, {'cnt': 6}]))

    def test_float_precision_differences_are_tolerated(self):
        # The agent's own ROUNDDECIMAL precision won't always match the
        # golden query's raw double — both round to the same whole unit.
        self.assertTrue(evals.rows_match(
            [{'avgDelay': 42.0}], [{'avg_delay': 42.4}]))

    def test_a_real_precision_difference_still_fails(self):
        self.assertFalse(evals.rows_match(
            [{'avgDelay': 42.0}], [{'avg_delay': 47.0}]))

    def test_empty_result_sets_match(self):
        self.assertTrue(evals.rows_match([], []))


class IsFullScanTests(unittest.TestCase):

    def test_true_when_any_column_mentions_full_scan(self):
        rows = [{'Operator': 'FILTER_FULL_SCAN(operator:AND)'}]
        self.assertTrue(evals.is_full_scan(rows))

    def test_false_for_an_indexed_plan(self):
        rows = [{'Operator': 'FILTER_SORTED_INDEX(operator:EQ)'},
                {'Operator': 'FILTER_STARTREE_INDEX'}]
        self.assertFalse(evals.is_full_scan(rows))

    def test_false_for_an_empty_plan(self):
        self.assertFalse(evals.is_full_scan([]))

    def test_checks_every_column_not_just_one_named_operator(self):
        # The exact EXPLAIN PLAN column name wasn't verified against a live
        # 1.5.1 controller (see the module docstring) — this must not
        # assume it is literally "Operator".
        rows = [{'Plan': 'some tree', 'Details': 'FILTER_FULL_SCAN here'}]
        self.assertTrue(evals.is_full_scan(rows))


class GradeTests(unittest.TestCase):

    def _case(self, **overrides):
        base = {'id': 'x', 'question': 'q?', 'max_scan_ratio': 0.01}
        base.update(overrides)
        return base

    def test_result_ok_true_when_rows_match_golden(self):
        agent_result = {'rows': [{'cnt': 5}], 'sql': 'SELECT ...',
                         'stats': {'docsScanned': 1, 'totalDocs': 100}, 'ms': 10}
        r = evals.grade(self._case(), agent_result, [{'cnt': 5}], None, [])
        self.assertTrue(r['result_ok'])

    def test_result_ok_false_when_rows_differ(self):
        agent_result = {'rows': [{'cnt': 5}], 'sql': 'SELECT ...',
                         'stats': {'docsScanned': 1, 'totalDocs': 100}, 'ms': 10}
        r = evals.grade(self._case(), agent_result, [{'cnt': 6}], None, [])
        self.assertFalse(r['result_ok'])

    def test_result_ok_none_when_check_result_is_false(self):
        agent_result = {'rows': [{'cnt': 5}], 'sql': 'SELECT ...',
                         'stats': {'docsScanned': 1, 'totalDocs': 100}, 'ms': 10}
        r = evals.grade(self._case(check_result=False), agent_result, [], None, [])
        self.assertIsNone(r['result_ok'])

    def test_result_ok_none_when_golden_sql_itself_errored(self):
        agent_result = {'rows': [{'cnt': 5}], 'sql': 'SELECT ...',
                         'stats': {'docsScanned': 1, 'totalDocs': 100}, 'ms': 10}
        r = evals.grade(self._case(), agent_result, [], 'no such column', [])
        self.assertIsNone(r['result_ok'])
        self.assertEqual(r['golden_error'], 'no such column')

    def test_index_hit_true_under_the_threshold(self):
        agent_result = {'rows': [], 'sql': 's', 'stats': {'docsScanned': 5, 'totalDocs': 1000}}
        r = evals.grade(self._case(max_scan_ratio=0.01), agent_result, [], None, [])
        self.assertTrue(r['index_hit'])
        self.assertEqual(r['scan_ratio'], 0.005)

    def test_index_hit_false_over_the_threshold(self):
        agent_result = {'rows': [], 'sql': 's', 'stats': {'docsScanned': 500, 'totalDocs': 1000}}
        r = evals.grade(self._case(max_scan_ratio=0.01), agent_result, [], None, [])
        self.assertFalse(r['index_hit'])

    def test_index_hit_false_when_no_query_ran_at_all(self):
        # e.g. a pure find_stops question with no run_pinot_sql call.
        agent_result = {'rows': [], 'sql': '', 'stats': None}
        r = evals.grade(self._case(), agent_result, [], None, [])
        self.assertFalse(r['index_hit'])
        self.assertIsNone(r['scan_ratio'])

    def test_full_scan_flows_through_from_explain_rows(self):
        agent_result = {'rows': [], 'sql': 's', 'stats': {'docsScanned': 1, 'totalDocs': 100}}
        r = evals.grade(self._case(), agent_result,
                         [], None, [{'Operator': 'FILTER_FULL_SCAN'}])
        self.assertTrue(r['full_scan'])


if __name__ == '__main__':
    unittest.main()
