#!/usr/bin/env python3
"""
integration_test_sumo_dashboard_client.py — live-API integration tests for
sumo_dashboard_client.py, run directly against a real Sumo Logic org.

Exercises the full get-dashboard -> build-body -> create-job -> poll ->
fetch-binary-result lifecycle for real, covering the shapes the unit tests
can only fake:

  1. get_dashboard             — fetch a real dashboard, sanity-check its shape
  2. describe (summary/panels/queries) — cli/dashboard_describe.py against a
     real payload, including its `layoutStructures[].structure` JSON-string
     parsing and (if the dashboard has any) CollapsiblePanel child handling
  3. default_variable_values   — the dashboard's real saved variable defaults
     merge correctly (the "silent failure trap" this client exists to avoid)
  4. panel-override validation — parse/validate against the dashboard's real
     panel list (SKIPs if the dashboard has no collapsible sections)
  5. full report-job run       — create -> poll -> fetch a real PDF, assert
     non-empty PDF bytes come back
  6. unknown dashboard id      — 404 surfaces as SumoDashboardError
  7. poll timeout with 0s budget — SumoDashboardTimeout, no network needed

Requires SUMO_ACCESS_ID / SUMO_ACCESS_KEY for a real org, read from this
process's actual environment (same variables as
integration_test_sumo_search_client.py; SUMO_ENDPOINT defaults to the AU
region if unset — override it if your dashboard lives in a different org).
Set SUMO_TEST_DASHBOARD_ID to a dashboard your credential can view; defaults
to a public demo dashboard.

Run:
    uv run python tests/integration_test_sumo_dashboard_client.py
    uv run python tests/integration_test_sumo_dashboard_client.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from cli.dashboard_describe import (  # noqa: E402
    describe_dashboard_panels,
    describe_dashboard_queries,
    summarize_dashboard,
)
from sumo_dashboard_client import (  # noqa: E402
    SumoDashboardClient,
    SumoDashboardError,
    SumoDashboardTimeout,
    build_report_body,
    collapsible_panel_ids,
    default_variable_values,
    parse_panel_overrides,
    poll_report_job,
    validate_panel_overrides,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"
INFO = "\033[36mINFO\033[0m"

_results: list[dict] = []

HAS_CREDS = bool(os.environ.get("SUMO_ACCESS_ID") and os.environ.get("SUMO_ACCESS_KEY"))
ENDPOINT = os.environ.get("SUMO_ENDPOINT", "https://api.au.sumologic.com")  # AU default
DASHBOARD_ID = os.environ.get(
    "SUMO_TEST_DASHBOARD_ID",
    "qHejuxBPCoVf8rfE32gjVe8wy5yE17ZNtTnB3nub8szJ7ZG978MAqektn7H6",
)


def log(label: str, msg: str) -> None:
    print(f"[{label}] {msg}")


def record(name: str, status: str, detail: str = "") -> None:
    _results.append({"name": name, "status": status, "detail": detail})
    tag = {"PASS": PASS, "FAIL": FAIL, "SKIP": SKIP}.get(status, status)
    print(f"[{tag}] {name}{' — ' + detail if detail else ''}")


def run_test(name: str, fn) -> None:
    try:
        fn()
        record(name, "PASS")
    except AssertionError as exc:
        record(name, "FAIL", str(exc))
    except Exception as exc:
        record(name, "FAIL", f"{type(exc).__name__}: {exc}")


def _client() -> SumoDashboardClient:
    return SumoDashboardClient(
        os.environ["SUMO_ACCESS_ID"], os.environ["SUMO_ACCESS_KEY"], ENDPOINT,
    )


# ---------------------------------------------------------------------------
# 1. get_dashboard — fetch a real dashboard
# ---------------------------------------------------------------------------

def test_get_dashboard_returns_known_shape():
    client = _client()
    dashboard = client.get_dashboard(DASHBOARD_ID)
    for key in ("id", "title", "panels"):
        assert key in dashboard, f"expected {key!r} on a dashboard, got keys {list(dashboard)}"
    log(INFO, f"dashboard {DASHBOARD_ID!r}: title={dashboard['title']!r}, "
              f"{len(dashboard.get('panels', []))} panel(s)")


# ---------------------------------------------------------------------------
# 2. describe levels against a real payload
# ---------------------------------------------------------------------------

def test_describe_levels_against_real_dashboard():
    client = _client()
    dashboard = client.get_dashboard(DASHBOARD_ID)

    summary = summarize_dashboard(dashboard)
    assert summary["id"] == dashboard["id"]
    assert summary["panel_count"] == len(dashboard.get("panels", []))

    panels = describe_dashboard_panels(dashboard)
    assert "panels" in panels
    top_level_count = len(panels["panels"])
    log(INFO, f"describe_dashboard_panels: {top_level_count} top-level panel(s)")

    queries = describe_dashboard_queries(dashboard)
    assert all("queries" in p for p in queries["panels"]), "every top-level panel should carry a queries[] list"
    collapsible = [p for p in panels["panels"] if p.get("panelType") == "CollapsiblePanel"]
    if collapsible:
        section = collapsible[0]
        assert "children" in section, "a CollapsiblePanel entry must carry a children[] list"
        log(INFO, f"collapsible section {section['id']!r}: {len(section['children'])} child panel(s)")
    else:
        log(INFO, "no CollapsiblePanel sections on this dashboard — child-flattening path not exercised")


# ---------------------------------------------------------------------------
# 3. default_variable_values — the "silent failure trap" this client exists
#    to close: the report-job API won't apply these on its own.
# ---------------------------------------------------------------------------

def test_default_variable_values_matches_dashboard_variables():
    client = _client()
    dashboard = client.get_dashboard(DASHBOARD_ID)
    declared = {v["name"] for v in dashboard.get("variables", []) if v.get("name")}
    defaults = default_variable_values(dashboard)
    if not declared:
        log(INFO, "dashboard has no template variables — nothing to merge")
        return
    log(INFO, f"declared variables: {sorted(declared)}, defaults resolved: {defaults}")
    # Every variable with a non-empty saved defaultValue should be present.
    has_default = {v["name"] for v in dashboard.get("variables", []) if v.get("defaultValue")}
    assert has_default <= set(defaults), (
        f"expected every variable with a saved defaultValue ({sorted(has_default)}) "
        f"to appear in default_variable_values() output ({sorted(defaults)})"
    )


# ---------------------------------------------------------------------------
# 4. panel-override validation against the real panel list
# ---------------------------------------------------------------------------

def test_panel_override_validation_against_real_dashboard():
    client = _client()
    dashboard = client.get_dashboard(DASHBOARD_ID)
    collapsible = collapsible_panel_ids(dashboard)
    if not collapsible:
        log(INFO, "dashboard has no collapsible sections — SKIP")
        record("panel-override validation (real dashboard)", "SKIP", "no collapsible sections")
        return
    panel_id = next(iter(collapsible))
    overrides = parse_panel_overrides([f"{panel_id}=collapsed"])
    validate_panel_overrides(overrides, dashboard)  # should not raise
    log(INFO, f"valid override accepted for panel {panel_id!r}")
    try:
        validate_panel_overrides(parse_panel_overrides(["not-a-real-panel-id=expanded"]), dashboard)
        raise AssertionError("expected an unknown panel id to raise ValueError")
    except ValueError:
        log(INFO, "unknown panel id correctly rejected")


# ---------------------------------------------------------------------------
# 5. Full report-job run — create, poll, fetch a real PDF
# ---------------------------------------------------------------------------

def test_full_report_run_produces_pdf_bytes():
    client = _client()
    dashboard = client.get_dashboard(DASHBOARD_ID)
    variables = default_variable_values(dashboard)
    body = build_report_body(
        export_format="pdf", mode="snapshot", theme=None, export_width=None,
        timezone_name="UTC", dashboard_id=DASHBOARD_ID, time_range=None,
        variables=variables, panel_overrides=[],
    )
    job_id = client.create_report_job(body)
    log(INFO, f"created report job {job_id}")
    status = poll_report_job(client, job_id, timeout_s=120)
    assert status.get("status") == "Success", f"expected Success, got {status}"
    result = client.get_report_result(job_id)
    assert result.content, "expected non-empty PDF bytes"
    assert result.content[:4] == b"%PDF", f"expected a PDF magic header, got {result.content[:8]!r}"
    log(INFO, f"fetched {len(result.content)} bytes, content_type={result.content_type!r}")


# ---------------------------------------------------------------------------
# 6. Unknown dashboard id — 404 surfaces as SumoDashboardError
# ---------------------------------------------------------------------------

def test_unknown_dashboard_id_raises_404():
    client = _client()
    try:
        client.get_dashboard("this-dashboard-id-does-not-exist-12345")
        raise AssertionError("expected an unknown dashboard id to raise SumoDashboardError")
    except SumoDashboardError as exc:
        assert exc.status_code == 404, f"expected HTTP 404, got {exc.status_code}"
        log(INFO, f"unknown dashboard id correctly rejected: {exc}")


# ---------------------------------------------------------------------------
# No-API tests
# ---------------------------------------------------------------------------

class _StubClient:
    """No network calls — status never reaches a terminal state, so
    poll_report_job must time out on its own deadline math."""

    def get_report_status(self, job_id: str) -> dict:
        return {"status": "InProgress"}


def test_poll_timeout_with_zero_budget():
    try:
        poll_report_job(_StubClient(), "nonexistent-job-id", timeout_s=0)
        raise AssertionError("expected SumoDashboardTimeout with a 0s budget")
    except SumoDashboardTimeout:
        pass


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _print_summary() -> None:
    total = len(_results)
    passed = sum(1 for r in _results if r["status"] == "PASS")
    failed = sum(1 for r in _results if r["status"] == "FAIL")
    skipped = sum(1 for r in _results if r["status"] == "SKIP")
    print(f"\n{'=' * 60}")
    print(f"Results: {passed}/{total} passed, {failed} failed, {skipped} skipped")
    if failed:
        print("FAILED tests:")
        for r in _results:
            if r["status"] == "FAIL":
                print(f"  ✗ {r['name']}: {r['detail']}")
    print("=" * 60)


def main() -> None:
    parser = argparse.ArgumentParser(description="Integration tests for sumo_dashboard_client.py")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip live API tests (run no-API tests only)")
    args = parser.parse_args()

    live_tests = [
        ("get_dashboard shape",              test_get_dashboard_returns_known_shape),
        ("describe levels (real dashboard)", test_describe_levels_against_real_dashboard),
        ("default_variable_values merge",    test_default_variable_values_matches_dashboard_variables),
        ("panel-override validation",        test_panel_override_validation_against_real_dashboard),
        ("full report run -> PDF bytes",     test_full_report_run_produces_pdf_bytes),
        ("unknown dashboard id -> 404",      test_unknown_dashboard_id_raises_404),
    ]
    no_api_tests = [
        ("poll timeout with 0s budget", test_poll_timeout_with_zero_budget),
    ]

    for name, fn in no_api_tests:
        run_test(name, fn)

    if args.dry_run:
        for name, _ in live_tests:
            record(name, "SKIP", "dry-run")
    elif not HAS_CREDS:
        for name, _ in live_tests:
            record(name, "SKIP", "no credentials (set SUMO_ACCESS_ID + SUMO_ACCESS_KEY)")
    else:
        log(INFO, f"running against {ENDPOINT}, dashboard {DASHBOARD_ID}")
        for name, fn in live_tests:
            run_test(name, fn)

    _print_summary()
    sys.exit(0 if all(r["status"] != "FAIL" for r in _results) else 1)


if __name__ == "__main__":
    main()
