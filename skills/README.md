# Search-Job API Reference — Skills

Lightweight, single-purpose skills (Agent Skills format — YAML frontmatter
`name`/`description`, no ties to any specific harness) for an AI agent
querying Sumo Logic — whether through `sumo_search_client.py` one directory
up, the `sumosearch` CLI in `../cli/`, or Sumo's official `runSearchJob`
MCP tool. Copy this `skills/` directory alongside the client, or use it
standalone — nothing here depends on the rest of this repo.

Each skill is scoped to one concern; load the one matching the task at
hand rather than all seven. They split into two groups:

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

## Suggested reading order for a new integration

1. `search-job-api-best-practices` (if you're calling the Search Job API directly, not just through `sumo_search_client.py`)
2. `discovery-without-metadata` (if scope isn't known yet) → `discovery-profile-scope` (once it is, to sample and confirm schema)
3. `query-scoping-efficiency`
4. `search-indexes-partitions` (choosing `_index=`; includes system/audit indexes)
5. `common-query-patterns` + `operator-ordering`
6. `ai-agent-result-shaping` (if the caller is an agent/LLM, not a human dashboard)
7. `scheduled-views-overview` (only if the query will run repeatedly)
8. `search-siem-investigation` (Cloud SIEM customers only, when the target data is `sec_record_*`/`sec_signal`/insights)
