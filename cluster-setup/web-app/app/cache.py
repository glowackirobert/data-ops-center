"""A tiny reusable TTL cache for the single-value, lazily-refreshed cache
sites (live delays, heatmap cells, table stats).

Deliberately a class rather than module-level dict+lock globals: relocating
the same pattern into shared globals wouldn't actually fix the testability
problem it's meant to solve, since tests would still share one process-wide
cache instance. Each call site constructs its own `TTLCache`.

`_gtfs` and `_shapes` are not built on this: GTFS is refreshed on a fixed
timer rather than lazily on read, and shapes are keyed per (route, trip) with
whole-cache invalidation at day rollover, not a single TTL'd value — forcing
either into this shape would cost clarity for no real gain.
"""
import threading
import time


class TTLCache:
    def __init__(self, ttl_seconds):
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._at = 0.0
        self._data = None

    def get(self):
        """Return the cached value if still fresh, else None."""
        with self._lock:
            if self._data is not None and time.time() - self._at < self._ttl:
                return self._data
            return None

    def set(self, data):
        with self._lock:
            self._at = time.time()
            self._data = data

    def get_stale(self):
        """Return whatever is cached regardless of freshness (fallback when
        a refresh attempt fails)."""
        with self._lock:
            return self._data
