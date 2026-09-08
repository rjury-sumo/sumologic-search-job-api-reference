---
name: discovery-dashboard-reuse
description: >
  Find dashboards already relevant to a use case and mine their panels for
  known-good query text — the "Reuse" stage of the log search journey, done
  programmatically instead of browsing Library/Apps in the UI. Covers
  `sumosearch discover dashboards --match/--grep` to locate one or a few
  candidate dashboards, then `report describe --queries` to extract each
  panel's actual search text (filtered to log queries; Metrics/Tracing
  panels are out of scope), using panel/dashboard title and description for
  use-case context. Use either to pull a diverse sample of query shapes when
  building a skill or reference for a log source, or to pull a few example
  queries to infer likely metadata (`_sourceCategory`/`_index`) and field
  structure for a specific ask. Most orgs have Sumo apps or custom
  dashboards for their key platforms/services, so this is usually the
  fastest path to a real, human-validated starting query. Triggers on:
  "is there already a dashboard for this", "find a dashboard about X", "reuse
  an existing search", "mine dashboards for query examples", "what searches
  exist for this log source", "starting point query for Y", "known-good
  query for this platform".
---

## Why this exists

Journey Stage 1 ("Reuse") in `log-search-journey` says to check
Library/Apps for a solved or close-relative use case before scoping,
sampling, or crafting a search from scratch. In the UI that's a manual
browse; this skill is the programmatic equivalent — locate the right
dashboard(s) by keyword/relevance search, then read off each panel's real
query text directly, with no query execution and no rendering.

It works because dashboards are human-curated: an org's own users build
them around a specific platform, service, or business process, and each
panel already encodes a previously validated search with a title that
names its purpose. Most orgs have either an imported Sumo Logic app or
custom-built content around their key applications and services, so a
relevant dashboard is very often already sitting in the org, visible to
any user with access to it.

This is **specific to this repo's `sumo_dashboard_client.py` /
`sumosearch` CLI** — Sumo's standard `runSearchJob` MCP tool has no
dashboard-listing or describe equivalent (see root `README.md`'s
"Dashboard reports" positioning table), so this technique isn't portable
to a bare Search Job API / MCP-only session the way most other skills in
this repo are.

## The two things this pulls, and when to use each

**A. A diverse sample of query *shapes*, for building a skill/reference.**
When the goal is "teach an agent how to query <platform/log source> in
general," pull a few panels' queries and deliberately favor a *mix* of
shapes rather than the first ones found:

- a **categorical** aggregate (`count by <field>`/`sum(...) by <field>`,
  no `timeslice`)
- a **time series** (`timeslice` present, one series)
- a **multi-series time series with `transpose`** (one field's value
  broken out into columns over time — e.g. count by status code over
  time) — these are the most informative for schema/field discovery
  since the field being transposed is usually the single most important
  categorical field for the use case
- any **rarer panel shape** the dashboard happens to have — single value,
  map, box plot, outlier. `report describe` cannot tell you a panel's
  visualization type directly (see the gotcha below) — panel title is the
  best signal (e.g. "Total Errors" → single value, "Traffic by Region" →
  map, "Latency Distribution" → box plot); confirm by reading the query
  shape once found.

**B. A few example queries, to infer metadata and field structure for a
specific use case.** When the goal is "I want to search for X / for use
case Y" rather than build general reference material, a handful of panel
queries from one relevant dashboard already answer:

- likely `_sourceCategory`/`_index` scope for this data (read straight off
  each query's scope line)
- whether the log format is JSON, key-value, or something else, and which
  fields are already parsed vs. extracted at search time — read the
  `parse`/`json`/`parse regex` clauses, if any, in each query

## Gotcha: `report describe` has no panel-visualization-type field

`panelType` on every search-backed panel is generically `SumoSearchPanel`
regardless of whether it renders as a line chart, table, single value,
map, or box plot — the actual chart/viewer type isn't captured by
`describe_dashboard_queries()`/`report describe`. Don't rely on
`panelType`/`panel_types` counts to sort panels by visualization shape;
infer shape from the **query text** itself (see `common-query-patterns`
for the categorical/time-series/transpose/time-compare shapes and how
each maps to a query pattern) and from the **panel title**, not from any
field `report describe` returns directly.

## Gotcha: `{{variables}}` in extracted query text

A dashboard can define variables (shown in `report describe`'s summary
output, with each variable's saved `defaultValue`); panel queries
reference them as literal `{{varname}}` tokens, substituted only at
report-render time — `report describe --queries` returns the raw,
unsubstituted text. When lifting a query as a starting point:

- swap `{{varname}}` for that variable's `defaultValue` from the summary
  output, if a concrete example is wanted, or
- swap it for a value relevant to the current investigation, or
- if the variable gates an entire filter clause and no substitution makes
  sense, drop that clause and treat the rest of the query as the
  reusable skeleton.

Either way, don't hand a query containing a literal `{{...}}` token
onward as if it were directly runnable.

## Workflow

**1. Find candidate dashboards.** Use `--match` for open-ended/fuzzy
queries where the exact title is unknown (ranked relevance across
`title`/`description`/`domain`); use `--grep` instead when a specific
word is already known (fast exact substring filter). Add `--show-score`
with `--match` to see ranking confidence:

```bash
sumosearch discover dashboards --match "AWS WAF Security" --limit 10 --show-score
```

```
id,contentId,title,description,folderId,domain,score
GVEQ...dbx2tB5tGGO4sL,00000000067E8425,AWS WAF - Security Monitoring - Overview,"This dashboard serves as an overarching summary of AWS WAF data and general trends...",00000000067EAAC9,,5.857
```

Read `title`/`description` to judge relevance before spending a
`report describe` call — the description alone is often enough to
confirm this is the right dashboard for the use case.

**2. Confirm shape and variables cheaply first.** No flags = summary
only (top-level properties, variables with saved defaults, panel count
and `panel_types`, layout grid) — a single small object, no query text
yet:

```bash
sumosearch report describe GVEQ...dbx2tB5tGGO4sL
```

**3. Pull the queries.** `--queries` returns one compact row per query
(panel title + `queryKey`/`queryType`/query text, `--queries` implies
`--panels`):

```bash
sumosearch report describe GVEQ...dbx2tB5tGGO4sL --queries
```

**Filter to `queryType == "Logs"` only** — a panel can also carry
Metrics or Tracing queries, which are out of scope for this reference
client (Search Job API / log search only). Use the panel title alongside
each query's text for use-case context — it's often more descriptive of
intent than the query itself (e.g. "Blocked Requests by Rule Group" tells
you this is a categorical `count by` over a rule-group field before you
even read the query).

If a quick sanity check on panel mix is useful before pulling every
query, `--panels` alone (no `--queries`) gives per-panel rows (title,
`panelType`, `query_count`, `variables_referenced`) without the query
text — cheaper if only judging relevance/shape mix rather than reading
the actual searches.

## Caching: reuse it, don't re-pull

`GET /v2/dashboards` has no server-side search parameter — `discover
dashboards` always pulls the *entire* org-visible dashboard list
(paginated 100/page) and filters client-side. In a large org this can be
1000s of dashboards and take 30-60+ seconds. The unfiltered list is
cached to disk per instance+`--mode` and reused for 24h; every
`--grep`/`--match`/`--limit` call in that window filters the cached list
instead of re-pulling.

**Practical implication:** run several `--grep`/`--match` searches back
to back in one session freely — only the *first* one pays the pull cost.
Only pass `--no-cache` when new dashboard content was just published and
is specifically the target — not as a routine flag on every call.

## Example flow end to end

```bash
# 1. Locate candidate dashboard(s) for the use case
sumosearch discover dashboards --match "AWS WAF Security" --limit 10 --show-score

# 2. Confirm shape/variables cheaply before pulling query text
sumosearch report describe <dashboard-id>

# 3. Pull every panel's query text, filter to queryType=Logs,
#    pick a diverse sample (categorical / time series / transpose / rare)
sumosearch report describe <dashboard-id> --queries
```

## Related Skills (this folder)

None — this is a single-concern skill.

## Related Skills (other folders)

- `log-search-journey` — Stage 1 ("Reuse") routes here; this skill is the
  programmatic version of "check Library/Apps first."
- `common-query-patterns` — the categorical/time-series/transpose/
  time-compare shapes referenced above when sorting extracted queries by
  panel shape.
- `discovery-without-metadata` / `discovery-profile-scope` — the fallback
  path once no relevant dashboard exists, or once a dashboard query only
  gets you partway (e.g. confirms `_sourceCategory` but not full field
  schema).
- `search-indexes-partitions` — `sumologic_search_usage_per_query` is a
  complementary, admin/power-user-only source of "known good" prior
  queries (mining the search-audit log itself rather than dashboard
  content) — see its `references/sumologic-search-usage.md`.
- `discovery-log-domains` / `log-domain-skill-authoring` — if this
  technology is likely to come up repeatedly, the query sample this
  skill produces is exactly what `log-domain-skill-authoring` persists
  into a reusable per-instance file, so the next request skips this step
  entirely. Check `discovery-log-domains` first — a prior run may have
  already done this.
