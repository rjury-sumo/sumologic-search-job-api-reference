# Search-Job API Reference — Skills

Lightweight, single-purpose skills (Agent Skills format — YAML frontmatter
`name`/`description`, no ties to any specific harness) for an AI agent
querying Sumo Logic — whether through `sumo_search_client.py` one directory
up, the `sumosearch` CLI in `../cli/`, or Sumo's official `runSearchJob`
MCP tool. Copy this `skills/` directory alongside the client, or use it
standalone — nothing here depends on the rest of this repo.

Each skill is scoped to one concern; load the one matching the task at
hand rather than all of them. They split into three groups:

**Start here** — the entry point for a fresh, informally-phrased request:

| Skill | Load when... |
| --- | --- |
| [`log-search-journey`](log-search-journey/SKILL.md) | Starting a new log search or investigation and it's not yet clear which skill applies — routes by persona (observability/troubleshooting, security/SIEM, admin) and by journey stage (reuse, scope, sample, map fields, craft, iterate) to the specific skill(s) needed. |
| [`discovery-log-domains`](discovery-log-domains/SKILL.md) | The request names a specific technology (AWS CloudTrail, Kubernetes, Nginx, ...) — check for an already-generated, org-specific log-domain skill for it *before* running scope/format/query discovery from scratch. Load before `discovery-without-metadata`/`discovery-profile-scope`/`discovery-dashboard-reuse` whenever the technology is nameable. |
| [`discovery-dashboard-reuse`](discovery-dashboard-reuse/SKILL.md) | Journey stage 1 ("Reuse") — find a dashboard already relevant to the use case and mine its panels for known-good query text, before scoping/crafting a search from scratch. |

**Calling the API correctly** (client/transport concerns):

| Skill | Load when... |
| --- | --- |
| [`search-job-api-best-practices`](search-job-api-best-practices/SKILL.md) | Writing or reviewing a script/client that talks to `/api/v1/search/jobs` directly — rate limiting, retries, pagination, state-machine handling, time-splitting large exports. Paired with `sumo_search_client.py`, which implements every rule here. |

**Authoring the query itself** (content/pipeline concerns), once the client is calling the API correctly:

| Skill | Load when... |
| --- | --- |
| [`query-scoping-efficiency`](query-scoping-efficiency/SKILL.md) | Writing or reviewing any query for scan cost/latency — partition scope, bloom-filter keywords, index-time fields, time range. |
| [`discovery-without-metadata`](discovery-without-metadata/SKILL.md) | The partition/source category isn't known yet — find it from a keyword or technology name. |
| [`discovery-profile-scope`](discovery-profile-scope/SKILL.md) | The partition/source category is already known — enumerate other metadata dimensions and sample raw logs to confirm schema. |
| [`search-indexes-partitions`](search-indexes-partitions/SKILL.md) | Choosing/scoping `_index=` — partition types, tiers, and the built-in system/audit indexes: `sumologic_audit`/`sumologic_audit_events` (admin/auth activity), `sumologic_volume` (ingest volume — key discovery source for matching metadata strings), `sumologic_system_events` (platform health), and `sumologic_search_usage_per_query` (search cost/compliance auditing, and mining the `query` column for existing workloads against a data source). |
| [`operator-ordering`](operator-ordering/SKILL.md) | Deciding where scope, filter, parse, aggregate, and format belong in the pipeline. |
| [`common-query-patterns`](common-query-patterns/SKILL.md) | Building an aggregate, time series, multi-series (transpose), or time-compare query from a template. |
| [`ai-agent-result-shaping`](ai-agent-result-shaping/SKILL.md) | The result feeds an LLM/agent/MCP-style caller and needs to stay small — pre-aggregate, cap rows, trim fields. |
| [`scheduled-views-overview`](scheduled-views-overview/SKILL.md) | A recurring query might benefit from (or already targets) a scheduled view (`_view=`). |
| [`search-siem-investigation`](search-siem-investigation/SKILL.md) | Querying Cloud SIEM data — normalized records (`sec_record_*`), signals (`sec_signal`), or insight audit events. Cloud SIEM customers only. |

**Persisting discovery for reuse** (writes an org-specific file *outside* this repo — see below):

| Skill | Load when... |
| --- | --- |
| [`log-domain-skill-authoring`](log-domain-skill-authoring/SKILL.md) | A technology was just discovered from scratch (scope + format + example queries) and is likely to come up again — research it once and write a per-instance log-domain skill file so `discovery-log-domains` can serve it instantly next time. Chains `discovery-without-metadata` → `discovery-profile-scope` → `discovery-dashboard-reuse` into one assembled output; a good fit for a scoped background agent (see `.claude/agents/log-domain-discovery.md`). |

`log-domain-skill-authoring`'s output contains real, org-specific
`_sourceCategory`/`_index` values, so — unlike every other skill in this
directory — it deliberately writes to `~/sumo-search/output/<instance>/
skills/`, not into this repo. This directory stays 100% portable and
org-agnostic; only the two skills above (which teach the *method*) live
here. `../skills-log-examples/` at the repo root has four worked
examples of that output, generated against Sumo Logic's public
demo/training org and marked `EXAMPLE ONLY` — read for the shape, not as
a live reference.

## Suggested reading order for a new integration

0. `log-search-journey` (for a fresh, informally-phrased request — routes to the rest of this list by persona and journey stage)
1. `discovery-log-domains` (a named technology may already have a saved scope+format+examples bundle for this instance — check before anything below) → `log-domain-skill-authoring` (persist a fresh discovery for next time)
2. `discovery-dashboard-reuse` (check for a relevant dashboard/known-good query before scoping from scratch)
3. `search-job-api-best-practices` (if you're calling the Search Job API directly, not just through `sumo_search_client.py`)
4. `discovery-without-metadata` (if scope isn't known yet) → `discovery-profile-scope` (once it is, to sample and confirm schema)
5. `query-scoping-efficiency`
6. `search-indexes-partitions` (choosing `_index=`; includes system/audit indexes)
7. `common-query-patterns` + `operator-ordering`
8. `ai-agent-result-shaping` (if the caller is an agent/LLM, not a human dashboard)
9. `scheduled-views-overview` (only if the query will run repeatedly)
10. `search-siem-investigation` (Cloud SIEM customers only, when the target data is `sec_record_*`/`sec_signal`/insights)
