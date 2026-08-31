# `sumologic_search_usage_per_query` — Search Usage View

**Type:** Scheduled View | **Tier:** Continuous
**Keyword search:** ❌ **NOT SUPPORTED — freetext keywords fail silently**
**App coverage:** Search Audit app

One row per executed search: user, query text, bytes scanned, duration,
query type, and per-tier scan breakdown. Use for search cost analysis,
finding expensive queries, identifying heavy users, per-tier scan cost,
and compliance reporting on search activity.

**Key use case — open-ended discovery:** the `query` column is a
searchable log of every query other users have run, including against
data sources with no other documentation. For a custom or proprietary
log format, searching this view for a keyword, source category, or
field name surfaces working queries other users (or scheduled
searches/monitors) already built for that data — often faster than
profiling raw logs from scratch. See "Discovery: mining prior queries
for workload patterns" below.

### CRITICAL: No Freetext Keywords

This is a **view**, not an index — it does not support bloom-filter
keyword matching.

- ❌ `_view=sumologic_search_usage_per_query error` — silently returns empty results
- ✅ `_view=sumologic_search_usage_per_query query_type=Interactive*` — preferred scope (matches "Interactive Search" and "Interactive Dashboard")
- ✅ `_index=sumologic_search_usage_per_query query_type=Interactive*` — also works; `_view=` preferred since this is a scheduled view, not a partition
- ✅ `_view=... | where query contains "error"` — correct keyword filtering via `where`

## Schema

| Field | Description |
|-------|-------------|
| `user_name` | Email/username of the search executor |
| `query` | Full query text |
| `query_type` | See query_type values below |
| `query_start_time` | Epoch ms — when the search started |
| `query_end_time` | Epoch ms — when the search ended |
| `execution_duration_ms` | Wall-clock duration in milliseconds |
| `data_scanned_bytes` | Total bytes scanned across all tiers |
| `data_retrieved_bytes` | Bytes returned in the result set |
| `scanned_message_count` | Number of messages scanned |
| `retrieved_message_count` | Number of messages returned |
| `scanned_partition_count` | Number of partitions scanned |
| `scanned_bytes_breakdown` | JSON — bytes per tier (`Continuous`, `Infrequent`) |
| `scanned_bytes_breakdown_by_metering_type` | JSON — bytes per metering type (`Flex` billable; `FlexSecurity` SIEM-forwarded logs non-billable; `Security` sec_record_*/sec_signal non-billable) — **use this for billing analysis** |
| `analytics_tier` | Tier context string (e.g. `continuous`, `infrequent`, `flex`) |
| `status_message` | Completion status — see values below |
| `content_name` | Name of the saved search, dashboard, or monitor |
| `content_identifier` | ID of the saved content item |
| `session_id` | Search session identifier |
| `remote_ip` | IP address the search was submitted from |
| `is_aggregate` | `true` if query includes an aggregation operator |
| `is_emulated_search` | `true` for internally generated searches |

### `status_message` values

| Value | Meaning |
|-------|---------|
| `Finished successfully` | Query completed and returned results |
| `Query Failed` | Query encountered an error during execution |
| `Query canceled` | Query was manually canceled or timed out |

Use exact string match — `status_message = "Finished successfully"`.
`!=` is valid for negation and returns both failed and canceled rows.

### `query_type` values

| Value | Description |
|-------|-------------|
| `Alerts` | Monitor fired — the monitor's query ran to evaluate an alert condition |
| `CSE` | Cloud SIEM UI — user ran a search from the SIEM investigation interface back to log search |
| `Interactive Dashboard` | Dashboard panel rendered interactively by a user |
| `Interactive Search` | Log search run directly in the UI |
| `Monitors` | Scheduled monitor evaluation (distinct from a fired alert) |
| `Query Agent` | Copilot / mobot — AI-driven search agent |
| `SOC Analyst Agent` | SOC Analyst Agent (preview) — AI agent for SIEM workflows |
| `Scheduled Search` | Saved search running on a schedule |
| `Search API` | Search submitted via the Search Job API |
| `Search MCP` | Sumo Logic MCP server (preview) — tool-call driven searches |
| `Unknown` | Type could not be determined |

Values with spaces must be quoted in queries, e.g.
`query_type="Interactive Dashboard"`. Globs also work: `query_type=*Agent*`.

## Query patterns

### Discovery: mining prior queries for workload patterns

For power users/admins doing open-ended discovery — especially against
custom log sources with a proprietary or undocumented format — searching
the `query` column for other users' workloads is often faster than
profiling raw logs cold. Layer coarse keyword filtering (fast, cheap,
case-insensitive) before fine-grained `where`/regex filtering (slower,
more precise):

```
_view=sumologic_search_usage_per_query

-- filter using keyword expressions vs the query field: fast, case
-- insensitive, but not fine grained
query=*cloudtrail*

-- optionally narrow to a workload type, e.g. Scheduled Search, Monitors
-- query_type=Scheduled Search

-- fine-grained filtering with where: slower, but can be very specific
| where query matches /cloudtrail.*errorCode/
| where query matches "*errorCode*"

-- capture the scope (everything before the first pipe) as its own field
| parse regex field=query "^(?<scope>[^\|]+)"

-- aggregate to keep the response small
| count as searches by query_type, scope, query
| sort searches
| limit 10
```

### Expensive searches

```
-- Slowest interactive searches, last 24h
-- query_type=Interactive* matches both "Interactive Search" and "Interactive Dashboard"
_view=sumologic_search_usage_per_query query_type=Interactive*
| where status_message = "Finished successfully"
| execution_duration_ms / 1000 as duration_s
| where duration_s > 60
| top 20 query, user_name, duration_s, data_scanned_bytes by duration_s

-- Top queries by total bytes scanned
_view=sumologic_search_usage_per_query
| where data_scanned_bytes > (1 * 1G)
| if(status_message = "Finished successfully", 0, 1) as failed
| count_distinct(query) as unique_queries,
  sum(data_scanned_bytes) as bytes_scanned,
  max(scanned_partition_count) as max_partitions,
  sum(failed) as failures
  by user_name, query_type
| sort bytes_scanned desc
| bytes_scanned / 1G as scan_gb

-- Total scan by user, last 7 days
_view=sumologic_search_usage_per_query
| sum(data_scanned_bytes) as total_bytes by user_name
| sort total_bytes desc
| total_bytes / 1G as total_gb
```

### Per-tier scan cost breakdown

Prefer `scanned_bytes_breakdown_by_metering_type` over
`scanned_bytes_breakdown` — newer, more complete, and aligned to billing
metering types (`Flex` billable; `FlexSecurity`/`Security` non-billable).

```
-- Flex billable vs non-billable breakdown by user
_view=sumologic_search_usage_per_query
| json field=scanned_bytes_breakdown_by_metering_type "$['Flex']" as flex_bytes nodrop
| json field=scanned_bytes_breakdown_by_metering_type "$['FlexSecurity']" as flexsec_bytes nodrop
| json field=scanned_bytes_breakdown_by_metering_type "$['Security']" as sec_bytes nodrop
| if(!isNull(flex_bytes), flex_bytes, 0) as flex_bytes
| if(!isNull(flexsec_bytes), flexsec_bytes, 0) as flexsec_bytes
| if(!isNull(sec_bytes), sec_bytes, 0) as sec_bytes
| sum(flex_bytes) as billable_bytes,
  sum(flexsec_bytes) as siem_fwd_bytes,
  sum(sec_bytes) as sec_record_bytes
  by user_name
| billable_bytes / 1G as billable_gb
| sort billable_gb desc

-- Infrequent-tier scan with credit estimate (tiered plans)
_view=sumologic_search_usage_per_query
| json field=scanned_bytes_breakdown "$['Infrequent']" as inf_bytes nodrop
| if(!isNull(inf_bytes), inf_bytes, 0) as inf_bytes
| sum(inf_bytes) as inf_bytes_sum, count as queries by user_name
| inf_bytes_sum / 1G as inf_gb
| inf_gb * 0.016 as scan_credits
| sort scan_credits desc
```

### User activity audit

```
-- All searches by a specific user
_view=sumologic_search_usage_per_query user_name=analyst@company.com
| execution_duration_ms / 1000 as duration_s
| fields query_start_time, query, query_type, content_name, duration_s,
  data_scanned_bytes, scanned_partition_count, status_message
| sort query_start_time desc

-- Users running the most searches, last 7 days
_view=sumologic_search_usage_per_query
| count as searches, sum(data_scanned_bytes) as total_bytes by user_name
| sort searches desc
```

### Query pattern analysis

```
-- Queries scanning many partitions (optimization candidates)
_view=sumologic_search_usage_per_query
| where scanned_partition_count > 5
| count as occurrences, avg(scanned_partition_count) as avg_partitions,
  sum(data_scanned_bytes) as total_bytes by user_name, query_type
| sort total_bytes desc

-- Named content driving the most scan (dashboards, scheduled searches)
_view=sumologic_search_usage_per_query
| where !isempty(content_name)
| sum(data_scanned_bytes) as total_bytes, count as executions by content_name, query_type, user_name
| sort total_bytes desc
| total_bytes / 1G as total_gb
```

### Failed and slow searches

```
-- Searches that did not finish successfully
_view=sumologic_search_usage_per_query
| where status_message != "Finished successfully"
| count by user_name, status_message
| sort _count desc

-- Long-running searches that may indicate performance issues
_view=sumologic_search_usage_per_query
| execution_duration_ms / 1000 / 60 as duration_min
| where duration_min > 5
| where status_message = "Finished successfully"
| top 20 query, user_name, duration_min, data_scanned_bytes by duration_min
```

## Common mistakes

| Mistake | Correct approach |
|---------|------------------|
| `_view=... error` (keyword) | `_view=... \| where query contains "error"` |
| `where status = "Done"` / wrong field name | Use `status_message`. Valid values: `"Finished successfully"`, `"Query Failed"`, `"Query canceled"` |
| `partitions_used` field | No such field — use `scanned_partition_count` |
| `scanned_bytes_breakdown` for billing | Use `scanned_bytes_breakdown_by_metering_type` |

## Prerequisites

Enable at **Administration → Security → Search Audit Index**. Data
populates with ~5 min delay.

## Related

- `search-indexes-partitions` — parent overview.
- `query-scoping-efficiency` — general scan-cost levers for the
  expensive queries this view surfaces.
- `sumologic-volume.md` (this folder) — complementary discovery source:
  resolves metadata strings (source categories, partitions) that
  actually match ingested data, vs. this view's discovery of existing
  *queries* against that data.
- `discovery-without-metadata` — the broader discovery workflow this
  view's query-mining use case feeds into.
