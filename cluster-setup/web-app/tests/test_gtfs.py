"""Tests for app.gtfs: service-day parsing, departure generation, and the
terminus-radius geometry check (at_terminus) — flagged in the refactor review
notes as missing from the original test plan.

Exercises the real load_gtfs() parser against a small in-memory GTFS zip
(http_get is monkeypatched) rather than mocking the parsing logic itself.
"""
import csv
import datetime
import io
import os
import sys
import unittest
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.gtfs as gtfs

TODAY = datetime.datetime.now(gtfs.GTFS_TZ).date()
TODAY_STR = TODAY.strftime('%Y%m%d')


def _csv_bytes(fieldnames, rows):
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames)
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue().encode('utf-8')


def build_fake_gtfs_zip():
    """A minimal feed: one route, one trip (course "1"), two stops, with a
    dropoff-only final stop (pickup_type=1) so it's excluded from departures
    but still counts as the scheduled terminus."""
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w') as zf:
        zf.writestr('calendar_dates.txt', _csv_bytes(
            ['service_id', 'date', 'exception_type'],
            [{'service_id': 'SVC1', 'date': TODAY_STR, 'exception_type': '1'}]))
        zf.writestr('routes.txt', _csv_bytes(
            ['route_id', 'route_short_name'],
            [{'route_id': 'R1', 'route_short_name': '8'}]))
        # trip_id must be <anything>_<course no>_<task> (3 underscore parts)
        # for the course-number join used by departures/trip_ends/last_stop.
        zf.writestr('trips.txt', _csv_bytes(
            ['route_id', 'service_id', 'trip_id', 'trip_headsign'],
            [{'route_id': 'R1', 'service_id': 'SVC1', 'trip_id': 'T_1_A',
              'trip_headsign': 'Jelitkowo'}]))
        zf.writestr('stop_times.txt', _csv_bytes(
            ['trip_id', 'arrival_time', 'departure_time', 'stop_id',
             'stop_sequence', 'pickup_type'],
            [
                {'trip_id': 'T_1_A', 'arrival_time': '08:00:00',
                 'departure_time': '08:00:00', 'stop_id': 'S1',
                 'stop_sequence': '1', 'pickup_type': '0'},
                {'trip_id': 'T_1_A', 'arrival_time': '08:10:00',
                 'departure_time': '08:10:00', 'stop_id': 'S2',
                 'stop_sequence': '2', 'pickup_type': '1'},
            ]))
        zf.writestr('stops.txt', _csv_bytes(
            ['stop_id', 'stop_code', 'stop_name', 'stop_lat', 'stop_lon'],
            [
                {'stop_id': 'S1', 'stop_code': '01', 'stop_name': 'Start',
                 'stop_lat': '54.35000', 'stop_lon': '18.64000'},
                {'stop_id': 'S2', 'stop_code': '02', 'stop_name': 'End',
                 'stop_lat': '54.36000', 'stop_lon': '18.65000'},
            ]))
    return zip_buf.getvalue()


class GtfsLoadTests(unittest.TestCase):

    def setUp(self):
        self._orig_http_get = gtfs.http_get
        gtfs.http_get = lambda url, timeout: build_fake_gtfs_zip()
        gtfs.load_gtfs()

    def tearDown(self):
        gtfs.http_get = self._orig_http_get

    def test_stops_loaded_with_route_membership(self):
        stops = {s['stopId']: s for s in gtfs.get_stops()}
        self.assertEqual(stops['S1']['routes'], ['8'])
        self.assertEqual(stops['S2']['routes'], ['8'])

    def test_departures_exclude_dropoff_only_final_stop(self):
        # S1 has pickup_type 0 -> a real departure; S2 is pickup_type 1 ->
        # dropoff-only, so it should have no departures at all.
        self.assertIsNotNone(gtfs.get_departures('S1'))
        self.assertEqual(len(gtfs.get_departures('S1')), 1)
        self.assertIsNone(gtfs.get_departures('S2'))

    def test_departure_fields(self):
        _, route, headsign, trip_no = gtfs.get_departures('S1')[0]
        self.assertEqual(route, '8')
        self.assertEqual(headsign, 'Jelitkowo')
        self.assertEqual(trip_no, '1')  # course number from trip_id "T_1_A"

    def test_trip_last_stop_is_the_dropoff_only_final_stop(self):
        # Keyed (GTFS route_id, course_no) — S2 is the last stop even though
        # it's excluded from departures.
        last = gtfs.get_trip_last_stop('R1', '1')
        self.assertEqual(last, (54.36, 18.65))

    def test_at_terminus_true_near_first_or_last_stop(self):
        trip_ends = gtfs.get_trip_ends()
        near_start = {'route': '8', 'tripId': '1', 'lat': 54.35001, 'lon': 18.64001}
        self.assertTrue(gtfs.at_terminus(near_start, trip_ends))

    def test_at_terminus_false_far_from_both_ends(self):
        trip_ends = gtfs.get_trip_ends()
        mid_route = {'route': '8', 'tripId': '1', 'lat': 54.40, 'lon': 18.70}
        self.assertFalse(gtfs.at_terminus(mid_route, trip_ends))

    def test_at_terminus_false_for_unknown_course(self):
        trip_ends = gtfs.get_trip_ends()
        unknown = {'route': '8', 'tripId': '999', 'lat': 54.35, 'lon': 18.64}
        self.assertFalse(gtfs.at_terminus(unknown, trip_ends))


if __name__ == '__main__':
    unittest.main()
