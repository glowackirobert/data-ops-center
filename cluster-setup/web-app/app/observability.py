"""Read-only Loki (logs) and Prometheus (metrics) queries for the MCP
server's operating tools — see AI_PLATFORM_PLAN.md Track 1. Same
urlopen-and-normalize shape as app/pinot.py's _read_json, kept separate
since these are a different pair of unauthenticated internal services.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from app.config import LOKI_URL, PROMETHEUS_URL


class ObservabilityUnavailableError(Exception):
    """Loki or Prometheus never answered — same shape as
    pinot.PinotUnavailableError."""


_DURATION_RE = re.compile(r'^(\d+)([smhd])$')
_DURATION_SECONDS = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}


def _duration_seconds(text):
    m = _DURATION_RE.match(text)
    if not m:
        raise ValueError(f"invalid duration {text!r}, expected e.g. '30m', '1h', '2d'")
    n, unit = m.groups()
    return int(n) * _DURATION_SECONDS[unit]


def _read_json(url, timeout, what):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return json.load(resp)
    except TimeoutError as e:
        raise ObservabilityUnavailableError(
            f'{what} did not respond within {timeout}s') from e
    except urllib.error.URLError as e:
        raise ObservabilityUnavailableError(f'{what} unreachable: {e.reason}') from e


def _escape_logql_string(pattern):
    return pattern.replace('\\', '\\\\').replace('"', '\\"')


def get_logs(container, since='1h', pattern=None):
    """Recent log lines for one container, matching the same `container`
    label Grafana's own logs_dashboard.json filters on — the same LogQL
    Grafana runs, minus the browser. Newest-last, capped at 200 lines.
    """
    query = f'{{container="{container}"}}'
    if pattern:
        query += f' |= "{_escape_logql_string(pattern)}"'
    end_ns = time.time_ns()
    start_ns = end_ns - _duration_seconds(since) * 1_000_000_000
    params = {'query': query, 'start': str(start_ns), 'end': str(end_ns), 'limit': '200'}
    url = LOKI_URL + '/loki/api/v1/query_range?' + urllib.parse.urlencode(params)
    result = _read_json(url, 10, 'loki')
    lines = [
        {'timestamp': ts, 'labels': stream.get('stream', {}), 'line': line}
        for stream in result.get('data', {}).get('result', [])
        for ts, line in stream.get('values', [])
    ]
    lines.sort(key=lambda entry: entry['timestamp'])
    return lines[-200:]


def get_metric(promql, range_='1h'):
    """A PromQL range query over the last `range_` — broker latency, server
    heap, Kafka consumer lag, or anything else the JMX exporters publish
    (see cluster-setup/jmx_exporter/).
    """
    seconds = _duration_seconds(range_)
    end = time.time()
    start = end - seconds
    step = max(15, seconds // 120)  # ~120 points, never finer than the scrape interval
    params = {'query': promql, 'start': str(start), 'end': str(end), 'step': str(step)}
    url = PROMETHEUS_URL + '/api/v1/query_range?' + urllib.parse.urlencode(params)
    result = _read_json(url, 10, 'prometheus')
    if result.get('status') != 'success':
        raise ValueError(result.get('error') or 'prometheus query failed')
    return result.get('data', {}).get('result', [])
