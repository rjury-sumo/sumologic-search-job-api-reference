---
name: ai-agent-result-shaping
description: >
  Shape a Sumo Logic query's output for an AI agent or MCP-style caller —
  pre-aggregate and filter in-query rather than in Python, cap result size
  with scope-line limit/topk/sort+limit, and trim fields — to keep token
  cost, response size, and latency small. Use whenever query results feed
  an LLM/agent context rather than a human dashboard. Triggers on:
  "reduce token usage", "results too large for the agent", "MCP context",
  "keep the response small", "this result set is huge", "agent-friendly
  query", "trim fields before returning", "cap row count for the model".
---

## The core principle

**Do the filtering, aggregation, and formatting inside the query — not in
Python after fetching.** Every row and field that comes back through
`fetch_all()`/`run_search()` costs response size, fetch time, and (if it
reaches an LLM) tokens. The Sumo Logic query language can pre-aggregate,
filter, sort, and select columns server-side for free; doing the
equivalent work client-side means paying to transfer data you're about to
throw away.

## Scope before you shape

Result shaping caps what comes back; scoping cuts what gets scanned in the
first place, and a smaller scan is also a smaller candidate set for
whatever aggregate/limit you apply next. Before applying the four levers
below, put every piece of known metadata on the **scope line**:

- **`_sourceCategory`, even partial.** If the exact category is unknown
  but a prefix is, use a wildcard rather than omitting it —
  `_sourceCategory=prod/*` still prunes everything outside `prod/` before
  the query engine does any parsing or aggregation. A vague scope beats no
  scope.
- **`_index`, if known.** Narrows the search to a single logical
  partition instead of scanning everything the credential can see.
- **Any other metadata or keyword the caller already knows** (`_collector`,
  `_sourceHost`, a literal keyword like `error` or a service name) —
  each additional expression on the scope line is a free filter applied
  before aggregation, not an extra cost.

This matters more for agent callers than humans: an LLM-driven caller
often has a vague-but-nonzero amount of metadata (a category prefix, a
partition name) and defaults to omitting it rather than guessing — that
default should flip. See `query-scoping-efficiency` for the full scan-cost
picture.

## Four levers, applied in order

1. **Prefer aggregate over raw messages.** `count by`, `sum()`, `avg()`
   collapse thousands of events into tens of rows server-side. Raw-message
   queries return one row per event — orders of magnitude larger for the
   same underlying data. Default to an aggregate shape unless the caller
   genuinely needs individual log lines (see `common-query-patterns`).

2. **Cap raw-message queries at the source.** When raw messages are
   required, put `| limit N` on the **scope line** (before the first
   `|`), not at the end of the pipeline — `_index=x error | limit 100`
   stops scanning once 100 matches are found; a `limit` placed later still
   scans everything in scope first. See `operator-ordering` for why
   position matters here.

3. **Cap aggregate queries after sorting.** `| sort _count desc | limit
   N` or `| topk(N, _count)` after the aggregate stage — `topk` is a
   single-pass operation and is faster than `sort` + `limit` when only the
   top N matter and the exact rank of items beyond N is irrelevant.
   Never return an aggregate with no cap — a `count by` over a
   high-cardinality field can return thousands of rows.

4. **Trim fields with `| fields`.** Drop everything the caller doesn't
   need before the query ends — `map` payloads carry only what's asked
   for, cutting both response size and downstream token cost.

## Client-side settings that matter

- `run_search(..., requires_raw_messages=False)` for any aggregate-only
  query — skips raw-message retention server-side, which is faster and
  can enable result caching for repeated overlapping-range aggregate
  queries.
- `run_search(..., limit=N)` / `fetch_all(..., limit=N)` caps how many
  rows the client pages through *after* the job completes — a second,
  independent cap from the in-query `| limit`/`topk`. Use the in-query cap
  as the primary control; the client-side `limit` is a safety net, not a
  substitute.
- Keep the time range as short as the question allows — shorter range
  means fewer underlying events to aggregate, which means both a cheaper
  scan (`query-scoping-efficiency`) and a smaller/faster result.

## Anti-pattern

```python
# Bad: fetch everything, then filter/aggregate in Python
result = client.run_search('_index=x error', from_time="-24h", to_time="now",
                           requires_raw_messages=True, limit=50000)
by_host = {}
for item in result.items:
    host = item["map"]["_sourcehost"]
    by_host[host] = by_host.get(host, 0) + 1
top10 = sorted(by_host.items(), key=lambda kv: -kv[1])[:10]
```

```python
# Good: push the aggregation into the query
result = client.run_search(
    '_index=x error | count by _sourceHost | sort _count desc | limit 10',
    from_time="-24h", to_time="now", requires_raw_messages=False,
)
top10 = [(r["map"]["_sourcehost"], int(r["map"]["_count"])) for r in result.items]
```

Same answer, one query instead of a 50,000-row fetch plus client-side
aggregation.

## Quick Reference

```
-- Raw sample, capped at the source
_index=x error | limit 100

-- Aggregate, capped after sort
_index=x | count by service | sort _count desc | limit 20

-- Aggregate, capped with topk (single pass)
_index=x | count by service | topk(20, _count)

-- Trimmed fields on final output
_index=x | count by service, region | sort _count desc | limit 20
| fields service, region, _count

-- Scoped with _sourceCategory only (no _index known) — exact category
_sourceCategory=prod/checkout error | count by error_code | sort _count desc | limit 20

-- Scoped with a vague _sourceCategory prefix — still narrows the scan
_sourceCategory=prod/* error | limit 100
```

## Related Skills (this folder)

- `query-scoping-efficiency` — reduce scan cost before the result-shaping
  stage even runs.
- `operator-ordering` — why scope-line `limit` and post-aggregate `limit`
  are not interchangeable.
- `common-query-patterns` — the aggregate/time-series shapes to apply
  these caps to.
