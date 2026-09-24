"""Asynchronous MapLark OSM Features API client (httpx-based)."""

from __future__ import annotations

from typing import Any

import httpx

from .chunking import shapely_to_bbox
from ._geo_agent import (
    PLACES_NEARBY_PATH,
    PLACES_SEARCH_PATH,
    ROUTES_ISOCHRONE_PATH,
    ROUTES_OPTIMIZED_PATH_PATH,
    ROUTES_PATH_PATH,
    RouteTravelMode,
    places_details_path,
    places_nearby_body,
    places_search_body,
    routes_isochrone_body,
    routes_optimized_path_body,
    routes_path_body,
)
from ._http import (
    DEFAULT_BASE_URL,
    GEOJSON_ACCEPT,
    ElementType,
    ShapeType,
    build_params,
    build_rate_limit_error,
    is_429_retryable,
    is_geojson_accept,
    raise_for_response,
)
from .models import (
    BinaryQueryResult,
    OSMFeature,
    OSMFeatureCollection,
    ResponseMeta,
)
from .client import DEFAULT_MAX_FEATURES, _V3_PATH, _resolve_query_limit
from .retry import RetryConfig, retry_async


class AsyncOSMFeaturesClient:
    """Asynchronous client for the MapLark OSM Features API.

    Use as an async context manager::

        async with AsyncOSMFeaturesClient(api_key="...") as client:
            fc = await client.query_async(bbox="18.06,59.32,18.09,59.34", tags=["building"])

    Parameters
    ----------
    api_key:
        Your MapLark API key.
    base_url:
        API base URL.  Defaults to ``https://api.maplark.com``.
    retry_config:
        Retry / backoff settings.  Defaults to 3 retries with exponential
        backoff and jitter.
    timeout:
        HTTP request timeout in seconds.  Defaults to 60.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        retry_config: RetryConfig | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retry = retry_config or RetryConfig()
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self._headers,
                timeout=self._timeout,
            )
        return self._client

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _request(
        self, params: dict[str, Any], *, accept: str | None = None
    ) -> httpx.Response:
        param_list = build_params(params)
        client = await self._get_client()

        async def _do() -> httpx.Response:
            return await client.get(
                f"{self._base_url}{_V3_PATH}",
                params=param_list,
                headers={"Accept": accept or GEOJSON_ACCEPT},
            )

        resp = await retry_async(
            _do,
            self._retry,
            get_status=lambda r: r.status_code,
            get_headers=lambda r: dict(r.headers),
            is_rate_limit_error=is_429_retryable,
            build_rate_limit_error=build_rate_limit_error,
        )
        raise_for_response(resp)
        return resp

    async def _raw_query(
        self, params: dict[str, Any], *, accept: str | None = None
    ) -> OSMFeatureCollection:
        resp = await self._request(params, accept=accept)
        return OSMFeatureCollection.from_http(resp.json(), resp.headers)

    async def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        client = await self._get_client()

        async def _do() -> httpx.Response:
            return await client.post(f"{self._base_url}{path}", json=body)

        resp = await retry_async(
            _do,
            self._retry,
            get_status=lambda r: r.status_code,
            get_headers=lambda r: dict(r.headers),
            is_rate_limit_error=is_429_retryable,
            build_rate_limit_error=build_rate_limit_error,
        )
        raise_for_response(resp)
        return resp.json()  # type: ignore[no-any-return]

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        client = await self._get_client()

        async def _do() -> httpx.Response:
            return await client.get(f"{self._base_url}{path}", params=params or None)

        resp = await retry_async(
            _do,
            self._retry,
            get_status=lambda r: r.status_code,
            get_headers=lambda r: dict(r.headers),
            is_rate_limit_error=is_429_retryable,
            build_rate_limit_error=build_rate_limit_error,
        )
        raise_for_response(resp)
        return resp.json()  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def query_async(
        self,
        *,
        bbox: str | None = None,
        location: str | None = None,
        radius: float | None = None,
        type: ElementType | list[ElementType] | None = None,  # noqa: A002
        way_shape: ShapeType | None = None,
        shape: ShapeType | None = None,
        osm_ids: str | None = None,
        within: str | None = None,
        tags: list[str] | str | None = None,
        or_tags: list[str] | str | None = None,
        not_tags: list[str] | str | None = None,
        zoom: float | None = None,
        min_length_m: float | None = None,
        max_length_m: float | None = None,
        min_area_m2: float | None = None,
        max_area_m2: float | None = None,
        geometry: Any = None,
        centroid: bool = False,
        clip_geometry: bool | None = None,
        max_features: int | None = DEFAULT_MAX_FEATURES,
        accept: str | None = None,
        limit: int | None = None,
    ) -> OSMFeatureCollection | BinaryQueryResult:
        """One ``/v3/osm_features`` call. Same filters as :meth:`OSMFeaturesClient.query`.

        No cursor and no bbox tiling. Use the sync client for ``split_until_fit``.
        Non-GeoJSON ``accept`` returns :class:`BinaryQueryResult`.
        """
        if geometry is not None:
            bbox = shapely_to_bbox(geometry)

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
        if or_tags is not None:
            params["or_tags"] = or_tags
        if not_tags is not None:
            params["not_tags"] = not_tags
        resolved_limit = _resolve_query_limit(limit)
        if resolved_limit is not None:
            params["limit"] = resolved_limit
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
        if centroid:
            params["centroid"] = True
        if clip_geometry is not None:
            params["clip_geometry"] = clip_geometry

        if not is_geojson_accept(accept):
            resp = await self._request(params, accept=accept)
            return BinaryQueryResult.from_http(resp.content, resp.headers)

        collection = await self._raw_query(params)
        page = list(collection.get("features", []))
        truncated = collection.meta.has_more
        if max_features is not None and len(page) > max_features:
            page = page[:max_features]
            truncated = True
        return OSMFeatureCollection(
            features=[OSMFeature.from_dict(f) for f in page],
            meta=ResponseMeta(returned=len(page), has_more=truncated),
        )

    async def query_all_async(self, **kwargs: Any) -> OSMFeatureCollection | BinaryQueryResult:
        """Same as :meth:`query_async`."""
        return await self.query_async(**kwargs)

    async def count_async(
        self,
        *,
        group_by: str,
        bbox: str | None = None,
        location: str | None = None,
        radius: float | None = None,
        type: ElementType | list[ElementType] | None = None,  # noqa: A002
        way_shape: ShapeType | None = None,
        within: str | None = None,
        tags: list[str] | str | None = None,
        or_tags: list[str] | str | None = None,
        not_tags: list[str] | str | None = None,
        limit: int | None = None,
        min_length_m: float | None = None,
        max_length_m: float | None = None,
        min_area_m2: float | None = None,
        max_area_m2: float | None = None,
        disable_budget_warning: bool = False,
    ) -> dict[str, Any]:
        """``GET /v2/osm_features/count``: count features grouped by a tag key.

        Same as :meth:`OSMFeaturesClient.count`. Example::

            await client.count_async(
                group_by="amenity",
                bbox="18.05,59.32,18.10,59.34",
                type="node",
                tags=["amenity"],
            )
        """
        params: dict[str, Any] = {"group_by": group_by}
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
        if within is not None:
            params["within"] = within
        if tags is not None:
            params["tags"] = tags
        if or_tags is not None:
            params["or_tags"] = or_tags
        if not_tags is not None:
            params["not_tags"] = not_tags
        if limit is not None:
            params["limit"] = limit
        if min_length_m is not None:
            params["min_length_m"] = min_length_m
        if max_length_m is not None:
            params["max_length_m"] = max_length_m
        if min_area_m2 is not None:
            params["min_area_m2"] = min_area_m2
        if max_area_m2 is not None:
            params["max_area_m2"] = max_area_m2
        if disable_budget_warning:
            params["disable_budget_warning"] = disable_budget_warning

        param_list = build_params(params)
        client = await self._get_client()

        async def _do() -> httpx.Response:
            return await client.get(
                f"{self._base_url}/v2/osm_features/count",
                params=param_list,
            )

        resp = await retry_async(
            _do,
            self._retry,
            get_status=lambda r: r.status_code,
            get_headers=lambda r: dict(r.headers),
            is_rate_limit_error=is_429_retryable,
            build_rate_limit_error=build_rate_limit_error,
        )
        raise_for_response(resp)
        return resp.json()  # type: ignore[no-any-return]

    async def usage_async(self) -> dict[str, Any]:
        """Return this month's unit-budget usage for the authenticated API key."""
        return await self._get_json("/v1/usage")

    async def places_search_async(
        self,
        *,
        bbox: str | None = None,
        location: dict[str, float] | None = None,
        radius: float | None = None,
        type: str | None = None,  # noqa: A002
        tags: list[str] | None = None,
        or_tags: list[str] | None = None,
        limit: int | None = None,
        open_now: bool = False,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        """Find places via ``POST /v1/places/search``."""
        return await self._post_json(
            PLACES_SEARCH_PATH,
            places_search_body(
                bbox=bbox,
                location=location,
                radius=radius,
                type=type,
                tags=tags,
                or_tags=or_tags,
                limit=limit,
                open_now=open_now,
                as_of=as_of,
            ),
        )

    async def places_nearby_async(
        self,
        *,
        location: dict[str, float],
        radius: float | None = None,
        type: str | None = None,  # noqa: A002
        tags: list[str] | None = None,
        or_tags: list[str] | None = None,
        limit: int | None = None,
        open_now: bool = False,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        """Nearest places ranked by straight-line distance."""
        return await self._post_json(
            PLACES_NEARBY_PATH,
            places_nearby_body(
                location=location,
                radius=radius,
                type=type,
                tags=tags,
                or_tags=or_tags,
                limit=limit,
                open_now=open_now,
                as_of=as_of,
            ),
        )

    async def places_details_async(
        self,
        osm_type: str,
        osm_id: int | str | None = None,
    ) -> dict[str, Any]:
        """One place via ``GET /v1/places/{osm_type}/{osm_id}``.

        *osm_type* may be a search/nearby feature id (``node/123``) when
        *osm_id* is omitted.
        """
        return await self._get_json(places_details_path(osm_type, osm_id))

    async def routes_isochrone_async(
        self,
        *,
        origin: dict[str, float],
        max_distance_m: float | None = None,
        duration_s: float | None = None,
        search_buffer_m: float | None = None,
        travel_mode: RouteTravelMode | None = None,
    ) -> dict[str, Any]:
        """Reach polygon along the walk/bike network."""
        return await self._post_json(
            ROUTES_ISOCHRONE_PATH,
            routes_isochrone_body(
                origin=origin,
                max_distance_m=max_distance_m,
                duration_s=duration_s,
                search_buffer_m=search_buffer_m,
                travel_mode=travel_mode,
            ),
        )

    async def routes_path_async(
        self,
        *,
        stops: list[dict[str, float]],
        search_buffer_m: float | None = None,
        travel_mode: RouteTravelMode | None = None,
    ) -> dict[str, Any]:
        """Given-order walk/bike path."""
        return await self._post_json(
            ROUTES_PATH_PATH,
            routes_path_body(
                stops=stops,
                search_buffer_m=search_buffer_m,
                travel_mode=travel_mode,
            ),
        )

    async def routes_optimized_path_async(
        self,
        *,
        start: dict[str, float],
        stops: list[dict[str, float]],
        search_buffer_m: float | None = None,
        loop: bool | None = None,
        travel_mode: RouteTravelMode | None = None,
    ) -> dict[str, Any]:
        """TSP walk/bike tour from ``start``. ``loop`` returns to start."""
        return await self._post_json(
            ROUTES_OPTIMIZED_PATH_PATH,
            routes_optimized_path_body(
                start=start,
                stops=stops,
                search_buffer_m=search_buffer_m,
                loop=loop,
                travel_mode=travel_mode,
            ),
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "AsyncOSMFeaturesClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
