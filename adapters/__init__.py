"""ATS board adapters.

Each adapter is a `fetch_<platform>(board)` function returning a list of
normalized job dicts:

    { "id", "title", "location", "posted", "url", "company" }

Shared constants/helpers live in `adapters.common`. Adapters are registered
into the `ADAPTERS` dict in `jobwatch.py`.
"""
