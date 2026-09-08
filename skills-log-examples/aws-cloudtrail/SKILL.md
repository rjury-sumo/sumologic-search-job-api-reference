---
name: example-log-aws-cloudtrail
description: >
  EXAMPLE ONLY — illustrates the log-domain-skill format
  (log-domain-skill-authoring / discovery-log-domains) using AWS
  CloudTrail logs from Sumo Logic's public demo/training org. Not a live
  reference and not meant to be loaded as an active skill — see
  ../README.md. Generated 2026-09-08.
metadata:
  instance: demo (Sumo Logic's public demo/training org — not a real customer)
  generated: 2026-09-08
  generated_by: log-domain-skill-authoring
  source_dashboards: ["V1xSn8nfcgy0p9atLxGe4FgfMZq94f6yVwyfBsgSGGhCjqOTQPg4cZkFsHHx"]
  source_method: dashboard-reuse + raw-log sample
---

> **EXAMPLE ONLY.** This illustrates what `log-domain-skill-authoring`
> produces — see [`../README.md`](../README.md). A real generated file
> lives at `~/sumo-search/output/<instance>/skills/<domain>/SKILL.md`,
> never in this repo. Read this for the format and discovery patterns,
> not as a current, actionable reference.

## Scope

| Metadata | Value | Confidence |
| --- | --- | --- |
| `_sourceCategory` | `Labs/AWS/CloudTrail*` | confirmed by sample — live data seen under `Labs/AWS/CloudTrailDevOps/Analytics` |
| `_index`/`_view` | not confirmed — default scope resolved it | needs-sample |

Dashboard panels use `_sourceCategory = Labs/AWS/CloudTrail*` (wildcard)
directly on the scope line — this org has at least one sub-category
under it (`Labs/AWS/CloudTrailDevOps/Analytics`); assume more may exist
and keep the wildcard rather than narrowing to the one observed value.

## Log format & key fields

**Format:** JSON, one event per line — standard AWS CloudTrail event
shape (`eventVersion`, `userIdentity`, `eventTime`, `eventSource`,
`eventName`, `awsRegion`, `sourceIPAddress`, `requestParameters`,
`responseElements`, `eventID`, ...).

**Parsing approach used by the example searches below:** top-level scalar
keys are pulled with a literal string-anchored `parse`, not `json auto`
or a named `json field=` extraction — e.g.
`parse "\"eventName\":\"*\"" as event_name`. This is faster than JSON
parsing when the key is unique enough in the raw text, and is what every
confirmed example below actually does. Nested paths (e.g. the caller's
IAM principal, which isn't top-level) use
`json field=_raw "userIdentity.principalId" as principal_id nodrop`
instead.

**User resolution gotcha:** `userName` is only present for IAM-user
calls; service-role/assumed-role calls have no `userName` but do have
`userIdentity.principalId`. The dashboard's pattern for a single "who did
this" field:
```
parse "\"userName\":\"*\"" as user_name nodrop
| json field=_raw "userIdentity.principalId" as principal_id nodrop
| parse regex field=principal_id ":(?<user_principal>.+)" nodrop
| if (user_name="", user_principal, user_name) as user
```

| Field | Meaning | Extraction |
| --- | --- | --- |
| `event_name` | API action called (`RunInstances`, `DeleteBucket`, ...) | `parse "\"eventName\":\"*\"" as event_name` |
| `event_type` | Verb prefix of `event_name` (`Create`, `Delete`, `Run`, ...) — CRUD-style bucketing | `parse regex field=event_name "^(?<event_type>[A-Z][a-z]+?)[A-Z]"` |
| `event_source` | AWS service the call targeted (`dynamodb.amazonaws.com`, ...) | `parse "\"eventSource\":\"*\"" as event_source` |
| `aws_region` | Region the call ran in | `parse "awsRegion\":\"*\"" as aws_region` |
| `user` | Caller identity, IAM user or role principal | see gotcha above |
| `public_ip` (Elastic IP panels only) | IP address referenced in an Address-related event | `parse regex "publicIp\":\"(?<public_ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\""` |

Every example query filters `event_type not in ("Get","Describe","List")`
early to drop the high-volume, low-signal read-only API traffic before
aggregating — worth keeping in any new CloudTrail query over this scope.

## Known-good example searches

### Categorical (`count by`, no `timeslice`)

**Actionable (non read-only) events by action** — from `AWS CloudTrail -
Operations / Action Events`
```
_sourceCategory = Labs/AWS/CloudTrail*
| parse "\"userName\":\"*\"" as user nodrop
| parse "\"eventName\":\"*\"" as event_name
| parse regex field=event_name "^(?<event_type>[A-Z][a-z]+?)[A-Z]"
| where event_type not in ("Get","Describe","List")
| count by event_name
| sort by _count
```

### Multi-series time series (`timeslice` + `transpose`)

**Resources created over time, by resource type** — from `AWS CloudTrail
- Operations / Created Resources Over Time`
```
_sourceCategory = Labs/AWS/CloudTrail* (Create* OR Run*)
| parse "\"userName\":\"*\"" as user_name nodrop
| json field=_raw "userIdentity.principalId" as principal_id nodrop
| parse regex field=principal_id ":(?<user_principal>.+)" nodrop
| if (user_name="", user_principal, user_name) as user
| parse "\"eventName\":\"*\"" as event_name
| parse regex field=event_name "^(?:Create|Run)(?<resource_type>[A-Z][A-Za-z]+)"
| timeslice 1h
| count _timeslice, resource_type
| transpose row _timeslice column resource_type
```
Same shape also covers **Deleted Resources Over Time** (swap `Delete*`
for the keyword and regex prefix), **Requested AWS Services Over Time**
(group by `event_source` instead of `resource_type`), and **Events by
AWS Region** (group by `aws_region`) — all four panels are this same
`timeslice 1h | count ... | transpose row _timeslice column <field>`
template with a different grouping field.

### Other shapes present (filtered recent-event listing)

**Recent Elastic IP address operations** — from `AWS CloudTrail -
Operations / Recent Elastic IP Address Operations`. Not a real time
series despite `timeslice` — the 1-minute bucket is used only to dedupe/
group a small filtered event list into a table, capped with `limit 10`:
```
_sourceCategory = Labs/AWS/CloudTrail* *Address* (!"DescribeAddresses")
| parse "\"userName\":\"*\"" as user_name nodrop
| json field=_raw "userIdentity.principalId" as principal_id nodrop
| parse regex field = principal_id ":(?<user_principal>.+)" nodrop
| if (user_name="", user_principal, user_name) as user
| parse "\"eventName\":\"*\"" as event_name
| parse "awsRegion\":\"*\"" as aws_region
| where event_name matches "*Address*"
| parse regex "publicIp\":\"(?<public_ip>\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\""
| timeslice 1m
| count as count by _timeslice,user, event_name,aws_region,public_ip
| fields -count
| sort _timeslice
| limit 10
```

No single-value, map, or box-plot panels found in the source dashboard —
not included rather than invented.

## Common pivots / related skills

- `common-query-patterns`, `operator-ordering`, `query-scoping-efficiency`
  — apply once scope/format are known, as above.
- `search-siem-investigation` — CloudTrail data commonly also lands in
  Cloud SIEM normalized records if this org has that enabled; check
  `sec_record_audit`/`sec_record_authentication` for a security-tier view
  of the same activity.
- Other dashboards worth checking for a use case not covered above:
  `AWS CloudTrail - Console Logins`, `AWS CloudTrail - User Monitoring`,
  `AWS CloudTrail - PCI Req 10 - Login Activity`, `Threat Intel for AWS -
  AWS CloudTrail`.

## Provenance & freshness

- Generated 2026-09-08 against Sumo Logic's public demo/training org via
  `log-domain-skill-authoring`, using `discovery-dashboard-reuse`
  (dashboard `V1xSn8nfcgy0p9atLxGe4FgfMZq94f6yVwyfBsgSGGhCjqOTQPg4cZkFsHHx`,
  "AWS CloudTrail - Operations") plus a live raw-log sample.
- Confirmed by live sample: yes — 2 raw events sampled, `_sourceCategory`
  confirmed as `Labs/AWS/CloudTrailDevOps/Analytics` under the wildcard.
- Re-verify if: this file is old, `_sourceCategory=Labs/AWS/CloudTrail*`
  silently returns zero results, or CloudTrail ingestion in this org
  changes. **In practice: don't rely on this file at all — it's a
  point-in-time example, not maintained.**
