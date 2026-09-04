# `sumo_dashboard_client.py` — reference

Lower-level usage of the dashboard report client that doesn't belong in
the top-level [README.md](../README.md) quickstart: manual job control,
variable/panel-override handling, constructor configuration, logging,
errors, and what the client deliberately leaves out. See the top-level
README's [Dashboard reports: export and discovery](../README.md#dashboard-reports-export-and-discovery)
section for the two agentic use cases this supports and how it compares
to the `sumosearch report` CLI and Sumo's MCP tools.

## Manual control (create/poll/fetch separately)

`build_report_body()` plus `poll_report_job()` cover the common case (see
the top-level README's dashboard quickstart). If you need to inspect the
job between steps, or you're building the request body by hand, use the
lower-level methods directly:

```python
body = {
    "action": {"actionType": "DirectDownloadReportAction"},
    "exportFormat": "Pdf",
    "timezone": "UTC",
    "template": {"templateType": "DashboardTemplate", "id": dashboard_id},
}
job_id = client.create_report_job(body)

status = client.get_report_status(job_id)   # {"status": "InProgress" | "Success" | "Failed", ...}

result = client.get_report_result(job_id)   # only once status == "Success"
with open(f"dashboard.{result.ext}", "wb") as f:
    f.write(result.content)
```

There is no `delete_job` equivalent — report jobs are not cleaned up
client-side the way search jobs are; the API doesn't expose a delete
endpoint for them.

## Variable values

The report-job API applies a dashboard's saved default *time range*
automatically when `template.timeRange` is omitted, but does **not**
apply saved default *variable values* the same way — a `{{var}}` panel
with no value supplied renders "Something went wrong" per-panel, with no
error surfaced at the job-status level. Always fetch the dashboard and
merge its defaults in before submitting, unless you know it has no
`{{variables}}`:

```python
from sumo_dashboard_client import default_variable_values, validate_variables

dashboard = client.get_dashboard(dashboard_id)
variables = default_variable_values(dashboard)      # {name: [defaultValue]}
variables["environment"] = ["prod"]                 # explicit values win per-name

validate_variables(variables, dashboard)             # raises on an unknown variable name
```

`parse_variables()` turns a list of repeatable `NAME=VALUE` strings (as
you'd collect from CLI flags) into this same `{name: [value, ...]}`
shape — multiple entries for one name accumulate, for multi-select
variables.

## Panel overrides (collapsible sections)

Collapsing or expanding a `CollapsiblePanel` section requires the
dashboard's real panel list to validate the id against — the API doesn't
error descriptively on an unknown id:

```python
from sumo_dashboard_client import (
    collapsible_panel_ids, parse_panel_overrides, validate_panel_overrides,
)

collapsible_panel_ids(dashboard)   # {panel_id: title} for every CollapsiblePanel

overrides = parse_panel_overrides(["abc123=collapsed"])
validate_panel_overrides(overrides, dashboard)   # raises on an unknown/non-collapsible id
```

## Time range

`resolve_report_time_range()` builds the `template.timeRange` body
fragment from either `--hours` or an explicit `from`/`to` pair (reusing
`resolve_time()` from `sumo_search_client.py` for the latter). Passing
neither returns `(None, None, None)` — in that case, omit `timeRange`
from the body entirely and the dashboard's own saved default time range
applies automatically server-side:

```python
from sumo_dashboard_client import resolve_report_time_range

time_range, from_ms, to_ms = resolve_report_time_range(
    hours=24, from_time=None, to_time=None,
)
```

## Configuration

```python
client = SumoDashboardClient(
    access_id, access_key, endpoint,
    min_interval=0.25,      # 4 req/sec — the per-key API limit
    max_retries=3,          # retries after an HTTP 429
    base_backoff=5.0,       # seconds; doubles per retry attempt
    max_backoff=60.0,       # seconds; backoff cap
)
```

`poll_report_job()` takes its own `timeout_s` (default 180s) — separate
from the request-level retry/backoff above, since it's polling job
status, not retrying a single failed request. Its poll interval starts
at 1s and doubles up to a 10s cap.

## Errors

All non-429 HTTP errors surface as `SumoDashboardError` (with
`status_code` and, where known, `job_id` attributes). Two subclasses
cover the two ways `poll_report_job()` can fail to reach `"Success"`:

- `SumoDashboardJobFailed` — the job itself reported `"Failed"`.
- `SumoDashboardTimeout` — polling exceeded `timeout_s` without the job
  reaching a terminal state.

## Logging

The client uses the standard `logging` module under the logger name
`sumo_dashboard_client`:

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## Output format

`get_report_result()` returns a `ReportResult` with `content` (raw
bytes), `content_type` (from the response's `Content-Type` header), and
`ext` — derived from `content_type` (`pdf` for `application/pdf`, `png`
for `image/png`, `bin` for anything else/unrecognized).

## What this client intentionally leaves out

To stay a lightweight, portable reference:

- No result caching or output post-processing (e.g. PDF-to-image
  conversion, thumbnailing) — bring your own for production use.
- No dashboard *content* management (create/update/delete a dashboard,
  its panels, or its variables) — this client is read/export-only,
  covering `GET .../dashboards/{id}` and the report-job endpoints, not
  the rest of the Dashboards API surface.
- No cross-process rate coordination — same caveat as
  `sumo_search_client.py`; see its
  [reference doc](sumo-search-client-reference.md#what-this-client-intentionally-leaves-out).
