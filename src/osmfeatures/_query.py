"""Shared ``/v3/osm_features`` query: tiling, auto_split, binary accept.

Sync and async clients pass their HTTP callables into :func:`execute_query`.
They cannot call each other: different HTTP stacks (requests vs httpx), and
that import would cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .chunking import (
    merge_features,
    shapely_to_bbox,
)
from ._http import is_geojson_accept
from .models import (
    BinaryQueryResult,
    OSMFeature,
    OSMFeatureCollection,
    ResponseMeta,
)
from ._pagination import DEFAULT_QUERY_ALL_TIMEOUT_S
from ._split import (
    await_maybe as _await_maybe,
    count_tile_total,
    fold_auto_split,
    iter_split_tiles,
    match_split_cap,
    max_limit_from_tier,
    run_sync,
)

_V3_PATH = "/v3/osm_features"

QUERY_DOC = """One ``/v3/osm_features`` call for a tile that fits in ``limit`` features.

Omit ``limit`` for the key's ``max_limit``. A lower ``limit`` truncates
(``meta.has_more``). A match set larger than the caller's ``max_limit``
is HTTP 400 ``result_too_large``. ``auto_split=True`` reads ``max_limit`` from ``GET /v1/account/tier``,
counts first, and jumps to a power-of-2 tile grid when that match set
exceeds the key's ``max_limit``. A leftover dense leaf may jump once
more. A lower ``limit`` truncates. That adds latency.
``within``, radius, and ``osm_ids`` cannot be split, so the API error
propagates.

Parameters
----------
bbox_tiles:
    Split the requested bbox into this many tiles (power of 2).
    Default 1 sends the bbox as one request. Use 2, 4, 8, ... to stay
    under a tier area cap.
auto_split:
    Count matches first, then fetch. Same walk as
    ``places_search``. Reads the key's ``max_limit`` from
    ``GET /v1/account/tier`` (cached on the client). If the match set
    exceeds that cap, jump to a power-of-2 tile grid; one leftover
    dense leaf may jump once more (do not walk a deeper tree). A
    lower ``limit`` is a page size and does not split. A bbox-area
    400 jumps to the tier limit in the error instead of probing
    every quarter, up to 32 tiles. Default False. Slower: a count
    per tile. ``split_until_fit`` is a deprecated alias.
timeout:
    Wall-clock seconds for this call. Defaults to 60. ``None`` is no cap.
    A genuine deadline miss is :class:`OSMFeaturesTimeoutError`.
    ``auto_split`` walks at most 32 tiles (five longest-side bisections).
    Covering the bbox would need more tiles:
    :class:`OSMFeaturesTooManyTilesError` (area too large). A tile still
    overflows at that cap: :class:`OSMFeaturesTooDenseError`.
**params:
    Filter kwargs: ``bbox``, ``location``, ``radius``, ``type``,
    ``way_shape``, ``shape``, ``osm_ids``, ``within``, ``tags``,
    ``or_tags``, ``not_tags``, ``zoom``, size bounds, ``centroid``,
    ``clip_geometry``, ``geometry``, ``accept``, ``limit``. No
    ``cursor``. ``disable_budget_warning`` is count-only. An AND ``tags``
    key (else the first ``or_tags`` key) is the count ``group_by``.

    ``limit`` is the maximum features in the response (omit for the
    key's ``max_limit``). A lower limit truncates. A match
    set larger than the caller's ``max_limit`` is HTTP 400
    ``result_too_large``. ``auto_split`` reads ``max_limit`` from
    ``GET /v1/account/tier``, counts first, and jumps to a power-of-2
    grid when that match set exceeds the key's cap (a lower ``limit``
    truncates and does not split).
    A leftover dense leaf may jump once more.

    ``accept`` selects the encoding (default GeoJSON). Non-GeoJSON
    (CSV, TSV, FlatGeobuf, GeoParquet) is one request and returns
    :class:`BinaryQueryResult`. Those encodings cannot tile.
"""


def _resolve_query_limit(limit: Any) -> int | None:
    """Validate ``limit``. ``None`` omits it so the API uses the key's max_limit."""
    if limit is None:
        return None
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if value < 1:
        raise ValueError("limit must be a positive integer")
    return value


def _with_limit(params: dict[str, Any], limit: int | None) -> dict[str, Any]:
    out = dict(params)
    if limit is not None:
        out["limit"] = limit
    return out


async def execute_query(
    *,
    raw_query: Callable[..., Any],
    request: Callable[..., Any],
    count: Callable[..., Any],
    account_tier: Callable[..., Any] | None = None,
    bbox_tiles: int = 1,
    auto_split: bool = False,
    split_until_fit: bool | None = None,
    timeout: float | None = DEFAULT_QUERY_ALL_TIMEOUT_S,
    **params: Any,
) -> OSMFeatureCollection | BinaryQueryResult:
    """Run a v3 query via *raw_query* / *request* / *count* (sync or async)."""
    auto_split = fold_auto_split(auto_split, split_until_fit)
    if "cursor" in params:
        raise ValueError("query does not take cursor")
    if "max_features" in params:
        raise ValueError("query does not take max_features; pass limit to cap the server page")
    # v3 rejects this; keep it only for count() during auto_split.
    disable_budget_warning = bool(params.pop("disable_budget_warning", False))
    limit = _resolve_query_limit(params.pop("limit", None))
    accept = params.pop("accept", None)
    params = {k: v for k, v in params.items() if v is not None}
    if not is_geojson_accept(accept):
        if auto_split:
            raise ValueError("auto_split only works with GeoJSON")
        if bbox_tiles != 1:
            raise ValueError("bbox_tiles only works with GeoJSON")
        if "geometry" in params:
            geom = params.pop("geometry")
            params["bbox"] = shapely_to_bbox(geom)
        resp = await _await_maybe(
            request(
                _with_limit(params, limit),
                accept=accept,
                path=_V3_PATH,
            )
        )
        return BinaryQueryResult.from_http(resp.content, resp.headers)

    if "geometry" in params:
        geom = params.pop("geometry")
        params["bbox"] = shapely_to_bbox(geom)

    bbox = params.get("bbox")
    api_truncated = False

    async def _fetch_tile(tile: str) -> list[dict[str, Any]]:
        nonlocal api_truncated
        collection = await _await_maybe(
            raw_query(_with_limit({**params, "bbox": tile}, limit), path=_V3_PATH)
        )
        features = list(collection.get("features", []))
        if collection.meta.has_more:
            api_truncated = True
        return features

    async def _match_count(tile: str) -> int | None:
        return await count_tile_total(
            count,
            tile,
            params,
            disable_budget_warning=disable_budget_warning,
        )

    if not isinstance(bbox, str):
        if bbox_tiles != 1:
            raise ValueError("bbox_tiles requires bbox")
        collection = await _await_maybe(
            raw_query(
                _with_limit(params, limit),
                path=_V3_PATH,
            )
        )
        page = list(collection.get("features", []))
        return OSMFeatureCollection(
            features=[OSMFeature.from_dict(f) for f in page],
            meta=ResponseMeta(returned=len(page), has_more=collection.meta.has_more),
        )

    feature_lists: list[list[dict[str, Any]]] = []
    omitted_cap = 1
    if auto_split:
        if account_tier is None:
            raise TypeError("auto_split requires account_tier")
        omitted_cap = max_limit_from_tier(await _await_maybe(account_tier()))
    tile_cap = match_split_cap(limit, omitted_cap)

    async for page in iter_split_tiles(
        bbox,
        bbox_tiles=bbox_tiles,
        auto_split=auto_split,
        timeout=timeout,
        tile_cap=tile_cap,
        match_count=_match_count if auto_split else None,
        fetch_tile=_fetch_tile,
        overflow_name="query",
    ):
        feature_lists.append(page)

    all_features = merge_features(feature_lists)
    return OSMFeatureCollection(
        features=[OSMFeature.from_dict(f) for f in all_features],
        meta=ResponseMeta(
            returned=len(all_features),
            has_more=api_truncated,
        ),
    )


def execute_query_sync(
    *,
    raw_query: Callable[..., Any],
    request: Callable[..., Any],
    count: Callable[..., Any],
    account_tier: Callable[..., Any] | None = None,
    bbox_tiles: int = 1,
    auto_split: bool = False,
    split_until_fit: bool | None = None,
    timeout: float | None = DEFAULT_QUERY_ALL_TIMEOUT_S,
    **params: Any,
) -> OSMFeatureCollection | BinaryQueryResult:
    """Run :func:`execute_query` on a private loop (or a worker thread if nested)."""
    return run_sync(
        execute_query,
        raw_query=raw_query,
        request=request,
        count=count,
        account_tier=account_tier,
        bbox_tiles=bbox_tiles,
        auto_split=auto_split,
        split_until_fit=split_until_fit,
        timeout=timeout,
        **params,
    )
