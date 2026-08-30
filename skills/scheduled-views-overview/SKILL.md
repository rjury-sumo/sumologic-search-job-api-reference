---
name: scheduled-views-overview
description: >
  High-level summary of Sumo Logic scheduled views — what they are, how
  to query one via the Search Job API, and when a recurring query is a
  good candidate to become one. Lightweight overview only, not a
  creation/management guide. Use when a recurring aggregate query is slow
  or expensive and pre-aggregation might help, or when a query targets
  `_view=` and its behavior needs explaining. Triggers on: "scheduled
  view", "_view=", "pre-aggregated data", "speed up this recurring
  query", "materialized view", "what is a scheduled view".
---

## What it is

A scheduled view continuously runs a saved aggregate query in the
background and stores the pre-computed results under its own partition
name (`_view=<indexName>`). Querying the view reads those already-computed
rows instead of re-scanning and re-aggregating raw logs on every run —
the aggregation cost is paid once by the view, not once per query.

## Querying an existing view

Same Search Job API as any other query — scope to the view like a
partition, filter on its indexed columns, and re-aggregate its stored
metric columns with `sum()`, not `count`:

```
_view=apache_status_code_1m_v2 status_code=500
| sum(_count) as errors by status_code
| timeslice 1h
```

- **Only `field=value` filters work** — no free-text keyword search, no
  extracting new fields. If a field the query needs isn't in the view's
  schema, the raw logs (or a new/updated view) are required instead.
- **Aggregate columns are pre-reduced** — use `sum(<column>)` to re-total
  an already-aggregated metric; a bare `count` counts pre-aggregated rows,
  not underlying events, and gives the wrong number.
- **~1 minute processing delay** — not suitable for sub-minute/real-time
  needs.

## When a recurring query is a good candidate

A query is a candidate for becoming (or already having) a scheduled view
when it's:
- **Run frequently** — a dashboard panel, monitor, or scheduled report
  re-executed on an interval, not a one-off investigation.
- **Aggregate-heavy, low-selectivity** — `count by`, trend, or percentile
  queries where most events in scope contribute to the result (as opposed
  to a high-selectivity lookup like a specific transaction ID, which
  bloom-filter keyword scoping already serves well — see
  `query-scoping-efficiency`).
- **Expensive at raw-log scan cost** — meaningful GB/credits scanned per
  run on a Flex or Infrequent-tier account; pre-aggregation amortizes that
  cost across every future run instead of paying it each time.

## Creating or managing views

Out of scope for this lightweight client and skill set — creating a
scheduled view is an admin action (Sumo Logic UI, or the Content
Management / Scheduled Views REST API), not something this Search Job
API client performs. If a recurring query looks like a strong candidate,
flag it and point to Sumo Logic's Scheduled Views documentation/API
rather than attempting to create one here.

## Key Rules

- Query a view like a partition scope (`_view=name field=value`), never
  with free-text keywords.
- Re-aggregate with `sum(<column>)`, never bare `count`, on pre-aggregated
  columns.
- Don't expect sub-minute freshness.
- Recommending a *new* view is a design suggestion to hand off, not an
  action this client can take.

## Related Skills (this folder)

- `query-scoping-efficiency` — the raw-log-scan alternative this trades
  against; use it to decide whether pre-aggregation is actually needed.
- `common-query-patterns` — the aggregate shapes a view typically
  pre-computes.
