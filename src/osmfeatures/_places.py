"""Shared ``POST /v1/places/search`` tiling: bbox_tiles and auto_split.

Sync and async clients pass their POST/count callables into
:func:`execute_places_search`. ``bbox_tiles`` and ``auto_split`` are
client-side; they are not sent in the JSON body. Both only apply to bbox
search. location+radius is a circle (same as ``query`` radius) and cannot
split; convert with :func:`around_to_bbox` and search that bbox if
you need tiles.

``auto_split`` uses the same count-first walk as :func:`execute_query`.
Counts are ``GET /v2/osm_features/count`` with ``way_shape=polygon``.
``asOf`` / ``openNow`` AND ``opening_hours``. An ``asOf`` page ``limit``
truncates; density-split vs the 10k parse cap only when ``limit`` is
raised above 10k. ``openNow`` always density-splits vs that 10k scan cap
(the drain returns 200 at 10k hours-tagged rows).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ._geo_agent import places_search_body
from ._pagination import DEFAULT_QUERY_ALL_TIMEOUT_S
from ._split import (
    await_maybe,
    fold_auto_split,
    hours_parse_split_cap,
    iter_split_tiles,
    match_split_cap,
    max_limit_from_tier,
    run_sync,
)
from .chunking import merge_features

BBOX_TILES_REQUIRES_BBOX = (
    "bbox_tiles only applies to bbox search, not location+radius"
)
AUTO_SPLIT_REQUIRES_BBOX = (
    "auto_split only applies to bbox search, not location+radius"
)


_SUM_META_KEYS = ("units", "input_count", "kept_count", "hours_parsed")


def _merge_place_metadata(metas: list[dict[str, Any]]) -> dict[str, Any]:
    """Sum billed/scan fields across tiles. Keep first ``evaluated_at``."""
    out = dict(metas[0])
    out.pop("hint", None)
    for key in _SUM_META_KEYS:
        total = 0
        seen = False
        for meta in metas:
            value = meta.get(key)
            if value is None:
                continue
            try:
                total += int(value)
            except (TypeError, ValueError):
                continue
            seen = True
        if seen:
            out[key] = total
    if any(m.get("hours_parse_capped") for m in metas):
        out["hours_parse_capped"] = True
    return out


def _merge_place_collections(payloads: list[dict[str, Any]]) -> dict[str, Any]:
    if not payloads:
        return {"type": "FeatureCollection", "features": []}
    features = merge_features(
        [list(p.get("features") or []) for p in payloads if isinstance(p, dict)]
    )
    out = dict(payloads[0]) if isinstance(payloads[0], dict) else {}
    out["type"] = "FeatureCollection"
    out["features"] = features
    metas = [
        p["metadata"]
        for p in payloads
        if isinstance(p, dict) and isinstance(p.get("metadata"), dict)
    ]
    if metas:
        out["metadata"] = _merge_place_metadata(metas)
    return out


def _hours_on(open_now: bool, as_of: str | None) -> bool:
    return bool(open_now or as_of is not None)


async def execute_places_search(
    *,
    post: Callable[[dict[str, Any]], Any],
    count: Callable[..., Any],
    account_tier: Callable[..., Any] | None = None,
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

    count_kwargs: dict[str, Any] = {"way_shape": "polygon"}
    if tags is not None:
        count_kwargs["tags"] = list(tags)
    if or_tags is not None:
        count_kwargs["or_tags"] = list(or_tags)
    hours_on = _hours_on(open_now, as_of)
    if hours_on:
        count_kwargs["tags"] = [*count_kwargs.get("tags", []), "opening_hours"]
    omitted_cap = 1
    if auto_split and not hours_on:
        if account_tier is None:
            raise TypeError("auto_split requires account_tier")
        omitted_cap = max_limit_from_tier(await await_maybe(account_tier()))
    tile_cap = (
        hours_parse_split_cap(limit, open_now=open_now)
        if hours_on
        else match_split_cap(limit, omitted_cap)
    )

    async def _fetch_tile(tile: str) -> dict[str, Any]:
        return await await_maybe(post(_body(tile=tile)))  # type: ignore[no-any-return]

    async def _match_count(tile: str) -> int | None:
        body = await await_maybe(count(bbox=tile, **count_kwargs))
        return int(body["total"])

    payloads: list[dict[str, Any]] = []
    async for page in iter_split_tiles(
        bbox,
        bbox_tiles=bbox_tiles,
        auto_split=auto_split,
        timeout=timeout,
        tile_cap=tile_cap,
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
    account_tier: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run :func:`execute_places_search` on a private loop (or a worker if nested)."""
    return run_sync(
        execute_places_search,
        post=post,
        count=count,
        account_tier=account_tier,
        **kwargs,
    )
