"""Concurrency load test against the Pinot broker.

Fires N concurrent copies of a representative analytical query (the P3 /
"Route punctuality" dashboard chart's shape) and reports both client-observed
wall-clock latency and Pinot's own reported timeUsedMs, at p50/p95/p99/max —
the concurrency half of the "low-latency, high-QPS" showcase claim (see
PINOT_SHOWCASE_GOALS.md item 4).

Watch cluster-setup/grafana's "Table Query Latency" panel
(pinot_broker_queryExecution_{50,75,95,99,999}thPercentile, already
provisioned) while this runs for the server-side view of the same thing —
no separate Grafana panel needed, it's already there.

Usage: python cluster-setup/scripts/load_test_broker.py [--concurrency 100] [--rounds 5] [--broker http://localhost:8099]
"""
import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

# Same shape as P3 (cluster-setup/superset/dashboards/dashboard/charts/
# P3_Route_Punctuality.yaml) / query 2 in gdansk_public_transport_queries.sql:
# GROUP BY route with three CASE-based percentage metrics, scoped to the last
# 24h so latency reflects concurrency, not the "whole history, no time
# filter" problem tracked separately in PINOT_IMPROVEMENT_PLAN.md item 1.
QUERY = """
SELECT routeShortName,
       ROUNDDECIMAL(SUM(CASE WHEN delay BETWEEN -60 AND 60 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS pct_on_time,
       ROUNDDECIMAL(SUM(CASE WHEN delay > 60 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS pct_late,
       ROUNDDECIMAL(SUM(CASE WHEN delay < -60 THEN 1 ELSE 0 END) * 100.0 / COUNT(*), 1) AS pct_early
FROM gdansk_public_transport
WHERE generatedTransformed > ago('P1D')
  AND NOT (speed = 0 AND delay > 1200)
GROUP BY routeShortName
LIMIT 100
"""


def run_one(broker_url):
    req = urllib.request.Request(
        broker_url + '/query/sql',
        data=json.dumps({'sql': QUERY}).encode('utf-8'),
        headers={'Content-Type': 'application/json'},
    )
    start = time.monotonic()
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = json.load(resp)
    wall_ms = (time.monotonic() - start) * 1000
    if body.get('exceptions'):
        raise RuntimeError(body['exceptions'])
    return wall_ms, body.get('timeUsedMs', 0)


def percentile(values, pct):
    if not values:
        return 0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(len(ordered) * pct / 100))
    return ordered[idx]


def run_round(broker_url, concurrency):
    wall_times, server_times, errors = [], [], 0
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = [pool.submit(run_one, broker_url) for _ in range(concurrency)]
        for f in as_completed(futures):
            try:
                wall_ms, server_ms = f.result()
                wall_times.append(wall_ms)
                server_times.append(server_ms)
            except Exception as e:
                errors += 1
                print(f'  query failed: {e}')
    return wall_times, server_times, errors


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--concurrency', type=int, default=100,
                         help='concurrent queries per round (default: 100)')
    parser.add_argument('--rounds', type=int, default=5,
                         help='number of bursts, so the load is visible on a '
                              'live Grafana panel rather than a single instant (default: 5)')
    parser.add_argument('--pause', type=float, default=1.0,
                         help='seconds between rounds (default: 1.0)')
    parser.add_argument('--broker', default='http://localhost:8099',
                         help='Pinot broker base URL (default: http://localhost:8099)')
    args = parser.parse_args()

    all_wall, all_server, total_errors = [], [], 0
    started = time.monotonic()
    for r in range(args.rounds):
        print(f'round {r + 1}/{args.rounds}: firing {args.concurrency} concurrent queries...')
        wall_times, server_times, errors = run_round(args.broker, args.concurrency)
        all_wall += wall_times
        all_server += server_times
        total_errors += errors
        if r < args.rounds - 1:
            time.sleep(args.pause)
    elapsed = time.monotonic() - started

    total = args.concurrency * args.rounds
    print(f'\n{total} queries ({args.rounds} rounds x {args.concurrency} concurrent) '
          f'against {args.broker} in {elapsed:.1f}s ({total / elapsed:.1f} qps), '
          f'{total_errors} errors\n')
    print(f'{"":>6}{"client wall-clock":>20}{"Pinot timeUsedMs":>20}')
    for label, pct in [('p50', 50), ('p95', 95), ('p99', 99), ('max', 100)]:
        print(f'{label:>6}{percentile(all_wall, pct):>17.0f} ms'
              f'{percentile(all_server, pct):>17.0f} ms')


if __name__ == '__main__':
    main()
