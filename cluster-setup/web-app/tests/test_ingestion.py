"""Tests for app.ingestion: the gap-merging logic in isolation (no S3 call —
_find_gaps is a pure function over a set of present (HH, MM) pairs) and
ingestion_gaps()'s day-boundary handling, with _present_minutes mocked.
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.ingestion as ingestion
from botocore.exceptions import EndpointConnectionError


def _minutes(*hhmm_pairs):
    """('00:00', '00:01', ...) -> {('00','00'), ('00','01'), ...}"""
    return {(hhmm[:2], hhmm[3:]) for hhmm in hhmm_pairs}


def _all_but(*missing_hhmm):
    """Every minute of a day except the given ones, as a present-set."""
    missing = {(hhmm[:2], hhmm[3:]) for hhmm in missing_hhmm}
    return {('%02d' % (m // 60), '%02d' % (m % 60)) for m in range(1440)} - missing


class FindGapsTests(unittest.TestCase):

    def test_no_gaps_when_every_minute_is_present(self):
        present = {('%02d' % (m // 60), '%02d' % (m % 60)) for m in range(60)}
        self.assertEqual(ingestion._find_gaps(present, up_to_minute=60), [])

    def test_a_single_missing_minute_is_a_one_minute_gap(self):
        present = _all_but('00:05')
        gaps = ingestion._find_gaps(present, up_to_minute=10)
        self.assertEqual(gaps, [{'start': '00:05', 'end': '00:05', 'missingMinutes': 1}])

    def test_a_contiguous_run_merges_into_one_gap(self):
        present = _all_but('00:05', '00:06', '00:07')
        gaps = ingestion._find_gaps(present, up_to_minute=10)
        self.assertEqual(gaps, [{'start': '00:05', 'end': '00:07', 'missingMinutes': 3}])

    def test_two_separate_runs_are_two_gaps(self):
        present = _all_but('00:01', '00:08')
        gaps = ingestion._find_gaps(present, up_to_minute=10)
        self.assertEqual(gaps, [
            {'start': '00:01', 'end': '00:01', 'missingMinutes': 1},
            {'start': '00:08', 'end': '00:08', 'missingMinutes': 1},
        ])

    def test_a_gap_still_open_at_the_end_of_the_window_is_closed_there(self):
        present = _minutes('00:00', '00:01')  # everything from 00:02 on is missing
        gaps = ingestion._find_gaps(present, up_to_minute=5)
        self.assertEqual(gaps, [{'start': '00:02', 'end': '00:04', 'missingMinutes': 3}])

    def test_a_gap_crossing_an_hour_boundary(self):
        present = _all_but('00:59', '01:00', '01:01')
        gaps = ingestion._find_gaps(present, up_to_minute=125)
        self.assertEqual(gaps, [{'start': '00:59', 'end': '01:01', 'missingMinutes': 3}])


class PresentMinutesErrorTests(unittest.TestCase):
    """S3 failures are folded into one exception type, same shape as
    pinot.PinotUnavailableError / observability.ObservabilityUnavailableError
    — not left as a raw botocore exception escaping the MCP tool."""

    def setUp(self):
        self._orig_client = ingestion.boto3.client

    def tearDown(self):
        ingestion.boto3.client = self._orig_client

    def test_a_boto_core_error_becomes_ingestion_unavailable_error(self):
        class _FakeS3:
            def get_paginator(self, name):
                raise EndpointConnectionError(endpoint_url='https://s3.amazonaws.com')
        ingestion.boto3.client = lambda service: _FakeS3()
        with self.assertRaises(ingestion.IngestionUnavailableError):
            ingestion._present_minutes(datetime.date(2026, 9, 1))


class IngestionGapsTests(unittest.TestCase):

    def setUp(self):
        self._orig_present_minutes = ingestion._present_minutes

    def tearDown(self):
        ingestion._present_minutes = self._orig_present_minutes

    def test_a_fully_elapsed_day_expects_all_1440_minutes(self):
        ingestion._present_minutes = lambda day: set()
        yesterday = (datetime.datetime.now(datetime.timezone.utc)
                     - datetime.timedelta(days=1)).date()
        result = ingestion.ingestion_gaps(yesterday)
        self.assertEqual(result['expectedMinutes'], 1440)
        self.assertEqual(result['missingMinutes'], 1440)

    def test_a_future_day_expects_nothing_and_skips_the_s3_call(self):
        def boom(day):
            raise AssertionError('should not list S3 for a day that has not happened yet')
        ingestion._present_minutes = boom
        tomorrow = (datetime.datetime.now(datetime.timezone.utc)
                    + datetime.timedelta(days=1)).date()
        result = ingestion.ingestion_gaps(tomorrow)
        self.assertEqual(result, {
            'date': tomorrow.isoformat(), 'expectedMinutes': 0,
            'missingMinutes': 0, 'gaps': [],
        })

    def test_todays_expected_minutes_track_the_wall_clock_not_the_full_day(self):
        ingestion._present_minutes = lambda day: set()
        today = datetime.datetime.now(datetime.timezone.utc).date()
        result = ingestion.ingestion_gaps(today)
        self.assertLess(result['expectedMinutes'], 1440)
        self.assertEqual(result['missingMinutes'], result['expectedMinutes'])

    def test_missing_minutes_totals_across_every_gap(self):
        ingestion._present_minutes = lambda day: _all_but('00:01', '00:05', '00:06')
        yesterday = (datetime.datetime.now(datetime.timezone.utc)
                     - datetime.timedelta(days=1)).date()
        result = ingestion.ingestion_gaps(yesterday)
        self.assertEqual(result['missingMinutes'], 3)
        self.assertEqual(len(result['gaps']), 2)


if __name__ == '__main__':
    unittest.main()
