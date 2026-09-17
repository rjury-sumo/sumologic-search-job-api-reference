# Slide: Discover Metadata for Free with the Data Volume Index

## Subtitle
A built-in, pre-aggregated index — zero scan cost, always the right first step

## The idea

Every Sumo Logic org has a standing volume index that pre-aggregates ingest
metadata — no setup required. Query it before touching raw logs: it's free,
has no scan cost, and tells you what data exists, where it lives, and how much
of it there is.

## What you learn

- Matching source categories and their naming conventions
- Data tier per source (Continuous / Infrequent / Flex) — determines query cost
- Ingestion volume (GB) — helps prioritize what actually matters
- Which partition/index each source routes to

## Source category is just one dimension

The volume index isn't only sliced by source category — six rollup dimensions
are available, each a different `_sourceCategory` value selecting the index
itself:

| Rollup dimension | Granularity | Key parsed fields |
|---|---|---|
| `sourcecategory_and_tier_volume` | Per source category | `sourceCategory`, `dataTier` |
| `collector_and_tier_volume` | Per collector | `collector`, `dataTier` |
| `source_and_tier_volume` | Per source | `collector`, `sourceName`, `dataTier` |
| `sourcehost_and_tier_volume` | Per source host | `sourceHost`, `dataTier` |
| `sourcename_and_tier_volume` | Per source name | `sourceName`, `dataTier` |
| `view_and_tier_volume` | Per partition/view | `field` (→ index name), `dataTier` |

Pick the dimension that matches the question — "what partitions exist" needs
`view_and_tier_volume`, not `sourcecategory_and_tier_volume`.

## The step everyone (including AI assistants) gets wrong

Each event in this index is **not** a single row — it's a pre-aggregated
summary that embeds many rows as a **nested JSON array**. Filtering or
aggregating on `sourceCategory` directly in scope, without unpacking that
array first, silently returns zero or wrong results. There is no shortcut:
the array must always be parsed out before anything else happens.

**Wrong** — treats a nested field as if it were top-level and searchable in scope:
```sql
_index=sumologic_volume _sourceCategory=sourcecategory_and_tier_volume cloudtrail
| sum(sizeInBytes) as bytes by sourceCategory
```

**Correct** — unpack the array, then filter and aggregate on the parsed fields:
```sql
_index=sumologic_volume _sourceCategory=sourcecategory_and_tier_volume
| parse regex "(?<data>\{[^\{]+\})" multi
| json field=data "field","sizeInBytes","dataTier" as sourceCategory,bytes,tier
| where sourceCategory matches "*cloudtrail*"
| sum(bytes/1073741824) as gbytes, first(tier) as tier by sourceCategory
| sort gbytes desc
```

Note the naming collision: `_sourceCategory` (the query scope) picks *which
rollup dimension to read*; `sourceCategory` (the parsed field) is the actual
value inside it, e.g. `aws/cloudtrail`. Keyword text in scope only narrows
which pre-aggregated *events* match — it does not filter the rows packed
inside them, so the `where` clause after unpacking is required, not optional.

## Implementation options

This is a search against a built-in index, not a dedicated discovery
endpoint — same query text, run through whichever transport you're using:

| Route | How |
|---|---|
| API | `sumo_search_client.py`'s `run_search()` (standard search job — create/poll/fetch/delete) |
| `sumosearch` CLI | `sumosearch search run '_index=sumologic_volume ...' --from -1h --to now` (or `sample` for a quick peek first) |
| Sumo MCP | `runLogSearch` tool with the same query text |

## Talk track

This index answers "what do I have, and how much" before you spend a single
credit. The one gotcha that trips up both people and AI copilots: skipping
the JSON-array unpack step and querying the packed field directly in
scope — always parse first, filter second.
