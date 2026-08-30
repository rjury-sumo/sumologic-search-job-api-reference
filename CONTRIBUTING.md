# Contributing

## Dev setup

This project uses [uv](https://docs.astral.sh/uv/).

```bash
uv sync --group dev   # installs requests + pytest + ruff into ./.venv
```

## Running tests

```bash
uv run pytest                # unit tests — no credentials, no network
uv run pytest -k retry       # run a subset by keyword
```

`tests/integration_test_sumo_search_client.py` exercises the full
create → poll → fetch → delete lifecycle against a real Sumo Logic org and
requires `SUMO_ACCESS_ID` / `SUMO_ACCESS_KEY`. It is not part of the unit
test suite and does not run in CI — see its module docstring for details.

```bash
uv run python tests/integration_test_sumo_search_client.py
uv run python tests/integration_test_sumo_search_client.py --dry-run
```

## Linting

```bash
uv run ruff check .
uv run ruff format .
```

## Pull requests

- Keep `sumo_search_client.py` a single, dependency-light file (`requests`
  only) — it's meant to be copied into other projects wholesale.
- Add or update unit tests for any behavior change in the client.
- If a change affects a rule described in
  `skills/search-job-api-best-practices/SKILL.md`, update that skill too so
  the code and the documented rationale stay in sync.
- Run `uv run pytest` and `uv run ruff check .` before opening a PR.
