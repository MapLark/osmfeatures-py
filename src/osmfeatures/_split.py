"""Shared bbox ``auto_split``: count first, then fetch.

``query()`` and ``places_search()`` use the same walk so a county-sized
window does not probe every quarter with 400s. Count skips empty tiles and
jumps to a power-of-2 grid when the match set cannot fit one fetch, and
may jump one leftover dense leaf the same way. Count uses the large
count window; a ``count bbox area`` 400 is not split (still too big to
count). A fetch ``bbox area`` 400 is parsed for the search/query tile
size; later tiles jump to that size instead of thrashing. The walk stops at :data:`MAX_SPLIT_TILES` (32). More
tiles to cover the bbox is :class:`OSMFeaturesTooManyTilesError`; a tile
still overflowing after that jump is :class:`OSMFeaturesTooDenseError`. A
wall-clock miss is still :class:`OSMFeaturesTimeoutError`.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import math
import re
import time
import warnings
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, TypeVar

from .chunking import (
    bbox_area_deg2,
    split_bbox_tiles,
    tile_count_for_max_area,
    _next_power_of_two,
)
from ._pagination import query_all_deadline
from .models import (
    OSMFeaturesAPIError,
    OSMFeaturesTimeoutError,
    OSMFeaturesTooDenseError,
    OSMFeaturesTooManyTilesError,
)

T = TypeVar("T")
R = TypeVar("R")

# Hours-parse cap when places_search sends asOf/openNow (OPEN_NOW_SCAN_MAX).
# No-hours auto_split uses GET /v1/account/tier max_limit instead.
PLACES_MAX_LIMIT = 10_000
# Max tiles ``auto_split`` will walk. 32 is five longest-side bisections
# (2^5). Density jumps the parent window to a power-of-2 grid, then at
# most one leftover dense leaf. No deeper tree.
MAX_SPLIT_TILES = 32
# Jumps allowed at depth 0 (parent) and 1 (dense leaf). Depth 2+ errors.
MAX_DENSITY_JUMP_DEPTH = 1
_COUNT_FILTER_KEYS = (
    "location",
    "radius",
    "type",
    "within",
    "tags",
    "or_tags",
    "not_tags",
    "min_length_m",
    "max_length_m",
    "min_area_m2",
    "max_area_m2",
)
# Search/query fetch 400s. Do not match "count bbox area" (count window
# is ~400 deg²; jumping to that size does not produce fetchable tiles).
_AREA_LIMIT_RE = re.compile(
    r"(?<!count )bbox area [0-9.]+\s+exceeds the (?:tagged )?tier limit ([0-9.]+)",
    re.IGNORECASE,
)


def tiles_for_match_count(total: int, tile_cap: int) -> int:
    """Power-of-2 tiles so each holds at most *tile_cap* matches if uniform."""
    if tile_cap <= 0 or total <= tile_cap:
        return 1
    return _next_power_of_two(math.ceil(total / tile_cap))


def max_limit_from_tier(tier: Any) -> int:
    """``max_limit`` from ``GET /v1/account/tier`` (``/v3/osm_features`` row cap)."""
    if not isinstance(tier, dict) or tier.get("max_limit") is None:
        raise ValueError("GET /v1/account/tier must include max_limit")
    try:
        value = int(tier["max_limit"])
    except (TypeError, ValueError) as exc:
        raise ValueError("tier max_limit must be an integer") from exc
    if value < 1:
        raise ValueError("tier max_limit must be >= 1")
    return value


def match_split_cap(limit: int | None, omitted: int) -> int:
    """Per-tile match cap that triggers a density split.

    A caller ``limit`` below the API ``max_limit`` is a page size (truncate).
    Count-split only when the match set would 400 ``result_too_large``.
    A ``limit`` at or above *omitted* is a paid raise of ``max_limit``.
    """
    if limit is None:
        return omitted
    return max(limit, omitted)


# Count never density-splits when the fetch cannot 400. Area 400s still split.
_NO_DENSITY_CAP = 2**31 - 1


def hours_parse_split_cap(limit: int | None, *, open_now: bool = False) -> int:
    """Per-tile cap for asOf/openNow auto_split.

    ``openNow`` drains up to :data:`PLACES_MAX_LIMIT` hours-tagged rows
    and returns 200 (no ``hours_parse_limit``). Split on that scan cap so
    each tile gets its own budget, even with a small fill ``limit``.

    ``asOf`` 400s ``hours_parse_limit`` only when the fetch can return more
    than 10k hours-tagged rows. That needs a paid ``limit`` raise above
    10k. Omit or a page ``limit`` truncates; do not split.
    Do not let :func:`match_split_cap` lift that 10k ceiling.
    """
    if open_now:
        return PLACES_MAX_LIMIT
    if limit is not None and limit > PLACES_MAX_LIMIT:
        return PLACES_MAX_LIMIT
    return _NO_DENSITY_CAP


def too_many_tiles_message(needed: int, *, total: int | None = None) -> str:
    extra = f" for {total} matches" if total is not None else ""
    return (
        f"auto_split needs {needed} bbox tiles{extra} "
        f"(max {MAX_SPLIT_TILES}). The area is too large to query. "
        "Geocode a smaller named place (city or neighborhood), or ask "
        "the user which area to use. Do not split the leftover bbox."
    )


def too_dense_message(*, total: int | None = None) -> str:
    extra = f" ({total} matches)" if total is not None else ""
    return (
        f"auto_split cannot fetch this area: it is still too dense after a "
        f"power-of-2 jump{extra} (max {MAX_SPLIT_TILES} tiles). Ask the user "
        "to shrink the area or add filters (more specific tags). If they do "
        "not answer, add tighter tags and retry a smaller named place."
    )


def is_result_too_large(exc: BaseException) -> bool:
    return (
        isinstance(exc, OSMFeaturesAPIError)
        and exc.status_code == 400
        and "result_too_large" in str(exc)
    )


def is_hours_parse_limit(exc: BaseException) -> bool:
    return (
        isinstance(exc, OSMFeaturesAPIError)
        and exc.status_code == 400
        and "hours_parse_limit" in str(exc)
    )


def parse_bbox_area_limit(exc: BaseException) -> float | None:
    """Search/query fetch area cap from a 400, or None.

    Ignores ``count bbox area`` 400s from ``/v2/osm_features/count``
    (those name the count window, not a fetchable tile size).
    """
    if not isinstance(exc, OSMFeaturesAPIError) or exc.status_code != 400:
        return None
    match = _AREA_LIMIT_RE.search(str(exc))
    if match is None:
        return None
    limit = float(match.group(1))
    return limit if limit > 0 else None


def is_split_error(exc: BaseException) -> bool:
    """True when a 400 can be recovered by splitting the bbox."""
    return (
        is_result_too_large(exc)
        or is_hours_parse_limit(exc)
        or parse_bbox_area_limit(exc) is not None
    )


def fold_auto_split(
    auto_split: bool = False,
    split_until_fit: bool | None = None,
    *,
    stacklevel: int = 3,
) -> bool:
    """Canonical ``auto_split``. ``split_until_fit`` is the deprecated alias."""
    if split_until_fit is None:
        return bool(auto_split)
    warnings.warn(
        "split_until_fit is deprecated; use auto_split",
        DeprecationWarning,
        stacklevel=stacklevel,
    )
    alias = bool(split_until_fit)
    if auto_split and auto_split != alias:
        raise ValueError(
            "auto_split and deprecated split_until_fit disagree. Pass only auto_split."
        )
    return alias or auto_split


async def await_maybe(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


async def count_tile_total(
    count: Callable[..., Any],
    tile: str,
    params: dict[str, Any],
    *,
    disable_budget_warning: bool = False,
) -> int | None:
    """``GET /v2/osm_features/count`` scalar total for *tile*."""
    stat: dict[str, Any] = {"bbox": tile}
    for name in _COUNT_FILTER_KEYS:
        if params.get(name) is not None:
            stat[name] = params[name]
    way_shape = params.get("way_shape", params.get("shape"))
    if way_shape is not None:
        stat["way_shape"] = way_shape
    if disable_budget_warning:
        stat["disable_budget_warning"] = True
    body = await await_maybe(count(**stat))
    return int(body["total"])


def run_sync(coro_fn: Callable[..., Awaitable[R]], **kwargs: Any) -> R:
    """Run an async entrypoint on a private loop (or a worker if nested)."""

    async def _call() -> R:
        return await coro_fn(**kwargs)

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_call())

    def _in_thread() -> R:
        return asyncio.run(_call())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_in_thread).result()


async def iter_split_tiles(
    bbox: str,
    *,
    bbox_tiles: int = 1,
    auto_split: bool = False,
    timeout: float | None = None,
    tile_cap: int,
    match_count: Callable[[str], Awaitable[int | None]] | None = None,
    fetch_tile: Callable[[str], Awaitable[T]],
    should_stop: Callable[[], bool] | None = None,
    overflow_name: str = "query",
) -> AsyncIterator[T]:
    """Walk *bbox* tiles: count first when splitting, then fetch.

    ``match_count`` is called for every tile under ``auto_split`` when
    provided. Empty tiles are skipped. A total above *tile_cap* (the API
    ``max_limit``, not a smaller page ``limit``) jumps to a power-of-2
    grid. A leftover leaf that is still too dense may jump once more;
    deeper overflow is too dense, not another tree. Fetch 400s
    that name a bbox-area cap jump to that tile size. ``result_too_large``
    after a count that already fit is too dense, not another split.
    ``auto_split`` walks at most :data:`MAX_SPLIT_TILES` tiles.
    """
    deadline = query_all_deadline(timeout)
    tiles: deque[tuple[str, int]] = deque(
        (t, 0)
        for t in ([bbox] if bbox_tiles == 1 else split_bbox_tiles(bbox, bbox_tiles))
    )
    if auto_split and len(tiles) > MAX_SPLIT_TILES:
        raise OSMFeaturesTooManyTilesError(
            too_many_tiles_message(len(tiles)),
            tiles=len(tiles),
            max_tiles=MAX_SPLIT_TILES,
        )
    area_limit: float | None = None
    last_total: int | None = None
    started = False

    def _deadline() -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise OSMFeaturesTimeoutError(
                f"{overflow_name} exceeded {timeout}s timeout",
                timeout=timeout,
            )

    def _raise_too_dense(*, tiles_needed: int | None = None) -> None:
        raise OSMFeaturesTooDenseError(
            too_dense_message(total=last_total),
            max_tiles=MAX_SPLIT_TILES,
            tiles=tiles_needed,
            total=last_total,
        )

    def _raise_too_many(*, tiles_needed: int) -> None:
        raise OSMFeaturesTooManyTilesError(
            too_many_tiles_message(tiles_needed, total=last_total),
            tiles=tiles_needed,
            max_tiles=MAX_SPLIT_TILES,
            total=last_total,
        )

    def _enqueue(
        children: list[str],
        depth: int,
        *,
        overflow: Callable[..., None],
    ) -> None:
        needed = len(tiles) + len(children)
        if needed > MAX_SPLIT_TILES:
            overflow(tiles_needed=needed)
        child_depth = depth + 1
        for child in children:
            tiles.append((child, child_depth))

    def _jump_density(tile: str, depth: int, total: int) -> None:
        if depth > MAX_DENSITY_JUMP_DEPTH:
            _raise_too_dense()
        n = tiles_for_match_count(total, tile_cap)
        if n > MAX_SPLIT_TILES:
            _raise_too_dense(tiles_needed=n)
        _enqueue(split_bbox_tiles(tile, n), depth, overflow=_raise_too_dense)

    def _enqueue_area(tile: str, depth: int, limit: float) -> bool:
        n = tile_count_for_max_area(tile, limit)
        if n <= 1:
            return False
        if n > MAX_SPLIT_TILES:
            _raise_too_many(tiles_needed=n)
        _enqueue(split_bbox_tiles(tile, n), depth, overflow=_raise_too_many)
        return True

    def _on_split_error(tile: str, depth: int, exc: OSMFeaturesAPIError) -> bool:
        nonlocal area_limit
        limit = parse_bbox_area_limit(exc)
        if limit is not None:
            area_limit = limit
            return _enqueue_area(tile, depth, limit)
        if is_hours_parse_limit(exc):
            _jump_density(tile, depth, max(last_total or 0, tile_cap) + 1)
            return True
        if is_result_too_large(exc):
            _raise_too_dense()
        return False

    while tiles:
        if started:
            _deadline()
        started = True
        if should_stop is not None and should_stop():
            break
        tile, depth = tiles.popleft()
        last_total = None
        if auto_split and area_limit is not None:
            if bbox_area_deg2(tile) > area_limit:
                if not _enqueue_area(tile, depth, area_limit):
                    _raise_too_dense()
                continue
        if auto_split and match_count is not None:
            try:
                total = await match_count(tile)
            except OSMFeaturesAPIError as exc:
                if _on_split_error(tile, depth, exc):
                    continue
                raise
            last_total = total
            if total == 0:
                continue
            if total is not None and total > tile_cap:
                _jump_density(tile, depth, total)
                continue
        try:
            yield await fetch_tile(tile)
        except OSMFeaturesAPIError as exc:
            if auto_split and _on_split_error(tile, depth, exc):
                continue
            raise
