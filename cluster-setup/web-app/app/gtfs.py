"""GTFS loading and schedule-derived lookups (stops, departures, termini).

The ZTM GTFS feed covers the next ~14 days and is regenerated daily. We keep
only today's and tomorrow's service days in memory: enough for a "next 60
minutes / next 3 departures" popup, and small (~300k stop_times rows).
"""
import csv
import datetime
import io
import math
import threading
import time
import zipfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.config import GTFS_URL, GTFS_REFRESH_S, TERMINUS_RADIUS_M
from app.http_client import http_get

# GTFS times are local Gdansk wall clock. Windows host Pythons ship no IANA
# database — fall back to the system zone there (dev only; the Debian-based
# container image always has tzdata).
try:
    GTFS_TZ = ZoneInfo('Europe/Warsaw')
except ZoneInfoNotFoundError:
    GTFS_TZ = datetime.datetime.now().astimezone().tzinfo
    print(f'GTFS: Europe/Warsaw tzdata unavailable, using system zone {GTFS_TZ}')

_lock = threading.Lock()
_state = {'stops': [], 'departures': {}, 'trip_ends': {}, 'trip_last_stop': {},
          'routes': [], 'loaded_at': None}


def is_loaded():
    with _lock:
        return _state['loaded_at'] is not None


def get_stops():
    with _lock:
        return _state['stops']


def get_routes():
    """All route short names known to the schedule, not just ones with a
    live vehicle right now — lets the dropdown show idle routes too."""
    with _lock:
        return _state['routes']


def get_departures(stop_id):
    with _lock:
        return _state['departures'].get(stop_id)


def get_trip_ends():
    with _lock:
        return _state['trip_ends']


def get_trip_last_stop(route_id, trip_id):
    with _lock:
        return _state['trip_last_stop'].get((route_id, trip_id))


def at_terminus(row, trip_ends):
    """True when the vehicle is within TERMINUS_RADIUS_M of a terminus of the
    course it is serving (the schedule-derived first/last stop of the trip)."""
    pts = trip_ends.get((str(row.get('route')), str(row.get('tripId'))))
    if not pts:
        return False
    kx = math.cos(math.radians(row['lat']))
    for slat, slon in pts:
        dx = (slon - row['lon']) * kx
        dy = slat - row['lat']
        if math.sqrt(dx * dx + dy * dy) * 111320 <= TERMINUS_RADIUS_M:
            return True
    return False


def _gtfs_rows(zf, name):
    return csv.DictReader(io.TextIOWrapper(zf.open(name), encoding='utf-8-sig'))


def _load_service_dates(zf, days):
    """service_id -> date object (today/tomorrow only)."""
    service_date = {}
    for r in _gtfs_rows(zf, 'calendar_dates.txt'):
        if r['exception_type'] == '1' and r['date'] in days:
            service_date[r['service_id']] = days[r['date']]
    return service_date


def _load_route_names(zf):
    return {r['route_id']: r['route_short_name']
            for r in _gtfs_rows(zf, 'routes.txt')}


def _load_trips(zf, service_date, route_name):
    """trip_id -> (service date, route id, route short name, headsign)."""
    trips = {}
    for r in _gtfs_rows(zf, 'trips.txt'):
        d = service_date.get(r['service_id'])
        if d:
            trips[r['trip_id']] = (d, r['route_id'],
                                   route_name.get(r['route_id'], '?'),
                                   r['trip_headsign'])
    return trips


def _update_trip_span(trip_span, trip_id, seq, stop_id):
    """Track [min seq, its stop, max seq, its stop] per trip — first/last stop
    of the trip, updated before the pickup_type skip since a trip's final stop
    is usually dropoff-only."""
    span = trip_span.get(trip_id)
    if span is None:
        trip_span[trip_id] = [seq, stop_id, seq, stop_id]
    else:
        if seq < span[0]:
            span[0], span[1] = seq, stop_id
        if seq > span[2]:
            span[2], span[3] = seq, stop_id


def _load_stop_times(zf, trips, noon):
    """Per-stop sorted departures, stop_id -> serving routes, and per-trip
    [min seq, stop, max seq, stop] span."""
    departures = {}
    stop_routes = {}  # stop_id -> set of route short names serving it
    trip_span = {}  # trip_id -> [min seq, its stop, max seq, its stop]
    for r in _gtfs_rows(zf, 'stop_times.txt'):
        trip = trips.get(r['trip_id'])
        if not trip:
            continue
        d, _route_id, route, headsign = trip
        stop_routes.setdefault(r['stop_id'], set()).add(route)
        _update_trip_span(trip_span, r['trip_id'], int(r['stop_sequence']),
                           r['stop_id'])
        if r['pickup_type'] == '1':  # 1 = no passenger pickup
            continue
        h, m, s = map(int, r['departure_time'].split(':'))
        dep = noon[d] + datetime.timedelta(seconds=(h - 12) * 3600 + m * 60 + s)
        # GTFS trip_id = <route+start datetime>_<course no>_<task>; the course
        # number is what the GPS feed publishes as tripId — the join key for
        # attaching live delays to scheduled departures.
        parts = r['trip_id'].split('_')
        trip_no = parts[1] if len(parts) == 3 else None
        departures.setdefault(r['stop_id'], []).append(
            (int(dep.timestamp() * 1000), route, headsign, trip_no))
    for lst in departures.values():
        lst.sort()
    return departures, stop_routes, trip_span


def _load_stops(zf, stop_routes):
    return [{'stopId': r['stop_id'], 'name': r['stop_name'],
              'code': r['stop_code'],
              'lat': float(r['stop_lat']), 'lon': float(r['stop_lon']),
              'routes': sorted(stop_routes.get(r['stop_id'], ()))}
             for r in _gtfs_rows(zf, 'stops.txt')]


def _build_trip_ends(trip_span, trips, stop_coord):
    """(route short name, course no) -> {(lat, lon), …} of that course's
    scheduled first/last stop — the termini where the vehicle rests between
    runs. Powers the atTerminus flag on /api/positions.

    (GTFS route id, course no) -> (lat, lon) of the course's scheduled last
    stop only, keyed the way /api/route-shape receives its query params —
    used to trim the shapes API's polyline, which commonly overshoots the
    last passenger stop (loop-back curves, depot access roads)."""
    trip_ends = {}
    trip_last_stop = {}
    for trip_id, (_, first, _, last) in trip_span.items():
        parts = trip_id.split('_')
        if len(parts) != 3:  # no course number to join on
            continue
        _d, route_id, route_short, _headsign = trips[trip_id]
        pts = trip_ends.setdefault((route_short, parts[1]), set())
        for sid in (first, last):
            if sid in stop_coord:
                pts.add(stop_coord[sid])
        if last in stop_coord:
            trip_last_stop[(route_id, parts[1])] = stop_coord[last]
    return trip_ends, trip_last_stop


def load_gtfs():
    """Parse the GTFS zip into stops + per-stop sorted departure lists."""
    zf = zipfile.ZipFile(io.BytesIO(http_get(GTFS_URL, timeout=120)))

    today = datetime.datetime.now(GTFS_TZ).date()
    days = {today.strftime('%Y%m%d'): today,
            (today + datetime.timedelta(days=1)).strftime('%Y%m%d'):
                today + datetime.timedelta(days=1)}

    service_date = _load_service_dates(zf, days)
    route_name = _load_route_names(zf)
    trips = _load_trips(zf, service_date, route_name)

    # GTFS times are "noon minus 12 h"-relative and may exceed 24:00 for
    # after-midnight courses; anchoring at noon keeps DST days correct.
    noon = {d: datetime.datetime.combine(d, datetime.time(12), GTFS_TZ)
            for d in days.values()}
    departures, stop_routes, trip_span = _load_stop_times(zf, trips, noon)
    stops = _load_stops(zf, stop_routes)

    stop_coord = {s['stopId']: (s['lat'], s['lon']) for s in stops}
    trip_ends, trip_last_stop = _build_trip_ends(trip_span, trips, stop_coord)

    with _lock:
        _state['stops'] = stops
        _state['departures'] = departures
        _state['trip_ends'] = trip_ends
        _state['trip_last_stop'] = trip_last_stop
        _state['routes'] = sorted(set(route_name.values()))
        _state['loaded_at'] = time.time()
    print(f'GTFS: loaded {len(stops)} stops, '
          f'{sum(map(len, departures.values()))} departures, '
          f'termini for {len(trip_ends)} courses '
          f'for {sorted(days)}')


def gtfs_refresher():
    while True:
        try:
            load_gtfs()
            time.sleep(GTFS_REFRESH_S)
        except Exception as e:  # keep last good data, retry sooner
            print(f'GTFS: refresh failed: {e}')
            time.sleep(300)
