# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-09-04

### Added

- `sumo_dashboard_client.py`: `list_dashboards()` — paginated
  `GET /v2/dashboards` (100/page, until the full viewable list is fetched),
  projecting each item down to `id`/`contentId`/`title`/`description`/
  `folderId`/`domain` (dropping `panels`/`layout`/`variables`/
  `topologyLabelMap`, which can be tens of KB per dashboard).
- `cli/dashboard_cache.py` — on-disk cache for that full pull, one file per
  instance+mode at `~/sumo-search/output/<instance>/dashboards/list-<mode>.json`,
  reused for 24h.
- `sumosearch discover dashboards` — client-side keyword/technology/
  business-service discovery over that list (`--grep`, `--mode all|mine`,
  `--limit`, `--no-cache`), since the endpoint has no server-side search
  param. No `created`/`modified` filter in this v1 — the list endpoint
  doesn't return those fields.

### Documentation

- Added a "Dashboard reports: export and discovery" section to the
  top-level README covering `sumo_dashboard_client.py` (added in 0.4.0
  but not yet documented at the README level): the two agentic use cases
  (export-for-visual-analysis, query-exemplar discovery), a three-path
  positioning table (`sumo_dashboard_client.py` / `sumosearch` CLI / Sumo
  MCP — MCP has no export or describe tool today), and a quickstart
  example. Added `docs/sumo-dashboard-client-reference.md`, mirroring
  `docs/sumo-search-client-reference.md`, for manual job control,
  variable/panel-override handling, configuration, and error types.

## [0.4.0] - 2026-09-04

### Added

- `sumo_dashboard_client.py` — a new standalone client (sibling to
  `sumo_search_client.py`, zero dependency on it beyond the pure
  `resolve_time()` helper) for the Sumo Logic Dashboard Report Job API:
  create -> poll -> fetch a PDF/PNG export of a dashboard.
- `sumosearch report run/describe/status/result/list/open/cleanup` — the
  CLI surface for the above. `run` fetches the dashboard first by default
  and merges its saved variable defaults into `variableValues` before
  submitting, since the report-job API (unlike its handling of the saved
  default time range) does not apply them on its own — omitting this
  silently breaks any panel referencing a `{{variable}}`. Files default to
  `~/sumo-search/output/<instance>/report/`; `list`/`open`/`cleanup` manage
  that directory. See `cli/README.md`'s `report run` section for the full
  flag reference and this gotcha.
- `cli/dashboard_describe.py` — pure `dashboard dict -> summary dict`
  functions backing `report describe`, at three levels (`summary`,
  `--panels`, `--queries`).
- `tests/integration_test_sumo_dashboard_client.py` — live-API integration
  tests for `sumo_dashboard_client.py`, mirroring
  `integration_test_sumo_search_client.py`'s style/conventions.

## [0.3.0] - 2026-09-04

### Added

- `sumosearch instance add/list/remove/show` and `sumosearch context
  set/show/unset` — named, kubectl-context-style instances (endpoint +
  optional description, persisted in `~/sumo-search/config.yaml`;
  credentials are never stored) for working across multiple Sumo Logic
  orgs/regions. Any command accepts `--instance NAME` to use one instance
  for that invocation, or `context set NAME` to make it the default.
  Credentials for a named instance are read from
  `SUMO_ACCESS_ID_<NAME>`/`SUMO_ACCESS_KEY_<NAME>` env vars
  (name uppercased, non-alphanumeric -> `_`); `--access-id`/`--access-key`/
  `--endpoint` still override everything. See `cli/README.md#multiple-instances`.
- `--endpoint` (on any `sumosearch` command, and on `instance add`) now
  also accepts a case-insensitive region alias (`us1`, `us2`, `au`, `ca`,
  `de`, `eu`, `fed`, `in`, `jp`, `kr`) in addition to a full endpoint URL.
- `cli` dependency group now also installs `pyyaml`, for the instance
  config file. `uv tool install` users need `--with pyyaml` alongside
  `--with typer` — the README's install snippet is updated accordingly.

### Documentation

- Documented the scan-ratio pitfall for automated/scheduled searches
  (window vs. run interval) and scheduled views as the fix for recurring
  overlapping queries, in the README and
  `skills/search-job-api-best-practices/SKILL.md`.
- Added `docs/workshop-log-search-journey.md`, a slide-content outline for
  a workshop on the log search journey and agentic Search Job API/MCP
  integration.

## [0.2.0] - 2026-08-31

### Added

- `sumosearch` — a shell/agent-oriented CLI (`cli/`) wrapping
  `sumo_search_client.py`: `search run/estimate/count`, `discover
  partitions/fers/views`, `schema`, `sample`, and `export`. Credentials are
  read from `SUMO_ACCESS_ID`/`SUMO_ACCESS_KEY`/`SUMO_ENDPOINT`, matching
  the Python client.
- csv/ndjson/json/table output formats, with agent-oriented defaults (csv
  for aggregate/`records` results, ndjson for raw/`messages` results),
  client-side field projection for raw-message output (`--fields`, a fixed
  envelope), `--drop-null-columns`, a stderr token-budget warning, and a
  hard `--max-tokens` truncation cap.
- `sumosearch schema` — profiles a query's field schema from a small
  sample (PRESENT/TYPE/CONST/INDEX-TIME/EXAMPLE per field) without
  relying on in-query auto-parsing.
- `sumosearch export` — bulk export straight to disk, time-splitting
  automatically via `time_split_search()` for results that would exceed
  the 100k raw-message per-job cap.
- Opt-in `cli` dependency group (`uv sync --group cli`) — not installed by
  the base `dev` group.

## [0.1.0] - 2026-08-31

### Added

- Initial release: `sumo_search_client.py`, a standalone reference client
  for the Sumo Logic Search Job API (create → poll → fetch → delete),
  covering rate limiting/retry, pagination, state-machine handling,
  large-export time-splitting, lookup-table detection, and pre-flight scan
  estimation.
- Unit test suite (`tests/test_sumo_search_client.py`) and a live-credential
  integration test (`tests/integration_test_sumo_search_client.py`).
- `skills/` — portable Agent Skills covering Search Job API best practices
  and Sumo Logic query authoring (scoping/cost efficiency, discovery,
  operator ordering, common query patterns, agent-friendly result shaping,
  scheduled views, indexes/partitions, and Cloud SIEM investigation).
