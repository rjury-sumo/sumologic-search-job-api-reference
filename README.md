# Sumo Logic Search Job API — Reference & Skills

<!-- markdownlint-disable MD013 -->

A standalone, customer-distributable reference for building log search and analytics on top of Sumo Logic's [Search Job API](https://help.sumologic.com/docs/api/search-job/): a gold-standard Python client plus a set of portable Agent Skills for writing good Sumo Logic queries.

## Who this is for

**Engineers building agentic automation** on top of the Search Job API directly who need an API client that handles the 'sharp edges' to provide a simple api client experience, to run well scoped, effective and efficient log searches in Sumo Logic. — `sumo_search_client.py` is a single-file, single-dependency (`requests`) Python client that gets the API's sharp edges right on the first pass: one shot 'run a search job' endpoint that handles, 3 separate API calls and sync polling for completion, Messages (raw) and Records (aggregate) with smart defaults such as 'requiresRawMessages', a per-key rate limit (throttled client-side to the default 4 requests/second) backed by exponential backoff with jitter on 429s that honors a numeric `Retry-After` header, and a `pendingErrors` field that a naive "did I get 0 results back" check will miss entirely, silently turning a broken query into a false "no results" report. Copy it into your own project and adapt it.

**End users, admins, and SIEM analysts** doing log discovery, query authoring, and search best-practice work — the `skills/` directory is portable [Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) content covering how to find the right data, scope a query efficiently, order pipeline operators, and shape results for an AI agent. These skills teach *how Sumo Logic search works*, not this specific client's API surface — they're equally useful driving queries through `sumo_search_client.py`, the `sumosearch` CLI, or Sumo's official `runLogSearch` MCP tool.

**An agent (or human) driving ad hoc searches from a terminal/bash tool** — the `sumosearch` CLI in `cli/` is a third path, for callers that don't want to embed the Python client in a program and don't have MCP tool access. It's a small `kubectl`-style wrapper over the same endpoints, with token-efficient output shaping (csv/ndjson/json/table, aggregate-friendly defaults, client-side field trimming for raw messages) built in rather than left to the caller. See "Quickstart: the `sumosearch` CLI" below and the full [`cli/README.md`](cli/README.md) reference.

**An agent (or human) investigating a problem, or looking for a good starting query for a log source** — `sumo_dashboard_client.py` exports a Sumo Logic dashboard as a PDF/PNG for visual analysis, or describes its structure, variables, and per-panel query text as query exemplars. Dashboards are built by an org's own users around a specific platform, use case, or business service/user journey, so a relevant one is already a high-fidelity, human-curated starting point. See [Dashboard reports: export and discovery](#dashboard-reports-export-and-discovery) below.

## Contents

| File | Purpose |
| --- | --- |
| `sumo_search_client.py` | The reference client — search job lifecycle plus read-only discovery endpoints (partitions, field extraction rules, scheduled views). Copy this into your project. See [`docs/sumo-search-client-reference.md`](docs/sumo-search-client-reference.md) for manual job control, configuration, and logging. |
| `sumo_dashboard_client.py` | The reference client for the Dashboard Report Job API — export a dashboard as PDF/PNG, or fetch and describe its structure, variables, and panel queries. Sibling to `sumo_search_client.py` (imports its `resolve_time()` helper); copy both files together. See [Dashboard reports: export and discovery](#dashboard-reports-export-and-discovery) below, and [`docs/sumo-dashboard-client-reference.md`](docs/sumo-dashboard-client-reference.md) for manual job control, variable/panel-override handling, configuration, and logging. |
| `cli/` | `sumosearch` — a shell/agent-oriented CLI wrapping the same endpoints (search jobs and dashboard reports), with token-efficient output shaping built in. Install via `uv tool install . --with typer --with pyyaml` (see [Quickstart](#quickstart-the-sumosearch-cli) below for why the `--with` flags are required) or `uv sync --group cli`, invoke as `sumosearch ...`. Not a copy-paste artifact like the clients — it's an installable console-script entry point. See [`cli/README.md`](cli/README.md) for the full command reference. |
| `skills/` | Portable, harness-agnostic Agent Skills: the API-calling best practices the client implements, plus query-authoring skills (scoping, discovery, operator ordering, common patterns, agent-friendly result shaping, scheduled views, indexes/partitions, Cloud SIEM). See [`skills/README.md`](skills/README.md) for the full index and suggested reading order. No dashboard-specific skills yet — see [Dashboard reports: export and discovery](#dashboard-reports-export-and-discovery) below. |
| `tests/` | Unit tests (`test_sumo_search_client.py`, `test_sumo_dashboard_client.py`, `test_dashboard_describe.py`, `test_cli.py`, no credentials needed) and live-credential integration tests for both clients. |
| `pyproject.toml` | A self-contained [uv](https://docs.astral.sh/uv/) project for developing and testing these clients and the CLI — not needed if you're just copying a client file into your own project. |

## Skills work with this client, the CLI, or Sumo's `runLogSearch` MCP tool

The `skills/` directory is not tied to `sumo_search_client.py`. Each skill teaches query-authoring and search-methodology patterns — how to scope a query to keep scan cost down, how to discover which partition or source category holds the data you need, how to order pipeline operators, how to shape results for an LLM caller — that apply the same way regardless of how the query actually gets executed. Whether you're running queries through this client, the `sumosearch` CLI, or Sumo's official `runLogSearch` MCP tool, the skills apply unchanged; only the transport differs.

Start with [`skills/README.md`](skills/README.md) for the full skill index, what each one covers, and a suggested reading order for a new integration.

### Positioning: three paths for running Search Jobs

| | `sumo_search_client.py` | `sumosearch` CLI | Sumo MCP (`runLogSearch`, `listPartitions`, `listExtractionRules`) |
| --- | --- | --- | --- |
| Transport | Python library, embed in your own code | Subprocess, invoked via shell/bash tool | In-chat tool call, no subprocess |
| Best for | Building a service/pipeline on top of the API | An agent (or human) driving ad hoc searches from a terminal/bash tool | An agent inside a harness with native MCP tool access |
| Dependency footprint | `requests` only | `typer`, isolated to an opt-in `cli` dependency group | None (no install) |

### The log search journey

Getting from "I have a problem to solve" to "a correctly scoped search — right syntax, right parsing, right field schema — for this specific use case" is rarely one step.

For Sumo Logic's new Mobot Log Analysis agent the agent interpets user intent, discovers log sources, writes multiple searches, reports back summarized reuslts and can suggest next steps. The flow is more 'ask a question' rather than 'write a log search'.

In the traditional search UI flow this plays out as a series of phases with a UX experience built to enable flexible, fast, open ended log exploration as scale (either raw message or aggegates). Users must frame the problem and write a series of log searches.

We can think of this log analysis journey for the user as a series of steps to go from question/prolem to log search(es). A user who already knows part of the problem domain for their Sumo Logic instance can skip ahead to a later one, and over time saves content in the Library as a known-good starting point for next time:

1. **Reuse existing content.** Check Sumo Logic apps, dashboards, saved searches, and alerts for a solved (or close-relative) use case — this can fast-forward straight to field mapping or query crafting below. Advanced users pivot off data-volume/ingestion audit data to find matching metadata, or search the audit index for similar searches other users have already run.
   > **Output:** a reusable saved asset close to the current use case.
2. **Confirm metadata scope** (`_sourceCategory`, `_index`, etc.) with exploratory searches — scope drives both search speed and success rate. In the UI, query-assist autocomplete surfaces metadata fields and values as you type, IDE-style; the Search Job API has no discovery endpoint of its own, which is what [`skills/discovery-without-metadata`](skills/discovery-without-metadata/SKILL.md) exists to work around programmatically.
   > **Output:** correct metadata scope, e.g. `_sourceCategory=foo _index=bar`.
3. **Sample and discover log format** — JSON, key-value/space-delimited, an industry-standard format, or fully custom. (See [`skills/discovery-profile-scope`](skills/discovery-profile-scope/SKILL.md) for the API-driven version.)
   > **Output:** log format and field schema broadly understood.
4. **Map fields.** Review the field browser: which fields matter for this use case, are they already parsed, or does it need `parse`/`json`/`parse regex`? Per-field value-distribution views (sampled from the first 100k results) and click-to-filter let advanced users zoom in — narrowing time range and field values across a series of related searches via the field browser or the clickable time histogram.
   > **Output:** exact fields and values needed to answer the use case.
5. **Craft the final search.** With scope, format, and fields known, write the query. Basic users "super-grep" — locate raw events and read them. Advanced users chain `parse`/filter/aggregate/format operators (see [`skills/operator-ordering`](skills/operator-ordering/SKILL.md)) to turn events into insight — tabular or graphical, categorical breakdown or time series.
   > **Output:** a syntactically correct search that answers the question.
6. **Iterate.** Power users rarely stop at one search — they run a series of related queries, narrowing to a key slice of data or reshaping output to surface a specific insight. The two audiences iterate differently: **security investigation** typically pivots on an entity (user, host, IP), correlating activity across log sources to reconstruct a timeline, often under time pressure — see [`skills/search-siem-investigation`](skills/search-siem-investigation/SKILL.md), which covers querying Cloud SIEM's normalized records, signals, and insight audit events (`sec_record_*`, `sec_signal`) alongside the original log events behind them, all via the Search Job API. Sumo's own [Sumo Investigator MCP skill](https://www.sumologic.com/help/docs/api/mcp-server/#improve-investigations-with-the-sumo-investigator-skill) goes further, with direct read/write access to Insights, Signals, and Detection Rules as first-class objects — deliberately out of scope for this repo today, which constrains itself to log search rather than Cloud SIEM's config APIs; **observability** work more often drills from a symptom (an alert, an anomalous graph) down through correlated signals to a root cause.
   > **Output:** an investigation timeline or an identified root cause, depending on the audience.

### Key considerations for agentic API integration

These sharp edges matter far more to an API/agent caller than to a human in the search UI — the UI absorbs most of them via presentation, result paging, and interactive discovery (query-assist autocomplete, field browser, clickable histograms) that the raw API simply doesn't have. Calling the Search Job API directly, or through an MCP tool, means owning these yourself:

- **No discovery endpoint.** Log search requires metadata and knowledge of the log format(s). The API can't autocomplete metadata the way the UI does — see [`skills/discovery-without-metadata`](skills/discovery-without-metadata/SKILL.md) and [`skills/discovery-profile-scope`](skills/discovery-profile-scope/SKILL.md), or this client's read-only `list_partitions()`/`list_extraction_rules()`/`list_scheduled_views()` calls. Two built-in system indexes fill much of this gap by search rather than API call: `sumologic_volume` rolls up ingest volume by source category/collector/partition, so searching it for a keyword or technology name is a fast way to confirm which metadata strings actually match ingested data in this instance — see [`skills/search-indexes-partitions/references/sumologic-volume.md`](skills/search-indexes-partitions/references/sumologic-volume.md). `sumologic_search_usage_per_query` (the Search Audit view) is a complementary source of "known good" queries — its `query` and `query_type` columns are a searchable log of every query other users, dashboards, and scheduled searches have already run, which is especially valuable for discovering how to query a proprietary or otherwise-undocumented custom log format — see [`skills/search-indexes-partitions/references/sumologic-search-usage.md`](skills/search-indexes-partitions/references/sumologic-search-usage.md).
- **Good scoping practices** In large instances poor scoped or 'fishing trip' searches vs raw logs can be slow, inefficient or incur pay-per-search charges on some plans (infrequent, flex) see: [`skills/query-scoping-efficiency`](skills/query-scoping-efficiency/SKILL.md).
- **Search best practices** LLMs are often poor at writing syntactically correct Sumo Logic log searches, or write valid but very slow / inefficient ones. Skills in this repo help with common search patterns: [`skills/common-query-patterns`](skills/common-query-patterns/SKILL.md) and [`skills/operator-ordering`](skills/operator-ordering/SKILL.md)
- **Per-key rate limits (429s) and search job api patterns.** The 4 requests/sec rate limit means UI-side queuing is essential, especially for high volume or multi-agent scenarios — see [Engineers building agentic automation](#who-this-is-for) above for this client's built-in throttling and backoff. Common pitfalls and patterns are documented for those wanting a good start for 'roll your own'. see: [`skills/search-job-api-best-practices`](skills/search-job-api-best-practices/SKILL.md) One such gotcha is the **`pendingErrors` masquerading as empty results.** A broken query can report zero rows with errors — see the "State Machine" section of [`skills/search-job-api-best-practices`](skills/search-job-api-best-practices/SKILL.md).
- **Token/output cost.** Directly reading large log search result sets in an LLM context results in high token / context use. Query shape (aggregate vs. raw) dominates response size well before any formatting choice — see ["Why aggregate results are the best choice for token efficiency"](#why-aggregate-results-are-the-best-choice-for-token-efficiency) below. The 'agent friendly' cli can export in csv format: proven to reduce token usage dramatically vs JSON.
- **Unbounded result sets.** Keep result sets small and compact — cap every raw query with a first-line `| limit N` and every aggregate with `| sort ... | limit N` / `| topk`, and use `nodrop` on `parse`/`json` field extraction so rows with a missing field don't silently disappear. See [`skills/operator-ordering`](skills/operator-ordering/SKILL.md).

Let's consider how steps 2–6 above map onto the API/MCP stages below. Step 1 (reusing saved content) is UI/Library-only for `sumo_search_client.py` and `sumosearch` — both are scoped to the Search Job API — but Sumo's own MCP investigator skill can reuse dashboards and alerts directly (see the table's last row); that's a Sumo-provided capability layered on MCP, not something this repo's client or CLI expose.

| Stage | `sumo_search_client.py` | `sumosearch` CLI | Sumo MCP |
| --- | --- | --- | --- |
| **Discover** data (partitions, source categories, scheduled views) | `list_partitions()`, `list_extraction_rules()`, `list_scheduled_views()` — synchronous, no job created | `sumosearch discover partitions\|fers\|views` | `listPartitions`, `listExtractionRules` |
| **Schema discovery** (what fields does this query actually produce) | Manual — `list_extraction_rules()` plus hand-writing a raw sample yourself | First-class `sumosearch schema` command | Not exposed |
| **Pre-flight cost check** (scan bytes before committing) | `estimate_scan()` | `sumosearch search estimate` | Not in the three listed tools |
| **Sample** a query on a small window | Manual — `run_search()` with `\| limit N` | `sumosearch sample` | Manual — `runLogSearch` with `\| limit N` |
| **Write and Run** a search (create → poll → fetch → delete) | `run_search()` | `sumosearch search run` | `runLogSearch` |
| **Work with results** | Raw API JSON — bring your own formatting | Built-in csv/json/ndjson/table, agent-optimized defaults, client-side field trimming that actually works on the raw-message path | Whatever Sumo's MCP server returns — out of this repo's control |
| **Investigation workflow discipline** (sample-before-run, first-line limits, `nodrop`) | Left to the caller — `skills/` documents it | Left to the caller — `skills/` documents it | Not enforced by the raw tools; Sumo's [Investigator skill](https://www.sumologic.com/help/docs/api/mcp-server/#improve-investigations-with-the-sumo-investigator-skill) layers a mandatory Discover (parallel `listPartitions`/`listCustomFields`/`listExtractionRules`) → 5-min/≤5-row Sample → Targeted Search workflow on top as agent policy |
| **Insights, Detection Rules, Alerts, Dashboards** (SIEM triage & content reuse — not log search) | Out of scope — Search Job API only | Out of scope — Search Job API only | Native, via the [Investigator skill](https://www.sumologic.com/help/docs/api/mcp-server/#improve-investigations-with-the-sumo-investigator-skill): `getInsights`, `getRules`, `alertsSearch`, `listDashboards`, `createDashboard`, etc. |

The skills in this repo are written against that stage, not against any one of the three tools above — the same skill applies whether the caller is `sumo_search_client.py`, the `sumosearch` CLI, or Sumo's MCP tools, since all three eventually hit the same Search Job API sharp edges:

| Stage | Skill(s) |
| --- | --- |
| **Discover** data | [`discovery-without-metadata`](skills/discovery-without-metadata/SKILL.md), [`discovery-profile-scope`](skills/discovery-profile-scope/SKILL.md), [`search-indexes-partitions`](skills/search-indexes-partitions/SKILL.md), [`search-siem-investigation`](skills/search-siem-investigation/SKILL.md) |
| **Schema discovery** | [`discovery-profile-scope`](skills/discovery-profile-scope/SKILL.md) |
| **Pre-flight cost check** | [`query-scoping-efficiency`](skills/query-scoping-efficiency/SKILL.md), [`search-indexes-partitions`](skills/search-indexes-partitions/SKILL.md) |
| **Sample** a query | [`discovery-profile-scope`](skills/discovery-profile-scope/SKILL.md), [`operator-ordering`](skills/operator-ordering/SKILL.md) |
| **Write and Run** a search | [`search-job-api-best-practices`](skills/search-job-api-best-practices/SKILL.md), [`common-query-patterns`](skills/common-query-patterns/SKILL.md), [`ai-agent-result-shaping`](skills/ai-agent-result-shaping/SKILL.md) |
| **Work with results** | — |
| **Investigation workflow discipline** | [`operator-ordering`](skills/operator-ordering/SKILL.md), [`search-job-api-best-practices`](skills/search-job-api-best-practices/SKILL.md) |
| **Insights, Detection Rules, Alerts, Dashboards** | — out of scope for this repo's skills; native to Sumo's MCP [Investigator skill](https://www.sumologic.com/help/docs/api/mcp-server/#improve-investigations-with-the-sumo-investigator-skill) |

## Dashboard reports: export and discovery

`sumo_dashboard_client.py` is a sibling client for a different API surface: the [Dashboard Report Job API](https://help.sumologic.com/docs/api/dashboards/) (`/api/v2/dashboards/...`), not the Search Job API above. It creates → polls → fetches an async report job that renders a dashboard to PDF or PNG, and it can fetch the dashboard's own JSON object to describe its structure — time range, `{{variables}}` and their saved defaults, panel layout, and each panel's actual query text — without rendering anything.

Dashboards are built by an org's own users around a specific platform, use case, or business service/user journey: each panel already encodes a previously validated search, its title names what it's for, and text panels often add human interpretation alongside the data. That makes a relevant dashboard useful to an agentic caller in two different ways, depending on which side of it you use:

1. **Export for visual analysis.** Render the dashboard as a PDF/PNG — optionally scoped to the current investigation via `--hours`/`--from`/`--to` and `--variable`, or with sections collapsed/expanded via `--panel-override` — then read the image for insight. Because a dashboard runs many panels' worth of searches together, and panel titles/text panels supply interpretation the raw search results don't have, this is a fast way to get broad, human-curated context on a symptom or question without hand-writing each underlying search yourself.
2. **Discover exemplar queries.** `report describe --queries` (or `describe_dashboard_queries()` in the client) returns every panel's actual query text without executing anything or rendering an image. For a dashboard already known to be relevant to a log source or use case, this is a source of known-good exemplar queries — a starting point when crafting a new search, or raw material for building a skill around a custom or unfamiliar log source.

Both paths need only a dashboard id. `report describe` (no flags) is cheap and synchronous — use it first to confirm what's on a dashboard and what `{{variables}}` it expects before spending a full export job on it.

**Gotcha carried over from the client itself:** the report-job API applies a dashboard's saved default *time range* automatically when no time flag is given, but does **not** apply saved default *variable values* the same way — a `{{var}}` panel with no value supplied renders "Something went wrong" with no error at the job-status level. Both `sumo_dashboard_client.py` (via `default_variable_values()`) and the CLI's `report run` (by default, unless `--no-preflight`) fetch the dashboard first and merge each variable's own `defaultValue` in before submitting.

### Positioning: three paths for dashboard reports

| | `sumo_dashboard_client.py` | `sumosearch` CLI | Sumo MCP |
| --- | --- | --- | --- |
| Transport | Python library, embed in your own code | Subprocess, invoked via shell/bash tool | In-chat tool call, no subprocess |
| Export a PDF/PNG report | `create_report_job()` → `poll_report_job()` → `get_report_result()` | `sumosearch report run <dashboard-id> --format pdf\|png` | **Not available** — no report/export tool |
| Describe structure, variables, panel queries | `get_dashboard()`, summarized with `cli/dashboard_describe.py`'s pure helpers | `sumosearch report describe <dashboard-id> [--panels] [--queries]` | **Not available** — no describe tool |
| Best for | Building a service/pipeline (e.g. scheduled export + downstream analysis) on top of the API | An agent driving ad hoc exports/describes from a terminal | Listing/creating dashboards as SIEM content (via the Investigator skill's `listDashboards`/`createDashboard`) — not rendering or describing them |
| Dependency footprint | `requests` only (same as `sumo_search_client.py`) | none beyond `sumosearch` itself | None (no install) |

Sumo's official MCP tools have no equivalent of `report run`/`report describe` today — a gap this repo's client and CLI fill. If that changes, this table is the place to update.

`skills/` has no dashboard-specific skill yet — teaching an agent when to reach for a dashboard export versus running its own search job, or how to read a rendered dashboard image for insight, is a planned addition for a later iteration. Until then, the query-authoring skills listed [above](#skills-work-with-this-client-the-cli-or-sumos-runlogsearch-mcp-tool) (`common-query-patterns`, `operator-ordering`, etc.) apply equally to query text pulled from a dashboard panel via `report describe --queries`.

## Why aggregate results are the best choice for token efficiency

If a result feeds an LLM/agent caller rather than a human dashboard, the query shape matters far more than any client-side formatting choice. Measured against real data (structured JSON, space-delimited text, and a JSON-in-`_raw` application log — full methodology and numbers in [`docs/dev/agent-cli-analysis-and-plan.md`](docs/dev/agent-cli-analysis-and-plan.md)):

- The same underlying events, expressed as an aggregate (`records`) query vs. raw messages, differed by **13–39x** in output size across the datasets tested — collapsing events into a `count by`/`sum()`/`avg()` aggregate server-side is the single biggest token-cost lever available, well before any formatting choice.
- For `records` output specifically, **CSV came out ~2.3x smaller than raw JSON** for the same rows — dropping repeated key names and JSON punctuation. Prefer CSV (or an equivalent flat/columnar text format) over JSON when the result is aggregate data headed for an LLM context.
- `| fields` does **not** reliably shrink raw-message (`messages`) output — on an account with a large field-extraction-rule catalog it left the full row envelope intact, and a broader `| fields`/`json auto` clause actually inflated it by triggering a global field union across every field the account has ever defined. If raw messages are unavoidable, trim fields **client-side after fetch**, not via an in-query `| fields`/`json auto` clause.

### Suggested approach

1. Default to an aggregate query (`count by`, `sum()`, `avg()`, ...) with `requires_raw_messages=False` unless the caller genuinely needs individual log lines — see the example above and `skills/common-query-patterns/SKILL.md`.
2. Extract only the specific fields actually needed from JSON `_raw` payloads by name (`| json field=_raw "x" as x`), not `| json auto` — auto-parsing can pull in every field the account has ever defined, not just what's in the current row.
3. Cap every aggregate with `| sort ... | limit N` or `| topk(N, ...)` — an uncapped `count by` over a high-cardinality field can still return thousands of rows.
4. When rendering `records` results for an LLM/agent caller, prefer CSV over JSON; for `messages` results, project down to the fields actually needed after fetching rather than relying on the query to have already done it.
5. If the search itself is automated/scheduled rather than one-shot, match run frequency to time range — the same query, run more often than its own window, rescans overlapping data every run. A 3-hour window searched every minute has a **scan ratio of 180x** the data actually needed; keep the ratio near 1x (last 1h → run hourly, last 15m → run every 15 minutes). On Flex or Infrequent tier that ratio is a direct multiplier on credits; on any tier it's unnecessary account load. For a recurring aggregate that must run more often than its window, put a scheduled view behind it instead of re-scanning raw logs — see [`skills/scheduled-views-overview/SKILL.md`](skills/scheduled-views-overview/SKILL.md).

See [`skills/ai-agent-result-shaping/SKILL.md`](skills/ai-agent-result-shaping/SKILL.md) for the general query-shaping principles this analysis confirms, and [`docs/dev/agent-cli-analysis-and-plan.md`](docs/dev/agent-cli-analysis-and-plan.md) for the full per-format token measurements and an agent-oriented CLI design built on these findings.

## Quickstart: `sumo_search_client.py`

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

Python 3.10+ (uses `X | None` union syntax and `dataclasses`). Auth is HTTP Basic — Access ID as username, Access Key as password. Never hardcode credentials; read them from the environment or a secrets manager.

### Aggregate query (count by field)

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

The client auto-detects `records` vs. `messages` from the job's `recordCount`/`messageCount` — you never need to tell it which one your query produces.

Region endpoints:

| Region | Endpoint |
| --- | --- |
| US1 | `https://api.sumologic.com` |
| US2 | `https://api.us2.sumologic.com` |
| AU | `https://api.au.sumologic.com` |
| CA | `https://api.ca.sumologic.com` |
| DE | `https://api.de.sumologic.com` |
| EU | `https://api.eu.sumologic.com` |
| FED | `https://api.fed.sumologic.com` |
| IN | `https://api.in.sumologic.com` |
| JP | `https://api.jp.sumologic.com` |
| KR | `https://api.kr.sumologic.com` |

The `sumosearch` CLI accepts these as case-insensitive aliases (`--endpoint
us2`) — see [`cli/README.md`](cli/README.md#region-aliases) — and can manage
multiple named instances/contexts across regions/orgs at once, see
[`cli/README.md`](cli/README.md#multiple-instances).

## Quickstart: `sumo_dashboard_client.py`

Same dependency (`requests`) and credentials as `sumo_search_client.py` above — copy both files together, since this client imports `resolve_time()` from the search client.

```python
import os
from sumo_dashboard_client import (
    SumoDashboardClient, build_report_body, default_variable_values,
    poll_report_job, resolve_report_time_range,
)

client = SumoDashboardClient(
    access_id=os.environ["SUMO_ACCESS_ID"],
    access_key=os.environ["SUMO_ACCESS_KEY"],
    endpoint=os.environ.get("SUMO_ENDPOINT", "https://api.sumologic.com"),
)

dashboard_id = "000000000ABC123"
dashboard = client.get_dashboard(dashboard_id)          # needed for variable defaults below
time_range, _, _ = resolve_report_time_range(hours=24, from_time=None, to_time=None)

body = build_report_body(
    export_format="pdf", mode="snapshot", theme=None, export_width=None,
    timezone_name="UTC", dashboard_id=dashboard_id, time_range=time_range,
    variables=default_variable_values(dashboard), panel_overrides=[],
)
job_id = client.create_report_job(body)
poll_report_job(client, job_id)                          # blocks until Success/Failed
result = client.get_report_result(job_id)

with open(f"dashboard.{result.ext}", "wb") as f:
    f.write(result.content)
```

To describe a dashboard's structure instead of rendering it — no report job, just the fetched object:

```python
from cli.dashboard_describe import describe_dashboard_queries

print(describe_dashboard_queries(dashboard))   # variables, panels, and every panel's query text
```

See [Dashboard reports: export and discovery](#dashboard-reports-export-and-discovery) above for the two agentic use cases this supports, and [`cli/README.md`](cli/README.md#report-run) for the equivalent `sumosearch report run`/`report describe` CLI commands (same underlying gotchas — e.g. default variable values — documented there).

## Quickstart: the `sumosearch` CLI

The CLI doesn't replace either existing path — it's the missing third leg for the specific case of running a search or answering a discovery question *from a shell*. The query-authoring skills in `skills/` apply unchanged across all three; only the transport/output layer differs. See [`docs/dev/agent-cli-analysis-and-plan.md`](docs/dev/agent-cli-analysis-and-plan.md) for the full design rationale and empirical token-cost measurements behind these choices.

Install `sumosearch` as an isolated tool, on its own PATH entry, from the root of this repo:

```bash
uv tool install . --with typer --with pyyaml     # installs the `sumosearch` command on your PATH

# after pulling new commits, reinstall to pick up the changes:
uv tool install . --with typer --with pyyaml --force
```

`--with typer --with pyyaml` is needed because both live in the opt-in `cli` dependency group, which `uv tool install` doesn't pull in on its own.

Working inside a checkout of this repo instead (e.g. for development)? Use the project-local install shown in [`cli/README.md`](cli/README.md#install):

```bash
uv sync --group cli
```

Credentials come from the same environment variables as the Python client (see [Quickstart](#quickstart-sumo_search_clientpy) above) — never as CLI flags, so they don't end up in shell history.

```bash
# Run a search job (create -> poll -> fetch -> delete); ndjson by default
# for raw-message results, csv by default for aggregate/records results.
uv run sumosearch search run '_sourceCategory=prod/app error' --from -1h --to now

# Profile a query's field schema from a small sample without hand-writing one
uv run sumosearch schema '_sourceCategory=prod/app' --from -1h --to now

# Bulk export straight to disk (time-splits automatically past ~80k rows)
uv run sumosearch export '_sourceCategory=prod/app error' --from -24h --to now \
    --format csv --out events.csv

# Export a dashboard to PDF, and describe its panel queries — see
# "Dashboard reports: export and discovery" above
uv run sumosearch report run <dashboard-id> --hours 24 --format pdf
uv run sumosearch report describe <dashboard-id> --queries
```

Full command list, every flag, output-format defaults, and the token-budget controls (`--max-tokens`, `--drop-null-columns`, the stderr warning): see [`cli/README.md`](cli/README.md).

## Development & Testing

This is a self-contained [uv](https://docs.astral.sh/uv/) project.

```bash
uv sync --group dev          # installs requests + pytest + ruff into ./.venv

uv run pytest                # unit tests — no credentials, no network
uv run pytest -k retry       # run a subset by keyword

uv run ruff check .          # lint
```

`tests/test_sumo_search_client.py` and `tests/test_sumo_dashboard_client.py` fake HTTP via the `session=` parameter each client's `__init__` already accepts, so they exercise the client's real request-building, retry, polling, and pagination logic rather than a re-implementation of it. `tests/test_dashboard_describe.py` covers `cli/dashboard_describe.py`'s pure summarization functions against hand-built dashboard fixtures.

```bash
# Integration tests — exercise the full create -> poll -> fetch -> delete
# lifecycle against a real org. Needs SUMO_ACCESS_ID / SUMO_ACCESS_KEY.
# Not run in CI. --dry-run skips the live calls.
uv run python tests/integration_test_sumo_search_client.py
uv run python tests/integration_test_sumo_search_client.py --dry-run

# Same, for the dashboard report client — needs a real dashboard id too.
uv run python tests/integration_test_sumo_dashboard_client.py
uv run python tests/integration_test_sumo_dashboard_client.py --dry-run
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for PR expectations.

## License

[MIT](LICENSE).
