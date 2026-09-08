---
name: example-log-kubernetes-api-server
description: >
  EXAMPLE ONLY — illustrates the log-domain-skill format
  (log-domain-skill-authoring / discovery-log-domains) using Kubernetes
  API Server control-plane logs from Sumo Logic's public demo/training
  org. Not a live reference and not meant to be loaded as an active
  skill — see ../README.md. Generated 2026-09-08.
metadata:
  instance: demo (Sumo Logic's public demo/training org — not a real customer)
  generated: 2026-09-08
  generated_by: log-domain-skill-authoring
  source_dashboards: ["WgPX6ihJdwkVsdpNyX2ZUn9juNR7fwuquJNe322QteIiqppQwaMpZWSrhO4I"]
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
| `_sourceCategory` | `Labs/Kubernetesv2/core/api-server` | needs-sample — this is the dashboard variable `{{ApiServerLogSource}}`'s saved `defaultValue`, not yet confirmed by a live query |
| `_index`/`_view` | not determined | needs-sample |

Sibling control-plane sources exist in this org under the same
`Labs/Kubernetesv2/core/*` convention, each its own dashboard variable:
`Labs/Kubernetesv2/core/falco` (Falco runtime security),
`Labs/Kubernetesv2/core/controller` (controller manager),
`Labs/Kubernetesv2/core/kube-system` (kube-system namespace pods) — not
researched in this file; run `log-domain-skill-authoring` again with one
of those as the target if needed, rather than assuming this file covers
them.

## Log format & key fields

**Format:** JSON envelope wrapping the real log line — the classic
containerized-workload shipper shape: `{"timestamp": "...", "log":
"..."}`. The interesting content is inside the `log` string, which
itself needs a second parse pass, not top-level JSON fields.

**Parsing approach used by the example searches below:**
```
json field=_raw "timestamp"
| json field=_raw "log"
| parse regex field=log "^(?<severity>.)(?:[0-9])"
```
`severity` here is klog-style: a single leading letter (`I`nfo,
`W`arning, `E`rror, `F`atal) immediately followed by a digit (month) at
the start of the inner log line — this is the standard Kubernetes
component log format (`kube-apiserver`, `kube-controller-manager`, etc.
all use it), not specific to this dashboard.

| Field | Meaning | Extraction |
| --- | --- | --- |
| `log` | The actual apiserver log line (klog format) | `json field=_raw "log"` |
| `severity` | Single-letter klog level (`I`/`W`/`E`/`F`) | `parse regex field=log "^(?<severity>.)(?:[0-9])"` |

## Known-good example searches

**Note:** the source dashboard also has Metrics-type panels (API request
rate/latency via `metric=apiserver_request_total`, using `quantize`/
`rate`) — out of scope for this file (Log Search Job API / log queries
only); omitted below.

### Categorical (`count by`, no `timeslice`)

**Log volume by severity** — from `Kubernetes - API Server / Severity
Breakdown`
```
_sourceCategory={{ApiServerLogSource}}
| json field=_raw "timestamp"
| json field=_raw "log"
| parse regex field=log "^(?<severity>.)(?:[0-9])"
| count by severity
| sort by _count
```
(`{{ApiServerLogSource}}` substituted to `Labs/Kubernetesv2/core/api-server`
for direct use — see Scope above.)

### Multi-series time series (`timeslice` + `transpose`)

**Severity mix over time** — from `Kubernetes - API Server / Severity
Over Time`
```
_sourceCategory=Labs/Kubernetesv2/core/api-server
| json field=_raw "timestamp"
| json field=_raw "log"
| parse regex field=log "^(?<severity>.)(?:[0-9])"
| timeslice 1h
| count by _timeslice, severity
| transpose row _timeslice column severity
| fillmissing timeslice(1h)
```
`fillmissing timeslice(1h)` fills empty buckets so the resulting chart
has no gaps — worth copying into other timeslice+transpose queries over
sparse log volume.

### Other shapes present (filtered recent-event listing)

**Recent error-level log lines** — from `Kubernetes - API Server / Error
Logs`
```
_sourceCategory=Labs/Kubernetesv2/core/api-server (error OR warning)
| json field=_raw "log"
| parse regex field=log "^(?<severity>.)(?:[0-9])"
| where severity == "E"
| formatDate(_messageTime,"MM-dd-yyyy HH:mm") as MessageTime
| count by MessageTime,severity,log
```
Not a real aggregate — `count by` over near-unique fields (`log`,
`MessageTime`) is a way to present a filtered raw-event table via the
`records` result type rather than a categorical rollup.

No single-value, map, or box-plot panels found for the Logs-type queries
in this dashboard — not included rather than invented.

## Common pivots / related skills

- `common-query-patterns`, `operator-ordering` — the klog severity regex
  above is a good candidate for `common-query-patterns`' categorical
  template once scope is confirmed.
- Other same-technology dashboards in this org: `Kubernetes - Controller
  Manager`, `Azure Kubernetes Service - Controller Manager`, `Azure
  Kubernetes Service - Cloud Control Manager`, `EKS - Controller
  Manager` — worth checking `discovery-dashboard-reuse` again if the use
  case is controller-manager rather than API-server logs specifically.

## Provenance & freshness

- Generated 2026-09-08 against Sumo Logic's public demo/training org via
  `log-domain-skill-authoring`, using `discovery-dashboard-reuse` only
  (dashboard `WgPX6ihJdwkVsdpNyX2ZUn9juNR7fwuquJNe322QteIiqppQwaMpZWSrhO4I`,
  "Kubernetes - API Server").
- Confirmed by live sample: **no** — `_sourceCategory` came only from the
  dashboard variable's saved default. Run a small sample query against
  `Labs/Kubernetesv2/core/api-server` before relying on this scope value
  for anything beyond a rough first pass, and update `INDEX.md`'s status
  to `confirmed` once done. This example deliberately keeps
  `needs-sample` status to show how that gets flagged rather than
  presented as verified.
- Re-verify if: this file is old, the scope value above returns zero
  results on a sample, or this org's Kubernetes log shipping changes.
  **In practice: don't rely on this file at all — it's a point-in-time
  example, not maintained.**
