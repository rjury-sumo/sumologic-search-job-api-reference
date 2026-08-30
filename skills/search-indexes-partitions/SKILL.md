---
name: search-indexes-partitions
description: >
  Scope a Search Job API query to the right partition (`_index=`) or data
  tier to get results, avoid empty result sets silently caused by tier
  exclusion, and control scan cost. Covers the four partition types —
  default catch-all, user-defined, system/audit indexes, and Cloud SIEM
  security-tier partitions — and which query rules apply to each.
  Use when writing or fixing a query that needs `_index=`/`_view=` scope.
  NOT for designing partition architecture or routing (that's an
  admin-console/API task outside this reference client's scope).
  Triggers on: "which partition holds my logs", "use _index= in my query",
  "Infrequent tier no results", "sumologic_default", "how do I scope to a
  partition", "query data tier", "infrequent vs continuous querying",
  "my search returns empty", "audit index query", "sec_record",
  "security tier partition", "what is _index vs _view".
---

## Partition Types Overview

Sumo Logic has four distinct partition types. Each has different query
rules and behaviour.

### Type 1 — `sumologic_default` (default catch-all)

All un-routed ingested logs land here automatically.

- Tiered plans: always **Continuous** tier.
- Flex plans: included in default search scope — `_index=` not required
  but recommended for performance.
- Standard keyword search and parsing rules apply, no special constraints.

```
_index=sumologic_default _sourceCategory=prod/myapp error
```

### Type 2 — User-defined partitions

Created by admins to route specific log types for performance/cost
control, via a `routingExpression` (typically `_sourceCategory=...`).

- Can be **Continuous** (included or excluded from default scope) or
  **Infrequent** tier.
- **Infrequent-tier partitions are always excluded from default scope** —
  you **must** pass `_index=<name>` or the result set is silently empty.
- On Flex, every search incurs scan cost regardless of tier — scope
  tightly to one or few partitions.

```
_index=cloudtrail_infreq _sourceCategory=aws/cloudtrail GuardDuty
```

### Type 3 — System / audit indexes (special partitions)

Platform-managed partitions capturing Sumo Logic administration, audit,
and operational events — not user log data.

| Partition | Keyword search | Primary use |
|---|---|---|
| `sumologic_audit` | ✅ | Legacy unstructured-text admin/auth events |
| `sumologic_audit_events` | ✅ | Structured JSON admin events (auth, content, users, monitors, FERs) |
| `sumologic_volume` | ✅ | Ingest volume by source category/collector/view/tier |
| `sumologic_system_events` | ✅ | Platform health events; also Cloud SIEM insight lifecycle |
| `sumologic_search_usage_per_query` | ❌ (view — `field=value`/`\| where` only) | Per-query scan cost/duration |

Full per-index query patterns, field tables, and gotchas:
[`references/sumologic-audit.md`](references/sumologic-audit.md),
[`references/sumologic-audit-events.md`](references/sumologic-audit-events.md),
[`references/sumologic-system-events.md`](references/sumologic-system-events.md),
[`references/sumologic-volume.md`](references/sumologic-volume.md).
`sumologic_search_usage_per_query` is not covered here — outside this
reference client's bundled skill set.

### Type 4 — Cloud SIEM security-tier partitions

Populated by the Cloud SIEM normalization pipeline (CSIEM customers
only): `sec_record_*`, `sec_signal`. Contain normalized records/signals,
not raw logs, with distinct column-naming and nested-field syntax rules.
Load `search-siem-investigation` for the full query rules and
investigation workflow — not duplicated here.

---

## Finding the Right Partition

1. Run a short-range sample search with only `_sourceCategory=<value>`,
   no `_index=` — inspect the returned `_view` field, which names the
   partition.
2. Or resolve it directly with an aggregate:
   ```
   _sourceCategory=prod/checkout | count by _view | sort _count desc | limit 5
   ```
3. If the credential set can call `list_partitions()`, that's a direct,
   search-free lookup of every partition name and routing expression —
   match the keyword against the `routingExpression`, not just the
   partition's `name` (naming conventions vary; the expression is what
   actually determines routing).

## Quick Reference

```
-- Single partition
_index=prod_app_logs _sourceCategory=prod/checkout error

-- Multiple partitions
(_index=partition_a OR _index=partition_b) _sourceCategory=prod/*
```

Estimate scan cost before running anything wide: `estimate_scan(query,
from_ms, to_ms)` — see `query-scoping-efficiency`.

## Key Rules

- `_index` and `_view` refer to the same partition — use `_index=` in
  query scope; `_view` is what comes back in log messages.
- All un-routed logs land in `sumologic_default`.
- Infrequent-tier partitions are excluded from default scope — a missing
  `_index=` means no results, not an error.
- On Flex, every search incurs scan cost regardless of tier — always
  scope with `_index=` to minimize it.
- A wildcard `_index=prod_*` expands scan — use with caution; an overly
  broad wildcard can error outright rather than just running slow.
- `sumologic_search_usage_per_query` does not support freetext keyword
  search — `field=value` scope and `| where` only.

## Related Skills (this folder)

- `search-siem-investigation` — Cloud SIEM (Type 4) query rules and
  investigation workflow.
- `discovery-without-metadata` — resolving partition/source category
  when neither is known yet (the fast paths there use `sumologic_volume`
  and `list_partitions()`/`list_extraction_rules()`).
- `discovery-profile-scope` — sampling and confirming schema once a
  partition/source category is resolved.
- `query-scoping-efficiency` — `estimate_scan()` and general scan-cost
  levers once a partition is chosen.
- `search-job-api-best-practices` — `list_partitions()`/
  `list_extraction_rules()` client methods referenced above.
