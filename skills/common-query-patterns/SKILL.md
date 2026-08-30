---
name: common-query-patterns
description: >
  Copy-paste query templates for the four most common Sumo Logic aggregate
  shapes — categorical aggregate, time series, multi-series time series
  with transpose, and time-compare — plus how each maps to the Search Job
  API's records vs. messages result type. Use when building any of these
  four shapes from scratch. Triggers on: "count by field", "time series
  query", "multi-series chart query", "transpose", "compare to last week",
  "day over day comparison", "aggregate query template", "how do I build
  a timeseries query".
---

## All four patterns are aggregate queries

Every pattern below has an aggregate tail (`count`, `sum`, `avg`, ...) and
therefore produces **records**, not raw messages — call
`run_search(..., requires_raw_messages=False)` and read
`result.items[i]["map"]`. All map values come back as strings; cast before
arithmetic (`int(m["_count"])`, `float(m["avg_ms"])`).

## 1. Categorical aggregate — count/sum by field

### 1a. Single aggregate, single group-by field

```
_index=prod_app _sourceCategory=prod/checkout error
| json "errorCode" as error_code
| count by error_code
| sort _count desc
| limit 20
```

Use for: top-N breakdowns, error/status distributions, ranking dimensions.
Swap `count` for `sum(<field>)`, `avg(<field>)`, `pct(<field>, 95)` etc.
for numeric aggregates instead of event counts.

### 1b. Multiple aggregates, multiple group-by fields

```
_index=prod_app _sourceCategory=prod/checkout
| json "errorCode" as error_code
| json "durationMs" as duration_ms
| sum(duration_ms) as total_ms, count as events, max(duration_ms) as max_ms
  by _sourceCategory, error_code, region
| sort events desc
| limit 20
```

A single `by` clause can group on several fields (`by field1, field2,
field3`), and the aggregate list before `by` can hold several operators
at once (`sum(x) as x, count as events, max(x) as max_x`) — one query,
one job, one table with all the aggregates as columns instead of running
a separate query per metric or per group-by field.

## 2. Time series — single metric over time

```
_index=prod_app _sourceCategory=prod/checkout
| timeslice 5m
| count by _timeslice
| sort _timeslice asc
```

Use for: a single trend line (request volume, error count, latency avg
over time). `timeslice` bucket size should scale with time range — see
the granularity table below. Always `sort _timeslice asc`; result order
is not otherwise guaranteed.

| Time range | Suggested `timeslice` |
|---|---|
| < 1 hour | `1m`–`5m` |
| 1–24 hours | `15m`–`1h` |
| 1–7 days | `1h`–`6h` |
| > 7 days | `1d` |

## 3. Multi-series time series — with transpose

```
_index=prod_app _sourceCategory=prod/checkout
| json "errorCode" as error_code
| timeslice 5m
| count by _timeslice, error_code
| transpose row _timeslice column error_code
```

Use for: one line per category (e.g. one series per `error_code`) instead
of a single blended trend. `transpose` pivots the category dimension into
separate columns (`error_code_AccessDenied`, `error_code_Throttling...`) —
without it, charting libraries see one flat table instead of distinct
series. Aggregate first (`count by _timeslice, error_code`), transpose
last — transposing before aggregating produces the wrong shape.

## 4. Time compare — this period vs. a prior period

### 4a. Without `_timeslice` — one comparison, two totals

```
_index=prod_app
| count as events by _sourceCategory
| compare with timeshift 7d
```

No time-series bucketing here — each `_sourceCategory` gets one total for
the current range and one for the range shifted back 7 days, side by
side. Use this when you want a single before/after number per category,
not a trend line.

### 4b. With `_timeslice` — a compared trend line

```
_index=prod_app _sourceCategory=prod/checkout
| timeslice 1h
| count by _timeslice
| compare with timeshift 24h
```

Bucketing with `timeslice` first means each bucket in the current period
is compared against the same relative bucket 24h earlier — this produces
two parallel trend lines (current vs. prior), not just two totals. Use
this when the caller wants to see *where* the periods diverge, not just
whether they diverge.

In both forms, `compare with timeshift <duration>` re-runs the same
aggregate shifted back by `<duration>` and adds a parallel `_count_1d`-style
column next to the current one — day-over-day or week-over-week comparison
in a single query and a single job, rather than two separate
`run_search()` calls diffed client-side. Use a `timeshift` that matches
the seasonality you expect (`24h` for daily, `7d` for weekly-cyclic
traffic).

**When to skip `compare with timeshift` and run separate searches
instead:** an agent caller isn't limited to one job per comparison — it
can just as easily issue two (or more) `run_search()` calls over
different explicit `from_time`/`to_time` ranges and diff the results
itself. Prefer that approach when the periods need different scope or
filters (e.g. comparing prod traffic this week against a differently-
scoped staging baseline), when more than two periods are being compared,
or when the caller wants the raw per-period results for further
processing rather than a single pre-diffed table. Prefer the built-in
`compare with timeshift` when the shape is a simple same-scope
before/after and a single job is preferable to running multiples.

## Key Rules

- Set `requires_raw_messages=False` on every one of these — none of them
  need raw messages retained.
- `sort` before `limit`/`topk` on any categorical aggregate, or the
  returned rows are an arbitrary sample rather than the top N.
- Match `timeslice` granularity to time range (table above) — too fine a
  slice on a long range returns thousands of near-empty rows.
- For `transpose`, aggregate on `_timeslice` + the category field together
  in one `count by`/`sum by` clause before transposing.
- `compare with timeshift` runs as one job — prefer it over two manual
  `run_search()` calls for the same-shape comparison; only fall back to
  two calls when the two periods need genuinely different scope/filters.

## Related Skills (this folder)

- `operator-ordering` — where these aggregate/format operators sit
  relative to scope, parse, and filter.
- `ai-agent-result-shaping` — capping row count and field list on any of
  these patterns before returning to an agent/LLM caller.
