"""All Pinot broker/controller access and JSON response-shaping.

One place for every SQL string and every raw urlopen() call against Pinot so
route handlers never talk to the broker/controller directly.
"""
import json
import re
import urllib.error
import urllib.request

from app.config import APPLICATION_JSON, PINOT_BROKER_URL, PINOT_CONTROLLER_URL

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
       -- Epoch millis, not the bare TIMESTAMP. Pinot renders a TIMESTAMP
       -- column on the wire as "2026-08-21 16:54:36.0" — a UTC wall clock
       -- with no zone marker — and new Date() in the browser parses that
       -- as *local* time, so the tooltip read 2 h behind in CEST (1 h in
       -- CET). Millis also travel smaller and skip a per-row string parse.
       CAST(generatedTransformed AS LONG) AS lastSeen
FROM gdansk_public_transport_latest
WHERE generatedTransformed > ago('PT10M')
LIMIT 2000
"""

# Current delay per (route, course number) from the GPS feed, used to shift
# scheduled departures: a delayed vehicle stays listed until its *estimated*
# time passes. Reads the upsert table (same as POSITIONS_SQL) instead of
# aggregating LASTWITHTIME over the history table's 10-minute window: the
# latest row per vehicle already *is* the latest delay, so a plain scan of
# ~1-2k rows replaces a GROUP BY over every ping in the window.
DELAYS_SQL = """
SELECT routeShortName, tripId, delay
FROM gdansk_public_transport_latest
WHERE generatedTransformed > ago('PT10M')
LIMIT 2000
"""

# GPS ping density on a ~100 m grid over the last 24 h, for the map's heatmap
# layer ("where do buses spend their time"). Derived from the density-grid
# query formerly in gdansk_public_transport_queries.sql, at 3 decimals instead
# of 2 so the hotspots read at street level. Bounding box = Gdansk metro area,
# same as the original query.
HEATMAP_SQL = """
-- Grouped on a 0.001-degree lattice (~111 m N/S, ~65 m E/W at this latitude),
-- but plotted at the *centroid* of the pings in each cell, not at the lattice
-- point. Emitting the rounded value put every blob on a regular grid up to
-- ~55 m N/S and ~32 m E/W from any vehicle -- measured 13-48 m on the busiest
-- cells -- which parked hotspots on blocks of flats beside the road instead of
-- on it. Pings in a cell lie along the same road, so their centroid lands on
-- the road. Grouping is unchanged, so cell count, LIMIT and payload are too.
SELECT ROUNDDECIMAL(AVG(lat), 5)   AS latCell,
       ROUNDDECIMAL(AVG(lon), 5)   AS lonCell,
       COUNT(*)                    AS pings,
       ROUNDDECIMAL(AVG(speed), 1) AS avgSpeed
FROM gdansk_public_transport_REALTIME
WHERE generatedTransformed > ago('P1D')
  AND lat BETWEEN 54.27 AND 54.50
  AND lon BETWEEN 18.45 AND 18.80
GROUP BY ROUNDDECIMAL(lat, 3), ROUNDDECIMAL(lon, 3)
ORDER BY pings DESC
LIMIT 20000
"""

# Headline numbers for the header strip: total GPS points (broker COUNT(*)
# over the hybrid table, realtime + offline), segment count and reported size
# (controller APIs). A filter-free COUNT(*) is answered from segment metadata,
# so it stays instant without caching.
STATS_SQL = 'SELECT COUNT(*) AS totalDocs FROM gdansk_public_transport'

# Drill-down: average delay by hour of day, for one route, over the table's
# *entire* history (no time filter) — the interactive "click a route" demo of
# routeShortName being the sorted column (see gdansk_public_transport_*
# table configs): a whole-history GROUP BY that would otherwise be the exact
# shape of query that times out (see PINOT_IMPROVEMENT_PLAN.md item 1) stays
# fast here because the equality filter prunes via the sort order.
#
# HOUR() int buckets, not TODATETIME('HH:00') string keys: the per-row Joda
# formatting dominated the query (measured 2.0 s → 0.4 s warm here, 2.0 s →
# 0.1 s on NETWORK_HOURLY_SQL; cold runs of the old form passed the broker's
# 10 s timeout and surfaced as 502s in the UI). The 'HH:00' labels the
# frontend keys on are re-attached in Python — see _hour_labeled().
ROUTE_HOURLY_DELAY_SQL = """
SELECT HOUR(generatedTransformed, 'Europe/Warsaw') AS hourOfDay,
       ROUNDDECIMAL(AVG(delay), 0) AS avgDelaySec,
       COUNT(*)                    AS snapshots
FROM gdansk_public_transport
WHERE routeShortName = '{route}'
GROUP BY hourOfDay
ORDER BY hourOfDay
LIMIT 24
"""
# Hour-by-hour count of distinct active vehicles across the whole network over
# the last 24 h — the "network rush hour" panel on the Analytics tab's native
# overview strip (formerly Superset chart P7, moved here because the embedded
# SDK spends seconds booting Superset's frontend inside the iframe while this
# query answers in tens of ms). HOUR() folds the window's two partial hours
# (yesterday's and today's share of the current wall-clock hour) into one
# bucket, same as the original chart's 'HH:00' keys — the int bucket is
# relabeled in _hour_labeled() (see ROUTE_HOURLY_DELAY_SQL for the numbers).
# DISTINCTCOUNTBITMAP instead of COUNT(DISTINCT): exact for an INT column
# like vehicleId, and the roaring bitmaps merge across segments/servers
# cheaper than the default hash sets.
NETWORK_HOURLY_SQL = """
SELECT HOUR(generatedTransformed, 'Europe/Warsaw') AS hourOfDay,
       DISTINCTCOUNTBITMAP(vehicleId) AS activeVehicles
FROM gdansk_public_transport
WHERE generatedTransformed > ago('P1D')
GROUP BY hourOfDay
ORDER BY hourOfDay
LIMIT 100
"""

# Route short names are short alphanumeric codes (trams "8", "12"; buses incl.
# night lines "N1", "159") — never containing a quote or other SQL-meaningful
# character. Validated before string-interpolating into ROUTE_HOURLY_DELAY_SQL
# since the Pinot broker's plain SQL endpoint has no parameterized-query
# option to use instead.
_ROUTE_RE = re.compile(r'^[A-Za-z0-9]{1,10}$')


class PinotQueryError(Exception):
    """Raised when Pinot's response body carries a query-execution error."""
    def __init__(self, exceptions):
        super().__init__(str(exceptions))
        self.exceptions = exceptions


class PinotUnavailableError(Exception):
    """Raised when the broker/controller never answered at all.

    Distinct from PinotQueryError, which means Pinot *did* answer and said
    the query failed. This one is the cluster being unreachable or too busy
    to reply within the socket budget — expected under load (an S3 backfill
    building a multi-million-row segment on the same host will do it), so
    route handlers turn it into a 504 instead of an unexpected-error 500.
    """


# How much longer than the query budget the socket waits. Pinot enforces the
# budget itself (see _query), so the extra seconds only cover response
# transfer — the point is that the broker's own structured timeout error
# wins the race and surfaces as a 502 with a real message, rather than the
# socket giving up first and leaving us with a bare TimeoutError.
_SOCKET_GRACE_SEC = 2


def _read_json(target, timeout, what):
    """urlopen + json.load for a Request or plain URL, transport failures
    normalized.

    Every way of *not getting an answer* — socket timeout, connection
    refused, an HTTP error page — becomes PinotUnavailableError so callers
    have one thing to catch instead of a bare TimeoutError escaping as a 500.
    """
    try:
        with urllib.request.urlopen(target, timeout=timeout) as resp:
            return json.load(resp)
    except TimeoutError as e:
        raise PinotUnavailableError(
            f'{what} did not respond within {timeout}s') from e
    except urllib.error.URLError as e:
        raise PinotUnavailableError(f'{what} unreachable: {e.reason}') from e


def _query(sql, timeout=15):
    """Run a SQL query against the broker; return (rows, timeUsedMs).

    `timeout` is the *query* budget in seconds, handed to Pinot as the
    queryOptions timeoutMs (which overrides the broker's own default) and
    used to derive the socket timeout — see _SOCKET_GRACE_SEC.

    timeUsedMs is Pinot's own broker-reported query time — surfaced end to
    end as a latency badge in the app, not just used internally.
    """
    payload = {'sql': sql, 'queryOptions': f'timeoutMs={int(timeout * 1000)}'}
    req = urllib.request.Request(
        PINOT_BROKER_URL + '/query/sql',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': APPLICATION_JSON},
    )
    result = _read_json(req, timeout + _SOCKET_GRACE_SEC, 'broker')
    if result.get('exceptions'):
        raise PinotQueryError(result['exceptions'])
    cols = result['resultTable']['dataSchema']['columnNames']
    rows = [dict(zip(cols, r)) for r in result['resultTable']['rows']]
    return rows, result.get('timeUsedMs', 0)


def get_positions():
    return _query(POSITIONS_SQL, timeout=15)


def _hour_labeled(row):
    """Swap the query's INT hourOfDay bucket for the 'HH:00' label the
    frontend charts key on — formatting ~24 rows here is free, unlike
    TODATETIME's per-row formatting inside Pinot (see ROUTE_HOURLY_DELAY_SQL).
    """
    out = dict(row)
    out['hour'] = '%02d:00' % out.pop('hourOfDay')
    return out


def route_hourly_delay(route):
    """(rows of {hour, avgDelaySec, snapshots} for one route's whole history, timeUsedMs)."""
    if not _ROUTE_RE.match(route):
        raise ValueError('invalid route')
    rows, ms = _query(ROUTE_HOURLY_DELAY_SQL.format(route=route), timeout=15)
    return [_hour_labeled(r) for r in rows], ms


def live_delays():
    """((route short name, course no as str) -> current delay in seconds, timeUsedMs)."""
    try:
        rows, ms = _query(DELAYS_SQL, timeout=10)
        data = {(str(r['routeShortName']), str(r['tripId'])): r['delay'] for r in rows}
    except Exception as e:  # degrade to schedule-only rather than fail
        print(f'delays: fetch failed: {e}')
        return {}, 0
    return data, ms


def heatmap_cells():
    return _query(HEATMAP_SQL, timeout=30)


def network_hourly():
    """(rows of {hour, activeVehicles} over the last 24 h, timeUsedMs)."""
    rows, ms = _query(NETWORK_HOURLY_SQL, timeout=15)
    return [_hour_labeled(r) for r in rows], ms


def table_stats():
    rows, ms = _query(STATS_SQL, timeout=15)
    docs = rows[0]['totalDocs']
    size_bytes = _read_json(
        PINOT_CONTROLLER_URL + '/tables/gdansk_public_transport/size?detailed=false',
        15, 'controller')['reportedSizeInBytes']
    # [{"OFFLINE": [names…]}, {"REALTIME": [names…]}]
    segments = sum(
        len(names)
        for entry in _read_json(
            PINOT_CONTROLLER_URL + '/segments/gdansk_public_transport',
            15, 'controller')
        for names in entry.values())
    data = {'totalDocs': docs, 'segments': segments, 'sizeBytes': size_bytes}
    return data, ms
