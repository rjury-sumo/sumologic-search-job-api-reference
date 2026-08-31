# `sumo_search_client.py` — reference

Lower-level usage of the Python client that doesn't belong in the
top-level [README.md](../README.md) quickstart: manual job control,
discovery endpoints, pre-flight cost estimates, constructor configuration,
logging, and what the client deliberately leaves out. See the top-level
README for installation, the basic `run_search()` example, and how this
client compares to the `sumosearch` CLI and Sumo's MCP tool.

## Pre-flight scan-cost estimate

Before running an expensive or unfamiliar query, check what it will scan
without creating a search job at all:

```python
import time

to_ms = int(time.time() * 1000)
from_ms = to_ms - 3_600_000   # last 1h; estimate_scan() needs epoch ms, not "-1h"

estimate = client.estimate_scan(
    query='_index=prod_logs _sourceCategory=prod/app error',
    from_ms=from_ms, to_ms=to_ms,
)
print(f"{estimate.total_bytes / 1e9:.2f} GB across {len(estimate.partitions)} partition(s)")
```

## Discovery endpoints (partitions, field extraction rules, scheduled views)

Three synchronous, no-job-created endpoints — useful before writing a query
at all, when the source category or partition isn't known yet (see
`skills/discovery-without-metadata/SKILL.md`):

```python
partitions = client.list_partitions()
rules = client.list_extraction_rules()
views = client.list_scheduled_views()
```

All three page through the API's `token`/`next` cursor internally and
return the full list.

## Exporting more than 100,000 raw messages

A single job silently truncates raw-message results at 100,000. For larger
exports, probe the volume first and split the time range into windows sized
to stay under the cap:

```python
from sumo_search_client import estimate_count, time_split_search

query = '_sourceCategory=prod/app error'
from_ms, to_ms = 1_700_000_000_000, 1_700_604_800_000   # ~7 days

total = estimate_count(client, query, from_ms, to_ms)
print(f"~{total} events in range")

all_messages = time_split_search(
    client, query, from_ms, to_ms, interval_hours=6,
)
print(f"fetched {len(all_messages)} messages across all windows")
```

See [`skills/search-job-api-best-practices/SKILL.md`](../skills/search-job-api-best-practices/SKILL.md#large-exports-time-splitting)
for the full rationale (probe-then-split, density considerations, when a
fixed interval isn't enough).

## Manual control (create/poll/fetch separately)

`run_search()` covers the common case. If you need to inspect the job
between steps, use the lower-level methods directly:

```python
job = client.create_job(query, from_time="-1h", to_time="now")
job_id = job["id"]

try:
    status = client.poll_until_done(job_id, timeout_s=300)
    result = client.fetch_all(job_id, status, limit=5000)
finally:
    client.delete_job(job_id)   # always clean up, even on failure
```

## Configuration

```python
client = SumoSearchClient(
    access_id, access_key, endpoint,
    min_interval=0.25,      # 4 req/sec — the per-key API limit
    max_retries=3,          # retries after an HTTP 429
    base_backoff=5.0,       # seconds; doubles per retry attempt
    max_backoff=60.0,       # seconds; backoff cap
)
```

`run_search()` and `poll_until_done()` also accept `poll_timeout_s`
(default 600s). Jobs expire roughly 10 minutes after creation, so raising
this default rarely helps.

## Logging

The client uses the standard `logging` module under the logger name
`sumo_search_client`:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## What this client intentionally leaves out

To stay a lightweight, portable reference:

- No result caching, PII redaction, or output formatting — bring your own
  for production use.
- No adaptive (density-based) time-splitting — only fixed-interval; see
  `skills/search-job-api-best-practices/SKILL.md` for the pattern if you
  need it.
- No cross-process rate coordination — the throttle lives in one client
  instance. If multiple scripts or services share the same access key,
  each one throttling independently still lets combined traffic exceed the
  account's actual (shared) limits; see the shared-access-key note under
  "Rate Limiting & Retry" in `skills/search-job-api-best-practices/SKILL.md`.
