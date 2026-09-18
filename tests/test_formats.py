"""
test_formats.py — unit tests for cli/formats.py's render_list().

Run:
    uv run pytest tests/test_formats.py
"""

from __future__ import annotations

from cli import formats


def test_render_list_property_value_blocks_with_blank_line_between_rows():
    rows = [{"title": "Panel A", "x": 0}, {"title": "Panel B", "x": 4}]
    out = formats.render_list(rows, ["title", "x"])
    assert out == "title : Panel A\nx     : 0\n\ntitle : Panel B\nx     : 4"


def test_render_list_pads_labels_to_longest_column_name():
    out = formats.render_list([{"a": 1, "longcolumn": 2}], ["a", "longcolumn"])
    lines = out.splitlines()
    assert lines[0].startswith("a          :")  # padded to len("longcolumn")
    assert lines[1].startswith("longcolumn :")


def test_render_list_string_list_renders_as_bullets():
    rows = [{"title": "Panel A", "queries": ["A: count", "B: sum"]}]
    out = formats.render_list(rows, ["title", "queries"])
    assert out == "title   : Panel A\nqueries :\n    - A: count\n    - B: sum"


def test_render_list_multiline_bullet_item_keeps_line_breaks():
    rows = [{"queries": ["A: line1\n// comment\n| line3"]}]
    out = formats.render_list(rows, ["queries"])
    assert out == "queries :\n    - A: line1\n      // comment\n      | line3"


def test_render_list_empty_list_renders_blank():
    out = formats.render_list([{"queries": []}], ["queries"])
    assert out == "queries : "


def test_render_list_no_rows_returns_empty_string():
    assert formats.render_list([], ["title"]) == ""


def test_render_by_format_dispatches_list():
    rows = [{"title": "Panel A"}]
    assert formats.render_by_format(rows, ["title"], "list") == "title : Panel A"


def test_list_is_a_valid_format():
    assert "list" in formats.VALID_FORMATS
