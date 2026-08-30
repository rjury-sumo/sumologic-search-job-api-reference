# `sumologic_volume` — Data Volume Index

**Type:** AuditIndex | **Tier:** Continuous | **Retention:** 30 days (default)
**Keyword search:** ✅ supported

Hourly/daily rollups of ingest volume (bytes), broken down by dimension.
Each event is a pre-aggregated volume summary, not an individual log —
and each event embeds **many rows as a nested JSON array**, not
top-level fields.

| `_sourceCategory` | Dimension | Key parsed fields |
|---|---|---|
| `collector_and_tier_volume` / `collector_volume` | Per collector | `collector`, `dataTier` |
| `source_and_tier_volume` / `source_volume` | Per source | `collector`, `sourceName`, `dataTier` |
| `sourcecategory_and_tier_volume` / `sourcecategory_volume` | Per source category | `sourceCategory`, `dataTier` |
| `sourcehost_and_tier_volume` / `sourcehost_volume` | Per source host | `sourceHost`, `dataTier` |
| `sourcename_and_tier_volume` / `sourcename_volume` | Per source name | `sourceName`, `dataTier` |
| `view_and_tier_volume` / `view_volume` | Per partition/view | `field` (→ index name), `dataTier` |

`*_and_tier_volume` (v2) adds `dataTier` (Continuous/Infrequent/Flex);
the plain `*_volume` (v1) rollups omit it. Prefer v2.

## Parse-first requirement (mandatory)

Every query against this index must parse the raw JSON array before
aggregating — skipping this silently returns zero or wrong results.

**Wrong** — `dataVolumeBytes`/`sizeInBytes` are not top-level fields:
```
_index=sumologic_volume _sourceCategory=sourcecategory_and_tier_volume
| sum(dataVolumeBytes) as bytes by sourceCategory, dataTier
```

**Correct:**
```
_index=sumologic_volume _sourceCategory=sourcecategory_and_tier_volume
| parse regex "(?<data>\{[^\{]+\})" multi
| json field=data "field","dataTier","sizeInBytes" as sourceCategory,dataTier,bytes nodrop
| sum(bytes/1Gi) as gbytes by sourceCategory, dataTier
| sort gbytes desc
```

`_sourceCategory` (scope) ≠ `sourceCategory` (the parsed field) — the
scope value selects which rollup *dimension* to read; the parsed field
is the actual value (e.g. `aws/cloudtrail`) inside it.

## Using it to discover a partition/source category by keyword

Because the payload is a single JSON array per event, a keyword only
selects which *events* match — it does not filter which split-out rows
pertain to the keyword. **The `| where` after `| json` is required, not
optional**, or `count by` counts every sibling row from the same
matching event (unrelated categories included).

```
_index=sumologic_volume _sourceCategory=sourcecategory_and_tier_volume cloudtrail
| parse regex "(?<data>\{[^\{]+\})" multi
| json field=data "field","dataTier","sizeInBytes" as sourceCategory,dataTier,bytes nodrop
| where sourceCategory matches /(?i)cloudtrail/
| count by sourceCategory
| sort _count desc
```

Swap `sourceCategory_and_tier_volume` for `view_and_tier_volume` (parsed
field `index`) to resolve partition/view names by the same pattern
instead. This works for any of the six dimensions in the table above —
just check the keyword against the parsed field, not the raw payload.

Rollups carry no sample messages or field names — use a raw sample
against the resolved partition to profile schema (see
`discovery-profile-scope`). This index is single-dimension per query;
cross-referencing more than one dimension at once needs the raw-log
discovery sequence instead.

## More query patterns

```
-- Volume trend for one source category, hourly, last 7 days:
_index=sumologic_volume _sourceCategory=sourcecategory_and_tier_volume
| parse regex "(?<data>\{[^\{]+\})" multi
| json field=data "field","dataTier","sizeInBytes" as sourceCategory,dataTier,bytes nodrop
| where sourceCategory = "aws/cloudtrail"
| timeslice 1h
| sum(bytes/1Gi) as gbytes by _timeslice, dataTier
| sort _timeslice asc

-- Volume by partition/view, excluding system partitions:
_index=sumologic_volume _sourceCategory=view_and_tier_volume
| parse regex "(?<data>\{[^\{]+\})" multi
| json field=data "field","dataTier","sizeInBytes" as index,dataTier,bytes nodrop
| where index not in ("Default Index", "sumologic_volume", "sumologic_audit")
| sum(bytes/1Gi) as gbytes by index, dataTier
| sort gbytes desc
```

## Prerequisites

Must be enabled under **Administration → Security → Data Volume Index**;
zero results usually means it's disabled on this account, not that
there's no data.

## Related

- `search-indexes-partitions` — parent overview of all four partition types.
- `discovery-without-metadata` — Fast Path A uses this index to resolve
  an unknown source category/partition before falling back to raw-log
  scans.
- `discovery-profile-scope` — sampling/profiling schema once a partition
  is resolved.
- `query-scoping-efficiency` — general scan-cost levers.
