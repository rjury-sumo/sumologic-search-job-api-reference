---
name: discovery-without-metadata
description: >
  Find the right partition and source category for a Sumo Logic query when
  none of that metadata is known yet — fastest first via the data-volume
  index or partition/FER admin endpoints when RBAC allows, falling back to a
  carefully-scoped Search Job API sequence when it doesn't. Use before
  writing any query when the caller only knows a technology or keyword, not
  a `_sourceCategory`/`_index`/`_view`. Ends once `_sourceCategory`/`_index`
  are locked in — NOT for sampling raw logs, confirming schema, or
  enumerating other metadata dimensions (`_collector`, `_sourceHost`) once a
  scope is known: use `discovery-profile-scope` for that. Triggers on: "I
  don't know where these logs are", "unknown source category", "which
  partition has X", "find the right index for", "discover logs for", "cold
  start on this data".
---

## The problem

Every technique in `query-scoping-efficiency` assumes you already know
`_index=`/`_sourceCategory=`. When you don't, the discovery step itself
must be run carefully — it's exactly the moment scope is still unknown,
which is the highest-risk condition for an expensive or slow scan. This
skill covers the fastest available path first, then a raw-log Search Job
sequence that works even with no discovery/admin RBAC at all. It stops once
`_sourceCategory`/`_index` are found — hand off to `discovery-profile-scope`
from there to sample raw logs and confirm schema.

## Fast paths — try these before scanning raw logs

### A. Data volume index (fast, cheap, needs `sumologic_volume` read access)

If the `sumologic_volume` index is enabled and readable, it's the
fastest/cheapest way to locate metadata values such as
`_sourceCategory` — much better than scanning raw logs. Zero results
can mean no matching data, or that the credential just lacks access to
this index. Pre-aggregated ingestion audit data, updated every few
minutes; a short window (`-3h` for small accounts, `-1h` for large
ones) is enough — metadata patterns repeat predictably in real time.

Every query needs the **parse-first pattern** — the payload is a nested
JSON array, not top-level fields — and the keyword only selects which
*events* match, not which of the split-out rows are relevant, so the
`| where` after `| json` is **mandatory**, not optional. Full pattern,
the six-dimension rollup table (collector/source/sourcecategory/
sourcehost/sourcename/view), and more examples:
`search-indexes-partitions/references/sumologic-volume.md`.

```
_index=sumologic_volume _sourceCategory=sourcecategory_and_tier_volume cloudtrail
| parse regex "(?<data>\{[^\{]+\})" multi
| json field=data "field","dataTier","sizeInBytes" as sourceCategory,dataTier,bytes nodrop
| where sourceCategory matches /(?i)cloudtrail/
| count by sourceCategory
| sort _count desc
```

Swap `_sourceCategory=sourcecategory_and_tier_volume` /
`sourceCategory` for `_sourceCategory=view_and_tier_volume` / `index` to
jump straight to partition/index names instead — option B below is
usually more direct and predictable for that specific case, but this
works when option B's RBAC isn't available.

Rollups carry no sample messages or field names — hand off to
`discovery-profile-scope` once a candidate is found to sample and profile
it. This index is single-dimension per query; cross-referencing more than
one dimension (e.g. "which `_collector`s exist within this
`_sourceCategory`?") is `discovery-profile-scope` territory, not this fast
path.

### B. Partition and FER admin endpoints, when RBAC allows

If the credential set can list partitions or field extraction rules —
`client.list_partitions()` / `client.list_extraction_rules()` (also
exposed as tools in Sumo's public MCP server — these are direct, search-free
lookups: partition names and routing expressions, and FER field
mappings, describe log structure and location without executing a
query at all.

**Picking the right partition from the list:** match the keyword or
technology name against the service name embedded in each partition's
`routingExpression` (e.g. `_sourceCategory=*/Apache` on a partition named
`apache`) rather than against the partition's `name` field alone — naming
conventions vary, but the routing expression is what actually determines
which logs land there. When more than one partition's expression
plausibly matches, prefer the one whose expression scopes narrowest to
the keyword over one that's merely topically adjacent; if still
ambiguous, hand off to `discovery-profile-scope` to confirm by content
against each candidate rather than guessing.

Not every credential set has this RBAC, and it varies a lot by
tenant/role — same for `sumologic_volume` access above. There's no
separate capability-check endpoint for either: calling
`list_partitions()`/`list_extraction_rules()` (or running the Fast Path A
query) IS the check — catch a `SumoSearchError` with `status_code == 403`
and fall back to the raw-log sequence below. Because this varies per
tenant rather than per session, it's worth a short note in your own
integration ("this credential can list partitions but not
sumologic_volume") so you don't re-probe it every run.

## Raw-log discovery sequence (fallback — always available)

Use this when the fast paths above aren't available or don't resolve it.
Keep every step in this sequence to a short time window (`-15m` to `now`
is a good default) until scope is confirmed — widen only afterward.

**1. Find candidate source categories and partition (index) values.** Anchor with a keyword specific
enough to be selective, aggregate rather than fetch raw messages:

```
checkout | count by _sourceCategory, _view | sort _count desc | limit 20
```

Syntax notes:
- A leading `*` is never necessary — a bare keyword like `checkout` alone
  already means "search default scope for this keyword." Omit it.
- Quotes (`"checkout"`) are only needed to quote a literal string
  containing spaces or characters that would otherwise break tokenization
  — e.g. `"foo bar"` or `"\"json_key\":\"value\""` for a literal JSON
  substring. A single unspaced keyword needs none.
- An unscoped bare-keyword query means "search the account's *default*
  search scope," not "search everything" — for tiered accounts that's
  the non-billable tiers (Continuous/Frequent, not Infrequent); for Flex
  it's whichever partitions aren't flagged excluded from default scope.

This is a `recordCount`-producing aggregate — call `run_search(...,
requires_raw_messages=False)` for it. It's still an unscoped query, so
keep the time window short and estimate first (see `estimate_scan()` in
`query-scoping-efficiency`) if the account bills Flex/Infrequent scans.

**Variations, by what you want to trade off:**

- Cap the scan itself when the keyword is common enough that a full
  unscoped aggregate would be expensive: `checkout | limit 100 | count by
  _sourceCategory` stops after finding 100 raw matches, then aggregates
  just those — cheaper, but the ranking is only as representative as
  those first 100 hits.
- Gather every built-in metadata dimension in one pass instead of doing
  source-category and partition resolution as separate steps — this
  merges Step 1 and Step 2 below into one query:
  ```
  checkout | count by _view, _sourceCategory, _collector, _source | sort _count desc | limit 100
  ```
  Skip the source-side `| limit` here only if you deliberately want the
  aggregate computed over the full unlimited scan (better sample size)
  with the cap applied solely at the end.
- When `_collector`/`_source` cardinality might be large and noisy,
  narrow to just the two dimensions that matter for locating scope:
  ```
  checkout | count by _view, _sourceCategory | limit 100
  ```

**2. Resolve the partition for a candidate category** (skip if Step 1
already used the merged `_view, _sourceCategory` form above):

```
_sourceCategory=prod/checkout | count by _view | sort _count desc | limit 5
```

The `_view` value in the result *is* the value to use as `_index=` in
every subsequent query — hardcode it once found, don't keep re-deriving
it. A source category can live in more than one view; if so, scope
subsequent queries with an explicit OR rather than picking one
arbitrarily:

```
(_view=a or _view=b or _view=c)
```

A wildcard view scope also works when a naming convention is known
(`_view=cloudtrail_prod_*`) — useful since partition naming often mirrors
log type across an org — but keep it as narrow as the convention allows;
an overly broad wildcard can error outright rather than just running slow.

Once `_sourceCategory` and `_view`/`_index` are locked in, hand off to
`discovery-profile-scope` — it covers sampling raw messages, confirming
field names, identifying index-time fields, and enumerating other
metadata dimensions (`_collector`, `_sourceHost`) within this scope.

## Custom / non-standard log sources

Industry-standard log formats (nginx, Apache, AWS CloudTrail, Azure
Audit, ...) are well documented and Sumo publishes apps with ready-made
queries for common use cases. Custom or homegrown log sources have no such
reference — `discovery-profile-scope`'s schema-sampling step is where this
usually surfaces, and it covers investing in a tenant-specific reference
for those sources.

## Searching System Partitions and Cloud SIEM Data

Built-in system/audit indexes (`sumologic_audit`, `sumologic_audit_events`,
`sumologic_volume`, `sumologic_system_events`) and Cloud SIEM
security-tier partitions (`sec_record_*`, `sec_signal`, insight audit
events) have their own dedicated skills rather than being covered here:

- `search-indexes-partitions` — all four partition types, including the
  system/audit indexes above, each with a per-index reference doc.
- `search-siem-investigation` — Cloud SIEM query rules, cost-aware
  per-tier time scoping, and insight audit events (`cseinsight`).

## Key Rules

- **Try the data-volume index or partition/FER admin endpoints first**
  when RBAC allows — both resolve source category/view names without
  scanning a single raw log, and are far cheaper than the raw-log
  sequence below. Record per-tenant RBAC results once discovered so
  later sessions don't re-probe capability.
- **Never skip straight to a wide, unscoped raw-log query "to see what's
  there."** Step 1's keyword anchor plus a short time window keeps even
  the fallback discovery phase cheap.
- **Skip the leading `*` and unnecessary quotes** — a bare keyword is a
  complete, valid scope on its own.
- **Aggregate (`count by`) before sampling raw messages** — a `count by
  _sourceCategory`/`count by _view` call is far cheaper than pulling raw
  messages and inspecting them by hand.
- **Lock in `_view` as `_index=` as soon as it's found** — don't leave
  later queries unscoped just because the discovery step itself was.
- **Widen time range only after scope is locked**, not during discovery.
- If the account bills Flex/Infrequent scans, run `estimate_scan()` on the
  step-1 query before executing it for real — see `query-scoping-efficiency`.
- **A fully unscoped, all-logs, all-time search (e.g. "find this IOC
  anywhere") is a last resort, not a starting point** — anchor with at
  least a keyword and a short time window even when the caller's ask
  sounds maximally broad.

## Quick Reference

```
-- Fast path A: data volume, keyword-anchored (needs sumologic_volume access)
-- The `where` after json is required — see note above the example in Fast path A.
_index=sumologic_volume _sourceCategory=sourcecategory_and_tier_volume cloudtrail
| parse regex "(?<data>\{[^\{]+\})" multi
| json field=data "field","dataTier","sizeInBytes" as sourceCategory,dataTier,bytes nodrop
| where sourceCategory matches /(?i)cloudtrail/
| count by sourceCategory | sort _count desc

-- Fast path A variant: jump straight to partition/index names
_index=sumologic_volume _sourceCategory=view_volume cloudtrail
| parse regex "(?<data>\{[^\{]+\})" multi
| json field=data "field","sizeInBytes" as index,bytes nodrop
| where index matches /(?i)cloudtrail/
| count by index | sort _count desc

-- Fallback Step 1: candidate source categories (short window, keyword-anchored)
checkout | count by _sourceCategory | sort _count desc | limit 20

-- Fallback Step 1 (merged): all built-in metadata dimensions in one pass
checkout | count by _view, _sourceCategory, _collector, _source | sort _count desc | limit 100

-- Fallback Step 2: resolve partition for the chosen category
_sourceCategory=prod/checkout | count by _view | sort _count desc | limit 5
```

## Related Skills (this folder)

- `discovery-profile-scope` — next step once `_sourceCategory`/`_index` is
  locked in: sample raw logs, confirm schema/index-time fields, and
  enumerate other metadata dimensions within this scope.
- `query-scoping-efficiency` — apply once scope is known; also covers
  `estimate_scan()` for pre-flight cost checks during discovery itself.
- `operator-ordering` — structuring the real query once scope + schema are
  established.

## Related Skills (other folders)

- `search-indexes-partitions` — partition types overview; its
  `references/sumologic-volume.md` has the full parse-first pattern and
  dimension table for the data-volume fast path above.
- `sumo_search_client.py` — `list_partitions()` / `list_extraction_rules()`
  for the admin-endpoint fast path above.
- `search-siem-investigation` — if discovery turns up `sec_record_*` /
  `sec_signal` scope, its query rules and cost-aware time scoping differ
  from standard log partitions.
