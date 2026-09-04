"""
test_dashboard_cache.py — unit tests for cli/dashboard_cache.py.

No credentials, no network. Isolates cli.instances.CONFIG_DIR (which
cli.report_paths.output_root() — and therefore this module — reads
fresh on every call) at a per-test tmp_path, same mechanism
tests/test_cli.py uses.

Run:
    uv run pytest tests/test_dashboard_cache.py
"""

from __future__ import annotations

import json as jsonlib
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("yaml")

import cli.dashboard_cache as dc  # noqa: E402
import cli.instances as instances_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated_config_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(instances_mod, "CONFIG_DIR", tmp_path / "sumo-search")


def test_read_cache_missing_file_returns_none():
    assert dc.read_cache("default", "all") is None


def test_write_then_read_cache_round_trips():
    rows = [{"id": "1", "title": "a"}, {"id": "2", "title": "b"}]
    dc.write_cache("default", "all", rows)
    assert dc.read_cache("default", "all") == rows


def test_cache_is_scoped_per_instance():
    dc.write_cache("prod", "all", [{"id": "1"}])
    assert dc.read_cache("demo", "all") is None


def test_cache_is_scoped_per_mode():
    dc.write_cache("default", "all", [{"id": "1"}])
    assert dc.read_cache("default", "mine") is None


def test_read_cache_expired_returns_none():
    rows = [{"id": "1"}]
    dc.write_cache("default", "all", rows)
    path = dc.cache_path("default", "all")
    payload = jsonlib.loads(path.read_text())
    stale = datetime.now(timezone.utc) - timedelta(hours=dc.DEFAULT_MAX_AGE_HOURS + 1)
    payload["fetched_at"] = stale.isoformat()
    path.write_text(jsonlib.dumps(payload))
    assert dc.read_cache("default", "all") is None


def test_read_cache_respects_custom_max_age():
    rows = [{"id": "1"}]
    dc.write_cache("default", "all", rows)
    path = dc.cache_path("default", "all")
    payload = jsonlib.loads(path.read_text())
    payload["fetched_at"] = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    path.write_text(jsonlib.dumps(payload))
    assert dc.read_cache("default", "all", max_age_hours=1) is None
    assert dc.read_cache("default", "all", max_age_hours=3) == rows


def test_read_cache_corrupt_json_returns_none():
    path = dc.cache_path("default", "all")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not json")
    assert dc.read_cache("default", "all") is None


def test_write_cache_creates_parent_dirs():
    path = dc.write_cache("default", "all", [{"id": "1"}])
    assert path.is_file()
    assert path.parent.name == "dashboards"
