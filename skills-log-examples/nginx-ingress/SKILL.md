---
name: example-log-nginx-ingress
description: >
  EXAMPLE ONLY — illustrates the log-domain-skill format
  (log-domain-skill-authoring / discovery-log-domains) using Nginx
  Ingress access/error logs from Sumo Logic's public demo/training org.
  Not a live reference and not meant to be loaded as an active skill —
  see ../README.md. Generated 2026-09-08.
metadata:
  instance: demo (Sumo Logic's public demo/training org — not a real customer)
  generated: 2026-09-08
  generated_by: log-domain-skill-authoring
  source_dashboards: ["hW2AUn4412GwYCN3kkzfoeEc6QvhD8ch0qTiLe28MOWHtCA9JHviOXHUPeUO"]
  source_method: dashboard-reuse + raw-log sample
---

> **EXAMPLE ONLY.** This illustrates what `log-domain-skill-authoring`
> produces — see [`../README.md`](../README.md). A real generated file
> lives at `~/sumo-search/output/<instance>/skills/<domain>/SKILL.md`,
> never in this repo. Read this for the format and discovery patterns,
> not as a current, actionable reference. The raw-log excerpt below has
> its client IP address replaced with an
> [RFC 3849](https://www.rfc-editor.org/rfc/rfc3849) documentation-range
> address.

## Scope

| Metadata | Value | Confidence |
| --- | --- | --- |
| `_sourceCategory` | `Labs/nginx-ingress-ulm` | confirmed by sample |
| `_index`/`_view` | not confirmed — default scope resolved it | needs-sample |

## Log format & key fields

**Format:** plain-text nginx **combined log format**, one line per
request — *not* JSON in this org's actual data, despite the source
dashboard defensively checking for one (see gotcha below).
```
2001:db8:6319:eae0:fc0c:987b:d3ac:18e6 - - [08/Sep/2026:00:11:54 +0000] "POST /api/v1/event/log HTTP/2.0" 200 37 "-" "okhttp/4.6.0"
```

**Wrapper gotcha, confirmed NOT to apply here:** every example query
starts with `json auto maxdepth 1 nodrop | if (isEmpty(log), _raw, log)
as nginx_log_message` — a defensive check for the containerized-workload
JSON-envelope pattern (`{"log": "...", ...}`) seen in the Kubernetes
domain example. A live sample confirmed this org's nginx-ingress logs
are **not** wrapped — `log` is always empty and `nginx_log_message`
falls through to `_raw` directly. Keep the `if (isEmpty(log), _raw,
log))` guard anyway when copying a query from here (harmless if
unwrapped, correct if a future source *is* wrapped) rather than assuming
the same for every instance.

**Parsing approach:** the combined log format isn't simply space-
delimited (quoted fields contain spaces), so every field is pulled via
one or two named `parse regex` patterns:
```
parse regex field=nginx_log_message "(?<Client_Ip>(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
| parse regex field=nginx_log_message "(?<Method>[A-Z]+)\s(?<URL>\S+)\sHTTP/[\d\.]+\"\s(?<Status_Code>\d+)\s(?<Size>[\d-]+)\s\"(?<Referrer>.*?)\"\s\"(?<User_Agent>.+?)\".*"
```

| Field | Meaning | Extraction |
| --- | --- | --- |
| `Client_Ip` | Requester IP (v4 or v6) | regex above |
| `Method` | HTTP method | regex above |
| `URL` | Requested path | regex above |
| `Status_Code` | HTTP status code | regex above |
| `Size` | Response size, bytes | regex above |
| `User_Agent` | Client user agent string | regex above |

Error-log lines use a different shape (`[level] pid#tid: message,
client: ..., server: ..., request: "...", host: "..."`) — separate regex:
```
parse regex field=nginx_log_message "\s\[(?<Log_Level>\S+)\]\s\d+#\d+:\s(?:\*\d+\s|)(?<Message>[A-Za-z][^,]+)(?:,|$)"
| parse field=nginx_log_message "client: *, server: *, request: \"* * HTTP/1.1\", host: \"*\"" as Client_Ip, Server, Method, URL, Host nodrop
```

## Known-good example searches

**Note:** the source dashboard also has Metrics-type panels
(`nginx_ingress_nginx_http_requests_total`, connection counts) — out of
scope for this file; omitted below.

### Categorical (`count by`, no `timeslice`)

**Client OS/device platform breakdown** — from `Nginx Ingress - Overview
/ Client OS Platforms`
```
_sourceCategory = Labs/nginx-ingress-ulm
| json auto maxdepth 1 nodrop
| if (isEmpty(log), _raw, log) as nginx_log_message
| parse regex field=nginx_log_message "(?<Client_Ip>(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
| parse regex field=nginx_log_message "(?<Method>[A-Z]+)\s(?<URL>\S+)\sHTTP/[\d\.]+\"\s(?<Status_Code>\d+)\s(?<Size>[\d-]+)\s\"(?<Referrer>.*?)\"\s\"(?<User_Agent>.+?)\".*"
| count by User_Agent
| if (User_Agent matches "*iPad*" or User_Agent matches "*iPhone*" or User_Agent matches "*Android*", "Mobile", "PC") as type
| count type
| sort by _count, type asc
```

### Time series (`timeslice`, multiple explicit aggregates — not `transpose`)

**Response status mix over time** — from `Nginx Ingress - Overview /
Responses Over Time`. A different multi-series construction than
`transpose`: instead of pivoting one field's values into columns, this
computes several named `sum()` aggregates together per bucket:
```
_sourceCategory = Labs/nginx-ingress-ulm
| json auto maxdepth 1 nodrop
| if (isEmpty(log), _raw, log) as nginx_log_message
| parse regex field=nginx_log_message "(?<Client_Ip>...)"
| parse regex field=nginx_log_message "(?<Method>[A-Z]+)\s(?<URL>\S+)\sHTTP/[\d\.]+\"\s(?<Status_Code>\d+)\s(?<Size>[\d-]+)\s\"(?<Referrer>.*?)\"\s\"(?<User_Agent>.+?)\".*"
| if(Status_Code matches "2*", 1, 0) as Successes
| if(Status_Code matches "3*", 1, 0) as Redirects
| if(status_code matches "4*", 1, 0) as Client_Errors
| if(Status_Code matches "5*", 1, 0) as Server_Errors
| timeslice by 5m
| sum(Successes) as Successes, sum(Client_Errors) as Client_Errors, sum(Redirects) as Redirects, sum(Server_Errors) as Server_Errors by _timeslice
| sort by _timeslice asc
```

### Other shapes present (single value, map)

**Critical error count (single value)** — from `Nginx Ingress - Overview
/ Critical Error Messages`. No `by` clause at all — a bare `| count`
renders as a single-value panel:
```
_sourceCategory = Labs/nginx-ingress-ulm
| json auto maxdepth 1 nodrop
| if (isEmpty(log), _raw, log) as nginx_log_message
| parse regex field=nginx_log_message "\s\[(?<Log_Level>\S+)\]\s\d+#\d+:\s(?:\*\d+\s|)(?<Message>[A-Za-z][^,]+)(?:,|$)"
| where log_level in ("emerg", "alert", "crit")
| count
```

**Visitor location map** — from `Nginx Ingress - Overview / Visitor
Locations`
```
_sourceCategory = Labs/nginx-ingress-ulm
| json auto maxdepth 1 nodrop
| if (isEmpty(log), _raw, log) as nginx_log_message
| parse regex field=nginx_log_message "(?<Client_Ip>(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})"
| count by Client_Ip
| lookup latitude, longitude, country_code, country_name, region, city, postal_code from geo://location on ip = Client_Ip
| count by latitude, longitude, country_code, country_name, region, city, postal_code
| sort _count
```

No box-plot panel found in this dashboard (the sibling "Nginx Ingress -
Outlier Analysis" dashboard likely has one — not researched here).

## Common pivots / related skills

- `common-query-patterns`, `operator-ordering` — the categorical, multi-
  aggregate time series, and single-value templates above map directly.
- Other same-technology dashboards in this org: `Nginx Ingress - Trends`,
  `Nginx Ingress - Error Logs`, `Nginx Ingress - Threat Intel`, `Nginx
  Ingress - Outlier Analysis`, `Nginx Ingress (Classic) - Trends` — check
  `discovery-dashboard-reuse` again for outlier/box-plot or threat-intel
  use cases specifically.

## Provenance & freshness

- Generated 2026-09-08 against Sumo Logic's public demo/training org via
  `log-domain-skill-authoring`, using `discovery-dashboard-reuse`
  (dashboard `hW2AUn4412GwYCN3kkzfoeEc6QvhD8ch0qTiLe28MOWHtCA9JHviOXHUPeUO`,
  "Nginx Ingress - Overview") plus a live raw-log sample.
- Confirmed by live sample: yes — 2 raw events sampled, confirmed
  `_sourceCategory=Labs/nginx-ingress-ulm` and confirmed the JSON-wrapper
  guard is a no-op for this org's actual data (plain combined log format).
- Re-verify if: this file is old, `_sourceCategory=Labs/nginx-ingress-ulm`
  silently returns zero results, or this org changes how nginx-ingress
  logs are shipped (e.g. moves to a containerized JSON-wrapped shipper).
  **In practice: don't rely on this file at all — it's a point-in-time
  example, not maintained.**
