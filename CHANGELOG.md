# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
