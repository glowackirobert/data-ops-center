# AI on the Data Operations Center — Plan

A text-to-SQL agent behind `/api/ask` — ask a question in English, get
index-aware Pinot SQL and its result, with the SQL shown before it runs — has
already shipped, alongside the sibling **explain panel** (Pinot's own scan
statistics, no LLM): the `X-Pinot-Stats` header and the click-to-open
latency-badge popover. So has Track 1's MCP server — the same tool surface,
plus schema/health/ingestion/log/metric tools, with Claude Code wired as its
first client via `.mcp.json`. So has Track 2's natural-language map filter —
the **Filter** button beside **Ask**, Pinot-free, so it keeps working when
`/api/ask` is answering 504s. So has Track 4's eval harness — a checked-in
question set and three deterministic graders, run by hand against the live
cluster, not in CI. All five are live, documented in `CLAUDE.md`,
`README-docker.md` (endpoints, MCP service, eval-harness command) and
`README-ec2.md` (the `anthropic_api_key` secret). What remains is Tracks 3
and 5 below, plus Track 1's optional public exposure (for a Claude Desktop /
claude.ai client — nothing needs it yet).

The organising idea: **one tool surface, many clients.** The same handful of
tools — run SQL, explain SQL, find stops, read logs, read metrics — serve the
map's chat box, Claude Code sessions in this repo, Claude Desktop for a
business user, and a scheduled agent that writes the morning brief. The tools
are built once as an MCP server (Track 1, done); every track after that is a
client.

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

## Track 1 — MCP server: one tool surface, three clients

Shipped: `cluster-setup/web-app/mcp_server.py` (FastMCP, a second entrypoint
of the web-app image), exposing 8 read-only tools —
`run_pinot_sql`/`explain_sql`/`find_stops` reuse `app/agent.py`'s guard and
`app/pinot.py`'s `_query()` verbatim; `get_schema`/`cluster_health` are new
`pinot.py` functions (controller `/schemas`, `/tables`, `/segments`,
`/health`, `/tables/{t}/externalview` — the last of these unverified against
a live 1.5.1 controller, see `CLAUDE.md`); `ingestion_gaps` is
`app/ingestion.py` (paginated S3 listing, boto3, merged into contiguous
missing-minute ranges); `get_logs`/`get_metric` are `app/observability.py`
(Loki/Prometheus `query_range`, both already unauthenticated on
`pinot-network`). Runs `stdio` or `MCP_TRANSPORT=http` (the `data-ops-mcp`
compose service). Full detail in `CLAUDE.md`'s Web App section.

Of the three clients, only the first is wired:

1. **Claude Code in this repo** — done, via the `.mcp.json` checked in at
   the repo root (Track 5's first bullet, pulled forward). The reviewer
   agents in `.claude/agents/` still only read files; giving
   `pinot-config-reviewer` a line telling it the MCP tools exist (so it can
   ask the live cluster whether a star-tree it's reviewing is *used*) is the
   rest of Track 5, not yet done.
2. **The web-app's `/api/ask`** — not done, and may not be worth doing:
   `agent.py` already calls `_guard_select()`/`pinot._query()` directly,
   in-process, which is simpler than round-tripping through MCP over the
   compose network for no behavior change. Revisit only if a second
   in-process consumer of the same tools shows up.
3. **Claude Desktop / claude.ai** for the audience `BUSINESS_OVERVIEW.md` is
   written for — not done; needs the public exposure below.

Public exposure, only when a client needs it (not done, nothing needs it
yet): `mcp.{$BASE_DOMAIN}` in the Caddyfile, streamable-HTTP transport,
bearer token from a new `mcp_bearer_token` secret (same pattern as
`pinot_basic_auth_hash`), and the existing per-IP rate limit from the agent
applied here too. The tools are read-only and the data is public transit
already on the map; the risk is the same as `/api/ask` — broker load and API
spend — and the guard is the same code.

## Track 2 — Natural-language map filter (no SQL, no guard)

Shipped: "show me late 6s near Wrzeszcz" — the **Filter** button beside
**Ask** — turns into a `MapFilter` (`routes`, `min_delay_s`, `place`,
`in_service_only`, `heatmap`) via one `client.messages.parse` call in
`app/agent.py`'s `parse_map_filter()`, no tools, no guard, since nothing it
produces is SQL. The frontend (`map.js`'s `applyMapFilter`, wired through
`mapfilter.js`) applies it to `/api/positions` rows already in the browser —
a named route/heatmap drives the existing dropdown/checkbox and their
existing onchange handlers (camera fit, histogram load, all reused
verbatim); delay/in-service live in `state.nlFilter`, an additive filter
`currentRows()` layers on top; a named place gets `map.fitBounds()`'d via
the same `find_stops` bbox `/api/ask` uses. Nothing reaches Pinot, so it
keeps working when `/api/ask` is answering 504s — verified in-browser by
submitting a filter while `/api/positions` itself was 504ing throughout.
Only ever *adds* to the dropdown/checkbox, never resets them: a filter that
doesn't name a route or ask for the heatmap leaves whatever was already
picked alone. Full detail in `CLAUDE.md`'s Web App section.

Not done: the experimental voice-input garnish (Chrome's Web Speech API
feeding the same box) was skipped — optional, and the text box already
delivers the track's actual value.

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

Shipped: `cluster-setup/web-app/tests/evals/text_to_sql.jsonl`, 14 questions
with golden SQL (current-state, star-tree daily aggregates, whole-history
sorted-column, and two geographic cases — smaller than the ~30 first
sketched here, in favor of every case being grounded in a query shape
already proven elsewhere in this codebase rather than padded to a round
number; growing it is one JSONL line), and three deterministic graders in
`tests/evals/text_to_sql.py` that need no LLM judge:

1. **Result equality** — the agent's own SQL, executed, matched against the
   golden query's rows. Row/column-order- and alias-independent (the
   agent's SELECT list won't match the golden query's), which trades a
   small accepted risk of a coincidental cross-column collision for not
   having to predict the agent's exact column names — the standard
   execution-accuracy tradeoff. Numeric values are rounded to the nearest
   whole unit before comparing, so a `ROUNDDECIMAL` precision mismatch
   doesn't fail a case the answer got right. Group-by (multi-row) cases opt
   out via `"check_result": false` for the same column-shape-prediction
   reason; geographic cases opt out because their correct answer depends on
   `find_stops`'s live bbox, which isn't known at authoring time.
2. **Index hit** — `docsScanned / totalDocs` under a per-case
   `max_scan_ratio`, taken from the same broker stats the explain panel
   shows.
3. **Plan shape** — `EXPLAIN PLAN FOR` the agent's own SQL contains no
   `FULL_SCAN` in any column (not assumed to be literally named
   `"Operator"` — that exact shape wasn't checked against a live 1.5.1
   controller, see `CLAUDE.md`).

Run by hand against the live cluster with a real Anthropic API key and
Pinot reachable (`python -m tests.evals.text_to_sql --model claude-opus-5
--date 2026-09-10`, from `cluster-setup/web-app`), printing pass rates and
median `timeUsedMs`. `{date}` in a question/golden SQL is substituted from
`--date` (default: 2 days ago) rather than the agent resolving "yesterday"
itself — `agent.py`'s system prompt carries no notion of "today". CI stays
offline: the pure grading logic (`grade`, `rows_match`, `is_full_scan`,
`load_cases`) is unit-tested in `tests/test_evals_text_to_sql.py` with
everything mocked; `run_case`/`main`, the only parts that call
Pinot/Anthropic, are not run there — same split `mcp_server.py`'s untested-
by-design live dispatch has. This is the run that answers "Opus-low or
Haiku", "does the geographic rule actually prevent the 2.5 s bbox scan",
and "did adding `explain_sql` change anything" — with numbers, in the same
style as the measurements this plan is built on. Not yet actually run
against the live cluster with a real API key while writing this — the
grading logic is verified, the questions/golden SQL are not.

## Track 5 — Claude Code as part of the repo's workflow

Small, optional, and cheap now that Track 1 exists:

- ~~`.mcp.json` checked in (Track 1 client 1) so every session here can query
  the cluster, not just read its config.~~ Done.
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

| Step | Piece                              | Depends on                              | Effort   | What it proves                           |
|------|-------------------------------------|-------------------------------------------|----------|--------------------------------------------|
| 1    | Track 1 server + `.mcp.json`       | already available (`app/agent.py`, `/api/ask`) | **Done** | the cluster is agent-operable      |
| 2    | Track 2 map filter (voice not done) | —                                       | **Done** | structured output, Pinot-free            |
| 3    | Track 4 evals (not yet run live)   | 1                                        | **Done** | numbers behind every model/prompt choice |
| 4    | Track 3 brief, path (b) then (a)   | 1, `mcp.` exposure                       | 1–2 days | scheduled autonomous agent               |
| 5    | Track 5                            | 1                                        | hours (1/3 done) | —                                |

The morning brief is one session a day at perhaps 50–100k tokens with tool
results: under a dollar a day on Opus 5. On a public endpoint the per-IP
bucket plus a daily spend cap (sum `usage` per day, 503 past it) is enough
insurance for a demo; both are stdlib and a dozen lines.

Secrets added across the tracks: `anthropic_api_key` (already added, for
`/api/ask`), `mcp_bearer_token` (Track 1's still-undone public exposure).
`aws_credentials` (already existed, for Pinot's S3 deep store) is now also
mounted on the `data-ops-mcp` service, for `ingestion_gaps`. Image inputs
added: `anthropic`, `sqlglot`, `mcp`, `boto3` in `Dockerfile.web-app` — the
first non-stdlib dependencies in the web-app, recorded in `CLAUDE.md`.
