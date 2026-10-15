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
    iter_split_tiles,
    match_split_cap,
    max_bbox_area_from_tier,
    max_limit_from_tier,
    run_sync,
)

_V3_PATH = "/v3/osm_features"

QUERY_DOC = """Fetch OSM features matching the filters (``GET /v3/osm_features``).

Pass a ``bbox``, a ``location`` + ``radius`` circle, or ``osm_ids``. With
``auto_split=True`` a large ``bbox`` is split into tiles, fetched, and merged.

Parameters
----------
bbox:
    ``"min_lon,min_lat,max_lon,max_lat"``.
location:
    Circle centre, as ``"lat,lng"``. Use with ``radius``.
radius:
    Circle radius in metres.
type:
    Element type(s) to match: ``"node"``, ``"way"``, ``"relation"``, or a list.
tags:
    Tag filter: a string, or a list ANDed together. ``|`` is OR, ``!``
    negates a clause, and ``key=value``, ``key>n`` and ``key~regex`` are
    supported. Example: ``["amenity=cafe", "!wheelchair"]``. Quote values
    containing ``|`` or ``&``. Regex cannot use an index, so pair it with
    an indexed tag.
limit:
    Maximum features returned. Defaults to the key's ``max_limit``. A lower
    value truncates the response (see ``meta.has_more``).
bbox_tiles:
    Split ``bbox`` into this many tiles (power of 2) and merge the results.
auto_split:
    Split a ``bbox`` that is too large for your tier into tiles that fit
    the area cap, then merge. Requires ``bbox``. Default False.
auto_split_skip_count:
    With ``auto_split``, fetch each tile directly and halve any tile that
    is too large (default True). Set False in dense cities, where each tile
    is counted first with a scalar ``GET /v2/osm_features/count``
    (no ``group_by``) to avoid billed failures.
timeout:
    Wall-clock seconds for a tiled request (``auto_split`` or ``bbox_tiles``).
    ``None`` for no cap. Raises ``OSMFeaturesTimeoutError`` when exceeded.
    A single HTTP request uses the client's ``timeout``.
accept:
    Response format. Default is GeoJSON. Other formats (CSV, TSV,
    FlatGeobuf, GeoParquet) return a :class:`BinaryQueryResult` and are
    not tiled.
disable_budget_warning:
    Skip the unit-budget warning for large requests.
bbox_tiles, auto_split, auto_split_skip_count, timeout:
    See above.
osm_ids, within, way_shape, shape, zoom, min_length_m, max_length_m,
min_area_m2, max_area_m2, centroid, clip_geometry, geometry:
    Additional filters and output options, passed through to the API.

Raises
------
OSMFeaturesTooManyTilesError:
    ``auto_split`` would need more than 16 tiles to cover the bbox.
OSMFeaturesTooDenseError:
    A tile is still too dense at the maximum split.
OSMFeaturesTimeoutError:
    The tiled request exceeded ``timeout``.
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
    auto_split_skip_count: bool = True,
    timeout: float | None = DEFAULT_QUERY_ALL_TIMEOUT_S,
) -> OSMFeatureCollection | BinaryQueryResult:
    """Run a v3 query via *raw_query* / *request* / *count* (sync or async)."""
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
    area_limit: float | None = None
    if auto_split:
        if account_tier is None:
            raise TypeError("auto_split requires account_tier")
        tier = await _await_maybe(account_tier())
        omitted_cap = max_limit_from_tier(tier)
        area_limit = max_bbox_area_from_tier(tier, tags=tags)
    tile_cap = match_split_cap(limit, omitted_cap)

    async for page in iter_split_tiles(
        bbox,
        bbox_tiles=bbox_tiles,
        auto_split=auto_split,
        timeout=timeout,
        tile_cap=tile_cap,
        area_limit=area_limit,
        auto_split_skip_count=auto_split_skip_count,
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
