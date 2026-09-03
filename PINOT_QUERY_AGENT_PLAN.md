# Pinot Query Agent + Explain Panel — Implementation Plan

Two features that reinforce each other and both answer the same question for a
visitor: *what can this database do that Postgres can't?*

1. **Explain panel** — surface what Pinot already tells us about every query and
   we currently discard. No LLM, no new dependency, no cost.
2. **Query agent** — ask a question in English, get index-aware Pinot SQL and
   its result, with the SQL shown before it runs.

Ship them in that order. The explain panel is cheap on its own and is what makes
the agent's output legible when it lands.

This supersedes the journey-planning scope in `WEB_APP_ASSISTANT_PLAN.md`; that
file now records only what was cut and why. The plumbing it specified (SDK
dependency, secret, `/api/ask`, chat widget, rate limiting, test convention) is
sound and is absorbed here.

## The measurement this is built on

Two queries returning the identical answer, run against the live cluster:

| Query shape                                | Time    | Rows scanned         |
|--------------------------------------------|---------|----------------------|
| `WHERE routeShortName IN ('12','8')`       | 172 ms  | 5,279 / 60,417,459   |
| `WHERE REGEXP_LIKE(routeShortName, ...)`   | 1784 ms | 4,579,761 / 60,417,459 |

10x the latency, 868x the rows, same result. The first hits the star-tree index
on `(dayBucket, routeShortName)`; the second cannot and falls back to a scan.
The rules are already written down in `cluster-setup/superset/README.md`.

That table is the whole thesis. An agent that knows those rules writes the first
query; a generic text-to-SQL agent writes the second. Showing both the SQL *and*
the rows-scanned figure is what makes the difference visible instead of a claim.

## Phase 1 — Explain panel

### What Pinot already returns

Every broker response carries far more than the `timeUsedMs` we keep. From a
real response:

```
timeUsedMs = 172              numDocsScanned = 5279
totalDocs = 60417459          numEntriesScannedInFilter = 0
numSegmentsQueried = 33       numEntriesScannedPostFilter = 82308
numSegmentsProcessed = 32     numSegmentsPrunedByValue = 1
numSegmentsMatched = 32       numServersQueried = 2
```

"Scanned 5,279 of 60,417,459 rows across 32 segments" is a better advertisement
for a columnar store than any prose on the page.

### Backend

`app/pinot.py` `_query()` already parses the response and returns
`(rows, timeUsedMs)`. Widen that to carry a small stats dict, and emit it beside
the existing latency header:

- Keep `X-Pinot-Time-Ms` exactly as is — `CLAUDE.md` records that it was made a
  header specifically so no consumer's parsing had to change, and that reasoning
  still holds.
- Add `X-Pinot-Stats`, a compact JSON object with six fields:
  `docsScanned`, `totalDocs`, `segmentsProcessed`, `segmentsQueried`,
  `segmentsPruned`, `serversQueried`. Header values cannot contain newlines, so
  serialize with `json.dumps(..., separators=(',', ':'))`.
- Set it in `app/http.py`'s `send()` alongside the time header, so every
  Pinot-backed endpoint gets it for free — the same reasoning that put the 502
  handling in `do_GET` rather than per route.

### Frontend

The latency badge lives in `utils.js` and has unit tests
(`tests/test_utils.js`) — extend there, not in each panel:

- Badge stays as it is at rest; clicking it expands a small popover with the
  scan figures and a one-line reading of them ("0.009% of the table").
- Highlight the ratio, not the raw numbers — `docsScanned / totalDocs` is the
  number that lands.
- Add cases to `test_utils.js` for the formatter (ratio rounding, missing
  header, `totalDocs` of 0).

No new dependency, no config, no security surface. Half a day of work.

## Phase 2 — Query agent

### Architecture

```
Browser (chat widget)
   │  POST /api/ask  { question }
   ▼
server.py  (new do_POST)
   │
   ▼
app/agent.py
   │  client.beta.messages.tool_runner(...)
   ▼
Claude API — tools: run_pinot_sql, find_stops
   │
   ▼
app/agent.py guard  ──▶ app/pinot.py _query()
                    └──▶ app/gtfs.py get_stops()   (already in memory)
```

Module boundary follows `CLAUDE.md`: `server.py` stays thin routing, everything
else in a new `app/agent.py`.

### The system prompt is the feature

Three things go in it, and the third is what makes this worth building:

1. **The schema.** Both tables, every column, types, and which is the sorted
   column. Small enough (~15 columns) to inline verbatim — no RAG, no schema
   retrieval step.
2. **What the tables mean.** That `gdansk_public_transport` is history and
   `gdansk_public_transport_latest` is one upsert row per vehicle; that
   `generatedTransformed` is the event time. Without this the model will write
   `LASTWITHTIME` GROUP BYs against the history table for current-position
   questions, which is exactly the mistake the upsert table exists to avoid.
3. **The index rules**, lifted from `cluster-setup/superset/README.md`: prefer
   `IN` and range predicates over `REGEXP_LIKE`; anchor time windows to midnight
   so they align with `dayBucket`; the star-tree covers
   `SUM(isOnTime)`, `SUM(isLate)`, `COUNT(*)`, `AVG(speed)` grouped by
   `dayBucket` and `routeShortName`.

4. **The geographic rule.** `lat`/`lon` are unindexed metric columns: never
   filter on them without also constraining `dayBucket`, and prefer constraining
   `routeShortName` to the stop's serving lines too. Measured cost of getting
   this wrong is under "Two tools" below - it is the difference between 128 ms
   and a query that times out.

Instruct the model to state, in one line, which index shape it targeted. That
line next to the explain panel's rows-scanned figure is the demo.

Keep the prompt in a module-level constant in `app/agent.py`, not a separate
file — it is code, it changes with the schema, and it should show up in a diff
when the schema does.

### Two tools

`run_pinot_sql(sql, rationale)` and `find_stops(query)`. The earlier draft had
five; the other three duplicated panels the UI already shows.

#### Why `find_stops` is required, not optional

**There is no stop column in the Pinot table.** The schema carries `vehicleId`,
`routeShortName`, `tripId`, `headsign`, `lat`, `lon`, `delay` and friends — but
stops exist only in the GTFS feed, in memory in `app/gtfs.py`. So a whole class
of natural question ("how bad are delays around Wrzeszcz?", "is line 6 late at
Galeria Bałtycka?") is not merely inconvenient without this tool, it is
*unexpressible*. The model would do one of the two classic text-to-SQL failures:
hallucinate a `stopName` column, or refuse a question the data can actually
answer.

`find_stops` is the bridge. It resolves a place name against
`gtfs.get_stops()` and returns, per candidate:

| Field            | Why the model needs it |
|------------------|------------------------|
| `stopId`, `name` | disambiguation back to the user |
| `lat`, `lon`     | the location itself |
| `bbox`           | a precomputed `latMin/latMax/lonMin/lonMax` for a given radius |
| `routes`         | **the query anchor** — see below |

Return the **bounding box precomputed, not the point**. A degree of longitude at
Gdansk's latitude is ~65 m against ~111 m for latitude, and a model asked to do
that trigonometry inline will get it wrong in a way nothing catches. The math
already exists in `app/geo.py` (`kx = math.cos(math.radians(tlat))`, the
`111320` constant) and the ratio is already documented in `pinot.py`'s
`HEATMAP_SQL` header — reuse it rather than restating it.

#### The trap this tool walks into, measured

`lat` and `lon` are **metric** columns with no index — `rangeIndexColumns` covers
only `delay`, the inverted indexes cover `speed`, `vehicleCode` and
`routeShortName`. So a bounding-box filter is an unindexed scan. Measured on the
live cluster, same question, same answer:

| Query                                          | Time     | Rows scanned         | Segments |
|------------------------------------------------|----------|----------------------|----------|
| bbox alone                                     | 2519 ms  | 2,357,904 / 61,149,998 | 32 / 34 |
| bbox + `routeShortName IN (...)` + `dayBucket` | 128 ms   | 8,441                | 4 / 34   |

20x the latency, 279x the rows. And the unanchored version is worse than the
number suggests: at the **default** broker timeout it does not return at all —
it comes back as `1 servers not responded`, the 504 path in `app/pinot.py`.
A location question asked naively takes the demo down.

This is why `find_stops` returns `routes`. A stop's serving lines are exactly
the anchor the query needs, and `routeShortName` is both the sorted column and a
star-tree dimension. **The tool hands the model its own optimization** — the
system prompt only has to tell it to use it.

#### The prompt rule this creates

> Never filter on `lat`/`lon` without also constraining `dayBucket`, and prefer
> constraining `routeShortName` to the stop's `routes` as well. They are
> unindexed metric columns; an unanchored geographic filter scans millions of
> rows and will time out.

That rule plus the explain panel is the demo in one screen: ask a location
question, watch it come back in 128 ms having touched 4 segments of 34, with the
agent's one-line rationale naming the anchor it chose.

### The guard — what the actual risk is

Be precise here, because the obvious framing is wrong. This is **not** a
data-exfiltration risk: the broker's `/query/sql` endpoint is read-only (Pinot
takes no DML there), there are two tables, and every row is public transit data
already on the map. The real risks are **resource exhaustion and API cost**: a
generated cartesian join or an unbounded high-cardinality `GROUP BY` can pin the
broker, and the same S3-backfill contention that already produces
`PinotUnavailableError` makes that worse.

So the guard is a cost control, and it lives in `app/agent.py` before `_query`:

- Single statement — reject anything with a `;` other than a trailing one.
- Must parse as a `SELECT`. Use `sqlglot` (pure Python, no C extension) and
  check the AST root, rather than regex-blocklisting keywords, which is the
  approach that always loses.
- Reject any table not in the known set of two.
- Force a `LIMIT` if the model omitted one; cap it.
- Pass an explicit `timeoutMs` to `_query` — it already plumbs one into Pinot's
  `queryOptions`, which is what makes a rejected query come back as a clean 502
  rather than a socket timeout.
- On rejection, return the reason to the model so it can retry once, then give
  up. Do not silently rewrite its SQL — the displayed SQL must be the SQL that
  ran.

### Backend wiring

Reused verbatim from the superseded plan, which got these right:

- `Dockerfile.web-app`: `pip install --no-cache-dir anthropic sqlglot`. This is
  the first time the web-app is not stdlib-only; call it out in `CLAUDE.md`
  rather than letting it be discovered.
- New secret `anthropic_api_key`, same pattern as `superset_mapbox_api_key`:
  gitignored file, `secrets:` block, `web-app` service list, `ANTHROPIC_KEY_FILE`
  in `app/config.py`. Client constructed once at startup, not per request.
- `server.py` gains `do_POST` — it only dispatches GET today — with the same
  try/except and `send_error_response` shape as `do_GET`.
- Response shape: `{answer, sql, rationale, rows, stats}` so the frontend can
  render the SQL and the explain figures next to the answer.

### Model and cost

`claude-haiku-4-5` is the right default here, not Opus. This is short,
schema-grounded SQL generation against a 15-column schema, it may sit on a
public IP, and latency is part of the demo — a 700 ms answer sells the app
better than a 4 s one. Make the model an env var so it is a one-line change to
try Opus 5 on a question that stumps it.

Rate limiting is non-optional before this is public: an in-memory per-IP token
bucket in `app/agent.py` (stdlib, no dependency), 429 past the limit. It is
spoofable and resets on restart — adequate for a demo, not for real traffic, and
worth saying so in the code comment. Caddy can also cap `/api/ask` at the edge
now that it exists.

### Testing

Follow the existing convention — `tests/` mocks everything, no network:

- `tests/test_agent.py`: the guard in isolation (multi-statement, non-SELECT,
  unknown table, missing LIMIT, all rejected with the right reason), and
  `/api/ask` end to end with a mocked Anthropic client.
- `find_stops` against the existing GTFS test fixtures: name match, no match,
  and the bbox maths - assert the longitude span is visibly narrower than the
  latitude span at Gdansk's latitude, since a bbox that silently used equal
  degree offsets would still look plausible in a result.
- `tests/test_utils.js`: the explain-badge formatter cases from Phase 1.

The guard tests matter more than the agent tests — that is the part where a bug
costs something.

## Step-by-step

**Phase 1**
1. `app/pinot.py` — widen `_query` to return the stats dict.
2. `app/http.py` — emit `X-Pinot-Stats` in `send()`.
3. `static/js/utils.js` — expand the badge into a click-to-open popover.
4. `tests/test_utils.js` — formatter cases.
5. Rebuild the `web-app` image, recreate the container.

**Phase 2**
6. `Dockerfile.web-app` — add `anthropic` and `sqlglot`.
7. Secret + compose wiring + `app/config.py`.
8. `app/agent.py` — prompt, `run_pinot_sql`, `find_stops` (reusing `geo.py`'s
   latitude scaling for the bbox), guard, token bucket.
9. `server.py` — `do_POST` and `_serve_ask`.
10. `static/js/chat.js` + `styles.css` + `index.html` wire-up.
11. `tests/test_agent.py`.
12. Rebuild, recreate, and add a `/api/ask` rate limit to the Caddyfile.

## Deliberately not in scope

- **Streaming responses.** Needs SSE on the stdlib `BaseHTTPRequestHandler`.
  Worth doing once the feature proves itself; not before.
- **Multi-turn conversation.** Needs session state. Single-turn answers the
  demo question fine.
- **Chart generation from results.** Tempting, and a trap — it doubles the
  surface while the interesting part (the SQL and the scan figures) is already
  on screen.
