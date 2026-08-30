# `sumologic_audit` — Legacy Audit Index

**Type:** AuditIndex | **Tier:** Continuous | **Retention:** 30 days (default)
**Keyword search:** ✅ supported

Legacy, **unstructured text** platform administration/activity events —
predates `sumologic_audit_events` (structured JSON). Still commonly
enabled; check both if one comes up empty.

| Event category | Examples |
|---|---|
| Authentication | Login success/failure, logout, password reset |
| Content changes | Dashboard/search created, updated, deleted, moved |
| Data collection | Collector created/deleted, source added/changed |
| User management | User created, role assigned, user deleted |
| Access control | Permission changed, shared content |
| Scheduled searches | Alert fired, scheduled search created |

## Query approach

Events are text blobs — scope to `_index=sumologic_audit`, add keywords
for bloom-filter matching, and `| parse` to extract fields. Field names
are not standardized (free text), so parse patterns vary by event type.

```
-- Login failures:
_index=sumologic_audit "Login failed"
| parse "user=*," as user
| count by user | sort _count desc

-- Content deletions:
_index=sumologic_audit "Content deleted" OR "Dashboard deleted"
| parse "by user * " as actor nodrop
| parse "name=*," as content_name nodrop
| count by actor, content_name

-- Collector changes:
_index=sumologic_audit "Collector" ("created" OR "deleted" OR "updated")
| parse "collector=*," as collector nodrop
| parse "by user *" as actor nodrop
| count by actor, collector | sort _count desc

-- User management activity:
_index=sumologic_audit "User" ("created" OR "deleted" OR "role assigned")
| parse "user=* " as target_user nodrop
| parse "by *" as actor nodrop
| count by actor, target_user
```

## Common parse patterns (field names vary — free text)

- Actor (who): `parse "by user *" as actor` or `parse "by *\n" as actor`
- Target object: `parse "name=*," as name`
- IP address: `parse "ip=*" as src_ip`
- Timestamp: use `_messagetime` (the event time), not a parsed field

## Prerequisites

Must be enabled under **Administration → Security → Audit Index**; zero
results usually means it's disabled or the credential lacks audit-index
read permission, not that nothing happened.

## Performance

Always add keywords — audit events are text blobs, and bloom-filter
matching on a keyword dramatically cuts scan versus an unscoped
`_index=sumologic_audit` search. Keep compliance-style queries scoped to
a tight window (days, not months).

## Related

- `search-indexes-partitions` — parent overview.
- `sumologic-audit-events.md` (this folder) — newer structured-JSON
  version of the same event categories; prefer it when both are enabled.
