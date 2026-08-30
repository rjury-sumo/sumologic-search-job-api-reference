# `sumologic_audit_events` — Structured Audit Events

**Type:** AuditIndex | **Tier:** Continuous | **Retention:** 30 days (default)
**Keyword search:** ✅ supported

Structured JSON platform-administration events. This file covers the
admin/platform categories only — Cloud SIEM insight audit events also
live in this index under `_sourceCategory=cseinsight`; see
`search-siem-investigation/references/insight-audit-events.md` for that
slice (SOC-analyst use case, kept separate from general admin auditing).

| Category | `eventName` values | `_sourceCategory` |
|---|---|---|
| Authentication | `UserLoginSuccess`, `UserLoginFailure`, `UserLogout`, `MfaEnabled`, `AccessKeyCreated` | `userSessions` |
| Content | `ContentCreated`, `ContentUpdated`, `ContentDeleted`, `ContentMoved`, `ContentCopied` | `contentManagement` |
| Data collection | `CollectorCreated`, `CollectorDeleted`, `SourceCreated`, `SourceUpdated`, `SourceDeleted` | `collection` |
| User management | `UserCreated`, `UserUpdated`, `UserDeleted`, `RoleAssigned`, `RoleRevoked` | `userManagement` |
| Monitors | `MonitorCreated`, `MonitorUpdated`, `MonitorDeleted`, `MonitorTriggered` | `monitorManagement` |
| Field extraction | `FERCreated`, `FERUpdated`, `FERDeleted` | `fieldExtractionRules` |

## Query approach

Events are JSON blobs in `_raw`:

1. Scope with `_index=sumologic_audit_events`, optionally `_sourceCategory=<category>`.
2. Add keywords (event names, emails, IPs) for bloom-filter matching.
3. Extract fields with `| json field=_raw "fieldName" as alias`.

```
-- Login failures by user, last 24h:
_index=sumologic_audit_events _sourceCategory=userSessions UserLoginFailure
| json field=_raw "eventName","operator.email","operator.sourceIp","eventTime" as eventName,email,src_ip,eventTime
| where eventName="UserLoginFailure"
| count by email, src_ip | sort _count desc

-- Content changes, last 7 days:
_index=sumologic_audit_events _sourceCategory=contentManagement
| json field=_raw "eventName","operator.email","resourceName","eventTime" as eventName,actor,resource,eventTime
| where eventName in ("ContentCreated","ContentUpdated","ContentDeleted","ContentMoved")
| count by actor, eventName, resource | sort _count desc

-- Collector/source changes:
_index=sumologic_audit_events _sourceCategory=collection
| json field=_raw "eventName","operator.email","resourceName","status" as eventName,actor,resource,status nodrop
| count by eventName, actor, resource | sort _count desc

-- Role assignments:
_index=sumologic_audit_events _sourceCategory=userManagement RoleAssigned
| json field=_raw "eventName","operator.email","targetUserEmail","roleName","eventTime" as eventName,actor,target,role,eventTime nodrop
| where eventName="RoleAssigned"
| count by actor, target, role

-- Monitor changes (who is modifying alerts):
_index=sumologic_audit_events _sourceCategory=monitorManagement
| json field=_raw "eventName","operator.email","resourceName","eventTime" as eventName,actor,monitor,eventTime
| count by actor, eventName, monitor | sort _count desc
```

## Key fields

| Field path | Description |
|---|---|
| `eventName` | Type of event (e.g. `UserLoginFailure`, `ContentDeleted`) |
| `eventTime` | ISO 8601 timestamp of the event |
| `operator.email` | Email of the user who performed the action |
| `operator.id` | User ID of the actor |
| `operator.sourceIp` | IP address of the actor |
| `resourceName` | Name of the affected resource |
| `resourceType` | Type of affected resource |

## Prerequisites

Must be enabled under **Administration → Security → Audit Index**; zero
results usually means it's disabled, not that nothing happened.

## Related

- `search-indexes-partitions` — parent overview.
- `sumologic-audit.md` (this folder) — legacy unstructured predecessor.
- `sumologic-system-events.md` (this folder) — complementary platform
  system events.
- `search-siem-investigation/references/insight-audit-events.md` —
  Cloud SIEM insight audit slice of this same index.
