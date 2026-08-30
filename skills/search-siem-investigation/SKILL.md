---
name: search-siem-investigation
description: >
  Querying Cloud SIEM data across its three tiers via the Search Job
  API: normalized security records (sec_record_*), generated signals
  (sec_signal), and insight audit/lifecycle events (in the
  sumologic_audit_events / sumologic_system_events indexes). Covers the
  query-syntax rules unique to sec_record_*/sec_signal, per-tier
  timestamp semantics for cost-aware scoping, and investigation
  workflow. Cloud SIEM customers only. NOT for Cloud SIEM configuration
  (rules, match lists, entity groups, tuning) — that lives behind a
  separate config REST API (`/api/sec/v1`), out of scope for this
  Search-Job-API-only reference.
  Triggers on: "sec_record", "sec_signal", "normalized records", "CSIEM
  records", "query security records", "security partition", "signal
  search", "MITRE query", "failed records", "record normalization
  failure", "cloud siem search", "siem investigation", "csiem insight",
  "siem signals", "cloud siem query", "investigate in cloud siem",
  "siem threat hunting", "signal to insight", "cseinsight".
---

## Cloud SIEM Data Tiers

Cloud SIEM produces three distinct tiers of security-enriched data, each
in different partitions with different query rules. Availability: Cloud
SIEM customers only.

### Tier 1 — Normalized records (`sec_record_*`)

Raw logs parsed, mapped, and normalized against the Sumo Logic schema.
One record per original log event — the raw material for signals.

| Partition | Contents | Retention |
|---|---|---|
| `sec_record_authentication` | Normalized authentication/identity events | 90 days |
| `sec_record_notification` | Normalized alert/notification events | 90 days |
| `sec_record_network` | Normalized network/firewall/proxy events | 90 days |
| `sec_record_audit` | Normalized audit/change/admin events | 90 days |
| `sec_record_failure` | Records that failed parsing or mapping | 90 days |
| `sec_record_endpoint` | Normalized endpoint detection events | 90 days |
| `sec_record_email` | Normalized email security events | 90 days |

### Tier 2 — Signals (`sec_signal`)

Enriched events of interest, generated when a detection rule fires
against incoming records. Multiple records can contribute to one signal.
Retention: 730 days.

### Tier 3 — Insights (audit-index partitions, not `sec_*`)

Insights are created when signals for an entity exceed a correlation
window's severity threshold. Insight lifecycle events live in the
platform audit indexes, not in a `sec_*` partition — see
[`references/insight-audit-events.md`](references/insight-audit-events.md)
for the query patterns and field tables (also queryable directly via
`_sourceCategory=cseinsight`).

---

## Critical Query Differences for `sec_record_*` and `sec_signal`

1. **Column name** — the UI shows "Security Record Details" instead of
   "Message"; it's the same `_raw` field, cosmetic only.
2. **Top-level vs. nested fields** — top-level fields can be used
   directly in query scope (before the first `|`):
   ```
   _index=sec_record_audit metadata_vendor=Microsoft metadata_product="Windows Event"
   ```
   Common scope fields: `metadata_vendor`, `metadata_product`,
   `metadata_parser`, `metadata_mapperName`, `metadata_sourceCategory`,
   `objectType`. **Nested** fields in the `fields` array need
   `%"fields.path"` syntax: `| where %"fields.action" = "blocked"`.
3. **Case sensitivity** — scope expressions (top-level fields) are
   case-insensitive; `| where` on nested arrays is case-sensitive.
4. **Keyword search** works and is fast on both partitions, and is not
   case-sensitive.

## Quick Reference

```
-- Record coverage by vendor/product:
_index=sec_record*
| count by _view, metadata_vendor, metadata_product, metadata_mapperName

-- Failed record reasons:
_index=sec_record_failure objectType=FailedRecord
| json "reason" nodrop
| count by metadata_vendor, metadata_product, reason | sort _count desc

-- Active signals by rule:
_index=sec_signal
| where isempty(suppressedreasons)
| json field=_raw "rulename","severity" as rulename,severity nodrop
| count by rulename, severity | sort _count desc
```

---

## Cost-Aware Time Scoping Across Tiers

A signal and the records that produced it have **different times** —
scoping a search to the wrong one is a common cause of slow or empty
results.

| Object | Field (in search) | Meaning |
|---|---|---|
| Signal | `_index=sec_signal`, `_messagetime` | When the signal itself was raised (near `signal.created`) |
| Record inside a signal | (from the signal's own metadata — see note below) `timestamp`, epoch ms, per record | When the originating raw event happened |
| Record | `_index=sec_record_*`, `_messagetime` | Same as above — the originating raw event time |

> Per-record timestamps (`signal.allRecords[].timestamp`) are only
> available from Cloud SIEM's separate configuration REST API
> (`GET /api/sec/v1/signals/{id}`), not the Search Job API this
> reference client wraps. If your integration has access to that API,
> fetch the signal there first to get exact record timestamps before
> issuing Search Job API calls against `sec_record_*`. Without it, widen
> the window around the signal's own `_messagetime` incrementally
> instead (see the escalation table below).

**Critical:** for chain/aggregation/threshold/outlier rules, a signal
can span minutes to hours between its earliest and latest contributing
record. The signal's raised time is usually close to the *latest*
contributing record — the earliest may be 60+ minutes earlier. Scoping
a `sec_record_*` pull to ±1 minute around the signal's time will miss
most of the records.

### Scoping discipline by caller

| Caller | Default scope per pull | Acceptable upper bound | Anchor on |
|---|---|---|---|
| Agent/script with per-record timestamps (via config API) | ±1 minute per record | ±5 min if correlating | each record's own `timestamp` |
| Agent/script without per-record timestamps | ±5 min around signal `_messagetime`, widen if empty | ±30 min | signal's `_messagetime` |
| Human, known signal time | ±5–15 min | 1 hour | signal `_messagetime` |
| Human, no anchor event | last 1h–24h | 7d only, `estimate_scan()` first | n/a — hunting |

### Recommended workflow

1. Find candidate signals with an aggregate `sec_signal` query (cheap —
   aggregates, not raw pulls):
   ```
   _index=sec_signal severity=4
   | json field=_raw "rulename","signalname" as rulename,signalname nodrop
   | count by rulename, signalname | sort _count desc
   ```
2. Pull the signal row itself scoped tightly to its own `_messagetime`
   window (±1–5 min), not a wide range.
3. Pull contributing `sec_record_*` events using the widest of: known
   per-record timestamps (if available via the config API) or an
   incrementally widening window around the signal's `_messagetime`
   (±5 min → ±15 min → ±30 min) — never jump straight to hours/days.
4. **Always `estimate_scan()` before running anything wider than 1 hour**
   on `sec_record_*` or `sec_signal` — see `query-scoping-efficiency`.

### Anti-patterns

```
-- BAD: multi-day scan of sec_record_* when a specific signal anchors the search
_index=sec_record_authentication "alice@example.com"   -- run over a 7d window

-- BAD: treating signal.timestamp (epoch ms) as UTC seconds when computing a window
```

### When wider windows are appropriate

| Goal | Window | Why |
|---|---|---|
| Pull records that produced a specific signal | ±1–5 min around signal time | Tightest plausible scope |
| Correlate adjacent events around a signal | ±5–15 min | Catch precursor/follow-on records |
| Coverage/health audit (aggregate counts only) | 24h aggregate | Aggregates are far cheaper than raw pulls |
| Failed-record diagnosis (aggregate counts by reason) | 24h aggregate | Same — aggregate-only |
| Threat-hunt sweep without a known anchor event | 7d max, `estimate_scan()` first | Verify cost before running |

---

## SIEM Forwarding Configuration (Reference Only)

For logs to appear in `sec_record_*` they must be forwarded and
successfully parsed/mapped at ingest:

1. **C2C connector** — "Forward to SIEM" checkbox (simplest).
2. **Source config** — `_siemforward=true` and `_parser=<parser_path>`.
3. **Field Extraction Rule** — set `_siemforward=true` at ingest for
   bulk routing.

On tiered plans only **Continuous**-tier logs can forward to SIEM; on
Flex, any log with `_siemforward=true` routes to SIEM. This is
ingest-side configuration, not something the Search Job API changes —
included here only as context for why a partition might have no data.

## Related Skills

- `search-indexes-partitions` — sec_record_*/sec_signal are Type 4
  partitions in the broader partition-type overview.
- `references/insight-audit-events.md` (this folder) — Tier 3 insight
  lifecycle/audit query patterns.
- `query-scoping-efficiency` — `estimate_scan()` and general scan-cost
  levers, referenced throughout the scoping section above.
- `discovery-without-metadata` — resolving an unfamiliar
  `_sourceCategory`/partition before writing a `sec_record_*` query.
