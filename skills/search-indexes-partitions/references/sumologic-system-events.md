# `sumologic_system_events` — System Events Index

**Type:** AuditIndex | **Tier:** Continuous | **Retention:** 30 days (default)
**Keyword search:** ✅ supported

Platform-generated (not user-action) operational events. This file
covers the platform/collection-health categories only — Cloud SIEM
insight lifecycle events also live in this index under
`_sourceCategory=cseinsight`; see
`search-siem-investigation/references/insight-audit-events.md` for that
slice (SOC-analyst use case, kept separate from platform-health monitoring).

| `_sourceCategory` | Contents |
|---|---|
| `Collection` | Collection health events (collector down, source errors). Raw events carry `"subsystem":"Collection"`, `"eventType":"Health-Change"`. |
| `rateLimit` | Data rate-limiting events |
| `ingest` | Ingest pipeline system events |

## Query patterns

```
-- Unhealthy collection events:
_index=sumologic_system_events _sourceCategory=Collection UnHealthy
| json field=_raw "eventName","resourceIdentity.name","resourceIdentity.type","eventTime" as eventName,collectorName,resourceType,eventTime nodrop
| count by collectorName, eventName, resourceType | sort _count desc

-- Rate limiting events:
_index=sumologic_system_events _sourceCategory=rateLimit
| json "eventType","details","resourceName" as eventType,details,resource nodrop
| timeslice 1h
| count by _timeslice, resource | sort _timeslice desc
```

## Key JSON fields

| Field | Description |
|---|---|
| `eventType` | Event type string (e.g. `CollectorOffline`) |
| `eventTime` | ISO 8601 event timestamp |
| `severity` | Severity level (Info/Warning/Error/Critical) |
| `resourceName` | Name of the affected resource |

## Prerequisites

Must be enabled under **Administration → Security → System Event
Index**; zero results on health queries usually means it's disabled.

## Related

- `search-indexes-partitions` — parent overview.
- `sumologic-audit-events.md` (this folder) — complementary structured
  admin audit events.
- `search-siem-investigation/references/insight-audit-events.md` —
  Cloud SIEM insight lifecycle slice of this same index.
