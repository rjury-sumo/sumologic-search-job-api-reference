"""cli — sumosearch, a thin typer CLI over sumo_search_client.py.

Opt-in dependency group (`uv sync --group cli`); never pulled in by
`uv sync --group dev` alone. Imports sumo_search_client.py rather than
reimplementing any of its request/retry/pagination logic.
"""
