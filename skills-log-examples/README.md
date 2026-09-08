# Log-domain skill examples

Four real, worked examples of what `log-domain-skill-authoring` produces
for one technology in one Sumo Logic instance — AWS CloudTrail,
Kubernetes API Server, Azure Audit, Nginx Ingress. **Illustration only,
not portable content**: these follow the format defined in
[`skills/discovery-log-domains/references/log-domain-skill-template.md`](../skills/discovery-log-domains/references/log-domain-skill-template.md),
but a *real* generated log-domain skill is instance-specific working
data, not something this repo distributes — see
["Why this can't be a real, live log-domain skill"](#why-this-cant-be-a-real-live-log-domain-skill)
below.

## Where these came from

Generated 2026-09-08 against Sumo Logic's own public demo/training
org (the `Labs/...` source-category naming below is that org's own
convention, not any customer's) using
`sumosearch discover dashboards` + `report describe --queries` against
four of that org's built-in sample dashboards, plus a couple of raw-log
samples to confirm format. No customer or production data is
involved — this is Sumo's own training sandbox, the same one anyone can
query by pointing `sumosearch`/`sumo_search_client.py` at a demo
credential set. One client IP address in the Nginx example's raw-log
excerpt was replaced with an [RFC 3849](https://www.rfc-editor.org/rfc/rfc3849)
documentation-range address out of general caution; nothing else here
needed redaction.

| Example | What it demonstrates |
| --- | --- |
| [`aws-cloudtrail/SKILL.md`](aws-cloudtrail/SKILL.md) | JSON logs parsed with literal string-anchored `parse` (not `json auto`); a "resolve the caller identity" pattern that falls back from `userName` to an assumed-role principal; categorical + timeslice/`transpose` query shapes; a "filtered recent-event listing" shape that isn't a real time series despite using `timeslice`. |
| [`kubernetes-api-server/SKILL.md`](kubernetes-api-server/SKILL.md) | The containerized-workload log-wrapper gotcha (`{"log": "...", "timestamp": "..."}` wrapping the real klog-format line); a scope resolved only from a dashboard variable's default (`needs-sample` status, not `confirmed`) — see how that's flagged rather than presented as verified. |
| [`azure-audit/SKILL.md`](azure-audit/SKILL.md) | One `_sourceCategory` covering **two structurally different JSON shapes** (EventHub-streamed vs. Azure Insight-API-collected) and the dual-`json`-extraction-plus-`concat()` pattern that normalizes them into one field set; a geo-map panel via `lookup ... from geo://location`. |
| [`nginx-ingress/SKILL.md`](nginx-ingress/SKILL.md) | Classic space-delimited "combined log format" needing named `parse regex` (not simple field-splitting); a live sample that *disproved* the source dashboard's own defensive JSON-wrapper check for this org's actual data — confirmed the wrapper guard was a harmless no-op rather than assuming it; single-value, map, and multi-aggregate-time-series (not `transpose`) panel shapes. |

Read these to see the format applied to real query text and real
(sanitized) sample data, not just the template's placeholder prose.

## Why this can't be a real, live log-domain skill

A real log-domain skill's entire value is *this org's actual, current*
`_sourceCategory`/`_index` values and field structure — meaningless, or
actively wrong, if reused against a different instance, and liable to
drift out of date even for the same instance. Committing one into
version control gives every reader a file that looks authoritative but
is neither current nor theirs. That's why
`log-domain-skill-authoring` writes real output to
`~/sumo-search/output/<instance>/skills/<domain>/SKILL.md` (see
[`skills/discovery-log-domains`](../skills/discovery-log-domains/SKILL.md))
instead of anywhere in this repo, and why everything in this folder is
explicitly labeled `EXAMPLE ONLY` — read for the shape and the
discovery patterns, not for the literal values.

## Related

- [`skills/discovery-log-domains`](../skills/discovery-log-domains/SKILL.md) — reads real generated files back (never this folder).
- [`skills/log-domain-skill-authoring`](../skills/log-domain-skill-authoring/SKILL.md) — produces them.
- [`skills/discovery-dashboard-reuse`](../skills/discovery-dashboard-reuse/SKILL.md) — the query-mining method behind the "known-good example searches" section in each file here.
