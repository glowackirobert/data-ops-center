"""All Pinot broker/controller access and JSON response-shaping.

One place for every SQL string and every raw urlopen() call against Pinot so
route handlers never talk to the broker/controller directly.
"""
import json
import urllib.request

from app.cache import TTLCache
from app.config import APPLICATION_JSON, PINOT_BROKER_URL, PINOT_CONTROLLER_URL, \
    DELAYS_TTL_S, HEATMAP_TTL_S, STATS_TTL_S

# Latest position per vehicle seen in the last 10 minutes. The Kafka feed only
# publishes changed positions, so a 10-minute window keeps parked vehicles
# visible while LASTWITHTIME dedupes to the freshest row.
# _REALTIME suffix: skips the OFFLINE side of the hybrid table — its many
# batch-ingested segments add broker planning overhead, and a 10-minute window
# is always within realtime retention (1 day).
POSITIONS_SQL = """
SELECT vehicleId,
       LASTWITHTIME(lat, generatedTransformed, 'DOUBLE')             AS lat,
       LASTWITHTIME(lon, generatedTransformed, 'DOUBLE')             AS lon,
       LASTWITHTIME(routeShortName, generatedTransformed, 'STRING')  AS route,
       LASTWITHTIME(routeId, generatedTransformed, 'INT')            AS routeId,
       LASTWITHTIME(tripId, generatedTransformed, 'INT')             AS tripId,
       LASTWITHTIME(headsign, generatedTransformed, 'STRING')        AS headsign,
       LASTWITHTIME(delay, generatedTransformed, 'INT')              AS delay,
       LASTWITHTIME(speed, generatedTransformed, 'FLOAT')            AS speed,
       LASTWITHTIME(direction, generatedTransformed, 'INT')          AS direction,
       MAX(generatedTransformed)                                     AS lastSeen
FROM gdansk_public_transport_REALTIME
WHERE generatedTransformed > ago('PT10M')
GROUP BY vehicleId
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

_delays_cache = TTLCache(DELAYS_TTL_S)
_heatmap_cache = TTLCache(HEATMAP_TTL_S)
_stats_cache = TTLCache(STATS_TTL_S)


class PinotQueryError(Exception):
    """Raised when Pinot's response body carries a query-execution error."""
    def __init__(self, exceptions):
        super().__init__(str(exceptions))
        self.exceptions = exceptions


def _query(sql, timeout=15):
    """Run a SQL query against the broker; return rows as a list of dicts."""
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
    return [dict(zip(cols, r)) for r in result['resultTable']['rows']]


def get_positions():
    return _query(POSITIONS_SQL, timeout=15)


def live_delays():
    """(route short name, course no as str) -> current delay in seconds."""
    cached = _delays_cache.get()
    if cached is not None:
        return cached
    try:
        rows = _query(DELAYS_SQL, timeout=10)
        data = {(str(r['routeShortName']), str(r['tripId'])): r['delay'] for r in rows}
    except Exception as e:  # degrade to schedule-only rather than fail
        print(f'delays: fetch failed: {e}')
        return _delays_cache.get_stale() or {}
    _delays_cache.set(data)
    return data


def heatmap_cells():
    cached = _heatmap_cache.get()
    if cached is not None:
        return cached
    data = _query(HEATMAP_SQL, timeout=30)
    _heatmap_cache.set(data)
    return data


def table_stats():
    cached = _stats_cache.get()
    if cached is not None:
        return cached
    docs = _query(STATS_SQL, timeout=15)[0]['totalDocs']
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
    _stats_cache.set(data)
    return data
