"""
cli/report_paths.py — output-file naming and management for `report run`/
`report result`.

Files are written under `~/sumo-search/output/<instance>/report/`, alongside
`cli/instances.py`'s own `~/sumo-search/config.yaml` (`CONFIG_DIR` is
imported from there, not redefined, so both stay in sync). This is the
first managed output directory in this CLI — everything else (`export`)
writes only to an explicit `--out` path — so `report` also always accepts
an explicit `--out`/`--output` override; the managed directory is purely a
convenience default.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cli import instances

REPORT_EXTENSIONS = (".pdf", ".png")

_DURATION_RE = re.compile(r"^(\d+)(d|h|m)$")
_DURATION_UNITS = {"d": "days", "h": "hours", "m": "minutes"}


def output_root() -> Path:
    """`cli.instances.CONFIG_DIR / "output"`, read fresh on every call (not
    cached at import time) so tests can redirect `instances.CONFIG_DIR` via
    monkeypatch — same isolation mechanism `tests/test_cli.py`'s
    `_isolated_instances_config` fixture already uses for `~/sumo-search/config.yaml`."""
    return instances.CONFIG_DIR / "output"


def report_dir(instance_name: str) -> Path:
    return output_root() / instance_name.lower().strip() / "report"


def slug(text: str, maxlen: int = 40) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())[:maxlen].strip()
    return re.sub(r"[\s_-]+", "-", s).strip("-") or "dashboard"


def default_output_path(instance_name: str, id_for_name: str, title: str | None, ext: str) -> Path:
    """`<report_dir>/<UTC timestamp>_<slug or id[:24]>.<ext>`. `title` is the
    dashboard title when known (`report run`); `report result` has no
    dashboard context, so it passes `title=None` and the job id is used
    verbatim instead of being slugged."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    name = slug(title) if title else id_for_name[:24]
    return report_dir(instance_name) / f"{ts}_{name}.{ext}"


def all_report_files() -> list[Path]:
    """Every `.pdf`/`.png` under any instance's report dir, sorted newest-first."""
    root = output_root()
    if not root.is_dir():
        return []
    files = [
        f
        for instance_dir in root.iterdir()
        if instance_dir.is_dir()
        for f in (instance_dir / "report").glob("*")
        if f.is_file() and f.suffix.lower() in REPORT_EXTENSIONS
    ]
    return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)


def instance_from_path(path: Path) -> str:
    """Recover the instance name from a path under output_root() — the
    parent of the `report/` directory the file lives in."""
    return path.parent.parent.name


def parse_cleanup_duration(spec: str) -> timedelta:
    m = _DURATION_RE.match(spec.strip().lower())
    if not m:
        raise ValueError(f"--older-than must look like '30d', '12h', or '90m', got: {spec!r}")
    amount, unit = m.groups()
    return timedelta(**{_DURATION_UNITS[unit]: int(amount)})
