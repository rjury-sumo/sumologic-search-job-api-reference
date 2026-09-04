"""
cli/dashboard_cache.py — on-disk cache for `discover dashboards`'s full
dashboard-list pull.

`GET /v2/dashboards` has no server-side search param (see `discover
dashboards` in cli/main.py), so finding a dashboard by keyword means
pulling the caller's *entire* viewable list first and filtering
client-side. That full pull can take a while for a large org, but
dashboards don't change often and a caller will typically run several
`--grep` searches back to back — so the raw, unfiltered list from
`SumoDashboardClient.list_dashboards()` is cached to disk, one file per
(instance, mode) pair, and reused across calls within `DEFAULT_MAX_AGE_HOURS`
unless `--no-cache` forces a fresh pull. Filtering/`--limit` are never
cached — always applied fresh against whatever list (cached or freshly
pulled) is in hand.

Cache files live under `~/sumo-search/output/<instance>/dashboards/`,
alongside `cli/report_paths.py`'s own managed report directory (same
`output_root()`, imported from there rather than redefined).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cli.report_paths import output_root

DEFAULT_MAX_AGE_HOURS = 24.0


def cache_dir(instance_name: str) -> Path:
    return output_root() / instance_name.lower().strip() / "dashboards"


def cache_path(instance_name: str, mode: str) -> Path:
    return cache_dir(instance_name) / f"list-{mode}.json"


def read_cache(
    instance_name: str, mode: str, max_age_hours: float = DEFAULT_MAX_AGE_HOURS,
) -> list[dict] | None:
    """The cached dashboard list if a cache file exists and is younger than
    `max_age_hours`, else None (caller should pull fresh). A corrupt or
    unreadable cache file is treated the same as a missing one — cache
    reuse is a convenience, never a hard dependency."""
    path = cache_path(instance_name, mode)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text())
        fetched_at = datetime.fromisoformat(payload["fetched_at"])
    except (json.JSONDecodeError, OSError, KeyError, ValueError):
        return None
    if datetime.now(timezone.utc) - fetched_at > timedelta(hours=max_age_hours):
        return None
    return payload.get("dashboards")


def write_cache(instance_name: str, mode: str, dashboards: list[dict]) -> Path:
    path = cache_path(instance_name, mode)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "count": len(dashboards),
        "dashboards": dashboards,
    }
    path.write_text(json.dumps(payload))
    return path
