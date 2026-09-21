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
from typing import NamedTuple
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


class TripSpan(NamedTuple):
    """A trip's scheduled extent: the stops at its lowest and highest
    stop_sequence, i.e. where the course starts and where it ends."""
    first_seq: int
    first_stop: str
    last_seq: int
    last_stop: str


def _update_trip_span(trip_span, trip_id, seq, stop_id):
    """Widen the trip's first/last stop pair with one stop_times row.

    Called before the pickup_type skip, since a trip's final stop is usually
    drop-off only and is exactly the stop wanted here."""
    span = trip_span.get(trip_id)
    if span is None:
        trip_span[trip_id] = TripSpan(seq, stop_id, seq, stop_id)
        return
    if seq < span.first_seq:
        span = span._replace(first_seq=seq, first_stop=stop_id)
    if seq > span.last_seq:
        span = span._replace(last_seq=seq, last_stop=stop_id)
    trip_span[trip_id] = span


# GTFS pickup/drop-off type values the ZTM feed uses. 3 is its "na żądanie"
# (request) stop — a real boarding, the passenger just has to signal the
# driver — and matches the onDemand flag in ZTM's own stops.json, so it must
# not be filtered out the way 1 is.
REGULAR = '0'
NO_PICKUP = '1'


class StopTime(NamedTuple):
    """One scheduled call at a stop, as the departures popup needs it.

    `trip_no` is the course number the GPS feed publishes as tripId — the
    join key for attaching a live delay to a scheduled departure — or None
    for a trip_id that does not carry one.
    """
    time_ms: int
    route: str
    headsign: str
    trip_no: str | None


def _stop_time(r, trip, noon):
    """One boardable stop_times row as the StopTime the popup shows.

    Times are offsets from the service day's noon (built in load_gtfs, which
    says why) rather than its midnight, so a 25:10 after-midnight course
    lands on the right day.
    """
    d, _route_id, route, headsign = trip
    h, m, s = map(int, r['departure_time'].split(':'))
    dep = noon[d] + datetime.timedelta(seconds=(h - 12) * 3600 + m * 60 + s)
    # GTFS trip_id = <route+start datetime>_<course no>_<task>; the course
    # number is what the GPS feed publishes as tripId — the join key for
    # attaching live delays to scheduled departures.
    parts = r['trip_id'].split('_')
    trip_no = parts[1] if len(parts) == 3 else None
    return StopTime(int(dep.timestamp() * 1000), route, headsign, trip_no)


def _group_departures(by_stop, in_service):
    """Sorted departure list and serving routes per stop, passenger trips only.

    Drops the positioning runs (see _load_stop_times), which is why this
    waits for the whole file: a trip is only known not to carry passengers
    once every one of its stop times has been read.
    """
    departures = {}
    stop_routes = {}  # stop_id -> set of route short names serving it
    for stop_id, lst in by_stop.items():
        kept = [st for st, trip_id in lst if trip_id in in_service]
        if not kept:
            continue
        kept.sort()  # by departure time: StopTime's first field
        departures[stop_id] = kept
        # Derived from what survived, so a pole where a line only ever drops
        # off — a depot such as "Zajezdnia NOWY PORT T1", every row
        # pickup_type=1 — advertises no line that can't be boarded there. The
        # map would otherwise mark it as served and the popup then have
        # nothing to show. A normal terminus keeps its route from the return
        # trip's boardable row at the same pole.
        stop_routes[stop_id] = {st.route for st in kept}
    return departures, stop_routes


def _load_stop_times(zf, trips, noon):
    """Read stop_times.txt into per-stop departures, serving routes and spans.

    One pass over ~300k rows doing three things at once, which is why the two
    halves that can stand alone are split out: this loop accumulates, and
    _stop_time / _group_departures shape what it accumulated.
    """
    # stop_id -> [(StopTime, trip_id)] — the trip_id rides alongside only
    # until the non-passenger trips below have been filtered out, which
    # needs the whole file read first, and is then dropped.
    by_stop = {}
    trip_span = {}  # trip_id -> TripSpan
    regular_pickup = set()    # trip_ids with an ordinary boarding somewhere
    regular_drop_off = set()  # trip_ids with an ordinary alighting somewhere
    for r in _gtfs_rows(zf, 'stop_times.txt'):
        trip = trips.get(r['trip_id'])
        if not trip:
            continue
        _update_trip_span(trip_span, r['trip_id'], int(r['stop_sequence']),
                          r['stop_id'])
        # Both columns are optional in GTFS and default to regular service.
        pickup = r.get('pickup_type') or REGULAR
        drop_off = r.get('drop_off_type') or REGULAR
        if pickup == REGULAR:
            regular_pickup.add(r['trip_id'])
        if drop_off == REGULAR:
            regular_drop_off.add(r['trip_id'])
        if pickup == NO_PICKUP:
            continue
        by_stop.setdefault(r['stop_id'], []).append(
            (_stop_time(r, trip, noon), r['trip_id']))

    # A trip with no regular pickup *and* no regular drop-off anywhere is a
    # positioning run (ZTM marks both ends type 3, not 1), not a service.
    # Type 3 alone stays boardable: the ~430 genuine request stops use it.
    departures, stop_routes = _group_departures(
        by_stop, regular_pickup | regular_drop_off)
    return departures, stop_routes, trip_span


# Beyond this, a course number is ambiguous: tomorrow's run reuses it, so a
# schedule row that far from now must stay schedule-only.
DELAY_MATCH_WINDOW_MS = 3 * 3_600_000


def upcoming_departures(deps, delays, now):
    """Schedule rows + live delays -> future departures, earliest first.

    `deps` is get_departures() output — StopTime rows, unpacked positionally
    so any 4-sequence works. `delays` is keyed (route, course no) as
    pinot.live_delays() returns it. Kept free of any Pinot import so the
    merge stays a pure function of its arguments.
    """
    upcoming = []
    for t, route, headsign, trip_no in deps or ():
        delay_s = (delays.get((route, trip_no))
                   if trip_no and abs(t - now) < DELAY_MATCH_WINDOW_MS else None)
        est = t + (delay_s or 0) * 1000
        if est < now:  # departed (per estimate, when live; else schedule)
            continue
        upcoming.append({
            'time': t, 'estimated': est, 'route': route,
            'headsign': headsign,
            'delayMin': None if delay_s is None else round(delay_s / 60),
        })
    upcoming.sort(key=lambda d: d['estimated'])  # delays can reorder
    return upcoming


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
    for trip_id, span in trip_span.items():
        parts = trip_id.split('_')
        if len(parts) != 3:  # no course number to join on
            continue
        _d, route_id, route_short, _headsign = trips[trip_id]
        pts = trip_ends.setdefault((route_short, parts[1]), set())
        for sid in (span.first_stop, span.last_stop):
            if sid in stop_coord:
                pts.add(stop_coord[sid])
        if span.last_stop in stop_coord:
            trip_last_stop[(route_id, parts[1])] = stop_coord[span.last_stop]
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
