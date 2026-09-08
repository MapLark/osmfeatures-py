"""Synchronous MapLark OSM Features API client."""

from __future__ import annotations

from typing import Any

import requests

from .chunking import merge_features, shapely_to_bbox, split_bbox_tiles
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
    CostEstimate,
    OSMFeature,
    OSMFeatureCollection,
    ResponseMeta,
)
from ._pagination import paginate_all
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
        HTTP request timeout in seconds.  Defaults to 30.
    """

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        retry_config: RetryConfig | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._retry = retry_config or RetryConfig()
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {api_key}"})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _request(self, params: dict[str, Any], *, accept: str | None = None) -> requests.Response:
        """Execute a single HTTP request and return the Response."""
        param_list = build_params(params)

        def _do() -> requests.Response:
            return self._session.get(
                f"{self._base_url}/v2/osm_features",
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

    def _raw_query(self, params: dict[str, Any], *, accept: str | None = None) -> OSMFeatureCollection:
        """Execute a single HTTP request; pagination comes from response headers."""
        resp = self._request(params, accept=accept)
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

        def _do() -> requests.Response:
            return self._session.get(
                f"{self._base_url}{path}",
                params=params or None,
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
        type: ElementType | list[ElementType] | None = None,  # noqa: A002
        way_shape: ShapeType | None = None,
        shape: ShapeType | None = None,
        osm_ids: str | None = None,
        tags: list[str] | str | None = None,
        or_tags: list[str] | str | None = None,
        not_tags: list[str] | str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        zoom: float | None = None,
        min_length_m: float | None = None,
        max_length_m: float | None = None,
        min_area_m2: float | None = None,
        max_area_m2: float | None = None,
        disable_budget_warning: bool = False,
        geometry: Any = None,
        centroid: bool = False,
        clip_geometry: bool | None = None,
        accept: str | None = None,
    ) -> OSMFeatureCollection | BinaryQueryResult:
        """Fetch a single page of OSM elements.

        Parameters
        ----------
        bbox:
            Spatial filter as ``"min_lon,min_lat,max_lon,max_lat"``.
        location:
            Point for a radius search as ``"lat,lng"``. Requires ``radius``.
        radius:
            Search radius in metres. Requires ``location``.
        type:
            Element type(s) to return: ``"node"``, ``"way"``, or
            ``"relation"``.  Pass a list to request multiple types.
        way_shape:
            Geometry class for ways/relations: ``"polygon"`` or ``"line"``.
            Omit for both shapes; ``"all"`` also means both.
        shape:
            Deprecated alias for ``way_shape``.
        osm_ids:
            Comma-separated OSM IDs for direct lookup.  Mutually exclusive
            with spatial / tag filters.
        tags:
            AND-combined tag filters, e.g. ``"building"`` or
            ``"amenity=cafe"``.  Pass a list for multiple filters.
        or_tags:
            OR-combined tag filters.  Requires a spatial anchor.
        not_tags:
            Exclusion tag filters.  Requires a spatial anchor.
        limit:
            Maximum features per page. Omit to use the API default (1000).
        cursor:
            Pagination cursor; use ``meta.next_cursor`` (from ``X-Next-Cursor``)
            of the previous response. Omit to start from the first page.
        zoom:
            Map zoom level used to simplify geometry at lower zooms.
        min_length_m:
            Minimum line length in metres (inclusive). Applies to line geometry.
        max_length_m:
            Maximum line length in metres (inclusive). Applies to line geometry.
        min_area_m2:
            Minimum polygon area in square metres (inclusive). Applies to polygon geometry.
        max_area_m2:
            Maximum polygon area in square metres (inclusive). Applies to polygon geometry.
        disable_budget_warning:
            Bypass the per-request unit cap.  The query runs and credits are
            still charged.
        geometry:
            Shapely geometry object.  Converted to ``bbox`` automatically
            (requires ``pip install osmfeatures[geo]``).
        centroid:
            When True, request ``properties.centroid`` on non-point features.
            Omit to use the API default (False).
        clip_geometry:
            When True, clip returned geometry to the requested bbox.
            Omit to use the API default (True). Set False to return full
            geometry for features intersecting the bbox.
        accept:
            ``Accept`` media type. Default / ``application/geo+json`` returns
            ``OSMFeatureCollection``. Other types (``text/csv``,
            ``text/tab-separated-values``, ``application/flatgeobuf``,
            ``application/vnd.apache.parquet``) return :class:`BinaryQueryResult`
            with body bytes and pagination ``meta`` (for manual ``cursor`` paging).
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
        if tags is not None:
            params["tags"] = tags
        if or_tags is not None:
            params["or_tags"] = or_tags
        if not_tags is not None:
            params["not_tags"] = not_tags
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
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
        if disable_budget_warning:
            params["disable_budget_warning"] = disable_budget_warning
        if centroid:
            params["centroid"] = True
        if clip_geometry is not None:
            params["clip_geometry"] = clip_geometry

        if is_geojson_accept(accept):
            return self._raw_query(params, accept=accept)
        resp = self._request(params, accept=accept)
        return BinaryQueryResult.from_http(resp.content, resp.headers)

    def query_all(
        self,
        *,
        limit_per_page: int | None = None,
        bbox_tiles: int = 2,
        max_features: int | None = 55_000,
        **params: Any,
    ) -> OSMFeatureCollection:
        """Fetch *all* pages of OSM elements, auto-paginating until complete.

        When ``bbox`` is present, splits it into *bbox_tiles* sub-bboxes
        (power of 2; default 2), paginates each tile sequentially, then
        merges and deduplicates by feature ``id``. Use ``bbox_tiles=1`` to
        disable tiling.

        Parameters
        ----------
        limit_per_page:
            Upstream ``limit`` per HTTP request (page size). Omit to use the
            API default (1000).
        bbox_tiles:
            Number of bbox tiles (power of 2). Defaults to 2. Ignored when
            there is no ``bbox``.
        max_features:
            Cap on merged features. Defaults to 55_000. Pass ``None`` for no
            upper limit (API rate limits still apply).
        **params:
            Same as :meth:`query`, except ``limit`` and ``cursor`` (managed
            internally). Non-GeoJSON ``accept`` is not supported here.
        """
        if "limit" in params:
            raise ValueError(
                "query_all does not take limit; use limit_per_page (page size) "
                "and max_features (total cap)"
            )
        if not is_geojson_accept(params.pop("accept", None)):
            raise TypeError(
                "query_all() only supports GeoJSON; use query(accept=...) for binary/table encodings"
            )
        if "cursor" in params:
            raise ValueError("query_all manages cursors; do not pass cursor")

        if "geometry" in params:
            geom = params.pop("geometry")
            params["bbox"] = shapely_to_bbox(geom)

        bbox = params.get("bbox")
        tile_bboxes = (
            split_bbox_tiles(bbox, bbox_tiles) if isinstance(bbox, str) else [None]
        )

        feature_lists: list[list[dict[str, Any]]] = []
        count = 0
        truncated = False
        for tile_bbox in tile_bboxes:
            if max_features is not None and count >= max_features:
                truncated = True
                break
            tile_params = dict(params)
            if tile_bbox is not None:
                tile_params["bbox"] = tile_bbox
            tile_features: list[dict[str, Any]] = []
            for page_features in paginate_all(
                self._raw_query, tile_params, limit_per_page=limit_per_page
            ):
                if max_features is not None:
                    room = max_features - count
                    if room <= 0:
                        truncated = True
                        break
                    if len(page_features) > room:
                        tile_features.extend(page_features[:room])
                        count += room
                        truncated = True
                        break
                tile_features.extend(page_features)
                count += len(page_features)
            feature_lists.append(tile_features)

        all_features = merge_features(feature_lists)
        if max_features is not None and len(all_features) > max_features:
            all_features = all_features[:max_features]
            truncated = True
        return OSMFeatureCollection(
            features=[OSMFeature.from_dict(f) for f in all_features],
            meta=ResponseMeta(returned=len(all_features), has_more=truncated),
        )

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

    def estimate_cost(self, **params: Any) -> CostEstimate:
        """Call ``/v2/osm_features/cost`` to preflight the credit cost.

        Returns a :class:`CostEstimate` with ``estimated_credits``,
        ``tier_limits``, and any ``hints``.  No OSM data is queried.
        """
        if "geometry" in params:
            geom = params.pop("geometry")
            params["bbox"] = shapely_to_bbox(geom)

        param_list = build_params(params)

        def _do() -> requests.Response:
            return self._session.get(
                f"{self._base_url}/v2/osm_features/cost",
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
        return CostEstimate.from_dict(resp.json())

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
    ) -> dict[str, Any]:
        """Find places via ``POST /v1/places/search`` (GeoJSON FeatureCollection)."""
        return self._post_json(
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
