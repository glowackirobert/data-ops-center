"""MCP server exposing the cluster's read-only tools — AI_PLATFORM_PLAN.md
Track 1: one tool surface, many clients. Second entrypoint of the web-app
image; reuses app/agent.py's guard and app/pinot.py's query functions
verbatim rather than duplicating them, so a fix to the guard fixes both
this and /api/ask at once.

No tool here catches PinotQueryError/PinotUnavailableError/ValueError
itself, unlike agent.py's ask() (which translates them to ToolError for the
Anthropic tool_runner). That's deliberate, not an oversight: the low-level
MCP server FastMCP sits on already wraps every @mcp.tool() call and turns an
uncaught exception into a clean isError tool result on its own, so a second
translation layer here would just restate the same message.

    python mcp_server.py                      # stdio (Claude Code / Desktop)
    MCP_TRANSPORT=http python mcp_server.py   # streamable-HTTP (data-ops-mcp
                                               # compose service)
"""
import datetime
import json

from mcp.server.fastmcp import FastMCP

from app import agent, ingestion, observability, pinot
from app.config import MCP_PORT, MCP_TRANSPORT

mcp = FastMCP('data-ops-center', host='0.0.0.0', port=MCP_PORT)

# Same reasoning as agent.py's _MAX_ROWS_TO_MODEL: the guard's own LIMIT caps
# a query at up to 5,000 rows, but every row here also lands in whichever
# LLM is driving this MCP client — capped separately so a broad SELECT
# doesn't spend that client's context on rows nobody asked to see printed.
_MAX_ROWS_RETURNED = 50


@mcp.tool()
def run_pinot_sql(sql: str) -> str:
    """Run a single read-only SELECT against gdansk_public_transport (history)
    or gdansk_public_transport_latest (one current row per vehicle) and
    return its rows plus scan stats. A LIMIT is added automatically if
    omitted, and capped if set too high. Only the first 50 rows are
    returned inline; rowCount reports how many the query actually matched.
    """
    guarded = agent._guard_select(sql)
    rows, ms, stats = pinot._query(guarded, timeout=agent._QUERY_TIMEOUT_S)
    return json.dumps({
        'sql': guarded,
        'rows': rows[:_MAX_ROWS_RETURNED],
        'rowCount': len(rows),
        'timeUsedMs': ms,
        'stats': stats,
    })


@mcp.tool()
def explain_sql(sql: str) -> str:
    """Run EXPLAIN PLAN FOR on a candidate SELECT, before spending any rows
    on it. The filter operator names the index it will use —
    FILTER_STARTREE_INDEX, FILTER_SORTED_INDEX, FILTER_INVERTED_INDEX, or
    FILTER_FULL_SCAN.
    """
    guarded = agent._guard_select(sql)
    rows, _ms, _stats = pinot._query(
        f'EXPLAIN PLAN FOR {guarded}', timeout=agent._EXPLAIN_TIMEOUT_S)
    return json.dumps(rows)


@mcp.tool()
def find_stops(query: str) -> str:
    """Resolve a place or stop name (e.g. "Wrzeszcz") to candidate GTFS
    stops — id, coordinates, a precomputed bounding box, and the lines
    serving each one, the anchor a nearby-vehicle query needs to stay fast.
    """
    return json.dumps(agent._find_stops(query))


@mcp.tool()
def get_schema(table: str) -> str:
    """Columns, types, sorted column, inverted/range index columns and
    star-tree dimensions for gdansk_public_transport or
    gdansk_public_transport_latest.
    """
    return json.dumps(pinot.get_schema(table))


@mcp.tool()
def cluster_health() -> str:
    """Controller reachability, deep-store size and segment counts/status
    (including whether the realtime table is currently consuming) for both
    known tables.
    """
    return json.dumps(pinot.cluster_health())


@mcp.tool()
def ingestion_gaps(day: str) -> str:
    """Minutes missing from the S3 raw/ prefix for one day (YYYY-MM-DD) —
    the offline table's data-quality signal, as contiguous missing-minute
    ranges rather than a 1,440-entry list.
    """
    return json.dumps(ingestion.ingestion_gaps(datetime.date.fromisoformat(day)))


@mcp.tool()
def get_logs(container: str, since: str = '1h', pattern: str | None = None) -> str:
    """Recent Loki log lines for one container (e.g. "pinot-broker",
    "kafka"), optionally filtered by a literal line-match pattern. `since`
    is a duration like '30m', '1h', '2d'.
    """
    return json.dumps(observability.get_logs(container, since, pattern))


@mcp.tool()
def get_metric(promql: str, range: str = '1h') -> str:
    """Run a PromQL range query against Prometheus (broker latency, server
    heap, Kafka consumer lag, …) over the last `range` (e.g. '30m', '1h').
    """
    return json.dumps(observability.get_metric(promql, range))


if __name__ == '__main__':
    mcp.run(transport='streamable-http' if MCP_TRANSPORT == 'http' else 'stdio')
