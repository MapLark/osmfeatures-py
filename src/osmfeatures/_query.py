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
from ._http import ElementType, ShapeType, is_geojson_accept, merge_response_headers
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
``auto_split`` requires ``bbox``: ``within``, radius, and ``osm_ids``
raise ``ValueError`` instead of sending one unsplit request.

Parameters
----------
bbox_tiles:
    Split the requested bbox into this many tiles (power of 2).
    Default 1 sends the bbox as one request. Use 2, 4, 8, ... to stay
    under a tier area cap.
auto_split:
    Count matches first via a scalar ``GET /v2/osm_features/count``
    (no ``group_by``), then fetch. Same walk as ``places_search``.
    Reads the key's ``max_limit`` from ``GET /v1/account/tier``
    (cached on the client). If the match set exceeds that cap, jump
    to a power-of-2 tile grid; one leftover dense leaf may jump once
    more (do not walk a deeper tree). A lower ``limit`` is a page
    size and does not split. A bbox-area 400 jumps to the tier
    limit in the error instead of probing every quarter, up to 32
    tiles. Default False. Slower: a count per tile.
    ``split_until_fit`` is a deprecated alias.
timeout:
    Wall-clock seconds for tiled walks (``auto_split`` / ``bbox_tiles``).
    Defaults to :data:`osmfeatures.DEFAULT_QUERY_ALL_TIMEOUT_S`.
    A single HTTP request uses the client :data:`~osmfeatures.DEFAULT_TIMEOUT`.
    ``None`` is no cap.
    A genuine deadline miss is :class:`OSMFeaturesTimeoutError`.
    ``auto_split`` walks at most 32 tiles (five longest-side bisections).
    Covering the bbox would need more tiles:
    :class:`OSMFeaturesTooManyTilesError` (area too large). A tile still
    overflows at that cap: :class:`OSMFeaturesTooDenseError`.
bbox, location, radius, type, way_shape, shape, osm_ids, within, tags,
zoom, min_length_m, max_length_m, min_area_m2, max_area_m2, centroid,
clip_geometry, geometry, accept, limit, disable_budget_warning:
    Filters for ``GET /v3/osm_features``. No ``cursor``.
    ``disable_budget_warning`` is count-only (``auto_split``).

    ``tags`` is a string or list of strings, sent as one ``tags`` query value.
    A list is ANDed with ``,``. ``|`` inside one item is OR. Parentheses
    group one operator: ``a|(b,c)`` is ``a OR (b AND c)``, ``(a|b),c`` is
    ``(a OR b) AND c``. ``!`` prefixes one clause (``!key``, ``!key=value``);
    ``key!=value`` and ``key!~regex`` also negate. Clause forms: ``key``,
    ``key=value``, ``key>n`` / ``>=`` / ``<`` / ``<=``, ``key~regex``. Quote a
    value that contains ``|`` or ``&`` (``'name="Ben & Jerry\'s"'``). Unquoted
    ``&`` is not an operator (HTTP 400). Regex cannot use an index, so AND it
    with an indexed tag.
    ``auto_split`` counts with a scalar ``GET /v2/osm_features/count``
    (no ``group_by``).

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


def _query_filters(
    *,
    bbox: str | None = None,
    location: str | None = None,
    radius: float | None = None,
    type: ElementType | list[ElementType] | str | None = None,  # noqa: A002
    way_shape: ShapeType | None = None,
    shape: ShapeType | None = None,
    osm_ids: str | None = None,
    within: str | None = None,
    tags: list[str] | str | None = None,
    zoom: float | None = None,
    min_length_m: float | None = None,
    max_length_m: float | None = None,
    min_area_m2: float | None = None,
    max_area_m2: float | None = None,
    centroid: bool | None = None,
    clip_geometry: bool | None = None,
    geometry: Any = None,
) -> dict[str, Any]:
    """Omit ``None`` so the API keeps its defaults."""
    params: dict[str, Any] = {}
    if bbox is not None:
        params["bbox"] = bbox
    if location is not None:
        params["location"] = location
    if radius is not None:
        params["radius"] = radius
    if type is not None:
        params["type"] = type
    if way_shape is not None:
        params["way_shape"] = way_shape
    if shape is not None:
        params["shape"] = shape
    if osm_ids is not None:
        params["osm_ids"] = osm_ids
    if within is not None:
        params["within"] = within
    if tags is not None:
        params["tags"] = tags
    if zoom is not None:
        params["zoom"] = zoom
    if min_length_m is not None:
        params["min_length_m"] = min_length_m
    if max_length_m is not None:
        params["max_length_m"] = max_length_m
    if min_area_m2 is not None:
        params["min_area_m2"] = min_area_m2
    if max_area_m2 is not None:
        params["max_area_m2"] = max_area_m2
    if centroid is not None:
        params["centroid"] = centroid
    if clip_geometry is not None:
        params["clip_geometry"] = clip_geometry
    if geometry is not None:
        params["geometry"] = geometry
    return params


async def execute_query(
    *,
    raw_query: Callable[..., Any],
    request: Callable[..., Any],
    count: Callable[..., Any],
    account_tier: Callable[..., Any] | None = None,
    bbox: str | None = None,
    location: str | None = None,
    radius: float | None = None,
    type: ElementType | list[ElementType] | str | None = None,  # noqa: A002
    way_shape: ShapeType | None = None,
    shape: ShapeType | None = None,
    osm_ids: str | None = None,
    within: str | None = None,
    tags: list[str] | str | None = None,
    zoom: float | None = None,
    min_length_m: float | None = None,
    max_length_m: float | None = None,
    min_area_m2: float | None = None,
    max_area_m2: float | None = None,
    centroid: bool | None = None,
    clip_geometry: bool | None = None,
    geometry: Any = None,
    accept: str | None = None,
    limit: int | None = None,
    disable_budget_warning: bool = False,
    bbox_tiles: int = 1,
    auto_split: bool = False,
    split_until_fit: bool | None = None,
    timeout: float | None = DEFAULT_QUERY_ALL_TIMEOUT_S,
) -> OSMFeatureCollection | BinaryQueryResult:
    """Run a v3 query via *raw_query* / *request* / *count* (sync or async)."""
    auto_split = fold_auto_split(auto_split, split_until_fit)
    limit = _resolve_query_limit(limit)
    params = _query_filters(
        bbox=bbox,
        location=location,
        radius=radius,
        type=type,
        way_shape=way_shape,
        shape=shape,
        osm_ids=osm_ids,
        within=within,
        tags=tags,
        zoom=zoom,
        min_length_m=min_length_m,
        max_length_m=max_length_m,
        min_area_m2=min_area_m2,
        max_area_m2=max_area_m2,
        centroid=centroid,
        clip_geometry=clip_geometry,
        geometry=geometry,
    )
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
    if bbox_tiles != 1 and not isinstance(bbox, str):
        raise ValueError("bbox_tiles requires bbox")
    if auto_split and not isinstance(bbox, str):
        raise ValueError("auto_split requires bbox")
    # One request: return the API FeatureCollection as-is (body + headers).
    if bbox_tiles == 1 and not auto_split:
        return await _await_maybe(  # type: ignore[no-any-return]
            raw_query(
                _with_limit(params, limit),
                path=_V3_PATH,
            )
        )

    assert isinstance(bbox, str)
    api_truncated = False
    header_blobs: list[dict[str, str]] = []

    async def _fetch_tile(tile: str) -> list[dict[str, Any]]:
        nonlocal api_truncated
        collection = await _await_maybe(
            raw_query(_with_limit({**params, "bbox": tile}, limit), path=_V3_PATH)
        )
        headers = collection.get("response_headers")
        if isinstance(headers, dict):
            header_blobs.append(dict(headers))
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
    out = OSMFeatureCollection(
        features=[OSMFeature.from_dict(f) for f in all_features],
        meta=ResponseMeta(
            returned=len(all_features),
            has_more=api_truncated,
        ),
    )
    merged = merge_response_headers(header_blobs)
    if merged is not None:
        out["response_headers"] = merged
    return out


def execute_query_sync(
    *,
    raw_query: Callable[..., Any],
    request: Callable[..., Any],
    count: Callable[..., Any],
    account_tier: Callable[..., Any] | None = None,
    **kwargs: Any,
) -> OSMFeatureCollection | BinaryQueryResult:
    """Run :func:`execute_query` on a private loop (or a worker thread if nested)."""
    return run_sync(
        execute_query,
        raw_query=raw_query,
        request=request,
        count=count,
        account_tier=account_tier,
        **kwargs,
    )
