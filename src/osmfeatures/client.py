"""Synchronous MapLark OSM Features API client."""

from __future__ import annotations

from typing import Any

import requests

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
from ._places import execute_places_search_sync
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
    execute_query_sync,
)
from .retry import RetryConfig, retry


class OSMFeaturesClient:
    """Synchronous client for the MapLark OSM Features API.

    Parameters
    ----------
    api_key:
        Your MapLark API key.  Can also be set via the ``MAPLARK_API_KEY``
        environment variable when using the CLI.
    base_url:
        API base URL.  Defaults to ``https://api.maplark.com``.  Override
        with ``MAPLARK_BASE_URL`` environment variable or this parameter.
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
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {api_key}"})
        self._account_tier: dict[str, Any] | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(
        self,
        params: dict[str, Any],
        *,
        accept: str | None = None,
        path: str = _V3_PATH,
    ) -> requests.Response:
        """Execute a single HTTP request and return the Response."""
        param_list = build_params(params)

        def _do() -> requests.Response:
            return self._session.get(
                f"{self._base_url}{path}",
                params=param_list,
                headers={"Accept": accept or GEOJSON_ACCEPT},
                timeout=self._timeout,
            )

        resp = retry(
            _do,
            self._retry,
            get_status=lambda r: r.status_code,
            get_headers=lambda r: dict(r.headers),
            is_rate_limit_error=is_429_retryable,
            build_rate_limit_error=build_rate_limit_error,
        )
        raise_for_response(resp)
        return resp

    def _raw_query(
        self,
        params: dict[str, Any],
        *,
        accept: str | None = None,
        path: str = _V3_PATH,
    ) -> OSMFeatureCollection:
        """Execute a single HTTP request and parse a GeoJSON FeatureCollection."""
        resp = self._request(params, accept=accept, path=path)
        return OSMFeatureCollection.from_http(resp.json(), resp.headers)

    def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST JSON to a geo-agent path; raise SDK errors on non-2xx."""

        def _do() -> requests.Response:
            return self._session.post(
                f"{self._base_url}{path}",
                json=body,
                timeout=self._timeout,
            )

        resp = retry(
            _do,
            self._retry,
            get_status=lambda r: r.status_code,
            get_headers=lambda r: dict(r.headers),
            is_rate_limit_error=is_429_retryable,
            build_rate_limit_error=build_rate_limit_error,
        )
        raise_for_response(resp)
        return resp.json()  # type: ignore[no-any-return]

    def _get_json(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET JSON from a geo-agent path; raise SDK errors on non-2xx."""
        param_list = build_params(params) if params else None

        def _do() -> requests.Response:
            return self._session.get(
                f"{self._base_url}{path}",
                params=param_list or None,
                timeout=self._timeout,
            )

        resp = retry(
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

    def query(
        self,
        *,
        bbox_tiles: int = 1,
        auto_split: bool = False,
        split_until_fit: bool | None = None,
        timeout: float | None = DEFAULT_QUERY_ALL_TIMEOUT_S,
        **params: Any,
    ) -> OSMFeatureCollection | BinaryQueryResult:
        return execute_query_sync(
            raw_query=self._raw_query,
            request=self._request,
            count=self.count,
            account_tier=self.account_tier,
            bbox_tiles=bbox_tiles,
            auto_split=auto_split,
            split_until_fit=split_until_fit,
            timeout=timeout,
            **params,
        )

    query.__doc__ = QUERY_DOC

    def query_all(self, **kwargs: Any) -> OSMFeatureCollection | BinaryQueryResult:
        """Same as :meth:`query`."""
        return self.query(**kwargs)

    def account_tier(self) -> dict[str, Any]:
        """Return this API key's plan limits (``GET /v1/account/tier``).

        Cached on the client. Includes ``max_limit`` (``/v3/osm_features``
        row cap), bbox area caps, and rate limits. ``query(auto_split=True)``
        uses ``max_limit`` as the density-split threshold.
        """
        if self._account_tier is None:
            self._account_tier = self._get_json(ACCOUNT_TIER_PATH)
        return self._account_tier

    def usage(self) -> dict[str, Any]:
        """Return this month's unit-budget usage for the authenticated API key.

        Calls ``GET /v1/usage`` and returns a dict with:

        - ``tier`` - your account tier label.
        - ``api_key_id`` - UUID of the active API key.
        - ``units_per_month`` - monthly budget (``None`` for unlimited tiers).
        - ``usage_this_month`` - units consumed so far this month.
        - ``remaining_this_month`` - units remaining (``None`` for unlimited).
        """

        def _do() -> requests.Response:
            return self._session.get(
                f"{self._base_url}/v1/usage",
                timeout=self._timeout,
            )

        resp = retry(
            _do,
            self._retry,
            get_status=lambda r: r.status_code,
            get_headers=lambda r: dict(r.headers),
            is_rate_limit_error=is_429_retryable,
            build_rate_limit_error=build_rate_limit_error,
        )
        raise_for_response(resp)
        return resp.json()  # type: ignore[no-any-return]

    def count(
        self,
        *,
        group_by: str | None = None,
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
        """``GET /v2/osm_features/count``: scalar total, or histogram with ``group_by``.

        Omit ``group_by`` for ``{groups: [], total, truncated: false}``.
        Places search is that total with ``way_shape="polygon"``.
        Same tag filters as :meth:`query` except ``osm_ids``. Spatial windows are larger
        than :meth:`query` when tags or ``group_by`` are set (country-scale on every
        tier) and billed count-only.
        ``limit`` is max histogram buckets (API default 100, max 10000). Example::

            client.count(
                group_by="amenity",
                bbox="18.05,59.32,18.10,59.34",
                type="node",
                tags=["amenity"],
            )
            # {"groups": [{"value": "restaurant", "count": 184},
            #             {"value": "cafe", "count": 91},
            #             {"value": "bar", "count": 47}],
            #  "total": 412, "truncated": False}
        """
        params: dict[str, Any] = {}
        if group_by is not None:
            params["group_by"] = group_by
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

        def _do() -> requests.Response:
            return self._session.get(
                f"{self._base_url}/v2/osm_features/count",
                params=param_list,
                timeout=self._timeout,
            )

        resp = retry(
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
    # Geo-agent (/v1/places/*, /v1/routes/*)
    # ------------------------------------------------------------------

    def places_search(
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
        """Find places via ``POST /v1/places/search`` (GeoJSON FeatureCollection).

        ``bbox_tiles`` and ``auto_split`` are client-side, like :meth:`query`,
        and **only apply to bbox search**. ``bbox_tiles`` splits the bbox up
        front (power of 2). ``auto_split`` counts first via :meth:`count`
        with ``way_shape="polygon"``, then fetches. No hours: split on
        ``total`` vs the key's ``max_limit``. ``asOf`` / ``openNow`` AND
        ``opening_hours``. An ``asOf`` page ``limit`` (default 100) truncates;
        split on the 10k parse cap only when ``limit`` is raised above 10k.
        ``openNow`` splits on that 10k scan cap even with a small fill limit.
        A leftover dense leaf may jump once more. A bbox-area 400 jumps to
        the tier limit in the error instead of probing every quarter, up
        to 32 tiles.
        location+radius is a circle and cannot split; :func:`around_to_bbox`
        turns a radius into a covering bbox if you need tiles (square around
        the circle; corners can fall outside the original radius).
        ``split_until_fit`` is a deprecated alias for ``auto_split``.
        """
        return execute_places_search_sync(
            post=lambda body: self._post_json(PLACES_SEARCH_PATH, body),
            count=self.count,
            account_tier=self.account_tier,
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

    def places_nearby(
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
        """Nearest places ranked by straight-line distance (``POST /v1/places/nearby``)."""
        return self._post_json(
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

    def places_details(
        self,
        osm_type: str,
        osm_id: int | str | None = None,
    ) -> dict[str, Any]:
        """One place via ``GET /v1/places/{osm_type}/{osm_id}``.

        *osm_type* may be a search/nearby feature id (``node/123``) when
        *osm_id* is omitted.
        """
        return self._get_json(places_details_path(osm_type, osm_id))

    def routes_isochrone(
        self,
        *,
        origin: dict[str, float],
        max_distance_m: float | None = None,
        duration_s: float | None = None,
        search_buffer_m: float | None = None,
        travel_mode: RouteTravelMode | None = None,
    ) -> dict[str, Any]:
        """Reach polygon along the walk/bike network (``POST /v1/routes/isochrone``)."""
        return self._post_json(
            ROUTES_ISOCHRONE_PATH,
            routes_isochrone_body(
                origin=origin,
                max_distance_m=max_distance_m,
                duration_s=duration_s,
                search_buffer_m=search_buffer_m,
                travel_mode=travel_mode,
            ),
        )

    def routes_path(
        self,
        *,
        stops: list[dict[str, float]],
        search_buffer_m: float | None = None,
        travel_mode: RouteTravelMode | None = None,
    ) -> dict[str, Any]:
        """Given-order walk/bike path (``POST /v1/routes/path``)."""
        return self._post_json(
            ROUTES_PATH_PATH,
            routes_path_body(
                stops=stops,
                search_buffer_m=search_buffer_m,
                travel_mode=travel_mode,
            ),
        )

    def routes_optimized_path(
        self,
        *,
        start: dict[str, float],
        stops: list[dict[str, float]],
        search_buffer_m: float | None = None,
        loop: bool | None = None,
        travel_mode: RouteTravelMode | None = None,
    ) -> dict[str, Any]:
        """TSP walk/bike tour from ``start`` (``POST /v1/routes/optimized_path``). ``loop`` returns to start."""
        return self._post_json(
            ROUTES_OPTIMIZED_PATH_PATH,
            routes_optimized_path_body(
                start=start,
                stops=stops,
                search_buffer_m=search_buffer_m,
                loop=loop,
                travel_mode=travel_mode,
            ),
        )

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def __enter__(self) -> "OSMFeaturesClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
