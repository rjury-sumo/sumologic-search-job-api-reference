"""
sumo_dashboard_client.py — standalone reference client for the Sumo Logic
Dashboard Report Job REST API (create -> poll -> fetch binary result).

Sibling to `sumo_search_client.py`, not an extension of it: dashboards are a
different API surface (`/api/v2/dashboards/...`) with a binary result
payload, so keeping them in a separate file keeps the search-job client free
of dashboard-specific concerns. Same distribution model — single file, one
external dependency (`requests`) plus the pure `resolve_time()` helper
imported from `sumo_search_client.py` — copy both files into your own
project and adapt.

Retry/throttle policy is copied verbatim from `sumo_search_client.py`
(429-only retry, exponential backoff + jitter, honoring `Retry-After`) — see
that file's module docstring for the rationale; it applies unchanged here.

Two gotchas worth knowing before using this client (see `run_report()` in a
CLI layer built on top of this module, or the functions below):

  1. The report-job API applies a dashboard's saved default *time range*
     automatically when `template.timeRange` is omitted, but does NOT apply
     saved default *variable values* the same way — omitting
     `template.variableValues` on a dashboard with `{{var}}` panels renders
     "Something went wrong" per-panel with no error at the job-status level.
     Fetch the dashboard first and merge each variable's own `defaultValue`
     in via `default_variable_values()` before submitting.
  2. `panelOverrides` (collapsing/expanding a `CollapsiblePanel` section)
     requires the dashboard's real panel list to validate the id — don't
     submit a panel override without having fetched the dashboard.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from sumo_search_client import resolve_time

logger = logging.getLogger("sumo_dashboard_client")

# ---------------------------------------------------------------------------
# Tunables — copied from sumo_search_client.py's retry/backoff policy
# ---------------------------------------------------------------------------

DEFAULT_MIN_INTERVAL = 0.25    # 4 requests/second — the per-key API limit
DEFAULT_MAX_RETRIES = 3        # additional attempts after a 429
DEFAULT_BASE_BACKOFF = 5.0     # seconds; doubles per attempt
DEFAULT_MAX_BACKOFF = 60.0     # seconds; backoff cap

DEFAULT_POLL_TIMEOUT_S = 180.0
POLL_INTERVAL_START = 1.0      # seconds; doubles per poll, capped below
POLL_INTERVAL_CAP = 10.0

EXPORT_WIDTH_MIN = 1500
EXPORT_WIDTH_MAX = 6000

# CLI-facing choice strings -> exact API enum strings.
VALID_FORMATS = {"pdf": "Pdf", "png": "Png"}
VALID_MODES = {"snapshot": "DashboardTemplate", "report-mode": "DashboardReportModeTemplate"}
VALID_THEMES = {"light": "Light", "dark": "Dark"}
VALID_COLLAPSE_STATES = {"collapsed": True, "expanded": False}

_EXT_FROM_CONTENT_TYPE = {"application/pdf": "pdf", "image/png": "png"}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class SumoDashboardError(Exception):
    """Base error for this client. Non-429 HTTP errors surface as this."""

    def __init__(self, message: str, *, status_code: int | None = None,
                job_id: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.job_id = job_id


class SumoDashboardJobFailed(SumoDashboardError):
    """Report job reported status "Failed"."""


class SumoDashboardTimeout(SumoDashboardError):
    """Polling exceeded the configured deadline without reaching "Success"."""


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------

@dataclass
class ReportResult:
    job_id: str
    content: bytes
    content_type: str
    ext: str = field(init=False)

    def __post_init__(self) -> None:
        self.ext = _EXT_FROM_CONTENT_TYPE.get(self.content_type.split(";")[0].strip(), "bin")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SumoDashboardClient:
    """Minimal client for the Sumo Logic Dashboard Report Job API.

    One throttled `requests.Session` per instance. Not thread-safe by
    design — same rationale as `SumoSearchClient`.
    """

    def __init__(self, access_id: str, access_key: str, endpoint: str, *,
                min_interval: float = DEFAULT_MIN_INTERVAL,
                max_retries: int = DEFAULT_MAX_RETRIES,
                base_backoff: float = DEFAULT_BASE_BACKOFF,
                max_backoff: float = DEFAULT_MAX_BACKOFF,
                session: requests.Session | None = None):
        self.base = endpoint.rstrip("/") + "/api"
        self.session = session or requests.Session()
        self.session.auth = HTTPBasicAuth(access_id, access_key)
        self.session.headers.update({
            "Content-Type": "application/json",
            # Not "application/json" — the report result endpoint returns
            # application/pdf or image/png.
            "Accept": "*/*",
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

    def _send_with_retry(self, method: str, path: str, *,
                         params: dict | None = None,
                         json_body: dict | None = None,
                         operation: str = "") -> requests.Response:
        """Throttled request with 429-only retry — same policy as
        `SumoSearchClient._request`. Returns the raw `Response`; callers
        decide how to interpret the body (JSON vs binary)."""
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
        return resp

    @staticmethod
    def _error_detail(resp: requests.Response) -> Any:
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def _request_json(self, method: str, path: str, *, params: dict | None = None,
                      json_body: dict | None = None, operation: str = "") -> dict:
        resp = self._send_with_retry(method, path, params=params, json_body=json_body,
                                     operation=operation)
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise SumoDashboardError(
                f"{operation or 'request'} failed: HTTP {resp.status_code} — {self._error_detail(resp)}",
                status_code=resp.status_code,
            ) from exc
        return resp.json() if resp.text.strip() else {}

    def _request_binary(self, method: str, path: str, *, operation: str = "") -> requests.Response:
        resp = self._send_with_retry(method, path, operation=operation)
        try:
            resp.raise_for_status()
        except requests.HTTPError as exc:
            raise SumoDashboardError(
                f"{operation or 'request'} failed: HTTP {resp.status_code} — {self._error_detail(resp)}",
                status_code=resp.status_code,
            ) from exc
        return resp

    # -- dashboard / report-job endpoints ------------------------------------

    def get_dashboard(self, dashboard_id: str) -> dict:
        return self._request_json("GET", f"/v2/dashboards/{dashboard_id}",
                                  operation=f"get dashboard {dashboard_id}")

    def create_report_job(self, body: dict) -> str:
        result = self._request_json("POST", "/v2/dashboards/reportJobs", json_body=body,
                                     operation="create report job")
        job_id = result.get("id")
        if not job_id:
            raise SumoDashboardError(f"create report job did not return an id: {result}")
        return job_id

    def get_report_status(self, job_id: str) -> dict:
        return self._request_json("GET", f"/v2/dashboards/reportJobs/{job_id}/status",
                                  operation=f"get report status {job_id}")

    def get_report_result(self, job_id: str) -> ReportResult:
        resp = self._request_binary("GET", f"/v2/dashboards/reportJobs/{job_id}/result",
                                    operation=f"get report result {job_id}")
        return ReportResult(job_id=job_id, content=resp.content,
                            content_type=resp.headers.get("Content-Type", ""))


# ---------------------------------------------------------------------------
# Pure helpers — time range, variables, panel overrides, job body, polling
# ---------------------------------------------------------------------------

def validate_export_width(width: int | None) -> None:
    if width is not None and not (EXPORT_WIDTH_MIN <= width <= EXPORT_WIDTH_MAX):
        raise ValueError(
            f"--export-width must be between {EXPORT_WIDTH_MIN} and {EXPORT_WIDTH_MAX}, got {width}"
        )


def resolve_report_time_range(
    hours: float | None, from_time: str | None, to_time: str | None,
) -> tuple[dict | None, int | None, int | None]:
    """Build a `template.timeRange` body fragment, or `(None, None, None)` if
    no time flag was given — in which case the dashboard's own saved default
    time range applies automatically server-side (unlike variable values,
    see `default_variable_values()`)."""
    if hours is None and not from_time and not to_time:
        return None, None, None

    if hours is not None:
        to_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        from_ms = to_ms - int(hours * 3_600_000)
    else:
        if not (from_time and to_time):
            raise ValueError("--from and --to must both be given (or use --hours instead).")
        from_ms = int(resolve_time(from_time))
        to_ms = int(resolve_time(to_time))

    time_range = {
        "type": "BeginBoundedTimeRange",
        "from": {"type": "EpochTimeRangeBoundary", "epochMillis": from_ms},
        "to": {"type": "EpochTimeRangeBoundary", "epochMillis": to_ms},
    }
    return time_range, from_ms, to_ms


def parse_variables(pairs: list[str]) -> dict[str, list[str]]:
    """`--variable NAME=VALUE`, repeatable per NAME for multi-select
    variables — output shape matches `variableValues.data`."""
    data: dict[str, list[str]] = {}
    for p in pairs:
        if "=" not in p:
            raise ValueError(f"--variable must be in NAME=VALUE form, got: {p!r}")
        name, _, value = p.partition("=")
        name = name.strip()
        value = value.strip()
        if not name:
            raise ValueError(f"--variable has an empty NAME: {p!r}")
        data.setdefault(name, []).append(value)
    return data


def default_variable_values(dashboard: dict) -> dict[str, list[str]]:
    """Each variable's own saved `defaultValue`, read from a fetched
    dashboard. The report-job API does not apply these on its own — see
    the module docstring."""
    data: dict[str, list[str]] = {}
    for v in dashboard.get("variables", []):
        name = v.get("name")
        default = v.get("defaultValue")
        if name and default:
            data[name] = [default]
    return data


def validate_variables(data: dict[str, list[str]], dashboard: dict) -> None:
    valid_names = {v.get("name") for v in dashboard.get("variables", []) if v.get("name")}
    unknown = [n for n in data if n not in valid_names]
    if unknown:
        raise ValueError(
            f"Unknown dashboard variable(s): {', '.join(unknown)}. "
            f"Valid variables for this dashboard: {', '.join(sorted(valid_names)) or '(none defined)'}."
        )


def parse_panel_overrides(pairs: list[str]) -> list[dict]:
    overrides = []
    seen_ids = set()
    for p in pairs:
        if "=" not in p:
            raise ValueError(f"--panel-override must be in ID=collapsed|expanded form, got: {p!r}")
        panel_id, _, state = p.partition("=")
        panel_id = panel_id.strip()
        state = state.strip().lower()
        if not panel_id:
            raise ValueError(f"--panel-override has an empty ID: {p!r}")
        if state not in VALID_COLLAPSE_STATES:
            raise ValueError(f"--panel-override state must be 'collapsed' or 'expanded', got: {state!r}")
        if panel_id in seen_ids:
            raise ValueError(f"--panel-override given more than once for panel id: {panel_id}")
        seen_ids.add(panel_id)
        overrides.append({
            "id": panel_id,
            "panelType": "CollapsiblePanel",
            "collapsed": VALID_COLLAPSE_STATES[state],
        })
    return overrides


def collapsible_panel_ids(dashboard: dict) -> dict[str, str]:
    """id -> title, for every CollapsiblePanel entry in a dashboard."""
    return {
        p["id"]: p.get("title") or p.get("key") or p["id"]
        for p in dashboard.get("panels", []) or []
        if p.get("panelType") == "CollapsiblePanel" and p.get("id")
    }


def validate_panel_overrides(overrides: list[dict], dashboard: dict) -> None:
    valid = collapsible_panel_ids(dashboard)
    unknown = [o["id"] for o in overrides if o["id"] not in valid]
    if unknown:
        available = ", ".join(f"{pid} ({title})" for pid, title in valid.items()) or \
            "(none — this dashboard has no collapsible sections)"
        raise ValueError(
            f"--panel-override references unknown/non-collapsible panel id(s): {', '.join(unknown)}. "
            f"Collapsible sections on this dashboard: {available}."
        )


def build_report_body(
    *, export_format: str, mode: str, theme: str | None, export_width: int | None,
    timezone_name: str, dashboard_id: str, time_range: dict | None,
    variables: dict[str, list[str]], panel_overrides: list[dict],
) -> dict:
    template = {"templateType": VALID_MODES[mode], "id": dashboard_id}
    if time_range:
        template["timeRange"] = time_range
    if variables:
        template["variableValues"] = {"data": variables}
    if panel_overrides:
        template["panelOverrides"] = panel_overrides

    body = {
        "action": {"actionType": "DirectDownloadReportAction"},
        "exportFormat": VALID_FORMATS[export_format],
        "timezone": timezone_name,
        "template": template,
    }
    if theme:
        body["theme"] = VALID_THEMES[theme]
    if export_width:
        body["exportWidth"] = export_width
    return body


def poll_report_job(client: SumoDashboardClient, job_id: str,
                    timeout_s: float = DEFAULT_POLL_TIMEOUT_S) -> dict:
    """Poll `GET .../reportJobs/{id}/status` until "Success"/"Failed" or the
    deadline passes. Backoff doubles from 1s up to a 10s cap; sleep is
    clamped so it never overshoots the deadline."""
    deadline = time.monotonic() + timeout_s
    interval = POLL_INTERVAL_START
    while time.monotonic() < deadline:
        status = client.get_report_status(job_id)
        state = status.get("status", "")
        if state == "Success":
            return status
        if state == "Failed":
            error = status.get("error") or status.get("statusMessage") or "unknown error"
            raise SumoDashboardJobFailed(f"Report job {job_id} failed: {error}", job_id=job_id)
        sleep_time = min(interval, max(0.0, deadline - time.monotonic()))
        if sleep_time > 0:
            time.sleep(sleep_time)
        interval = min(interval * 2, POLL_INTERVAL_CAP)
    raise SumoDashboardTimeout(f"Timed out after {timeout_s}s waiting for report job {job_id}",
                               job_id=job_id)
