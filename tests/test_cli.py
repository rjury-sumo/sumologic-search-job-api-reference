"""
test_cli.py — unit tests for the sumosearch CLI (cli/main.py, cli/formats.py).

No credentials, no network — mirrors tests/test_sumo_search_client.py's
fake-session pattern: build a real SumoSearchClient against a FakeSession,
then monkeypatch its higher-level methods directly (the same pattern
test_sumo_search_client.py uses for test_run_search_happy_path_deletes_job
etc.), and monkeypatch cli.main.SumoSearchClient so commands pick up the
pre-built fake client instead of constructing a real one.

Requires the `cli` dependency group (typer). `pytest.importorskip` below
keeps `uv sync --group dev` alone (no `cli` group) from breaking
tests/test_sumo_search_client.py's collection, per AGENTS.md.

Run:
    uv run pytest tests/test_cli.py
"""

from __future__ import annotations

import json as jsonlib

import pytest
import requests

pytest.importorskip("typer")

from typer.testing import CliRunner  # noqa: E402

import cli.instances as instances_mod  # noqa: E402
import cli.main as clim  # noqa: E402
import cli.report_paths as report_paths_mod  # noqa: E402
import cli.schema as schema_mod  # noqa: E402
import sumo_dashboard_client as sdc  # noqa: E402
import sumo_search_client as ssc  # noqa: E402

runner = CliRunner()

ENV = {
    "SUMO_ACCESS_ID": "test-id",
    "SUMO_ACCESS_KEY": "test-key",
    "SUMO_ENDPOINT": "https://api.example.com",
}


@pytest.fixture(autouse=True)
def _isolated_instances_config(monkeypatch, tmp_path):
    """Every command now consults cli.instances (current context lookup) even
    when --instance isn't passed — point it at a per-test tmp_path so tests
    never read/write the developer's real ~/sumo-search/config.yaml."""
    monkeypatch.setattr(instances_mod, "CONFIG_DIR", tmp_path / "sumo-search")


# ---------------------------------------------------------------------------
# Fakes — same shape as tests/test_sumo_search_client.py's helpers
# ---------------------------------------------------------------------------

def make_response(status_code: int, json_body=None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = status_code
    resp._content = jsonlib.dumps(json_body).encode() if json_body is not None else b""
    resp.url = "https://api.example.com/api/v1/search/jobs"
    return resp


class FakeSession:
    def __init__(self):
        self.calls: list[dict] = []
        self.auth = None
        self.headers: dict = {}

    def request(self, method, url, params=None, json=None):
        self.calls.append({"method": method, "url": url, "params": params, "json": json})
        return make_response(200, {})

    def delete(self, url):
        return make_response(200)


def make_fake_client(**method_overrides) -> ssc.SumoSearchClient:
    client = ssc.SumoSearchClient(
        "test-id", "test-key", "https://api.example.com", session=FakeSession(), min_interval=0.0,
    )
    for name, fn in method_overrides.items():
        setattr(client, name, fn)
    return client


def patch_client(monkeypatch, client) -> None:
    monkeypatch.setattr(clim, "SumoSearchClient", lambda *a, **k: client)


def patch_client_capture(monkeypatch, client) -> dict:
    """Like patch_client, but also records the (access_id, access_key, endpoint)
    positional args SumoSearchClient was constructed with, so tests can assert
    on how --instance/context resolution fed _client()."""
    captured: dict = {}

    def fake_ctor(*a, **k):
        captured["args"] = a
        return client

    monkeypatch.setattr(clim, "SumoSearchClient", fake_ctor)
    return captured


def records_result(rows: list[dict], total: int | None = None) -> ssc.SearchJobResult:
    items = [{"map": r} for r in rows]
    return ssc.SearchJobResult(
        job_id="job-1", result_type="records", total=total if total is not None else len(items),
        items=items,
    )


def messages_result(rows: list[dict], total: int | None = None) -> ssc.SearchJobResult:
    items = [{"map": r} for r in rows]
    return ssc.SearchJobResult(
        job_id="job-1", result_type="messages", total=total if total is not None else len(items),
        items=items,
    )


# ---------------------------------------------------------------------------
# Fakes for `report` — same shape as make_fake_client/patch_client above,
# but for SumoDashboardClient.
# ---------------------------------------------------------------------------

def make_fake_dashboard_client(**method_overrides) -> sdc.SumoDashboardClient:
    client = sdc.SumoDashboardClient(
        "test-id", "test-key", "https://api.example.com", session=FakeSession(), min_interval=0.0,
    )
    # Sensible defaults so a test only needs to override what it cares about.
    client.get_dashboard = lambda dashboard_id: {"id": dashboard_id, "title": "Test Dashboard",
                                                  "variables": [], "panels": []}
    client.create_report_job = lambda body: "job-1"
    client.get_report_status = lambda job_id: {"status": "Success", "id": job_id}
    client.get_report_result = lambda job_id: report_result()
    for name, fn in method_overrides.items():
        setattr(client, name, fn)
    return client


def patch_dashboard_client(monkeypatch, client) -> None:
    monkeypatch.setattr(clim, "SumoDashboardClient", lambda *a, **k: client)


def report_result(content: bytes = b"%PDF-fake", content_type: str = "application/pdf") -> sdc.ReportResult:
    return sdc.ReportResult(job_id="job-1", content=content, content_type=content_type)


# ---------------------------------------------------------------------------
# search run — records path
# ---------------------------------------------------------------------------

def test_search_run_records_default_is_csv(monkeypatch):
    result = records_result([{"_count": "3", "host": "a"}, {"_count": "7", "host": "b"}])
    client = make_fake_client(run_search=lambda *a, **k: result)
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "run", "* | count by host", "--from", "-1h", "--to", "now"],
                        env=ENV)
    assert out.exit_code == 0, out.output
    lines = out.stdout.strip().splitlines()
    assert lines[0] == "_count,host"
    assert "3,a" in lines[1]


def test_search_run_records_json_envelope(monkeypatch):
    result = records_result([{"_count": "3"}])
    client = make_fake_client(run_search=lambda *a, **k: result)
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "run", "* | count", "--from", "-1h", "--to", "now",
                                   "--format", "json"], env=ENV)
    assert out.exit_code == 0, out.output
    envelope = jsonlib.loads(out.stdout)
    assert envelope["result_type"] == "records"
    assert envelope["total"] == 1
    assert envelope["items"] == [{"_count": "3"}]


def test_search_run_records_table(monkeypatch):
    result = records_result([{"a": "1", "b": "2"}])
    client = make_fake_client(run_search=lambda *a, **k: result)
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "run", "*", "--from", "-1h", "--to", "now",
                                   "--format", "table"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "a" in out.stdout and "b" in out.stdout and "1" in out.stdout


# ---------------------------------------------------------------------------
# search run — messages path
# ---------------------------------------------------------------------------

def test_search_run_messages_default_is_ndjson(monkeypatch):
    rows = [
        {"_messagetime": "1", "_sourcecategory": "sc", "_sourcehost": "h", "_raw": "line1", "extra": "x"},
        {"_messagetime": "2", "_sourcecategory": "sc", "_sourcehost": "h", "_raw": "line2", "extra": "y"},
    ]
    result = messages_result(rows)
    client = make_fake_client(run_search=lambda *a, **k: result)
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "run", "* | limit 2", "--from", "-1h", "--to", "now"],
                        env=ENV)
    assert out.exit_code == 0, out.output
    lines = [jsonlib.loads(line) for line in out.stdout.strip().splitlines()]
    assert len(lines) == 2
    # fixed envelope projected, "extra" dropped since not requested via --fields
    assert set(lines[0]) == {"_messagetime", "_sourcecategory", "_sourcehost", "_raw"}
    assert lines[0]["_raw"] == "line1"


def test_search_run_messages_fields_projection(monkeypatch):
    rows = [{"_raw": "line1", "tool_name": "Bash", "unwanted": "nope"}]
    result = messages_result(rows)
    client = make_fake_client(run_search=lambda *a, **k: result)
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "run", "*", "--from", "-1h", "--to", "now",
                                   "--fields", "tool_name"], env=ENV)
    assert out.exit_code == 0, out.output
    line = jsonlib.loads(out.stdout.strip())
    assert line["tool_name"] == "Bash"
    assert "unwanted" not in line


# ---------------------------------------------------------------------------
# --aggregate / --raw / --auto-parsing
# ---------------------------------------------------------------------------

def test_search_run_aggregate_and_raw_both_set_is_error(monkeypatch):
    client = make_fake_client(run_search=lambda *a, **k: records_result([]))
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "run", "*", "--from", "-1h", "--to", "now",
                                   "--aggregate", "--raw"], env=ENV)
    assert out.exit_code == 1
    assert "Cannot pass both --aggregate and --raw" in out.output


def test_search_run_aggregate_sets_requires_raw_messages_false(monkeypatch):
    captured = {}

    def fake_run_search(query, from_time, to_time, **kwargs):
        captured.update(kwargs)
        return records_result([])

    client = make_fake_client(run_search=fake_run_search)
    patch_client(monkeypatch, client)

    runner.invoke(clim.app, ["search", "run", "*", "--from", "-1h", "--to", "now", "--aggregate"], env=ENV)
    assert captured["requires_raw_messages"] is False


def test_search_run_auto_parsing_maps_to_client_casing(monkeypatch):
    captured = {}

    def fake_run_search(query, from_time, to_time, **kwargs):
        captured.update(kwargs)
        return messages_result([])

    client = make_fake_client(run_search=fake_run_search)
    patch_client(monkeypatch, client)

    runner.invoke(clim.app, ["search", "run", "*", "--from", "-1h", "--to", "now",
                             "--auto-parsing", "AUTOPARSE"], env=ENV)
    assert captured["auto_parsing_mode"] == "AutoParse"


def test_search_run_invalid_auto_parsing_is_error(monkeypatch):
    client = make_fake_client(run_search=lambda *a, **k: messages_result([]))
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "run", "*", "--from", "-1h", "--to", "now",
                                   "--auto-parsing", "bogus"], env=ENV)
    assert out.exit_code == 1
    assert "Invalid --auto-parsing" in out.output


# ---------------------------------------------------------------------------
# discover partitions --grep
# ---------------------------------------------------------------------------

def test_discover_partitions_grep_filters_name_and_routing_expression(monkeypatch):
    rows = [
        {"id": "1", "name": "apache", "routingExpression": "_sourceCategory=*/Apache"},
        {"id": "2", "name": "windows", "routingExpression": "_sourceCategory=*/Windows"},
        {"id": "3", "name": "misc", "routingExpression": "_sourceCategory=*/apache-proxy"},
    ]
    client = make_fake_client(list_partitions=lambda **k: rows)
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["discover", "partitions", "--grep", "apache"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "windows" not in out.stdout
    assert "apache" in out.stdout
    assert "misc" in out.stdout  # matches via routingExpression


def test_discover_partitions_no_grep_returns_all(monkeypatch):
    rows = [{"id": "1", "name": "a", "routingExpression": "x"},
            {"id": "2", "name": "b", "routingExpression": "y"}]
    client = make_fake_client(list_partitions=lambda **k: rows)
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["discover", "partitions"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "a" in out.stdout and "b" in out.stdout


# ---------------------------------------------------------------------------
# discover dashboards
# ---------------------------------------------------------------------------

def test_discover_dashboards_grep_filters_title_description_domain(monkeypatch):
    rows = [
        {"id": "1", "title": "Checkout latency", "description": "", "domain": "Custom"},
        {"id": "2", "title": "Billing overview", "description": "checkout funnel", "domain": "Custom"},
        {"id": "3", "title": "Auth errors", "description": "", "domain": "checkout-service"},
        {"id": "4", "title": "Unrelated", "description": "", "domain": "Custom"},
    ]
    client = make_fake_dashboard_client(list_dashboards=lambda **k: rows)
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["discover", "dashboards", "--grep", "checkout"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "Checkout latency" in out.stdout
    assert "Billing overview" in out.stdout
    assert "Auth errors" in out.stdout
    assert "Unrelated" not in out.stdout


def test_discover_dashboards_no_grep_returns_all(monkeypatch):
    rows = [{"id": "1", "title": "a"}, {"id": "2", "title": "b"}]
    client = make_fake_dashboard_client(list_dashboards=lambda **k: rows)
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["discover", "dashboards"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "a" in out.stdout and "b" in out.stdout


def test_discover_dashboards_default_mode_is_all_viewable(monkeypatch):
    captured = {}

    def fake_list_dashboards(**kwargs):
        captured.update(kwargs)
        return []

    client = make_fake_dashboard_client(list_dashboards=fake_list_dashboards)
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["discover", "dashboards"], env=ENV)
    assert out.exit_code == 0, out.output
    assert captured["mode"] == "allViewableByUser"


def test_discover_dashboards_mode_mine_maps_to_created_by_user(monkeypatch):
    captured = {}

    def fake_list_dashboards(**kwargs):
        captured.update(kwargs)
        return []

    client = make_fake_dashboard_client(list_dashboards=fake_list_dashboards)
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["discover", "dashboards", "--mode", "mine"], env=ENV)
    assert out.exit_code == 0, out.output
    assert captured["mode"] == "createdByUser"


def test_discover_dashboards_invalid_mode_exits_1(monkeypatch):
    client = make_fake_dashboard_client(list_dashboards=lambda **k: [])
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["discover", "dashboards", "--mode", "bogus"], env=ENV)
    assert out.exit_code == 1
    assert "Invalid --mode" in out.output


def test_discover_dashboards_limit_caps_filtered_results(monkeypatch):
    rows = [{"id": str(i), "title": f"dash-{i}"} for i in range(5)]
    client = make_fake_dashboard_client(list_dashboards=lambda **k: rows)
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(
        clim.app, ["discover", "dashboards", "--limit", "2", "--format", "json"], env=ENV,
    )
    assert out.exit_code == 0, out.output
    items = jsonlib.loads(out.stdout)
    assert len(items) == 2


def test_discover_dashboards_second_call_uses_cache_not_client(monkeypatch):
    calls = []

    def fake_list_dashboards(**kwargs):
        calls.append(kwargs)
        return [{"id": "1", "title": "a"}]

    client = make_fake_dashboard_client(list_dashboards=fake_list_dashboards)
    patch_dashboard_client(monkeypatch, client)

    out1 = runner.invoke(clim.app, ["discover", "dashboards"], env=ENV)
    assert out1.exit_code == 0, out1.output
    assert len(calls) == 1

    out2 = runner.invoke(clim.app, ["discover", "dashboards"], env=ENV)
    assert out2.exit_code == 0, out2.output
    assert len(calls) == 1  # cache hit — no second API pull
    assert "a" in out2.stdout
    assert "using cached dashboard list" in out2.output


def test_discover_dashboards_no_cache_forces_fresh_pull(monkeypatch):
    calls = []

    def fake_list_dashboards(**kwargs):
        calls.append(kwargs)
        return [{"id": "1", "title": "a"}]

    client = make_fake_dashboard_client(list_dashboards=fake_list_dashboards)
    patch_dashboard_client(monkeypatch, client)

    runner.invoke(clim.app, ["discover", "dashboards"], env=ENV)
    assert len(calls) == 1

    out = runner.invoke(clim.app, ["discover", "dashboards", "--no-cache"], env=ENV)
    assert out.exit_code == 0, out.output
    assert len(calls) == 2


def test_discover_dashboards_mode_all_and_mine_cache_separately(monkeypatch):
    calls = []

    def fake_list_dashboards(**kwargs):
        calls.append(kwargs["mode"])
        return [{"id": "1", "title": kwargs["mode"]}]

    client = make_fake_dashboard_client(list_dashboards=fake_list_dashboards)
    patch_dashboard_client(monkeypatch, client)

    runner.invoke(clim.app, ["discover", "dashboards", "--mode", "all"], env=ENV)
    runner.invoke(clim.app, ["discover", "dashboards", "--mode", "mine"], env=ENV)
    assert calls == ["allViewableByUser", "createdByUser"]

    # both re-invocations hit their own cache, not the client
    runner.invoke(clim.app, ["discover", "dashboards", "--mode", "all"], env=ENV)
    runner.invoke(clim.app, ["discover", "dashboards", "--mode", "mine"], env=ENV)
    assert calls == ["allViewableByUser", "createdByUser"]


def test_discover_dashboards_list_error_exits_1(monkeypatch):
    def raise_error(**k):
        raise sdc.SumoDashboardError("boom")

    client = make_fake_dashboard_client(list_dashboards=raise_error)
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["discover", "dashboards"], env=ENV)
    assert out.exit_code == 1
    assert "Failed to list dashboards" in out.output


# ---------------------------------------------------------------------------
# search count / search estimate
# ---------------------------------------------------------------------------

def test_search_count_prints_scalar(monkeypatch):
    client = make_fake_client(
        run_search=lambda *a, **k: records_result([{"_count": "42"}]),
    )
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "count", "*", "--from", "-1h", "--to", "now"], env=ENV)
    assert out.exit_code == 0, out.output
    assert out.stdout.strip() == "42"


def test_search_estimate_table_output(monkeypatch):
    estimate = ssc.ScanEstimate(
        total_bytes=3500,
        partitions=[{"viewName": "prod_view", "totalDataScannedInBytes": 3500}],
    )
    client = make_fake_client(estimate_scan=lambda *a, **k: estimate)
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "estimate", "*", "--from", "-1h", "--to", "now"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "total_bytes: 3500" in out.stdout
    assert "prod_view" in out.stdout


def test_search_estimate_json_output(monkeypatch):
    estimate = ssc.ScanEstimate(total_bytes=100, partitions=[{"viewName": "v"}])
    client = make_fake_client(estimate_scan=lambda *a, **k: estimate)
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "estimate", "*", "--from", "-1h", "--to", "now",
                                   "--format", "json"], env=ENV)
    assert out.exit_code == 0, out.output
    body = jsonlib.loads(out.stdout)
    assert body["total_bytes"] == 100


# ---------------------------------------------------------------------------
# stderr token-budget warning / --no-warn
# ---------------------------------------------------------------------------

def test_token_warning_fires_on_large_result(monkeypatch):
    rows = [{"_raw": "x" * 20000, "_messagetime": "1", "_sourcecategory": "sc", "_sourcehost": "h"}]
    client = make_fake_client(run_search=lambda *a, **k: messages_result(rows))
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "run", "*", "--from", "-1h", "--to", "now"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "warning: response is ~" in out.stderr
    assert "tokens" in out.stderr


def test_no_warn_suppresses_warning(monkeypatch):
    rows = [{"_raw": "x" * 20000, "_messagetime": "1", "_sourcecategory": "sc", "_sourcehost": "h"}]
    client = make_fake_client(run_search=lambda *a, **k: messages_result(rows))
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "run", "*", "--from", "-1h", "--to", "now",
                                   "--no-warn"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "warning:" not in out.stderr


# ---------------------------------------------------------------------------
# --max-tokens truncation
# ---------------------------------------------------------------------------

def test_max_tokens_drops_whole_trailing_rows(monkeypatch):
    rows = [
        {"_raw": f"line-{i}" + ("x" * 200), "_messagetime": str(i),
         "_sourcecategory": "sc", "_sourcehost": "h"}
        for i in range(20)
    ]
    client = make_fake_client(run_search=lambda *a, **k: messages_result(rows))
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "run", "*", "--from", "-1h", "--to", "now",
                                   "--max-tokens", "200", "--no-warn"], env=ENV)
    assert out.exit_code == 0, out.output
    remaining_lines = out.stdout.strip().splitlines()
    assert len(remaining_lines) < 20
    assert "note: dropped" in out.stderr
    assert "to stay under --max-tokens 200" in out.stderr
    # never truncates mid-record — every remaining line is valid JSON
    for line in remaining_lines:
        jsonlib.loads(line)


# ---------------------------------------------------------------------------
# --drop-null-columns
# ---------------------------------------------------------------------------

def test_drop_null_columns_drops_all_null_column(monkeypatch):
    rows = [
        {"host": "a", "empty_field": "", "count": "1"},
        {"host": "b", "empty_field": None, "count": "2"},
    ]
    client = make_fake_client(run_search=lambda *a, **k: records_result(rows))
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "run", "*", "--from", "-1h", "--to", "now",
                                   "--drop-null-columns"], env=ENV)
    assert out.exit_code == 0, out.output
    header = out.stdout.strip().splitlines()[0]
    assert "empty_field" not in header
    assert "host" in header and "count" in header


# ---------------------------------------------------------------------------
# cli/schema.py — pure functions
# ---------------------------------------------------------------------------

def test_try_parse_raw_valid_json_object():
    assert schema_mod.try_parse_raw('{"a": 1}') == {"a": 1}


def test_try_parse_raw_json_array_returns_none():
    assert schema_mod.try_parse_raw("[1, 2, 3]") is None


def test_try_parse_raw_non_json_text_returns_none():
    assert schema_mod.try_parse_raw("2024-01-01 INFO started") is None


def test_try_parse_raw_malformed_json_returns_none():
    assert schema_mod.try_parse_raw('{"a": 1') is None


def test_try_parse_raw_non_string_returns_none():
    assert schema_mod.try_parse_raw(None) is None


def test_flatten_one_level_dot_notation():
    flat = schema_mod.flatten_one_level({"eventname": "AssumeRole", "os": {"type": "darwin"}})
    assert flat == {"eventname": "AssumeRole", "os.type": "darwin"}


def test_flatten_one_level_does_not_recurse_past_one_level():
    flat = schema_mod.flatten_one_level({"os": {"type": "darwin", "detail": {"arch": "arm64"}}})
    assert flat["os.detail"] == {"arch": "arm64"}


def test_infer_type_bool_before_int():
    assert schema_mod.infer_type(True) == "boolean"
    assert schema_mod.infer_type(1) == "number"
    assert schema_mod.infer_type(1.5) == "number"
    assert schema_mod.infer_type("x") == "string"
    assert schema_mod.infer_type([1]) == "array"
    assert schema_mod.infer_type({"a": 1}) == "object"


def test_profile_sample_const_and_index_time_and_present_fraction():
    rows = [
        {"_messagetime": "1", "_sourcecategory": "sc", "sourceipaddress": "1.2.3.4",
         "_raw": jsonlib.dumps({"eventname": "AssumeRole", "os": {"type": "darwin"}})},
        {"_messagetime": "2", "_sourcecategory": "sc",
         "_raw": jsonlib.dumps({"eventname": "PutObject", "os": {"type": "darwin"}})},
    ]
    items = [{"map": r} for r in rows]
    report = schema_mod.profile_sample(items)
    by_field = {f["FIELD"]: f for f in report.fields}

    # index-time-only field, present in every row
    assert by_field["_sourcecategory"]["INDEX-TIME"] == "yes"
    assert by_field["_sourcecategory"]["CONST"] == "YES"

    # search-time field discovered only via parsed _raw
    assert by_field["os.type"]["INDEX-TIME"] == "no"
    assert by_field["os.type"]["CONST"] == "YES"
    assert by_field["os.type"]["PRESENT"] == "2/2"

    # non-constant search-time field
    assert by_field["eventname"]["CONST"] == "no"
    assert by_field["eventname"]["INDEX-TIME"] == "no"

    # present in only one of two rows
    assert by_field["sourceipaddress"]["PRESENT"] == "1/2"


def test_profile_sample_unstructured_raw_marks_type_and_hints():
    rows = [
        {"_messagetime": str(i), "_sourcecategory": "sc", "_sourcehost": "h",
         "_raw": f"2024-01-01 12:00:0{i} INFO service started ok"}
        for i in range(5)
    ]
    items = [{"map": r} for r in rows]
    report = schema_mod.profile_sample(items)
    by_field = {f["FIELD"]: f for f in report.fields}

    assert by_field["_raw"]["TYPE"] == "unstructured-text"
    assert report.hint is not None
    assert "parse regex" in report.hint
    assert "5/5" in report.hint


def test_profile_sample_no_hint_when_token_counts_inconsistent():
    texts = ["a b c", "a b c d e f g", "x", "one two three four five six seven eight nine"]
    rows = [
        {"_messagetime": str(i), "_sourcecategory": "sc", "_sourcehost": "h", "_raw": t}
        for i, t in enumerate(texts)
    ]
    items = [{"map": r} for r in rows]
    report = schema_mod.profile_sample(items)
    assert report.hint is None


# ---------------------------------------------------------------------------
# sumosearch schema (CLI)
# ---------------------------------------------------------------------------

def test_schema_json_raw_table_columns(monkeypatch):
    rows = [
        {"_messagetime": "1", "_sourcecategory": "sc", "sourceipaddress": "1.2.3.4",
         "_raw": jsonlib.dumps({"eventname": "AssumeRole", "os": {"type": "darwin"}})},
        {"_messagetime": "2", "_sourcecategory": "sc",
         "_raw": jsonlib.dumps({"eventname": "PutObject", "os": {"type": "darwin"}})},
    ]
    client = make_fake_client(run_search=lambda *a, **k: messages_result(rows))
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["schema", "_sourcecategory=*cloudtrail*",
                                   "--from", "-1h", "--to", "now"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "FIELD" in out.stdout and "PRESENT" in out.stdout and "INDEX-TIME" in out.stdout
    assert "os.type" in out.stdout
    assert "eventname" in out.stdout
    assert "darwin" in out.stdout  # EXAMPLE column


def test_schema_appends_limit_to_query(monkeypatch):
    captured = {}

    def fake_run_search(query, from_time, to_time, **kwargs):
        captured["query"] = query
        captured.update(kwargs)
        return messages_result([])

    client = make_fake_client(run_search=fake_run_search)
    patch_client(monkeypatch, client)

    runner.invoke(clim.app, ["schema", "_sourcecategory=*x*", "--from", "-1h", "--to", "now",
                             "--n", "10"], env=ENV)
    assert captured["query"] == "_sourcecategory=*x* | limit 10"


def test_schema_default_auto_parsing_is_manual(monkeypatch):
    captured = {}

    def fake_run_search(query, from_time, to_time, **kwargs):
        captured.update(kwargs)
        return messages_result([])

    client = make_fake_client(run_search=fake_run_search)
    patch_client(monkeypatch, client)

    runner.invoke(clim.app, ["schema", "*", "--from", "-1h", "--to", "now"], env=ENV)
    assert captured["auto_parsing_mode"] == "Manual"


def test_schema_auto_parsing_override(monkeypatch):
    captured = {}

    def fake_run_search(query, from_time, to_time, **kwargs):
        captured.update(kwargs)
        return messages_result([])

    client = make_fake_client(run_search=fake_run_search)
    patch_client(monkeypatch, client)

    runner.invoke(clim.app, ["schema", "*", "--from", "-1h", "--to", "now",
                             "--auto-parsing", "autoparse"], env=ENV)
    assert captured["auto_parsing_mode"] == "AutoParse"


def test_schema_invalid_auto_parsing_is_error(monkeypatch):
    client = make_fake_client(run_search=lambda *a, **k: messages_result([]))
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["schema", "*", "--from", "-1h", "--to", "now",
                                   "--auto-parsing", "bogus"], env=ENV)
    assert out.exit_code == 1
    assert "Invalid --auto-parsing" in out.output


def test_schema_non_json_raw_fallback_and_hint(monkeypatch):
    rows = [
        {"_messagetime": str(i), "_sourcecategory": "otel/mac", "_sourcehost": "h",
         "_raw": f"2024-01-01 12:00:0{i} INFO service started ok"}
        for i in range(5)
    ]
    client = make_fake_client(run_search=lambda *a, **k: messages_result(rows))
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["schema", '_sourceCategory="otel/mac"',
                                   "--from", "-24h", "--to", "now"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "unstructured-text" in out.stdout
    assert "parse regex" in out.stdout


# ---------------------------------------------------------------------------
# sumosearch sample (CLI)
# ---------------------------------------------------------------------------

def test_sample_messages_default_ndjson(monkeypatch):
    rows = [
        {"_messagetime": "1", "_sourcecategory": "sc", "_sourcehost": "h", "_raw": "line1", "extra": "x"},
    ]
    client = make_fake_client(run_search=lambda *a, **k: messages_result(rows))
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["sample", "*", "--from", "-1h", "--to", "now"], env=ENV)
    assert out.exit_code == 0, out.output
    line = jsonlib.loads(out.stdout.strip())
    assert set(line) == {"_messagetime", "_sourcecategory", "_sourcehost", "_raw"}
    assert line["_raw"] == "line1"


def test_sample_records_default_csv(monkeypatch):
    rows = [{"_count": "3", "host": "a"}]
    client = make_fake_client(run_search=lambda *a, **k: records_result(rows))
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["sample", "* | count by host", "--from", "-1h", "--to", "now"],
                        env=ENV)
    assert out.exit_code == 0, out.output
    lines = out.stdout.strip().splitlines()
    assert lines[0] == "_count,host"
    assert "3,a" in lines[1]


def test_sample_appends_limit_to_query(monkeypatch):
    captured = {}

    def fake_run_search(query, from_time, to_time, **kwargs):
        captured["query"] = query
        return records_result([])

    client = make_fake_client(run_search=fake_run_search)
    patch_client(monkeypatch, client)

    runner.invoke(clim.app, ["sample", "*", "--from", "-1h", "--to", "now", "--n", "7"], env=ENV)
    assert captured["query"] == "* | limit 7"


def test_sample_drop_null_columns(monkeypatch):
    rows = [
        {"host": "a", "empty_field": "", "count": "1"},
        {"host": "b", "empty_field": None, "count": "2"},
    ]
    client = make_fake_client(run_search=lambda *a, **k: records_result(rows))
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["sample", "*", "--from", "-1h", "--to", "now",
                                   "--drop-null-columns"], env=ENV)
    assert out.exit_code == 0, out.output
    header = out.stdout.strip().splitlines()[0]
    assert "empty_field" not in header
    assert "host" in header and "count" in header


# ---------------------------------------------------------------------------
# missing-credentials / SumoSearchError -> clean exit 1 (Phase 0 regression)
# ---------------------------------------------------------------------------

def test_missing_credentials_exit_1():
    # Explicitly unset (not just omit) — the real shell environment running
    # this test suite may itself export SUMO_ACCESS_ID/KEY/ENDPOINT (e.g. for
    # the live integration test), and CliRunner's `env=` only overrides keys
    # it's given, it doesn't clear unlisted ones. A None value here tells
    # typer's CliRunner to delete the key from os.environ for the duration
    # of the call, guaranteeing this test actually exercises the
    # missing-credentials path instead of silently hitting the real API.
    no_creds = {"SUMO_ACCESS_ID": None, "SUMO_ACCESS_KEY": None, "SUMO_ENDPOINT": None}
    out = runner.invoke(clim.app, ["search", "run", "*", "--from", "-1h", "--to", "now"], env=no_creds)
    assert out.exit_code == 1
    assert "Missing required credentials" in out.output


def test_sumo_search_error_clean_exit_1(monkeypatch):
    def raise_error(*a, **k):
        raise ssc.SumoSearchError("boom", status_code=400)

    client = make_fake_client(run_search=raise_error)
    patch_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "run", "*", "--from", "-1h", "--to", "now"], env=ENV)
    assert out.exit_code == 1
    assert "Search failed" in out.output


# ---------------------------------------------------------------------------
# sumosearch export
# ---------------------------------------------------------------------------

def _count_run_search(count: int, data_result: ssc.SearchJobResult):
    """Fake `run_search` that answers estimate_count()'s `<query> | count`
    sub-call with `count`, and the actual export query with `data_result`."""
    def fake(query, from_time, to_time, **kwargs):
        if query.endswith("| count"):
            return records_result([{"_count": str(count)}])
        return data_result
    return fake


def test_export_rejects_table_format(monkeypatch, tmp_path):
    client = make_fake_client(run_search=lambda *a, **k: records_result([{"_count": "1"}]))
    patch_client(monkeypatch, client)

    out_file = tmp_path / "x.csv"
    out = runner.invoke(clim.app, ["export", "*", "--from", "-1h", "--to", "now",
                                   "--format", "table", "--out", str(out_file)], env=ENV)
    assert out.exit_code == 1
    assert "Unsupported --format" in out.output


def test_export_estimate_count_error_clean_exit_1(monkeypatch, tmp_path):
    def raise_error(*a, **k):
        raise ssc.SumoSearchError("boom", status_code=400)

    client = make_fake_client(run_search=raise_error)
    patch_client(monkeypatch, client)

    out_file = tmp_path / "x.csv"
    out = runner.invoke(clim.app, ["export", "*", "--from", "-1h", "--to", "now",
                                   "--format", "csv", "--out", str(out_file)], env=ENV)
    assert out.exit_code == 1
    assert "Export failed" in out.output


def test_export_single_shot_csv_writes_full_map(monkeypatch, tmp_path):
    """Small estimated count -> a single run_search() call over the whole
    window, and every row is the FULL map (not the 4-field messages
    envelope `search run`/`sample` use) — `custom_field` is not in
    MESSAGE_ENVELOPE_FIELDS, so its presence proves no trimming happened."""
    rows = [
        {"_messagetime": "1", "_sourcecategory": "sc", "_sourcehost": "h",
         "_raw": "line1", "custom_field": "keepme"},
    ]
    captured = {}

    def fake_run_search(query, from_time, to_time, **kwargs):
        if query.endswith("| count"):
            return records_result([{"_count": "5"}])
        captured["query"] = query
        captured["kwargs"] = kwargs
        return messages_result(rows)

    client = make_fake_client(run_search=fake_run_search)
    patch_client(monkeypatch, client)

    out_file = tmp_path / "export.csv"
    out = runner.invoke(clim.app, ["export", "*", "--from", "-1h", "--to", "now",
                                   "--format", "csv", "--out", str(out_file)], env=ENV)
    assert out.exit_code == 0, out.output
    assert captured["query"] == "*"
    assert captured["kwargs"]["limit"] is None

    content = out_file.read_text()
    header = content.splitlines()[0]
    assert "custom_field" in header
    assert "keepme" in content

    assert "wrote 1 rows" in out.stdout
    assert "single job, no time-splitting needed" in out.stdout
    # never printed to stdout
    assert "keepme" not in out.stdout


def test_export_single_shot_ndjson_full_map(monkeypatch, tmp_path):
    rows = [{"_raw": "line1", "custom_field": "keepme"}]
    client = make_fake_client(run_search=_count_run_search(5, messages_result(rows)))
    patch_client(monkeypatch, client)

    out_file = tmp_path / "export.ndjson"
    out = runner.invoke(clim.app, ["export", "*", "--from", "-1h", "--to", "now",
                                   "--format", "ndjson", "--out", str(out_file)], env=ENV)
    assert out.exit_code == 0, out.output
    line = jsonlib.loads(out_file.read_text().strip())
    assert line == {"_raw": "line1", "custom_field": "keepme"}


def test_export_single_shot_json_full_map(monkeypatch, tmp_path):
    rows = [{"_raw": "line1", "custom_field": "keepme"}]
    client = make_fake_client(run_search=_count_run_search(5, messages_result(rows)))
    patch_client(monkeypatch, client)

    out_file = tmp_path / "export.json"
    out = runner.invoke(clim.app, ["export", "*", "--from", "-1h", "--to", "now",
                                   "--format", "json", "--out", str(out_file)], env=ENV)
    assert out.exit_code == 0, out.output
    body = jsonlib.loads(out_file.read_text())
    assert body == rows


def test_export_interval_hours_override_skips_auto_sizing(monkeypatch, tmp_path):
    """When --interval-hours is passed explicitly, it must reach
    time_split_search() unchanged — the auto-sizing formula (which would
    compute something else entirely for this count/window combo) must be
    skipped, not merely overridden after the fact."""
    captured = {}

    def fake_time_split_search(client, query, from_ms, to_ms, *, interval_hours,
                               time_zone="UTC", result_type="messages"):
        captured["interval_hours"] = interval_hours
        return [{"map": {"_raw": "x"}}]

    monkeypatch.setattr(clim, "time_split_search", fake_time_split_search)

    client = make_fake_client(run_search=lambda *a, **k: records_result([{"_count": "999999"}]))
    patch_client(monkeypatch, client)

    out_file = tmp_path / "export.ndjson"
    out = runner.invoke(clim.app, ["export", "*", "--from", "0", "--to", "7200000",
                                   "--format", "ndjson", "--out", str(out_file),
                                   "--interval-hours", "0.5"], env=ENV)
    assert out.exit_code == 0, out.output
    assert captured["interval_hours"] == 0.5


def test_export_time_split_value_error_is_clean_cli_error(monkeypatch, tmp_path):
    def fake_time_split_search(*a, **k):
        raise ValueError("window 0-3600000 hit the 100000 message cap")

    monkeypatch.setattr(clim, "time_split_search", fake_time_split_search)

    client = make_fake_client(run_search=lambda *a, **k: records_result([{"_count": "999999"}]))
    patch_client(monkeypatch, client)

    out_file = tmp_path / "export.csv"
    out = runner.invoke(clim.app, ["export", "*", "--from", "-2h", "--to", "now",
                                   "--format", "csv", "--out", str(out_file)], env=ENV)
    assert out.exit_code == 1
    assert "Export failed" in out.output
    assert "smaller explicit --interval-hours" in out.output
    assert not out_file.exists()


# -- time-split path exercised at the real HTTP level --------------------
# Unlike the tests above (which monkeypatch run_search/time_split_search
# directly), this one builds a real SumoSearchClient over a queued-response
# fake session (same pattern as tests/test_sumo_search_client.py's
# FakeSession) so create/poll/fetch/delete actually run for each window,
# and multiple time windows are verified via the session's own call log.

class QueuedFakeSession:
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


def _job_cycle_responses(job_id: str, *, record_count: int, message_count: int,
                         page_body: dict) -> list[requests.Response]:
    """The 3 non-delete responses for one create -> poll -> fetch cycle,
    plus a delete response — matches exactly what run_search() issues when
    the job reaches DONE on the first status poll."""
    return [
        make_response(200, {"id": job_id}),
        make_response(200, {
            "state": "DONE GATHERING RESULTS", "recordCount": record_count,
            "messageCount": message_count, "pendingErrors": [], "pendingWarnings": [],
        }),
        make_response(200, page_body),
        make_response(200),  # delete
    ]


def test_export_time_split_path_spans_multiple_windows(monkeypatch, tmp_path):
    responses = [
        # estimate_count()'s "<query> | count" job
        *_job_cycle_responses("job-count", record_count=1, message_count=0,
                              page_body={"records": [{"map": {"_count": "100000"}}]}),
        # window 1: 0 - 3_600_000
        *_job_cycle_responses("job-w1", record_count=0, message_count=2,
                              page_body={"messages": [
                                  {"map": {"_raw": "a", "custom_field": "w1"}},
                                  {"map": {"_raw": "b", "custom_field": "w1"}},
                              ]}),
        # window 2: 3_600_000 - 7_200_000
        *_job_cycle_responses("job-w2", record_count=0, message_count=1,
                              page_body={"messages": [{"map": {"_raw": "c", "custom_field": "w2"}}]}),
    ]
    session = QueuedFakeSession(responses)
    client = ssc.SumoSearchClient("test-id", "test-key", "https://api.example.com",
                                  session=session, min_interval=0.0)
    patch_client(monkeypatch, client)

    out_file = tmp_path / "export.ndjson"
    # Exactly 2 hours (0 to 7_200_000 ms), --interval-hours 1 -> exactly 2 windows.
    out = runner.invoke(clim.app, ["export", "*", "--from", "0", "--to", "7200000",
                                   "--format", "ndjson", "--out", str(out_file),
                                   "--interval-hours", "1"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "wrote 3 rows" in out.stdout
    assert "2 time windows" in out.stdout

    lines = [jsonlib.loads(line) for line in out_file.read_text().strip().splitlines()]
    assert len(lines) == 3
    assert {"_raw": "a", "custom_field": "w1"} in lines
    assert {"_raw": "c", "custom_field": "w2"} in lines

    create_calls = [c for c in session.calls if c["method"] == "post" and c["url"].endswith("/search/jobs")]
    assert len(create_calls) == 3  # 1 count job + 2 data-window jobs


# ---------------------------------------------------------------------------
# instance / context
# ---------------------------------------------------------------------------

def test_instance_add_list_show():
    out = runner.invoke(clim.app, ["instance", "add", "demo", "--endpoint", "us2",
                                    "--description", "demo org"])
    assert out.exit_code == 0, out.output
    assert "demo" in out.output and "https://api.us2.sumologic.com" in out.output

    out = runner.invoke(clim.app, ["instance", "list"])
    assert out.exit_code == 0, out.output
    assert "demo" in out.output
    assert "https://api.us2.sumologic.com" in out.output

    out = runner.invoke(clim.app, ["instance", "show", "demo"])
    assert out.exit_code == 0, out.output
    assert "endpoint: https://api.us2.sumologic.com" in out.output
    assert "description: demo org" in out.output


def test_instance_add_rejects_unrecognized_endpoint():
    out = runner.invoke(clim.app, ["instance", "add", "demo", "--endpoint", "bogus"])
    assert out.exit_code == 1
    assert "Unrecognized endpoint" in out.output


def test_instance_add_overwrites_existing():
    runner.invoke(clim.app, ["instance", "add", "demo", "--endpoint", "us2"])
    out = runner.invoke(clim.app, ["instance", "add", "demo", "--endpoint", "au"])
    assert out.exit_code == 0, out.output

    out = runner.invoke(clim.app, ["instance", "show", "demo"])
    assert "https://api.au.sumologic.com" in out.output


def test_instance_remove():
    runner.invoke(clim.app, ["instance", "add", "demo", "--endpoint", "us2"])
    out = runner.invoke(clim.app, ["instance", "remove", "demo"])
    assert out.exit_code == 0, out.output

    out = runner.invoke(clim.app, ["instance", "show", "demo"])
    assert out.exit_code == 1


def test_instance_remove_unknown_is_error():
    out = runner.invoke(clim.app, ["instance", "remove", "nope"])
    assert out.exit_code == 1
    assert "not found" in out.output


def test_instance_remove_clears_matching_context():
    runner.invoke(clim.app, ["instance", "add", "demo", "--endpoint", "us2"])
    runner.invoke(clim.app, ["context", "set", "demo"])
    runner.invoke(clim.app, ["instance", "remove", "demo"])

    out = runner.invoke(clim.app, ["context", "show"])
    assert "no context set" in out.output


def test_context_set_show_unset():
    runner.invoke(clim.app, ["instance", "add", "demo", "--endpoint", "us2"])

    out = runner.invoke(clim.app, ["context", "set", "demo"])
    assert out.exit_code == 0, out.output

    out = runner.invoke(clim.app, ["context", "show"])
    assert out.exit_code == 0, out.output
    assert "demo" in out.output and "https://api.us2.sumologic.com" in out.output

    out = runner.invoke(clim.app, ["context", "unset"])
    assert out.exit_code == 0, out.output

    out = runner.invoke(clim.app, ["context", "show"])
    assert "no context set" in out.output


def test_context_set_unknown_instance_is_error():
    out = runner.invoke(clim.app, ["context", "set", "nope"])
    assert out.exit_code == 1
    assert "not found" in out.output


def test_context_set_none_clears():
    runner.invoke(clim.app, ["instance", "add", "demo", "--endpoint", "us2"])
    runner.invoke(clim.app, ["context", "set", "demo"])

    out = runner.invoke(clim.app, ["context", "set", "none"])
    assert out.exit_code == 0, out.output

    out = runner.invoke(clim.app, ["context", "show"])
    assert "no context set" in out.output


def test_instance_flag_interpolates_credentials_and_endpoint(monkeypatch):
    runner.invoke(clim.app, ["instance", "add", "demo", "--endpoint", "us2"])
    client = make_fake_client(run_search=lambda *a, **k: records_result([{"_count": "1"}]))
    captured = patch_client_capture(monkeypatch, client)

    env = {"SUMO_ACCESS_ID_DEMO": "demo-id", "SUMO_ACCESS_KEY_DEMO": "demo-key"}
    out = runner.invoke(clim.app, ["--instance", "demo", "search", "count", "*",
                                    "--from", "-1h", "--to", "now"], env=env)
    assert out.exit_code == 0, out.output
    assert captured["args"] == ("demo-id", "demo-key", "https://api.us2.sumologic.com")


def test_instance_flag_env_var_name_uses_uppercased_suffix(monkeypatch):
    runner.invoke(clim.app, ["instance", "add", "us2-prod", "--endpoint", "us2"])
    client = make_fake_client(run_search=lambda *a, **k: records_result([{"_count": "1"}]))
    captured = patch_client_capture(monkeypatch, client)

    env = {"SUMO_ACCESS_ID_US2_PROD": "prod-id", "SUMO_ACCESS_KEY_US2_PROD": "prod-key"}
    out = runner.invoke(clim.app, ["--instance", "us2-prod", "search", "count", "*",
                                    "--from", "-1h", "--to", "now"], env=env)
    assert out.exit_code == 0, out.output
    assert captured["args"] == ("prod-id", "prod-key", "https://api.us2.sumologic.com")


def test_instance_flag_undefined_instance_is_error():
    out = runner.invoke(clim.app, ["--instance", "nope", "search", "count", "*",
                                    "--from", "-1h", "--to", "now"], env={})
    assert out.exit_code == 1
    assert "is not defined" in out.output


def test_explicit_flags_override_instance(monkeypatch):
    runner.invoke(clim.app, ["instance", "add", "demo", "--endpoint", "us2"])
    client = make_fake_client(run_search=lambda *a, **k: records_result([{"_count": "1"}]))
    captured = patch_client_capture(monkeypatch, client)

    env = {"SUMO_ACCESS_ID_DEMO": "demo-id", "SUMO_ACCESS_KEY_DEMO": "demo-key"}
    out = runner.invoke(clim.app, [
        "--instance", "demo", "--access-id", "explicit-id", "--access-key", "explicit-key",
        "--endpoint", "eu", "search", "count", "*", "--from", "-1h", "--to", "now",
    ], env=env)
    assert out.exit_code == 0, out.output
    assert captured["args"] == ("explicit-id", "explicit-key", "https://api.eu.sumologic.com")


def test_persisted_context_used_without_instance_flag(monkeypatch):
    runner.invoke(clim.app, ["instance", "add", "demo", "--endpoint", "au"])
    runner.invoke(clim.app, ["context", "set", "demo"])
    client = make_fake_client(run_search=lambda *a, **k: records_result([{"_count": "1"}]))
    captured = patch_client_capture(monkeypatch, client)

    env = {"SUMO_ACCESS_ID_DEMO": "demo-id", "SUMO_ACCESS_KEY_DEMO": "demo-key"}
    out = runner.invoke(clim.app, ["search", "count", "*", "--from", "-1h", "--to", "now"], env=env)
    assert out.exit_code == 0, out.output
    assert captured["args"] == ("demo-id", "demo-key", "https://api.au.sumologic.com")


def test_default_env_vars_used_when_no_instance_active(monkeypatch):
    client = make_fake_client(run_search=lambda *a, **k: records_result([{"_count": "1"}]))
    captured = patch_client_capture(monkeypatch, client)

    out = runner.invoke(clim.app, ["search", "count", "*", "--from", "-1h", "--to", "now"], env=ENV)
    assert out.exit_code == 0, out.output
    assert captured["args"] == ("test-id", "test-key", "https://api.example.com")


def test_endpoint_flag_accepts_region_alias_case_insensitive(monkeypatch):
    client = make_fake_client(run_search=lambda *a, **k: records_result([{"_count": "1"}]))
    captured = patch_client_capture(monkeypatch, client)

    env = {"SUMO_ACCESS_ID": "test-id", "SUMO_ACCESS_KEY": "test-key"}
    out = runner.invoke(clim.app, ["--endpoint", "AU", "search", "count", "*",
                                    "--from", "-1h", "--to", "now"], env=env)
    assert out.exit_code == 0, out.output
    assert captured["args"] == ("test-id", "test-key", "https://api.au.sumologic.com")


def test_missing_credentials_error_mentions_instance_env_vars():
    runner.invoke(clim.app, ["instance", "add", "demo", "--endpoint", "us2"])
    # Explicitly unset — see test_missing_credentials_exit_1 for why: the
    # shell running this suite may itself export SUMO_ACCESS_ID_DEMO/
    # SUMO_ACCESS_KEY_DEMO (e.g. for live integration testing).
    no_creds = {"SUMO_ACCESS_ID_DEMO": None, "SUMO_ACCESS_KEY_DEMO": None}
    out = runner.invoke(clim.app, ["--instance", "demo", "search", "count", "*",
                                    "--from", "-1h", "--to", "now"], env=no_creds)
    assert out.exit_code == 1
    assert "SUMO_ACCESS_ID_DEMO" in out.output
    assert "SUMO_ACCESS_KEY_DEMO" in out.output


# ---------------------------------------------------------------------------
# report describe / status
# ---------------------------------------------------------------------------

def test_report_describe_default_is_summary_level(monkeypatch):
    dashboard = {"id": "dash-1", "title": "My Dashboard", "variables": [], "panels": [
        {"id": "p1", "key": "p1", "panelType": "SumoSearchPanel", "queries": []},
    ]}
    client = make_fake_dashboard_client(get_dashboard=lambda dashboard_id: dashboard)
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["report", "describe", "dash-1"], env=ENV)
    assert out.exit_code == 0, out.output
    payload = jsonlib.loads(out.output)
    assert payload["id"] == "dash-1"
    assert "panels" not in payload  # summary level omits the per-panel list


def test_report_describe_panels_flag_includes_panel_list(monkeypatch):
    dashboard = {"id": "dash-1", "title": "My Dashboard", "variables": [], "panels": [
        {"id": "p1", "key": "p1", "panelType": "SumoSearchPanel", "queries": []},
    ]}
    client = make_fake_dashboard_client(get_dashboard=lambda dashboard_id: dashboard)
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["report", "describe", "dash-1", "--panels"], env=ENV)
    assert out.exit_code == 0, out.output
    payload = jsonlib.loads(out.output)
    assert [p["key"] for p in payload["panels"]] == ["p1"]
    assert "queries" not in payload["panels"][0]  # panels level, not queries level


def test_report_describe_not_found_exits_1(monkeypatch):
    def raise_not_found(dashboard_id):
        raise sdc.SumoDashboardError("get dashboard dash-1 failed: HTTP 404 — not found",
                                     status_code=404)

    client = make_fake_dashboard_client(get_dashboard=raise_not_found)
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["report", "describe", "dash-1"], env=ENV)
    assert out.exit_code == 1
    assert "Report describe failed" in out.output


def test_report_status_prints_json(monkeypatch):
    client = make_fake_dashboard_client(
        get_report_status=lambda job_id: {"status": "InProgress", "id": job_id},
    )
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["report", "status", "job-1"], env=ENV)
    assert out.exit_code == 0, out.output
    assert jsonlib.loads(out.output) == {"status": "InProgress", "id": "job-1"}


# ---------------------------------------------------------------------------
# report run
# ---------------------------------------------------------------------------

def test_report_run_dry_run_creates_no_job(monkeypatch):
    created = []
    client = make_fake_dashboard_client(create_report_job=lambda body: created.append(body) or "job-1")
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["report", "run", "dash-1", "--dry-run", "--hours", "1"], env=ENV)
    assert out.exit_code == 0, out.output
    assert created == []  # no job actually created
    body = jsonlib.loads(out.output)
    assert body["template"]["id"] == "dash-1"
    assert "timeRange" in body["template"]


def test_report_run_merges_default_variable_values(monkeypatch, tmp_path):
    dashboard = {
        "id": "dash-1", "title": "My Dashboard", "panels": [],
        "variables": [
            {"name": "region", "defaultValue": "us-east-1"},
            {"name": "override_me", "defaultValue": "should-not-appear"},
        ],
    }
    captured = {}

    def capture_body(body):
        captured["body"] = body
        return "job-1"

    client = make_fake_dashboard_client(
        get_dashboard=lambda dashboard_id: dashboard,
        create_report_job=capture_body,
    )
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, [
        "report", "run", "dash-1", "--hours", "1",
        "--variable", "override_me=explicit-value",
        "--out", str(tmp_path / "out.pdf"),
    ], env=ENV)
    assert out.exit_code == 0, out.output
    variables = captured["body"]["template"]["variableValues"]["data"]
    assert variables["region"] == ["us-east-1"]          # dashboard default, unmerged
    assert variables["override_me"] == ["explicit-value"]  # explicit --variable wins


def test_report_run_writes_file_and_prints_summary(monkeypatch, tmp_path):
    client = make_fake_dashboard_client(get_report_result=lambda job_id: report_result(b"pdf-bytes"))
    patch_dashboard_client(monkeypatch, client)

    out_file = tmp_path / "out.pdf"
    out = runner.invoke(clim.app, ["report", "run", "dash-1", "--hours", "1",
                                   "--out", str(out_file)], env=ENV)
    assert out.exit_code == 0, out.output
    assert out_file.read_bytes() == b"pdf-bytes"
    assert "Report saved:" in out.output
    assert "9 bytes" in out.output


def test_report_run_no_out_uses_managed_output_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(instances_mod, "CONFIG_DIR", tmp_path / "sumo-search")
    dashboard = {"id": "dash-1", "title": "My Cool Dashboard!", "variables": [], "panels": []}
    client = make_fake_dashboard_client(
        get_dashboard=lambda dashboard_id: dashboard,
        get_report_result=lambda job_id: report_result(b"pdf-bytes"),
    )
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["report", "run", "dash-1", "--hours", "1"], env=ENV)
    assert out.exit_code == 0, out.output

    files = list((tmp_path / "sumo-search" / "output" / "default" / "report").glob("*.pdf"))
    assert len(files) == 1
    assert "my-cool-dashboard" in files[0].name


def test_report_run_export_width_out_of_range_exits_1(monkeypatch, tmp_path):
    client = make_fake_dashboard_client()
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["report", "run", "dash-1", "--export-width", "100",
                                   "--out", str(tmp_path / "out.pdf")], env=ENV)
    assert out.exit_code == 1
    assert "export-width" in out.output


def test_report_run_panel_override_requires_preflight(monkeypatch, tmp_path):
    client = make_fake_dashboard_client()
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, [
        "report", "run", "dash-1", "--no-preflight",
        "--panel-override", "p1=collapsed", "--out", str(tmp_path / "out.pdf"),
    ], env=ENV)
    assert out.exit_code == 1
    assert "--panel-override requires preflight" in out.output


def test_report_run_unknown_variable_rejected(monkeypatch, tmp_path):
    dashboard = {"id": "dash-1", "title": "My Dashboard", "variables": [{"name": "region"}], "panels": []}
    client = make_fake_dashboard_client(get_dashboard=lambda dashboard_id: dashboard)
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, [
        "report", "run", "dash-1", "--variable", "not_a_real_variable=x",
        "--out", str(tmp_path / "out.pdf"),
    ], env=ENV)
    assert out.exit_code == 1
    assert "Unknown dashboard variable" in out.output


def test_report_run_job_failed_exits_1(monkeypatch, tmp_path):
    client = make_fake_dashboard_client(
        get_report_status=lambda job_id: {"status": "Failed", "error": "boom"},
    )
    patch_dashboard_client(monkeypatch, client)

    out = runner.invoke(clim.app, ["report", "run", "dash-1", "--hours", "1",
                                   "--out", str(tmp_path / "out.pdf")], env=ENV)
    assert out.exit_code == 1
    assert "boom" in out.output


# ---------------------------------------------------------------------------
# report result
# ---------------------------------------------------------------------------

def test_report_result_writes_file(monkeypatch, tmp_path):
    client = make_fake_dashboard_client(get_report_result=lambda job_id: report_result(b"result-bytes"))
    patch_dashboard_client(monkeypatch, client)

    out_file = tmp_path / "result.pdf"
    out = runner.invoke(clim.app, ["report", "result", "job-1", "--out", str(out_file)], env=ENV)
    assert out.exit_code == 0, out.output
    assert out_file.read_bytes() == b"result-bytes"


# ---------------------------------------------------------------------------
# report list / open / cleanup — exercise cli/report_paths.py through the CLI
# ---------------------------------------------------------------------------

def _write_fake_report(tmp_path, instance: str, filename: str, age_days: float = 0) -> None:
    import os
    import time as time_mod

    report_dir = tmp_path / "sumo-search" / "output" / instance / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / filename
    path.write_bytes(b"fake-pdf-bytes")
    if age_days:
        mtime = time_mod.time() - age_days * 86400
        os.utime(path, (mtime, mtime))


def test_report_list_shows_files_across_instances(monkeypatch, tmp_path):
    monkeypatch.setattr(instances_mod, "CONFIG_DIR", tmp_path / "sumo-search")
    _write_fake_report(tmp_path, "default", "20260101_000000_dash-a.pdf")
    _write_fake_report(tmp_path, "demo", "20260102_000000_dash-b.png")

    out = runner.invoke(clim.app, ["report", "list", "--format", "csv"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "20260101_000000_dash-a.pdf" in out.output
    assert "20260102_000000_dash-b.png" in out.output
    assert "demo" in out.output


def test_report_list_empty_shows_header_only(monkeypatch, tmp_path):
    monkeypatch.setattr(instances_mod, "CONFIG_DIR", tmp_path / "sumo-search")
    out = runner.invoke(clim.app, ["report", "list", "--format", "csv"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "file" in out.output  # header row only


def test_report_open_no_open_prints_most_recent(monkeypatch, tmp_path):
    monkeypatch.setattr(instances_mod, "CONFIG_DIR", tmp_path / "sumo-search")
    _write_fake_report(tmp_path, "default", "older.pdf", age_days=2)
    _write_fake_report(tmp_path, "default", "newer.pdf", age_days=0)

    out = runner.invoke(clim.app, ["report", "open", "--no-open"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "newer.pdf" in out.output


def test_report_open_no_files_exits_1(monkeypatch, tmp_path):
    monkeypatch.setattr(instances_mod, "CONFIG_DIR", tmp_path / "sumo-search")
    out = runner.invoke(clim.app, ["report", "open", "--no-open"], env=ENV)
    assert out.exit_code == 1
    assert "no report files found" in out.output


def test_report_cleanup_deletes_only_old_files(monkeypatch, tmp_path):
    monkeypatch.setattr(instances_mod, "CONFIG_DIR", tmp_path / "sumo-search")
    _write_fake_report(tmp_path, "default", "old.pdf", age_days=40)
    _write_fake_report(tmp_path, "default", "recent.pdf", age_days=1)

    out = runner.invoke(clim.app, ["report", "cleanup", "--older-than", "30d"], env=ENV)
    assert out.exit_code == 0, out.output
    assert "Deleted 1 file(s)" in out.output

    remaining = report_paths_mod.all_report_files()
    assert [f.name for f in remaining] == ["recent.pdf"]


def test_report_cleanup_invalid_duration_exits_1(monkeypatch, tmp_path):
    monkeypatch.setattr(instances_mod, "CONFIG_DIR", tmp_path / "sumo-search")
    out = runner.invoke(clim.app, ["report", "cleanup", "--older-than", "bogus"], env=ENV)
    assert out.exit_code == 1
    assert "--older-than" in out.output
