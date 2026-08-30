"""
test_sumo_search_client.py — unit tests for sumo_search_client.py.

No credentials, no network. HTTP is faked via an injectable `session`
object (SumoSearchClient accepts one directly), so these tests exercise
the client's actual request-building, retry, polling, and pagination
logic rather than a re-implementation of it.

Run:
    uv run pytest
    uv run pytest -k retry
"""

from __future__ import annotations

import json as jsonlib

import pytest
import requests

import sumo_search_client as ssc

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def make_response(status_code: int, json_body=None, text: str | None = None,
                   headers: dict | None = None) -> requests.Response:
    """Build a real requests.Response so raise_for_status()/.json()/.text
    all behave exactly as they do against a live server."""
    resp = requests.Response()
    resp.status_code = status_code
    resp.headers.update(headers or {})
    if json_body is not None:
        content = jsonlib.dumps(json_body).encode()
    elif text is not None:
        content = text.encode()
    else:
        content = b""
    resp._content = content
    resp.url = "https://example.test/api/v1/search/jobs"
    return resp


class FakeSession:
    """Stand-in for requests.Session — queued responses, recorded calls.

    Matches the two call shapes SumoSearchClient actually uses:
    `.request(method, url, params=, json=)` and `.delete(url)`.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.auth = None
        self.headers: dict = {}

    def request(self, method, url, params=None, json=None):
        self.calls.append({"method": method, "url": url, "params": params, "json": json})
        return self._responses.pop(0)

    def delete(self, url):
        self.calls.append({"method": "delete", "url": url})
        return self._responses.pop(0)


def make_client(responses=(), **kwargs) -> ssc.SumoSearchClient:
    session = FakeSession(list(responses))
    kwargs.setdefault("min_interval", 0.0)
    return ssc.SumoSearchClient("id", "key", "https://api.example.com", session=session, **kwargs)


@pytest.fixture(autouse=True)
def no_real_sleep(monkeypatch):
    """Poll/retry backoff sleeps for real seconds by default — dead weight
    in a unit test. Deadlines in poll_until_done still rely on real
    monotonic time elapsing, so timeout tests stay correct, just fast."""
    monkeypatch.setattr(ssc.time, "sleep", lambda _s: None)
    monkeypatch.setattr(ssc.random, "uniform", lambda _a, b: 0.0)


# ---------------------------------------------------------------------------
# messages_lack_raw
# ---------------------------------------------------------------------------

def test_messages_lack_raw_empty_list():
    assert ssc.messages_lack_raw([]) is False


def test_messages_lack_raw_all_have_raw():
    msgs = [{"map": {"_raw": "log line 1"}}, {"map": {"_raw": "log line 2"}}]
    assert ssc.messages_lack_raw(msgs) is False


def test_messages_lack_raw_all_empty():
    msgs = [{"map": {"_raw": "", "col1": "a"}}, {"map": {"_raw": "", "col1": "b"}}]
    assert ssc.messages_lack_raw(msgs) is True


def test_messages_lack_raw_mixed_is_false():
    msgs = [{"map": {"_raw": ""}}, {"map": {"_raw": "has content"}}]
    assert ssc.messages_lack_raw(msgs) is False


def test_messages_lack_raw_missing_map_key():
    msgs = [{"map": {}}, {"map": {}}]
    assert ssc.messages_lack_raw(msgs) is True


# ---------------------------------------------------------------------------
# _retry_after_seconds / _backoff_seconds
# ---------------------------------------------------------------------------

def test_retry_after_seconds_valid():
    resp = make_response(429, headers={"Retry-After": "12"})
    assert ssc.SumoSearchClient._retry_after_seconds(resp) == 12.0


def test_retry_after_seconds_missing():
    resp = make_response(429)
    assert ssc.SumoSearchClient._retry_after_seconds(resp) is None


def test_retry_after_seconds_non_numeric():
    resp = make_response(429, headers={"Retry-After": "not-a-number"})
    assert ssc.SumoSearchClient._retry_after_seconds(resp) is None


def test_retry_after_seconds_negative_clamped_to_zero():
    resp = make_response(429, headers={"Retry-After": "-5"})
    assert ssc.SumoSearchClient._retry_after_seconds(resp) == 0.0


def test_backoff_seconds_exponential_no_jitter():
    client = make_client(base_backoff=5.0, max_backoff=60.0)
    assert client._backoff_seconds(0, None) == 5.0
    assert client._backoff_seconds(1, None) == 10.0
    assert client._backoff_seconds(2, None) == 20.0


def test_backoff_seconds_capped():
    client = make_client(base_backoff=5.0, max_backoff=60.0)
    assert client._backoff_seconds(10, None) == 60.0


def test_backoff_seconds_retry_after_floor():
    client = make_client(base_backoff=5.0, max_backoff=60.0)
    # retry_after (30) > computed backoff (5) -> retry_after wins
    assert client._backoff_seconds(0, 30.0) == 30.0
    # retry_after (1) < computed backoff (5) -> backoff wins
    assert client._backoff_seconds(0, 1.0) == 5.0


# ---------------------------------------------------------------------------
# _check
# ---------------------------------------------------------------------------

def test_check_success_returns_parsed_json():
    resp = make_response(200, json_body={"id": "abc123"})
    assert ssc.SumoSearchClient._check(resp, "op") == {"id": "abc123"}


def test_check_success_empty_body_returns_empty_dict():
    resp = make_response(204, text="")
    assert ssc.SumoSearchClient._check(resp, "op") == {}


def test_check_http_error_with_json_detail():
    resp = make_response(400, json_body={"message": "bad query syntax"})
    with pytest.raises(ssc.SumoSearchError) as exc_info:
        ssc.SumoSearchClient._check(resp, "create search job")
    assert exc_info.value.status_code == 400
    assert "bad query syntax" in str(exc_info.value)
    assert "create search job" in str(exc_info.value)


def test_check_http_error_with_non_json_body():
    resp = make_response(500, text="internal server error")
    with pytest.raises(ssc.SumoSearchError) as exc_info:
        ssc.SumoSearchClient._check(resp, "op")
    assert exc_info.value.status_code == 500
    assert "internal server error" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _format_time / resolve_time
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (1_700_000_000_000, "1700000000000"),
    (1_700_000_000_000.0, "1700000000000"),
    ("2025-05-20T00:00:00Z", "2025-05-20T00:00:00Z"),
    ("1700000000000", "1700000000000"),  # already-numeric string: pass through
])
def test_format_time_pass_through(value, expected):
    assert ssc.SumoSearchClient._format_time(value) == expected


def test_format_time_now_resolves_to_epoch_ms():
    before = int(__import__("time").time() * 1000)
    result = int(ssc.SumoSearchClient._format_time("now"))
    after = int(__import__("time").time() * 1000)
    assert before <= result <= after


@pytest.mark.parametrize("expr,expected_ms", [
    ("-1h", -3_600_000),
    ("-30m", -1_800_000),
    ("-2d", -2 * 86_400_000),
    ("-1w", -7 * 86_400_000),
    ("+30m", 1_800_000),
])
def test_format_time_relative_resolves_near_now(expr, expected_ms):
    now_ms = int(__import__("time").time() * 1000)
    result = int(ssc.SumoSearchClient._format_time(expr))
    assert abs(result - (now_ms + expected_ms)) < 5_000, f"{expr} off by too much"


def test_resolve_time_rejects_nothing_falls_back_to_pass_through():
    # An unrecognized string (e.g. malformed relative expr) is not our job
    # to validate — pass it through and let the API's own 400 report it.
    assert ssc.resolve_time("not-a-time") == "not-a-time"


# ---------------------------------------------------------------------------
# create_job / get_status / get_messages / get_records — request shape
# ---------------------------------------------------------------------------

def test_create_job_body_defaults():
    client = make_client([make_response(200, {"id": "job-1"})])
    client.create_job("_sourceCategory=* | count", "-1h", "now")
    body = client.session.calls[0]["json"]
    assert body["query"] == "_sourceCategory=* | count"
    # relative expressions are resolved to epoch-ms strings before sending —
    # the live API rejects "-1h"/"now" literally (HTTP 400)
    assert body["from"].isdigit()
    assert body["to"].isdigit()
    assert int(body["from"]) < int(body["to"])
    assert body["timeZone"] == "UTC"
    assert body["byReceiptTime"] is False
    assert body["bySearchableTime"] is False
    assert "requiresRawMessages" not in body
    assert "autoParsingMode" not in body


def test_create_job_body_includes_requires_raw_messages_when_set():
    client = make_client([make_response(200, {"id": "job-1"})])
    client.create_job("* | count", "-1h", "now", requires_raw_messages=False)
    body = client.session.calls[0]["json"]
    assert body["requiresRawMessages"] is False


def test_create_job_body_by_searchable_time():
    client = make_client([make_response(200, {"id": "job-1"})])
    client.create_job("*", "-1h", "now", by_searchable_time=True)
    body = client.session.calls[0]["json"]
    assert body["bySearchableTime"] is True
    assert body["byReceiptTime"] is False


def test_create_job_body_includes_auto_parsing_mode_when_set():
    client = make_client([make_response(200, {"id": "job-1"})])
    client.create_job("*", "-1h", "now", auto_parsing_mode="AutoParse")
    body = client.session.calls[0]["json"]
    assert body["autoParsingMode"] == "AutoParse"


def test_create_job_epoch_time_formatted_as_string():
    client = make_client([make_response(200, {"id": "job-1"})])
    client.create_job("*", 1_700_000_000_000, 1_700_003_600_000)
    body = client.session.calls[0]["json"]
    assert body["from"] == "1700000000000"
    assert body["to"] == "1700003600000"


def test_get_status_path():
    client = make_client([make_response(200, {"state": "DONE GATHERING RESULTS"})])
    client.get_status("job-42")
    call = client.session.calls[0]
    assert call["method"] == "get"
    assert call["url"].endswith("/search/jobs/job-42")


def test_get_messages_params():
    client = make_client([make_response(200, {"messages": []})])
    client.get_messages("job-1", offset=100, limit=50)
    call = client.session.calls[0]
    assert call["url"].endswith("/search/jobs/job-1/messages")
    assert call["params"] == {"offset": 100, "limit": 50}


def test_get_records_params():
    client = make_client([make_response(200, {"records": []})])
    client.get_records("job-1", offset=0, limit=10)
    call = client.session.calls[0]
    assert call["url"].endswith("/search/jobs/job-1/records")
    assert call["params"] == {"offset": 0, "limit": 10}


# ---------------------------------------------------------------------------
# delete_job — best-effort, never raises
# ---------------------------------------------------------------------------

def test_delete_job_success_no_raise():
    client = make_client([make_response(200)])
    client.delete_job("job-1")  # should not raise


def test_delete_job_non_2xx_logged_not_raised():
    client = make_client([make_response(500, text="oops")])
    client.delete_job("job-1")  # should not raise, just log a warning


def test_delete_job_network_exception_swallowed():
    class RaisingSession(FakeSession):
        def delete(self, url):
            raise requests.ConnectionError("network down")

    client = ssc.SumoSearchClient("id", "key", "https://api.example.com",
                                  session=RaisingSession([]), min_interval=0.0)
    client.delete_job("job-1")  # should not raise


# ---------------------------------------------------------------------------
# _request — 429-only retry
# ---------------------------------------------------------------------------

def test_request_retries_on_429_then_succeeds():
    client = make_client([make_response(429), make_response(200, {"id": "job-1"})])
    result = client.create_job("*", "-1h", "now")
    assert result == {"id": "job-1"}
    assert len(client.session.calls) == 2


def test_request_does_not_retry_on_4xx():
    client = make_client([make_response(400, {"message": "bad query"})])
    with pytest.raises(ssc.SumoSearchError) as exc_info:
        client.create_job("*", "-1h", "now")
    assert exc_info.value.status_code == 400
    assert len(client.session.calls) == 1


def test_request_does_not_retry_on_5xx():
    client = make_client([make_response(500, text="server error")])
    with pytest.raises(ssc.SumoSearchError) as exc_info:
        client.create_job("*", "-1h", "now")
    assert exc_info.value.status_code == 500
    assert len(client.session.calls) == 1


def test_request_exhausts_retries_returns_last_429_as_error():
    responses = [make_response(429) for _ in range(4)]  # 1 initial + 3 default retries
    client = make_client(responses, max_retries=3)
    with pytest.raises(ssc.SumoSearchError) as exc_info:
        client.create_job("*", "-1h", "now")
    assert exc_info.value.status_code == 429
    assert len(client.session.calls) == 4


# ---------------------------------------------------------------------------
# poll_until_done
# ---------------------------------------------------------------------------

def test_poll_returns_status_on_done():
    client = make_client()
    statuses = iter([
        {"state": "GATHERING RESULTS", "pendingErrors": []},
        {"state": "DONE GATHERING RESULTS", "pendingErrors": [], "recordCount": 5},
    ])
    client.get_status = lambda job_id: next(statuses)
    result = client.poll_until_done("job-1", timeout_s=5)
    assert result["state"] == "DONE GATHERING RESULTS"


def test_poll_raises_immediately_on_pending_errors():
    client = make_client()
    # 0/0 counts but pendingErrors present — the case a naive counts-only
    # check would silently misreport as an empty-but-valid search.
    client.get_status = lambda job_id: {
        "state": "GATHERING RESULTS", "messageCount": 0, "recordCount": 0,
        "pendingErrors": [{"code": "SYNTAX", "message": "bad field name"}],
    }
    with pytest.raises(ssc.SumoSearchJobFailed):
        client.poll_until_done("job-1", timeout_s=5)


@pytest.mark.parametrize("state", ["CANCELLED", "FORCE PAUSED"])
def test_poll_raises_on_terminal_fail_states(state):
    client = make_client()
    client.get_status = lambda job_id: {"state": state, "pendingErrors": []}
    with pytest.raises(ssc.SumoSearchJobFailed):
        client.poll_until_done("job-1", timeout_s=5)


def test_poll_times_out_if_never_done():
    client = make_client()
    client.get_status = lambda job_id: {"state": "GATHERING RESULTS", "pendingErrors": []}
    with pytest.raises(ssc.SumoSearchTimeout):
        client.poll_until_done("job-1", timeout_s=0.02)


def test_poll_tolerates_transient_5xx_then_succeeds():
    client = make_client()
    calls = {"n": 0}

    def flaky_get_status(job_id):
        calls["n"] += 1
        if calls["n"] <= 2:
            raise ssc.SumoSearchError("server hiccup", status_code=503)
        return {"state": "DONE GATHERING RESULTS", "pendingErrors": []}

    client.get_status = flaky_get_status
    result = client.poll_until_done("job-1", timeout_s=5)
    assert result["state"] == "DONE GATHERING RESULTS"
    assert calls["n"] == 3


def test_poll_does_not_retry_non_5xx_error():
    client = make_client()

    def bad_request(job_id):
        raise ssc.SumoSearchError("not found", status_code=404)

    client.get_status = bad_request
    with pytest.raises(ssc.SumoSearchError) as exc_info:
        client.poll_until_done("job-1", timeout_s=5)
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# fetch_all — pagination, truncation, result-type detection
# ---------------------------------------------------------------------------

def test_fetch_all_detects_records_over_messages():
    client = make_client()
    client.get_records = lambda job_id, offset, limit: {
        "records": [{"map": {"_count": "7"}}]
    }
    status = {"recordCount": 1, "messageCount": 0}
    result = client.fetch_all("job-1", status)
    assert result.result_type == "records"
    assert result.total == 1
    assert result.items[0]["map"]["_count"] == "7"


def test_fetch_all_paginates_by_actual_batch_size():
    """A page can return fewer rows than requested — offset must advance
    by the real batch size, not the requested page_size."""
    client = make_client()
    pages = {
        0: [{"map": {"_raw": "a"}}, {"map": {"_raw": "b"}}],  # short page: 2 of a 5-size page
        2: [{"map": {"_raw": "c"}}],
    }
    calls = []

    def get_messages(job_id, offset, limit):
        calls.append((offset, limit))
        return {"messages": pages.get(offset, [])}

    client.get_messages = get_messages
    status = {"recordCount": 0, "messageCount": 3}
    result = client.fetch_all("job-1", status, page_size=5)
    assert [row["map"]["_raw"] for row in result.items] == ["a", "b", "c"]
    assert calls == [(0, 3), (2, 1)]


def test_fetch_all_limit_caps_target():
    client = make_client()
    calls = []

    def get_messages(job_id, offset, limit):
        calls.append((offset, limit))
        return {"messages": [{"map": {"_raw": "x"}}] * limit}

    client.get_messages = get_messages
    status = {"recordCount": 0, "messageCount": 1000}
    result = client.fetch_all("job-1", status, limit=10, page_size=1000)
    assert len(result.items) == 10
    assert calls == [(0, 10)]


def test_fetch_all_truncated_flag_at_message_cap():
    client = make_client()
    client.get_messages = lambda job_id, offset, limit: {"messages": []}
    status = {"recordCount": 0, "messageCount": ssc.MAX_RAW_MESSAGES}
    result = client.fetch_all("job-1", status, limit=0)
    assert result.truncated is True


def test_fetch_all_not_truncated_below_cap():
    client = make_client()
    client.get_messages = lambda job_id, offset, limit: {"messages": []}
    status = {"recordCount": 0, "messageCount": ssc.MAX_RAW_MESSAGES - 1}
    result = client.fetch_all("job-1", status, limit=0)
    assert result.truncated is False


def test_fetch_all_detects_lookup_table():
    client = make_client()
    client.get_messages = lambda job_id, offset, limit: {
        "messages": [{"map": {"_raw": "", "status": "active"}},
                     {"map": {"_raw": "", "status": "inactive"}}]
    }
    status = {"recordCount": 0, "messageCount": 2}
    result = client.fetch_all("job-1", status)
    assert result.looks_like_lookup_table is True
    assert result.items[0]["map"]["status"] == "active"


def test_fetch_all_stops_on_unexpected_empty_page():
    """Defensive: an empty page before reaching `target` breaks the loop
    instead of spinning forever."""
    client = make_client()
    client.get_messages = lambda job_id, offset, limit: {"messages": []}
    status = {"recordCount": 0, "messageCount": 50}
    result = client.fetch_all("job-1", status)
    assert result.items == []
    assert result.total == 50


# ---------------------------------------------------------------------------
# run_search — orchestration + always-delete guarantee
# ---------------------------------------------------------------------------

def test_run_search_happy_path_deletes_job():
    client = make_client()
    calls = {"deleted": None}
    client.create_job = lambda *a, **k: {"id": "job-9"}
    client.poll_until_done = lambda job_id, timeout_s: {
        "state": "DONE GATHERING RESULTS", "recordCount": 1, "messageCount": 0,
        "pendingWarnings": [],
    }
    client.fetch_all = lambda job_id, status, limit, page_size: ssc.SearchJobResult(
        job_id=job_id, result_type="records", total=1, items=[{"map": {"_count": "3"}}],
    )
    client.delete_job = lambda job_id: calls.__setitem__("deleted", job_id)

    result = client.run_search("* | count", "-1h", "now")
    assert result.items[0]["map"]["_count"] == "3"
    assert calls["deleted"] == "job-9"


def test_run_search_deletes_job_even_when_poll_fails():
    client = make_client()
    calls = {"deleted": None}
    client.create_job = lambda *a, **k: {"id": "job-9"}

    def failing_poll(job_id, timeout_s):
        raise ssc.SumoSearchJobFailed("bad query", job_id=job_id)

    client.poll_until_done = failing_poll
    client.delete_job = lambda job_id: calls.__setitem__("deleted", job_id)

    with pytest.raises(ssc.SumoSearchJobFailed):
        client.run_search("_sourceCateogry=typo | count", "-1h", "now")
    assert calls["deleted"] == "job-9"


def test_run_search_no_job_id_raises_without_delete():
    client = make_client()
    calls = {"deleted": False}
    client.create_job = lambda *a, **k: {}  # no "id" key
    client.delete_job = lambda job_id: calls.__setitem__("deleted", True)

    with pytest.raises(ssc.SumoSearchError):
        client.run_search("*", "-1h", "now")
    assert calls["deleted"] is False


# ---------------------------------------------------------------------------
# list_partitions / list_extraction_rules / list_scheduled_views
# ---------------------------------------------------------------------------

def test_list_partitions_single_page():
    client = make_client([make_response(200, {
        "data": [{"id": "1", "name": "apache", "routingExpression": "_sourceCategory=*/Apache"}],
    })])
    result = client.list_partitions()
    assert result == [{"id": "1", "name": "apache", "routingExpression": "_sourceCategory=*/Apache"}]
    call = client.session.calls[0]
    assert call["url"].endswith("/partitions")
    assert call["params"]["limit"] == 1000
    assert "token" not in call["params"]


def test_list_partitions_paginates_across_token():
    client = make_client([
        make_response(200, {"data": [{"id": "1"}], "next": "tok-abc"}),
        make_response(200, {"data": [{"id": "2"}]}),
    ])
    result = client.list_partitions()
    assert [p["id"] for p in result] == ["1", "2"]
    assert len(client.session.calls) == 2
    assert "token" not in client.session.calls[0]["params"]
    assert client.session.calls[1]["params"]["token"] == "tok-abc"


def test_list_partitions_view_types_comma_joined():
    client = make_client([make_response(200, {"data": []})])
    client.list_partitions(view_types=["Partition", "AuditIndex"])
    call = client.session.calls[0]
    assert call["params"]["viewTypes"] == "Partition,AuditIndex"


def test_list_partitions_no_view_types_omits_param():
    client = make_client([make_response(200, {"data": []})])
    client.list_partitions()
    assert "viewTypes" not in client.session.calls[0]["params"]


def test_list_extraction_rules_returns_data():
    client = make_client([make_response(200, {
        "data": [{"id": "1", "name": "rule1", "scope": "_sourceHost=127.0.0.1",
                  "parseExpression": "csv _raw extract 1 as f1", "fieldNames": ["f1"]}],
    })])
    result = client.list_extraction_rules()
    assert result[0]["fieldNames"] == ["f1"]
    assert client.session.calls[0]["url"].endswith("/extractionRules")


def test_list_scheduled_views_returns_data():
    client = make_client([make_response(200, {
        "data": [{"id": "1", "indexName": "cloudtrail_view",
                  "query": "_sourceCategory=*/cloudtrail", "status": "COMPLETE"}],
    })])
    result = client.list_scheduled_views()
    assert result[0]["indexName"] == "cloudtrail_view"
    assert client.session.calls[0]["url"].endswith("/scheduledViews")


def test_list_paginated_propagates_http_error():
    client = make_client([make_response(403, {"message": "insufficient permissions"})])
    with pytest.raises(ssc.SumoSearchError) as exc_info:
        client.list_partitions()
    assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# estimate_scan
# ---------------------------------------------------------------------------

def test_estimate_scan_sums_bytes_across_partitions():
    client = make_client([make_response(200, {
        "estimatedUsageDetails": [
            {"viewName": "prod_view", "usageDetails": [
                {"tier": "Continuous", "dataScannedInBytes": 1000},
                {"tier": "Infrequent", "dataScannedInBytes": 2000},
            ]},
            {"viewName": "", "usageDetails": [{"tier": "Continuous", "dataScannedInBytes": 500}]},
        ]
    })])
    estimate = client.estimate_scan("_sourceCategory=* error", 1_700_000_000_000, 1_700_003_600_000)
    assert estimate.total_bytes == 3500
    assert estimate.partitions[0]["totalDataScannedInBytes"] == 3000
    assert estimate.partitions[1]["viewName"] == "sumologic_default"


def test_estimate_scan_body_uses_epoch_millis_and_by_view_path():
    client = make_client([make_response(200, {"estimatedUsageDetails": []})])
    client.estimate_scan("*", 1_700_000_000_000, 1_700_003_600_000, by_view=True)
    call = client.session.calls[0]
    assert call["url"].endswith("/logSearches/estimatedUsageByView")
    body = call["json"]
    assert body["timeRange"]["from"]["epochMillis"] == 1_700_000_000_000
    assert body["timeRange"]["to"]["epochMillis"] == 1_700_003_600_000


def test_estimate_scan_org_wide_path_when_by_view_false():
    client = make_client([make_response(200, {"estimatedUsageDetails": []})])
    client.estimate_scan("*", 0, 1, by_view=False)
    assert client.session.calls[0]["url"].endswith("/logSearches/estimatedUsage")


def test_estimate_scan_bad_query_raises_with_400():
    client = make_client([make_response(400, {"message": "unknown partition"})])
    with pytest.raises(ssc.SumoSearchError) as exc_info:
        client.estimate_scan("_view=doesNotExist *", 0, 1)
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# estimate_count
# ---------------------------------------------------------------------------

def test_estimate_count_builds_count_query_and_parses_result():
    captured = {}

    class StubClient:
        def run_search(self, query, from_time, to_time, *, time_zone="UTC", requires_raw_messages=None):
            captured["query"] = query
            captured["requires_raw_messages"] = requires_raw_messages
            return ssc.SearchJobResult(job_id="j", result_type="records", total=1,
                                       items=[{"map": {"_count": "4242"}}])

    total = ssc.estimate_count(StubClient(), "_sourceCategory=prod/app error", "-1h", "now")
    assert total == 4242
    assert captured["query"] == "_sourceCategory=prod/app error | count"
    assert captured["requires_raw_messages"] is False


def test_estimate_count_empty_items_returns_zero():
    class StubClient:
        def run_search(self, *a, **k):
            return ssc.SearchJobResult(job_id="j", result_type="records", total=0, items=[])

    assert ssc.estimate_count(StubClient(), "*", "-1h", "now") == 0


# ---------------------------------------------------------------------------
# time_split_search
# ---------------------------------------------------------------------------

def test_time_split_search_splits_into_windows_and_concatenates():
    windows = []

    class StubClient:
        def run_search(self, query, from_ms, to_ms, *, time_zone="UTC", requires_raw_messages=None):
            windows.append((from_ms, to_ms))
            return ssc.SearchJobResult(job_id="j", result_type="messages", total=1,
                                       items=[{"map": {"_raw": f"{from_ms}"}}], truncated=False)

    from_ms, to_ms = 0, 10_800_000  # 3 hours
    items = ssc.time_split_search(StubClient(), "error", from_ms, to_ms, interval_hours=1)
    assert windows == [(0, 3_600_000), (3_600_000, 7_200_000), (7_200_000, 10_800_000)]
    assert len(items) == 3


def test_time_split_search_raises_when_window_truncated():
    class StubClient:
        def run_search(self, *a, **k):
            return ssc.SearchJobResult(job_id="j", result_type="messages", total=ssc.MAX_RAW_MESSAGES,
                                       items=[], truncated=True)

    with pytest.raises(ValueError):
        ssc.time_split_search(StubClient(), "*", 0, 3_600_000, interval_hours=1)
