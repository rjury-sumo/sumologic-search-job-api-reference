"""
test_dashboard_describe.py — unit tests for cli/dashboard_describe.py.

Pure dict-in/dict-out functions, no API calls — synthetic dashboard
fixtures only. The fixture with a CollapsiblePanel section locks in the
flattening/child-exclusion behavior described in the module docstring:
section children are normal top-level entries in `dashboard["panels"]`
with layout coordinates relative to the section's own local origin, not
the dashboard grid.

Run:
    uv run pytest tests/test_dashboard_describe.py
"""

from __future__ import annotations

import json

from cli import dashboard_describe as dd


def _layout_entry(key: str, x: int, y: int, width: int, height: int) -> dict:
    return {"key": key, "structure": json.dumps({"x": x, "y": y, "width": width, "height": height})}


def make_dashboard() -> dict:
    """A dashboard with: one plain panel referencing a template variable,
    and one CollapsiblePanel section containing one child panel. Section
    child layout coordinates are relative to the section's own (0, 0)
    origin, not the dashboard grid — deliberately overlapping the parent's
    own coordinates to prove they're excluded from top-level bounding-box
    math."""
    return {
        "id": "dash-1",
        "title": "Test Dashboard",
        "description": "",
        "domain": "Observability",
        "theme": "Light",
        "folderId": "folder-1",
        "isPublic": False,
        "refreshInterval": 0,
        "timeRange": {
            "type": "BeginBoundedTimeRange",
            "from": {"type": "RelativeTimeRangeBoundary", "relativeTime": "-1d"},
            "to": None,
        },
        "variables": [
            {
                "name": "region",
                "defaultValue": "us-east-1",
                "allowMultiSelect": False,
                "includeAllOption": True,
                "sourceDefinition": {"variableSourceType": "CsvVariableSourceDefinition"},
            },
        ],
        "layout": {
            "layoutType": "Grid",
            "layoutStructures": [
                _layout_entry("panelA", x=0, y=0, width=12, height=4),
                _layout_entry("sec1", x=12, y=0, width=12, height=8),
                # child1's (x, y) is relative to sec1's own origin, not the
                # dashboard grid — deliberately overlapping panelA's slot to
                # prove summarize_dashboard() excludes it from the bounding box.
                _layout_entry("child1", x=0, y=0, width=6, height=4),
            ],
        },
        "panels": [
            {
                "id": "panel-a-id",
                "key": "panelA",
                "title": "Errors by region",
                "panelType": "SumoSearchPanel",
                "queries": [
                    {"queryKey": "A", "queryType": "Logs",
                     "queryString": '_sourceCategory={{region}} | count by _sourceHost'},
                ],
            },
            {
                "id": "sec1-id",
                "key": "sec1",
                "title": "Details",
                "panelType": "CollapsiblePanel",
                "collapsed": True,
                "collapsiblePanelChildKeys": ["child1"],
                "queries": [],
            },
            {
                "id": "child1-id",
                "key": "child1",
                "title": "Child panel",
                "panelType": "SumoSearchPanel",
                "queries": [
                    {"queryKey": "A", "queryType": "Logs", "queryString": "  _sourceCategory=*  "},
                ],
            },
        ],
    }


# ---------------------------------------------------------------------------
# summarize_dashboard
# ---------------------------------------------------------------------------

def test_summarize_dashboard_scalars():
    summary = dd.summarize_dashboard(make_dashboard())
    assert summary["id"] == "dash-1"
    assert summary["title"] == "Test Dashboard"
    assert summary["theme"] == "Light"
    assert summary["timeRange"] == "-1d -> now"


def test_summarize_dashboard_variables():
    summary = dd.summarize_dashboard(make_dashboard())
    assert summary["variables"] == [{
        "name": "region", "defaultValue": "us-east-1",
        "allowMultiSelect": False, "includeAllOption": True,
        "sourceType": "CsvVariableSourceDefinition",
    }]


def test_summarize_dashboard_panel_count_includes_children():
    summary = dd.summarize_dashboard(make_dashboard())
    assert summary["panel_count"] == 3
    assert summary["panel_types"] == {"SumoSearchPanel": 2, "CollapsiblePanel": 1}


def test_summarize_dashboard_layout_grid_excludes_section_children():
    """child1's layout coords (relative to sec1's local origin) must not
    corrupt the top-level bounding box — only panelA (0,0,12,4) and sec1
    (12,0,12,8) should contribute, giving columns=24, rows=8."""
    summary = dd.summarize_dashboard(make_dashboard())
    assert summary["layout_grid"] == {"columns": 24, "rows": 8}


def test_summarize_dashboard_no_panels_grid_is_none():
    dashboard = make_dashboard()
    dashboard["panels"] = []
    summary = dd.summarize_dashboard(dashboard)
    assert summary["layout_grid"] is None


# ---------------------------------------------------------------------------
# describe_dashboard_panels — top-level list + nested children
# ---------------------------------------------------------------------------

def test_describe_dashboard_panels_top_level_excludes_children():
    result = dd.describe_dashboard_panels(make_dashboard())
    keys = [p["key"] for p in result["panels"]]
    assert keys == ["panelA", "sec1"]  # child1 must not appear at top level


def test_describe_dashboard_panels_section_has_nested_children():
    result = dd.describe_dashboard_panels(make_dashboard())
    section = next(p for p in result["panels"] if p["key"] == "sec1")
    assert section["collapsed"] is True
    assert [c["key"] for c in section["children"]] == ["child1"]
    # child's position is its own local-origin layout entry, untouched
    child = section["children"][0]
    assert (child["x"], child["y"], child["width"], child["height"]) == (0, 0, 6, 4)


def test_describe_dashboard_panels_variable_references_detected():
    result = dd.describe_dashboard_panels(make_dashboard())
    panel_a = next(p for p in result["panels"] if p["key"] == "panelA")
    assert panel_a["variables_referenced"] == ["region"]


def test_describe_dashboard_panels_reading_order():
    dashboard = make_dashboard()
    # Swap layout so sec1 is visually above panelA — reading order must follow.
    for entry in dashboard["layout"]["layoutStructures"]:
        if entry["key"] == "panelA":
            entry["structure"] = json.dumps({"x": 0, "y": 10, "width": 12, "height": 4})
        elif entry["key"] == "sec1":
            entry["structure"] = json.dumps({"x": 0, "y": 0, "width": 12, "height": 8})
    result = dd.describe_dashboard_panels(dashboard)
    assert [p["key"] for p in result["panels"]] == ["sec1", "panelA"]


# ---------------------------------------------------------------------------
# describe_dashboard_queries — query text attached at every level
# ---------------------------------------------------------------------------

def test_describe_dashboard_queries_attaches_top_level_queries():
    result = dd.describe_dashboard_queries(make_dashboard())
    panel_a = next(p for p in result["panels"] if p["key"] == "panelA")
    assert panel_a["queries"] == [{
        "queryKey": "A", "queryType": "Logs",
        "queryString": "_sourceCategory={{region}} | count by _sourceHost",
    }]


def test_describe_dashboard_queries_attaches_nested_child_queries():
    result = dd.describe_dashboard_queries(make_dashboard())
    section = next(p for p in result["panels"] if p["key"] == "sec1")
    child = section["children"][0]
    assert child["queries"] == [{"queryKey": "A", "queryType": "Logs", "queryString": "_sourceCategory=*"}]


def test_describe_dashboard_queries_panel_with_no_queries_gets_empty_list():
    result = dd.describe_dashboard_queries(make_dashboard())
    section = next(p for p in result["panels"] if p["key"] == "sec1")
    assert section["queries"] == []


# ---------------------------------------------------------------------------
# _format_time_boundary / _format_time_range
# ---------------------------------------------------------------------------

def test_format_time_range_epoch_boundary():
    time_range = {
        "from": {"type": "EpochTimeRangeBoundary", "epochMillis": 1700000000000},
        "to": None,
    }
    assert dd._format_time_range(time_range) == "2023-11-14T22:13:20Z -> now"


def test_format_time_range_none_returns_none():
    assert dd._format_time_range(None) is None


def test_format_time_range_iso8601_boundary():
    time_range = {
        "from": {"type": "Iso8601TimeRangeBoundary", "iso8601Time": "2025-01-01T00:00:00Z"},
        "to": {"type": "Iso8601TimeRangeBoundary", "iso8601Time": "2025-01-02T00:00:00Z"},
    }
    assert dd._format_time_range(time_range) == "2025-01-01T00:00:00Z -> 2025-01-02T00:00:00Z"


# ---------------------------------------------------------------------------
# _panel_layout_map — malformed structure JSON is tolerated
# ---------------------------------------------------------------------------

def test_panel_layout_map_handles_malformed_json():
    dashboard = {"layout": {"layoutStructures": [{"key": "panelA", "structure": "not-json"}]}}
    assert dd._panel_layout_map(dashboard) == {"panelA": {}}
