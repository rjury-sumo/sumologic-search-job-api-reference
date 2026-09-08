---
name: example-log-azure-audit
description: >
  EXAMPLE ONLY — illustrates the log-domain-skill format
  (log-domain-skill-authoring / discovery-log-domains) using Azure Audit
  activity logs from Sumo Logic's public demo/training org, including a
  two-variant JSON shape and how to normalize it. Not a live reference
  and not meant to be loaded as an active skill — see ../README.md.
  Generated 2026-09-08.
metadata:
  instance: demo (Sumo Logic's public demo/training org — not a real customer)
  generated: 2026-09-08
  generated_by: log-domain-skill-authoring
  source_dashboards: ["n9m8mKIGPa9VnUHbF6673u5P6q9Vf6WsZHG0WYGyoVS5spNwvluMusxvb2jG"]
  source_method: dashboard-reuse (scope not yet confirmed by live sample)
---

> **EXAMPLE ONLY.** This illustrates what `log-domain-skill-authoring`
> produces — see [`../README.md`](../README.md). A real generated file
> lives at `~/sumo-search/output/<instance>/skills/<domain>/SKILL.md`,
> never in this repo. Read this for the format and discovery patterns,
> not as a current, actionable reference.

## Scope

| Metadata | Value | Confidence |
| --- | --- | --- |
| `_sourceCategory` | `Labs/Azure/Audit*` | needs-sample — this is the dashboard variable `{{Logsdatasource}}`'s saved `defaultValue`, not yet confirmed by a live query |
| `_index`/`_view` | not determined | needs-sample |

## Log format & key fields

**Format: JSON, but with two structurally different shapes under the
same scope**, depending on how a given Azure resource's logs were
ingested — this is the single most important thing to know before
writing a new query here:

1. **EventHub-streamed logs** — lowercase/dotted keys:
   `level`, `resultType`, `identity.claims.name`, `callerIpAddress`,
   `properties.eventCategory`, `operationName`.
2. **Azure Insight/Monitor API-collected logs** (this org's custom
   PowerShell collection scripts) — PascalCase keys: `Level`, `Status`,
   `Caller`, `HttpRequest.ClientIpAddress`, `Category`, `OperationName`.

**Normalization pattern used by every example query below** — extract
both variants with `nodrop` (so a missing key doesn't drop the row),
then `concat()` whichever one actually populated:
```
json "level", "Level" as level1, level2 nodrop
| json "resultType", "Status" as status1, status2 nodrop
| json "identity.claims.name", "Caller" as src_user1, src_user2 nodrop
| json "callerIpAddress", "HttpRequest.ClientIpAddress" as src_ip1, src_ip2 nodrop
| json "properties.eventCategory", "Category" as Category1, Category2 nodrop
| json "operationName", "OperationName" as OperationName1, OperationName2 nodrop
| concat (level1, level2) as Level
| concat (status1, status2) as Status
| concat (src_user1, src_user2) as src_user
| concat (src_ip1, src_ip2) as src_ip
| concat (Category1, Category2) as Category
| concat (OperationName1, OperationName2) as OperationName
| if (IsEmpty(Category), "Undefined", Category) as Category
| if(Level="1","Critical", if (Level="2","Error", if (Level="3","Warning", if (Level="4","Information", Level) ))) as Level
```
This is a generally reusable pattern any time one `_sourceCategory`
scope covers more than one ingestion path for the same log type — worth
copying wholesale into a new query rather than re-deriving it.

| Field (normalized) | Meaning | Source keys (variant 1 / variant 2) |
| --- | --- | --- |
| `Level` | Severity, numeric-coded (`1`=Critical...`4`=Information) then re-mapped to text | `level` / `Level` |
| `Status` | Result/outcome of the operation | `resultType` / `Status` |
| `src_user` | Caller identity | `identity.claims.name` / `Caller` |
| `src_ip` | Caller's source IP | `callerIpAddress` / `HttpRequest.ClientIpAddress` |
| `Category` | Event category, `"Undefined"` if empty in both variants | `properties.eventCategory` / `Category` |
| `OperationName` | Azure operation performed | `operationName` / `OperationName` |
| `ResourceGroupName` | Azure resource group | parsed from `resourceId` (regex `/RESOURCEGROUPS/(?<x>[^/]+)`) or `ResourceGroupName` directly |

## Known-good example searches

### Categorical (`count by`, no `timeslice`)

**Events by severity level** — from `Azure Audit - Overview / Events By
Level`
```
_sourceCategory={{Logsdatasource}} (level or Level)
<normalization pattern above>
| fields Level, Status, src_user, src_ip, Category, OperationName
| count by Level
| where !isEmpty(Level)
| sort by _count
```

### Multi-series time series (`timeslice` + `transpose`)

**Events by result status, over time** — from `Azure Audit - Overview /
Events By Status`
```
_sourceCategory={{Logsdatasource}} (resultType or Status)
<normalization pattern above>
| fields Level, Status, src_user, src_ip, Category, OperationName
| timeslice 1d
| count by Status, _timeslice
| where !isEmpty(Status)
| transpose row _timeslice column Status
```
Same shape covers **Events by Caller** (group by `src_user`), **Events
by Resource Group** (group by `ResourceGroupName`, needs the extra
`resourceId`-parsing step above), and **Events by Category** (group by
`Category`) — all four are this same `timeslice 1d | count ... |
transpose` template with a different grouping field.

### Other shapes present (map)

**Source-IP geolocation map** — from `Azure Audit - Overview / Azure
Activity by Source Location`
```
_sourceCategory={{Logsdatasource}} (callerIpAddress or (HttpRequest ClientIpAddress))
<normalization pattern above, src_ip only needed>
| count by src_ip
| where !isEmpty(src_ip)
| lookup latitude, longitude, country_code, country_name, region, city, postal_code from geo://location on ip = src_ip
| count by latitude, longitude
| where !isNull(latitude)
```
`lookup ... from geo://location on ip=<field>` is the general pattern
for any geo/map-shaped panel — enrich an IP to lat/long via the built-in
`geo://location` lookup table, then aggregate by the resulting
coordinates.

No single-value or box-plot panels found in the source dashboard — not
included rather than invented.

## Common pivots / related skills

- `common-query-patterns`, `operator-ordering` — the timeslice+transpose
  and categorical templates above map directly.
- Other same-technology dashboards in this org: `Azure Audit - Service
  Health`, `Azure Audit - User Activity`, `Azure Audit - Resource Usage`,
  `Azure Cosmos DB - Audit`, `Azure Event Hubs - Audit` — check
  `discovery-dashboard-reuse` again if the use case is Cosmos DB/Event
  Hubs specifically rather than general activity-log auditing.

## Provenance & freshness

- Generated 2026-09-08 against Sumo Logic's public demo/training org via
  `log-domain-skill-authoring`, using `discovery-dashboard-reuse` only
  (dashboard `n9m8mKIGPa9VnUHbF6673u5P6q9Vf6WsZHG0WYGyoVS5spNwvluMusxvb2jG`,
  "Azure Audit - Overview").
- Confirmed by live sample: **no** — `_sourceCategory` came only from the
  dashboard variable's saved default, and the two-variant format above is
  inferred from the dashboard's own defensive parsing, not from a raw
  sample of this org's actual data. Run
  `discovery-profile-scope` against `Labs/Azure/Audit*` before relying on
  this for anything beyond a rough first pass — in particular, confirm
  which of the two format variants (or both) this org actually has.
- Re-verify if: this file is old, the scope value above returns zero
  results on a sample, or Azure log ingestion in this org changes.
  **In practice: don't rely on this file at all — it's a point-in-time
  example, not maintained.**
