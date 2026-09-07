---
name: log-search-journey
description: >
  Entry point for a new Sumo Logic log search or investigation, to load
  before any other skill in this repo. Works out which persona's path
  applies (observability/troubleshooting, security/SIEM investigation, or
  Sumo Logic admin/audit) and which stage of the log search journey
  (reuse, scope, sample/format, map fields, craft, iterate) the request is
  at, then routes to the specific skill(s) that cover it. This is a router
  only — it does not itself explain query syntax, scoping, or discovery
  mechanics; each linked skill does that. Use when a request is phrased as
  a problem or question rather than an already-specified query ("find
  errors for service X", "investigate this alert", "what did user Y do",
  "how much are we spending on search", "where do I even start with this
  in Sumo Logic"), or when it's unclear which of this repo's other skills
  applies. Triggers on: "search sumo logic for", "investigate in sumo
  logic", "run a search in sumo logic", "troubleshoot in sumo logic",
  "help me find logs for", "log search for an incident", "where do I
  start with sumo logic", "which skill do I need for this", "sumo logic
  investigation", "look into this in sumo logic".
---

## Why this skill exists

Getting from "I have a problem to solve" to a correctly scoped Sumo Logic
search is rarely one step, and this repo deliberately splits each
stage/concern into its own narrow skill rather than one large skill that
tries to do everything (see `skills/README.md`). That's good for depth,
but it means an agent facing a fresh, informally-phrased request has
nowhere obvious to start. This skill is that starting point: it doesn't
teach query syntax or discovery mechanics itself, it tells you which of
the other skills to load, in what order, for the persona and journey
stage at hand.

Load this skill first for a new request; once you know the persona and
stage, load the 1-3 skills it points to and drop this one.

## Step 0: identify the persona

Persona drives which skills matter most and where the data actually
lives. Requests can straddle personas — treat this as "which skill set to
reach for first," not an exclusive label.

| Persona | Typical asks | Data usually lives in | Skills that matter most |
| --- | --- | --- | --- |
| **Observability / troubleshooting** (developer, SRE, product owner) | "Why is X slow/erroring", "show traffic for service Y", "what changed before this alert fired" | App/infra logs in the default or user-defined partitions | [`discovery-without-metadata`](../discovery-without-metadata/SKILL.md) or [`discovery-profile-scope`](../discovery-profile-scope/SKILL.md), [`query-scoping-efficiency`](../query-scoping-efficiency/SKILL.md), [`common-query-patterns`](../common-query-patterns/SKILL.md), [`operator-ordering`](../operator-ordering/SKILL.md) |
| **Security analyst / engineer** (SIEM investigation) | "What did user/IP/host X do", "show failed logins", "correlate this insight with raw logs" | Cloud SIEM normalized records/signals (`sec_record_*`, `sec_signal`) and/or the general logs behind them | [`search-siem-investigation`](../search-siem-investigation/SKILL.md), [`search-indexes-partitions`](../search-indexes-partitions/SKILL.md) |
| **Sumo Logic admin** | "Who's running expensive searches", "which source category costs the most", "what changed in account config" | System/audit indexes: `sumologic_volume`, `sumologic_search_usage_per_query`, `sumologic_audit`/`sumologic_audit_events`, `sumologic_system_events` | [`search-indexes-partitions`](../search-indexes-partitions/SKILL.md) — see its `references/sumologic-volume.md` and `references/sumologic-search-usage.md` |

A security analyst asking "why is my search so slow" is briefly wearing
the admin hat; an admin chasing down an anomalous cost spike may end up
needing `search-siem-investigation` if the source turns out to be
security tooling. Follow the question, not the job title.

## Step 1: place the request on the journey

Every request sits somewhere on this six-stage arc, and later stages
depend on earlier ones being settled correctly:

1. **Reuse** — is there already a saved search, dashboard, or alert close
   to this? (Library/Apps in the UI; programmatically,
   [`discovery-dashboard-reuse`](../discovery-dashboard-reuse/SKILL.md)
   covers finding a relevant dashboard and mining its panels for
   known-good query text — usually the fastest and most reliable
   equivalent, since most orgs already have Sumo apps or custom
   dashboards around their key platforms and services. Admins/power
   users with `sumologic_search_usage_per_query` access have a further
   option: mining that view for a prior query other users have already
   run against this data — see `search-indexes-partitions`.)
2. **Scope** — confirm `_sourceCategory`/`_index`/`_view`. Unknown yet →
   [`discovery-without-metadata`](../discovery-without-metadata/SKILL.md).
   Known already → [`discovery-profile-scope`](../discovery-profile-scope/SKILL.md).
3. **Sample & format** — confirm the log format (JSON, key-value,
   custom) — also `discovery-profile-scope`.
4. **Map fields** — which fields matter, are they already parsed or do
   they need `parse`/`json`? — also `discovery-profile-scope`, plus
   [`operator-ordering`](../operator-ordering/SKILL.md) for where parsing
   belongs in the pipeline.
5. **Craft** — write the real query: scope + filter + parse + aggregate +
   format. [`query-scoping-efficiency`](../query-scoping-efficiency/SKILL.md)
   (cost/latency), [`common-query-patterns`](../common-query-patterns/SKILL.md)
   (templates for the common shapes), `operator-ordering` (pipeline
   structure).
6. **Iterate** — security investigation pivots on an entity (user, host,
   IP) across sources — see `search-siem-investigation`. Observability
   work drills from a symptom down to root cause — no single skill covers
   this; combine `common-query-patterns` and `operator-ordering` across a
   short series of narrowing queries.

If this repo's root `README.md` is present, its `### The log search
journey` section has the full narrative behind these six stages; this
skill is the persona-oriented index into it and works standalone if that
file isn't there.

## Decision tree

```
What's the goal?
├─ Reuse a known-good query/dashboard first?
│    → Check Library/Apps in the UI, or programmatically:
│      discovery-dashboard-reuse (find a relevant dashboard, mine its
│      panel queries) — admins/power users can additionally search
│      sumologic_search_usage_per_query for prior queries against this
│      data (search-indexes-partitions)
│
├─ Investigating a security event / SIEM entity (user, host, IP, insight)?
│    → search-siem-investigation
│      (+ search-indexes-partitions for the security-tier partition types)
│
├─ Auditing Sumo Logic itself (ingest cost, who ran what search,
│  admin/auth activity, platform health)?
│    → search-indexes-partitions
│      (sumologic_volume / sumologic_search_usage_per_query /
│       sumologic_audit* / sumologic_system_events)
│
└─ Everything else (app/infra troubleshooting, general log exploration)?
     → discovery-without-metadata (scope unknown)
       or discovery-profile-scope (scope known)
     → query-scoping-efficiency
     → common-query-patterns + operator-ordering
     → ai-agent-result-shaping (if results feed an agent/LLM, not a human)
```

## Cross-cutting skills (any persona, any stage)

- [`search-job-api-best-practices`](../search-job-api-best-practices/SKILL.md)
  — calling `/api/v1/search/jobs` directly (create/poll/fetch/delete, rate
  limits, the `pendingErrors`-masquerading-as-empty-results gotcha).
- [`ai-agent-result-shaping`](../ai-agent-result-shaping/SKILL.md) — the
  result feeds an LLM/agent/MCP-style caller and must stay small.
- [`scheduled-views-overview`](../scheduled-views-overview/SKILL.md) — the
  query will run repeatedly and pre-aggregation might help.

## What this skill does not cover

- **Insights, Detection Rules, Alerts, Dashboard CRUD.** Out of scope for
  this repo — it's a Search Job API reference, not a Cloud SIEM config
  client. If Sumo's own Investigator MCP skill is available in the
  session, defer to it for those object types (`getInsights`, `getRules`,
  `alertsSearch`, `listDashboards`, etc.), then come back to this journey
  for the underlying log evidence.
- **Choosing MCP vs. CLI vs. the direct Python client as the execution
  method.** That's a session/harness decision, not a query-content
  concern, and it's specific to whatever repo or environment you're in —
  it does not belong in a portable skill. If this repo's `AGENTS.md` is
  present, follow its "Routing: how to run a search or dashboard report"
  section; otherwise use whichever Search Job API access method the
  session provides.
