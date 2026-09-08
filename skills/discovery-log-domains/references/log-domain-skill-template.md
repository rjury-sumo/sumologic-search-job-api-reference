# Log-domain skill template

The format every generated per-instance log-domain skill file follows.
`log-domain-skill-authoring` produces files in this shape; this skill
(`discovery-log-domains`) reads them back. Both must stay in sync with
this template — update all three together if the shape changes.

One file per **technology**, per **instance** — don't bundle unrelated
technologies into one file, and don't share one file across instances
(scope values are instance-specific by definition).

**File location:** `~/sumo-search/output/<instance>/skills/<domain-slug>/SKILL.md`
`<domain-slug>` is a short kebab-case technology name — `aws-cloudtrail`,
`kubernetes-api-server`, `azure-audit`, `nginx-ingress`. One row per file
also goes in the sibling `~/sumo-search/output/<instance>/skills/INDEX.md`
— see its own template further down.

**This is a normal Agent Skill** (YAML frontmatter + Markdown body) so it
can be loaded with the `Skill` tool / read directly like any other skill
— it just lives outside `skills/` because its content is org-specific,
not portable. Nothing about its *shape* is instance-specific.

---

## `SKILL.md` template

````markdown
---
name: log-<domain-slug>
description: >
  Org-specific reference for querying <Technology Name> logs in the
  <instance> Sumo Logic instance: confirmed metadata scope, log
  format/fields, and a sample of known-good example searches. Generated
  <YYYY-MM-DD> by log-domain-skill-authoring against instance
  `<instance>`. Re-run the authoring workflow to refresh if this file is
  more than ~90 days old, or if a scope value below stops matching data.
  Triggers on: "<technology> logs", "query <technology>", "<technology>
  in sumo logic", plus a few domain-specific phrasings.
metadata:
  instance: <instance>
  generated: <YYYY-MM-DD>
  generated_by: log-domain-skill-authoring
  source_dashboards: [<dashboard id>, ...]   # omit if none used
  source_method: dashboard-reuse | search-usage-audit | raw-log-discovery
---

## Scope

| Metadata | Value | Confidence |
| --- | --- | --- |
| `_sourceCategory` | `<value or wildcard>` | confirmed by sample \| from dashboard variable default only \| inferred |
| `_index`/`_view` | `<value>` | ... |
| `_collector` (if narrow) | `<value>` | ... |

Notes on scope quirks specific to this instance — sub-categories under a
wildcard, multiple partitions holding the same category, a dashboard
variable that had to be resolved to a concrete value, etc.

## Log format & key fields

**Format:** JSON \| key-value \| space-delimited/custom \| mixed (more
than one shape under the same scope — say how to tell them apart).

**Parsing approach used by the example searches below:** e.g. "top-level
keys pulled with `parse "\"key\":\"*\"" as x`, nested paths with
`json field=_raw "a.b.c"`" — whatever the confirmed searches actually do,
not a generic recommendation.

**Any wrapper/nesting gotcha** — e.g. a log shipper wrapping the real
message inside an outer JSON envelope (`{"log": "...", "stream": "...",
"time": "..."}`), needing a second parse pass on the inner field.

| Field | Meaning | Extraction |
| --- | --- | --- |
| `<field>` | <what it means for this use case> | `<parse/json snippet>` |

## Known-good example searches

One subsection per shape actually found — skip any shape not present in
this domain's dashboards/audit history rather than inventing one. Each
entry: a one-line use case, where it came from, and the query with any
`{{variable}}` substituted to a concrete value (note the substitution).

### Categorical (`count by` / `sum() by`, no `timeslice`)

**<use case>** — from `<dashboard title> / <panel title>`
```
<query, {{var}} substituted>
```

### Time series (`timeslice`, single series)

...

### Multi-series time series (`timeslice` + `transpose`, or multiple
explicit aggregates per `_timeslice`)

...

### Other shapes present (single value, map via `lookup ... geo://location`, box plot, ...)

...

## Common pivots / related skills

- Generic skills that apply once scope/format are known:
  `common-query-patterns`, `operator-ordering`, `query-scoping-efficiency`.
- `search-siem-investigation` if this domain's data also lands in Cloud
  SIEM normalized records/signals.
- Other same-technology dashboards worth checking if these examples
  don't cover the current use case: `<dashboard title>` (`<id>`).

## Provenance & freshness

- Generated `<date>` against `<instance>` via `log-domain-skill-authoring`,
  using `<source_method>`.
- Confirmed by live sample: yes/no — note anything taken only from a
  dashboard variable default rather than a live query.
- Re-verify if: this file is old, a scope value here silently returns
  zero results, or the org's ingestion for this technology has changed.
````

---

## `INDEX.md` template (one per instance, sibling to the domain folders)

```markdown
# Log-domain skills — <instance>

| Domain | Path | Scope | Generated | Status |
| --- | --- | --- | --- | --- |
| AWS CloudTrail | `aws-cloudtrail/SKILL.md` | `_sourceCategory=Labs/AWS/CloudTrail*` | 2026-09-08 | confirmed |
| Kubernetes API Server | `kubernetes-api-server/SKILL.md` | `_sourceCategory={{ApiServerLogSource}}` (dashboard variable, not confirmed by sample) | 2026-09-08 | needs-sample |
```

`Status` is a quick freshness/confidence flag — `confirmed` (scope
verified by a live sample), `needs-sample` (scope came from a dashboard
variable default or similar, never sampled directly), or `stale`
(known out of date, re-run authoring). `discovery-log-domains` reads
this table first, before opening any individual domain file.
