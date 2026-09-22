"""Auto-pagination helpers for /v2/osm_features."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from typing import Any, Callable

from osmfeatures.models import OSMFeatureCollection, OSMFeaturesTimeoutError


_MAX_PAGES = 500  # hard safety cap - prevents infinite loops
DEFAULT_QUERY_ALL_TIMEOUT_S = 60.0


def query_all_deadline(timeout: float | None) -> float | None:
    """Absolute monotonic deadline for a ``query_all`` call, or None if uncapped."""
    if timeout is None:
        return None
    return time.monotonic() + timeout


def paginate_all(
    fetch_fn: Callable[[dict[str, Any]], OSMFeatureCollection],
    params: dict[str, Any],
    *,
    limit_per_page: int | None = None,
    deadline: float | None = None,
    timeout: float | None = None,
) -> Iterator[list[dict[str, Any]]]:
    """Yield pages of raw feature dicts until ``X-Has-More`` is false.

    Parameters
    ----------
    fetch_fn:
        Synchronous callable that accepts a params dict and returns an
        :class:`OSMFeatureCollection` (pagination via ``.meta`` from headers).
    params:
        Base query parameters. Any ``limit`` and ``cursor`` keys are managed
        internally and will be overwritten.
    limit_per_page:
        Upstream ``limit`` per HTTP request (page size). Omit to use the API
        default (1000).

    Yields
    ------
    list[dict]
        The ``features`` list from each page response.
    """
    base = {k: v for k, v in params.items() if k not in ("limit", "cursor")}
    if limit_per_page is not None:
        base["limit"] = limit_per_page
    cursor: str | None = None

    for page in range(_MAX_PAGES):
        page_params = dict(base)
        if cursor is not None:
            page_params["cursor"] = cursor
        collection = fetch_fn(page_params)
        features: list[dict[str, Any]] = list(collection.get("features", []))
        yield features

        if not collection.meta.has_more:
            return

        if not features:
            raise RuntimeError(
                f"API returned has_more=true but an empty features page at cursor {cursor!r} "
                f"on page {page}."
            )

        next_cursor = collection.meta.next_cursor
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            raise RuntimeError(
                f"API returned has_more=true but next_cursor ({next_cursor!r}) "
                f"did not advance beyond current cursor ({cursor!r}) on page {page}."
            )
        if deadline is not None and time.monotonic() >= deadline:
            raise OSMFeaturesTimeoutError(
                f"query_all exceeded {timeout}s timeout after {page + 1} page(s)",
                timeout=timeout,
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
    limit_per_page: int | None = None,
    deadline: float | None = None,
    timeout: float | None = None,
) -> AsyncIterator[list[dict[str, Any]]]:
    """Async counterpart to :func:`paginate_all` (yields pages)."""
    base = {k: v for k, v in params.items() if k not in ("limit", "cursor")}
    if limit_per_page is not None:
        base["limit"] = limit_per_page
    cursor: str | None = None

    for page in range(_MAX_PAGES):
        page_params = dict(base)
        if cursor is not None:
            page_params["cursor"] = cursor
        collection = await fetch_fn(page_params)
        features: list[dict[str, Any]] = list(collection.get("features", []))
        yield features

        if not collection.meta.has_more:
            return

        if not features:
            raise RuntimeError(
                f"API returned has_more=true but an empty features page at cursor {cursor!r} "
                f"on page {page}."
            )

        next_cursor = collection.meta.next_cursor
        if not isinstance(next_cursor, str) or not next_cursor or next_cursor == cursor:
            raise RuntimeError(
                f"API returned has_more=true but next_cursor ({next_cursor!r}) "
                f"did not advance beyond current cursor ({cursor!r}) on page {page}."
            )
        if deadline is not None and time.monotonic() >= deadline:
            raise OSMFeaturesTimeoutError(
                f"query_all exceeded {timeout}s timeout after {page + 1} page(s)",
                timeout=timeout,
            )
        cursor = next_cursor

    raise RuntimeError(
        f"paginate_all_async exceeded {_MAX_PAGES} pages without has_more=false - "
        "possible infinite pagination loop."
    )
