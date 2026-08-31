"""
cli/main.py — sumosearch, a thin typer CLI over sumo_search_client.py.

Phase 0: one command (`sumosearch search run`), `--format json` only.
Later phases add `search estimate`/`search count`, `discover`, `schema`,
`sample`, `export`, and additional output formats — see
docs/dev/agent-cli-analysis-and-plan.md.

Credentials are resolved from the environment (`SUMO_ACCESS_ID`,
`SUMO_ACCESS_KEY`, `SUMO_ENDPOINT`) by default, since this project
deliberately avoids requiring credentials as CLI flags (shell-history
exposure). `--access-id`/`--access-key`/`--endpoint` exist as an
override, not the primary path.
"""

from __future__ import annotations

import json

import typer

from sumo_search_client import SumoSearchClient, SumoSearchError

app = typer.Typer(name="sumosearch", help="CLI for the Sumo Logic Search Job API.")
search_app = typer.Typer(help="Search job commands.")
app.add_typer(search_app, name="search")


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
    output_format: str = typer.Option(
        "json", "--format", help="Output format (only 'json' is supported in this phase).",
    ),
) -> None:
    """Run a search job (create -> poll -> fetch -> delete) and print the result."""
    if output_format != "json":
        typer.echo(
            f"Unsupported --format '{output_format}': only 'json' is supported in this phase.",
            err=True,
        )
        raise typer.Exit(code=1)

    config: Config = ctx.obj
    client = SumoSearchClient(config.access_id, config.access_key, config.endpoint)

    try:
        result = client.run_search(query, from_time, to_time)
    except SumoSearchError as exc:
        typer.echo(f"Search failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    envelope = {
        "result_type": result.result_type,
        "total": result.total,
        "truncated": result.truncated,
        "items": result.items,
    }
    typer.echo(json.dumps(envelope))


if __name__ == "__main__":
    app()
