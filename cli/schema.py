"""
cli/schema.py — schema-profiling logic for `sumosearch schema`.

Given a small `messages` sample (see `cli.main.schema_cmd`, which runs
`<query> | limit N` with `autoParsingMode=Manual` by default so this
module actually sees index-time fields as index-time — AutoParse would
pre-flatten JSON server-side and erase that distinction), this module
unions every field across the sample and reports one row per field:
PRESENT/TYPE/CONST/INDEX-TIME/EXAMPLE. See
docs/dev/agent-cli-analysis-and-plan.md §6.2 for the full rationale.

Field union has two sources per row: the top-level `map` keys (always
index-time — FER-extracted, collector/source-tagged, or header-derived),
and, when `_raw` parses as a JSON object, its top-level keys (one level
of dot-notation flattening for nested objects) — these are search-time,
since they only exist after client-side JSON-parsing. A `_raw` that
doesn't parse as JSON is left alone (no field-union attempted from it)
and the sample is treated as unstructured text, which can earn a
`| parse regex` hint after the table.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from cli import formats

TABLE_COLUMNS = ("FIELD", "PRESENT", "TYPE", "CONST", "INDEX-TIME", "EXAMPLE")
EXAMPLE_MAX_LEN = 80
UNSTRUCTURED_TYPE = "unstructured-text"

# Gating thresholds for the `| parse regex` hint (see _maybe_hint).
_UNSTRUCTURED_MAJORITY = 0.5
_TOKEN_COUNT_CONSISTENCY = 0.8


def looks_like_json(text: str) -> bool:
    stripped = text.strip()
    return stripped.startswith("{") or stripped.startswith("[")


def try_parse_raw(raw: Any) -> dict[str, Any] | None:
    """Client-side JSON-parse of a `_raw` value. Returns the parsed dict
    only when `raw` is a string, looks JSON-shaped, and parses cleanly to
    a dict — a JSON array or scalar body returns None too (nothing to
    union fields from), same as a parse failure."""
    if not isinstance(raw, str) or not looks_like_json(raw):
        return None
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def flatten_one_level(obj: dict[str, Any]) -> dict[str, Any]:
    """One level of dot-notation flattening: a nested dict value's own
    keys become `parent.child`, replacing the parent key. A value nested
    two levels deep is left as a dict under its one-level key rather than
    flattened further."""
    flat: dict[str, Any] = {}
    for key, value in obj.items():
        if isinstance(value, dict):
            for subkey, subvalue in value.items():
                flat[f"{key}.{subkey}"] = subvalue
        else:
            flat[key] = value
    return flat


def infer_type(value: Any) -> str:
    """`bool` is checked before `int`/`float` since `bool` is a Python
    subclass of `int`."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return "string"


def _stringify_example(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value)
    if len(text) > EXAMPLE_MAX_LEN:
        return text[:EXAMPLE_MAX_LEN] + "..."
    return text


@dataclass
class FieldProfile:
    name: str
    present: int = 0
    from_map: bool = False  # ever seen as a top-level `map` key (index-time)
    has_value: bool = False
    first_value: Any = None
    all_same: bool = True

    def record(self, value: Any, *, from_map: bool) -> None:
        self.present += 1
        if from_map:
            self.from_map = True
        if not self.has_value:
            self.has_value = True
            self.first_value = value
        elif self.all_same and value != self.first_value:
            self.all_same = False


@dataclass
class SchemaReport:
    sample_size: int
    fields: list[dict[str, str]]  # rendered rows, in display order
    hint: str | None


def profile_sample(items: list[dict[str, Any]]) -> SchemaReport:
    """Profile a `messages`-result sample (`SearchJobResult.items`) into a
    `SchemaReport`. Pure function of the sample — no client calls."""
    n = len(items)
    profiles: dict[str, FieldProfile] = {}
    order: list[str] = []

    def get_profile(name: str) -> FieldProfile:
        if name not in profiles:
            profiles[name] = FieldProfile(name=name)
            order.append(name)
        return profiles[name]

    non_json_raw_texts: list[str] = []
    any_raw_parsed = False
    raw_present_rows = 0

    for item in items:
        m = item.get("map", {})
        for key, value in m.items():
            if value is None:
                continue
            get_profile(key).record(value, from_map=True)

        raw = m.get("_raw")
        if raw is not None:
            raw_present_rows += 1
        parsed = try_parse_raw(raw)
        if parsed is not None:
            any_raw_parsed = True
            for key, value in flatten_one_level(parsed).items():
                if value is None:
                    continue
                get_profile(key).record(value, from_map=False)
        elif isinstance(raw, str):
            non_json_raw_texts.append(raw)

    ordered_names = sorted(order, key=lambda name: (-profiles[name].present, name))

    rows: list[dict[str, str]] = []
    for name in ordered_names:
        prof = profiles[name]
        is_const = prof.all_same and prof.present >= 2
        if name == "_raw" and raw_present_rows > 0 and not any_raw_parsed:
            type_name = UNSTRUCTURED_TYPE
        else:
            type_name = infer_type(prof.first_value) if prof.has_value else ""
        rows.append({
            "FIELD": name,
            "PRESENT": f"{prof.present}/{n}",
            "TYPE": type_name,
            "CONST": "YES" if is_const else "no",
            "INDEX-TIME": "yes" if prof.from_map else "no",
            "EXAMPLE": _stringify_example(prof.first_value) if prof.has_value else "",
        })

    hint = _maybe_hint(non_json_raw_texts, raw_present_rows)
    return SchemaReport(sample_size=n, fields=rows, hint=hint)


def _maybe_hint(non_json_raw_texts: list[str], raw_present_rows: int) -> str | None:
    """`| parse regex` nudge: gated on `_raw` being non-JSON for most/all
    of the sample, then on whitespace-token-count being consistent across
    those non-JSON lines. Never attempts to synthesize an actual regex —
    just the nudge."""
    if raw_present_rows == 0 or not non_json_raw_texts:
        return None
    if len(non_json_raw_texts) / raw_present_rows < _UNSTRUCTURED_MAJORITY:
        return None  # source is predominantly JSON-bodied; no hint needed

    counts: dict[int, int] = {}
    for text in non_json_raw_texts:
        counts[len(text.split())] = counts.get(len(text.split()), 0) + 1
    modal_count, modal_freq = max(counts.items(), key=lambda kv: kv[1])

    m = len(non_json_raw_texts)
    if modal_freq / m < _TOKEN_COUNT_CONSISTENCY:
        return None  # token counts too inconsistent to suggest a starting point

    return (
        f"hint: _raw looks like fixed-format text (~{modal_count} "
        f"whitespace-separated tokens per line, consistent across "
        f"{modal_freq}/{m} rows) — consider '| parse regex' to extract fields"
    )


def render_schema_report(report: SchemaReport) -> str:
    table = formats.render_table(report.fields, list(TABLE_COLUMNS))
    if report.hint:
        return f"{table}\n\n{report.hint}"
    return table
