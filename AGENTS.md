# Agent instructions — sumologic-search-job-api-reference

Standalone, customer-distributable reference: a Search Job API client +
portable Agent Skills. Two audiences: (1) engineers building on the client,
(2) end users/SIEM users using `skills/`. Everything here must work when
copied out of this repo with zero other context — no ties to any parent
repo, internal CLI, or specific harness.

## Layout & invariants

- `sumo_search_client.py` — single file, single dependency (`requests`).
  Copy-paste distribution model for consumers: they take this one file,
  not `pip install` the repo. Don't split it into a package or add a
  `src/` layout.
- `cli/` — the `sumosearch` agent-oriented CLI (see
  `docs/dev/agent-cli-analysis-and-plan.md`). A separate installable
  package that imports `sumo_search_client.py`; it's why
  `tool.uv.package = true` (needed for the `sumosearch` console-script
  entry point). This does not change the copy-paste distribution model
  above — `sumo_search_client.py` still stands alone with zero `cli/`
  dependency.
- `tests/test_sumo_search_client.py` — unit tests, no credentials/network.
  Must always pass; this is what CI runs.
- `tests/test_cli.py` — unit tests for `cli/`, same no-credentials/no-network
  constraint. Needs `uv sync --group dev --group cli` (the base `--group dev`
  alone doesn't install `typer`).
- `tests/integration_test_sumo_search_client.py` — needs live
  `SUMO_ACCESS_ID`/`SUMO_ACCESS_KEY`. Not pytest-collected (script style,
  run directly). Never wire this into CI.
- `skills/` — Agent Skills (YAML frontmatter `SKILL.md`). Must read the
  same whether driven through `sumo_search_client.py` or Sumo's official
  `runSearchJob` MCP tool — never bake this client's specific API surface
  into skill content. Relative links inside `skills/` must resolve within
  this repo only.

## Workflow

```bash
uv sync --group dev
uv run pytest tests/test_sumo_search_client.py   # must pass, 0 creds
uv run ruff check .                              # must be clean
```

- A behavior change in `sumo_search_client.py` that reflects a documented
  rule → update `skills/search-job-api-best-practices/SKILL.md` in the
  same change so code and rationale stay in sync.
- Update `CHANGELOG.md` (Keep a Changelog format) for user-visible changes.
- If porting content from elsewhere, grep it for monorepo/CLI-specific
  references (paths like `../../docs/`, a `sumo` CLI, `cli/*.py`) before
  merging — this repo must stand alone.

## Style

Reference implementation, not a production framework: no speculative
abstractions, no features beyond what's asked, no comments that just
restate the code. Keep changes surgical and match existing patterns in
`sumo_search_client.py` (dataclasses, explicit exceptions, docstrings that
explain *why*, not what).
