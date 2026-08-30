---
name: operator-ordering
description: >
  Canonical operator order for a Sumo Logic query pipeline — scope, early
  filter, parse, late filter, aggregate, format — and why each position
  affects both correctness and scan cost. Use when constructing or
  reviewing any query's structure, or when a query returns wrong results
  because a filter or limit is in the wrong position. Triggers on: "what
  order should these operators go in", "where does where go", "query
  pipeline structure", "why is my limit not reducing scan", "restructure
  this query", "filter before or after aggregate".
---

## Canonical order

```
1. Scope line       (_index=, keywords, index-time fields — no leading |)
2. Early | where     (filter on raw/index-time fields, before parsing)
3. Parse             (| json, | parse, | csv — extract only what's needed)
4. Late | where       (filter on newly-parsed fields)
5. Aggregate         (| count, | timeslice, | avg, | sum, ...)
6. Post-aggregate    (| where on aggregate output, | sort, topk)
7. Format            (| fields, | limit, | transpose — always last)
```

Each stage should only do work the next stage actually needs — pushing a
filter as early as possible means less data flows into every stage after
it.

## Why position changes behavior, not just style

- **`| limit N` on the scope line vs. at the end of the pipeline are not
  equivalent.** `_index=x error | limit 500` on line 1 stops the scan
  itself once 500 matching events are found — it's cheap. `_index=x error
  | json ... | where ... | limit 500` at the end still scans and processes
  everything in scope first, then discards all but the last 500 rows —
  the `limit` there only trims output, it does not reduce scan.
- **A `| where` before `| parse` filters on fields that already exist**
  (raw fields, index-time/FER fields, metadata like `_sourceHost`) — cheap,
  runs before any parse cost is paid. A `| where` on a field that doesn't
  exist yet must come *after* the `| parse`/`| json` that creates it —
  putting it before silently matches nothing.
- **Prefer a scope-line keyword expression over an early `| where ...
  matches`.** `_sourceHost=web-*` on the scope line (stage 1) gets
  bloom-filter treatment, runs before the scan itself, and matches
  case-insensitively. `| where _sourceHost matches "web-*"` runs after
  scope resolution, is case-sensitive (a literal `Web-01` silently fails
  to match `web-*` — a common source of "why did this return nothing"
  bugs), and gains none of the scope-line speed. Reserve `matches`/`where`
  for logic a keyword expression genuinely can't express — see
  `query-scoping-efficiency` for the full keyword-expression syntax.
- **Filter before aggregate, always.** `_index=x | where status=500 |
  timeslice 5m | count by _timeslice` computes the count of only the
  filtered events. Reversing the order (`timeslice` then `where`) is
  usually invalid or wasteful — aggregate operators consume the event
  stream, they don't pass it through.
- **Aggregate before `lookup`.** Deduplicate/count down to the unique keys
  you actually need to enrich, then `lookup` only those — enriching every
  raw event first (`lookup` before aggregate) can be 100x–1000x more
  lookup calls than necessary.
- **`| sort` must precede `| limit`/`topk` on aggregate output**, or the
  rows returned are an arbitrary sample rather than the top/bottom N by
  the metric that matters.

## Key Rules

- Never put a cost-reducing `| limit` anywhere but the scope line — a
  `| limit` later in the pipeline only shrinks the *output*, not the scan.
- Reference an extracted field only after the operator that extracts it.
- One aggregate stage per pipeline in the common case — chaining a second
  aggregate over already-aggregated output (`| count` on the output of
  `| count by field`) is valid but easy to get backwards; re-aggregate
  with `sum(_count)`, not `count`, when re-summing an existing count column.
- Format operators (`fields`, `transpose`) go last — they reshape output,
  not the underlying computation, and reordering them earlier has no
  performance benefit and can break the aggregate stage that follows.
- Not sure which fields are index-time (FER-extracted, collector/source-
  tagged, or HTTPS/OTLP header-derived) vs. search-time-parsed? Check
  before assuming a filter needs `| parse`/`| json` first — see
  `discovery-profile-scope`'s index-time field discovery step (FER API,
  or a raw sample with `autoParsingMode: "Manual"`).

## Quick Reference

```
_index=prod_app "checkout" _sourceHost=web-*   -- 1. scope: partition + keyword + index-time field wildcard
| where response_code >= 500               -- 2. early filter: numeric range on an index-time field, pre-parse
| json "status", "durationMs" as status, duration_ms   -- 3. parse
| where status >= 500 and duration_ms > 0  -- 4. late filter, post-parse
| timeslice 5m
| count as errors, avg(duration_ms) as avg_ms by _timeslice   -- 5. aggregate
| where errors > 0                         -- 6. post-aggregate filter
| sort _timeslice asc                      -- 7. format
```

`_sourceHost=web-*` sits on the scope line rather than in an early
`| where matches` because it's a plain wildcard match on a metadata
field — the scope line handles that case natively, faster and
case-insensitively. `response_code >= 500` stays as an early `| where`
because a numeric range isn't expressible as a keyword/scope-line
equality match, even though `response_code` is itself an index-time
field available pre-parse.

## Related Skills (this folder)

- `query-scoping-efficiency` — what belongs on the scope line and why,
  including the full keyword-expression syntax.
- `discovery-profile-scope` — find index-time fields (FER/collector-
  tagged/OTLP-header) before deciding a filter needs to wait for `| parse`.
- `common-query-patterns` — full templates for aggregate/timeseries/
  transpose/time-compare built on this ordering.
- `ai-agent-result-shaping` — the format stage in more depth for
  small-result-set, agent-facing queries.
