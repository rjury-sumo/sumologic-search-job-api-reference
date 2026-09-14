# Workshop: The Log Search Journey & Agentic Sumo Logic Search Integration

Slide-content outline. Each `##` is one slide: title + bullets, ready to
drop into slide software. Sourced from [`README.md`](../README.md) and the
`skills/*/SKILL.md` files — see inline pointers back to the source skill
for anyone who wants to go deeper live during the session.

---

## Abstract

Writing a Sumo Logic query is rarely the hard part — getting from "I have
a problem" to a query that's correctly scoped, correctly formatted, and
actually affordable to run is. That gap is wider still for an agent
calling the Search Job API directly instead of a human clicking through
autocomplete in the UI: no discovery endpoint, sharp rate limits, silent
zero-result failures, and query shapes that can blow out token or credit
cost by an order of magnitude with no error raised anywhere.

This workshop maps that gap as a six-phase journey — reuse, scope,
sample, map fields, craft, iterate — shared by every persona who searches
Sumo Logic, then shows how this repo turns each phase into something an
agent (or a human) can act on directly: a persona-and-stage router that
picks the right skill for a fresh request, a growing library of
persisted, org-specific discovery that turns "figure this out again" into
"look it up," and a documented set of gotchas — in the API, in query
syntax, in cost model — that are cheap to avoid once known and expensive
to debug once hit.

**By the end, attendees will be able to:**

- Place any log-search request — their own or an agent's — on the
  six-phase journey and know which skill or tool addresses that phase.
- Recognize the Search Job API's sharp edges before they cause an
  incident: the `pendingErrors`-as-empty-results trap, rate-limit scope,
  scan-ratio blowouts on scheduled automations, and query shapes that
  silently inflate token or credit cost.
- Choose confidently among the three ways to run a search in this
  ecosystem — `sumo_search_client.py`, the `sumosearch` CLI, or Sumo's
  official MCP tools — based on what's actually being built.
- Apply persona-specific workflows for observability troubleshooting,
  Cloud SIEM investigation, and Sumo Logic admin/audit use cases.
- Walk away knowing where discovery once done gets persisted for reuse,
  so the next request for a familiar log source starts from an answer
  instead of a cold start.

---

## 1. The Log Search Journey — Phases & Outputs

Getting from "I have a problem" to "a correctly scoped search" is rarely
one step. A user who already knows part of the domain can skip ahead;
over time, good answers get saved to the Library as a head start next time.

| # | Phase | Output |
|---|---|---|
| 1 | **Reuse existing content** — for a named technology, `skills/discovery-log-domains` checks first for an already-persisted scope+format+examples bundle that can short-circuit phases 2–4 entirely; otherwise dashboards, saved searches, or alerts — programmatically, mine a relevant dashboard's panel queries (`skills/discovery-dashboard-reuse`), or (admins/power users) the search-audit index for a prior similar query | A reusable saved asset close to the use case |
| 2 | **Confirm metadata scope** — `_sourceCategory`, `_index`, etc. (UI: autocomplete; API: no discovery endpoint — must be worked around) | Correct metadata scope |
| 3 | **Sample & discover log format** — JSON, key-value, custom | Format & field schema understood |
| 4 | **Map fields** — field browser, value distributions, parse/json/regex needed? | Exact fields/values needed |
| 5 | **Craft the final search** — chain scope → parse → filter → aggregate → format | A syntactically correct search that answers the question |
| 6 | **Iterate** — SOC pivots on an entity to build a timeline; observability drills from symptom to root cause | Investigation timeline or root cause |

> New agent products (e.g. Mobot Log Analysis) compress this into "ask a
> question" — the agent interprets intent, discovers sources, writes
> multiple searches, and summarizes — but it's still doing these same six
> phases under the hood.

---

## 2. How an Agent Actually Starts — `skills/log-search-journey`

The six phases above are the narrative; `skills/log-search-journey` is
the router that turns them into a starting action for an informally
phrased request ("find errors for service X", "investigate this alert",
"where do I even start with this in Sumo Logic").

- **Step 0 — identify the persona.** A routing table maps each persona
  to where its data usually lives and which skills matter most:
  Observability/troubleshooting, Security/SIEM investigation, or Sumo
  Logic admin/audit (detailed in slides 5–7). Requests can straddle
  personas — this is "which skill set to reach for first," not an
  exclusive label.
- **Step 1 — place the request on the 6-stage arc** from slide 1, and
  get pointed to the 1–3 skills that actually cover that stage, in
  order. It doesn't teach query syntax or discovery mechanics itself —
  it dispatches to the skill that does.
- **Decision tree** collapses the routing into four branches: reuse a
  known-good query/dashboard, investigate a SIEM entity, audit Sumo
  Logic itself, or general app/infra troubleshooting.
- **Training takeaway:** load `log-search-journey` first for any fresh,
  unqualified request; once persona + stage are known, load the 1–3
  skills it names and drop this one — it's a dispatcher, not a
  destination.

---

## 3. Challenges per Phase — and How to Address Them

| Phase | Challenge | Mitigation |
|---|---|---|
| Reuse | Prior art is undiscoverable without knowing it exists | Named technology? Check `skills/discovery-log-domains` first — may already be fully bundled for this instance. Otherwise mine dashboards (`skills/discovery-dashboard-reuse`: find a relevant dashboard, extract panel query text); admins/power users can additionally search `sumologic_search_usage_per_query` for prior queries against the same data |
| Confirm scope | **No discovery endpoint in the Search Job API** — UI autocomplete has no API equivalent | `skills/discovery-without-metadata`; `sumologic_volume` index; `list_partitions()`/`list_extraction_rules()` |
| Sample/discover format | Guessing format wastes scan budget | `skills/discovery-profile-scope` — small-sample queries scoped to the known dimension first |
| Map fields | LLMs default to `json auto`-style parsing, pulling every field the account has ever defined | Extract fields by name; prefer index-time (FER) fields over search-time parsing |
| Craft search | LLMs are often poor at valid/efficient Sumo syntax | `skills/common-query-patterns`, `skills/operator-ordering` |
| Iterate | Security pivots need cost-aware time scoping across tiers (signal time ≠ record time); observability needs cheap correlation across many series | `skills/search-siem-investigation`; `skills/ai-agent-result-shaping` |

---

## 4. Persisting Discovery — Log-Domain Skills

Discovery (confirm scope → sample format → find example queries) is real
work. Without persistence, every session that touches "AWS CloudTrail"
or "Kubernetes" pays that cost again from zero — this pair of skills
turns a one-off discovery into a standing, reusable asset.

- **`skills/log-domain-skill-authoring`** (write side) — chains
  `discovery-without-metadata` → `discovery-profile-scope` →
  `discovery-dashboard-reuse` (or, for admins, the search-usage audit
  index) into one workflow, then writes a single file per technology per
  Sumo Logic instance: confirmed `_sourceCategory`/`_index`, the log
  format and field-extraction approach real queries actually use, and a
  shape-diverse sample of known-good example searches.
- **`skills/discovery-log-domains`** (read side) — the cheap check that
  runs *before* paying for fresh discovery. Reads a per-instance
  `INDEX.md` first; a `confirmed` row is usable directly, `needs-sample`
  is worth one confirming query before relying on it, `stale` hands back
  to authoring for a refresh.
- **Storage is deliberately outside this repo:**
  `~/sumo-search/output/<instance>/skills/<domain-slug>/SKILL.md`. These
  files contain real, org-specific values — committing them here would
  break the "works when copied out with zero other context" guarantee
  the rest of this repo's skills hold to. A generated file can still be
  handed directly to a teammate on the same instance; it just doesn't
  travel with the repo.
- **`.claude/agents/log-domain-discovery.md`** — a Claude Code subagent
  pre-scoped to run the authoring workflow as a bounded background
  research task, one technology per run.
- **`skills-log-examples/`** (repo root) — four real, worked examples
  (AWS CloudTrail, Kubernetes API Server, Azure Audit, Nginx Ingress)
  generated against Sumo Logic's own public demo/training org, each
  clearly marked `EXAMPLE ONLY`. Never read by `discovery-log-domains` —
  a safe, no-credentials way to show the target shape live in training.
- **Training takeaway:** this directly attacks the Reuse phase's biggest
  weakness from slide 3 — prior art being undiscoverable — and turns it
  into compounding org-level leverage: discover a technology once, and
  every later request for it in this instance is near-instant.

---

## 5. Persona — Observability User

**Goal:** drill from a symptom (an alert, an anomalous graph) down through
correlated signals to a root cause.

- Iterates fast across many related time-series/aggregate queries rather
  than one big raw pull — cost and latency compound if each step isn't
  scoped and aggregated.
- Natural fit for `skills/common-query-patterns`: categorical aggregate,
  time series, multi-series (transpose), time-compare (day-over-day).
- When an agent is doing the correlating: `skills/ai-agent-result-shaping`
  — pre-aggregate, cap rows, trim fields — keeps each hop in the drill-down
  cheap enough to chain many of them in one investigation.
- Best practice: default to `requires_raw_messages=False` and
  `count by`/`sum()`/`avg()` — raw messages are rarely needed until root
  cause narrows to a specific event.
- For a platform or service already discovered before (a shared internal
  microservice, a common cloud log source), check
  `skills/discovery-log-domains` (slide 4) before re-deriving scope from
  scratch.

## 6. Persona — SOC Analyst / Security Engineer

**Goal:** pivot on an entity (user, host, IP), correlate across log
sources, reconstruct a timeline — often under time pressure.

- Works across Cloud SIEM's three tiers: **records** (`sec_record_*`,
  normalized per-event data), **signals** (`sec_signal`, rule hits),
  **insights** (correlated signal clusters, in the audit indexes) —
  `skills/search-siem-investigation`.
- **Critical gotcha:** a signal's time and its contributing records' times
  differ — sometimes by 60+ minutes. Scoping a record pull to ±1 minute
  around the signal's `_messagetime` silently misses most of the evidence.
  Widen incrementally (±5 → ±15 → ±30 min), never jump straight to hours.
- Recommended flow: cheap aggregate `sec_signal` query to find candidates
  → pull the signal row tightly scoped → pull contributing records with an
  incrementally widening window → `estimate_scan()` before anything wider
  than 1 hour.
- Config-plane actions (Insights, Detection Rules, Alerts) are **out of
  scope** for this repo/API — that's Sumo's own MCP Investigator skill and
  the separate `/api/sec/v1` config API.

## 7. Persona — Admin (Internal Auditing / Partitions)

**Goal:** audit platform activity and search cost/compliance, not
customer log data.

- System/audit indexes are purpose-built for this and behave differently
  from user log partitions — `skills/search-indexes-partitions`:
  - `sumologic_audit_events` — structured JSON: logins, content changes,
    user/role management, FER changes.
  - `sumologic_volume` — ingest volume by category/collector/partition;
    **parse-first required** (data is a nested JSON array, not top-level
    fields) — also the fastest way to confirm what metadata actually has
    ingested data, doubling as a discovery tool.
  - `sumologic_search_usage_per_query` — per-query scan cost/duration/user;
    a **scheduled view, not an index** — freetext keywords silently return
    empty; must use `field=value`/`| where`.
- Use cases: "who ran the expensive searches," ingest cost by source
  category, compliance history of search activity, auth/admin change
  audit trail.
- Infrequent-tier partitions are silently excluded from default search
  scope — a missing `_index=` looks like "no results," not an error.

---

## 8. Key Considerations for Agentic API Integration

The search UI absorbs most of these sharp edges (autocomplete, paging,
clickable histograms). An agent calling the Search Job API or an MCP tool
owns them directly:

- **No discovery endpoint** — metadata can't be autocompleted. Named
  technology? `skills/discovery-log-domains` (slide 4) is the fastest
  path if it's been discovered before; otherwise work around it with
  `sumologic_volume`, the search-usage view, or
  `list_partitions()`/`list_extraction_rules()`.
- **Scoping discipline** — unscoped/"fishing trip" queries are slow and,
  on Flex/Infrequent plans, directly billed.
- **LLMs write bad Sumo syntax** — both invalid and valid-but-slow queries
  are common; lean on documented patterns rather than free-form generation.
- **Rate limits & the `pendingErrors` trap** — 4 req/sec per key (not per
  script), 10 concurrent requests, 200 concurrent jobs org-wide; a broken
  query can report **zero results with no error surfaced** unless
  `pendingErrors` is checked on every poll.
- **Token/output cost** — aggregate vs. raw query shape is the single
  biggest lever (13–39x difference in output size); CSV beats JSON for
  aggregate output (~2.3x smaller).
- **Unbounded result sets** — always cap: scope-line `| limit N` for raw,
  `| sort | limit` or `| topk` for aggregates, `nodrop` on parse so rows
  don't silently vanish.

---

## 9. Search Best Practices — Scoping & Optimization (Applies to Any Search: UI or API)

These are query-authoring fundamentals — true whether a human types the
query in the UI or an agent generates it via API/MCP.

**Four levers, in priority order** (`skills/query-scoping-efficiency`):

1. **Partition & source category scope** — `_index=`/`_sourceCategory=`
   first, always. Never `_datatier=all` or a bare `*`.
2. **Bloom-filter keyword expressions** — literal/wildcard tokens on the
   scope line reject non-matching events before parsing; case-insensitive,
   unlike `| where ... matches` (case-sensitive, no bloom benefit).
3. **Index-time (FER) fields over search-time parsing** — a field
   extracted at ingest and used in scope gets bloom-filter speed; the
   same field pulled via `| json`/`| parse` requires scanning first.
4. **Short time ranges** — validate on 15 minutes before widening.

**Canonical operator order** (`skills/operator-ordering`): scope → early
`where` → parse → late `where` → aggregate → post-aggregate → format
(`fields`/`limit` always last). Position changes *behavior*, not just
style — e.g. `| limit N` on the scope line stops the scan; the same
`limit` at the end of the pipeline scans everything first and only trims
output.

**Common mistakes that silently break scoping:**

- `| where _raw matches "..."` instead of a scope-line keyword — throws
  away the bloom filter entirely.
- Missing parentheses around `OR` — `a foo OR bar` silently un-scopes the
  `bar` branch.
- Filtering `_messageTime`/`_receiptTime` inside the query instead of
  setting the job's actual time window.
- Assuming push-down optimization will rescue an unscoped `| where` — it
  never fires for `matches`, and even for `=` it's not guaranteed.

---

## 10. Search Job API Reference — Best Practices & Gotchas

From `skills/search-job-api-best-practices` — the practices
`sumo_search_client.py` implements end to end:

- **Always async, 4 steps:** create → poll → fetch → delete. Delete on
  every exit path (including errors/zero-results) — wrap in `finally`.
- **Rate limits are per access key, not per script** — 4 req/sec, 10
  concurrent requests, 200 concurrent jobs org-wide, all shared across
  every process using that key.
- **Retry only on 429**, exponential backoff + jitter, honor a numeric
  `Retry-After`. Never blindly retry a `create`/`fetch` on 5xx (risks
  double-running an expensive search) — polling gets its own, more
  lenient backoff that does tolerate transient 5xx.
- **State machine gotcha:** check `pendingErrors` on *every* poll, not
  just at `DONE` — a broken query can report `0` results with no visible
  error. **Zero results is not proof of a valid empty search.**
- **`FORCE PAUSED`** = hit the ~100k raw-message pause point — terminal,
  non-retriable; narrow scope and run as a *new* job.
- **Pagination:** advance offset by the actual returned batch size, never
  a fixed page size (a page can come back short under the 100MB/10k-row
  cap even when more data remains).
- **Hard limits:** 100k raw messages/job (silently truncated, no error);
  10k rows or 100MB per page; job expiry ~10 min observed in practice.
- **Pre-flight `estimate_scan()`** — separate synchronous endpoint, no job
  created, validates query syntax/partition names for free (HTTP 400 on
  either failure) before spending real scan budget.
- **Match automation frequency to time range — watch the scan ratio.**
  Vibe-coded automations make it trivial to wrap a search in a cron loop,
  and it's easy to pick the window and the interval independently without
  noticing the multiplier: a search over the **last 3 hours, run every
  minute**, rescans the same 3 hours ~180 times over — a **scan ratio of
  180x**. On Flex or Infrequent tier that's 180x the credits for the same
  answer; on any tier it's 180x the unnecessary account load. Keep the
  **scan ratio at ~1x** by matching window to interval — last 1 hour run
  hourly, last 15 minutes run every 15 minutes. For a recurring query that
  genuinely needs to run more often than its window (e.g. a rolling
  dashboard), don't re-scan raw logs at all — put a **scheduled view**
  behind it. A scheduled view pre-aggregates continuously in the
  background and stores the result under its own `_view=` partition, so
  each API call reads already-computed rows instead of re-scanning raw
  data — see `skills/scheduled-views-overview`.

---

## 11. This Repo — Abstract Summary & How It Addresses Slide 10

**Three paths to the same Search Job API**, same underlying gotchas:

| | `sumo_search_client.py` | `sumosearch` CLI | Sumo MCP |
|---|---|---|---|
| Transport | Python library | Shell/agent subprocess | In-chat tool call |
| Best for | Building a service on the API | Ad hoc agent/terminal search | Agent inside a harness with native MCP |

- **`sumo_search_client.py`** — single-file, single-dependency reference
  client. Bakes in every practice from Slide 10 by default: client-side
  throttling + backoff, `pendingErrors` checked every poll, automatic
  `records`/`messages` detection, time-splitting for large exports,
  `estimate_scan()` wrapper.
- **`sumosearch` CLI** — `kubectl`-style wrapper adding token-efficient
  output shaping the raw API doesn't provide: CSV/ndjson defaults,
  client-side field trimming that actually works on raw messages (in-query
  `| fields` doesn't, on large field catalogs), a first-class `schema`
  command, `search estimate`, and automatic export time-splitting.
- **`skills/`** — portable, harness-agnostic Agent Skills covering
  discovery, dashboard/query reuse, log-domain skill persistence
  (slide 4), scoping, partitions/indexes, operator ordering, common
  patterns, agent result-shaping, scheduled views, and SIEM investigation.
  Identical content applies whether the caller is this client, the CLI, or
  Sumo's own `runLogSearch`/Investigator MCP tools — only the transport
  differs, so an org can standardize on one set of query-authoring rules
  regardless of which of the three paths a given agent uses.

**A fourth surface, one specific use case:** `sumo_dashboard_client.py` /
`sumosearch report` talk to a different API entirely (the Dashboard
Report Job API, not the Search Job API above). Two things it enables
that a hand-written search can't: rendering a dashboard as PDF/PNG for
fast, human-curated visual context on a symptom (many panels' worth of
already-validated searches plus author-written interpretation, in one
image); and describing a dashboard's structure/panel query text
programmatically with no job at all — the mechanism behind
`skills/discovery-dashboard-reuse`'s query mining (slide 1/3). Left out
of the three-path table above since it isn't the Search Job API, but
worth knowing it exists — see README's "Dashboard reports: export and
discovery."

---

## 12. Suggested Additional Slides / Topics to Research

- **Live demo**: `sumosearch discover` → `schema` → `sample` → `search
  estimate` → `search run`, showing the journey phases mapped onto real
  commands. Extend with the persistence payoff from slide 4:
  `log-domain-skill-authoring` writing a fresh per-instance skill file,
  then `discovery-log-domains` reading it back instantly on a second,
  later ask. No live instance with real content handy? `skills-log-examples/`
  is a safe, no-credentials fallback to show the finished shape.
- **Visual investigation aid**: `sumosearch report run <dashboard-id>
  --format png` for reading a dashboard's rendered view instead of
  hand-writing every underlying panel search — a useful contrast with
  the API-only "Iterate" persona work in slides 5–7.
- **Billing model primer**: Flex vs. Tiered (Continuous/Infrequent) — scope
  only costs money on Flex and Infrequent; useful context before Slide 9's
  "why scope matters" lands with a cost-conscious audience.
- **Reference table** (appendix): README's full stage-by-stage mapping of
  `sumo_search_client.py` / `sumosearch` / Sumo MCP capabilities — good
  as a leave-behind slide rather than presented live.
- **Governance angle for the Admin persona**: a short walkthrough of using
  `sumologic_search_usage_per_query` to find and coach users running
  expensive/unscoped searches — ties Slide 7 back to Slide 9's cost levers
  concretely.
- **What's deliberately out of scope**: Cloud SIEM config API (Insights,
  Detection Rules, match lists) and Sumo's Investigator MCP skill — worth
  a slide so the audience doesn't expect this repo to cover SOC
  case-management, only log/record search.
- **Discussion prompt slide**: where does the audience's org sit today on
  the reuse-vs-cold-start spectrum (Journey Phase 1)? Would persisting
  discovery per technology (slide 4) shift that quickly for their most
  common log sources? Good segue into Q&A.
