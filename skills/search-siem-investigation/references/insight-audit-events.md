# Cloud SIEM Insight Audit Events (Tier 3)

Insight lifecycle events (created, updated, assigned, closed) are
written to two platform audit-index partitions, both under
`_sourceCategory=cseinsight` (lowercase for both the source category and
event-name keywords):

| Partition | What it adds |
|---|---|
| `sumologic_audit_events` | User-driven audit trail — all lifecycle states stored as `insightupdated` events with differing `insight.status` values |
| `sumologic_system_events` | System-generated lifecycle events, e.g. `InsightCreated`, `InsightStatusUpdated`, `InsightAssigned`, `InsightClosed` |

**Combined scope**, when the full picture of state changes is needed:
```
_sourceCategory=cseinsight (_index=sumologic_audit_events OR _index=sumologic_system_events)
```

## Query patterns

```
-- Closed insights — detailed fields (validated schema, sumologic_audit_events):
_index=sumologic_audit_events _sourcecategory=cseinsight insightupdated
"\"status\": \"closed\""
| json field=_raw "insightIdentity.id" as id
| json field=_raw "insightIdentity.readableId" as insightid
| json field=_raw "insight.status" as status
| json field=_raw "insight.name" as name
| json field=_raw "insight.entityType" as entitytype
| json field=_raw "insight.entityValue" as entityvalue
| json field=_raw "insight.severity" as severity
| json field=_raw "insight.severityName" as severityname
| json field=_raw "insight.assignee" as assignee nodrop
| json field=_raw "insight.resolution" as resolution nodrop
| json field=_raw "insight.timeToResponse" as timeToResponse nodrop
| json field=_raw "insight.timeToDetection" as timeToDetection nodrop
| json field=_raw "insight.timeToRemediation" as timeToRemediation nodrop
| where status="closed"
| max(_messagetime) as _messagetime, count by insightid, name, entitytype, entityvalue,
  severity, resolution, severityname, assignee
| dedup by insightid
| sort by insightid

-- Insight volume and analyst activity — summary (sumologic_audit_events):
_index=sumologic_audit_events _sourcecategory=cseinsight insightupdated
| json field=_raw "insightIdentity.readableId" as insightid
| json field=_raw "insight.status" as status
| json field=_raw "insight.severity" as severity
| json field=_raw "insight.assignee" as assignee nodrop
| count by status, severity, assignee | sort _count desc

-- Insights created in the last 24h (sumologic_system_events):
_index=sumologic_system_events _sourceCategory=cseinsight InsightCreated
| json "insightIdentity","insight.name","insight.severity","insight.status","eventType" as id,name,severity,status,eventType nodrop
| where eventType="InsightCreated"
| count by id, name, severity

-- Insight status changes — analyst workflow tracking (sumologic_system_events):
_index=sumologic_system_events _sourceCategory=cseinsight
| json "eventType","insightIdentity","insight.status","insight.assignee","eventTime" as eventType,id,status,assignee,eventTime nodrop
| where eventType in ("InsightStatusUpdated","InsightAssigned","InsightClosed")
| count by analyst=assignee, eventType, status | sort _count desc

-- Insight resolution rate, created vs. closed by day:
_index=sumologic_system_events _sourceCategory=cseinsight
| json "eventType" as eventType nodrop
| where eventType in ("InsightCreated","InsightClosed")
| timeslice 1d
| count by eventType, _timeslice
| transpose row _timeslice column eventType
```

## Key fields

| Field path | Description |
|---|---|
| `insightIdentity.id` | Internal insight UUID |
| `insightIdentity.readableId` | Human-readable insight ID (e.g. `INSIGHT-1234`) |
| `insight.status` | Current status (`new`, `inprogress`, `closed`) |
| `insight.name` | Insight name / detection rule description |
| `insight.entityType` | Entity type (e.g. `username`, `hostname`, `ip`) |
| `insight.entityValue` | Entity value |
| `insight.severity` | Numeric severity score |
| `insight.severityName` | Severity label (`LOW`, `MEDIUM`, `HIGH`, `CRITICAL`) |
| `insight.assignee` | Analyst assigned (present after assignment/close) |
| `insight.resolution` | Resolution reason (present on closed insights) |
| `insight.timeToResponse` / `timeToDetection` / `timeToRemediation` | Seconds between lifecycle milestones (null until reached) |
| `eventType` | System-event lifecycle marker (`InsightCreated`, `InsightStatusUpdated`, `InsightAssigned`, `InsightClosed`) |

## Prerequisites

Both indexes must be enabled under **Administration → Security**. Zero
results on insight queries most often means one of the two indexes is
disabled — try the combined scope above before concluding there's no
insight activity.

## Related

- `search-siem-investigation` (parent skill) — sec_record_*/sec_signal
  tiers and cost-aware time scoping.
- `search-indexes-partitions/references/sumologic-audit-events.md` —
  the non-SIEM admin-event side of the same `sumologic_audit_events` index.
- `search-indexes-partitions/references/sumologic-system-events.md` —
  the non-SIEM platform-health side of the same `sumologic_system_events` index.
