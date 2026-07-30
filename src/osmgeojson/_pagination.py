"""Auto-pagination helpers for /v2/osm_elements."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from typing import Any, Callable


_MAX_PAGES = 500  # hard safety cap - prevents infinite loops


def paginate_all(
    fetch_fn: Callable[[dict[str, Any]], dict[str, Any]],
    params: dict[str, Any],
    *,
    limit_per_page: int = 1000,
) -> Iterator[list[dict[str, Any]]]:
    """Yield pages of raw feature dicts until ``meta.has_more`` is False.

    Parameters
    ----------
    fetch_fn:
        Synchronous callable that accepts a params dict and returns a raw
        GeoJSON FeatureCollection dict (with ``meta`` envelope).
    params:
        Base query parameters. Any ``limit`` and ``cursor`` keys are managed
        internally and will be overwritten.
    limit_per_page:
        Upstream ``limit`` per HTTP request (page size).

    Yields
    ------
    list[dict]
        The ``features`` list from each page response.
    """
    base = {k: v for k, v in params.items() if k not in ("limit", "cursor")}
    base["limit"] = limit_per_page
    cursor: str | None = None

    for page in range(_MAX_PAGES):
        page_params = dict(base)
        if cursor is not None:
            page_params["cursor"] = cursor
        data = fetch_fn(page_params)
        features: list[dict[str, Any]] = data.get("features", [])
        yield features

        meta = data.get("meta", {})
        if not meta.get("has_more", False):
            return

        if not features:
            raise RuntimeError(
                f"API returned has_more=true but an empty features page at cursor {cursor!r} "
                f"on page {page}."
            )

        next_cursor = meta.get("next_cursor")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            raise RuntimeError(
                f"API returned has_more=true but next_cursor ({next_cursor!r}) "
                f"did not advance beyond current cursor ({cursor!r}) on page {page}."
            )
        cursor = next_cursor

    raise RuntimeError(
        f"paginate_all exceeded {_MAX_PAGES} pages without has_more=false - "
        "possible infinite pagination loop."
    )


async def paginate_all_async(
    fetch_fn: Callable[[dict[str, Any]], Any],
    params: dict[str, Any],
    *,
    limit_per_page: int = 1000,
) -> AsyncIterator[list[dict[str, Any]]]:
    """Async counterpart to :func:`paginate_all` (yields pages)."""
    base = {k: v for k, v in params.items() if k not in ("limit", "cursor")}
    base["limit"] = limit_per_page
    cursor: str | None = None

    for page in range(_MAX_PAGES):
        page_params = dict(base)
        if cursor is not None:
            page_params["cursor"] = cursor
        data = await fetch_fn(page_params)
        features: list[dict[str, Any]] = data.get("features", [])
        yield features

        meta = data.get("meta", {})
        if not meta.get("has_more", False):
            return

        if not features:
            raise RuntimeError(
                f"API returned has_more=true but an empty features page at cursor {cursor!r} "
                f"on page {page}."
            )

        next_cursor = meta.get("next_cursor")
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            raise RuntimeError(
                f"API returned has_more=true but next_cursor ({next_cursor!r}) "
                f"did not advance beyond current cursor ({cursor!r}) on page {page}."
            )
        cursor = next_cursor

    raise RuntimeError(
        f"paginate_all_async exceeded {_MAX_PAGES} pages without has_more=false - "
        "possible infinite pagination loop."
    )
