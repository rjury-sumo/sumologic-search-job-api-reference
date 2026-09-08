---
name: discovery-log-domains
description: >
  Check for an already-discovered, org-specific log-domain skill before
  running scope/format/query discovery from scratch for a technology
  (AWS CloudTrail, Kubernetes, Azure Audit, Nginx, ...). A log-domain
  skill bundles confirmed metadata scope, log format/fields, and a sample
  of known-good queries for one technology in one Sumo Logic instance —
  generated once by `log-domain-skill-authoring`, reused on every later
  request for that technology instead of re-running discovery each time.
  Load this FIRST, before `discovery-without-metadata`,
  `discovery-profile-scope`, or `discovery-dashboard-reuse`, whenever the
  target technology is nameable (not just "some logs somewhere"). Triggers
  on: "query <technology> logs", "is there already a reference for this
  log type", "have we looked at this log source before", "cloudtrail
  logs", "kubernetes logs", "nginx logs", "azure audit logs", or any
  request naming a specific platform/technology before scope is known.
---

## What this is

`log-domain-skill-authoring` researches one technology once (scope,
format, example queries — see its own skill) and writes the result to a
per-instance file. This skill is the read/lookup side: a cheap check for
that file *before* paying for fresh discovery. It's the persisted,
richer sibling of `discovery-dashboard-reuse` — that skill finds query
examples fresh every time; a log-domain skill is the saved output of
having already done that (plus scope confirmation and format notes)
for one technology.

Not a discovery method itself — it has nothing to say if no log-domain
skill exists yet for the target technology. In that case, fall straight
through to `discovery-without-metadata` (scope) → `discovery-profile-scope`
(format) → `discovery-dashboard-reuse` (examples), then consider running
`log-domain-skill-authoring` afterward so the next request doesn't pay
this cost again.

## File location

```
~/sumo-search/output/<instance>/skills/INDEX.md
~/sumo-search/output/<instance>/skills/<domain-slug>/SKILL.md
```

Mirrors the existing per-instance cache convention used for the
dashboard-list cache (`~/sumo-search/output/<instance>/dashboards/
list-<mode>.json`) — instance-scoped, local, never committed to this
repo (these files contain real `_sourceCategory`/`_index` values specific
to one org, so they can't live in the portable `skills/` directory — see
`log-domain-skill-authoring` for why). Full format spec and both files'
exact shape: [`references/log-domain-skill-template.md`](references/log-domain-skill-template.md).
Want to see the format applied to real query text before generating your
own? [`skills-log-examples/`](../../skills-log-examples/README.md) at
the repo root has four worked examples (clearly marked `EXAMPLE ONLY`) —
not something this skill reads, purely illustration.

## Workflow

**1. Read `INDEX.md` first** (one instance-scoped file, not a directory
scan) — `~/sumo-search/output/<instance>/skills/INDEX.md`. If it doesn't
exist yet, no log-domain skill has ever been generated for this instance;
go straight to the normal discovery chain.

**2. Match the target technology against the index's `Domain` column** —
exact or fuzzy (e.g. "cloudtrail" matches "AWS CloudTrail"). If nothing
matches, same fallback as above.

**3. Check the matched row's `Status`:**

- **`confirmed`** — scope was verified by a live sample. Read the domain
  file (`<slug>/SKILL.md`) and use its scope/format/example queries
  directly; no further discovery needed for this request.
- **`needs-sample`** — scope came from a dashboard variable default or
  similar, never confirmed live. Usable as a strong starting point, but
  worth a quick confirming sample (`discovery-profile-scope`, one small
  query) before relying on it for anything beyond a rough first pass.
- **`stale`** — known out of date. Treat as a starting point only; hand
  off to `log-domain-skill-authoring` to refresh before trusting the
  specific values.

**4. Sanity-check age even for `confirmed` rows** — `Generated` more than
~90 days old, or an org known to relabel `_sourceCategory` values
periodically, is worth a spot-check (one small sample query against the
recorded scope) before committing to it for anything consequential.
Silent zero results on that spot-check means the scope has drifted —
refresh via `log-domain-skill-authoring` rather than debugging the
consuming query.

**5. If discovery ran fresh anyway** (no match, or a `stale`/failed
sanity-check row) — after finishing the request, suggest running
`log-domain-skill-authoring` to persist what was found, so the next
request for this technology in this instance hits step 3 instead of
repeating the same discovery work.

## Related Skills (this folder)

None — this is a single-concern skill.

## Related Skills (other folders)

- `log-domain-skill-authoring` — the write side: researches a technology
  and produces/refreshes the files this skill reads.
- `discovery-without-metadata` — the fallback when no log-domain skill
  exists yet; this skill's "Fast path 0" cross-links back here.
- `discovery-dashboard-reuse` — what a log-domain skill's example-query
  section is generated from; still worth loading directly for a one-off
  request that doesn't warrant persisting a whole domain skill.
- `discovery-profile-scope` — the confirming sample for a `needs-sample`
  row above.
