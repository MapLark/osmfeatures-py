"""Shared ``POST /v1/places/search`` tiling: bbox_tiles and auto_split.

Sync and async clients pass their POST/count callables into
:func:`execute_places_search`. ``bbox_tiles`` and ``auto_split`` are
client-side; they are not sent in the JSON body. Both only apply to bbox
search. location+radius is a circle (same as ``query`` radius) and cannot
split; convert with :func:`around_to_bbox` and search that bbox if
you need tiles.

``auto_split`` uses the same count-first walk as :func:`execute_query`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ._geo_agent import places_search_body
from ._pagination import DEFAULT_QUERY_ALL_TIMEOUT_S
from ._split import (
    PLACES_MAX_LIMIT,
    await_maybe,
    count_tile_total,
    fold_auto_split,
    iter_split_tiles,
    match_split_cap,
    run_sync,
)
from .chunking import merge_features

BBOX_TILES_REQUIRES_BBOX = (
    "bbox_tiles only applies to bbox search, not location+radius"
)
AUTO_SPLIT_REQUIRES_BBOX = (
    "auto_split only applies to bbox search, not location+radius"
)


def _merge_place_collections(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    if not payloads:
        return {"type": "FeatureCollection", "features": []}
    features = merge_features(
        [list(p.get("features") or []) for p in payloads if isinstance(p, dict)]
    )
    out = dict(payloads[0]) if isinstance(payloads[0], dict) else {}
    out["type"] = "FeatureCollection"
    out["features"] = features
    return out


async def execute_places_search(
    *,
    post: Callable[[dict[str, Any]], Any],
    count: Callable[..., Any],
    bbox: str | None = None,
    location: dict[str, float] | None = None,
    radius: float | None = None,
    type: str | None = None,  # noqa: A002
    tags: list[str] | None = None,
    or_tags: list[str] | None = None,
    limit: int | None = None,
    open_now: bool = False,
    as_of: str | None = None,
    bbox_tiles: int = 1,
    auto_split: bool = False,
    split_until_fit: bool | None = None,
    timeout: float | None = DEFAULT_QUERY_ALL_TIMEOUT_S,
) -> dict[str, Any]:
    """POST places/search, optionally tiling *bbox* like :func:`execute_query`."""
    auto_split = fold_auto_split(auto_split, split_until_fit)
    if bbox_tiles != 1 and not isinstance(bbox, str):
        raise ValueError(BBOX_TILES_REQUIRES_BBOX)
    if auto_split and not isinstance(bbox, str):
        raise ValueError(AUTO_SPLIT_REQUIRES_BBOX)

    def _body(*, tile: str | None = None) -> dict[str, Any]:
        return places_search_body(
            bbox=tile if tile is not None else bbox,
            location=location,
            radius=radius,
            type=type,
            tags=tags,
            or_tags=or_tags,
            limit=limit,
            open_now=open_now,
            as_of=as_of,
        )

    if bbox_tiles == 1 and not auto_split:
        return await await_maybe(post(_body()))  # type: ignore[no-any-return]

    assert isinstance(bbox, str)

    count_params: dict[str, Any] = {}
    if type is not None:
        count_params["type"] = type
    if tags is not None:
        count_params["tags"] = tags
    if or_tags is not None:
        count_params["or_tags"] = or_tags

    async def _fetch_tile(tile: str) -> dict[str, Any]:
        return await await_maybe(post(_body(tile=tile)))  # type: ignore[no-any-return]

    async def _match_count(tile: str) -> int | None:
        return await count_tile_total(count, tile, count_params)

    payloads: list[dict[str, Any]] = []
    async for page in iter_split_tiles(
        bbox,
        bbox_tiles=bbox_tiles,
        auto_split=auto_split,
        timeout=timeout,
        tile_cap=match_split_cap(limit, PLACES_MAX_LIMIT),
        match_count=_match_count if auto_split else None,
        fetch_tile=_fetch_tile,
        overflow_name="places_search",
    ):
        payloads.append(page)
    return _merge_place_collections(payloads)


def execute_places_search_sync(
    *,
    post: Callable[[dict[str, Any]], Any],
    count: Callable[..., Any],
    **kwargs: Any,
) -> dict[str, Any]:
    """Run :func:`execute_places_search` on a private loop (or a worker if nested)."""
    return run_sync(execute_places_search, post=post, count=count, **kwargs)
