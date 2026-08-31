"""
cli/main.py — sumosearch, a thin typer CLI over sumo_search_client.py.

Phase 1: `search run/estimate/count` and `discover partitions/fers/views`,
with csv/ndjson/json/table output formats, client-side field projection
for `messages` results, a stderr token-budget warning, `--max-tokens`
truncation, and `--drop-null-columns`. Later phases add `schema`,
`sample`, and `export` — see docs/dev/agent-cli-analysis-and-plan.md.

Credentials are resolved from the environment (`SUMO_ACCESS_ID`,
`SUMO_ACCESS_KEY`, `SUMO_ENDPOINT`) by default, since this project
deliberately avoids requiring credentials as CLI flags (shell-history
exposure). `--access-id`/`--access-key`/`--endpoint` exist as an
override, not the primary path.
"""

from __future__ import annotations

import json

import typer

from cli import formats
from sumo_search_client import (
    SumoSearchClient,
    SumoSearchError,
    estimate_count,
    resolve_time,
)

app = typer.Typer(name="sumosearch", help="CLI for the Sumo Logic Search Job API.")
search_app = typer.Typer(help="Search job commands.")
discover_app = typer.Typer(help="Read-only discovery commands (no search job created).")
app.add_typer(search_app, name="search")
app.add_typer(discover_app, name="discover")

_AUTO_PARSING_MODES = {"manual": "Manual", "autoparse": "AutoParse"}


class Config:
    def __init__(self, access_id: str, access_key: str, endpoint: str):
        self.access_id = access_id
        self.access_key = access_key
        self.endpoint = endpoint


@app.callback()
def main(
    ctx: typer.Context,
    access_id: str | None = typer.Option(
        None, "--access-id", envvar="SUMO_ACCESS_ID",
        help="Sumo Logic access ID (default: SUMO_ACCESS_ID env var).",
    ),
    access_key: str | None = typer.Option(
        None, "--access-key", envvar="SUMO_ACCESS_KEY",
        help="Sumo Logic access key (default: SUMO_ACCESS_KEY env var).",
    ),
    endpoint: str | None = typer.Option(
        None, "--endpoint", envvar="SUMO_ENDPOINT",
        help="Sumo Logic API endpoint, e.g. https://api.us2.sumologic.com "
             "(default: SUMO_ENDPOINT env var).",
    ),
) -> None:
    """sumosearch — CLI for the Sumo Logic Search Job API."""
    missing = [
        name for name, value in (
            ("SUMO_ACCESS_ID", access_id),
            ("SUMO_ACCESS_KEY", access_key),
            ("SUMO_ENDPOINT", endpoint),
        )
        if not value
    ]
    if missing:
        typer.echo(
            f"Missing required credentials: {', '.join(missing)}. Set them as "
            "environment variables, or pass --access-id/--access-key/--endpoint.",
            err=True,
        )
        raise typer.Exit(code=1)
    ctx.obj = Config(access_id=access_id, access_key=access_key, endpoint=endpoint)


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

        if fmt == "json":
            items = subset if is_messages else [formats.select_columns(r, columns) for r in subset]
            envelope = {
                "result_type": result.result_type,
                "total": result.total,
                "truncated": result.truncated,
                "items": items,
            }
            return json.dumps(envelope)
        if fmt == "ndjson":
            return formats.render_ndjson(subset, None if is_messages else columns)
        return formats.render_by_format(subset, columns, fmt)

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
