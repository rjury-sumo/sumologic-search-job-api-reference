---
name: log-domain-skill-authoring
description: >
  Research one log technology in one Sumo Logic instance end to end
  (scope, format, known-good example queries) and write/refresh the
  per-instance log-domain skill file that `discovery-log-domains` later
  reads back — so the next request for this technology in this instance
  skips discovery entirely instead of repeating it. Chains
  `discovery-without-metadata` → `discovery-profile-scope` →
  `discovery-dashboard-reuse` (or, for admins, the search-usage audit
  index) into one assembled output file. Designed to run as a scoped,
  possibly backgrounded research task — see the companion
  `log-domain-discovery` subagent in `.claude/agents/` for Claude Code.
  Triggers on: "build a skill for these logs", "create a reference for
  this log source", "document this log type for reuse", "generate a
  log-domain skill", "refresh the <technology> skill", "research this log
  source and save it for next time".
---

## What this produces, and why it can't live in `skills/`

The output is one file: `~/sumo-search/output/<instance>/skills/
<domain-slug>/SKILL.md`, following
[`discovery-log-domains/references/log-domain-skill-template.md`](../discovery-log-domains/references/log-domain-skill-template.md).
It bundles confirmed `_sourceCategory`/`_index` values, the log format
and field-extraction approach actually used by real queries, and a
shape-diverse sample of known-good example searches — everything needed
to write a new query against this technology without re-running
discovery.

Those scope values are real, org-specific data (`_sourceCategory=Labs/
AWS/CloudTrail*` means nothing to a different org's instance, and may not
even exist there) — committing files like this into `skills/` would
break the "works when copied out with zero other context, not tied to
any specific org" invariant the rest of this repo's skills hold to. So
output goes to the per-instance cache directory instead, alongside the
existing dashboard-list cache — never into this repo, never git-committed.

Power users/admins can share a generated file directly with teammates
who have access to the same instance (Slack it, drop it in a shared
drive, etc.) even though it never enters this repo — that's the "share
with others in their org" use case this format is built for.

## When to run this

- A request names a specific technology with no existing log-domain
  skill (`discovery-log-domains` found no matching `INDEX.md` row).
- An existing row is `stale`, or a `needs-sample`/`confirmed` row just
  failed a sanity-check sample (see `discovery-log-domains` step 4).
- Proactively, for a technology expected to come up repeatedly (a
  platform team's own service, a compliance-relevant log source) —
  authoring once up front is cheaper than re-discovering it every
  incident.

## Workflow

**0. Check first.** Load `discovery-log-domains` and confirm this
technology genuinely isn't already covered (or is stale) before spending
discovery budget re-deriving it.

**1. Scope.** Two starting points, pick based on how likely the
technology is to have existing dashboard content:

- **Dashboard-first (usually cheaper, try this first for any
  reasonably common technology):** `discovery-dashboard-reuse` —
  `sumosearch discover dashboards --match "<technology>"`. A relevant
  dashboard's panel queries often state `_sourceCategory=`/`_index=`
  directly on the scope line, resolving scope and pulling example
  queries in the same pass.
- **Raw-log fallback (no relevant dashboard, or a custom/homegrown log
  source):** `discovery-without-metadata` — the data-volume index, admin
  partition/FER endpoints, or the keyword-anchored raw-log sequence.

Either way, end this step with a concrete `_sourceCategory`/`_index`
value — not a `{{variable}}` token. If the only source was a dashboard
variable's `defaultValue`, that's a `needs-sample` row for now (see
`INDEX.md` template) until step 2 confirms it live.

**2. Format & fields.** `discovery-profile-scope` — sample raw logs
against the confirmed scope, confirm the format (JSON, key-value,
space-delimited, custom), and note the *actual* extraction approach
(`parse` on a literal key pattern vs. `json field=_raw "a.b.c"` vs.
`parse regex` for a non-JSON line) — write down what the real example
queries from step 3 do, not a generic recommendation. Watch for:

- **Wrapper/nesting** — a log shipper wrapping the real line inside an
  outer envelope (`{"log": "...", "stream": "...", "time": "..."}` is
  common for containerized workloads); the actual fields of interest may
  need a second parse pass on the inner field.
- **Multiple shapes under one scope** — the same technology ingested two
  ways (e.g. an event-hub stream vs. a REST-polled API) can produce
  structurally different JSON under the same `_sourceCategory`. If
  found, note both shapes and how a query tells them apart (dashboards
  handle this by parsing both key sets with `nodrop` and `concat`-ing
  whichever populated — a pattern worth copying into the domain file
  directly if seen).

**3. Known-good examples.** `discovery-dashboard-reuse` — pull a
shape-diverse sample from the dashboard(s) found in step 1 (or a fresh
`--match` search if step 1 used the raw-log fallback): categorical, time
series, multi-series (`transpose`, or several explicit aggregates per
`_timeslice`), and any rarer shape present (single value, map via
`lookup ... geo://location`, box plot). **Filter to `queryType=Logs`** —
skip Metrics/Tracing panels. Substitute any `{{variable}}` token with its
dashboard-summary `defaultValue` (or a value relevant to a concrete
example) before writing it into the output file — never leave a literal
`{{...}}` in a query presented as runnable.

Admin/power-user alternative or supplement: `sumologic_search_usage_per_query`
(see `search-indexes-partitions/references/sumologic-search-usage.md`) —
mine prior real queries against this scope run by other users, dashboards,
or scheduled searches. Good for confirming a query shape actually gets
used in practice, or for technologies with no dedicated dashboard.

**4. Assemble and write.** Fill in
[`discovery-log-domains/references/log-domain-skill-template.md`](../discovery-log-domains/references/log-domain-skill-template.md)
with steps 1-3's findings. Write to
`~/sumo-search/output/<instance>/skills/<domain-slug>/SKILL.md`
(`<domain-slug>`: short kebab-case, e.g. `aws-cloudtrail`,
`kubernetes-api-server`). Create the directory if it doesn't exist yet.
Then create-or-update the sibling `~/sumo-search/output/<instance>/skills/
INDEX.md` row for this domain (`Status` = `confirmed` if scope was
verified by a live sample in step 2, `needs-sample` otherwise).

**5. Validate before declaring done.** Re-run one example query from the
assembled file — small time window, `| limit` capped — and confirm it
actually returns without error. A query copied from a dashboard panel
can still fail once its `{{variable}}` is substituted to a concrete
value (e.g. a value that doesn't exist in this account) or if scope
resolution in step 1 was slightly off; catch that now, not on the next
session that trusts this file blindly.

## Transport note

Every command shown across the referenced skills is `sumosearch` CLI —
the default per this repo's `AGENTS.md`. Nothing about the workflow is
CLI-specific: the same steps work via `sumo_search_client.py`/
`sumo_dashboard_client.py` directly, or via Sumo's official MCP tools —
**except** dashboard-based discovery (steps 1 and 3's dashboard path),
which needs this repo's client/CLI specifically (see
`discovery-dashboard-reuse`'s note — Sumo's `runSearchJob` MCP has no
describe/list-dashboards equivalent). An MCP-only session without this
repo's tools available should skip straight to the raw-log fallback in
step 1 and the search-usage-audit alternative in step 3.

## Running this as a scoped agent

This workflow is a good candidate for delegation — bounded scope (one
technology, one instance), a clear "done" condition (step 5's
validation), and output confined to one directory tree outside the repo.
In Claude Code, `.claude/agents/log-domain-discovery.md` is a subagent
pre-scoped to exactly this: it loads this skill, has `sumosearch`/file-write
access, and nothing broader. Launch it per technology rather than asking
it to cover several unrelated technologies in one run — keeps each run's
output file focused and its "done" condition unambiguous.

## Related Skills (this folder)

None — this is a single-concern skill.

## Related Skills (other folders)

- `discovery-log-domains` — the read side this workflow feeds; check it
  first (step 0) so this workflow isn't re-run needlessly.
- `discovery-without-metadata`, `discovery-profile-scope`,
  `discovery-dashboard-reuse` — the three discovery skills this workflow
  chains together; each documents its own method in full.
- `search-indexes-partitions` — `sumologic_search_usage_per_query`
  reference for the admin/power-user alternative in step 3.
- `common-query-patterns` — the categorical/time-series/transpose shape
  vocabulary used to sort examples in step 3.
