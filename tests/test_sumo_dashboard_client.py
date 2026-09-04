"""
test_sumo_dashboard_client.py — unit tests for sumo_dashboard_client.py.

No credentials, no network. HTTP is faked via an injectable `session`
object (SumoDashboardClient accepts one directly, same as
SumoSearchClient), so these tests exercise the client's actual
request-building, retry, and polling logic rather than a re-implementation
of it. See tests/integration_test_sumo_dashboard_client.py for the live-API
counterpart.

Run:
    uv run pytest tests/test_sumo_dashboard_client.py
"""

from __future__ import annotations

import json as jsonlib

import pytest
import requests

import sumo_dashboard_client as sdc

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def make_response(status_code: int, json_body=None, content: bytes | None = None,
                   text: str | None = None, headers: dict | None = None) -> requests.Response:
    """Build a real requests.Response so raise_for_status()/.json()/.text/
    .content all behave exactly as they do against a live server."""
    resp = requests.Response()
    resp.status_code = status_code
    resp.headers.update(headers or {})
    if content is not None:
        body = content
    elif json_body is not None:
        body = jsonlib.dumps(json_body).encode()
    elif text is not None:
        body = text.encode()
    else:
        body = b""
    resp._content = body
    resp.url = "https://example.test/api/v2/dashboards/reportJobs"
    return resp


class FakeSession:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.auth = None
        self.headers: dict = {}

    def request(self, method, url, params=None, json=None):
        self.calls.append({"method": method, "url": url, "params": params, "json": json})
        return self._responses.pop(0)


def make_client(responses=(), **kwargs) -> sdc.SumoDashboardClient:
    session = FakeSession(list(responses))
    kwargs.setdefault("min_interval", 0.0)
    return sdc.SumoDashboardClient("id", "key", "https://api.example.com", session=session, **kwargs)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    monkeypatch.setattr(sdc.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sdc.random, "uniform", lambda _a, b: 0.0)


# ---------------------------------------------------------------------------
# session setup
# ---------------------------------------------------------------------------

def test_init_sets_accept_star_not_json():
    client = make_client()
    assert client.session.headers["Accept"] == "*/*"
    assert client.session.headers["Content-Type"] == "application/json"


def test_init_base_url_has_no_version_segment():
    client = make_client()
    assert client.base == "https://api.example.com/api"


# ---------------------------------------------------------------------------
# _request_json / _request_binary
# ---------------------------------------------------------------------------

def test_request_json_success_returns_parsed_body():
    client = make_client([make_response(200, json_body={"id": "dash-1"})])
    result = client._request_json("GET", "/v2/dashboards/dash-1")
    assert result == {"id": "dash-1"}


def test_request_json_success_empty_body_returns_empty_dict():
    client = make_client([make_response(202, content=b"")])
    assert client._request_json("POST", "/v2/dashboards/reportJobs") == {}


def test_request_json_http_error_with_json_detail():
    client = make_client([make_response(404, json_body={"message": "not found"})])
    with pytest.raises(sdc.SumoDashboardError) as exc_info:
        client._request_json("GET", "/v2/dashboards/nope", operation="get dashboard nope")
    assert exc_info.value.status_code == 404
    assert "not found" in str(exc_info.value)
    assert "get dashboard nope" in str(exc_info.value)


def test_request_binary_returns_raw_response():
    client = make_client([make_response(200, content=b"%PDF-fake-bytes",
                                        headers={"Content-Type": "application/pdf"})])
    resp = client._request_binary("GET", "/v2/dashboards/reportJobs/job-1/result")
    assert resp.content == b"%PDF-fake-bytes"
    assert resp.headers["Content-Type"] == "application/pdf"


def test_request_binary_http_error_falls_back_to_text_detail():
    client = make_client([make_response(500, text="internal error")])
    with pytest.raises(sdc.SumoDashboardError) as exc_info:
        client._request_binary("GET", "/v2/dashboards/reportJobs/job-1/result")
    assert exc_info.value.status_code == 500
    assert "internal error" in str(exc_info.value)


def test_request_retries_on_429_then_succeeds():
    client = make_client([make_response(429), make_response(200, json_body={"id": "job-1"})])
    result = client._request_json("POST", "/v2/dashboards/reportJobs")
    assert result == {"id": "job-1"}
    assert len(client.session.calls) == 2


def test_request_does_not_retry_on_4xx():
    client = make_client([make_response(400, json_body={"message": "bad request"})])
    with pytest.raises(sdc.SumoDashboardError):
        client._request_json("POST", "/v2/dashboards/reportJobs")
    assert len(client.session.calls) == 1


def test_request_exhausts_retries_returns_last_429_as_error():
    responses = [make_response(429) for _ in range(4)]  # 1 initial + 3 default retries
    client = make_client(responses, max_retries=3)
    with pytest.raises(sdc.SumoDashboardError) as exc_info:
        client._request_json("POST", "/v2/dashboards/reportJobs")
    assert exc_info.value.status_code == 429
    assert len(client.session.calls) == 4


# ---------------------------------------------------------------------------
# endpoint methods
# ---------------------------------------------------------------------------

def test_get_dashboard_path():
    client = make_client([make_response(200, json_body={"id": "dash-1"})])
    client.get_dashboard("dash-1")
    assert client.session.calls[0]["url"] == "https://api.example.com/api/v2/dashboards/dash-1"
    assert client.session.calls[0]["method"] == "GET"


# ---------------------------------------------------------------------------
# list_dashboards / project_dashboard_summary
# ---------------------------------------------------------------------------

def _dashboard(id_, **extra):
    base = {
        "id": id_, "contentId": f"c-{id_}", "title": f"title-{id_}",
        "description": "d", "folderId": "f-1", "domain": "Custom",
        "panels": [{"huge": "x" * 100}], "layout": {"a": 1}, "variables": [{"name": "v"}],
    }
    base.update(extra)
    return base


def test_project_dashboard_summary_drops_heavy_fields():
    summary = sdc.project_dashboard_summary(_dashboard("1"))
    assert summary == {
        "id": "1", "contentId": "c-1", "title": "title-1",
        "description": "d", "folderId": "f-1", "domain": "Custom",
    }


def test_list_dashboards_single_page_defaults_mode_and_limit():
    page = {"dashboards": [_dashboard("1"), _dashboard("2")], "next": None}
    client = make_client([make_response(200, json_body=page)])
    rows = client.list_dashboards()
    assert [r["id"] for r in rows] == ["1", "2"]
    assert "panels" not in rows[0]
    call = client.session.calls[0]
    assert call["method"] == "GET"
    assert call["url"] == "https://api.example.com/api/v2/dashboards"
    assert call["params"] == {"limit": sdc.DASHBOARD_PAGE_SIZE, "mode": "allViewableByUser"}


def test_list_dashboards_passes_through_mode():
    page = {"dashboards": [_dashboard("1")], "next": None}
    client = make_client([make_response(200, json_body=page)])
    client.list_dashboards(mode="createdByUser")
    assert client.session.calls[0]["params"]["mode"] == "createdByUser"


def test_list_dashboards_paginates_until_next_is_null():
    page1 = {"dashboards": [_dashboard("1")], "next": "tok-2"}
    page2 = {"dashboards": [_dashboard("2")], "next": None}
    client = make_client([make_response(200, json_body=page1), make_response(200, json_body=page2)])
    rows = client.list_dashboards()
    assert [r["id"] for r in rows] == ["1", "2"]
    assert len(client.session.calls) == 2
    assert "token" not in client.session.calls[0]["params"]
    assert client.session.calls[1]["params"]["token"] == "tok-2"


def test_create_report_job_returns_id():
    client = make_client([make_response(200, json_body={"id": "job-1"})])
    job_id = client.create_report_job({"exportFormat": "Pdf"})
    assert job_id == "job-1"
    call = client.session.calls[0]
    assert call["method"] == "POST"
    assert call["url"] == "https://api.example.com/api/v2/dashboards/reportJobs"
    assert call["json"] == {"exportFormat": "Pdf"}


def test_create_report_job_missing_id_raises():
    client = make_client([make_response(200, json_body={})])
    with pytest.raises(sdc.SumoDashboardError):
        client.create_report_job({"exportFormat": "Pdf"})


def test_get_report_status_path():
    client = make_client([make_response(200, json_body={"status": "Success"})])
    client.get_report_status("job-1")
    assert client.session.calls[0]["url"] == \
        "https://api.example.com/api/v2/dashboards/reportJobs/job-1/status"


def test_get_report_result_returns_report_result_with_ext():
    client = make_client([make_response(200, content=b"pdf-bytes",
                                        headers={"Content-Type": "application/pdf; charset=binary"})])
    result = client.get_report_result("job-1")
    assert result.job_id == "job-1"
    assert result.content == b"pdf-bytes"
    assert result.ext == "pdf"


def test_get_report_result_png_ext():
    client = make_client([make_response(200, content=b"png-bytes",
                                        headers={"Content-Type": "image/png"})])
    result = client.get_report_result("job-1")
    assert result.ext == "png"


def test_get_report_result_unknown_content_type_falls_back_to_bin():
    client = make_client([make_response(200, content=b"???",
                                        headers={"Content-Type": "application/octet-stream"})])
    result = client.get_report_result("job-1")
    assert result.ext == "bin"


# ---------------------------------------------------------------------------
# validate_export_width
# ---------------------------------------------------------------------------

def test_validate_export_width_none_ok():
    sdc.validate_export_width(None)


@pytest.mark.parametrize("width", [1500, 3000, 6000])
def test_validate_export_width_in_range_ok(width):
    sdc.validate_export_width(width)


@pytest.mark.parametrize("width", [1499, 6001, 0, -100])
def test_validate_export_width_out_of_range_raises(width):
    with pytest.raises(ValueError):
        sdc.validate_export_width(width)


# ---------------------------------------------------------------------------
# resolve_report_time_range
# ---------------------------------------------------------------------------

def test_resolve_report_time_range_no_flags_returns_none():
    time_range, from_ms, to_ms = sdc.resolve_report_time_range(None, None, None)
    assert (time_range, from_ms, to_ms) == (None, None, None)


def test_resolve_report_time_range_hours_takes_precedence():
    time_range, from_ms, to_ms = sdc.resolve_report_time_range(1.0, "-5h", "now")
    assert to_ms - from_ms == 3_600_000
    assert time_range["type"] == "BeginBoundedTimeRange"
    assert time_range["from"] == {"type": "EpochTimeRangeBoundary", "epochMillis": from_ms}
    assert time_range["to"] == {"type": "EpochTimeRangeBoundary", "epochMillis": to_ms}


def test_resolve_report_time_range_from_to_explicit():
    time_range, from_ms, to_ms = sdc.resolve_report_time_range(
        None, "1700000000000", "1700003600000",
    )
    assert from_ms == 1700000000000
    assert to_ms == 1700003600000
    assert time_range["from"]["epochMillis"] == 1700000000000


def test_resolve_report_time_range_missing_to_raises():
    with pytest.raises(ValueError):
        sdc.resolve_report_time_range(None, "-1h", None)


# ---------------------------------------------------------------------------
# parse_variables
# ---------------------------------------------------------------------------

def test_parse_variables_single():
    assert sdc.parse_variables(["region=us-east-1"]) == {"region": ["us-east-1"]}


def test_parse_variables_repeated_name_appends():
    assert sdc.parse_variables(["region=us-east-1", "region=eu-west-1"]) == {
        "region": ["us-east-1", "eu-west-1"],
    }


def test_parse_variables_missing_equals_raises():
    with pytest.raises(ValueError):
        sdc.parse_variables(["region"])


def test_parse_variables_empty_name_raises():
    with pytest.raises(ValueError):
        sdc.parse_variables(["=value"])


# ---------------------------------------------------------------------------
# default_variable_values / validate_variables — the silent-failure-trap fix
# ---------------------------------------------------------------------------

def test_default_variable_values_reads_saved_defaults():
    dashboard = {"variables": [
        {"name": "region", "defaultValue": "us-east-1"},
        {"name": "empty_default", "defaultValue": ""},
        {"name": "no_default"},
    ]}
    assert sdc.default_variable_values(dashboard) == {"region": ["us-east-1"]}


def test_validate_variables_unknown_name_raises():
    dashboard = {"variables": [{"name": "region"}]}
    with pytest.raises(ValueError):
        sdc.validate_variables({"not_region": ["x"]}, dashboard)


def test_validate_variables_known_name_ok():
    dashboard = {"variables": [{"name": "region"}]}
    sdc.validate_variables({"region": ["us-east-1"]}, dashboard)  # no raise


# ---------------------------------------------------------------------------
# parse_panel_overrides / collapsible_panel_ids / validate_panel_overrides
# ---------------------------------------------------------------------------

def test_parse_panel_overrides_builds_body_fragment():
    overrides = sdc.parse_panel_overrides(["panel-1=collapsed", "panel-2=expanded"])
    assert overrides == [
        {"id": "panel-1", "panelType": "CollapsiblePanel", "collapsed": True},
        {"id": "panel-2", "panelType": "CollapsiblePanel", "collapsed": False},
    ]


def test_parse_panel_overrides_invalid_state_raises():
    with pytest.raises(ValueError):
        sdc.parse_panel_overrides(["panel-1=sideways"])


def test_parse_panel_overrides_duplicate_id_raises():
    with pytest.raises(ValueError):
        sdc.parse_panel_overrides(["panel-1=collapsed", "panel-1=expanded"])


def test_collapsible_panel_ids_filters_by_type():
    dashboard = {"panels": [
        {"id": "p1", "panelType": "CollapsiblePanel", "title": "Section A"},
        {"id": "p2", "panelType": "SumoSearchPanel", "title": "Not collapsible"},
    ]}
    assert sdc.collapsible_panel_ids(dashboard) == {"p1": "Section A"}


def test_validate_panel_overrides_unknown_id_raises():
    dashboard = {"panels": [{"id": "p1", "panelType": "CollapsiblePanel"}]}
    with pytest.raises(ValueError):
        sdc.validate_panel_overrides([{"id": "not-p1"}], dashboard)


def test_validate_panel_overrides_known_id_ok():
    dashboard = {"panels": [{"id": "p1", "panelType": "CollapsiblePanel"}]}
    sdc.validate_panel_overrides([{"id": "p1"}], dashboard)  # no raise


# ---------------------------------------------------------------------------
# build_report_body
# ---------------------------------------------------------------------------

def test_build_report_body_minimal():
    body = sdc.build_report_body(
        export_format="pdf", mode="snapshot", theme=None, export_width=None,
        timezone_name="UTC", dashboard_id="dash-1", time_range=None,
        variables={}, panel_overrides=[],
    )
    assert body == {
        "action": {"actionType": "DirectDownloadReportAction"},
        "exportFormat": "Pdf",
        "timezone": "UTC",
        "template": {"templateType": "DashboardTemplate", "id": "dash-1"},
    }


def test_build_report_body_full():
    time_range = {"type": "BeginBoundedTimeRange"}
    body = sdc.build_report_body(
        export_format="png", mode="report-mode", theme="dark", export_width=3000,
        timezone_name="America/Los_Angeles", dashboard_id="dash-1", time_range=time_range,
        variables={"region": ["us-east-1"]}, panel_overrides=[{"id": "p1"}],
    )
    assert body["exportFormat"] == "Png"
    assert body["theme"] == "Dark"
    assert body["exportWidth"] == 3000
    assert body["template"]["templateType"] == "DashboardReportModeTemplate"
    assert body["template"]["timeRange"] == time_range
    assert body["template"]["variableValues"] == {"data": {"region": ["us-east-1"]}}
    assert body["template"]["panelOverrides"] == [{"id": "p1"}]


# ---------------------------------------------------------------------------
# poll_report_job
# ---------------------------------------------------------------------------

class _StatusSequenceClient:
    def __init__(self, statuses):
        self._statuses = iter(statuses)

    def get_report_status(self, job_id):
        return next(self._statuses)


def test_poll_report_job_returns_status_on_success():
    client = _StatusSequenceClient([
        {"status": "InProgress"},
        {"status": "Success", "id": "job-1"},
    ])
    result = sdc.poll_report_job(client, "job-1", timeout_s=5)
    assert result["status"] == "Success"


def test_poll_report_job_raises_on_failed():
    client = _StatusSequenceClient([{"status": "Failed", "error": "boom"}])
    with pytest.raises(sdc.SumoDashboardJobFailed) as exc_info:
        sdc.poll_report_job(client, "job-1", timeout_s=5)
    assert "boom" in str(exc_info.value)


class _AlwaysInProgressClient:
    def get_report_status(self, job_id):
        return {"status": "InProgress"}


def test_poll_report_job_times_out_if_never_terminal():
    with pytest.raises(sdc.SumoDashboardTimeout):
        sdc.poll_report_job(_AlwaysInProgressClient(), "job-1", timeout_s=0)
