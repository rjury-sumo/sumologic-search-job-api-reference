# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
