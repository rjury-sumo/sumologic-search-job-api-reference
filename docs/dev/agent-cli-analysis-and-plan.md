# Agent-oriented CLI — analysis and implementation plan

Status: proposal, not yet implemented. Scope agreed with the repo owner
2026-08-31: bring in `typer`/`click` + `duckdb` as CLI-only dependencies,
live as a new top-level module in this repo, v1 = core commands + agent
formats + schema discovery (no caching/query layer yet — see Phasing).

## 1. Goal

Today this repo offers two paths for running a Sumo Logic search: copy
`sumo_search_client.py` into a Python project, or drive Sumo's official
`runSearchJob`/`listPartitions`/`listFers` MCP tools. Neither is a good fit
for an agent that reasons and acts through shell commands (Claude Code and
similar harnesses) — that agent currently has to write throwaway Python
against the client to get a one-off answer, and gets back whatever shape
`sumo_search_client.py` happens to produce (raw API JSON), which was never
optimized for token cost.

This document analyzes actual output-size data pulled from a live sandbox
across three real, differently-shaped datasets, then proposes a third
path: a small, `kubectl`-style CLI — `sumosearch` — that wraps the same
endpoints with (a) a terminal UX a human can use directly and (b) output
shaped for token efficiency and agent discoverability by default.

## 2. Empirical pass 1 — AWS CloudTrail (structured JSON-in-`_raw`)

Method: ran the existing integration-test credentials
(`SUMO_ACCESS_ID`/`SUMO_ACCESS_KEY`, AU endpoint) against real sandbox data
— AWS CloudTrail traffic, the same dataset the integration tests already
depend on. Pulled 20 rows each of an aggregate (`records`) query and a raw
(`messages`) query, then serialized the same 20 rows under several
candidate output shapes and measured size. Token counts are `len(text)//4`
— a standard, adequate-for-comparison approximation (no `tiktoken` in this
environment); treat the numbers as relative, not billing-accurate.

**Aggregate query** — `_sourcecategory=*cloudtrail* | count by
_sourcecategory,_view,recipientaccountid,eventsource,eventname | sort
_count desc | limit 20`, `requires_raw_messages=False`:

| Shape | chars | ~tokens |
| --- | ---: | ---: |
| Raw API item (`{"map": {...}}` per row, as `result.items` already is) | 3,932 | 983 |
| Unwrapped `map` only | 3,752 | 938 |
| **CSV** | **1,683** | **420** |
| Columnar `{fields: [...], rows: [[...], ...]}` | 2,102 | 525 |

**Raw message query** — `_sourcecategory=*cloudtrail* | limit 20`:

| Shape | chars | ~tokens |
| --- | ---: | ---: |
| Raw API item (full `map`, 34 keys/row) | 64,781 | 16,195 |
| Unwrapped `map` only | 64,601 | 16,150 |
| `_raw` field only, newline-joined | 36,485 | 9,121 |
| Trimmed to 4 common fields (`_messagetime`, `_sourcehost`, `_sourcecategory`, `_raw`) | 41,750 | 10,437 |

Field cardinality: 20 raw CloudTrail messages carried **34 distinct
top-level keys** in `map`. `_raw` itself is a full nested-JSON CloudTrail
event, averaging ~1,800 chars/message — that's the actual cost driver for
raw-message shapes, not the transport format.

## 3. Empirical pass 2 — two more real, differently-shaped sources

Pass 1 is a best case: uniform, deeply-nested JSON-in-`_raw`, a large,
consistent partition, exactly the shape most Sumo Logic docs use as their
running example. To stress-test the design, the same methodology was
repeated against two very different real sources in the same sandbox:
`_sourceCategory="otel/mac"` (a traditional space-delimited macOS audit
log, sent via an OpenTelemetry collector) and `_sourcecategory="claude_code"`
(a JSON-formatted application audit log — Claude Code's own usage
telemetry). Both surfaced findings that change the plan.

### 3.1 `otel/mac` — space-delimited text, OTel-collector metadata overhead

Raw sample (`_sourceCategory="otel/mac" | limit 10`):

```text
2026-08-31 12:18:12+12 demo-mac Jamf App Installers[21926]: Mozilla Firefox 154.0.1: Change - Apps with similar bundleIdentifier: [name: Firefox, bundleIdentifier: org.mozilla.firefox]
```

27 keys/row. 10 rows: full JSON 10,778 chars (~2,694 tokens) vs. **`_raw`
alone: 2,135 chars (~533 tokens)** — for this source, **built-in +
OTel-resource metadata is ~80% of the payload**, the inverse of pass 1
where `_raw` dominated. The 27 keys break into two groups: Sumo's own
per-message envelope (`_collector`, `_blockid`, `_messageid`, ...) and a
handful of **OTel resource attributes that are constant across the whole
sample** (`os.type`, `host.group`, `deployment.environment`,
`sumo.datasource`) — every row repeats `"os.type": "darwin"` verbatim.

An aggregate `count by _sourcehost` on this data returns **one row**
(`_sourcehost` = the OTel-collector's own HTTP endpoint, not the Mac
device) — for OTel/HTTP-collector-fronted sources, `_sourceHost`/`_collector`
are proxy identity, not device identity; real identity lives in a
resource-attribute field (`host.name`/`host.group` here) or, as below,
needs a parse pattern. A useful aggregate requires extracting structure
from `_raw` first:

```text
-- example record query: aggregate space-delimited otel/mac by process name
_sourceCategory="otel/mac"
| parse regex "^\S+ \S+ \S+ (?<process>[^\[]+)\[(?<pid>\d+)\]:" nodrop
| count by process | sort _count desc | limit 20
```

Verified against the sandbox — 10 rows back (`softwareupdated: 51859`,
`Jamf App Installers: 29050`, `softwareupdate: 7628`, ...), a clean,
useful, cheap aggregate. The point: **text/delimited logs need a `| parse`
step before aggregation is possible at all** — `discover`/`schema` tooling
can't assume every source is JSON-in-`_raw` the way pass 1 was.

### 3.2 `claude_code` — JSON-in-`_raw`, and an in-query auto-parse trap

Raw sample: 22 keys/row of envelope metadata, `_raw` a single-line JSON
object (`event.name`, `tool_name`, `cost_usd`, `session.id`,
`user.email`, `duration_ms`, ~20 meaningful keys total). 10 rows: full
JSON 16,634 chars (~4,158 tokens), `_raw` alone 8,710 chars (~2,177
tokens) — `_raw` dominates here, same shape as pass 1.

Two attempts to get a useful aggregate surfaced a real gotcha:

- **`| json auto | count by "event.name"`** — fails outright: `| count by`
  can't reference a dotted field name in quotes (`HTTP 400 Unexpected
  token '"'`).
- **`| json auto | fields -_raw | limit 3`** — runs, but each row comes
  back with **~190 keys**, the overwhelming majority empty strings
  (`container`, `exception`, `cluster`, `requestmethod`, `statefulset`,
  `dbclusteridentifier`, ...). This sandbox org has a large global
  FER/auto-parse field catalog, and `| json auto` (or, as found in §3.3,
  even a bare `| fields` on this path) appears to union every field the
  *account* has ever seen anywhere, not just this row's own JSON — a
  sparse-column explosion that's a pure token-cost trap with no
  information gain.

The narrow, working pattern — extract only the fields actually
needed, by name, from `_raw`:

```text
-- example record query: aggregate claude_code JSON logs by tool name
_sourcecategory="claude_code"
| json field=_raw "tool_name" as tool_name nodrop
| where !isNull(tool_name)
| count by tool_name | sort _count desc | limit 20
```

Verified against the sandbox — 9 rows back (`Bash: 585`, `Read: 284`,
`Edit: 220`, `Write: 72`, `TodoWrite: 34`, ...), clean and cheap. **Named
`| json field=_raw "x" as x` extraction, not `| json auto`, is the safe
default pattern** for JSON-in-`_raw` sources once specific field names are
known (from a schema-discovery pass, §5.2) — `json auto`/broad `| fields`
risk the sparse-explosion trap whenever the account has a large field
catalog, which a caller has no way to predict in advance.

### 3.3 The `| fields` gotcha — it doesn't reliably trim raw messages

`ai-agent-result-shaping` lever 4 ("trim fields with `| fields`") was
tested directly against `claude_code`, comparing three strategies on the
*same* 20 underlying raw events:

| Strategy | Query | chars | ~tokens |
| --- | --- | ---: | ---: |
| **A. Raw, untrimmed** | `_sourcecategory="claude_code" \| limit 20` | 32,941 | 8,235 |
| **B. Raw, `json field=...` extract + `\| fields` trim** | same, `+ 5× json field=_raw "x" as x nodrop + \| fields event_name,tool_name,hook_event,decision,tool_source` | 32,389 | 8,097 |
| **C. Aggregate, `count by` same 5 fields** | same extract `+ \| count by event_name,tool_name,hook_event,decision,tool_source` | 2,565 | 641 |

**B barely reduced anything (8,097 vs 8,235 — under 2%).** Inspecting
B's actual output explains why: `| fields x,y,z` on the raw-message
(`Messages`) path did **not** drop `_raw` or any of the ~15 built-in
envelope columns (`_blockid`, `_collectorid`, `_messagetime`, ...) — every
row still carried the full envelope *plus* the 5 requested fields tacked
on. A follow-up probe made this worse, not better: `| fields -_raw`
(explicit exclusion) actually **inflated** row width to the same ~190-key
sparse set from §3.2, and `| fields tool_name` (selecting a name that only
exists nested inside `_raw`, not as a resolved top-level field) failed
outright (`Field tool_name not found`).

**C (aggregation) is the only strategy that actually worked — a ~12.8x
reduction over A/B**, consistent with pass 1's records-vs-messages finding
(§2) but now demonstrating that field-trimming is not a substitute for it,
at least not via server-side `| fields` on the raw-message path in this
org. This reprioritizes a design decision from the original draft of this
document (§5.3): **client-side field projection is the CLI's primary
mechanism for shrinking raw-message output, not a fallback safety net** —
`| fields` cannot be relied on there. For the records/aggregate path,
server-side `| fields`/`count by` column selection remains reliable (as
shown by C, and by pass 1) and should stay the primary lever.

## 4. Findings → design implications

1. **Records vs. messages is still the dominant lever, and now
   quantified twice** — 17–39x (pass 1, §2) and ~12.8x (pass 2, §3.3) on
   two unrelated datasets. The CLI should make the aggregate path the path
   of least resistance (short flag, clear help text, and — §5.3 — a
   stderr nudge when a raw-message call looks like it should have been an
   aggregate), not just document the recommendation.
2. **CSV beats raw JSON by ~2.3x for tabular (records) data** (§2).
   Columnar JSON is *worse* than CSV despite deduplicating keys — JSON's
   per-cell overhead on short values outweighs the key-name savings.
   **CSV is the default format for `records` output.**
3. **`| fields` is unreliable for trimming raw-message output** (§3.3) —
   it can no-op (leave the full envelope intact) or actively backfire
   (trigger a sparse global-field union, §3.2). The CLI must do
   **client-side projection** as the default trimming mechanism for the
   `messages` path; it cannot assume a query-side `| fields` clause did
   the job. This also means the CLI's own default `messages` output
   should *not* pass every field through — pick a small fixed envelope
   subset (`_messagetime`, `_sourcecategory`, `_sourcehost`, `_raw`) plus
   whatever `--fields` adds, entirely client-side.
4. **`| json auto` / `autoParsingMode=AutoParse` is not a safe default
   for schema discovery** (§3.2) — in an org with a large FER/field
   catalog it can explode row width to ~190 mostly-empty columns with no
   information gain. `sumosearch schema` (§5.2) must sample `_raw` and
   parse it **client-side in Python** (`json.loads`, when it looks like
   JSON) rather than relying on in-query auto-parse, specifically to
   sidestep this trap. This overrides the original draft's proposal of an
   `--auto-parsing autoparse` opt-in mode — that mode is now a documented
   footgun, not a feature to expose without a strong warning.
5. **Metadata overhead can dominate over `_raw` itself** (§3.1) — for a
   compact text log via an OTel collector, ~80% of raw-message bytes were
   constant/near-constant envelope and resource-attribute fields, not
   content. `sumosearch schema`/`sample` should flag **fields with a
   single distinct value across the sample** (a cheap thing to compute
   while already unioning field names) so an agent can see "these N
   fields are constant across this sample" and drop them without a second
   round-trip, instead of only listing fields that exist.
6. **`_sourceHost`/`_collector` can be degenerate identity for
   OTel/HTTP-collector-fronted sources** (§3.1) — a source-type detail
   `discovery-profile-scope/SKILL.md`'s "enumerate other metadata
   dimensions" step doesn't currently call out. Worth a doc note there
   (§8, Phase 4) independent of the CLI itself.
7. **Not every source is JSON-in-`_raw`** (§3.1) — `schema` must handle
   the space-delimited/syslog-like case too: detect "`_raw` doesn't parse
   as JSON" and fall back to a lightweight structural hint (token count
   consistency, a leading-timestamp guess) rather than reporting "no
   fields," and should suggest trying a `| parse regex` rather than
   `| json`.

## 5. Positioning: three paths

| Capability | `sumo_search_client.py` | `sumosearch` CLI | Sumo MCP (`runSearchJob`, `listPartitions`, `listFers`) |
| --- | --- | --- | --- |
| Transport | Python library, embed in your own code | Subprocess, invoked via shell/bash tool | In-chat tool call, no subprocess |
| Best for | Building a service/pipeline on top of the API | An agent (or human) driving ad hoc searches from a terminal/bash tool | An agent inside a harness with native MCP tool access |
| Output shaping | None — raw API JSON, bring your own | Built-in: csv/json/ndjson/table, agent-optimized defaults, **client-side field trimming that actually works on the messages path (§3.3)** | Whatever Sumo's MCP server returns — out of this repo's control |
| Schema/field discovery | Manual (`list_extraction_rules()` + hand-write a raw sample) | First-class `sumosearch schema` command (§5.2), sidesteps the `json auto` trap (§3.2) | Not exposed |
| Scan estimate / pre-flight cost check | `estimate_scan()` | `sumosearch estimate` | Not in the three listed tools |
| Large-result caching/local filtering | None (explicitly out of scope — see README "What this client intentionally leaves out") | Phase 2 (§7) — local cache + `duckdb` query | Not applicable (no local state) |
| Dependency footprint | `requests` only | `typer` + `duckdb`, isolated to an opt-in dependency group | None (no install) |

The CLI doesn't replace either existing path — it's the missing third leg
for the specific case of an agent (or human) that wants to run a search or
answer a discovery question *from a shell*, without embedding the client
in a program or relying on MCP tool availability. The **query-authoring
skills in `skills/` apply unchanged across all three** — this document
only concerns the transport/output layer.

## 6. CLI design

### 6.1 Command surface

`kubectl`-style noun/verb grouping. All commands accept
`SUMO_ACCESS_ID`/`SUMO_ACCESS_KEY`/`SUMO_ENDPOINT` from the environment
(matching the client and integration tests today); `--access-id`
etc. flags exist but env vars are the documented default so credentials
never appear in shell history.

```text
sumosearch search run    <query> --from --to [--format] [--fields] [--limit]
                                            [--aggregate|--raw] [--auto-parsing manual|autoparse]
                                            [--max-tokens N] [--drop-null-columns]
sumosearch search estimate <query> --from --to        # estimate_scan()
sumosearch search count    <query> --from --to        # estimate_count(): one scalar, no format needed

sumosearch discover partitions [--grep KEYWORD] [--format]
sumosearch discover fers       [--grep KEYWORD] [--format]
sumosearch discover views      [--grep KEYWORD] [--format]

sumosearch schema <query> --from --to [--n 50] [--auto-parsing manual|autoparse]
sumosearch sample <query> --from --to [--n 20] [--drop-null-columns]

sumosearch export <query> --from --to --format csv|json|ndjson --out <file>
    # bulk export straight to disk; uses time_split_search() under the hood
    # when the row count would exceed the 100k raw-message cap. Never
    # printed to stdout — this is the "human wants a file" path, kept
    # separate from the token-budget-conscious agent paths above.
```

`search run` mirrors `run_search()` — the common case, one call,
create→poll→fetch→delete — and is the only job-lifecycle command in v1,
following Sumo MCP's own move to a single atomic `runSearchJob` tool
over a per-endpoint surface (§10). The client's manual-control mode
(`create_search_job()`/`get_search_job_status()`/etc.) is not exposed as
separate `start`/`status`/`fetch`/`delete` subcommands for now — parked
as a future enhancement for long-running jobs, where polling status
between other work and reading the histogram-bucket data job-status
responses expose would let an agent stop a job early to manage its own
token budget (§10).

### 6.2 `sumosearch schema` — operationalizing `discovery-profile-scope`, safely

Directly implements the "profile the schema with a raw sample" and
"identify index-time fields" sections of
`skills/discovery-profile-scope/SKILL.md`, today hand-executed by an agent
that writes its own sample query and eyeballs the JSON — and, per §4
finding 4, deliberately avoids the `| json auto` trap that hand-written
sampling would be tempted to reach for.

Behavior: run `<query> | limit N` (default N=50, matching the skill's
"keep sample sizes small" guidance) with `autoParsingMode=Manual` (never
`AutoParse` by default — §4 finding 4). For each returned message, parse
`_raw` as JSON **client-side**, in Python, if it looks like JSON;
otherwise flag it as unstructured (§4 finding 7) and skip field-union for
that row. Union every key (top-level `map` keys, plus parsed-`_raw` keys
when JSON) across the sample and emit one compact table row per field:

```text
FIELD               PRESENT   TYPE       CONST   INDEX-TIME   EXAMPLE
_raw                 50/50    string     no      no           {"eventVersion":"1.11","userIdentity"...
eventname             50/50    string     no      yes          AssumeRole
sourceipaddress       41/50    string     no      yes          54.153.242.94
os.type               50/50    string     YES     yes          darwin
errorcode              6/50    string     no      yes          AccessDenied
...
```

- `CONST` flags fields with exactly one distinct value across the sample
  (§4 finding 5) — the OTel resource-attribute case from §3.1, where
  these can be most of a compact log's byte count.
- `INDEX-TIME` cross-references `list_extraction_rules()` (already in the
  client) — a field present when `autoParsingMode=Manual` is index-time
  (FER-extracted, collector/source-tagged, or HTTPS/OTLP header-derived);
  a field that only appears after JSON-parsing `_raw` is search-time.
- `EXAMPLE` truncates at ~80 chars.
- Output for a 34-field schema like the CloudTrail sample (§2) is on the
  order of 40–50 lines / a few hundred tokens — two to three orders of
  magnitude smaller than pulling the same sample as raw messages.
- For a non-JSON `_raw` (§3.1's `otel/mac` case), `schema` reports the
  envelope fields normally, marks `_raw` as `unstructured-text`, and adds
  one hint line (token-count-consistency-based) suggesting a `| parse
  regex` starting point rather than silently having nothing to say about
  message content.

### 6.3 Output formats and agent-safety defaults

- `--format csv` (default for `records`/aggregate results, §4 finding 2).
- `--format ndjson` (default for `messages`/raw results — one JSON object
  per line, streamable, no CSV-escaping risk against `_raw` payloads that
  can contain arbitrary characters). **Always built from a client-side
  projected field set** (§4 finding 3), never "whatever the query
  returned" — a small fixed envelope subset (`_messagetime`,
  `_sourcecategory`, `_sourcehost`, `_raw`) plus `--fields` additions.
- `--format json` — single JSON array/object, for scripting.
- `--format table` — aligned, human-readable, for interactive terminal
  use (like `kubectl get`).
- `--fields a,b,c` — for `records` output, passed through as a rendering
  hint (server-side `| fields`/`count by` already controls the column set
  reliably there, §3.3). For `messages` output, this is the **primary**
  trimming mechanism, not a safety net (§4 finding 3) — the CLI does the
  projection itself after fetch, since the query-side equivalent can't be
  trusted to have worked.
- **Stderr token-budget warning, not a hard limit.** Every command that
  writes a result to stdout estimates output size (`len//4`) and, past a
  configurable threshold (default ~4,000 tokens, tunable — §10), prints
  one line to **stderr**: `warning: response is ~9,100 tokens — consider
  an aggregate query, a tighter | limit, or | fields`. Stderr, not
  stdout, so it never pollutes the payload an agent parses, but it's
  visible in a Claude Code-style transcript. `--no-warn` suppresses it
  for scripted/human use.
- **`--max-tokens N`** — a hard client-side cap, independent of the
  warning above (§10): truncates `messages`/raw output to whatever whole
  rows fit under N tokens (never mid-record) and reports how many rows
  were dropped. Gives an agent a lever to bound response size up front
  instead of only reacting to the stderr warning after the full result
  already came back.
- **`--drop-null-columns`** (table/csv/records output, §10) — drop any
  column that is null/empty across every row of the *current* result
  before rendering. Complementary to client-side field projection (§4
  finding 3): projection picks fields by name up front; this drops
  columns that turned out to be uniformly empty for this particular
  query/time range — relevant because org field catalogs commonly run
  to hundreds of sparsely-populated fields (§4 finding 4, §10).

### 6.4 Config resolution

`SUMO_ACCESS_ID` / `SUMO_ACCESS_KEY` / `SUMO_ENDPOINT` env vars, matching
`sumo_search_client.py` and the integration tests exactly — no new config
file format, no separate credential store. (The integration test file
already flags that a *different*, internal `sumo` CLI has its own
`~/.sumo/.env`-based config; this tool must not be confused with that one
— see naming note below.)

## 7. Phase 2 (not v1): local cache + client-side query

Deferred per scope decision, documented here so the v1 design doesn't
foreclose it. For exports too large to usefully return to an agent's
context at all (the 100k-row raw-message cap, or any large `export`
pull):

- Write the fetched rows to a local cache file
  (`.sumosearch_cache/<query-hash>.parquet` via `duckdb`'s native
  Parquet writer — no separate DB server, no schema migration step).
- Return a **manifest**, not the data: row count, column list, cache id,
  file path, and a 5-row preview — the same "small enough for an agent"
  shape as `schema`/`sample`.
- `sumosearch cache query <cache-id> "<SQL>"` — run SQL against the cached
  file via `duckdb.sql(f"SELECT ... FROM read_parquet('{path}')")`, so an
  agent can aggregate/filter/sample a large already-fetched result
  *without* re-running an expensive Sumo Logic scan and without ever
  pulling the full result back into its own context. This is the
  client-side answer to "for larger result sets, could content be cached
  and queryable/filterable to minimize token usage" — yes, and `duckdb`
  is a good fit specifically because it queries Parquet/CSV/JSON files
  directly with zero server setup.
- `sumosearch cache ls / show / export / rm / gc --older-than` for
  lifecycle management — a cache with no TTL/cleanup path is a silent disk
  leak.

This phase is deliberately not v1: it introduces real state (files that
outlive a single command invocation, a cache-key scheme, staleness
questions) that the core command surface doesn't need to answer to be
useful. Ship §6 first, revisit this once real usage shows how often
results are actually too large for a single `schema`/`sample`/aggregate
call to have already avoided the problem.

## 8. Architecture / file layout

New top-level `cli/` package, not folded into `sumo_search_client.py` —
keeps the client's single-file/single-dependency distribution model
(`AGENTS.md`) completely untouched. The CLI *imports* the client rather
than reimplementing any request/retry/pagination logic.

```text
cli/
  __init__.py
  main.py       # typer app; command registration; env-based config resolution
  formats.py    # csv/json/ndjson/table renderers + the stderr token-estimate warning
                # + client-side field projection for the messages path (§4 finding 3)
  schema.py     # sumosearch schema — sampling, client-side JSON parse of _raw,
                # field union, CONST detection, FER cross-reference
  cache.py      # Phase 2 only — duckdb-backed cache read/write/query
pyproject.toml:
  [dependency-groups]
  cli = ["typer>=0.12", "duckdb>=1.0"]     # opt-in group; `uv sync --group dev` alone
                                            # never pulls these in
  [project.scripts]
  sumosearch = "cli.main:app"
tests/
  test_cli.py                # unit tests; fake the client's `session=` the same way
                              # tests/test_sumo_search_client.py already does
  integration_test_cli.py    # optional live smoke test; same non-CI treatment as
                              # tests/integration_test_sumo_search_client.py
```

**Naming:** `sumosearch`, confirmed (§10) as the binary name —
deliberately *not* `sumo`, since the integration test file already
documents a separate, unrelated internal `sumo` CLI with its own
`~/.sumo/.env` config; reusing that name would confuse the two and
violate this repo's "must stand alone, no ties to any internal CLI"
invariant (`AGENTS.md`).

## 9. Phased implementation plan

### Phase 0 — scaffolding

- `cli/` package skeleton, `typer` app, env-based config resolution, `[project.scripts]` entry point, opt-in `cli` dependency group.
- `sumosearch search run` only, `--format json` only. Prove the wiring end-to-end (real call against sandbox) before building out formats.

### Phase 1 — core commands + formats

- `search run/estimate/count` — single atomic job command per §10 (no separate `start`/`status`/`fetch`/`delete` in v1).
- `discover partitions/fers/views` with `--grep`.
- csv/ndjson/table formats, client-side field projection for `messages` (§6.3), the stderr token-budget warning, `--max-tokens`, and `--drop-null-columns` (§10).
- Unit tests mirroring `tests/test_sumo_search_client.py`'s fake-session pattern; `ruff check` clean.

### Phase 2 — schema & sample

- `sumosearch schema` (§6.2) — client-side JSON parsing of `_raw`, `CONST` detection, non-JSON fallback hint.
- `sumosearch sample`.
- Cross-reference against `list_extraction_rules()` for the index-time-field column.

### Phase 3 — export

- `sumosearch export` — file-output path, using `time_split_search()` for anything that would hit the raw-message cap.

### Phase 4 — docs & skill sync

- README "three paths" section update.
- `skills/discovery-profile-scope/SKILL.md` gets two notes: (a) `sumosearch schema` now automates this workflow, (b) the `_sourceHost`/`_collector` degenerate-identity caveat for OTel/HTTP-collector-fronted sources (§4 finding 6) — the skill content itself doesn't otherwise change, it still needs to teach the *why* for the MCP-tool/manual path.
- `skills/ai-agent-result-shaping/SKILL.md` gets a caveat on lever 4 (`| fields`): reliable for aggregate/records output, not reliably effective for raw-message output (§3.3) — point at client-side projection (this CLI, or equivalent in a caller's own code) for that path instead. Also note (§10) that the sparse-explosion behavior generalizes with an account's FER/field-catalog size, not just this sandbox org.
- `AGENTS.md` gets a `cli/` bullet under "Layout & invariants".
- `CHANGELOG.md` entry.

### Deferred

- Phase 2-of-this-doc (§7, local cache + `duckdb` query) — revisit after Phase 4 ships and real usage shows whether it's needed.
- Async job management — `search start`/`status`/`fetch`/`delete` as separate subcommands, plus surfacing histogram-bucket data from job-status responses (§10) — v1 ships a single atomic `search run` only, mirroring Sumo MCP's `runSearchJob`. Revisit for long-running (>10min) jobs where an agent needs to poll completion state and stop a job early to manage its own token budget.

## 10. Decisions

Open questions from the original draft, resolved 2026-08-31:

1. **Binary name: confirmed `sumosearch`** (§8). No further discussion needed before implementation.
2. **Async job ergonomics: deferred — v1 ships `search run` only.** Sumo's own MCP tooling moved from per-endpoint tools to a single atomic `runSearchJob` after finding a per-endpoint surface overly complex; this CLI follows that precedent for simplicity. `search start`/`status`/`fetch`/`delete` are cut from v1 (§6.1, §9) and parked as a future enhancement for long-running jobs, where an agent polls completion state and reads the histogram-bucket data job-status responses expose, and can stop a job early to stay within a token budget.
3. **Token-warning threshold ships as designed (~4,000, tunable), plus a client-side truncation control.** Alongside the stderr warning, v1 adds `--max-tokens N` (§6.3): a hard client-side cap that truncates `messages` output to whole rows only (never mid-record), so an agent can bound response size directly instead of only reacting to a warning after the full result already came back.
4. **The `| fields`/`json auto` sparse-explosion (§3.2, §3.3) is general Sumo Logic behavior, not sandbox-specific.** Every org defines its own set of fields (via FERs or admin config) usable as HTTPS POST fields, and large orgs commonly have 500+ of them — any in-query mechanism that unions the account's full field catalog will produce a wide, mostly-empty result for any single row, regardless of org. Phase 4 docs (§9) should state this as a general property that scales with account field-catalog size, not a one-org artifact. v1 also adds `--drop-null-columns` (§6.3) — drop columns that are null/empty across every row of the current result — as a second, complementary lever alongside client-side field projection (§4 finding 3).
