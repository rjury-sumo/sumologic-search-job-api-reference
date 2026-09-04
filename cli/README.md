# sumosearch — CLI reference

`sumosearch` is a `kubectl`-style CLI over the same endpoints as
`sumo_search_client.py`, for an agent (or human) driving ad hoc searches
from a terminal/bash tool rather than embedding a Python client. See the
top-level [README.md](../README.md#positioning-three-paths) for how it
compares to the Python client and Sumo's official `runSearchJob` MCP tool
— this doc only covers `sumosearch` itself.

## Install

As an isolated tool, on its own PATH entry (recommended for end use):

```bash
uv tool install . --with typer --with pyyaml     # installs the `sumosearch` command on your PATH

# after pulling new commits, reinstall to pick up the changes:
uv tool install . --with typer --with pyyaml --force
```

`--with typer --with pyyaml` is required — `uv tool install` does not pull
in this project's opt-in `cli` dependency group on its own, and omitting
either one fails at import time (`ModuleNotFoundError: No module named
'typer'` or `'yaml'`), not at install time.

Working inside a checkout of this repo instead (e.g. for development)?

```bash
uv sync --group cli
```

This reads the `cli` dependency group (`typer` + `pyyaml`, both required —
`pyyaml` backs `cli/instances.py`'s named-instance config) directly from
`pyproject.toml`, separate from `--group dev`. Either way,
`sumo_search_client.py` stays a zero-extra-dependency, copy-paste file for
consumers who only want the Python client — installing the CLI never pulls
these into that path.

## Credentials

Resolved from the environment by default: `SUMO_ACCESS_ID`,
`SUMO_ACCESS_KEY`, `SUMO_ENDPOINT` — the same three variables the Python
client uses (see the top-level README's Quickstart). `--access-id`/
`--access-key`/`--endpoint` exist as an override, not the primary path:
credentials as CLI flags end up in shell history, so the env vars are what
you actually want.

Missing credentials print `Missing required credentials: ...` to stderr and
exit 1 — no traceback.

### Multiple instances

For talking to more than one Sumo Logic org/deployment, `sumosearch` supports
named **instances** (endpoint + optional description, kubectl-`context`
style) stored in `~/sumo-search/config.yaml`. Credentials are never written
to that file — only endpoint/description are persisted; auth for a named
instance always comes from suffixed env vars, resolved at the point of use.

```bash
# register an instance — --endpoint takes a region alias or a full URL
uv run sumosearch instance add demo --endpoint us2 --description "demo org"
uv run sumosearch instance list
uv run sumosearch instance show demo
uv run sumosearch instance remove demo

# persist a "current" instance, like kubectl's current-context
uv run sumosearch context set demo
uv run sumosearch context show
uv run sumosearch context unset      # same as `context set none`
```

To use `demo` for one command without changing the persisted context, pass
`--instance demo`. Either way (via `--instance` or a persisted context),
credentials for that instance are read from `SUMO_ACCESS_ID_DEMO` /
`SUMO_ACCESS_KEY_DEMO` — the instance name uppercased, with any non-
alphanumeric character turned into `_` (e.g. instance `us2-prod` ->
`SUMO_ACCESS_ID_US2_PROD`):

```bash
export SUMO_ACCESS_ID_DEMO=...
export SUMO_ACCESS_KEY_DEMO=...
uv run sumosearch --instance demo search count '_sourceCategory=prod/app' --from -1h --to now
```

**Resolution order**, most specific first:

1. `--access-id`/`--access-key`/`--endpoint` on the command line — always win.
2. `--instance NAME` on the command line, or (if not given) the persisted
   `context set` instance — credentials from `SUMO_ACCESS_ID_<NAME>`/
   `SUMO_ACCESS_KEY_<NAME>`, endpoint from that instance's stored config.
   An active instance does **not** fall back to the plain `SUMO_ACCESS_ID`/
   `SUMO_ACCESS_KEY` env vars if its own suffixed vars are unset — that
   would silently reuse the default identity for a different named
   instance, defeating the point of naming it.
3. No instance in play at all (no `--instance`, no persisted context): plain
   `SUMO_ACCESS_ID`/`SUMO_ACCESS_KEY`/`SUMO_ENDPOINT` — unchanged default
   behavior for anyone not using instances.

### Region aliases

`--endpoint` (on any command, and on `instance add`) accepts either a full
`https://` URL or a case-insensitive region alias:

| Alias | Endpoint |
| --- | --- |
| `us1` | `https://api.sumologic.com` |
| `us2` | `https://api.us2.sumologic.com` |
| `au` | `https://api.au.sumologic.com` |
| `ca` | `https://api.ca.sumologic.com` |
| `de` | `https://api.de.sumologic.com` |
| `eu` | `https://api.eu.sumologic.com` |
| `fed` | `https://api.fed.sumologic.com` |
| `in` | `https://api.in.sumologic.com` |
| `jp` | `https://api.jp.sumologic.com` |
| `kr` | `https://api.kr.sumologic.com` |

## Command reference

Structure, verified against `cli/main.py`: `run`/`estimate`/`count` nest
under `search`; `partitions`/`fers`/`views`/`dashboards` nest under
`discover`; `add`/`list`/`remove`/`show` nest under `instance`;
`set`/`show`/`unset` nest under `context`; `run`/`describe`/`status`/
`result`/`list`/`open`/`cleanup` nest under `report`; `schema`, `sample`,
and `export` are top-level commands (not nested under anything).

### `search run`

Create → poll → fetch → delete, print the result.

```bash
uv run sumosearch search run '_sourceCategory=prod/app error' --from -1h --to now
```

| Flag | Default | Notes |
| --- | --- | --- |
| `--from` / `--to` | required | epoch ms, `now`, a relative expression (`-1h`), or ISO 8601 |
| `--format` | `csv` for records results, `ndjson` for messages results | `csv\|ndjson\|json\|table` |
| `--fields` | none | comma-separated field names to project/keep (messages results only — see [Field projection](#field-projection-and-token-budget-controls)) |
| `--limit` | none | cap on rows fetched |
| `--aggregate` | off | forces `requiresRawMessages=False`; mutually exclusive with `--raw` |
| `--raw` | off | forces `requiresRawMessages=True`; mutually exclusive with `--aggregate` |
| `--auto-parsing` | server default | `manual\|autoparse` (case-insensitive) |
| `--max-tokens` | none | messages/raw results only — see below |
| `--drop-null-columns` | off | records output only — see below |
| `--no-warn` | off | suppress the stderr token-budget warning |

### `search estimate`

Pre-flight scan-size estimate (`estimate_scan()`) — no search job created.

```bash
uv run sumosearch search estimate '_sourceCategory=prod/app error' --from -1h --to now
```

`--from`/`--to` required. `--format csv|ndjson|json|table`, default `table`.
Output is `total_bytes` plus a per-partition breakdown, not a row/record
table — `table` is a more natural default here than `search run`'s `csv`.

### `search count`

```bash
uv run sumosearch search count '_sourceCategory=prod/app error' --from -1h --to now
```

Prints a single row-count scalar (`estimate_count()`) and nothing else — no
`--format`, since there's only one number to render.

### `discover partitions` / `discover fers` / `discover views`

Three synchronous, no-job-created discovery commands, useful before a
partition or source category is known:

```bash
uv run sumosearch discover partitions --grep cloudtrail
uv run sumosearch discover fers --grep json
uv run sumosearch discover views --grep prod
```

Each takes `--grep` (case-insensitive substring filter — `name`/
`routingExpression` for partitions, `name`/`parseExpression`/`fieldNames`
for FERs, `indexName`/`query` for views) and `--format csv|ndjson|json|table`
(default `csv`). Each carries the stderr token-budget warning (see below);
none takes `--no-warn`.

### `discover dashboards`

Find dashboards by keyword, technology, or business service/user journey
— useful when you know roughly what you're looking for but not the
dashboard id.

```bash
uv run sumosearch discover dashboards --grep checkout
```

`GET /v2/dashboards` has no server-side search parameter, so this pulls the
*entire* viewable dashboard list (paginated 100/page) and filters
client-side:

| Flag | Default | Notes |
| --- | --- | --- |
| `--grep` | none | case-insensitive substring filter on `title`/`description`/`domain` |
| `--mode` | `all` | `all\|mine` — `allViewableByUser` vs. `createdByUser` |
| `--no-cache` | off | skip the on-disk cache and pull fresh (see below) |
| `--limit` | `50` | cap on the *filtered* results actually printed |
| `--format` | `csv` | `csv\|ndjson\|json\|table` |

Each dashboard row is projected to `id`, `contentId`, `title`,
`description`, `folderId`, `domain` — the list endpoint's `panels`/
`layout`/`variables`/`topologyLabelMap` are dropped (a single dashboard's
full definition can be tens of KB, and 1000s of them would blow out the
context window). Note there's no `created`/`modified` filter — the
`/v2/dashboards` list endpoint doesn't return those fields at all.

**Caching**: a full-org pull can take a while, but dashboards don't change
often and a caller typically runs several `--grep` searches back to back to
find what they're after. So the unfiltered list (from `list_dashboards()`)
is cached to disk at
`~/sumo-search/output/<instance>/dashboards/list-<mode>.json` and reused
for 24h before a fresh pull; `--grep`/`--limit` are always applied fresh
against whatever list — cached or just-pulled — is in hand. Pass
`--no-cache` to force a fresh pull (which also refreshes the cache for next
time). The cache is keyed by instance + `--mode`, so `all` and `mine`
never collide.

### `schema`

Profile a query's field schema from a small sample, without hand-writing a
sample query yourself.

```bash
uv run sumosearch schema '_sourceCategory=prod/app' --from -1h --to now
```

| Flag | Default |
| --- | --- |
| `--from` / `--to` | required |
| `--n` | `50` — sample size, appended as `\| limit N` |
| `--auto-parsing` | `manual` |

Runs `<query> | limit N` under `autoParsingMode=Manual` by default —
deliberately not `autoparse`, because AutoParse pre-flattens JSON
server-side, which would erase the index-time-vs-search-time distinction
this command exists to report (see [Schema output](#schema-output) below).

### `sample`

```bash
uv run sumosearch sample '_sourceCategory=prod/app' --from -1h --to now --n 20
```

Runs `<query> | limit N` and prints it through the same rendering path as
`search run` — `--n` (default `20`), `--format` (default `csv` for records,
`ndjson` for messages), `--drop-null-columns` (records only). Does not take
`--fields`, `--max-tokens`, `--aggregate`, `--raw`, or `--auto-parsing` —
it's a quick raw preview, not a tuned fetch.

### `export`

Bulk export straight to disk.

```bash
uv run sumosearch export '_sourceCategory=prod/app error' --from -24h --to now \
    --format csv --out events.csv
```

| Flag | Default | Notes |
| --- | --- | --- |
| `--from` / `--to` | required | |
| `--format` | required | `csv\|ndjson\|json` — no `table`, this is file output |
| `--out` | required | output file path |
| `--interval-hours` | auto-computed | override the time-split window size |

Never prints exported data to stdout — only a one-line completion summary
(`wrote N rows to <path> (...)`). See [Export time-splitting](#export-time-splitting)
below for the auto-split behavior.

### `report run`

Export a dashboard to PDF/PNG via the async `dashboards/reportJobs` API
(`sumo_dashboard_client.py`, a separate client from `sumo_search_client.py`
— this is a different endpoint, not a search job).

```bash
uv run sumosearch report run <dashboard-id> --hours 24 --format pdf
```

| Flag | Default | Notes |
| --- | --- | --- |
| `--format` | `pdf` | `pdf\|png` |
| `--mode` | `snapshot` | `snapshot\|report-mode` |
| `--theme` | dashboard's own saved theme | `light\|dark` |
| `--export-width` | none | pixels, `1500`-`6000` — validated client-side; the API's 400 on an out-of-range value isn't descriptive |
| `--timezone` | `UTC` | IANA timezone name |
| `--from` / `--to` | none | same forms as `search run`; omit both to use the dashboard's own saved default time range |
| `--hours` | none | last N hours, overrides `--from`/`--to` |
| `--variable NAME=VALUE` | none | repeatable — multi-select variables append; explicit values win over the dashboard's own saved defaults (see below) |
| `--panel-override ID=collapsed\|expanded` | none | repeatable — collapse/expand a `CollapsiblePanel` section; requires preflight |
| `--out` | managed output directory | explicit output file path |
| `--timeout` | `180` | seconds to wait for the report job |
| `--no-preflight` | off | skip fetching the dashboard first — see the gotcha below before using this |
| `--open` | off | open the file after saving (cross-platform, via Click's `launch()`) |
| `--dry-run` | off | preflight and print the report job body; create no job |

**Gotcha — default variable values are not automatic.** The report-job API
applies a dashboard's saved default *time range* automatically when
`--from`/`--to`/`--hours` are all omitted, but does **not** apply saved
default *variable values* the same way: omitting a `{{var}}`'s value
entirely renders that panel as "Something went wrong", with no error at the
job-status level. `report run` closes this gap by fetching the dashboard
(unless `--no-preflight`) and merging each variable's own `defaultValue` in
before submitting — explicit `--variable` still wins per-name. Only pass
`--no-preflight` for a dashboard you know has no `{{variables}}`.

Files are written under `~/sumo-search/output/<instance>/report/` by
default (see `report list`/`open`/`cleanup` below) — pass `--out` for an
explicit path instead.

### `report describe`

```bash
uv run sumosearch report describe <dashboard-id> [--panels] [--queries]
```

Summarizes a dashboard's shape without exporting it: time range, variables
(with their saved defaults), panel count/types, and layout grid at the
default "summary" level; add `--panels` for a per-panel list (id, key,
title, type, `{{var}}` references, grid position — collapsible sections
nest their member panels under a `children` list); `--queries` implies
`--panels` and adds each panel's actual query text. Pure JSON output, no
`--format` flag.

### `report status` / `report result`

```bash
uv run sumosearch report status <job-id>
uv run sumosearch report result <job-id> --out result.pdf
```

Check an in-flight report job's status, or fetch an already-completed job's
binary result directly (useful if `report run` was interrupted after job
creation). `result` takes the same `--out` default as `run`.

### `report list` / `report open` / `report cleanup`

```bash
uv run sumosearch report list --format table
uv run sumosearch report open                  # opens the most recent report
uv run sumosearch report open my-report.pdf     # opens a specific file by name
uv run sumosearch report cleanup --older-than 30d
```

`list` shows every file under the managed output directory across all
instances (`--format csv|ndjson|json|table`, default `table`). `open`
resolves a path or bare filename (searching every instance's report
directory) or defaults to the most recently saved file, then opens it via
Click's cross-platform `launch()`; `--no-open` resolves and prints the path
without opening it. `cleanup --older-than` (default `30d`; also accepts
`h`/`m`) deletes files older than the given duration and reports the count
and bytes freed.

## Output formats

Four renderers, shared across commands that produce row data: `csv`,
`ndjson` (one JSON object per line), `json` (a single envelope/array), and
`table` (aligned, human-readable). Which one is the default is **not**
uniform across commands — it depends on the result shape:

| Command | Default when result is records/aggregate | Default when result is messages/raw |
| --- | --- | --- |
| `search run` | `csv` | `ndjson` |
| `sample` | `csv` | `ndjson` |
| `search estimate` | `table` (not a records/messages result at all) | — |
| `discover *` | `csv` (always — discovery rows have no records/messages distinction) | — |
| `export` | required explicitly, no default; `table` not accepted | |

CSV is the default for records/aggregate output because it measured
~2.3x smaller than raw JSON for the same rows in practice (see the
top-level README's "Token efficiency" section) — dropping repeated key
names and JSON punctuation matters when the result feeds an LLM/agent
caller. ndjson is the default for messages/raw output because those rows
don't share a fixed column set the way records do.

## Field projection and token-budget controls

These exist because the primary caller is an agent with a limited context
window, not a human terminal — every mechanism below trades completeness
for a predictable, controllable output size.

**Client-side field projection (`search run --fields`, and the fixed
envelope under it).** Server-side `| fields` cannot be trusted to actually
narrow a raw-message result — on an account with a large field-extraction-
rule catalog it can no-op (leaving the full row envelope intact) or trigger
a sparse global-field union that *inflates* the response. So for `messages`
results, `search run` and `sample` always build each row from a fixed
envelope (`_messagetime`, `_sourcecategory`, `_sourcehost`, `_raw` — only
those actually present, e.g. a lookup-table read has no `_raw`) plus
whatever `--fields` names you add, done client-side after fetch. `--fields`
is accepted only by `search run`, not `sample` or `export`.

**`--max-tokens` (search run only, messages/raw results only).** Drops
whole trailing rows — never truncates mid-record — until the rendered
output's estimated token count (a `len(text) // 4` heuristic) is at or
under the given budget. Uses a binary search over row count, so it's
O(log n) renders rather than O(n). Prints
`note: dropped N of M rows to stay under --max-tokens ...` to stderr when
it drops anything.

**`--drop-null-columns` (records output only).** Drops any column that is
null/empty/missing across *every* row in the result — useful for aggregate
results where a `count by` over several dimensions often has columns that
happen to be entirely blank in the current sample.

**stderr token-budget warning.** Every command that renders row data
(`search run`, `sample`, `discover *`) estimates the output's token count
after rendering; if it exceeds 4000 tokens (`DEFAULT_TOKEN_WARNING_THRESHOLD`),
it prints `warning: response is ~N tokens — consider an aggregate query, a
tighter | limit, or --fields` to stderr. `search run` accepts `--no-warn` to
suppress this; `sample` and `discover *` do not expose a suppression flag.
This is a nudge, not an enforcement mechanism — combine it with
`--max-tokens` or a tighter query when you actually need a hard cap.

## Schema output

`sumosearch schema` reports one row per field, in a table with columns
`FIELD | PRESENT | TYPE | CONST | INDEX-TIME | EXAMPLE`:

- **PRESENT** — `n/N`, how many of the sampled rows had this field.
- **TYPE** — inferred from the first non-null value seen (`boolean`,
  `number`, `object`, `array`, `string`), or `unstructured-text` for `_raw`
  when none of the sample's `_raw` values parsed as JSON.
- **CONST** — `YES` when every occurrence of the field (across ≥2
  occurrences) had the same value — a candidate for a query-time filter
  rather than a `count by` dimension.
- **INDEX-TIME** — `yes` when the field was ever seen as a top-level `map`
  key, i.e. present under `autoParsingMode=Manual` *without* needing to
  JSON-parse `_raw` at all: FER-extracted fields, collector/source
  metadata (`_sourcecategory`, `_sourcehost`, ...), and header-derived
  fields all count. `no` means the field only ever showed up after
  client-side JSON-parsing a JSON-shaped `_raw` (one level of dot-notation
  flattening for nested objects) — a search-time-only field, unavailable
  to index-time filtering.
- **EXAMPLE** — the first non-null value seen, stringified and truncated to
  80 characters.

When most of the sample's `_raw` values are non-JSON and share a
consistent whitespace-token count, `schema` appends a
`hint: _raw looks like fixed-format text (...) — consider '| parse regex'`
line after the table. It never synthesizes an actual regex, just flags the
opportunity.

## Export time-splitting

`export` first calls `estimate_count()` on the query/time range. If the
estimate is at or under 80,000 rows (a safety margin below the API's
100,000-raw-message-per-job cap), it runs a single job. Above that
threshold, it time-splits automatically via `time_split_search()`:

```text
interval_hours = max(
    total_window_hours * (80_000 / estimated_count),
    1/60,   # 1-minute floor, so a pathological estimate can't compute
            # an interval of a few seconds and spawn thousands of jobs
)
```

`--interval-hours` overrides this computed value directly. The completion
message reports which path was taken: `... (single job, no time-splitting
needed)` or `... (N time windows)`.

`export` deliberately does **not** apply the `search run`/`sample` field
projection — every row is the full, untrimmed `item["map"]`. This is the
disk/human path: a file on disk isn't consuming an agent's context window
the way stdout output is, so there's no token-budget reason to trim it, and
trimming would silently lose data from a bulk export.

## Errors and exit codes

Every command wraps its API calls in a `try`/`except SumoSearchError` (the
same exception `sumo_search_client.py` raises for HTTP-level failures,
timeouts, and failed jobs) and turns it into a one-line `<Verb> failed:
<message>` on stderr plus exit code 1 — never a Python traceback. Example:

```bash
$ uv run sumosearch search run '_sourceCateogry=typo | count' --from -1h --to now
Search failed: <error detail from the API>
$ echo $?
1
```

`export` additionally validates `--format` (rejects `table`) and surfaces
`time_split_search()`'s `ValueError` (e.g. an `--interval-hours` too small
for the window) the same way, with a hint to try a larger explicit
`--interval-hours`.

`report *` commands follow the same pattern against `SumoDashboardError`
(`sumo_dashboard_client.py`'s equivalent of `SumoSearchError`) plus
`ValueError` for client-side validation failures (bad `--export-width`,
unknown `--variable`/`--panel-override` names, `--panel-override` combined
with `--no-preflight`, a malformed `--older-than`) — same one-line `<Verb>
failed: <message>` plus exit code 1, no traceback.

## Full flag reference

For the exact, current flag list and help text for any command, ask the CLI
itself:

```bash
uv run sumosearch --help
uv run sumosearch <command> --help
uv run sumosearch search <subcommand> --help
uv run sumosearch discover <subcommand> --help
```
