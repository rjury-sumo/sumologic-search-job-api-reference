"""
cli/main.py — sumosearch, a thin typer CLI over sumo_search_client.py.

Phase 1: `search run/estimate/count` and `discover partitions/fers/views`,
with csv/ndjson/json/table output formats, client-side field projection
for `messages` results, a stderr token-budget warning, `--max-tokens`
truncation, and `--drop-null-columns`. Phase 2 adds `schema` (profiling
logic lives in cli/schema.py; this module just wires up the command) and
`sample`. Phase 3 adds `export` — a file-output path that deliberately
skips the fixed-envelope/`--fields` projection (full `item["map"]` rows),
using `time_split_search()` under the hood once the estimated row count
would exceed the 100k raw-message per-job cap. See
docs/dev/agent-cli-analysis-and-plan.md.

Credentials are resolved from the environment (`SUMO_ACCESS_ID`,
`SUMO_ACCESS_KEY`, `SUMO_ENDPOINT`) by default, since this project
deliberately avoids requiring credentials as CLI flags (shell-history
exposure). `--access-id`/`--access-key`/`--endpoint` exist as an
override, not the primary path.
"""

from __future__ import annotations

import json
import math
import os

import typer

from cli import formats, instances
from cli import schema as schema_mod
from sumo_search_client import (
    SearchJobResult,
    SumoSearchClient,
    SumoSearchError,
    estimate_count,
    resolve_time,
    time_split_search,
)

app = typer.Typer(name="sumosearch", help="CLI for the Sumo Logic Search Job API.")
search_app = typer.Typer(help="Search job commands (run, estimate, count).")
discover_app = typer.Typer(
    help="Read-only discovery commands (partitions, fers, views); no search job created."
)
instance_app = typer.Typer(
    help="Manage named instances (endpoint + optional description; no credentials stored)."
)
context_app = typer.Typer(
    help="Get/set the current instance context — like kubectl's current-context."
)
app.add_typer(search_app, name="search")
app.add_typer(discover_app, name="discover")
app.add_typer(instance_app, name="instance")
app.add_typer(context_app, name="context")

_AUTO_PARSING_MODES = {"manual": "Manual", "autoparse": "AutoParse"}

# Safety margin below MAX_RAW_MESSAGES (100k) — matches time_split_search()'s
# own docstring guidance ("size interval_hours so no window exceeds ~80,000
# rows"). Above this estimated row count, `export` time-splits instead of
# running a single job.
EXPORT_SPLIT_THRESHOLD = 80_000

# Floor for auto-computed interval_hours — keeps a pathologically high
# estimated count (relative to the requested window) from computing an
# interval of a few seconds, which would mean thousands of tiny sequential
# jobs. A few minutes is still fine-grained enough to be useful.
EXPORT_MIN_INTERVAL_HOURS = 1 / 60  # 1 minute

EXPORT_FORMATS = ("csv", "ndjson", "json")


class Config:
    def __init__(self, access_id: str, access_key: str, endpoint: str):
        self.access_id = access_id
        self.access_key = access_key
        self.endpoint = endpoint


@app.callback()
def main(
    ctx: typer.Context,
    access_id: str | None = typer.Option(
        None, "--access-id",
        help="Sumo Logic access ID (default: SUMO_ACCESS_ID, or "
             "SUMO_ACCESS_ID_<INSTANCE> when --instance/a context is active).",
    ),
    access_key: str | None = typer.Option(
        None, "--access-key",
        help="Sumo Logic access key (default: SUMO_ACCESS_KEY, or "
             "SUMO_ACCESS_KEY_<INSTANCE> when --instance/a context is active).",
    ),
    endpoint: str | None = typer.Option(
        None, "--endpoint",
        help="Region alias (us1, us2, au, ca, de, eu, fed, in, jp, kr; "
             "case-insensitive) or a full endpoint URL, e.g. "
             "https://api.us2.sumologic.com (default: SUMO_ENDPOINT, or the "
             "active instance's stored endpoint).",
    ),
    instance: str | None = typer.Option(
        None, "--instance",
        help="Use this named instance for this command only, overriding any "
             "current context set via `sumosearch context set`. Credentials "
             "come from SUMO_ACCESS_ID_<INSTANCE>/SUMO_ACCESS_KEY_<INSTANCE>.",
    ),
) -> None:
    """sumosearch — CLI for the Sumo Logic Search Job API."""
    if ctx.invoked_subcommand in ("instance", "context"):
        return

    resolved_id, resolved_key, resolved_endpoint = access_id, access_key, endpoint

    active_instance = instance or instances.get_context()
    if active_instance and not (resolved_id and resolved_key and resolved_endpoint):
        record = instances.get_instance(active_instance)
        if record is None:
            typer.echo(
                f"Instance '{active_instance}' is not defined. Add it with "
                f"`sumosearch instance add {active_instance} --endpoint <endpoint>`, "
                "or clear the current context with `sumosearch context unset`.",
                err=True,
            )
            raise typer.Exit(code=1)
        suffix = instances.env_suffix(active_instance)
        resolved_id = resolved_id or os.environ.get(f"SUMO_ACCESS_ID_{suffix}")
        resolved_key = resolved_key or os.environ.get(f"SUMO_ACCESS_KEY_{suffix}")
        resolved_endpoint = resolved_endpoint or record["endpoint"]
    elif not active_instance:
        # No instance in play at all (not via --instance, not via a persisted
        # context) — only then do plain SUMO_ACCESS_ID/KEY/ENDPOINT apply.
        # When an instance *is* active, its own SUMO_ACCESS_ID_<NAME>/
        # SUMO_ACCESS_KEY_<NAME> are the only env-var fallback: silently
        # reusing the default identity for a different named instance would
        # defeat the point of naming it.
        resolved_id = resolved_id or os.environ.get("SUMO_ACCESS_ID")
        resolved_key = resolved_key or os.environ.get("SUMO_ACCESS_KEY")
        resolved_endpoint = resolved_endpoint or os.environ.get("SUMO_ENDPOINT")

    if resolved_endpoint:
        try:
            resolved_endpoint = instances.resolve_endpoint(resolved_endpoint)
        except ValueError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    missing = [
        name for name, value in (
            ("SUMO_ACCESS_ID", resolved_id),
            ("SUMO_ACCESS_KEY", resolved_key),
            ("SUMO_ENDPOINT", resolved_endpoint),
        )
        if not value
    ]
    if missing:
        instance_hint = ""
        if active_instance:
            suffix = instances.env_suffix(active_instance)
            instance_hint = (
                f" Instance '{active_instance}' is active — also checked "
                f"SUMO_ACCESS_ID_{suffix}/SUMO_ACCESS_KEY_{suffix}."
            )
        typer.echo(
            f"Missing required credentials: {', '.join(missing)}. Set them as "
            "environment variables, or pass --access-id/--access-key/--endpoint."
            f"{instance_hint}",
            err=True,
        )
        raise typer.Exit(code=1)
    ctx.obj = Config(access_id=resolved_id, access_key=resolved_key, endpoint=resolved_endpoint)


def _client(config: Config) -> SumoSearchClient:
    return SumoSearchClient(config.access_id, config.access_key, config.endpoint)


def _validate_format(output_format: str) -> None:
    if output_format not in formats.VALID_FORMATS:
        typer.echo(
            f"Unsupported --format '{output_format}': choose from "
            f"{', '.join(formats.VALID_FORMATS)}.",
            err=True,
        )
        raise typer.Exit(code=1)


def _render_output(
    result: SearchJobResult, rows: list[dict], columns: list[str], is_messages: bool, fmt: str,
) -> str:
    """Shared json/ndjson/csv/table dispatch for an already-projected
    `rows`/`columns` pair from a `messages`/`records` SearchJobResult —
    factored out of `search run` so `sample` can reuse it without also
    inheriting `--fields`/`--max-tokens`, which it doesn't take."""
    if fmt == "json":
        items = rows if is_messages else [formats.select_columns(r, columns) for r in rows]
        envelope = {
            "result_type": result.result_type,
            "total": result.total,
            "truncated": result.truncated,
            "items": items,
        }
        return json.dumps(envelope)
    if fmt == "ndjson":
        return formats.render_ndjson(rows, None if is_messages else columns)
    return formats.render_by_format(rows, columns, fmt)


def _warn_if_over_budget(output: str, no_warn: bool) -> None:
    if no_warn:
        return
    tokens = formats.estimate_tokens(output)
    if tokens > formats.DEFAULT_TOKEN_WARNING_THRESHOLD:
        typer.echo(
            f"warning: response is ~{tokens} tokens — consider an aggregate "
            "query, a tighter | limit, or --fields",
            err=True,
        )


# ---------------------------------------------------------------------------
# search run / estimate / count
# ---------------------------------------------------------------------------

@search_app.command("run")
def search_run(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Sumo Logic search query."),
    from_time: str = typer.Option(
        ..., "--from", help='Start time: epoch ms, "now", a relative expression '
             '("-1h"), or ISO 8601.',
    ),
    to_time: str = typer.Option(
        ..., "--to", help='End time: epoch ms, "now", a relative expression '
             '("-1h"), or ISO 8601.',
    ),
    output_format: str | None = typer.Option(
        None, "--format",
        help="csv|ndjson|json|table (default: csv for aggregate/records results, "
             "ndjson for raw/messages results).",
    ),
    fields: str | None = typer.Option(
        None, "--fields", help="Comma-separated field names to project/keep.",
    ),
    limit: int | None = typer.Option(None, "--limit", help="Cap on rows fetched."),
    aggregate: bool = typer.Option(
        False, "--aggregate", help="requiresRawMessages=False (mutually exclusive with --raw).",
    ),
    raw: bool = typer.Option(
        False, "--raw", help="requiresRawMessages=True (mutually exclusive with --aggregate).",
    ),
    auto_parsing: str | None = typer.Option(
        None, "--auto-parsing", help="manual|autoparse (case-insensitive).",
    ),
    max_tokens: int | None = typer.Option(
        None, "--max-tokens",
        help="Hard cap: drop whole trailing rows (messages/raw results only) "
             "until output is under this many estimated tokens.",
    ),
    drop_null_columns: bool = typer.Option(
        False, "--drop-null-columns",
        help="Drop columns that are null/empty across every row (records output only).",
    ),
    no_warn: bool = typer.Option(
        False, "--no-warn", help="Suppress the stderr token-budget warning.",
    ),
) -> None:
    """Run a search job (create -> poll -> fetch -> delete) and print the result."""
    if aggregate and raw:
        typer.echo("Cannot pass both --aggregate and --raw.", err=True)
        raise typer.Exit(code=1)

    requires_raw_messages = False if aggregate else (True if raw else None)

    auto_parsing_mode = None
    if auto_parsing is not None:
        key = auto_parsing.strip().lower()
        if key not in _AUTO_PARSING_MODES:
            typer.echo(
                f"Invalid --auto-parsing '{auto_parsing}': expected 'manual' or 'autoparse'.",
                err=True,
            )
            raise typer.Exit(code=1)
        auto_parsing_mode = _AUTO_PARSING_MODES[key]

    if output_format is not None:
        _validate_format(output_format)

    field_list = [f.strip() for f in fields.split(",") if f.strip()] if fields else None

    config: Config = ctx.obj
    client = _client(config)

    try:
        result = client.run_search(
            query, from_time, to_time,
            requires_raw_messages=requires_raw_messages,
            auto_parsing_mode=auto_parsing_mode,
            limit=limit,
        )
    except SumoSearchError as exc:
        typer.echo(f"Search failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    is_messages = result.result_type == "messages"
    fmt = output_format or ("ndjson" if is_messages else "csv")

    if is_messages:
        rows = [formats.project_message_row(item, field_list) for item in result.items]
    else:
        rows = [item.get("map", {}) for item in result.items]

    def render(subset: list[dict]) -> str:
        if is_messages:
            columns = formats.union_columns(subset, None)
        else:
            columns = formats.union_columns(subset, field_list)
            if drop_null_columns:
                columns = formats.drop_null_columns(subset, columns)
        return _render_output(result, subset, columns, is_messages, fmt)

    dropped = 0
    if max_tokens is not None and is_messages:
        rows, dropped = formats.truncate_to_token_budget(rows, render, max_tokens)

    output = render(rows)
    _warn_if_over_budget(output, no_warn)
    typer.echo(output)
    if dropped:
        typer.echo(
            f"note: dropped {dropped} of {len(result.items)} rows to stay under "
            f"--max-tokens {max_tokens}",
            err=True,
        )


@search_app.command("estimate")
def search_estimate(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Sumo Logic search query."),
    from_time: str = typer.Option(..., "--from", help="Start time (same forms as `search run`)."),
    to_time: str = typer.Option(..., "--to", help="End time (same forms as `search run`)."),
    output_format: str = typer.Option("table", "--format", help="csv|ndjson|json|table."),
) -> None:
    """Pre-flight scan-size estimate (estimate_scan()) — no search job created."""
    _validate_format(output_format)
    config: Config = ctx.obj
    client = _client(config)

    from_ms = int(resolve_time(from_time))
    to_ms = int(resolve_time(to_time))

    try:
        estimate = client.estimate_scan(query, from_ms, to_ms)
    except SumoSearchError as exc:
        typer.echo(f"Estimate failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    output = formats.render_estimate(estimate.total_bytes, estimate.partitions, output_format)
    typer.echo(output)


@search_app.command("count")
def search_count(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Sumo Logic search query (scope, not a full `| count`)."),
    from_time: str = typer.Option(..., "--from", help="Start time (same forms as `search run`)."),
    to_time: str = typer.Option(..., "--to", help="End time (same forms as `search run`)."),
) -> None:
    """Print a single row-count scalar (estimate_count()) — no --format."""
    config: Config = ctx.obj
    client = _client(config)

    try:
        total = estimate_count(client, query, from_time, to_time)
    except SumoSearchError as exc:
        typer.echo(f"Count failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(str(total))


# ---------------------------------------------------------------------------
# schema / sample
# ---------------------------------------------------------------------------

@app.command("schema")
def schema_cmd(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Sumo Logic search query to profile."),
    from_time: str = typer.Option(..., "--from", help="Start time (same forms as `search run`)."),
    to_time: str = typer.Option(..., "--to", help="End time (same forms as `search run`)."),
    n: int = typer.Option(50, "--n", help="Sample size, appended as `| limit N`."),
    auto_parsing: str = typer.Option(
        "manual", "--auto-parsing",
        help="manual|autoparse (case-insensitive). Default manual — keeps the "
             "index-time-vs-search-time distinction meaningful; AutoParse would "
             "pre-flatten JSON server-side and erase it.",
    ),
) -> None:
    """Profile a query's field schema from a small sample: PRESENT/TYPE/CONST/INDEX-TIME/EXAMPLE
    per field, unioning top-level `map` keys with client-side JSON-parsed `_raw` keys."""
    key = auto_parsing.strip().lower()
    if key not in _AUTO_PARSING_MODES:
        typer.echo(
            f"Invalid --auto-parsing '{auto_parsing}': expected 'manual' or 'autoparse'.",
            err=True,
        )
        raise typer.Exit(code=1)
    auto_parsing_mode = _AUTO_PARSING_MODES[key]

    config: Config = ctx.obj
    client = _client(config)

    try:
        result = client.run_search(
            f"{query} | limit {n}", from_time, to_time,
            auto_parsing_mode=auto_parsing_mode,
        )
    except SumoSearchError as exc:
        typer.echo(f"Schema profiling failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    report = schema_mod.profile_sample(result.items)
    typer.echo(schema_mod.render_schema_report(report))


@app.command("sample")
def sample_cmd(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Sumo Logic search query to sample."),
    from_time: str = typer.Option(..., "--from", help="Start time (same forms as `search run`)."),
    to_time: str = typer.Option(..., "--to", help="End time (same forms as `search run`)."),
    n: int = typer.Option(20, "--n", help="Sample size, appended as `| limit N`."),
    output_format: str | None = typer.Option(
        None, "--format",
        help="csv|ndjson|json|table (default: csv for records results, ndjson for messages).",
    ),
    drop_null_columns: bool = typer.Option(
        False, "--drop-null-columns",
        help="Drop columns that are null/empty across every row (records output only).",
    ),
) -> None:
    """Run `<query> | limit N` and print the raw sample, via the same rendering path as
    `search run` (no --fields/--max-tokens/--aggregate/--raw/--auto-parsing)."""
    if output_format is not None:
        _validate_format(output_format)

    config: Config = ctx.obj
    client = _client(config)

    try:
        result = client.run_search(f"{query} | limit {n}", from_time, to_time)
    except SumoSearchError as exc:
        typer.echo(f"Sample failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    is_messages = result.result_type == "messages"
    fmt = output_format or ("ndjson" if is_messages else "csv")

    if is_messages:
        rows = [formats.project_message_row(item, None) for item in result.items]
        columns = formats.union_columns(rows, None)
    else:
        rows = [item.get("map", {}) for item in result.items]
        columns = formats.union_columns(rows, None)
        if drop_null_columns:
            columns = formats.drop_null_columns(rows, columns)

    typer.echo(_render_output(result, rows, columns, is_messages, fmt))


# ---------------------------------------------------------------------------
# instance / context
# ---------------------------------------------------------------------------

@instance_app.command("add")
def instance_add(
    name: str = typer.Argument(..., help="Instance name, e.g. 'demo'."),
    endpoint: str = typer.Option(
        ..., "--endpoint",
        help="Region alias (us1, us2, au, ca, de, eu, fed, in, jp, kr; "
             "case-insensitive) or a full endpoint URL.",
    ),
    description: str | None = typer.Option(
        None, "--description", help="Optional free-text description.",
    ),
) -> None:
    """Add (or overwrite) a named instance. Stores endpoint + description only
    — never credentials. Auth for this instance comes from
    SUMO_ACCESS_ID_<NAME>/SUMO_ACCESS_KEY_<NAME> env vars, or --access-id/
    --access-key."""
    try:
        record = instances.add_instance(name, endpoint, description)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"instance '{name}' -> {record['endpoint']}")


@instance_app.command("list")
def instance_list() -> None:
    """List configured instances; '*' marks the current context."""
    rows = instances.list_instances()
    if not rows:
        typer.echo("No instances configured.")
        return
    current = instances.get_context()
    for name, record in rows.items():
        marker = "*" if name == current else " "
        desc = f" — {record['description']}" if record.get("description") else ""
        typer.echo(f"{marker} {name}\t{record['endpoint']}{desc}")


@instance_app.command("remove")
def instance_remove(name: str = typer.Argument(..., help="Instance name to remove.")) -> None:
    """Remove a named instance. Also clears the current context if it pointed at it."""
    if not instances.remove_instance(name):
        typer.echo(f"Instance '{name}' not found.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"removed instance '{name}'")


@instance_app.command("show")
def instance_show(name: str = typer.Argument(..., help="Instance name to show.")) -> None:
    record = instances.get_instance(name)
    if record is None:
        typer.echo(f"Instance '{name}' not found.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"name: {name}")
    typer.echo(f"endpoint: {record['endpoint']}")
    if record.get("description"):
        typer.echo(f"description: {record['description']}")
    typer.echo(f"current: {name == instances.get_context()}")


@context_app.command("set")
def context_set(
    name: str = typer.Argument(..., help="Instance name to make current, or 'none' to clear."),
) -> None:
    """Persist the current instance context, used by default when no
    --instance/--access-id/--access-key/--endpoint is passed."""
    if name.lower() == "none":
        instances.unset_context()
        typer.echo("context cleared (using default SUMO_ACCESS_ID/SUMO_ACCESS_KEY/SUMO_ENDPOINT)")
        return
    if not instances.set_context(name):
        typer.echo(
            f"Instance '{name}' not found. Add it first with "
            f"`sumosearch instance add {name} --endpoint <endpoint>`.",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"context set to '{name}'")


@context_app.command("show")
def context_show() -> None:
    """Print the current instance context, or that none is set."""
    current = instances.get_context()
    if current is None:
        typer.echo("no context set (using default SUMO_ACCESS_ID/SUMO_ACCESS_KEY/SUMO_ENDPOINT)")
        return
    record = instances.get_instance(current)
    if record is None:
        typer.echo(
            f"context is set to '{current}' but that instance no longer exists; "
            "clear it with `sumosearch context unset`",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"{current}\t{record['endpoint']}")


@context_app.command("unset")
def context_unset() -> None:
    """Clear the current instance context (equivalent to `context set none`)."""
    instances.unset_context()
    typer.echo("context cleared (using default SUMO_ACCESS_ID/SUMO_ACCESS_KEY/SUMO_ENDPOINT)")


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

@app.command("export")
def export_cmd(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Sumo Logic search query."),
    from_time: str = typer.Option(..., "--from", help="Start time (same forms as `search run`)."),
    to_time: str = typer.Option(..., "--to", help="End time (same forms as `search run`)."),
    output_format: str = typer.Option(..., "--format", help="csv|ndjson|json (no table — file output)."),
    out: str = typer.Option(..., "--out", help="Output file path."),
    interval_hours: float | None = typer.Option(
        None, "--interval-hours",
        help="Override the auto-computed time-split window size (hours). Only "
             "relevant when the estimated row count exceeds the split threshold.",
    ),
) -> None:
    """Bulk export straight to disk — full, untrimmed `item['map']` rows (no
    fixed-envelope/`--fields` projection, unlike `search run`/`sample`).
    Uses time_split_search() under the hood when the estimated row count
    would exceed the 100k raw-message per-job cap. Never prints exported
    data to stdout — only a one-line completion summary."""
    if output_format not in EXPORT_FORMATS:
        typer.echo(
            f"Unsupported --format '{output_format}' for export: choose from "
            f"{', '.join(EXPORT_FORMATS)}.",
            err=True,
        )
        raise typer.Exit(code=1)

    config: Config = ctx.obj
    client = _client(config)

    from_ms = int(resolve_time(from_time))
    to_ms = int(resolve_time(to_time))

    try:
        estimated = estimate_count(client, query, from_time, to_time)
    except SumoSearchError as exc:
        typer.echo(f"Export failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if estimated <= EXPORT_SPLIT_THRESHOLD:
        try:
            result = client.run_search(query, from_time, to_time, limit=None)
        except SumoSearchError as exc:
            typer.echo(f"Export failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        rows = [item.get("map", {}) for item in result.items]
        summary_suffix = "single job, no time-splitting needed"
    else:
        total_window_hours = (to_ms - from_ms) / 3_600_000
        if interval_hours is not None:
            effective_interval_hours = interval_hours
        else:
            effective_interval_hours = max(
                total_window_hours * (EXPORT_SPLIT_THRESHOLD / estimated),
                EXPORT_MIN_INTERVAL_HOURS,
            )
        try:
            items = time_split_search(
                client, query, from_ms, to_ms,
                interval_hours=effective_interval_hours, result_type="messages",
            )
        except ValueError as exc:
            typer.echo(
                f"Export failed: {exc}. Try a smaller explicit --interval-hours.",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        except SumoSearchError as exc:
            typer.echo(f"Export failed: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        rows = [item.get("map", {}) for item in items]
        windows = max(1, math.ceil(total_window_hours / effective_interval_hours))
        summary_suffix = f"{windows} time windows"

    columns = formats.union_columns(rows, None)
    if output_format == "csv":
        content = formats.render_csv(rows, columns)
    elif output_format == "ndjson":
        content = formats.render_ndjson(rows, None)
    else:
        content = formats.render_json(rows, None)

    with open(out, "w") as f:
        f.write(content)
        if content:
            f.write("\n")

    typer.echo(f"wrote {len(rows)} rows to {out} ({summary_suffix})")


# ---------------------------------------------------------------------------
# discover partitions / fers / views
# ---------------------------------------------------------------------------

def _grep_filter(rows: list[dict], keyword: str | None, match_keys: list[str]) -> list[dict]:
    if not keyword:
        return rows
    needle = keyword.lower()
    filtered = []
    for row in rows:
        for key in match_keys:
            value = row.get(key)
            if value and needle in str(value).lower():
                filtered.append(row)
                break
    return filtered


def _emit_rows(rows: list[dict], output_format: str) -> None:
    _validate_format(output_format)
    columns = formats.union_columns(rows, None)
    output = formats.render_by_format(rows, columns, output_format)
    typer.echo(output)
    _warn_if_over_budget(output, no_warn=False)


@discover_app.command("partitions")
def discover_partitions(
    ctx: typer.Context,
    grep: str | None = typer.Option(
        None, "--grep", help="Case-insensitive substring filter on name/routingExpression.",
    ),
    output_format: str = typer.Option("csv", "--format", help="csv|ndjson|json|table."),
) -> None:
    """List partitions (list_partitions()) — no search job created."""
    config: Config = ctx.obj
    client = _client(config)
    try:
        rows = client.list_partitions()
    except SumoSearchError as exc:
        typer.echo(f"Failed to list partitions: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    rows = _grep_filter(rows, grep, ["name", "routingExpression"])
    _emit_rows(rows, output_format)


@discover_app.command("fers")
def discover_fers(
    ctx: typer.Context,
    grep: str | None = typer.Option(
        None, "--grep",
        help="Case-insensitive substring filter on name/parseExpression/fieldNames.",
    ),
    output_format: str = typer.Option("csv", "--format", help="csv|ndjson|json|table."),
) -> None:
    """List field extraction rules (list_extraction_rules()) — no search job created."""
    config: Config = ctx.obj
    client = _client(config)
    try:
        rows = client.list_extraction_rules()
    except SumoSearchError as exc:
        typer.echo(f"Failed to list extraction rules: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    rows = _grep_filter(rows, grep, ["name", "parseExpression", "fieldNames"])
    _emit_rows(rows, output_format)


@discover_app.command("views")
def discover_views(
    ctx: typer.Context,
    grep: str | None = typer.Option(
        None, "--grep", help="Case-insensitive substring filter on indexName/query.",
    ),
    output_format: str = typer.Option("csv", "--format", help="csv|ndjson|json|table."),
) -> None:
    """List scheduled views (list_scheduled_views()) — no search job created."""
    config: Config = ctx.obj
    client = _client(config)
    try:
        rows = client.list_scheduled_views()
    except SumoSearchError as exc:
        typer.echo(f"Failed to list scheduled views: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    rows = _grep_filter(rows, grep, ["indexName", "query"])
    _emit_rows(rows, output_format)


if __name__ == "__main__":
    app()
