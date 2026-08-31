# Sumo Logic Search Job API — Reference & Skills

A standalone, customer-distributable reference for building log search and
analytics on top of Sumo Logic's [Search Job
API](https://help.sumologic.com/docs/api/search-job/): a gold-standard
Python client plus a set of portable Agent Skills for writing good Sumo
Logic queries.

## Who this is for

**Engineers building agentic automation** on top of the Search Job API —
`sumo_search_client.py` is a single-file, single-dependency (`requests`)
Python client that gets the API's sharp edges right on the first pass:
silent truncation at 100,000 raw messages, a 429 rate limit that's easy to
ignore, and a `pendingErrors` field that a naive "did I get 0 results back"
check will miss entirely, silently turning a broken query into a false
"no results" report. Copy it into your own project and adapt it.

**End users, admins, and SIEM analysts** doing log discovery, query
authoring, and search best-practice work — the `skills/` directory is
portable [Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
content covering how to find the right data, scope a query efficiently,
order pipeline operators, and shape results for an AI agent. These skills
teach *how Sumo Logic search works*, not this specific client's API surface
— they're equally useful driving queries through `sumo_search_client.py`,
the `sumosearch` CLI, or Sumo's official `runSearchJob` MCP tool.

**An agent (or human) driving ad hoc searches from a terminal/bash tool** —
the `sumosearch` CLI in `cli/` is a third path, for callers that don't want
to embed the Python client in a program and don't have MCP tool access.
It's a small `kubectl`-style wrapper over the same endpoints, with
token-efficient output shaping (csv/ndjson/json/table, aggregate-friendly
defaults, client-side field trimming for raw messages) built in rather
than left to the caller. See "Quickstart: the `sumosearch` CLI" below and
the full [`cli/README.md`](cli/README.md) reference.

### Positioning: three paths

| Capability | `sumo_search_client.py` | `sumosearch` CLI | Sumo MCP (`runSearchJob`, `listPartitions`, `listFers`) |
| --- | --- | --- | --- |
| Transport | Python library, embed in your own code | Subprocess, invoked via shell/bash tool | In-chat tool call, no subprocess |
| Best for | Building a service/pipeline on top of the API | An agent (or human) driving ad hoc searches from a terminal/bash tool | An agent inside a harness with native MCP tool access |
| Output shaping | None — raw API JSON, bring your own | Built-in: csv/json/ndjson/table, agent-optimized defaults, client-side field trimming that actually works on the raw-message path | Whatever Sumo's MCP server returns — out of this repo's control |
| Schema/field discovery | Manual (`list_extraction_rules()` + hand-write a raw sample) | First-class `sumosearch schema` command | Not exposed |
| Scan estimate / pre-flight cost check | `estimate_scan()` | `sumosearch search estimate` | Not in the three listed tools |
| Dependency footprint | `requests` only | `typer`, isolated to an opt-in `cli` dependency group | None (no install) |

The CLI doesn't replace either existing path — it's the missing third leg
for the specific case of running a search or answering a discovery
question *from a shell*. The query-authoring skills in `skills/` apply
unchanged across all three; only the transport/output layer differs. See
[`docs/dev/agent-cli-analysis-and-plan.md`](docs/dev/agent-cli-analysis-and-plan.md)
for the full design rationale and empirical token-cost measurements behind
these choices.

## Quickstart

```bash
pip install requests
# or: uv add requests
```

```bash
export SUMO_ACCESS_ID="..."
export SUMO_ACCESS_KEY="..."
export SUMO_ENDPOINT="https://api.sumologic.com"   # see region table below
```

```python
import os
from sumo_search_client import SumoSearchClient

client = SumoSearchClient(
    access_id=os.environ["SUMO_ACCESS_ID"],
    access_key=os.environ["SUMO_ACCESS_KEY"],
    endpoint=os.environ.get("SUMO_ENDPOINT", "https://api.sumologic.com"),
)

result = client.run_search(
    query='_sourceCategory=prod/app error',
    from_time="-1h",
    to_time="now",
)

print(f"{result.result_type}: {len(result.items)} of {result.total} total")
for row in result.items:
    print(row["map"]["_raw"])
```

Python 3.10+ (uses `X | None` union syntax and `dataclasses`). Auth is HTTP
Basic — Access ID as username, Access Key as password. Never hardcode
credentials; read them from the environment or a secrets manager.

Region endpoints:

| Region | Endpoint |
| --- | --- |
| US1 | `https://api.sumologic.com` |
| US2 | `https://api.us2.sumologic.com` |
| AU | `https://api.au.sumologic.com` |
| EU | `https://api.eu.sumologic.com` |

## Quickstart: the `sumosearch` CLI

```bash
uv sync --group cli
```

Credentials come from the same environment variables as the Python client
(see [Quickstart](#quickstart) above) — never as CLI flags, so they don't
end up in shell history.

```bash
# Run a search job (create -> poll -> fetch -> delete); ndjson by default
# for raw-message results, csv by default for aggregate/records results.
uv run sumosearch search run '_sourceCategory=prod/app error' --from -1h --to now

# Profile a query's field schema from a small sample without hand-writing one
uv run sumosearch schema '_sourceCategory=prod/app' --from -1h --to now

# Bulk export straight to disk (time-splits automatically past ~80k rows)
uv run sumosearch export '_sourceCategory=prod/app error' --from -24h --to now \
    --format csv --out events.csv
```

Full command list, every flag, output-format defaults, and the token-budget
controls (`--max-tokens`, `--drop-null-columns`, the stderr warning): see
[`cli/README.md`](cli/README.md).

## Contents

| File | Purpose |
| --- | --- |
| `sumo_search_client.py` | The reference client — search job lifecycle plus read-only discovery endpoints (partitions, field extraction rules, scheduled views). Copy this into your project. |
| `cli/` | `sumosearch` — a shell/agent-oriented CLI wrapping the same endpoints, with token-efficient output shaping built in. Install via `uv sync --group cli`, invoke as `sumosearch ...`. Not a copy-paste artifact like the client — it's an installable console-script entry point. See [`cli/README.md`](cli/README.md) for the full command reference. |
| `skills/` | Portable, harness-agnostic Agent Skills: the API-calling best practices the client implements, plus query-authoring skills (scoping, discovery, operator ordering, common patterns, agent-friendly result shaping, scheduled views, indexes/partitions, Cloud SIEM). See [`skills/README.md`](skills/README.md) for the full index and suggested reading order. |
| `tests/` | Unit tests (`test_sumo_search_client.py`, `test_cli.py`, no credentials needed) and a live-credential integration test. |
| `pyproject.toml` | A self-contained [uv](https://docs.astral.sh/uv/) project for developing and testing this client and the CLI — not needed if you're just copying `sumo_search_client.py` into your own project. |

## Skills work with this client, the CLI, or Sumo's `runSearchJob` MCP tool

The `skills/` directory is not tied to `sumo_search_client.py`. Each skill
teaches query-authoring and search-methodology patterns — how to scope a
query to keep scan cost down, how to discover which partition or source
category holds the data you need, how to order pipeline operators, how to
shape results for an LLM caller — that apply the same way regardless of how
the query actually gets executed. Whether you're running queries through
this client, the `sumosearch` CLI, or Sumo's official `runSearchJob` MCP
tool, the skills apply unchanged; only the transport differs.

Start with [`skills/README.md`](skills/README.md) for the full skill index,
what each one covers, and a suggested reading order for a new integration.

## Example — Aggregate query (count by field)

```python
result = client.run_search(
    query='_sourceCategory=prod/app | count by _sourceHost',
    from_time="-6h",
    to_time="now",
    requires_raw_messages=False,   # skip raw-message overhead for aggregates
)

for row in result.items:
    m = row["map"]
    print(m["_sourcehost"], m["_count"])
```

The client auto-detects `records` vs. `messages` from the job's
`recordCount`/`messageCount` — you never need to tell it which one your
query produces.

### Token efficiency: aggregate results vs. raw messages

If a result feeds an LLM/agent caller rather than a human dashboard, the
query shape matters far more than any client-side formatting choice.
Measured against real data (structured JSON, space-delimited text, and a
JSON-in-`_raw` application log — full methodology and numbers in
[`docs/dev/agent-cli-analysis-and-plan.md`](docs/dev/agent-cli-analysis-and-plan.md)):

- The same underlying events, expressed as an aggregate (`records`) query
  vs. raw messages, differed by **13–39x** in output size across the
  datasets tested — collapsing events into a `count by`/`sum()`/`avg()`
  aggregate server-side is the single biggest token-cost lever available,
  well before any formatting choice.
- For `records` output specifically, **CSV came out ~2.3x smaller than
  raw JSON** for the same rows — dropping repeated key names and JSON
  punctuation. Prefer CSV (or an equivalent flat/columnar text format)
  over JSON when the result is aggregate data headed for an LLM context.
- `| fields` does **not** reliably shrink raw-message (`messages`)
  output — on an account with a large field-extraction-rule catalog it
  left the full row envelope intact, and a broader `| fields`/`json auto`
  clause actually inflated it by triggering a global field union across
  every field the account has ever defined. If raw messages are
  unavoidable, trim fields **client-side after fetch**, not via an
  in-query `| fields`/`json auto` clause.

Suggested approach:

1. Default to an aggregate query (`count by`, `sum()`, `avg()`, ...) with
   `requires_raw_messages=False` unless the caller genuinely needs
   individual log lines — see the example above and
   `skills/common-query-patterns/SKILL.md`.
2. Extract only the specific fields actually needed from JSON `_raw`
   payloads by name (`| json field=_raw "x" as x`), not `| json auto` —
   auto-parsing can pull in every field the account has ever defined, not
   just what's in the current row.
3. Cap every aggregate with `| sort ... | limit N` or `| topk(N, ...)` —
   an uncapped `count by` over a high-cardinality field can still return
   thousands of rows.
4. When rendering `records` results for an LLM/agent caller, prefer CSV
   over JSON; for `messages` results, project down to the fields actually
   needed after fetching rather than relying on the query to have already
   done it.

See [`skills/ai-agent-result-shaping/SKILL.md`](skills/ai-agent-result-shaping/SKILL.md)
for the general query-shaping principles this analysis confirms, and
[`docs/dev/agent-cli-analysis-and-plan.md`](docs/dev/agent-cli-analysis-and-plan.md)
for the full per-format token measurements and an agent-oriented CLI
design built on these findings.

### Gotcha: lookup-table reads have no `_raw`

`cat /shared/lookups/<table> | where ...` (and similar lookup-table reads
with no aggregate operator) hit the Messages endpoint like any raw query —
but every row comes back with `_raw` empty and the real data under the
table's own column names instead. Check `result.looks_like_lookup_table`
before assuming `map["_raw"]` is populated.

### Handling errors correctly

```python
from sumo_search_client import SumoSearchJobFailed, SumoSearchTimeout, SumoSearchError

try:
    result = client.run_search(query='_sourceCateogry=typo-in-field-name | count',
                               from_time="-1h", to_time="now")
except SumoSearchJobFailed as exc:
    # Covers BOTH: pendingErrors reported by an invalid query, and jobs that
    # ended in CANCELLED / FORCE PAUSED. This is the exception a naive
    # "if not result.items: print('no results')" check would miss.
    print(f"search failed: {exc}")
except SumoSearchTimeout as exc:
    print(f"search timed out: {exc}")
except SumoSearchError as exc:
    print(f"search request error (HTTP {exc.status_code}): {exc}")
```

### Exporting more than 100,000 raw messages

A single job silently truncates raw-message results at 100,000. For larger
exports, probe the volume first and split the time range into windows sized
to stay under the cap:

```python
from sumo_search_client import estimate_count, time_split_search

query = '_sourceCategory=prod/app error'
from_ms, to_ms = 1_700_000_000_000, 1_700_604_800_000   # ~7 days

total = estimate_count(client, query, from_ms, to_ms)
print(f"~{total} events in range")

all_messages = time_split_search(
    client, query, from_ms, to_ms, interval_hours=6,
)
print(f"fetched {len(all_messages)} messages across all windows")
```

### Pre-flight scan-cost estimate

Before running an expensive or unfamiliar query, check what it will scan
without creating a search job at all:

```python
import time

to_ms = int(time.time() * 1000)
from_ms = to_ms - 3_600_000   # last 1h; estimate_scan() needs epoch ms, not "-1h"

estimate = client.estimate_scan(
    query='_index=prod_logs _sourceCategory=prod/app error',
    from_ms=from_ms, to_ms=to_ms,
)
print(f"{estimate.total_bytes / 1e9:.2f} GB across {len(estimate.partitions)} partition(s)")
```

### Discovery endpoints (partitions, field extraction rules, scheduled views)

Three synchronous, no-job-created endpoints — useful before writing a query
at all, when the source category or partition isn't known yet (see
`skills/discovery-without-metadata/SKILL.md`):

```python
partitions = client.list_partitions()
rules = client.list_extraction_rules()
views = client.list_scheduled_views()
```

All three page through the API's `token`/`next` cursor internally and
return the full list.

### Manual control (create/poll/fetch separately)

`run_search()` covers the common case. If you need to inspect the job
between steps, use the lower-level methods directly:

```python
job = client.create_job(query, from_time="-1h", to_time="now")
job_id = job["id"]

try:
    status = client.poll_until_done(job_id, timeout_s=300)
    result = client.fetch_all(job_id, status, limit=5000)
finally:
    client.delete_job(job_id)   # always clean up, even on failure
```

## Configuration

```python
client = SumoSearchClient(
    access_id, access_key, endpoint,
    min_interval=0.25,      # 4 req/sec — the per-key API limit
    max_retries=3,          # retries after an HTTP 429
    base_backoff=5.0,       # seconds; doubles per retry attempt
    max_backoff=60.0,       # seconds; backoff cap
)
```

`run_search()` and `poll_until_done()` also accept `poll_timeout_s`
(default 600s). Jobs expire roughly 10 minutes after creation, so raising
this default rarely helps.

## Logging

The client uses the standard `logging` module under the logger name
`sumo_search_client`:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## What this client intentionally leaves out

To stay a lightweight, portable reference:

- No result caching, PII redaction, or output formatting — bring your own
  for production use.
- No adaptive (density-based) time-splitting — only fixed-interval; see
  `skills/search-job-api-best-practices/SKILL.md` for the pattern if you
  need it.
- No cross-process rate coordination — the throttle lives in one client
  instance. If multiple scripts or services share the same access key,
  each one throttling independently still lets combined traffic exceed the
  account's actual (shared) limits; see the shared-access-key note under
  "Rate Limiting & Retry" in `skills/search-job-api-best-practices/SKILL.md`.

## Development & Testing

This is a self-contained [uv](https://docs.astral.sh/uv/) project.

```bash
uv sync --group dev          # installs requests + pytest + ruff into ./.venv

uv run pytest                # unit tests — no credentials, no network
uv run pytest -k retry       # run a subset by keyword

uv run ruff check .          # lint
```

`tests/test_sumo_search_client.py` fakes HTTP via the `session=` parameter
`SumoSearchClient.__init__` already accepts, so it exercises the client's
real request-building, retry, polling, and pagination logic rather than a
re-implementation of it.

```bash
# Integration tests — exercise the full create -> poll -> fetch -> delete
# lifecycle against a real org. Needs SUMO_ACCESS_ID / SUMO_ACCESS_KEY.
# Not run in CI. --dry-run skips the live calls.
uv run python tests/integration_test_sumo_search_client.py
uv run python tests/integration_test_sumo_search_client.py --dry-run
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for PR expectations.

## License

[MIT](LICENSE).
