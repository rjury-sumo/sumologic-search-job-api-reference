"""
sumo_search_client.py — standalone reference client for the Sumo Logic
Search Job REST API (create -> poll -> fetch -> delete).

Gold-standard example for teams building their own Sumo Logic search
integrations. Single file, one external dependency (`requests`), no ties
to any particular CLI or framework — copy it into your own project and
adapt.

Best practices encoded here (see
./skills/search-job-api-best-practices/SKILL.md for the full rationale
behind each one):

  1. Per-key throttling (default 4 req/sec) before every request.
  2. Retry ONLY on HTTP 429, with exponential backoff + jitter, honoring
     a numeric `Retry-After` header when present. Every other status
     (2xx/4xx/5xx) is returned to the caller as-is — 4xx never succeeds
     on retry, and blind 5xx retry on a create/fetch call can double-run
     an expensive query.
  3. Polling uses its own, more lenient backoff (1s -> 30s cap) with an
     overall deadline, and tolerates transient 5xx *while polling* by
     retrying — a job in flight is expected to hiccup occasionally.
  4. `pendingErrors` is checked on EVERY poll response, not only once
     `state == "DONE GATHERING RESULTS"`. An invalid query can report
     `pendingErrors` almost immediately, with `messageCount`/`recordCount`
     both at 0 — a client that only branches on the counts will silently
     misreport a broken query as a valid empty search.
  5. Result-type auto-detection from `recordCount`/`messageCount` (never
     ask the caller to say whether their query was an aggregate).
  6. Pagination advances the offset by the ACTUAL size of the returned
     batch, never a fixed page size — the messages endpoint has a
     secondary "100 MB or 10,000 rows" cap and can return a short page.
  7. The 100,000-row hard cap on raw messages is surfaced as a warning,
     with a time-splitting helper for exports that would exceed it.
  8. The job is always deleted after use, including on the exception
     path (`try/finally`), to avoid leaking server-side job state.
  9. Lookup-table reads (`cat /shared/lookups/<table> | where ...`) hit the
     Messages endpoint like any raw query but return every row with `_raw`
     empty and the real data under other map keys — flagged via
     `SearchJobResult.looks_like_lookup_table` instead of silently reading
     blank `_raw` values.
 10. `estimate_scan()` is a distinct, synchronous endpoint — no job is
     created, so there's nothing to poll or delete. Use it as a free
     pre-flight check: it validates query syntax/scope (HTTP 400 on a bad
     query, same as a real job would eventually report) and returns the
     scan size that job would incur, before you spend any scan budget
     running it for real.
 11. Relative time expressions ("-1h", "+30m", "now") are resolved to
     epoch ms client-side via `resolve_time()` before every request — the
     API itself rejects relative strings outright (HTTP 400
     `searchjob.invalid.timestamp.from/to`), so this can't be left to the
     caller.

Usage:

    from sumo_search_client import SumoSearchClient

    client = SumoSearchClient(access_id, access_key, endpoint)
    result = client.run_search(
        '_sourceCategory=prod/app error | count by _sourceHost',
        from_time="-1h", to_time="now",
    )
    for row in result.items:
        print(row["map"])

See README.md for more end-to-end examples, including large-export
time-splitting.
"""

from __future__ import annotations

import logging
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger("sumo_search_client")

# ---------------------------------------------------------------------------
# Tunables — Sumo Logic API limits and safe defaults
# ---------------------------------------------------------------------------

DEFAULT_MIN_INTERVAL = 0.25    # 4 requests/second — the per-key API limit
DEFAULT_MAX_RETRIES = 3        # additional attempts after a 429
DEFAULT_BASE_BACKOFF = 5.0     # seconds; doubles per attempt
DEFAULT_MAX_BACKOFF = 60.0     # seconds; backoff cap
DEFAULT_POLL_TIMEOUT_S = 600.0 # 10 minutes; jobs expire ~10 min after creation
DEFAULT_PAGE_SIZE = 1000       # conservative; API allows up to 10,000/page

DONE_STATE = "DONE GATHERING RESULTS"
TERMINAL_FAIL_STATES = {"CANCELLED", "FORCE PAUSED"}
MAX_RAW_MESSAGES = 100_000      # hard API cap; exceeding it truncates silently

_RELATIVE_TIME_RE = re.compile(r"^([+-]?)(\d+(?:\.\d+)?)([smhdw])$")
_RELATIVE_TIME_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def resolve_time(value: int | float | str) -> str:
    """Convert epoch ms, "now", or a relative expression (-1h, +30m, -2d, ...)
    to an epoch-ms string; pass any other string (ISO 8601, or an already
    numeric epoch-ms string) through unchanged.

    The Search Job API itself does NOT accept relative strings — passing
    "-1h" straight through fails with HTTP 400
    (searchjob.invalid.timestamp.from/to). Every caller needs this
    resolved client-side, so create_job()/estimate_scan() both route
    through it rather than leaving it to the caller."""
    if isinstance(value, (int, float)):
        return str(int(value))

    v = value.strip()
    if v.lower() == "now":
        return str(int(datetime.now(timezone.utc).timestamp() * 1000))

    m = _RELATIVE_TIME_RE.match(v.lower())
    if m:
        sign, amount, unit = m.groups()
        amount = float(amount)
        if sign != "+":
            amount = -amount
        delta = timedelta(**{_RELATIVE_TIME_UNITS[unit]: amount})
        return str(int((datetime.now(timezone.utc) + delta).timestamp() * 1000))

    return value


def messages_lack_raw(messages: list[dict[str, Any]]) -> bool:
    """True when every message has an empty/missing `_raw`.

    Real log messages always carry the raw log line in `_raw`. But
    `cat /shared/lookups/<table> | where ...` (and similar lookup-table
    reads with no aggregate operator) also hit the Messages endpoint —
    `recordCount == 0`, so result-type detection correctly picks
    `.../messages` — yet the rows are lookup-table data with `_raw` left
    blank and the real values under the table's own column names. A caller
    that blindly reads `map["_raw"]` on every "messages" result gets a page
    of blanks and can mistake it for an empty/broken search."""
    return bool(messages) and all(not m.get("map", {}).get("_raw") for m in messages)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SumoSearchError(Exception):
    """Base error for this client. Non-429 HTTP errors surface as this."""

    def __init__(self, message: str, *, status_code: int | None = None,
                job_id: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.job_id = job_id


class SumoSearchJobFailed(SumoSearchError):
    """Job reported pendingErrors, or ended in CANCELLED / FORCE PAUSED."""


class SumoSearchTimeout(SumoSearchError):
    """Polling exceeded the configured deadline without reaching DONE."""


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class SearchJobResult:
    job_id: str
    result_type: str            # "messages" | "records"
    total: int                  # total rows the job reports (pre-truncation to `limit`)
    items: list[dict[str, Any]] = field(default_factory=list)
    pending_warnings: list[Any] = field(default_factory=list)
    truncated: bool = False     # True if `total` hit the 100k raw-message cap
    looks_like_lookup_table: bool = False  # True: "messages" result with every _raw empty


@dataclass
class ScanEstimate:
    """Result of estimate_scan() — a scan-size projection, not a job result."""
    total_bytes: int
    partitions: list[dict[str, Any]] = field(default_factory=list)
    # each entry: {viewName, totalDataScannedInBytes, usageDetails: [
    #   {tier, dataScannedInBytes, scanCreditAccounted, meteringType}, ...
    # ]}


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SumoSearchClient:
    """Minimal client for the Sumo Logic Search Job API.

    One throttled `requests.Session` per instance. Not thread-safe by
    design — the throttle assumes serial use, matching how the Search Job
    API's per-key rate limit is enforced server-side.
    """

    def __init__(self, access_id: str, access_key: str, endpoint: str, *,
                min_interval: float = DEFAULT_MIN_INTERVAL,
                max_retries: int = DEFAULT_MAX_RETRIES,
                base_backoff: float = DEFAULT_BASE_BACKOFF,
                max_backoff: float = DEFAULT_MAX_BACKOFF,
                session: requests.Session | None = None):
        self.base = endpoint.rstrip("/") + "/api/v1"
        self.session = session or requests.Session()
        self.session.auth = HTTPBasicAuth(access_id, access_key)
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.base_backoff = base_backoff
        self.max_backoff = max_backoff
        self._last_request = 0.0

    # -- rate limiting -----------------------------------------------------

    def _throttle(self) -> None:
        gap = self.min_interval - (time.monotonic() - self._last_request)
        if gap > 0:
            time.sleep(gap)
        self._last_request = time.monotonic()

    @staticmethod
    def _retry_after_seconds(resp: requests.Response) -> float | None:
        val = resp.headers.get("Retry-After")
        if not val:
            return None
        try:
            return max(0.0, float(val))
        except (TypeError, ValueError):
            return None

    def _backoff_seconds(self, attempt: int, retry_after: float | None) -> float:
        wait = min(self.base_backoff * (2 ** attempt), self.max_backoff)
        if retry_after is not None:
            wait = max(wait, retry_after)
        return wait + random.uniform(0, self.base_backoff)

    def _request(self, method: str, path: str, *, params: dict | None = None,
                json_body: dict | None = None, operation: str = "") -> dict:
        """Throttled request with 429-only retry. Every other status
        (2xx/4xx/5xx) is checked and returned/raised immediately — retrying
        a 5xx blindly risks double-running an expensive search."""
        url = f"{self.base}{path}"
        resp: requests.Response | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            resp = self.session.request(method, url, params=params, json=json_body)
            if resp.status_code == 429 and attempt < self.max_retries:
                wait = self._backoff_seconds(attempt, self._retry_after_seconds(resp))
                logger.warning("rate-limited on %s (attempt %d/%d) — waiting %.1fs",
                              operation or path, attempt + 1, self.max_retries, wait)
                time.sleep(wait)
                continue
            break
        return self._check(resp, operation)

    @staticmethod
    def _check(resp: requests.Response, operation: str) -> dict:
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            try:
                detail = resp.json()
            except ValueError:
                detail = resp.text
            raise SumoSearchError(
                f"{operation or 'request'} failed: HTTP {resp.status_code} — {detail}",
                status_code=resp.status_code,
            ) from exc
        return resp.json() if resp.text.strip() else {}

    @staticmethod
    def _format_time(value: int | float | str) -> str:
        """Epoch ms (int/float), "now", and relative expressions ("-1h",
        "+30m", ...) are resolved to an epoch-ms string client-side — the
        API itself rejects relative strings. ISO 8601 strings and
        already-numeric epoch-ms strings pass through unchanged."""
        return resolve_time(value)

    # -- job lifecycle -------------------------------------------------

    def create_job(self, query: str, from_time: int | str, to_time: int | str, *,
                   time_zone: str = "UTC", by_receipt_time: bool = False,
                   by_searchable_time: bool = False,
                   requires_raw_messages: bool | None = None,
                   auto_parsing_mode: str | None = None) -> dict:
        body = {
            "query": query,
            "from": self._format_time(from_time),
            "to": self._format_time(to_time),
            "timeZone": time_zone,
            "byReceiptTime": by_receipt_time,
            "bySearchableTime": by_searchable_time,
        }
        if requires_raw_messages is not None:
            body["requiresRawMessages"] = requires_raw_messages
        if auto_parsing_mode is not None:
            body["autoParsingMode"] = auto_parsing_mode
        return self._request("post", "/search/jobs", json_body=body,
                             operation="create search job")

    def get_status(self, job_id: str) -> dict:
        return self._request("get", f"/search/jobs/{job_id}",
                             operation=f"get status ({job_id})")

    def get_messages(self, job_id: str, offset: int, limit: int) -> dict:
        return self._request("get", f"/search/jobs/{job_id}/messages",
                             params={"offset": offset, "limit": limit},
                             operation=f"fetch messages ({job_id})")

    def get_records(self, job_id: str, offset: int, limit: int) -> dict:
        return self._request("get", f"/search/jobs/{job_id}/records",
                             params={"offset": offset, "limit": limit},
                             operation=f"fetch records ({job_id})")

    def delete_job(self, job_id: str) -> None:
        """Best-effort cleanup — a failed delete is logged, never raised,
        so it never masks the real result of a search."""
        try:
            self._throttle()
            resp = self.session.delete(f"{self.base}/search/jobs/{job_id}")
            if resp.status_code not in (200, 204):
                logger.warning("delete job %s returned HTTP %s", job_id, resp.status_code)
        except requests.RequestException:
            logger.warning("failed to delete job %s (non-fatal)", job_id, exc_info=True)

    # -- read-only discovery endpoints -----------------------------------
    # Partitions, field extraction rules, and scheduled views are metadata
    # lookups, not search jobs — no job is created, polled, or deleted for
    # any of the three. All three share the same `{data: [...], next:
    # token}` paginated envelope, resolved once via `_list_paginated()`
    # rather than three separate pagination loops.

    def _list_paginated(self, path: str, params: dict | None = None, *,
                        page_size: int = 1000) -> list[dict]:
        items: list[dict] = []
        base_params = dict(params or {})
        base_params["limit"] = page_size
        token: str | None = None
        while True:
            # A fresh dict per request — reusing one across iterations would
            # let a later page's `token` bleed into an earlier recorded call.
            request_params = dict(base_params)
            if token:
                request_params["token"] = token
            page = self._request("get", path, params=request_params, operation=f"list {path}")
            items.extend(page.get("data", []))
            token = page.get("next")
            if not token:
                return items

    def list_partitions(self, *, view_types: list[str] | None = None) -> list[dict]:
        """GET /v1/partitions — every partition's `id`, `name`,
        `routingExpression`, `analyticsTier`, and retention, with no query
        executed. `routingExpression` (e.g. `_sourceCategory=*/Apache`) is
        what actually determines which logs land there — match a keyword
        against it, not just against `name`, when picking a partition (see
        `discovery-without-metadata` Fast Path B). `view_types` filters to
        any of `DefaultView`, `Partition`, `AuditIndex` (default: all)."""
        params = {"viewTypes": ",".join(view_types)} if view_types else None
        return self._list_paginated("/partitions", params)

    def list_extraction_rules(self) -> list[dict]:
        """GET /v1/extractionRules — every field extraction rule's `id`,
        `name`, `scope`, `parseExpression`, and the `fieldNames` it
        produces, with no query executed. `fieldNames` identifies
        index-time fields available for bloom-filter-speed scoping before
        assuming a field needs `| parse`/`| json` at search time."""
        return self._list_paginated("/extractionRules")

    def list_scheduled_views(self) -> list[dict]:
        """GET /v1/scheduledViews — every scheduled view's `id`,
        `indexName`, `query`, `status`, and size, with no query executed.
        `indexName` is the value to use as `_index=` once found; `query`
        plays the same role as a partition's `routingExpression`."""
        return self._list_paginated("/scheduledViews")

    # -- scan estimation (no job created) -------------------------------

    def estimate_scan(self, query: str, from_ms: int, to_ms: int, *,
                      time_zone: str = "UTC", by_view: bool = True) -> ScanEstimate:
        """Pre-flight scan-size estimate — POST /logSearches/estimatedUsageByView
        (or /estimatedUsage for an org-wide total instead of a per-partition
        breakdown). A distinct, synchronous endpoint: it never creates a
        search job, so there's nothing to poll or delete, and it costs no
        scan budget itself. Also doubles as a free syntax/scope check — a
        malformed query or unknown partition surfaces as a 400 `SumoSearchError`
        here, the same failure a real job would eventually report, but
        without spending anything to find out.

        Unlike create_job()/run_search(), from/to must be epoch milliseconds
        — resolve relative windows before calling, e.g.
        `to_ms = int(time.time() * 1000); from_ms = to_ms - 3_600_000`.
        """
        body = {
            "queryString": query,
            "timeRange": {
                "type": "BeginBoundedTimeRange",
                "from": {"type": "EpochTimeRangeBoundary", "epochMillis": int(from_ms)},
                "to": {"type": "EpochTimeRangeBoundary", "epochMillis": int(to_ms)},
            },
            "timezone": time_zone,
        }
        path = "estimatedUsageByView" if by_view else "estimatedUsage"
        raw = self._request("post", f"/logSearches/{path}", json_body=body,
                            operation=f"estimate scan usage ({path})")

        details = raw.get("estimatedUsageDetails", [])
        total_bytes = 0
        for entry in details:
            if entry.get("viewName") == "":
                entry["viewName"] = "sumologic_default"
            part_bytes = sum(u.get("dataScannedInBytes", 0)
                             for u in entry.get("usageDetails", []))
            entry["totalDataScannedInBytes"] = part_bytes
            total_bytes += part_bytes

        return ScanEstimate(total_bytes=total_bytes, partitions=details)

    # -- polling -------------------------------------------------------

    def poll_until_done(self, job_id: str, *,
                        timeout_s: float = DEFAULT_POLL_TIMEOUT_S) -> dict:
        """Poll with exponential backoff (1s -> 30s cap) until DONE GATHERING
        RESULTS. Checks `pendingErrors` on every poll — not only at DONE —
        since a broken query can report it almost immediately with 0 results."""
        deadline = time.monotonic() + timeout_s
        interval = 1.0
        last_state = None

        while time.monotonic() < deadline:
            try:
                status = self.get_status(job_id)
            except SumoSearchError as exc:
                if exc.status_code and exc.status_code >= 500:
                    logger.warning("transient poll error %s for job %s — retrying",
                                  exc.status_code, job_id)
                    time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
                    interval = min(interval * 2, 30.0)
                    continue
                raise

            errors = status.get("pendingErrors") or []
            if errors:
                raise SumoSearchJobFailed(
                    f"job {job_id} reported pendingErrors: {errors}", job_id=job_id,
                )

            state = status.get("state", "")
            if state != last_state:
                logger.debug("job %s state=%s messages=%s records=%s",
                            job_id, state, status.get("messageCount"), status.get("recordCount"))
                last_state = state

            if state == DONE_STATE:
                return status
            if state in TERMINAL_FAIL_STATES:
                raise SumoSearchJobFailed(f"job {job_id} ended in state {state}", job_id=job_id)

            sleep_time = min(interval, max(0.0, deadline - time.monotonic()))
            if sleep_time > 0:
                time.sleep(sleep_time)
            interval = min(interval * 2, 30.0)

        raise SumoSearchTimeout(f"timed out after {timeout_s}s waiting for job {job_id}",
                                job_id=job_id)

    # -- fetch + paginate -----------------------------------------------

    def fetch_all(self, job_id: str, status: dict, *,
                  limit: int | None = None,
                  page_size: int = DEFAULT_PAGE_SIZE) -> SearchJobResult:
        """Detect records vs. messages from the status response and page
        through results, advancing the offset by each batch's ACTUAL size
        (never a fixed page size) since a page can come back short."""
        record_count = status.get("recordCount", 0)
        message_count = status.get("messageCount", 0)
        result_type = "records" if record_count > 0 else "messages"
        total = record_count if result_type == "records" else message_count
        truncated = result_type == "messages" and total >= MAX_RAW_MESSAGES

        if truncated:
            logger.warning(
                "job %s hit the %d raw-message cap — results are truncated; "
                "use time_split_search() for a complete export", job_id, MAX_RAW_MESSAGES,
            )

        target = total if limit is None else min(total, limit)
        getter = self.get_records if result_type == "records" else self.get_messages
        key = result_type

        items: list[dict[str, Any]] = []
        offset = 0
        while offset < target:
            batch = min(page_size, target - offset)
            page = getter(job_id, offset, batch)
            rows = page.get(key, [])
            if not rows:
                break  # defensive: stop rather than loop forever on an unexpected empty page
            items.extend(rows)
            offset += len(rows)

        looks_like_lookup_table = result_type == "messages" and messages_lack_raw(items)
        if looks_like_lookup_table:
            logger.info(
                "job %s returned messages with no _raw — likely a lookup-table "
                "read (e.g. `cat /shared/lookups/...`); read the other map fields "
                "instead of _raw", job_id,
            )

        return SearchJobResult(
            job_id=job_id, result_type=result_type, total=total, items=items,
            pending_warnings=status.get("pendingWarnings", []), truncated=truncated,
            looks_like_lookup_table=looks_like_lookup_table,
        )

    # -- high-level entry point -----------------------------------------

    def run_search(self, query: str, from_time: int | str, to_time: int | str, *,
                   time_zone: str = "UTC", by_receipt_time: bool = False,
                   by_searchable_time: bool = False,
                   requires_raw_messages: bool | None = None,
                   auto_parsing_mode: str | None = None,
                   limit: int | None = None, page_size: int = DEFAULT_PAGE_SIZE,
                   poll_timeout_s: float = DEFAULT_POLL_TIMEOUT_S) -> SearchJobResult:
        """Create -> poll -> fetch -> delete, in one call. The job is always
        deleted, even if polling or fetching raises."""
        job = self.create_job(query, from_time, to_time, time_zone=time_zone,
                              by_receipt_time=by_receipt_time,
                              by_searchable_time=by_searchable_time,
                              requires_raw_messages=requires_raw_messages,
                              auto_parsing_mode=auto_parsing_mode)
        job_id = job.get("id")
        if not job_id:
            raise SumoSearchError(f"no job id in create response: {job}")

        try:
            status = self.poll_until_done(job_id, timeout_s=poll_timeout_s)
            for warning in status.get("pendingWarnings", []):
                logger.info("job %s pendingWarning: %s", job_id, warning)
            return self.fetch_all(job_id, status, limit=limit, page_size=page_size)
        finally:
            self.delete_job(job_id)


# ---------------------------------------------------------------------------
# Large-export helper: time-splitting
# ---------------------------------------------------------------------------

def estimate_count(client: SumoSearchClient, scope_query: str,
                   from_time: int | str, to_time: int | str, *,
                   time_zone: str = "UTC") -> int:
    """Cheap volume probe — run before committing to a large raw-message
    export, to decide whether time-splitting is needed at all."""
    count_query = f"{scope_query} | count"
    result = client.run_search(count_query, from_time, to_time, time_zone=time_zone,
                               requires_raw_messages=False)
    if not result.items:
        return 0
    return int(result.items[0]["map"]["_count"])


def time_split_search(client: SumoSearchClient, query: str,
                      from_ms: int, to_ms: int, *, interval_hours: float,
                      time_zone: str = "UTC",
                      result_type: str = "messages") -> list[dict[str, Any]]:
    """Run the same query across sequential fixed-size time windows and
    concatenate results — the fallback for raw-message exports that would
    exceed the 100,000-row per-job cap.

    Choose `interval_hours` so that no single window is expected to exceed
    ~80,000 rows; run `estimate_count()` first to size it. Each window is
    still guarded individually: if a window's own count hits the cap, the
    interval was too coarse for that window and needs to be narrowed
    further (this function raises rather than silently returning a
    truncated window).
    """
    interval_ms = int(interval_hours * 3_600_000)
    all_items: list[dict[str, Any]] = []
    window_start = from_ms

    while window_start < to_ms:
        window_end = min(window_start + interval_ms, to_ms)
        result = client.run_search(
            query, window_start, window_end, time_zone=time_zone,
            requires_raw_messages=(result_type == "messages"),
        )
        if result.truncated:
            raise ValueError(
                f"window {window_start}-{window_end} hit the {MAX_RAW_MESSAGES} "
                "message cap — reduce interval_hours and retry"
            )
        all_items.extend(result.items)
        window_start = window_end

    return all_items
