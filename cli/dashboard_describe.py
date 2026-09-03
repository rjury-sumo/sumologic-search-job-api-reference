"""
cli/dashboard_describe.py — concise summaries of a Sumo Logic dashboard object.

The full payload from `GET /v2/dashboards/{id}` is dominated by per-panel
`visualSettings` blobs and repeated query definitions — a dashboard with ~20
panels serializes to 500+ lines of JSON, most of it irrelevant to the two
questions people actually ask: "what does this dashboard need to run" (time
range, variables) and "what's on it" (panel titles/types/layout).

summarize_dashboard()        -> scalars + variables + panel type counts (no panel list)
describe_dashboard_panels()  -> summarize_dashboard() plus a per-panel list (id, key,
                                 title, type, query count, {{var}} references, grid position)
describe_dashboard_queries() -> describe_dashboard_panels() plus each panel's actual
                                 query text (queryKey, queryType, queryString)

Collapsible sections (panelType 'CollapsiblePanel'): a section's member
panels are listed by key in its `collapsiblePanelChildKeys`, but each member
is still a normal top-level entry in `dashboard["panels"]` with its own
`queries[]` — the API does not nest them. Their `layout.layoutStructures`
entry is relative to the section's own origin (0, 0), not the dashboard
grid, so a section's first child and every other section's first child all
report position (0, 0) if read naively. This module treats section members
as children (a `children` list nested under the section's panel entry)
rather than flattening them — see `_child_key_map`.

All three functions are pure: dashboard dict in, plain dict out. No API
calls, no I/O — callers (`cli/main.py`'s `report describe`) own presentation
and fetching.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

_VAR_RE = re.compile(r"\{\{(\w+)\}\}")


def _format_time_boundary(boundary: dict | None) -> str:
    if not boundary:
        return "now"
    kind = boundary.get("type", "")
    if kind == "RelativeTimeRangeBoundary":
        return boundary.get("relativeTime", "?")
    if kind == "EpochTimeRangeBoundary":
        ms = boundary.get("epochMillis")
        if ms is None:
            return "?"
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if kind == "Iso8601TimeRangeBoundary":
        return boundary.get("iso8601Time", "?")
    return kind or "?"


def _format_time_range(time_range: dict | None) -> str | None:
    if not time_range:
        return None
    frm = _format_time_boundary(time_range.get("from"))
    to = _format_time_boundary(time_range.get("to"))
    return f"{frm} -> {to}"


def _panel_layout_map(dashboard: dict) -> dict[str, dict]:
    """panel key -> {x, y, width, height}, parsed from the stringified layout JSON."""
    out: dict[str, dict] = {}
    for entry in (dashboard.get("layout") or {}).get("layoutStructures", []) or []:
        key = entry.get("key")
        raw = entry.get("structure")
        if not key or not raw:
            continue
        try:
            out[key] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            out[key] = {}
    return out


def _variable_summary(dashboard: dict) -> list[dict]:
    return [
        {
            "name": v.get("name"),
            "defaultValue": v.get("defaultValue"),
            "allowMultiSelect": v.get("allowMultiSelect", False),
            "includeAllOption": v.get("includeAllOption", False),
            "sourceType": (v.get("sourceDefinition") or {}).get("variableSourceType"),
        }
        for v in dashboard.get("variables", []) or []
    ]


def _panel_type_counts(panels: list[dict]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in panels:
        t = p.get("panelType", "Unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


def _child_key_map(dashboard: dict) -> tuple[set, dict]:
    """Return (all keys that are a collapsible section's child, {parent_key: [child_key, ...]}),
    scanning every 'CollapsiblePanel' entry's collapsiblePanelChildKeys."""
    child_keys: set = set()
    children_by_parent: dict = {}
    for p in dashboard.get("panels", []) or []:
        if p.get("panelType") == "CollapsiblePanel":
            keys = p.get("collapsiblePanelChildKeys") or []
            children_by_parent[p.get("key")] = keys
            child_keys.update(keys)
    return child_keys, children_by_parent


def _panel_entry(p: dict, layout_map: dict) -> dict:
    """id/key/title/type/query metadata + grid position for one panel. Position is
    relative to the dashboard grid for a top-level panel, or relative to the parent
    section's origin for a collapsible section's child — see module docstring."""
    pos = layout_map.get(p.get("key"), {})
    var_refs: set = set()
    for q in p.get("queries", []) or []:
        var_refs.update(_VAR_RE.findall(q.get("queryString", "") or ""))
    return {
        "id": p.get("id"),
        "key": p.get("key"),
        "title": p.get("title"),
        "panelType": p.get("panelType"),
        "query_count": len(p.get("queries", []) or []),
        "variables_referenced": sorted(var_refs),
        "x": pos.get("x"),
        "y": pos.get("y"),
        "width": pos.get("width"),
        "height": pos.get("height"),
    }


def _sort_reading_order(panels: list[dict]) -> list[dict]:
    """Top-to-bottom, left-to-right — valid within one coordinate system (the dashboard
    grid for top-level panels, or one section's local grid for its children)."""
    return sorted(panels, key=lambda p: (p["y"] is None, p["y"] or 0, p["x"] or 0))


def summarize_dashboard(dashboard: dict) -> dict:
    """Level 'summary': everything needed to understand a dashboard's shape and
    defaults without any panel-by-panel detail."""
    panels = dashboard.get("panels", []) or []
    layout = dashboard.get("layout") or {}
    layout_map = _panel_layout_map(dashboard)
    child_keys, _ = _child_key_map(dashboard)

    # Collapsible section children use layout coordinates relative to their parent
    # section, not the dashboard grid — including them here would corrupt the bounding
    # box with numbers from an unrelated coordinate system.
    top_level_panels = [p for p in panels if p.get("key") not in child_keys]

    max_x = 0
    max_y = 0
    for p in top_level_panels:
        pos = layout_map.get(p.get("key"), {})
        max_x = max(max_x, (pos.get("x") or 0) + (pos.get("width") or 0))
        max_y = max(max_y, (pos.get("y") or 0) + (pos.get("height") or 0))

    return {
        "id": dashboard.get("id"),
        "title": dashboard.get("title"),
        "description": dashboard.get("description") or None,
        "domain": dashboard.get("domain"),
        "theme": dashboard.get("theme"),
        "folderId": dashboard.get("folderId"),
        "isPublic": dashboard.get("isPublic", False),
        "refreshInterval": dashboard.get("refreshInterval") or 0,
        "timeRange": _format_time_range(dashboard.get("timeRange")),
        "variables": _variable_summary(dashboard),
        "panel_count": len(panels),
        "panel_types": _panel_type_counts(panels),
        "layout_type": layout.get("layoutType"),
        "layout_grid": {"columns": max_x, "rows": max_y} if panels else None,
    }


def describe_dashboard_panels(dashboard: dict) -> dict:
    """Level 'panels': summarize_dashboard() plus a per-panel list, sorted in
    reading order (top-to-bottom, left-to-right). A collapsible section's member
    panels are nested under its entry as a 'children' list (also in reading order,
    but relative to the section's own origin) rather than flattened into the
    top-level list — see module docstring for why."""
    summary = summarize_dashboard(dashboard)
    layout_map = _panel_layout_map(dashboard)
    child_keys, children_by_parent = _child_key_map(dashboard)
    by_key = {p.get("key"): p for p in dashboard.get("panels", []) or []}

    top_level = []
    for p in dashboard.get("panels", []) or []:
        key = p.get("key")
        if key in child_keys:
            continue  # attached under its parent's "children" below instead
        entry = _panel_entry(p, layout_map)
        if p.get("panelType") == "CollapsiblePanel":
            entry["collapsed"] = p.get("collapsed", False)
            child_panels = [by_key[k] for k in children_by_parent.get(key, []) if k in by_key]
            entry["children"] = _sort_reading_order(
                [_panel_entry(c, layout_map) for c in child_panels])
        top_level.append(entry)

    summary["panels"] = _sort_reading_order(top_level)
    return summary


def describe_dashboard_queries(dashboard: dict) -> dict:
    """Level 'queries': describe_dashboard_panels() plus each panel's actual query
    text — queryKey, queryType, and queryString for every query the panel runs,
    including computed queries with no scope of their own (e.g. queryKey 'C' =
    "(#A/#B)*100"). Panel types with no queries (e.g. TextPanel) get an empty list.
    Collapsible sections' nested children get their own "queries" list too."""
    summary = describe_dashboard_panels(dashboard)
    queries_by_key = {
        p.get("key"): [
            {
                "queryKey": q.get("queryKey"),
                "queryType": q.get("queryType"),
                "queryString": (q.get("queryString") or "").strip(),
            }
            for q in (p.get("queries") or [])
        ]
        for p in dashboard.get("panels", []) or []
    }

    def _attach(panels: list[dict]) -> None:
        for panel in panels:
            panel["queries"] = queries_by_key.get(panel["key"], [])
            if "children" in panel:
                _attach(panel["children"])

    _attach(summary["panels"])
    return summary
