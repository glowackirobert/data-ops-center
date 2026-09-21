"""All Pinot broker/controller access and JSON response-shaping. Every SQL
string and every call against Pinot lives here."""
import functools
import json
import re
import urllib.error
import urllib.request

from app.config import APPLICATION_JSON, PINOT_BROKER_URL, PINOT_CONTROLLER_URL
from app.http_client import read_json

# The only tables app/agent.py's SQL guard and mcp_server.py's tools may reference.
KNOWN_TABLES = {'gdansk_public_transport', 'gdansk_public_transport_latest'}

# Pinot's INT null sentinel (Integer.MIN_VALUE): what a null tripId reads back
# as for a vehicle between trips. Derives the inService flag on /api/positions.
NULL_INT = -2147483648

# gdansk_public_transport_latest is upsert-enabled (primary key vehicleId), so
# a plain SELECT is one row per vehicle. The WHERE is still required: upsert
# guarantees one row per key, not freshness — a vehicle that goes quiet keeps
# its row for the full 1-day retention, and the 10-minute window is what
# drops it off the map.
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
       -- Epoch millis, not the bare TIMESTAMP: Pinot renders that as a
       -- zone-less UTC string that new Date() parses as *local* time.
       CAST(generatedTransformed AS LONG) AS lastSeen
FROM gdansk_public_transport_latest
WHERE generatedTransformed > ago('PT10M')
LIMIT 2000
"""

# Current delay per (route, course number), to shift scheduled departures.
# The upsert table's latest row per vehicle already is the latest delay.
DELAYS_SQL = """
SELECT routeShortName, tripId, delay
FROM gdansk_public_transport_latest
WHERE generatedTransformed > ago('PT10M')
LIMIT 2000
"""

# GPS ping density on a ~100 m grid over the last 24 h, for the heatmap layer.
HEATMAP_SQL = """
-- Grouped on a 0.001-degree lattice (~111 m N/S, ~65 m E/W here) but plotted
-- at the *centroid* of each cell's pings: the lattice point itself sat up to
-- ~55 m off the road, parking hotspots on the blocks of flats beside it.
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

# A filter-free COUNT(*) is answered from segment metadata, so it stays instant.
STATS_SQL = 'SELECT COUNT(*) AS totalDocs FROM gdansk_public_transport'

# Average delay by hour of day for one route over the table's entire history:
# the equality filter on routeShortName, the sorted column, is what keeps a
# whole-history GROUP BY fast. HOUR() int buckets, not TODATETIME('HH:00'):
# the per-row formatting dominated the query (2.0 s → 0.4 s); the labels the
# frontend keys on are re-attached in _hour_labeled().
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
# Distinct active vehicles per hour over the last 24 h (the "network rush
# hour" panel). HOUR() folds the window's two partial hours into one bucket.
# DISTINCTCOUNTBITMAP is exact for an INT column and merges across segments
# cheaper than COUNT(DISTINCT).
NETWORK_HOURLY_SQL = """
SELECT HOUR(generatedTransformed, 'Europe/Warsaw') AS hourOfDay,
       DISTINCTCOUNTBITMAP(vehicleId) AS activeVehicles
FROM gdansk_public_transport
WHERE generatedTransformed > ago('P1D')
GROUP BY hourOfDay
ORDER BY hourOfDay
LIMIT 100
"""

# Validated before interpolating into ROUTE_HOURLY_DELAY_SQL — the broker's
# SQL endpoint has no parameterized-query option.
_ROUTE_RE = re.compile(r'^[A-Za-z0-9]{1,10}$')


class PinotQueryError(Exception):
    """Pinot answered and said the query failed."""
    def __init__(self, exceptions):
        super().__init__(str(exceptions))
        self.exceptions = exceptions


class PinotUnavailableError(Exception):
    """The broker/controller never answered — down, or too busy to reply
    within the socket budget (an S3 backfill on the same host will do it).
    Route handlers turn it into a 504, not a 500."""


# How much longer than the query budget the socket waits: Pinot enforces the
# budget itself, so its structured timeout error wins the race and surfaces
# as a 502 with a real message rather than a bare socket TimeoutError.
_SOCKET_GRACE_SEC = 2

_read_json = functools.partial(read_json, error=PinotUnavailableError)


def _query(sql, timeout=15):
    """Run a SQL query against the broker; return (rows, timeUsedMs, stats).

    `timeout` is the query budget in seconds, handed to Pinot as timeoutMs.
    timeUsedMs is Pinot's own reported query time (the latency badge) and
    stats the scan figures behind it (the explain panel)."""
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
    stats = {
        'docsScanned': result.get('numDocsScanned', 0),
        'totalDocs': result.get('totalDocs', 0),
        'segmentsProcessed': result.get('numSegmentsProcessed', 0),
        'segmentsQueried': result.get('numSegmentsQueried', 0),
        'segmentsPruned': result.get('numSegmentsPrunedByValue', 0),
        'serversQueried': result.get('numServersQueried', 0),
    }
    return rows, result.get('timeUsedMs', 0), stats


def get_positions():
    return _query(POSITIONS_SQL, timeout=15)


def _hour_labeled(row):
    """Swap the INT hourOfDay bucket for the 'HH:00' label the charts key on."""
    out = dict(row)
    out['hour'] = '%02d:00' % out.pop('hourOfDay')
    return out


def route_hourly_delay(route):
    """(rows of {hour, avgDelaySec, snapshots} for one route's whole history, timeUsedMs, stats)."""
    if not _ROUTE_RE.match(route):
        raise ValueError('invalid route')
    rows, ms, stats = _query(ROUTE_HOURLY_DELAY_SQL.format(route=route), timeout=15)
    return [_hour_labeled(r) for r in rows], ms, stats


def live_delays():
    """((route short name, course no as str) -> current delay in seconds, timeUsedMs, stats)."""
    try:
        rows, ms, stats = _query(DELAYS_SQL, timeout=10)
        data = {(str(r['routeShortName']), str(r['tripId'])): r['delay'] for r in rows}
    except Exception as e:  # degrade to schedule-only rather than fail
        print(f'delays: fetch failed: {e}')
        return {}, 0, None
    return data, ms, stats


def heatmap_cells():
    return _query(HEATMAP_SQL, timeout=30)


def network_hourly():
    """(rows of {hour, activeVehicles} over the last 24 h, timeUsedMs, stats)."""
    rows, ms, stats = _query(NETWORK_HOURLY_SQL, timeout=15)
    return [_hour_labeled(r) for r in rows], ms, stats


def table_stats():
    rows, ms, stats = _query(STATS_SQL, timeout=15)
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
    return data, ms, stats


def get_schema(table):
    """Columns/types plus sorted column, index columns and star-tree config —
    the shape an MCP client needs (AI_PLATFORM_PLAN.md Track 1). Raises
    ValueError for a table outside KNOWN_TABLES."""
    if table not in KNOWN_TABLES:
        raise ValueError(f'unknown table: {table}')
    schema = _read_json(PINOT_CONTROLLER_URL + f'/schemas/{table}', 15, 'controller')
    columns = [
        {'name': f['name'], 'dataType': f['dataType']}
        for kind in ('dimensionFieldSpecs', 'metricFieldSpecs', 'dateTimeFieldSpecs')
        for f in schema.get(kind, [])
    ]
    # {"OFFLINE": {tableConfig...}, "REALTIME": {tableConfig...}}
    configs = _read_json(PINOT_CONTROLLER_URL + f'/tables/{table}', 15, 'controller')
    table_types = {}
    for table_type, config in configs.items():
        index_config = config.get('tableIndexConfig', {})
        star_tree = [
            {'dimensionsSplitOrder': s.get('dimensionsSplitOrder', []),
             'functionColumnPairs': s.get('functionColumnPairs', [])}
            for s in (index_config.get('starTreeIndexConfigs') or [])
        ]
        table_types[table_type] = {
            'sortedColumn': index_config.get('sortedColumn', []),
            'invertedIndexColumns': index_config.get('invertedIndexColumns', []),
            'rangeIndexColumns': index_config.get('rangeIndexColumns', []),
            'starTree': star_tree,
        }
    return {'table': table, 'columns': columns, 'tableTypes': table_types}


def _controller_healthy():
    try:
        # urlopen raises on any non-2xx, so reaching the body check is success.
        with urllib.request.urlopen(PINOT_CONTROLLER_URL + '/health', timeout=10) as resp:
            return resp.read().strip() == b'OK'
    except (TimeoutError, urllib.error.URLError):
        return False


def _segment_states(table, table_type):
    """{state: count} over one table type's external view."""
    view = _read_json(
        PINOT_CONTROLLER_URL + f'/tables/{table}_{table_type}/externalview', 15, 'controller')
    states = {}
    for segment_states in view.values():
        for state in segment_states.values():
            states[state] = states.get(state, 0) + 1
    return states


def _table_health(table):
    size_bytes = _read_json(
        PINOT_CONTROLLER_URL + f'/tables/{table}/size?detailed=false',
        15, 'controller').get('reportedSizeInBytes')
    info = {'sizeBytes': size_bytes, 'segmentStates': {}, 'consuming': False}
    # [{"OFFLINE": [names…]}, {"REALTIME": [names…]}]
    for entry in _read_json(PINOT_CONTROLLER_URL + f'/segments/{table}', 15, 'controller'):
        for table_type, names in entry.items():
            if names:
                states = _segment_states(table, table_type)
                info['segmentStates'][table_type] = states
                if states.get('CONSUMING'):
                    info['consuming'] = True
    return info


def cluster_health():
    """Controller reachability and, per known table, deep-store size and
    segment counts by state. A CONSUMING segment in the external view is the
    "realtime is consuming" signal.

    The /tables/{tableWithType}/externalview shape wasn't checked against a
    live 1.5.1 controller — verify at http://localhost:9000/help before
    relying on it operationally."""
    return {'controllerHealthy': _controller_healthy(),
            'tables': {t: _table_health(t) for t in sorted(KNOWN_TABLES)}}
