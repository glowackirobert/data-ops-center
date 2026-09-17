# AI on the Data Operations Center — Plan

Two things: a text-to-SQL agent behind `/api/ask` — ask a question in English,
get index-aware Pinot SQL and its result, with the SQL shown before it runs —
and the tracks that grow the same tool surface into something agents can
*operate*, not just query. The sibling **explain panel** (Pinot's own scan
statistics, no LLM) has already shipped — the `X-Pinot-Stats` header and the
click-to-open latency-badge popover are live, documented in `CLAUDE.md`.

The organising idea: **one tool surface, many clients.** The same handful of
tools — run SQL, explain SQL, find stops, read logs, read metrics — serve the
map's chat box, Claude Code sessions in this repo, Claude Desktop for a
business user, and a scheduled agent that writes the morning brief. Build the
tools once as an MCP server (Track 1); every track after that is a client.

## Where an LLM fits here, and where it does not

Nothing in either pipeline is free text. GPS rows are numeric and coded
(`routeShortName`, `delay`, `speed`, `lat`/`lon`), GTFS is a schedule, and
`Trade` is Avro with seven typed fields. So the usual stream-side uses —
classify, extract, enrich each message — have nothing to work on, and a vector
index in Pinot would have nothing to embed. The LLM's job here is at the
edges:

1. **Question → index-aware query.** The measured gap between an `IN`
   predicate (172 ms, 5,279 rows) and a `REGEXP_LIKE` (1,784 ms, 4.6M rows)
   on the same answer — see "The measurement this is built on" below — is
   the whole reason a schema-and-rules-grounded agent beats a generic
   text-to-SQL one.
2. **Statistics → narrative.** Scan figures, Prometheus series, Loki error
   clusters and per-route delay baselines are all produced deterministically;
   the model explains them. It narrates anomalies, it does not detect them.
3. **Operating the cluster.** A developer in Claude Code, or a scheduled
   agent, checking segment state, consumer lag, ingestion gaps and logs
   through the same tools instead of curl-and-squint.

## The measurement this is built on

Two queries returning the identical answer, run against the live cluster:

| Query shape                                | Time    | Rows scanned           |
|--------------------------------------------|---------|------------------------|
| `WHERE routeShortName IN ('12','8')`       | 172 ms  | 5,279 / 60,417,459     |
| `WHERE REGEXP_LIKE(routeShortName, ...)`   | 1784 ms | 4,579,761 / 60,417,459 |

10x the latency, 868x the rows, same result. The first hits the star-tree index
on `(dayBucket, routeShortName)`; the second cannot and falls back to a scan.
The rules are already written down in `cluster-setup/superset/README.md`.

That table is the whole thesis. An agent that knows those rules writes the first
query; a generic text-to-SQL agent writes the second. Showing both the SQL *and*
the rows-scanned figure (on screen for every query, via the explain panel) is
what makes the difference visible instead of a claim.

## The `/api/ask` agent

### Architecture

```
Browser (chat widget)
   │  POST /api/ask  { question }
   ▼
server.py  (new do_POST, streams text/event-stream)
   │
   ▼
app/agent.py
   │  @beta_tool + client.beta.messages.tool_runner(...)
   ▼
Claude API (claude-opus-5, output_config: {effort: "low"})
   │  tools: run_pinot_sql, explain_sql, find_stops
   ▼
app/agent.py guard  ──▶ app/pinot.py _query()
                    └──▶ app/gtfs.py get_stops()   (already in memory)
```

Module boundary follows `CLAUDE.md`: `server.py` stays thin routing, everything
else in a new `app/agent.py`.

### Model and cost

`claude-opus-5` with `output_config: {effort: "low"}` is the default, not
Haiku. Two reasons beyond the general "measure before downgrading": the
~2–3k-token system prompt (schema, table semantics, index rules) is meant to
be cached, and the minimum cacheable prefix is **4096 tokens on Haiku 4.5
versus 512 on Opus 5** — on Haiku the prompt would silently never cache. And
Opus at low effort makes fewer, better-consolidated tool calls, which is the
latency that matters when each call is a Pinot query. Keep the model an env
var (`ANTHROPIC_MODEL`) so the eval in Track 4 can decide; never hard-code a
downgrade. Thinking stays on (adaptive is the default on Opus 5).

Per-question cost at Opus 5, low effort: ~6–8k input tokens (system prompt
cached after the first call, tool schemas, two or three tool round trips) and
~500 output.

|                     | Opus 5     | Haiku 4.5                            |
|---------------------|------------|---------------------------------------|
| uncached input, 8k  | $0.04      | $0.008                               |
| same, prefix cached | ~$0.01     | ~$0.008 (prompt too short to cache)  |
| output, 500 tokens  | $0.0125    | $0.0025                              |
| **per question**    | **~2–5 ¢** | **~1 ¢**                             |

Rate limiting is non-optional before this is public: an in-memory per-IP token
bucket in `app/agent.py` (stdlib, no dependency), 429 past the limit. It is
spoofable and resets on restart — adequate for a demo, not for real traffic,
and worth saying so in the code comment. Caddy can also cap `/api/ask` at the
edge now that it exists.

### The system prompt is the feature

Four things go in it, and the fourth is what makes this worth building:

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
   filter on them without also constraining `dayBucket`, and prefer
   constraining `routeShortName` to the stop's serving lines too. Measured
   cost of getting this wrong is under "Three tools" below — it is the
   difference between 128 ms and a query that times out.

Instruct the model to state, in one line, which index shape it targeted —
reinforced by `explain_sql` (below), which lets it check that rather than
just assert it. That line next to the explain panel's rows-scanned figure is
the demo.

Keep the prompt in a module-level constant in `app/agent.py`, not a separate
file — it is code, it changes with the schema, and it should show up in a diff
when the schema does.

### Structured response

Return `{answer, sql, rationale, index_shape}` via structured outputs
(`client.messages.parse` with a Pydantic model, or `output_config.format`
with a JSON schema) instead of asking the model to print SQL in a fenced
block and parsing it back. `strict: true` on all three tools so
`run_pinot_sql` never receives a malformed input. The HTTP response to the
frontend adds the tool-call results the model doesn't itself generate:
`{answer, sql, rationale, index_shape, rows, stats}`, so the frontend can
render the SQL and the explain figures next to the answer.

### Three tools

`run_pinot_sql(sql, rationale)`, `find_stops(query)`, and `explain_sql(sql)`.
An earlier draft had five; the other two duplicated panels the UI already
shows.

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

| Query                                          | Time     | Rows scanned           | Segments |
|-------------------------------------------------|----------|-------------------------|----------|
| bbox alone                                     | 2519 ms  | 2,357,904 / 61,149,998  | 32 / 34  |
| bbox + `routeShortName IN (...)` + `dayBucket` | 128 ms   | 8,441                   | 4 / 34   |

20x the latency, 279x the rows. And the unanchored version is worse than the
number suggests: at the **default** broker timeout it does not return at all —
it comes back as `1 servers not responded`, the 504 path in `app/pinot.py`.
A location question asked naively takes the demo down.

This is why `find_stops` returns `routes`. A stop's serving lines are exactly
the anchor the query needs, and `routeShortName` is both the sorted column and a
star-tree dimension. **The tool hands the model its own optimization** — the
system prompt only has to tell it to use it.

The resulting prompt rule:

> Never filter on `lat`/`lon` without also constraining `dayBucket`, and prefer
> constraining `routeShortName` to the stop's `routes` as well. They are
> unindexed metric columns; an unanchored geographic filter scans millions of
> rows and will time out.

That rule plus the explain panel is the demo in one screen: ask a location
question, watch it come back in 128 ms having touched 4 segments of 34, with the
agent's one-line rationale naming the anchor it chose.

#### `explain_sql` — closing the loop the rules only describe

Pinot's `EXPLAIN PLAN FOR <sql>` returns the operator tree, and the filter
operator names the index it will use (`FILTER_STARTREE_INDEX`,
`FILTER_SORTED_INDEX`, `FILTER_INVERTED_INDEX`, or `FILTER_FULL_SCAN`).
Instruct the model to explain before it runs and to rewrite on a full scan:
the agent *sees* it missed the star-tree, at zero rows scanned, instead of
the system prompt's rules being the only thing standing between it and a bad
query. Verify the exact output shape on 1.5.1 first — the tree changed
between the v1 and multi-stage engines.

### The guard — what the actual risk is

Be precise here, because the obvious framing is wrong. This is **not** a
data-exfiltration risk: the broker's `/query/sql` endpoint is read-only (Pinot
takes no DML there), there are two tables, and every row is public transit data
already on the map. The real risks are **resource exhaustion and API cost**: a
generated cartesian join or an unbounded high-cardinality `GROUP BY` can pin the
broker, and the same S3-backfill contention that already produces
`PinotUnavailableError` makes that worse.

So the guard is a cost control, and it lives *inside* the `@beta_tool`
function, before `_query`, not in a wrapper around the tool-runner loop:

- Single statement — reject anything with a `;` other than a trailing one.
- Must parse as a `SELECT`. Use `sqlglot` (pure Python, no C extension) and
  check the AST root, rather than regex-blocklisting keywords, which is the
  approach that always loses.
- Reject any table not in the known set of two.
- Force a `LIMIT` if the model omitted one; cap it.
- Pass an explicit `timeoutMs` to `_query` — it already plumbs one into Pinot's
  `queryOptions`, which is what makes a rejected query come back as a clean 502
  rather than a socket timeout.
- On rejection, return an `is_error` tool result with the reason so the model
  can retry once, then give up — the tool runner's own retry path, with no
  loop code of our own to own. Do not silently rewrite its SQL — the displayed
  SQL must be the SQL that ran.

### Streaming

The tool runner yields one message per turn, and a stdlib
`BaseHTTPRequestHandler` can write `text/event-stream` chunks. Stream three
events: `sql` (before it runs), `rows`, `answer`. The SQL appearing first,
then the scan figures, then the sentence, *is* the demo.

### Backend wiring

- `Dockerfile.web-app`: `pip install --no-cache-dir anthropic sqlglot`. This is
  the first time the web-app is not stdlib-only; call it out in `CLAUDE.md`
  rather than letting it be discovered.
- New secret `anthropic_api_key`, same pattern as `superset_mapbox_api_key`:
  gitignored file, `secrets:` block, `web-app` service list, `ANTHROPIC_KEY_FILE`
  in `app/config.py`. Client constructed once at startup, not per request.
- `server.py` gains `do_POST` — it only dispatches GET today — with the same
  try/except and `send_error_response` shape as `do_GET`.

### Testing

Follow the existing convention — `tests/` mocks everything, no network:

- `tests/test_agent.py`: the guard in isolation (multi-statement, non-SELECT,
  unknown table, missing LIMIT, all rejected with the right reason), and
  `/api/ask` end to end with a mocked Anthropic client.
- `find_stops` against the existing GTFS test fixtures: name match, no match,
  and the bbox maths - assert the longitude span is visibly narrower than the
  latitude span at Gdansk's latitude, since a bbox that silently used equal
  degree offsets would still look plausible in a result.

The guard tests matter more than the agent tests — that is the part where a bug
costs something.

### Step-by-step

1. `Dockerfile.web-app` — add `anthropic` and `sqlglot`.
2. Secret + compose wiring + `app/config.py`.
3. `app/agent.py` — prompt, `run_pinot_sql`, `explain_sql`, `find_stops`
   (reusing `geo.py`'s latitude scaling for the bbox), guard, token bucket,
   `@beta_tool`/`tool_runner` wiring, structured-output parsing.
4. `server.py` — `do_POST`, `_serve_ask`, `text/event-stream` streaming.
5. `static/js/chat.js` + `styles.css` + `index.html` wire-up (SSE client).
6. `tests/test_agent.py`.
7. Rebuild, recreate, and add a `/api/ask` rate limit to the Caddyfile.

## Track 1 — MCP server: one tool surface, three clients

The centrepiece. A Model Context Protocol server exposing the cluster's
read-only tools, built as a second entrypoint of the web-app image so it reuses
`app/pinot.py`, `app/gtfs.py` and the SQL guard verbatim instead of copying
them:

```
cluster-setup/web-app/mcp_server.py       # FastMCP, beside server.py
cluster-setup/web-app/app/agent.py        # guard + tool bodies (shared with /api/ask)
container-compose.yml: data-ops-mcp       # same image, command: python mcp_server.py
```

Tools, all read-only, all returning compact JSON:

| Tool                                  | Backed by                                                               | Why an agent needs it                                                              |
|----------------------------------------|---------------------------------------------------------------------------|---------------------------------------------------------------------------------------|
| `run_pinot_sql(sql)`                  | broker `/query/sql` through the guard                                   | the query itself, with the scan stats attached                                     |
| `explain_sql(sql)`                    | `EXPLAIN PLAN FOR`                                                      | index shape before spending rows                                                   |
| `get_schema()`                        | controller `/schemas`, `/tables/{t}`                                    | columns, types, sorted column, star-tree dims — so the prompt need not inline them |
| `find_stops(query)`                   | `gtfs.get_stops()`                                                      | the bridge from place names to `routes` + bbox                                     |
| `cluster_health()`                    | controller `/health`, `/segments/{t}` status, consuming-segment offsets | is realtime consuming, are segments ONLINE, how many, deep-store size              |
| `ingestion_gaps(day)`                 | S3 `raw/YYYY/MM/DD/` listing                                            | minutes the Lambda missed — the offline table's data quality                       |
| `get_logs(container, since, pattern)` | Loki `/loki/api/v1/query_range`                                         | the same LogQL Grafana runs, minus the browser                                     |
| `get_metric(promql, range)`           | Prometheus `/api/v1/query_range`                                        | broker latency, server heap, Kafka consumer lag                                    |

`run_pinot_sql`, `explain_sql` and `find_stops` are the same three the
`/api/ask` agent uses; the rest extend the surface from *querying* the
cluster to *operating* it. Loki and Prometheus are already on the compose
network and already unauthenticated inside it; the MCP server is just the
first thing to read them programmatically.

Three clients, in the order they pay off:

1. **Claude Code in this repo** — a `.mcp.json` pointing at the server
   (stdio against tunnelled ports in dev, HTTP on the compose network on
   EC2). The reviewer agents in `.claude/agents/` today read files;
   `pinot-config-reviewer` could ask the live cluster whether a star-tree it
   is reviewing is *used* by the chart that motivated it, and
   `schema-pipeline-checker` could `SELECT COUNT(*) WHERE col IS NULL` to
   confirm the silent-null it suspects. Cheapest client: one JSON file.
2. **The web-app's `/api/ask`** — the agent above consumes the same tools
   through `anthropic[mcp]`'s `mcp_tool` helpers over the compose network. No
   public exposure needed. (The API's server-side MCP connector —
   `mcp_servers` + `mcp_toolset`, beta `mcp-client-2025-11-20` — would need
   the server reachable from Anthropic; keep that for Track 3.)
3. **Claude Desktop / claude.ai** for the audience `BUSINESS_OVERVIEW.md`
   is written for: the same tools as a BI chat, no dashboard-building
   required. Needs the public endpoint below.

Public exposure, only when a client needs it: `mcp.{$BASE_DOMAIN}` in the
Caddyfile, streamable-HTTP transport, bearer token from a new
`mcp_bearer_token` secret (same pattern as `pinot_basic_auth_hash`), and the
existing per-IP rate limit from the agent applied here too. The tools are
read-only and the data is public transit already on the map; the risk is the
same as `/api/ask` — broker load and API spend — and the guard is the same
code.

## Track 2 — Natural-language map filter (no SQL, no guard)

"Show me the late 6s near Wrzeszcz." One `client.messages.parse` call into

```python
class MapFilter(BaseModel):
    routes: list[str]          # [] = all
    min_delay_s: int | None
    place: str | None          # resolved with find_stops -> camera fit
    in_service_only: bool
    heatmap: bool
```

and the frontend applies it to the `/api/positions` rows it already holds —
the route dropdown, delay colouring, selection and camera-fit plumbing in
`map.js`/`state.js` all exist. Nothing reaches Pinot, so it works during an
S3 backfill when `/api/ask` would be answering 504s. Cheapest track after the
`.mcp.json`; a good first thing to ship because it is pure structured output
and cannot produce a bad query.

The experimental garnish: Chrome's Web Speech API
(`webkitSpeechRecognition`, feature-detected, ~20 lines) feeding the same
box. A spoken filter on a wall-mounted operations map is the demo the
"operations floor" line in `BUSINESS_OVERVIEW.md` is asking for.

## Track 3 — The morning brief (scheduled agent)

Every morning at 06:00 Europe/Warsaw, an agent with the Track 1 tools writes
a short "yesterday on the network" and "pipeline health" note:

- worst five routes by average delay against their own 30-day baseline —
  the baseline is a *tool* (`route_delay_baseline`, one star-tree query),
  the model only ranks and phrases;
- hours where distinct active vehicles fell outside the baseline band;
- ingestion: minutes missing from `raw/` (`ingestion_gaps`), realtime
  consumer lag, segment count delta, deep-store size delta;
- errors: Loki `level=error` grouped by container, top three messages;
- one paragraph, every number followed by the tool call that produced it.

Two ways to run it; both share the tools, so the choice is deployment only:

- **(a) Managed Agents scheduled deployment** — the experimental path. Agent
  config as version-controlled YAML applied once with `ant beta:agents
  create`, a deployment with `schedule: {type: cron, expression: "0 6 * * *",
  timezone: "Europe/Warsaw"}`, the MCP server attached via
  `mcp.{$BASE_DOMAIN}` with the bearer token in a vault (`static_bearer`),
  and the kickoff as `user.define_outcome` with a rubric ("every figure is
  traceable to a tool result", "no route named without its baseline",
  "under 250 words") so the grader iterates until it passes. A hard
  `budget` on the deployment caps a runaway morning at a few dollars.
- **(b) GitHub Actions cron** — the pattern `compact-s3.yml` already uses,
  running a ~100-line Python tool-runner script against the public MCP
  endpoint. Self-hosted, no beta, no vault, but you own the loop and the
  retry.

Where the brief goes: a `/api/brief` card at the top of the Analytics tab
(the brief is written once a day, so this is the one endpoint that *should*
cache — store it as a small JSON file in the web-app volume), plus a GitHub
issue per day so the history is searchable. Start with (b) to get the prompt
right cheaply, then move the same prompt to (a).

## Track 4 — Evals: what decides the model, the prompt and the rules

The thing that makes the agent engineering rather than a demo. A checked-in
set, `cluster-setup/web-app/tests/evals/text_to_sql.jsonl`, of ~30 questions
with golden SQL, and three deterministic graders that need no LLM judge:

1. **Result equality** — the rows match the golden query's rows.
2. **Index hit** — `numDocsScanned / totalDocs` under a threshold, taken
   from the same broker stats the explain panel shows.
3. **Plan shape** — `explain_sql` output contains no `FILTER_FULL_SCAN`.

Run by hand against the live cluster with an API key
(`python -m tests.evals.text_to_sql --model ...`), printing one table of
pass rates and median `timeUsedMs` per model. CI stays offline as today. This
is the run that answers "Opus-low or Haiku", "does the geographic rule
actually prevent the 2.5 s bbox scan", and "did adding `explain_sql` change
anything" — with numbers, in the same style as the measurements this plan is
built on.

## Track 5 — Claude Code as part of the repo's workflow

Small, optional, and cheap once Track 1 exists:

- `.mcp.json` checked in (Track 1 client 1) so every session here can query
  the cluster, not just read its config.
- The existing reviewer agents get one line each telling them the MCP tools
  exist and when to use them.
- A `claude-code-action` job on pull requests running the three reviewers —
  useful even solo, since the reviewers' value is in running *every* time.

## Considered and cut

- **Vector index / embeddings.** Nothing to embed. Fuzzy stop matching
  ("Wrzeszcz" vs "Gdańsk Wrzeszcz PKP") is Unicode normalisation plus a
  trigram score inside `find_stops`, not a second vendor's embedding API
  and a Pinot HNSW index over 2,000 stop names.
- **LLM in the Kafka stream.** ~1–2k messages every 10 s with no text field.
  Cost with nothing to classify.
- **LLM anomaly *detection*.** The model cannot see 60M rows and would not
  be reproducible if it could. Statistics in tools, narrative from the model
  (Track 3).
- **Chart or dashboard generation.** Tempting, and a trap — it doubles the
  surface while the interesting part (the SQL and the scan figures) is
  already on screen.
- **Multi-turn chat.** Single-turn until Track 4 says the single turn is
  good. If added: history held in the browser and posted back, cached
  prefix, no server-side session state.
- **Fine-tuning.** No corpus, and the eval will show the prompt is the lever.

## Order, cost, effort

| Step | Piece                              | Depends on                  | Effort   | What it proves                           |
|------|-------------------------------------|-----------------------------|----------|--------------------------------------------|
| 1    | The `/api/ask` agent               | `anthropic_api_key` secret | 2 days   | index-aware text-to-SQL, streamed        |
| 2    | Track 1 server + `.mcp.json`       | 1 (shares `app/agent.py`)  | 1–2 days | the cluster is agent-operable            |
| 3    | Track 2 map filter (+ voice)       | —                           | ½–1 day  | structured output, Pinot-free            |
| 4    | Track 4 evals                      | 1, 2                        | 1 day    | numbers behind every model/prompt choice |
| 5    | Track 3 brief, path (b) then (a)   | 2, `mcp.` exposure          | 1–2 days | scheduled autonomous agent               |
| 6    | Track 5                            | 2                           | hours    | —                                         |

The morning brief is one session a day at perhaps 50–100k tokens with tool
results: under a dollar a day on Opus 5. On a public endpoint the per-IP
bucket plus a daily spend cap (sum `usage` per day, 503 past it) is enough
insurance for a demo; both are stdlib and a dozen lines.

Secrets added across the tracks: `anthropic_api_key` (the agent, see
"Backend wiring"), `mcp_bearer_token` (Track 1 exposure). Image inputs added:
`anthropic`, `sqlglot` in `Dockerfile.web-app` — the first non-stdlib
dependencies in the web-app, to be recorded in `CLAUDE.md` when they land.
