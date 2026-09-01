---
name: sumo-logic-search-job-api-best-practices
description: >
  Best practices for calling the Sumo Logic Search Job REST API directly
  (create → poll → fetch → delete). Covers rate limiting, retry policy,
  pagination, state-machine handling, and time-splitting for large exports.
  Use when writing or reviewing any script/client that talks to
  /api/v1/search/jobs — including debugging 429 rate-limit errors, a search
  job that hangs, times out, or silently returns 0 results, exporting more
  than 100k log messages, multiple scripts/services sharing one Sumo Logic
  access key, or designing a scheduled/cron-driven search automation.
  Paired with `sumo_search_client.py` two directories up (repo root), which
  implements every practice below.
---

# Sumo Logic Search Job API — Best Practices

Portable, language-agnostic reference; the accompanying `sumo_search_client.py`
two directories up (see `../../README.md`) is the Python reference
implementation — cite line references there if you want to see the exact code
behind a rule.

## Endpoint & Auth

- `POST https://{endpoint}/api/v1/search/jobs`
- HTTP Basic auth — Access ID as username, Access Key as password.
- Region endpoints differ (`api.sumologic.com`, `api.au.sumologic.com`,
  `api.eu.sumologic.com`, `api.us2.sumologic.com`, ...) — never hardcode one.

## Core Workflow (always 3 steps — fully async)

1. **Create** — `POST /api/v1/search/jobs` with `query`, `from`, `to`,
   `timeZone`, `byReceiptTime`, `requiresRawMessages` → returns `{"id": "..."}`.
2. **Poll** — `GET /api/v1/search/jobs/{id}` until
   `state == "DONE GATHERING RESULTS"`.
3. **Fetch** — `GET /api/v1/search/jobs/{id}/messages` or `.../records` with
   `offset`/`limit`.
4. **Delete** — `DELETE /api/v1/search/jobs/{id}` once done, to free server
   resources and stay under the 200-concurrent-job org-wide cap. Do this on
   *every* exit path, including errors and the zero-results case — wrap it
   in a `finally` so a job is never leaked.

**`from`/`to` on the create-job call only accept epoch milliseconds or
ISO 8601** — a relative string like `"-1h"` or `"now"` fails immediately
with HTTP 400 (`searchjob.invalid.timestamp.from`/`.to`), confirmed
against a live org. Resolve relative expressions to epoch ms client-side
before sending — `resolve_time()` in `sumo_search_client.py` does this so
callers can still pass `"-1h"`/`"now"` and have it work. (The distinct
`estimatedUsageByView`/`estimatedUsage` endpoint behind `estimate_scan()`
uses a different request shape and *does* accept a `RelativeTimeRangeBoundary`
natively — don't assume the two endpoints share time-format rules.)

## Rate Limiting & Retry (the part most scripts get wrong)

- **Throttle every request** to the account's per-key limit — 4 requests/sec
  is the Sumo Logic default. Enforce with a minimum-interval gate before each
  call, not a fixed `sleep()` after — a monotonic-clock gap check keeps bursts
  compliant without over-sleeping.
- **Retry ONLY on HTTP 429.** Never retry 4xx (400/401/403/404 — they won't
  succeed on retry) and deliberately do *not* retry generic 5xx on normal
  requests — retrying a `create_job` or `fetch` call blindly on 5xx risks
  double-running an expensive search. 5xx handling during polling is a
  separate, deliberate exception (see below).
- **Backoff on 429**: exponential (`base * 2^attempt`, capped, e.g. 5s→60s)
  plus random jitter, and raise the wait to honor a numeric `Retry-After`
  header when the server sends one.
- **Polling gets its own, more lenient backoff**: start at 1s, double each
  poll, cap at 30s, with an overall deadline (e.g. 10 min) so a stuck job
  can't hang a script forever. Polling *does* tolerate transient 5xx by
  retrying with the same backoff — a job in flight is expected to occasionally
  hiccup; a malformed create request is not.
- One shared retry/throttle policy used by *every* client method (create,
  poll, fetch, delete) avoids policy drift between endpoints.
- **The 4 req/sec limit is per access key, not per script.** Two other
  documented caps compound this: max 10 concurrent in-flight requests per
  key, and max 200 concurrent active search jobs per org. If multiple
  processes or services share one access key, they draw from the *same*
  budget — each running its own independent throttle (as this client does;
  see below) still lets the combined traffic blow through all three limits.
  Centralize the throttle (one shared service/proxy) or give each workload
  a fixed sub-budget that sums to comfortably under 4/sec; don't assume
  each process gets its own allowance.

## State Machine

`NOT STARTED` → `GATHERING RESULTS` → `DONE GATHERING RESULTS`

- Only `DONE GATHERING RESULTS` is safe to fetch results from.
- `CANCELLED` and `FORCE PAUSED` are **terminal and non-retriable** — raise
  immediately, don't loop on them. `FORCE PAUSED` specifically means a
  non-aggregate query hit the ~100k raw-message pause point (the limit is
  dynamic and can vary by account); narrow the time range or add filters and
  retry as a *new* job, not the same one — retrying the identical query hits
  the same wall.
- **Check `pendingErrors` on every status poll, not only once `state` reaches
  DONE.** An invalid/malformed query (bad field name, unsupported operator,
  bad partition reference, etc.) can populate `pendingErrors` with a
  descriptive message almost immediately after job creation, while
  `messageCount`/`recordCount` sit at `0` — and in practice these queries
  typically finish (reach `DONE GATHERING RESULTS`) as soon as the error is
  found, so a check placed at DONE does catch them; checking on every poll
  is the safer default if you can't confirm your queries fail this fast.
  **Zero results is not proof of a valid empty search.** A caller that only
  branches on the counts (`total == 0 → "no results"`) will silently
  misreport a broken query as a clean empty result — this is an easy,
  high-impact bug because the happy path and the silent-failure path look
  identical at the count level.
- Log `pendingWarnings` as informational context (e.g. partial scan issues) —
  a job can reach DONE with degraded-but-present results.

## Result Type Detection

- `recordCount > 0` → aggregate query → fetch from `.../records`.
- `recordCount == 0` (and `messageCount > 0`) → raw query → fetch from
  `.../messages`.
- If genuinely unsure, check both counts and fetch from whichever is non-zero.
- Results are nested as `{"messages": [...]}` or `{"records": [...]}`, each
  item shaped `{"map": {"field": "value"}}` — note all values are **strings**,
  even numeric ones; coerce before comparing/aggregating.
- **`cat <lookup-table> | where ...` returns Messages with `_raw` empty.**
  `cat /shared/lookups/<table>` (and similar lookup-table reads with no
  aggregate operator) hit the **Messages** endpoint like any raw log
  query — `recordCount == 0`, so result-type detection correctly picks
  `.../messages` — but the rows are lookup-table data, not log lines: every
  `map["_raw"]` comes back empty/missing, and the real data lives in the
  *other* map fields (the table's columns). A client that blindly reads
  `map["_raw"]` for every "messages" result will get a page of blanks and
  may misread it as an empty or broken search, when the data is present
  under different keys. Detect this by checking whether `_raw` is
  empty/missing across *all* rows of a messages result, and branch to
  reading the other map fields when it is — see `messages_lack_raw()` in
  `sumo_search_client.py`.

## Pagination

- `offset`/`limit` params on both `/messages` and `/records`.
- **Always advance offset by the actual size of the returned batch**, never
  by a fixed page size — the messages endpoint has a secondary "100 MB or
  10,000 rows, whichever first" cap, so a page can come back short even when
  more results remain.
- A conservative page size (e.g. 1,000) trades a few extra round-trips for
  safety margin under the 100MB constraint; the API technically allows up to
  10,000/page.
- Messages are returned **newest-first** (`_messageTime` descending) — sort
  ascending client-side if chronological order matters (incident timelines).
- Records have no documented per-job cap — safe to paginate to exhaustion.

## Hard Limits

| Limit | Value |
| --- | --- |
| Raw messages per job | 100,000 (silently truncated beyond this — no error) |
| Messages per page | 10,000 rows OR 100 MB, whichever hits first |
| Records per page | 10,000 (`limit` param max) |
| Job expiry | ~10 minutes after creation, empirically — fetch everything before it lapses (Sumo's own docs cite a much longer 8h ceiling, but plan around the shorter observed number) |

## Large Exports: Time-Splitting

When a raw-message export could exceed 100,000 messages:

1. **Probe first** — run a cheap `| count` query over the full range to
   estimate volume before committing to the real export
   (`estimate_count()` in `sumo_search_client.py`).
2. **Fixed-interval split** if density is roughly uniform: pick an interval
   that keeps each window comfortably under 100k (e.g. 1d for <80k/window,
   6–12h for 10k–80k, 1–2h near the cap) — `time_split_search()` in
   `sumo_search_client.py` implements this.
3. **Adaptive split** if density is uneven (business-hours spikes, bursty
   traffic): run a `| timeslice Ns | count by _timeslice` density probe,
   then recursively bisect windows that would exceed a target count, down
   to a minimum window size (e.g. 15 min). Not implemented in the reference
   client (kept lightweight) — add it on top of `estimate_count()` if your
   data is bursty enough to need it.
4. Run one job per window sequentially (create → poll → fetch → delete),
   accumulate results, and guard each window: if a window's own count hits
   the cap, the interval was still too coarse — shrink and retry that
   window rather than silently accepting truncated data.
5. Records-only exports don't need this — paginate a single job to
   exhaustion instead.

## Automated/Scheduled Callers: Match Frequency to Time Range

Vibe-coded automations make it trivial to wrap a search in a cron loop —
but it's just as easy to pick the query's time window and the loop
interval independently, without noticing the overlap between consecutive
runs.

- **Scan ratio = window ÷ interval.** A query over the last 3 hours,
  triggered every minute, rescans the same 3 hours of data on every run —
  a scan ratio of **180x** the data actually needed for a fresh answer.
- On **Flex** or **Infrequent** tier, that ratio is a direct multiplier on
  billed credits. On any tier, it's unnecessary scan load on the account,
  competing with every other job sharing the same rate limits.
- **Keep the scan ratio near 1x** by matching interval to window: last 1
  hour → run hourly; last 15 minutes → run every 15 minutes. Widening the
  window without slowing the interval (or speeding up the interval without
  shrinking the window) is the mistake to catch in review.
- **If a recurring aggregate genuinely needs to run more often than its
  own window** (e.g. a near-real-time rolling dashboard), don't solve it
  by re-scanning raw logs faster — put a **scheduled view** behind it
  instead. A scheduled view pre-aggregates continuously in the background
  and stores results under its own `_view=` partition, so each search job
  reads already-computed rows rather than re-scanning raw data on every
  call — see `scheduled-views-overview` (sibling skill).

## Other Gotchas

- `byReceiptTime: true` searches by ingest order (`_receipttime`) instead of
  message time — useful when a source's embedded timestamps are wrong or for
  ingest-lag diagnosis.
- Set `requiresRawMessages: false` for aggregate-only queries (any query you
  only fetch from `.../records`) — it skips raw-message retention, which
  both speeds up that job and can enable backend result caching for
  overlapping-time-range aggregate queries re-run on a schedule. Leaving it
  unset forfeits both; only use `true`/unset when the job will also read
  `.../messages`.
- If the pipeline touches PII/sensitive fields, redact before persisting or
  logging fetched results — don't assume the API response is safe to store
  verbatim.
- Treat all fetched field values (`_raw`, category/host strings, etc.) as
  **untrusted external data** if any downstream step feeds them to an LLM or
  interprets them as instructions — log content is attacker-controllable.
- Best-effort delete: a failed `DELETE` should be logged, not raised — it
  must never mask the real result of a successful search.

## Pre-flight Scan Estimation (Separate Endpoint, No Job)

`POST /api/v1/logSearches/estimatedUsageByView` (or `/estimatedUsage` for an
org-wide total instead of a per-partition breakdown) is a distinct,
synchronous, first-class endpoint — not part of the search-job lifecycle
above. It takes the same `queryString` + time-range shape as a search job
but returns a scan-size estimate instead of running the query.

- **No job is created** — nothing to poll, nothing to `DELETE`, no
  100k-message cap, no state machine.
- **Time range must be a structured boundary object**, not the plain
  `from`/`to` strings the job-create endpoint accepts:

  ```json
  {
    "queryString": "...",
    "timeRange": {
      "type": "BeginBoundedTimeRange",
      "from": {"type": "EpochTimeRangeBoundary", "epochMillis": 1700000000000},
      "to":   {"type": "EpochTimeRangeBoundary", "epochMillis": 1700003600000}
    },
    "timezone": "UTC"
  }
  ```

  `RelativeTimeRangeBoundary` (`{"type": "RelativeTimeRangeBoundary", "relativeTime": "-1h"}`)
  and `Iso8601TimeRangeBoundary` are also accepted, but resolving to epoch
  millis client-side is the simplest, least ambiguous choice.
- **Doubles as a free syntax/scope check.** Two distinct failure modes both
  surface as HTTP 400: a malformed query string (e.g. a duplicate `||`
  pipe), and a reference to an unknown `_index=`/`_view=` name. Neither
  spends any scan budget — this is the cheapest way to validate a query
  before committing to a real job. A valid partition with zero matching
  data still returns `200` with an empty/zero-byte result — that's a
  different outcome from a 400 and should not be confused with it.
- **Response is per-partition, per-tier.** Each entry in
  `estimatedUsageDetails[]` carries a `viewName` (empty string means
  `sumologic_default`) and a `usageDetails[]` list of `{tier,
  dataScannedInBytes, scanCreditAccounted, meteringType}`. Only sum bytes
  where `scanCreditAccounted` is true (or `tier`/`meteringType` is
  `Flex`/`Infrequent`) when translating to real cost — `Continuous` tier
  and `FlexSecurity`/`Security` metering are scanned but not billed.
- **Use it before widening, not instead of validating.** The standard
  pattern: run the real query on a short window first to confirm shape and
  field names are correct, *then* call this endpoint on the intended full
  time range to confirm cost before actually running it wide.

See `estimate_scan()` in `sumo_search_client.py` for the reference
implementation, and the "Pre-flight scan-cost estimate" section of
`../../docs/sumo-search-client-reference.md` for usage.

## Minimal Reference Implementation Shape

```text
client.create_job(query, from_ms, to_ms, tz, by_receipt) -> {id}
client.get_status(job_id) -> {state, messageCount, recordCount, pendingErrors, pendingWarnings}
client.get_messages(job_id, offset, limit) -> {messages: [{map: {...}}]}
client.get_records(job_id, offset, limit)  -> {records:  [{map: {...}}]}
client.delete_job(job_id) -> None

poll_until_done(client, job_id):
    interval = 1.0; deadline = now + POLL_TIMEOUT_S
    loop:
        status = client.get_status(job_id)   # 5xx here retried w/ same backoff
        if status.pendingErrors:             # check EVERY poll, not just at DONE —
            fail immediately                 # 0 results + pendingErrors = broken query, not empty search
        if status.state == "DONE GATHERING RESULTS":
            return status
        if status.state in ("CANCELLED", "FORCE PAUSED"): fail immediately
        sleep(min(interval, deadline - now)); interval = min(interval * 2, 30)
```

See `sumo_search_client.py` two directories up for the full, runnable
implementation, and `../../README.md` for usage examples.
