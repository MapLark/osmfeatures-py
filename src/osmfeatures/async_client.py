"""Asynchronous MapLark OSM Features API client (httpx-based)."""

from __future__ import annotations

from typing import Any

import httpx

from ._geo_agent import (
    PLACES_NEARBY_PATH,
    PLACES_SEARCH_PATH,
    ROUTES_ISOCHRONE_PATH,
    ROUTES_OPTIMIZED_PATH_PATH,
    ROUTES_PATH_PATH,
    RouteTravelMode,
    places_details_path,
    places_nearby_body,
    routes_isochrone_body,
    routes_optimized_path_body,
    routes_path_body,
)
from ._places import execute_places_search
from ._http import (
    ACCOUNT_TIER_PATH,
    DEFAULT_BASE_URL,
    GEOJSON_ACCEPT,
    ElementType,
    ShapeType,
    build_params,
    build_rate_limit_error,
    is_429_retryable,
    raise_for_response,
)
from .models import (
    BinaryQueryResult,
    OSMFeatureCollection,
)
from ._pagination import DEFAULT_QUERY_ALL_TIMEOUT_S
from ._query import (
    QUERY_DOC,
    _V3_PATH,
    execute_query,
)
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
        self._account_tier: dict[str, Any] | None = None

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
        self,
        params: dict[str, Any],
        *,
        accept: str | None = None,
        path: str = _V3_PATH,
    ) -> httpx.Response:
        param_list = build_params(params)
        client = await self._get_client()

        async def _do() -> httpx.Response:
            return await client.get(
                f"{self._base_url}{path}",
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
        self,
        params: dict[str, Any],
        *,
        accept: str | None = None,
        path: str = _V3_PATH,
    ) -> OSMFeatureCollection:
        resp = await self._request(params, accept=accept, path=path)
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
        bbox_tiles: int = 1,
        auto_split: bool = False,
        split_until_fit: bool | None = None,
        timeout: float | None = DEFAULT_QUERY_ALL_TIMEOUT_S,
        **params: Any,
    ) -> OSMFeatureCollection | BinaryQueryResult:
        return await execute_query(
            raw_query=self._raw_query,
            request=self._request,
            count=self.count_async,
            account_tier=self.account_tier_async,
            bbox_tiles=bbox_tiles,
            auto_split=auto_split,
            split_until_fit=split_until_fit,
            timeout=timeout,
            **params,
        )

    query_async.__doc__ = QUERY_DOC

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

    async def account_tier_async(self) -> dict[str, Any]:
        """Return this API key's plan limits (``GET /v1/account/tier``).

        Cached on the client. Same payload as
        :meth:`OSMFeaturesClient.account_tier`.
        """
        if self._account_tier is None:
            self._account_tier = await self._get_json(ACCOUNT_TIER_PATH)
        return self._account_tier

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
        bbox_tiles: int = 1,
        auto_split: bool = False,
        split_until_fit: bool | None = None,
        timeout: float | None = DEFAULT_QUERY_ALL_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Find places via ``POST /v1/places/search``.

        ``metadata.units`` is credits charged. ``bbox_tiles`` and ``auto_split``
        are client-side, like :meth:`query_async`, and **only apply to bbox search**.
        ``auto_split`` counts first, then fetches (same walk as
        :meth:`query_async`). location+radius is a circle and cannot split;
        :func:`around_to_bbox` turns a radius into a covering bbox if you
        need tiles (square around the circle; corners can fall outside the
        original radius).
        ``split_until_fit`` is a deprecated alias for ``auto_split``.
        """
        return await execute_places_search(
            post=lambda body: self._post_json(PLACES_SEARCH_PATH, body),
            count=self.count_async,
            bbox=bbox,
            location=location,
            radius=radius,
            type=type,
            tags=tags,
            or_tags=or_tags,
            limit=limit,
            open_now=open_now,
            as_of=as_of,
            bbox_tiles=bbox_tiles,
            auto_split=auto_split,
            split_until_fit=split_until_fit,
            timeout=timeout,
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
        """Nearest places ranked by straight-line distance.

        ``units`` is credits charged.
        """
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
        *osm_id* is omitted. ``units`` is credits charged.
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
