"""All Pinot broker/controller access and JSON response-shaping.

One place for every SQL string and every raw urlopen() call against Pinot so
route handlers never talk to the broker/controller directly.
"""
import json
import re
import urllib.request

from app.cache import TTLCache
from app.config import APPLICATION_JSON, PINOT_BROKER_URL, PINOT_CONTROLLER_URL, \
    DELAYS_TTL_S, HEATMAP_TTL_S, NETWORK_HOURLY_TTL_S, STATS_TTL_S

# Pinot's INT null sentinel (Integer.MIN_VALUE) — what a null tripId (no
# default set in either gdansk_public_transport schema) reads back as for a
# vehicle currently between trips (e.g. laying over at a depot/terminus) —
# used to derive the inService flag on /api/positions.
NULL_INT = -2147483648

# Latest position per vehicle. gdansk_public_transport_latest is upsert-enabled
# (primary key vehicleId), so a plain SELECT already returns exactly one row
# per vehicle — no LASTWITHTIME/GROUP BY/MAX needed. The WHERE clause is still
# required despite that: upsert only guarantees "one row per key", not
# freshness — a vehicle's row lingers in the table for its full 1-day
# retention even after it goes quiet (e.g. pulls into a depot), so the
# 10-minute window is what actually drops silent vehicles off the map.
POSITIONS_SQL = """
SELECT vehicleId,
       lat,
       lon,
       routeShortName          AS route,
       routeId,
       tripId,
       headsign,
       delay,
       speed,
       direction,
       generatedTransformed    AS lastSeen
FROM gdansk_public_transport_latest
WHERE generatedTransformed > ago('PT10M')
LIMIT 2000
"""

# Current delay per (route, course number) from the GPS feed, used to shift
# scheduled departures: a delayed vehicle stays listed until its *estimated*
# time passes. Cached briefly so stop clicks don't hammer the broker.
DELAYS_SQL = """
SELECT routeShortName, tripId,
       LASTWITHTIME(delay, generatedTransformed, 'INT') AS delay
FROM gdansk_public_transport_REALTIME
WHERE generatedTransformed > ago('PT10M')
GROUP BY routeShortName, tripId
LIMIT 2000
"""

# GPS ping density on a ~100 m grid over the last 24 h, for the map's heatmap
# layer ("where do buses spend their time"). Derived from the density-grid
# query formerly in gdansk_public_transport_queries.sql, at 3 decimals instead
# of 2 so the hotspots read at street level. Bounding box = Gdansk metro area,
# same as the original query. Cached: the picture moves slowly and every
# toggle-on would otherwise hit the broker.
HEATMAP_SQL = """
SELECT ROUNDDECIMAL(lat, 3)        AS latCell,
       ROUNDDECIMAL(lon, 3)        AS lonCell,
       COUNT(*)                    AS pings,
       ROUNDDECIMAL(AVG(speed), 1) AS avgSpeed
FROM gdansk_public_transport_REALTIME
WHERE generatedTransformed > ago('P1D')
  AND lat BETWEEN 54.27 AND 54.50
  AND lon BETWEEN 18.45 AND 18.80
GROUP BY latCell, lonCell
ORDER BY pings DESC
LIMIT 20000
"""

# Headline numbers for the header strip: total GPS points (broker COUNT(*)
# over the hybrid table, realtime + offline), segment count and reported size
# (controller APIs). Cached — every visitor loads them and they move slowly.
STATS_SQL = 'SELECT COUNT(*) AS totalDocs FROM gdansk_public_transport'

# Drill-down: average delay by hour of day, for one route, over the table's
# *entire* history (no time filter) — the interactive "click a route" demo of
# routeShortName being the sorted column (see gdansk_public_transport_*
# table configs): a whole-history GROUP BY that would otherwise be the exact
# shape of query that times out (see PINOT_IMPROVEMENT_PLAN.md item 1) stays
# fast here because the equality filter prunes via the sort order.
ROUTE_HOURLY_DELAY_SQL = """
SELECT TODATETIME(generatedTransformed, 'HH:00', 'Europe/Warsaw') AS hour,
       ROUNDDECIMAL(AVG(delay), 0) AS avgDelaySec,
       COUNT(*)                    AS snapshots
FROM gdansk_public_transport
WHERE routeShortName = '{route}'
  AND NOT (speed = 0 AND delay > 1200)
GROUP BY hour
ORDER BY hour
"""
# Hour-by-hour count of distinct active vehicles across the whole network over
# the last 24 h — the "network rush hour" panel on the Analytics tab's native
# overview strip (formerly Superset chart P7, moved here because the embedded
# SDK spends seconds booting Superset's frontend inside the iframe while this
# query answers in tens of ms). TODATETIME folds the window's two partial
# hours (yesterday's and today's share of the current wall-clock hour) into
# one 'HH:00' bucket, same as the original chart. Cached: whole-network
# aggregate, moves slowly.
NETWORK_HOURLY_SQL = """
SELECT TODATETIME(generatedTransformed, 'HH:00', 'Europe/Warsaw') AS hour,
       COUNT(DISTINCT vehicleId) AS activeVehicles
FROM gdansk_public_transport
WHERE generatedTransformed > ago('P1D')
GROUP BY hour
ORDER BY hour
LIMIT 100
"""

# Route short names are short alphanumeric codes (trams "8", "12"; buses incl.
# night lines "N1", "159") — never containing a quote or other SQL-meaningful
# character. Validated before string-interpolating into ROUTE_HOURLY_DELAY_SQL
# since the Pinot broker's plain SQL endpoint has no parameterized-query
# option to use instead.
_ROUTE_RE = re.compile(r'^[A-Za-z0-9]{1,10}$')

_delays_cache = TTLCache(DELAYS_TTL_S)
_heatmap_cache = TTLCache(HEATMAP_TTL_S)
_stats_cache = TTLCache(STATS_TTL_S)
_network_hourly_cache = TTLCache(NETWORK_HOURLY_TTL_S)


class PinotQueryError(Exception):
    """Raised when Pinot's response body carries a query-execution error."""
    def __init__(self, exceptions):
        super().__init__(str(exceptions))
        self.exceptions = exceptions


def _query(sql, timeout=15):
    """Run a SQL query against the broker; return (rows, timeUsedMs).

    timeUsedMs is Pinot's own broker-reported query time — surfaced end to
    end as a latency badge in the app, not just used internally.
    """
    req = urllib.request.Request(
        PINOT_BROKER_URL + '/query/sql',
        data=json.dumps({'sql': sql}).encode('utf-8'),
        headers={'Content-Type': APPLICATION_JSON},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        result = json.load(resp)
    if result.get('exceptions'):
        raise PinotQueryError(result['exceptions'])
    cols = result['resultTable']['dataSchema']['columnNames']
    rows = [dict(zip(cols, r)) for r in result['resultTable']['rows']]
    return rows, result.get('timeUsedMs', 0)


def get_positions():
    return _query(POSITIONS_SQL, timeout=15)


def route_hourly_delay(route):
    """(rows of {hour, avgDelaySec, snapshots} for one route's whole history, timeUsedMs).

    Not cached: the point of this drill-down is showing how fast the query
    itself is, not how fast our own cache is.
    """
    if not _ROUTE_RE.match(route):
        raise ValueError('invalid route')
    return _query(ROUTE_HOURLY_DELAY_SQL.format(route=route), timeout=15)


def live_delays():
    """((route short name, course no as str) -> current delay in seconds, timeUsedMs)."""
    cached = _delays_cache.get()
    if cached is not None:
        return cached
    try:
        rows, ms = _query(DELAYS_SQL, timeout=10)
        data = {(str(r['routeShortName']), str(r['tripId'])): r['delay'] for r in rows}
    except Exception as e:  # degrade to schedule-only rather than fail
        print(f'delays: fetch failed: {e}')
        return _delays_cache.get_stale() or ({}, 0)
    result = (data, ms)
    _delays_cache.set(result)
    return result


def heatmap_cells():
    cached = _heatmap_cache.get()
    if cached is not None:
        return cached
    rows, ms = _query(HEATMAP_SQL, timeout=30)
    result = (rows, ms)
    _heatmap_cache.set(result)
    return result


def network_hourly():
    """(rows of {hour, activeVehicles} over the last 24 h, timeUsedMs)."""
    cached = _network_hourly_cache.get()
    if cached is not None:
        return cached
    result = _query(NETWORK_HOURLY_SQL, timeout=15)
    _network_hourly_cache.set(result)
    return result


def table_stats():
    cached = _stats_cache.get()
    if cached is not None:
        return cached
    rows, ms = _query(STATS_SQL, timeout=15)
    docs = rows[0]['totalDocs']
    with urllib.request.urlopen(
            PINOT_CONTROLLER_URL + '/tables/gdansk_public_transport/size?detailed=false',
            timeout=15) as resp:
        size_bytes = json.load(resp)['reportedSizeInBytes']
    with urllib.request.urlopen(
            PINOT_CONTROLLER_URL + '/segments/gdansk_public_transport',
            timeout=15) as resp:
        # [{"OFFLINE": [names…]}, {"REALTIME": [names…]}]
        segments = sum(len(names) for entry in json.load(resp)
                       for names in entry.values())
    data = {'totalDocs': docs, 'segments': segments, 'sizeBytes': size_bytes}
    result = (data, ms)
    _stats_cache.set(result)
    return result
