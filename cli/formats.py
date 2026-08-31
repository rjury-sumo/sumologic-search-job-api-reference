"""
cli/formats.py — output renderers for sumosearch, plus the client-side
field projection and token-budget helpers that back them.

Four renderers: csv (default for `records`-typed results), ndjson
(default for `messages`-typed results), json (a single envelope/array),
and table (aligned, human-readable). All operate on plain `list[dict]`
rows — callers are responsible for turning API shapes (`{"map": {...}}`
items, partition/FER/view dicts) into rows first.

Why client-side projection for `messages` output: server-side `| fields`
cannot be trusted to have reliably narrowed a raw-message result (it can
no-op, leaving the full envelope intact, or trigger a sparse global-field
union) — see skills/ai-agent-result-shaping/SKILL.md and
docs/dev/agent-cli-analysis-and-plan.md §3.3 for the full rationale. The
fixed envelope + `--fields` projection here is the actual trimming
mechanism, not a formatting nicety.
"""

from __future__ import annotations

import csv
import io
import json
from typing import Any

VALID_FORMATS = ("csv", "ndjson", "json", "table")

# Fixed envelope kept for every `messages` row, regardless of `--fields` —
# the minimum needed to place a raw log line in context.
MESSAGE_ENVELOPE_FIELDS: tuple[str, ...] = ("_messagetime", "_sourcecategory", "_sourcehost", "_raw")

DEFAULT_TOKEN_WARNING_THRESHOLD = 4000


def estimate_tokens(text: str) -> int:
    """len//4 heuristic — adequate for a stderr nudge, not billing-accurate
    (see docs/dev/agent-cli-analysis-and-plan.md, which uses the same
    approximation when sizing these defaults)."""
    return len(text) // 4


# ---------------------------------------------------------------------------
# Client-side field projection (the `messages` trimming mechanism)
# ---------------------------------------------------------------------------

def project_message_row(item: dict[str, Any], fields: list[str] | None) -> dict[str, Any]:
    """Build the projected field set for one `messages` item: the fixed
    envelope (whichever of MESSAGE_ENVELOPE_FIELDS are actually present —
    e.g. a lookup-table read has no `_raw`) plus any `--fields` names, in
    that order. Never passes the raw `map` through wholesale."""
    m = item.get("map", {})
    projected: dict[str, Any] = {}
    for key in MESSAGE_ENVELOPE_FIELDS:
        if key in m:
            projected[key] = m[key]
    for key in fields or ():
        if key not in projected and key in m:
            projected[key] = m[key]
    return projected


def union_columns(rows: list[dict[str, Any]], fields: list[str] | None) -> list[str]:
    """Union of dict keys across rows, in order of first appearance. When
    `fields` is given, restrict to those names (in the given order) that
    actually appear somewhere in the union — a `--fields` name that never
    appears in any row contributes no column."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen_set:
                seen_set.add(key)
                seen.append(key)
    if fields:
        return [f for f in fields if f in seen_set]
    return seen


def drop_null_columns(rows: list[dict[str, Any]], columns: list[str]) -> list[str]:
    """Drop any column that is null/empty-string/missing across every row
    of `rows`."""
    def present(value: Any) -> bool:
        return value is not None and value != ""

    return [c for c in columns if any(present(row.get(c)) for row in rows)]


def select_columns(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    return {c: row.get(c) for c in columns if c in row}


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(columns)
    for row in rows:
        writer.writerow([_stringify(row.get(c)) for c in columns])
    return buf.getvalue().rstrip("\n")


def render_table(rows: list[dict[str, Any]], columns: list[str]) -> str:
    str_rows = [[_stringify(row.get(c)) for c in columns] for row in rows]
    widths = [len(c) for c in columns]
    for r in str_rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(c.ljust(w) for c, w in zip(columns, widths))]
    for r in str_rows:
        lines.append("  ".join(cell.ljust(w) for cell, w in zip(r, widths)))
    return "\n".join(lines)


def render_ndjson(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    """One JSON object per line. `columns=None` (the `messages` case)
    dumps each row's own keys as-is — rows are already the per-row
    projection, not a shared column set."""
    if columns is None:
        return "\n".join(json.dumps(row) for row in rows)
    return "\n".join(json.dumps(select_columns(row, columns)) for row in rows)


def render_json(rows: list[dict[str, Any]], columns: list[str] | None = None) -> str:
    if columns is None:
        return json.dumps(rows)
    return json.dumps([select_columns(row, columns) for row in rows])


def render_by_format(rows: list[dict[str, Any]], columns: list[str] | None, fmt: str) -> str:
    """Dispatch to the renderer named by `fmt`. `columns=None` is only
    meaningful for ndjson/json (per-row keys, no shared column set)."""
    if fmt == "csv":
        return render_csv(rows, columns or [])
    if fmt == "table":
        return render_table(rows, columns or [])
    if fmt == "ndjson":
        return render_ndjson(rows, columns)
    if fmt == "json":
        return render_json(rows, columns)
    raise ValueError(f"unknown format {fmt!r}")


# ---------------------------------------------------------------------------
# Token-budget helpers
# ---------------------------------------------------------------------------

def truncate_to_token_budget(
    rows: list[dict[str, Any]], render_fn, max_tokens: int,
) -> tuple[list[dict[str, Any]], int]:
    """Drop whole trailing rows (never mid-record) until
    `render_fn(rows)`'s estimated token count is under `max_tokens`.
    `render_fn` renders a list of rows to the final output string in
    whatever format the caller is using. Returns (kept rows, dropped
    count). Binary search over row count keeps this to O(log n) renders
    rather than O(n)."""
    if not rows or estimate_tokens(render_fn(rows)) <= max_tokens:
        return rows, 0

    lo, hi = 0, len(rows)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if estimate_tokens(render_fn(rows[:mid])) <= max_tokens:
            lo = mid
        else:
            hi = mid - 1
    return rows[:lo], len(rows) - lo


# ---------------------------------------------------------------------------
# search estimate — a distinct shape (total_bytes + per-partition table)
# ---------------------------------------------------------------------------

def render_estimate(total_bytes: int, partitions: list[dict[str, Any]], fmt: str) -> str:
    if fmt == "json":
        return json.dumps({"total_bytes": total_bytes, "partitions": partitions})
    columns = union_columns(partitions, None)
    if fmt == "ndjson":
        header = json.dumps({"total_bytes": total_bytes})
        return "\n".join([header, *(json.dumps(p) for p in partitions)])
    if fmt == "csv":
        return f"total_bytes\n{total_bytes}\n\n" + render_csv(partitions, columns)
    # table (default for `search estimate` per the CLI spec)
    return f"total_bytes: {total_bytes}\n\n" + render_table(partitions, columns)
