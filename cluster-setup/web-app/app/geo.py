"""Route-shape fetching and path-geometry helpers.

Pure domain logic (aside from the one HTTP fetch in route_shape) and easy to
test independently: given a path and a target point, truncate_path_at has no
dependency on GTFS state, Pinot, or the request handler — the caller looks up
the trip's last stop and passes it in.
"""
import datetime
import json
import math
import threading
import urllib.error
import urllib.parse

from app.gtfs import GTFS_TZ
from app.http_client import http_get
from app.config import SHAPES_URL

# One GeoJSON LineString per (date, routeId, tripId). Coordinates are plain
# lon/lat degrees (verified 2026-07-13; the docs' EPSG:3857 claim is wrong).
# Shapes are static within a day and a few KB each, so responses are cached in
# memory — including upstream 404s (cached as None): a trip absent from
# today's plan won't appear later.
_lock = threading.Lock()
_shapes = {'date': None, 'data': {}}  # (routeId, tripId) -> path | None


def route_shape(route_id, trip_id):
    """Path [[lon, lat], …] of today's trip, or None if upstream has none."""
    date = datetime.datetime.now(GTFS_TZ).strftime('%Y-%m-%d')
    key = (route_id, trip_id)
    with _lock:
        if _shapes['date'] != date:  # shapes are per-day; drop yesterday's
            _shapes['date'] = date
            _shapes['data'] = {}
        elif key in _shapes['data']:
            return _shapes['data'][key]
    url = SHAPES_URL + '?' + urllib.parse.urlencode(
        {'date': date, 'routeId': route_id, 'tripId': trip_id})
    try:
        path = json.loads(http_get(url, timeout=15))['coordinates']
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        path = None  # vehicle between trips / trip not in today's plan
    with _lock:
        if _shapes['date'] == date:
            _shapes['data'][key] = path
    return path


def truncate_path_at(path, target):
    """Cut [[lon, lat], …] at the point on it nearest `target` (lat, lon).

    Mirrors the client's segment-projection math (equirectangular, fine at
    city scale). Candidates within ~30 m of the best distance are considered
    tied; among those the one furthest along the path wins, since this is
    used to trim a shape down to its scheduled *last* stop, not to track a
    moving vehicle along it.
    """
    tlat, tlon = target
    kx = math.cos(math.radians(tlat))
    ambiguity2 = (30 / 111320) ** 2
    best_i, best_t, best_point, best_d2 = None, None, None, float('inf')
    candidates = []
    for i in range(len(path) - 1):
        ax, ay = path[i]
        bx, by = path[i + 1]
        dx, dy = (bx - ax) * kx, by - ay
        len2 = dx * dx + dy * dy
        ex, ey = (tlon - ax) * kx, tlat - ay
        t = min(1, max(0, (ex * dx + ey * dy) / len2)) if len2 else 0
        px, py = ex - t * dx, ey - t * dy
        d2 = px * px + py * py
        point = [ax + t * (bx - ax), ay + t * (by - ay)]
        candidates.append((i, t, point, d2))
        if d2 < best_d2:
            best_d2 = d2
    for i, t, point, d2 in candidates:
        if d2 <= best_d2 + ambiguity2 and (best_i is None or i + t > best_i + best_t):
            best_i, best_t, best_point = i, t, point
    return path[:best_i + 1] + [best_point]
