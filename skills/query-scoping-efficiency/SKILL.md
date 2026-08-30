---
name: query-scoping-efficiency
description: >
  Structure a Sumo Logic query to minimize scan cost and latency before
  running it via the Search Job API — partition scope, bloom-filter
  keyword expressions, index-time (FER) fields vs. search-time parsing,
  and short time windows. Use when writing any new query against this
  client, when a query is slow or scans more than expected, or before
  running anything on a Flex or Infrequent-tier account. Triggers on:
  "write an efficient query", "reduce scan cost", "avoid scanning all
  data", "keyword expression vs where matches", "bloom filter",
  "_datatier=all", "partition scope", "index-time field", "this query is
  expensive", "minimize credits", "how do I scope this search", "push
  down optimization", "where matches _raw", "unbracketed OR", "filter by
  time in query vs job time range".
---

## Why scope is the #1 cost lever

Scan cost only applies on **Flex** (every byte scanned, any tier, billed
per GB) and **Tiered plans' Infrequent tier** (credits per GB). Tiered
Continuous-tier scans are not billed per query. Regardless of billing
model, scope also controls latency — an unscoped query touches every
partition the credential can see.

**Never scope with `_datatier=all` or a bare `*`/keyword with no
partition.** Both force a scan across every partition, including
Infrequent/Flex data, even if only one partition actually holds the
answer. Always add `_index=<partition>` (equivalently `_view=<partition>`)
as the first token in the query.

## The four levers, in priority order

1. **Partition and source category scope** — `_index=<name>` narrows to
   one (or a few) partitions before anything else runs; this is the
   single highest-impact change available, and should be applied before
   touching keywords or time range. `_sourceCategory=<value>` deserves
   equal priority, not a distant second: in most Sumo orgs, partition
   routing is itself configured by source category, so even a partial
   `_sourceCategory` (e.g. `prod/*`) will typically prune the same
   partitions `_index=` would have, and it's often known when the exact
   partition name isn't. **A well-scoped query should include
   `_sourceCategory`, `_index`/`_view`, or both — never neither.**
2. **Bloom-filter keyword expressions** — literal tokens placed in the
   scope line (before the first `|`) are checked against a bloom filter
   index and reject non-matching events *before* any parsing or compute
   happens. Two forms, both scope-line-fast:
   - **Against built-in/index-time metadata**, as `field=value`, wildcards
     included: `_sourceCategory=abc`, `_sourceCategory=prod/*/foo`,
     `_sourceHost=web-*`.
   - **Against the full-text bloom index**, as a bare or wildcarded token:
     `foo`, `foo*bar`, `/myapi/endpoint/*`, `/myapi/string*`, `foo?bar`
     (`?` matches exactly one character), `"some words together"` (quoted
     — matches the words in that exact order), `"\"errorCode\":\"AccessDenied\""`
     (escaped quotes to match a literal `"..."` substring, e.g. a JSON
     key/value pair).
   - **Both forms are case-insensitive**, unlike `| where field matches
     "pattern"` — a literal `Web-01` matches `_sourceHost=web-*` but
     silently fails `| where _sourceHost matches "web-*"`. This is a
     common source of a filter that "should" match returning nothing.
   **Do not confuse either form with `| where field matches "*pattern*"`**
   — `where`/`matches` runs *after* parsing, against every event already
   in scope, gains nothing from the bloom filter, and is case-sensitive.
   Reserve `matches` for filters that genuinely need regex logic the
   bloom filter can't express (e.g. capture groups); prefer a scope-line
   keyword/field expression whenever the target is a literal or simple
   wildcard, on metadata or full text alike. Selectivity is what makes a
   keyword pay off — a highly unique literal (a GUID, an email address,
   an IP, an order ID) can reject nearly every non-matching event before
   any parsing runs, so put those in the scope line even when they'd
   otherwise only appear inside a later `| where`/`| json` filter.
   **Rule of thumb for any query:** (a) always include `_sourceCategory`,
   `_index`/`_view`, or both, and (b) add one or a few keyword
   expressions on top — the more selective, the bigger the win.
3. **Index-time (FER) fields over search-time parsing** — a Field
   Extraction Rule (FER) extracts a field at ingest time and stores it
   with the message; using that field in the scope line (`_index=x
   status_code=500`) gets the same bloom-filter-speed treatment as a
   keyword. A field extracted with `| json`/`| parse` at search time
   requires the engine to scan and parse every event in scope first —
   more flexible (no ingest-time setup required) but strictly slower.
   Prefer an existing FER field in scope; fall back to `| parse`/`| json`
   only for fields that aren't index-time-extracted.
4. **Short time ranges** — time range is a direct multiplier on scan
   volume. Validate a new query on a short window (e.g. 15 minutes) before
   widening to the real range you need.

## Push-down optimization — and why you shouldn't rely on it

Sumo's query engine can sometimes rewrite a post-parse equality filter
like `| where foo = "500"` into an implicit scope-line keyword check for
the literal `"500"` — this is push-down optimization, and when it fires,
the query gets bloom-filter pre-filtering for free even though the
filter was written after a `| json`/`| parse` stage. Two things make this
unsafe to depend on rather than a reason to skip manual scoping:

- **It never applies to `matches`.** `| where foo matches "*/a/bar/*"`
  gets no automatic push-down under any circumstances — the literal
  substrings inside the pattern (`bar`) have to be added to the scope
  line by hand to get any pre-filter benefit at all:
  ```
  -- Slow: every event in scope gets parsed and regex-matched
  <scope> | json foo | where foo matches "*/a/bar/*"

  -- Fast: "bar" pre-filters at the bloom-filter stage before parsing
  <scope> bar | json foo | where foo matches "*/a/bar/*"
  ```
- **Even where it can apply, it's not guaranteed.** Whether `| where foo
  = "500"` actually gets pushed down depends on the query shape and isn't
  something to assume; adding the literal to the scope line explicitly
  gets the same result deterministically:
  ```
  <scope> 500 | json status_code | where status_code = "500"
  ```
- **Adding just the JSON key name as a keyword also helps, independent of
  the value.** Push-down doesn't reach across a parse operator to ask "does
  this event even have this key" — but a scope-line keyword can, and when
  only a small fraction of events in scope carry a given key, that keyword
  alone eliminates most of the parsing work:
  ```
  -- Slower: every cloudtrail event gets parsed even though few have errorCode
  _sourceCategory=cloudtrail | json "errorCode" | where errorCode matches "*denied"

  -- Faster: only events containing the literal "errorCode" get parsed
  _sourceCategory=cloudtrail errorCode | json "errorCode" | where errorCode matches "*denied"
  ```

**Takeaway:** don't write a query assuming the optimizer will rescue an
un-scoped `| where`/`| json` pattern. Add the literal (value, or just the
field/key name when the value isn't known to be selective) to the scope
line explicitly — it's free insurance whether or not push-down would have
fired anyway.

## Common mistakes that silently break scoping

These show up often enough in agent-written queries to call out
explicitly — each one produces a query that "works" (returns a plausible
result set) while quietly reintroducing the cost or correctness problems
this skill exists to prevent.

**1. `| where _raw matches "..."` instead of a scope-line keyword.**
`_raw` is already the fully bloom-indexed message text — filtering it
with `matches` after scope resolution throws that away and forces the
engine to re-evaluate the full raw text of every event already in scope,
which is extremely high I/O for what a keyword does for free:
```
-- Bad: high I/O, ignores the bloom filter entirely
<scope> | where _raw matches "*foo*"
<scope> | where _raw matches /foo/

-- Good: same result, pre-filtered before any per-event evaluation
<scope> foo
```

**2. Missing parentheses around `OR`.** `AND` binds tighter than `OR`, so
`foo OR bar and foobar` parses as `(foo) OR (bar AND foobar)` — rarely
the intended grouping. In a scope line this is worse than a logic bug: it
silently un-scopes part of the query. `_sourceCategory=a foo OR bar`
parses as `(_sourceCategory=a foo) OR bar`, so the `bar` branch runs
against the entire default search scope, not `_sourceCategory=a` — the
exact unscoped-query problem this skill warns against, hidden inside a
query that otherwise looks scoped. Always bracket explicit `OR` branches:
```
_sourceCategory=a (foo OR bar)
```

**3. Filtering time inside the query instead of the job's time window.**
`| where _messageTime > x and _messageTime < y` (or `_receiptTime`/
`_searchableTime`) returns a correct result but is far less efficient
than setting the actual job time window — a `| where` on a time field
still scans and evaluates every event in whatever range was requested,
where a job-level window lets the engine prune at the storage/partition
level before that. If receipt-time or searchable-time semantics are
needed, use the Search Job POST payload's own `byReceiptTime`/
`bySearchableTime` boolean flags (default: run by message time) instead
of filtering a time field in the query body — there's essentially no
legitimate reason to see `| where _receiptTime > ...` in a query.

## Pre-flight: check before you run

Before running an unfamiliar or expensive-looking query, use
`estimate_scan()` — a synchronous endpoint that returns the scan size
without creating a job (no polling, no delete, no scan cost of its own).
It also validates query syntax and partition names for free (HTTP 400 on
either failure).

```python
import time

to_ms = int(time.time() * 1000)
from_ms = to_ms - 24 * 3_600_000

estimate = client.estimate_scan(
    query='_index=prod_logs _sourceCategory=prod/app error',
    from_ms=from_ms, to_ms=to_ms,
)
print(f"{estimate.total_bytes / 1e9:.2f} GB")
```

Compare a scoped vs. unscoped estimate on the same query to make the
savings concrete before recommending a fix.

## Quick Reference

```
-- Bad: unscoped, no partition, no keyword pre-filter
* error AccessDenied

-- Bad: partition scoped but filter deferred to search time
_index=prod_app
| json "errorCode" as error_code
| where error_code = "AccessDenied"

-- Good: partition + bloom-filter keyword + index-time field, filter
-- happens before any parsing
_index=prod_app "\"errorCode\":\"AccessDenied\"" errorCode=AccessDenied
```

## Key Rules

- Order scope tokens by decreasing selectivity: `_index=` first, then
  index-time field filters, then keyword literals — each rejects more of
  what's left.
- A keyword or field=value expression can be a literal or a `*`/`?`
  wildcard and still get bloom-filter treatment (`"AccessDenied"`,
  `foo*bar`, `_sourceHost=web-*` all qualify) — what it *cannot* be is a
  regex; regex logic requires `| where field matches "..."` post-parse
  and loses the bloom filter.
- `_datatier=all` and bare `*` are the two highest-risk unscoped patterns —
  flag both immediately in any query you review.
- On Continuous-tier-only (Tiered) accounts, scope still matters for
  latency even though it's not billed per scan.
- Run `estimate_scan()` before widening time range on any query touching
  Flex or Infrequent-tier data.
- Never assume push-down optimization will rescue an un-scoped `| where`/
  `matches` filter — add the literal (or just the key name) to the scope
  line explicitly; it never hurts and `matches` never gets push-down at all.
- Bracket every explicit `OR` in a scope line — an unbracketed `OR` can
  silently drop scope for one branch, not just misapply logic.
- Set the job's actual time window (`from_time`/`to_time`,
  `byReceiptTime`/`bySearchableTime`) — never filter `_messageTime`/
  `_receiptTime`/`_searchableTime` with `| where` inside the query.
- Never filter `_raw` with `matches` — it's already bloom-indexed; use a
  scope-line keyword instead.

## Related Skills (this folder)

- `discovery-without-metadata` — find the right partition/scope when it's
  not already known.
- `discovery-profile-scope` — sample and confirm schema once scope is
  known, before writing the real query.
- `operator-ordering` — where filters, parse, and aggregation belong once
  scope is set.
- `ai-agent-result-shaping` — keep the *result* small once the scan itself
  is efficient.
