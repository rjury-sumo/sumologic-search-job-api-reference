"""
cli/instances.py — named-instance config (endpoint + description) and the
persisted "current context" (which named instance is active by default),
kubectl-style. Credentials are never stored here: only endpoint/description
go in ~/sumo-search/config.yaml, and auth for a named instance is always
resolved from SUMO_ACCESS_ID_<INSTANCE>/SUMO_ACCESS_KEY_<INSTANCE> env vars
at the point of use (cli/main.py's `main()` callback), so a leaked config
file never leaks credentials.

REGION_ALIASES lets `--endpoint`/`instance add --endpoint` accept either a
short region alias (case-insensitive) or a full https:// endpoint URL.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

CONFIG_DIR = Path.home() / "sumo-search"

REGION_ALIASES: dict[str, str] = {
    "us1": "https://api.sumologic.com",
    "us2": "https://api.us2.sumologic.com",
    "au": "https://api.au.sumologic.com",
    "ca": "https://api.ca.sumologic.com",
    "de": "https://api.de.sumologic.com",
    "eu": "https://api.eu.sumologic.com",
    "fed": "https://api.fed.sumologic.com",
    "in": "https://api.in.sumologic.com",
    "jp": "https://api.jp.sumologic.com",
    "kr": "https://api.kr.sumologic.com",
}


def resolve_endpoint(value: str) -> str:
    """Normalize a region alias (e.g. `us2`, case-insensitive) or a full
    https:// endpoint URL to a full endpoint URL. Raises ValueError for
    anything else."""
    alias = REGION_ALIASES.get(value.strip().lower())
    if alias:
        return alias
    if value.startswith("http://") or value.startswith("https://"):
        return value.rstrip("/")
    raise ValueError(
        f"Unrecognized endpoint '{value}': expected a region alias "
        f"({', '.join(sorted(REGION_ALIASES))}) or a full https:// URL."
    )


def env_suffix(name: str) -> str:
    """`demo` -> `DEMO`, `us2-prod` -> `US2_PROD` — the suffix appended to
    SUMO_ACCESS_ID_/SUMO_ACCESS_KEY_ for a named instance's env vars."""
    return re.sub(r"[^A-Za-z0-9]", "_", name).upper()


def _config_path() -> Path:
    return CONFIG_DIR / "config.yaml"


def _load() -> dict:
    path = _config_path()
    if not path.exists():
        return {"instances": {}, "current_context": None}
    with open(path) as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("instances", {})
    data.setdefault("current_context", None)
    return data


def _save(data: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(_config_path(), "w") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def add_instance(name: str, endpoint: str, description: str | None = None) -> dict:
    """Add or overwrite (idempotent) a named instance. Returns the stored record."""
    resolved = resolve_endpoint(endpoint)
    data = _load()
    record = {"endpoint": resolved, "description": description}
    data["instances"][name] = record
    _save(data)
    return record


def remove_instance(name: str) -> bool:
    data = _load()
    if name not in data["instances"]:
        return False
    del data["instances"][name]
    if data.get("current_context") == name:
        data["current_context"] = None
    _save(data)
    return True


def list_instances() -> dict[str, dict]:
    return _load()["instances"]


def get_instance(name: str) -> dict | None:
    return _load()["instances"].get(name)


def set_context(name: str) -> bool:
    data = _load()
    if name not in data["instances"]:
        return False
    data["current_context"] = name
    _save(data)
    return True


def unset_context() -> None:
    data = _load()
    data["current_context"] = None
    _save(data)


def get_context() -> str | None:
    return _load().get("current_context")
