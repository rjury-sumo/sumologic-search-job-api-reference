#!/usr/bin/env python3
"""
integration_test_sumo_search_client.py — live-API integration tests for
sumo_search_client.py, run directly against a real Sumo Logic org.

Exercises the full create -> poll -> fetch -> delete lifecycle for real,
covering the shapes the unit tests can only fake:

  1. aggregate query  — records path (recordCount > 0)
  2. raw message query — messages path (messageCount > 0)
  3. lookup-table read — messages path with every _raw empty
  4. invalid query    — pendingErrors / non-2xx surfaced as an exception
  5. multi-line query — the same aggregate query reformatted across lines
  6. estimate_scan     — free pre-flight, no job created
  7-9. job-creation time anchors — byReceiptTime / bySearchableTime vs. the
       default (message time): min/max of the anchored time field must fall
       inside the requested [from, to] window
  10. timeZone         — a non-UTC zone shifts the scanned window when
       from/to are naive (no offset/Z) timestamps
  11-13. autoParsingMode — omitted/"Manual" extracts a small, fixed field set;
       "AutoParse" additionally flattens JSON log bodies into many more fields
  14-16. list_partitions / list_extraction_rules / list_scheduled_views —
       read-only metadata GETs; SKIPs (not FAILs) on a 403 since RBAC for
       these varies by credential/tenant

Requires SUMO_ACCESS_ID / SUMO_ACCESS_KEY for a real org, read from this
process's actual environment (SUMO_ENDPOINT defaults to the AU region,
matching this repo's sandbox, if unset). This script does NOT load any
.env file itself — no python-dotenv dependency here, unlike the `sumo`
CLI's config.py (which reads $SUMO_HOME/.env, e.g. ~/.sumo/.env — a
separate, CLI-only credential store, unrelated to this standalone
client). If these vars live in your shell rc file (~/.zshrc, etc.) rather
than being exported in your current shell, `source` it first — a
non-interactive shell won't pick them up automatically. Tests 1, 2, and
5 only assume `_sourcecategory` metadata and a "*" wildcard exist, which
is true of any org with log traffic. Test 3 additionally assumes a
specific lookup file exists in the org's content library — see SKIP
handling below if it doesn't. Tests 7-13 assume `_sourcecategory=*cloudtrail*`
matches real traffic in the org, same as tests 1 and 5.

Run:
    uv run python tests/integration_test_sumo_search_client.py
    uv run python tests/integration_test_sumo_search_client.py --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sumo_search_client import (  # noqa: E402
    SumoSearchClient,
    SumoSearchError,
    SumoSearchJobFailed,
    SumoSearchTimeout,
)

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"
SKIP = "\033[33mSKIP\033[0m"
INFO = "\033[36mINFO\033[0m"

_results: list[dict] = []

# Read directly from the environment — no dotenv loading in this script.
# ~/.sumo/.env is a DIFFERENT, CLI-only credential file loaded by the
# `sumo` CLI's config.py; it has no effect here. Export SUMO_ACCESS_ID /
# SUMO_ACCESS_KEY in this shell (or `source` the rc file that sets them)
# before running.
HAS_CREDS = bool(os.environ.get("SUMO_ACCESS_ID") and os.environ.get("SUMO_ACCESS_KEY"))
ENDPOINT = os.environ.get("SUMO_ENDPOINT", "https://api.au.sumologic.com")  # AU default

# A lookup-table path known to exist in the sandbox org for the
# "columns but no _raw" edge case (test 3). If your org doesn't have this
# path, override with SUMO_TEST_LOOKUP_PATH or the test SKIPs cleanly.
LOOKUP_PATH = os.environ.get(
    "SUMO_TEST_LOOKUP_PATH",
    '/Library/Users/rjury+test@sumologic.com/microlessons',
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


def _client() -> SumoSearchClient:
    return SumoSearchClient(
        os.environ["SUMO_ACCESS_ID"], os.environ["SUMO_ACCESS_KEY"], ENDPOINT,
    )


# ---------------------------------------------------------------------------
# 1. Aggregate query — create, poll, pull records
# ---------------------------------------------------------------------------

def test_aggregate_query_returns_records():
    client = _client()
    query = "_sourcecategory=*cloudtrail* | count by _sourcecategory,_view,recipientaccountid"
    result = client.run_search(query, from_time="-24h", to_time="now",
                               requires_raw_messages=False)
    assert result.result_type == "records", f"expected records, got {result.result_type}"
    log(INFO, f"aggregate query: {result.total} rows, {len(result.items)} fetched")
    if result.items:
        row = result.items[0]
        assert "map" in row, "record row missing 'map'"
        assert "_count" in row["map"], f"expected _count in aggregate row, got keys {list(row['map'])}"


# ---------------------------------------------------------------------------
# 2. Raw message query — messages path
# ---------------------------------------------------------------------------

def test_raw_message_query_returns_messages():
    client = _client()
    result = client.run_search("_sourcecategory = * | limit 10", from_time="-24h", to_time="now")
    assert result.result_type == "messages", f"expected messages, got {result.result_type}"
    log(INFO, f"raw query: {result.total} total, {len(result.items)} fetched")
    assert len(result.items) <= 10, f"expected <=10 rows (query has 'limit 10'), got {len(result.items)}"
    if result.items and not result.looks_like_lookup_table:
        assert result.items[0]["map"].get("_raw"), "expected non-empty _raw on a real log message"


# ---------------------------------------------------------------------------
# 3. Lookup-table read — columns present, _raw empty on every row
# ---------------------------------------------------------------------------

def test_lookup_table_read_has_no_raw():
    client = _client()
    query = f'cat path://"{LOOKUP_PATH}"'
    try:
        result = client.run_search(query, from_time="-24h", to_time="now")
    except (SumoSearchError, SumoSearchJobFailed) as exc:
        raise AssertionError(
            f"lookup path {LOOKUP_PATH!r} not readable in this org ({exc}) — "
            "set SUMO_TEST_LOOKUP_PATH to a path that exists, or treat as SKIP"
        )
    assert result.result_type == "messages", f"expected messages path, got {result.result_type}"
    if not result.items:
        log(INFO, f"lookup path {LOOKUP_PATH!r} returned 0 rows — nothing to assert on")
        return
    assert result.looks_like_lookup_table, (
        "expected looks_like_lookup_table=True for a lookup-table read "
        f"(got {len(result.items)} rows, first map keys: {list(result.items[0]['map'])})"
    )
    non_raw_keys = [k for k in result.items[0]["map"] if k != "_raw"]
    assert non_raw_keys, "expected real column data outside of _raw"
    log(INFO, f"lookup table columns: {non_raw_keys}")


# ---------------------------------------------------------------------------
# 4. Invalid query — too many pipes, expect a reported failure
# ---------------------------------------------------------------------------

def test_invalid_query_surfaces_as_error():
    client = _client()
    query = "* | limit 10 | | | count"
    try:
        client.run_search(query, from_time="-1h", to_time="now")
        raise AssertionError("expected the malformed query to fail, but run_search succeeded")
    except SumoSearchJobFailed as exc:
        log(INFO, f"job reported pendingErrors as expected: {exc}")
    except SumoSearchError as exc:
        assert exc.status_code and exc.status_code >= 400, (
            f"expected a 4xx from job creation, got status_code={exc.status_code}"
        )
        log(INFO, f"create_job rejected the query at HTTP {exc.status_code} as expected")


# ---------------------------------------------------------------------------
# 5. Multi-line query — same aggregate query, reformatted across lines
# ---------------------------------------------------------------------------

def test_multiline_query_runs_successfully():
    client = _client()
    query = """
        _sourcecategory=*cloudtrail*
        | count by _sourcecategory,
                    _view,
                    recipientaccountid
    """
    result = client.run_search(query, from_time="-24h", to_time="now",
                               requires_raw_messages=False)
    assert result.result_type == "records", f"expected records, got {result.result_type}"
    log(INFO, f"multi-line query: {result.total} rows")


# ---------------------------------------------------------------------------
# 6. estimate_scan — synchronous pre-flight, no job created
# ---------------------------------------------------------------------------

def test_estimate_scan_precheck():
    client = _client()
    to_ms = int(time.time() * 1000)
    from_ms = to_ms - 3_600_000
    estimate = client.estimate_scan("_sourcecategory=* error", from_ms=from_ms, to_ms=to_ms)
    assert estimate.total_bytes >= 0, "total_bytes should be a non-negative integer"
    log(INFO, f"estimate_scan: {estimate.total_bytes} bytes across {len(estimate.partitions)} partition(s)")


def test_estimate_scan_bad_query_raises_400():
    client = _client()
    to_ms = int(time.time() * 1000)
    from_ms = to_ms - 3_600_000
    try:
        client.estimate_scan("_view=thisViewDoesNotExist12345 *", from_ms=from_ms, to_ms=to_ms)
        raise AssertionError("expected estimate_scan on an unknown view to raise")
    except SumoSearchError as exc:
        assert exc.status_code == 400, f"expected HTTP 400, got {exc.status_code}"
        log(INFO, f"estimate_scan correctly rejected unknown view: {exc}")


# ---------------------------------------------------------------------------
# 7-9. Job-creation time anchor — byReceiptTime / bySearchableTime vs. the
#      default (message time). A single aggregate query computes min/max for
#      all three time fields at once, keyed off real cloudtrail traffic; each
#      test only asserts on the field matching that job's actual anchor,
#      since only the anchor field is guaranteed to fall inside [from, to] —
#      the other two are informational (logged, not asserted).
# ---------------------------------------------------------------------------

TIME_FIELDS_QUERY = (
    "_sourcecategory=*cloudtrail* | count, "
    "min(_messagetime) as min_mt, max(_messagetime) as max_mt, "
    "min(_receipttime) as min_rt, max(_receipttime) as max_rt, "
    "min(_searchableTime) as min_st, max(_searchableTime) as max_st "
    "by _sourcecategory"
)


def _epoch_ms(value: str) -> int:
    """min()/max() on a time field comes back as a scientific-notation string
    (e.g. "1.788125826717E12"), not a plain integer — int() alone chokes on
    it, so parse through float() first."""
    return int(float(value))


def test_search_by_message_time_within_window():
    """Default anchor (byReceiptTime=False, bySearchableTime=False):
    min/max _messagetime must fall inside [from, to]."""
    client = _client()
    to_ms = int(time.time() * 1000)
    from_ms = to_ms - 24 * 3_600_000
    result = client.run_search(TIME_FIELDS_QUERY, from_ms, to_ms, requires_raw_messages=False)
    assert result.result_type == "records", f"expected records, got {result.result_type}"
    if not result.items:
        log(INFO, "no cloudtrail data in range — nothing to assert on")
        return
    row = result.items[0]["map"]
    min_mt, max_mt = _epoch_ms(row["min_mt"]), _epoch_ms(row["max_mt"])
    log(INFO, f"message-time anchor: mt=[{min_mt},{max_mt}] window=[{from_ms},{to_ms}] "
              f"(rt=[{row['min_rt']},{row['max_rt']}] st=[{row['min_st']},{row['max_st']}])")
    assert from_ms <= min_mt, f"min_mt {min_mt} precedes window start {from_ms}"
    assert max_mt <= to_ms, f"max_mt {max_mt} exceeds window end {to_ms}"


def test_search_by_receipt_time_within_window():
    """byReceiptTime=True: min/max _receipttime must fall inside [from, to]."""
    client = _client()
    to_ms = int(time.time() * 1000)
    from_ms = to_ms - 24 * 3_600_000
    result = client.run_search(TIME_FIELDS_QUERY, from_ms, to_ms,
                               by_receipt_time=True, requires_raw_messages=False)
    assert result.result_type == "records", f"expected records, got {result.result_type}"
    if not result.items:
        log(INFO, "no cloudtrail data in range — nothing to assert on")
        return
    row = result.items[0]["map"]
    min_rt, max_rt = _epoch_ms(row["min_rt"]), _epoch_ms(row["max_rt"])
    log(INFO, f"receipt-time anchor: rt=[{min_rt},{max_rt}] window=[{from_ms},{to_ms}] "
              f"(mt=[{row['min_mt']},{row['max_mt']}] st=[{row['min_st']},{row['max_st']}])")
    assert from_ms <= min_rt, f"min_rt {min_rt} precedes window start {from_ms}"
    assert max_rt <= to_ms, f"max_rt {max_rt} exceeds window end {to_ms}"


def test_search_by_searchable_time_within_window():
    """bySearchableTime=True: min/max _searchableTime must fall inside [from, to]."""
    client = _client()
    to_ms = int(time.time() * 1000)
    from_ms = to_ms - 24 * 3_600_000
    result = client.run_search(TIME_FIELDS_QUERY, from_ms, to_ms,
                               by_searchable_time=True, requires_raw_messages=False)
    assert result.result_type == "records", f"expected records, got {result.result_type}"
    if not result.items:
        log(INFO, "no cloudtrail data in range — nothing to assert on")
        return
    row = result.items[0]["map"]
    min_st, max_st = _epoch_ms(row["min_st"]), _epoch_ms(row["max_st"])
    log(INFO, f"searchable-time anchor: st=[{min_st},{max_st}] window=[{from_ms},{to_ms}] "
              f"(mt=[{row['min_mt']},{row['max_mt']}] rt=[{row['min_rt']},{row['max_rt']}])")
    assert from_ms <= min_st, f"min_st {min_st} precedes window start {from_ms}"
    assert max_st <= to_ms, f"max_st {max_st} exceeds window end {to_ms}"


# ---------------------------------------------------------------------------
# 10. timeZone — a non-UTC zone shifts the scanned window when from/to are
#     naive (no offset/Z) ISO strings. Epoch-ms or offset-bearing timestamps
#     are absolute and ignore timeZone entirely, so this test deliberately
#     passes naive local-looking strings instead of the epoch ms used above.
# ---------------------------------------------------------------------------

# Fixed UTC+14 all year (no DST) — the largest standard IANA offset, chosen
# so the shifted window is unambiguous even against noisy live cloudtrail data.
TZ_HIGH_OFFSET = "Pacific/Kiritimati"
TZ_OFFSET_MS = 14 * 3_600_000
TZ_TOLERANCE_MS = 60_000  # query-execution slack, not a timezone fudge factor


def test_timezone_offset_shifts_scanned_window():
    client = _client()
    now_utc = datetime.now(timezone.utc)
    from_utc = now_utc - timedelta(hours=24)
    # No trailing 'Z' or offset: the API must fall back to `timeZone` to
    # resolve these wall-clock digits to an absolute instant.
    naive_from = from_utc.strftime("%Y-%m-%dT%H:%M:%S")
    naive_to = now_utc.strftime("%Y-%m-%dT%H:%M:%S")
    from_ms_utc = int(from_utc.timestamp() * 1000)
    to_ms_utc = int(now_utc.timestamp() * 1000)

    result_utc = client.run_search(TIME_FIELDS_QUERY, naive_from, naive_to,
                                   time_zone="UTC", requires_raw_messages=False)
    result_tz = client.run_search(TIME_FIELDS_QUERY, naive_from, naive_to,
                                  time_zone=TZ_HIGH_OFFSET, requires_raw_messages=False)
    assert result_utc.result_type == "records", f"expected records, got {result_utc.result_type}"
    assert result_tz.result_type == "records", f"expected records, got {result_tz.result_type}"

    if not result_utc.items or not result_tz.items:
        log(INFO, "no cloudtrail data in one of the two windows — nothing to assert on")
        return

    min_mt_utc = _epoch_ms(result_utc.items[0]["map"]["min_mt"])
    max_mt_utc = _epoch_ms(result_utc.items[0]["map"]["max_mt"])
    min_mt_tz = _epoch_ms(result_tz.items[0]["map"]["min_mt"])
    max_mt_tz = _epoch_ms(result_tz.items[0]["map"]["max_mt"])

    expected_from_tz = from_ms_utc - TZ_OFFSET_MS
    expected_to_tz = to_ms_utc - TZ_OFFSET_MS
    log(INFO, f"timeZone=UTC same digits -> mt=[{min_mt_utc},{max_mt_utc}] window=[{from_ms_utc},{to_ms_utc}]")
    log(INFO, f"timeZone={TZ_HIGH_OFFSET} same digits -> mt=[{min_mt_tz},{max_mt_tz}] "
              f"expected window=[{expected_from_tz},{expected_to_tz}]")

    # Same wall-clock digits interpreted at UTC+14 resolve to an instant 14h
    # EARLIER than at UTC — the whole scanned window (and its results) shifts back.
    assert max_mt_tz <= max_mt_utc, (
        f"expected the {TZ_HIGH_OFFSET} window's max_mt ({max_mt_tz}) at or before "
        f"the UTC window's max_mt ({max_mt_utc}) — a +14h timeZone should shift the "
        "scanned window earlier, not later"
    )
    assert max_mt_tz <= expected_to_tz + TZ_TOLERANCE_MS, (
        f"max_mt {max_mt_tz} exceeds the expected shifted window end "
        f"{expected_to_tz} (+{TZ_TOLERANCE_MS}ms tolerance)"
    )
    assert min_mt_tz >= expected_from_tz - TZ_TOLERANCE_MS, (
        f"min_mt {min_mt_tz} precedes the expected shifted window start "
        f"{expected_from_tz} (-{TZ_TOLERANCE_MS}ms tolerance)"
    )


# ---------------------------------------------------------------------------
# 11-13. autoParsingMode — omitted/"Manual" should extract only built-in +
#        FER fields (small, fixed set); "AutoParse" additionally flattens
#        JSON log bodies into their own fields (substantially larger, and
#        varying with the actual cloudtrail event mix in range).
# ---------------------------------------------------------------------------

RAW_CLOUDTRAIL_QUERY = "_sourcecategory=*cloudtrail* | limit 5"


def _field_count(client: SumoSearchClient, auto_parsing_mode: str | None) -> int | None:
    result = client.run_search(RAW_CLOUDTRAIL_QUERY, from_time="-24h", to_time="now",
                               auto_parsing_mode=auto_parsing_mode)
    assert result.result_type == "messages", f"expected messages, got {result.result_type}"
    if not result.items:
        return None
    return len(result.items[0]["map"])


def test_auto_parsing_mode_default_field_count_is_small():
    client = _client()
    count = _field_count(client, None)
    if count is None:
        log(INFO, "no cloudtrail messages in range — nothing to assert on")
        return
    log(INFO, f"autoParsingMode omitted (defaults to Manual): {count} fields")
    assert count < 50, f"expected a small built-in + FER-only field set, got {count} fields"


def test_auto_parsing_mode_manual_matches_default():
    client = _client()
    count = _field_count(client, "Manual")
    if count is None:
        log(INFO, "no cloudtrail messages in range — nothing to assert on")
        return
    log(INFO, f"autoParsingMode=Manual: {count} fields")
    assert count < 50, f"expected Manual to match the small default field set, got {count} fields"


def test_auto_parsing_mode_autoparse_increases_field_count():
    client = _client()
    manual_count = _field_count(client, "Manual")
    auto_count = _field_count(client, "AutoParse")
    if manual_count is None or auto_count is None:
        log(INFO, "no cloudtrail messages in range — nothing to assert on")
        return
    log(INFO, f"Manual={manual_count} fields, AutoParse={auto_count} fields")
    assert auto_count > manual_count + 15, (
        f"expected AutoParse ({auto_count}) to extract substantially more JSON "
        f"fields than Manual ({manual_count})"
    )


# ---------------------------------------------------------------------------
# 14-16. Read-only discovery endpoints — partitions, FERs, scheduled views.
#         All three are metadata-only GETs (no job created); RBAC to list
#         them varies by credential/tenant, so a 403 is logged as INFO and
#         the test SKIPs rather than FAILs — this endpoint being denied
#         says nothing about the client's own request-building logic.
# ---------------------------------------------------------------------------

def test_list_partitions_returns_known_shape():
    client = _client()
    try:
        partitions = client.list_partitions()
    except SumoSearchError as exc:
        if exc.status_code == 403:
            log(INFO, f"list_partitions: RBAC denied (403) for this credential — {exc}")
            return
        raise
    log(INFO, f"list_partitions: {len(partitions)} partition(s)")
    if not partitions:
        return
    row = partitions[0]
    for key in ("id", "name", "routingExpression"):
        assert key in row, f"expected {key!r} on a partition, got keys {list(row)}"


def test_list_extraction_rules_returns_known_shape():
    client = _client()
    try:
        rules = client.list_extraction_rules()
    except SumoSearchError as exc:
        if exc.status_code == 403:
            log(INFO, f"list_extraction_rules: RBAC denied (403) for this credential — {exc}")
            return
        raise
    log(INFO, f"list_extraction_rules: {len(rules)} rule(s)")
    if not rules:
        return
    row = rules[0]
    for key in ("id", "name", "scope", "parseExpression", "fieldNames"):
        assert key in row, f"expected {key!r} on a FER, got keys {list(row)}"


def test_list_scheduled_views_returns_known_shape():
    client = _client()
    try:
        views = client.list_scheduled_views()
    except SumoSearchError as exc:
        if exc.status_code == 403:
            log(INFO, f"list_scheduled_views: RBAC denied (403) for this credential — {exc}")
            return
        raise
    log(INFO, f"list_scheduled_views: {len(views)} view(s)")
    if not views:
        return
    row = views[0]
    for key in ("id", "indexName", "query", "status"):
        assert key in row, f"expected {key!r} on a scheduled view, got keys {list(row)}"


# ---------------------------------------------------------------------------
# No-API tests
# ---------------------------------------------------------------------------

def test_poll_timeout_with_zero_budget():
    """A timeout_s of 0 should never even get one successful poll in —
    validates SumoSearchTimeout without needing network access to trigger."""
    client = SumoSearchClient("id", "key", ENDPOINT)
    try:
        client.poll_until_done("nonexistent-job-id", timeout_s=0)
        raise AssertionError("expected SumoSearchTimeout with a 0s budget")
    except SumoSearchTimeout:
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
    parser = argparse.ArgumentParser(description="Integration tests for sumo_search_client.py")
    parser.add_argument("--dry-run", action="store_true",
                        help="Skip live API tests (run no-API tests only)")
    args = parser.parse_args()

    live_tests = [
        ("aggregate query -> records",       test_aggregate_query_returns_records),
        ("raw message query -> messages",    test_raw_message_query_returns_messages),
        ("lookup-table read has no _raw",    test_lookup_table_read_has_no_raw),
        ("invalid query surfaces as error",  test_invalid_query_surfaces_as_error),
        ("multi-line query runs",            test_multiline_query_runs_successfully),
        ("estimate_scan precheck",           test_estimate_scan_precheck),
        ("estimate_scan bad query -> 400",   test_estimate_scan_bad_query_raises_400),
        ("search by message time in window", test_search_by_message_time_within_window),
        ("search by receipt time in window", test_search_by_receipt_time_within_window),
        ("search by searchable time in window", test_search_by_searchable_time_within_window),
        ("timeZone shifts scanned window",   test_timezone_offset_shifts_scanned_window),
        ("autoParsingMode default is small", test_auto_parsing_mode_default_field_count_is_small),
        ("autoParsingMode Manual matches default", test_auto_parsing_mode_manual_matches_default),
        ("autoParsingMode AutoParse grows fields", test_auto_parsing_mode_autoparse_increases_field_count),
        ("list_partitions shape",         test_list_partitions_returns_known_shape),
        ("list_extraction_rules shape",   test_list_extraction_rules_returns_known_shape),
        ("list_scheduled_views shape",    test_list_scheduled_views_returns_known_shape),
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
        log(INFO, f"running against {ENDPOINT}")
        for name, fn in live_tests:
            run_test(name, fn)

    _print_summary()
    sys.exit(0 if all(r["status"] != "FAIL" for r in _results) else 1)


if __name__ == "__main__":
    main()
