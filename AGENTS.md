# Agent instructions — sumologic-search-job-api-reference

Standalone, customer-distributable reference: a Search Job API client +
portable Agent Skills. Two audiences: (1) engineers building on the client,
(2) end users/SIEM users using `skills/`. Everything here must work when
copied out of this repo with zero other context — no ties to any parent
repo, internal CLI, or specific harness.

## Routing: how to run a search or dashboard report

A request like "run a search for X in Sumo Logic" or "export this dashboard"
is ambiguous — there are three independent execution paths available in
this repo, and nothing in the phrasing tells you which one the user wants:

1. **Sumo's official MCP server** (`runSearchJob` and related tools) — used
   when an MCP server for Sumo Logic is connected in the session.
2. **`sumosearch` CLI** (`cli/`) — the agent-oriented CLI, installed as a
   `uv tool`. **This is the default route in this repo.**
3. **Reference Python clients** (`sumo_search_client.py`,
   `sumo_dashboard_client.py`) — direct library usage. These are reference
   implementations for users building their *own* projects; they are
   unlikely to be invoked directly to satisfy an ad hoc search/report
   request made in this repo's own sessions.

Resolution order:
- If the user states a route inline for a single request ("using the Sumo
  MCP", "via sumosearch cli", "using the python client directly"), honor
  it for that request only — don't change the session default.
- If the user sets a preference for the session ("for this session default
  to the MCP"), remember it and use it for subsequent unqualified requests
  in that session.
- Otherwise, default to the `sumosearch` CLI.
- If still ambiguous (e.g. a stated session default conflicts with the
  request, or neither applies cleanly), ask rather than guessing.

### `sumosearch` CLI: use vs. dev/test

These are different invocations — don't conflate them:

- **Use** (running a real search/report for the user): call the installed
  console-script directly, `sumosearch ...`. Do not prefix with `uv run`
  for this — that's the dev workflow, not the end-user one.
- **Dev/test** (changing code under `cli/`, running `tests/test_cli.py`,
  iterating on `cli/main.py`, etc.): use `uv run sumosearch ...` /
  `uv run pytest tests/test_cli.py` against the local source tree, per
  the dev workflow in `cli/README.md`.
- If a `cli/` change bumps the package version in `pyproject.toml`,
  reinstall the tool so `sumosearch` on PATH matches:
  `uv tool install . --with typer --with pyyaml --force` (see
  `cli/README.md` / root `README.md` Quickstart for the exact flags this
  repo requires).

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
- `.claude/skills` is a symlink to `../skills` (not a copy), so Claude
  Code auto-discovers every skill here as a project skill. It exists
  purely for in-repo discoverability — `skills/` stays the single,
  harness-agnostic source of truth for copy-paste distribution. New
  skills only need a folder under `skills/<name>/SKILL.md`; the symlink
  picks them up with no extra wiring. Don't turn `.claude/skills` into a
  real directory or duplicate content into it.
- **Org-specific generated content never goes in `skills/`.**
  `skills/log-domain-skill-authoring/SKILL.md` researches one log
  technology in one Sumo Logic instance and writes real
  `_sourceCategory`/`_index` values to
  `~/sumo-search/output/<instance>/skills/<domain-slug>/SKILL.md` (read
  back by `skills/discovery-log-domains`) — outside this repo entirely,
  same directory the CLI already uses for its dashboard-list cache. If a
  future change is tempted to commit an example of that output into
  `skills/` for documentation purposes, sanitize it first (placeholder
  metadata, not a real org's values) or keep it out — real per-instance
  output must never land in a commit.
- `.claude/agents/log-domain-discovery.md` is a Claude-Code-specific
  subagent (like `.claude/skills`, not part of the portable `skills/`
  set) pre-scoped to the `log-domain-skill-authoring` workflow above —
  bounded to one technology/instance per run, output confined to the
  per-instance directory.

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
