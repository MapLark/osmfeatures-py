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
    DEFAULT_TIMEOUT,
    GEOJSON_ACCEPT,
    ElementType,
    ShapeType,
    build_params,
    build_rate_limit_error,
    is_429_retryable,
    raise_for_response,
    response_headers_dict,
)
from .models import (
    BinaryQueryResult,
    DensityFeatureCollection,
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
        HTTP request timeout in seconds.  Defaults to
        :data:`~osmfeatures.DEFAULT_TIMEOUT`.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        retry_config: RetryConfig | None = None,
        timeout: float = DEFAULT_TIMEOUT,
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
        """POST JSON to a geo-agent path; raise SDK errors on non-2xx.

        Retries only transient 429. Places/routes may already be billed on 5xx.
        """

        def _do() -> requests.Response:
            return self._session.post(
                f"{self._base_url}{path}",
                json=body,
                timeout=self._timeout,
            )

        resp = retry(
            _do,
            self._retry.for_non_idempotent(),
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
        return execute_query_sync(
            raw_query=self._raw_query,
            request=self._request,
            count=self.count,
            account_tier=self.account_tier,
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
            accept=accept,
            limit=limit,
            disable_budget_warning=disable_budget_warning,
            bbox_tiles=bbox_tiles,
            auto_split=auto_split,
            auto_split_skip_count=auto_split_skip_count,
            timeout=timeout,
        )

    query.__doc__ = QUERY_DOC

    def query_all(self, **kwargs: Any) -> OSMFeatureCollection | BinaryQueryResult:
        """Alias for :meth:`query`."""
        return self.query(**kwargs)

    def account_tier(self) -> dict[str, Any]:
        """Return this API key's plan limits (``GET /v1/account/tier``).

        The result is cached on the client. Includes ``max_limit`` (the
        per-request feature cap), bbox area caps, and rate limits.
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
        limit: int | None = None,
        min_length_m: float | None = None,
        max_length_m: float | None = None,
        min_area_m2: float | None = None,
        max_area_m2: float | None = None,
        disable_budget_warning: bool = False,
    ) -> dict[str, Any]:
        """Count matching OSM features, optionally grouped by a tag.

        Without ``group_by`` the result is a scalar total. With ``group_by``
        it is a histogram of tag values.

        Parameters
        ----------
        group_by:
            Tag key to bucket results by, e.g. ``"amenity"``.
        bbox, location, radius, type, way_shape, within:
            Spatial filters, as in :meth:`query`.
        tags:
            Tag filter: a string, or a list ANDed together. Example:
            ``["amenity", "!amenity=bench"]``. See :meth:`query`.
        limit:
            Maximum histogram buckets (API default 100, max 10000).
        min_length_m, max_length_m, min_area_m2, max_area_m2:
            Geometry size filters.
        disable_budget_warning:
            Suppress the unit-budget warning for large requests.

        Returns
        -------
        dict
            ``{"groups": [...], "total": int, "truncated": bool}``, plus
            ``response_headers``.

        Example
        -------
        ::

            client.count(
                group_by="amenity",
                bbox="18.05,59.32,18.10,59.34",
                type="node",
                tags=["amenity", "!amenity=bench"],
            )
            # {"groups": [{"value": "restaurant", "count": 184}, ...],
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
        body = resp.json()
        if not isinstance(body, dict):
            raise ValueError("count response was not a JSON object")
        body["response_headers"] = response_headers_dict(resp.headers)
        return body  # type: ignore[no-any-return]

    def density(
        self,
        *,
        cell_deg: float | None = None,
        bbox: str | None = None,
        location: str | None = None,
        radius: float | None = None,
        type: ElementType | list[ElementType] | None = None,  # noqa: A002
        way_shape: ShapeType | None = None,
        within: str | None = None,
        tags: list[str] | str | None = None,
        limit: int | None = None,
        min_length_m: float | None = None,
        max_length_m: float | None = None,
        min_area_m2: float | None = None,
        max_area_m2: float | None = None,
        disable_budget_warning: bool = False,
    ) -> DensityFeatureCollection:
        """Count features per grid cell, returned as occupied cells (points).

        Useful for heatmaps. ``tags`` is required and must be a positive
        filter, e.g. ``"place"`` or ``"shop|amenity"``.

        Parameters
        ----------
        cell_deg:
            Grid cell size in degrees (API default 0.1).
        bbox, location, radius, type, way_shape, within:
            Spatial filters, as in :meth:`query`. ``type`` defaults to nodes.
        tags:
            Required tag filter, as in :meth:`query`.
        limit:
            Maximum occupied cells returned (defaults to the key's ``max_limit``).
        min_length_m, max_length_m, min_area_m2, max_area_m2:
            Geometry size filters.
        disable_budget_warning:
            Set True for large areas, such as a continent, that would
            otherwise fail the per-request unit cap.

        Returns
        -------
        DensityFeatureCollection
            One point per occupied cell, with ``properties.count``. Also
            exposes ``total`` and ``truncated``.

        Example
        -------
        ::

            client.density(
                bbox="-26,33,52,72",
                tags="place",
                cell_deg=0.1,
                disable_budget_warning=True,
            )
        """
        params: dict[str, Any] = {}
        if cell_deg is not None:
            params["cell_deg"] = cell_deg
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
                f"{self._base_url}/v2/osm_features/density",
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
        body = resp.json()
        if not isinstance(body, dict):
            raise ValueError("density response was not a JSON object")
        return DensityFeatureCollection.from_http(body, resp.headers)

    # ------------------------------------------------------------------
    # Geo-agent (/v1/places/*, /v1/routes/*)
    # ------------------------------------------------------------------

    def places_search(
        self,
        *,
        bbox: str | None = None,
        location: dict[str, float] | None = None,
        radius: float | None = None,
        tags: list[str] | str | None = None,
        limit: int | None = None,
        open_now: bool = False,
        as_of: str | None = None,
        bbox_tiles: int = 1,
        auto_split: bool = False,
            auto_split_skip_count: bool = True,
        timeout: float | None = DEFAULT_QUERY_ALL_TIMEOUT_S,
    ) -> dict[str, Any]:
        """Search for places within a bbox or a circle.

        Calls ``POST /v1/places/search``. Pass either ``bbox`` or
        ``location`` + ``radius``, not both. Polygon places are returned
        as centroid points.

        Parameters
        ----------
        bbox:
            ``"min_lon,min_lat,max_lon,max_lat"``.
        location:
            Circle centre as ``{"lat": ..., "lng": ...}``. Use with ``radius``.
        radius:
            Circle radius in metres.
        tags:
            Tag filter, as in :meth:`query`.
        limit:
            Maximum places returned.
        open_now:
            Keep only places open now. Adds an ``opening_hours`` tag filter.
        as_of:
            ISO timestamp for the opening-hours check, used with ``open_now``.
        bbox_tiles:
            Split ``bbox`` into this many tiles (power of 2) and merge the results.
        auto_split:
            Split a bbox that is too large for your tier into smaller tiles
            and merge the results. Bbox only.
        auto_split_skip_count:
            With ``auto_split``, fetch tiles directly and halve any that are
            too large (default). Set False in dense cities, where each tile
            is counted first to avoid billed failures.
        timeout:
            Wall-clock seconds allowed for a tiled search. ``None`` for no cap.
            Raises ``OSMFeaturesTimeoutError`` when exceeded.

        Returns
        -------
        dict
            A GeoJSON FeatureCollection.
        """
        return execute_places_search_sync(
            post=lambda body: self._post_json(PLACES_SEARCH_PATH, body),
            count=self.count,
            account_tier=self.account_tier,
            bbox=bbox,
            location=location,
            radius=radius,
            tags=tags,
            limit=limit,
            open_now=open_now,
            as_of=as_of,
            bbox_tiles=bbox_tiles,
            auto_split=auto_split,
            auto_split_skip_count=auto_split_skip_count,
            timeout=timeout,
        )

    def places_nearby(
        self,
        *,
        location: dict[str, float],
        radius: float | None = None,
        tags: list[str] | str | None = None,
        limit: int | None = None,
        open_now: bool = False,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        """Find the places nearest to a point, ranked by straight-line distance.

        Calls ``POST /v1/places/nearby``.

        Parameters
        ----------
        location:
            Centre point as ``{"lat": ..., "lng": ...}``.
        radius:
            Maximum distance in metres.
        tags:
            Tag filter, as in :meth:`query`.
        limit:
            Maximum places returned.
        open_now:
            Keep only places open now.
        as_of:
            ISO timestamp for the opening-hours check, used with ``open_now``.

        Returns
        -------
        dict
            A GeoJSON FeatureCollection, nearest first.
        """
        return self._post_json(
            PLACES_NEARBY_PATH,
            places_nearby_body(
                location=location,
                radius=radius,
                tags=tags,
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
        """Fetch one place by OSM type and id (``GET /v1/places/{osm_type}/{osm_id}``).

        Parameters
        ----------
        osm_type:
            ``"node"``, ``"way"`` or ``"relation"``. May also be a combined
            id such as ``"node/123"``, as returned by search, if ``osm_id``
            is omitted.
        osm_id:
            OSM element id. Omit when ``osm_type`` is a combined id.
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
        """Compute the area reachable from an origin on the walk/bike network.

        Calls ``POST /v1/routes/isochrone``. Set either ``max_distance_m`` or
        ``duration_s``.

        Parameters
        ----------
        origin:
            Starting point as ``{"lat": ..., "lng": ...}``.
        max_distance_m:
            Maximum travel distance in metres.
        duration_s:
            Maximum travel time in seconds.
        search_buffer_m:
            Search buffer in metres around the route, sent to the API as is.
        travel_mode:
            ``"WALK"`` or ``"BICYCLE"``.
        """
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
        """Route through stops in the given order (``POST /v1/routes/path``).

        Parameters
        ----------
        stops:
            Ordered points, each as ``{"lat": ..., "lng": ...}``.
        search_buffer_m:
            Search buffer in metres around the route, sent to the API as is.
        travel_mode:
            ``"WALK"`` or ``"BICYCLE"``.
        """
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
        """Visit all stops in the shortest order, starting from ``start``.

        Calls ``POST /v1/routes/optimized_path``.

        Parameters
        ----------
        start:
            Starting point as ``{"lat": ..., "lng": ...}``.
        stops:
            Points to visit, each as ``{"lat": ..., "lng": ...}``.
        search_buffer_m:
            Search buffer in metres around the route, sent to the API as is.
        loop:
            If True, the route returns to ``start``.
        travel_mode:
            ``"WALK"`` or ``"BICYCLE"``.
        """
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
