"""Auto-pagination helpers for /v2/osm_elements."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, Callable


_MAX_PAGES = 500  # hard safety cap - prevents infinite loops


def paginate_all(
    fetch_fn: Callable[[dict[str, Any]], dict[str, Any]],
    params: dict[str, Any],
    *,
    page_size: int = 1000,
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
    page_size:
        Number of features to request per page.

    Yields
    ------
    list[dict]
        The ``features`` list from each page response.
    """
    base = {k: v for k, v in params.items() if k not in ("limit", "cursor")}
    base["limit"] = page_size
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
    page_size: int = 1000,
) -> list[dict[str, Any]]:
    """Async counterpart to :func:`paginate_all`.

    Returns all features as a flat list (async generators are less ergonomic
    in Python, so we collect eagerly).
    """
    base = {k: v for k, v in params.items() if k not in ("limit", "cursor")}
    base["limit"] = page_size
    cursor: str | None = None
    all_features: list[dict[str, Any]] = []

    for page in range(_MAX_PAGES):
        page_params = dict(base)
        if cursor is not None:
            page_params["cursor"] = cursor
        data = await fetch_fn(page_params)
        features: list[dict[str, Any]] = data.get("features", [])
        all_features.extend(features)

        meta = data.get("meta", {})
        if not meta.get("has_more", False):
            return all_features

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
