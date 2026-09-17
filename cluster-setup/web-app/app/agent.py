"""Text-to-SQL agent behind /api/ask — see AI_PLATFORM_PLAN.md.

Guard-then-query pattern: every SQL string the model produces goes through
_guard_select() before it ever reaches pinot._query() — the same guard
whether the call comes from ask() here or (Track 1) the MCP server, since
both will share this module's tool bodies.

Wired into server.py's do_POST (AI_PLATFORM_PLAN.md step 4) — that handler
just calls ask() and hands the result to send_json(); this module stays
usable standalone (e.g. from a REPL or a future MCP server tool body) either
way.
"""
import difflib
import json
import threading
import time
import unicodedata

import sqlglot
from anthropic import Anthropic
from anthropic.lib.tools import ToolError, beta_tool
from pydantic import BaseModel
from sqlglot import exp

from app import geo, gtfs, pinot
from app.config import ANTHROPIC_KEY_FILE, ANTHROPIC_MODEL, read_secret

# Client is built once, on first use rather than at import time — importing
# this module (e.g. from a test) must not require the secret file to exist.
_client_lock = threading.Lock()
_client = None


def _get_client():
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = Anthropic(api_key=read_secret(ANTHROPIC_KEY_FILE))
    return _client


# --- The system prompt is the feature (AI_PLATFORM_PLAN.md) ---------------

SYSTEM_PROMPT = """\
You answer English questions about a live Gdansk public transport GPS feed by \
writing Pinot SQL, running it, and explaining the result. State in one \
sentence which index shape your query targets (star-tree / sorted column / \
range index / none) — that line is shown next to the query's scan stats.

## Tables

`gdansk_public_transport` — history, one row per GPS ping ever received \
(hybrid realtime + offline). Columns:
  vehicleId INT, vehicleCode STRING, vehicleService STRING, routeId INT,
  routeShortName STRING, tripId INT, headsign STRING, direction INT,
  gpsQuality INT, dayBucket STRING (yyyy-MM-dd, aligned to midnight),
  lat DOUBLE, lon DOUBLE, speed FLOAT, delay INT (seconds, +late/-early),
  isOnTime INT, isLate INT (0/1 flags, pre-computed for the star-tree),
  generatedTransformed TIMESTAMP (event time — filter/GROUP BY on this,
  not a wall-clock function),
  scheduledTripStartTimeTransformed TIMESTAMP.

`gdansk_public_transport_latest` — upsert table, primary key vehicleId, \
exactly one row per vehicle: its most recent ping. Same columns as above \
minus dayBucket/isOnTime/isLate. Use this, not the history table, for "where \
is X now" / "current delay" questions — a plain SELECT already returns the \
latest state, no LASTWITHTIME/GROUP BY/MAX needed. Rows older than 10 \
minutes are effectively stale (nothing else drops them).

No other tables exist. No stop-name column exists anywhere — stops live only \
in GTFS, reachable via find_stops, never by guessing a column.

## Index rules

`routeShortName` is the table's sorted column: an `IN (...)` or range \
predicate on it is fast; `REGEXP_LIKE` on it forces a full scan (measured: \
172 ms / 5,279 rows scanned vs. 1784 ms / 4.6M rows scanned for the same \
answer). Prefer `IN`/range predicates over `REGEXP_LIKE` whenever the model \
can name the routes.

Anchor time windows to midnight (`dayBucket = 'YYYY-MM-DD'`, or a range of \
whole days) so they align with the star-tree's first split. The star-tree \
covers `SUM(isOnTime)`, `SUM(isLate)`, `COUNT(*)`, `AVG(speed)` grouped by \
`dayBucket` and `routeShortName` — an aggregate query shaped exactly like \
that, filtered on those two columns, is nearly free. Use explain_sql before \
running an aggregate you're unsure about, and rewrite if it reports a full \
scan instead of the star-tree.

`lat`/`lon` are unindexed metric columns. Never filter on them without also \
constraining `dayBucket`, and prefer constraining `routeShortName` to the \
stop's `routes` too (from find_stops) — measured: a bare bounding-box filter \
scans 2.4M rows in 2.5 s and can time out; anchored with `dayBucket` and \
`routeShortName IN (...)`, the same question scans ~8k rows in 128 ms.

## Tools

- `find_stops(query)` — the only way to resolve a place name to \
  coordinates/lines. Returns a precomputed bounding box per candidate (do \
  not compute one yourself — a degree of longitude here is ~65 m against \
  ~111 m for latitude, and that trigonometry done inline is a common wrong \
  guess) and `routes`, the anchor for any query near that stop.
- `explain_sql(sql)` — runs `EXPLAIN PLAN FOR` your SELECT. Use it before an \
  aggregate or a geographic query you're not sure hits an index, and rewrite \
  on a full scan rather than asserting the index shape blind.
- `run_pinot_sql(sql, rationale)` — runs a single read-only SELECT against \
  the two tables above. `rationale` is the one-line index-shape statement, \
  not a restatement of the question. A rejected query's reason is returned \
  to you — fix it and try once more, then stop.

Finish with a short answer in plain English. Never fabricate a row or a \
number that didn't come from a tool result.\
"""


# --- The guard — a cost control, not a data-exfiltration control ----------
# (see AI_PLATFORM_PLAN.md, "The guard — what the actual risk is": the
# broker's /query/sql is read-only and every row is already public on the
# map. The real risk is a cartesian join or an unbounded GROUP BY pinning
# the broker, so this validates shape and caps LIMIT, nothing more.)

_KNOWN_TABLES = {'gdansk_public_transport', 'gdansk_public_transport_latest'}
_DEFAULT_LIMIT = 2000
_MAX_LIMIT = 5000
_QUERY_TIMEOUT_S = 15
_EXPLAIN_TIMEOUT_S = 10


def _guard_select(sql):
    """Validate a model-generated SQL string; return the SQL to actually run.

    Raises ToolError (caught by the tool runner and handed back to the model
    as an is_error tool result, so it can retry once) on any violation.
    Mutates the text only to add/cap a LIMIT — every other guard check is
    read-only, so the SQL that ran is always the SQL the model asked for,
    the trace, and (once wired in) the frontend all agree on.
    """
    text = sql.strip()
    if text.endswith(';'):
        text = text[:-1].rstrip()
    if ';' in text:
        raise ToolError('only a single statement is allowed (found a ";" before the end)')

    try:
        parsed = sqlglot.parse_one(text)
    except sqlglot.errors.ParseError as e:
        raise ToolError(f'SQL did not parse: {e}') from e
    if not isinstance(parsed, exp.Select):
        raise ToolError('only a SELECT statement is allowed')

    tables = {t.name for t in parsed.find_all(exp.Table)}
    unknown = tables - _KNOWN_TABLES
    if unknown:
        raise ToolError(
            f'unknown table(s): {", ".join(sorted(unknown))} — '
            f'only {", ".join(sorted(_KNOWN_TABLES))} may be queried')

    limit_node = parsed.args.get('limit')
    if limit_node is None:
        text = f'{text} LIMIT {_DEFAULT_LIMIT}'
    else:
        limit_value = limit_node.expression.this
        try:
            over_cap = int(limit_value) > _MAX_LIMIT
        except (TypeError, ValueError):
            over_cap = False  # non-literal LIMIT; Pinot will reject it itself
        if over_cap:
            text = text.replace(f'LIMIT {limit_value}', f'LIMIT {_MAX_LIMIT}', 1)
    return text


def _pinot_error(e):
    """Fold pinot.py's two failure modes into one ToolError message — the
    model doesn't need the 502-vs-504 distinction the HTTP layer cares
    about, just enough to decide whether retrying makes sense."""
    return ToolError(str(e))


# --- Stop lookup ------------------------------------------------------------

_STOP_RADIUS_M = 400
_MAX_STOP_MATCHES = 5


def _normalize(s):
    """Lowercase, strip Polish diacritics — 'Wrzeszcz' should match
    'Gdańsk Wrzeszcz PKP' without the model needing to guess the accent."""
    decomposed = unicodedata.normalize('NFKD', s)
    return ''.join(c for c in decomposed if not unicodedata.combining(c)).lower()


def _find_stops(query):
    norm_query = _normalize(query)
    scored = []
    for stop in gtfs.get_stops():
        norm_name = _normalize(stop['name'])
        score = difflib.SequenceMatcher(None, norm_query, norm_name).ratio()
        if norm_query in norm_name:
            score += 0.5  # substring hit beats a merely-similar name
        if score > 0.3:
            scored.append((score, stop))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            'stopId': stop['stopId'],
            'name': stop['name'],
            'lat': stop['lat'],
            'lon': stop['lon'],
            'bbox': geo.bbox_around(stop['lat'], stop['lon'], _STOP_RADIUS_M),
            'routes': stop['routes'],
        }
        for _, stop in scored[:_MAX_STOP_MATCHES]
    ]


# --- Rate limiting ----------------------------------------------------------

class TokenBucket:
    """In-memory per-IP rate limiter — stdlib, no dependency.

    Spoofable (trusts whatever IP the caller passes) and resets on restart:
    adequate for a demo, not for real traffic. Caddy can also cap /api/ask
    at the edge once this is public.
    """

    def __init__(self, capacity=10, refill_per_s=10 / 60):
        self.capacity = capacity
        self.refill_per_s = refill_per_s
        self._buckets = {}  # ip -> (tokens, last_refill_monotonic)
        self._lock = threading.Lock()

    def allow(self, ip):
        now = time.monotonic()
        with self._lock:
            tokens, last = self._buckets.get(ip, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill_per_s)
            if tokens < 1:
                self._buckets[ip] = (tokens, now)
                return False
            self._buckets[ip] = (tokens - 1, now)
            return True


RATE_LIMITER = TokenBucket()


# --- Structured response (AI_PLATFORM_PLAN.md, "Structured response") -----

class AgentAnswer(BaseModel):
    answer: str
    sql: str
    rationale: str
    index_shape: str


# --- The agent --------------------------------------------------------------

_MAX_TOKENS = 4096
_MAX_ROWS_TO_MODEL = 50  # keep the model's own context cheap; the frontend
                         # gets the full (guard-capped) row set separately


def ask(question):
    """Run one question through the agent; return the /api/ask response.

    {answer, sql, rationale, index_shape, rows, stats} — the model's own
    structured output plus the tool-call results it doesn't generate itself.
    `sql`/`rows`/`stats` come from the last successful run_pinot_sql call,
    not from the model's own echo of it, so the SQL shown is always the SQL
    that ran (empty/the model's own text if it never called the tool at all
    — a pure find_stops question, say).
    """
    last_query = {}

    @beta_tool
    def run_pinot_sql(sql: str, rationale: str) -> str:
        """Run a single read-only SELECT against the Pinot broker and return
        its rows plus scan stats. Only gdansk_public_transport (history) and
        gdansk_public_transport_latest (one current row per vehicle) may be
        queried. A LIMIT is added automatically if you omit one, and capped
        if you set one too high — the SQL you write is the SQL that runs
        otherwise. On rejection, fix the SQL and call this again once.

        Args:
            sql: The SELECT statement to run.
            rationale: One sentence: which index shape this targets (star-tree,
                sorted column, range index, or none) and why.
        """
        guarded = _guard_select(sql)
        try:
            rows, ms, stats = pinot._query(guarded, timeout=_QUERY_TIMEOUT_S)
        except (pinot.PinotQueryError, pinot.PinotUnavailableError) as e:
            raise _pinot_error(e) from e
        last_query['sql'] = guarded
        last_query['rows'] = rows
        last_query['stats'] = stats
        last_query['ms'] = ms
        return json.dumps({
            'rows': rows[:_MAX_ROWS_TO_MODEL],
            'row_count': len(rows),
            'timeUsedMs': ms,
            'stats': stats,
        })

    @beta_tool
    def explain_sql(sql: str) -> str:
        """Run EXPLAIN PLAN FOR on a candidate SELECT and return Pinot's
        operator tree, before spending any rows on it. The filter operator
        names the index it will use — FILTER_STARTREE_INDEX,
        FILTER_SORTED_INDEX, FILTER_INVERTED_INDEX, or FILTER_FULL_SCAN.
        Rewrite the query if this reports a full scan instead of the index
        you expected.

        Args:
            sql: The candidate SELECT statement (not run — only explained).
        """
        guarded = _guard_select(sql)
        try:
            rows, _ms, _stats = pinot._query(
                f'EXPLAIN PLAN FOR {guarded}', timeout=_EXPLAIN_TIMEOUT_S)
        except (pinot.PinotQueryError, pinot.PinotUnavailableError) as e:
            raise _pinot_error(e) from e
        return json.dumps(rows)

    @beta_tool
    def find_stops(query: str) -> str:
        """Resolve a place name to candidate GTFS stops. Each result carries
        a precomputed bounding box (do not compute your own — see the
        geographic rule in your instructions) and the lines serving that
        stop, which is the anchor a nearby-vehicle query needs to stay fast.

        Args:
            query: A place or stop name as the user wrote it, e.g. "Wrzeszcz".
        """
        return json.dumps(_find_stops(query))

    client = _get_client()
    runner = client.beta.messages.tool_runner(
        model=ANTHROPIC_MODEL,
        max_tokens=_MAX_TOKENS,
        system=[{'type': 'text', 'text': SYSTEM_PROMPT, 'cache_control': {'type': 'ephemeral'}}],
        output_config={'effort': 'low'},
        output_format=AgentAnswer,
        tools=[run_pinot_sql, explain_sql, find_stops],
        messages=[{'role': 'user', 'content': question}],
    )
    final = runner.until_done()
    answer = final.parsed_output
    if answer is None:
        raise RuntimeError('model did not produce a structured answer')

    return {
        'answer': answer.answer,
        'sql': last_query.get('sql', answer.sql),
        'rationale': answer.rationale,
        'index_shape': answer.index_shape,
        'rows': last_query.get('rows', []),
        'stats': last_query.get('stats'),
        'ms': last_query.get('ms'),
    }
