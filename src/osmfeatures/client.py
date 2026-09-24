"""Synchronous MapLark OSM Features API client."""

from __future__ import annotations

import time
from collections import deque
from typing import Any

import requests

from .chunking import (
    merge_features,
    shapely_to_bbox,
    split_bbox_tiles,
)
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
    OSMFeaturesAPIError,
    OSMFeaturesTimeoutError,
    ResponseMeta,
)
from ._pagination import DEFAULT_QUERY_ALL_TIMEOUT_S, query_all_deadline
from .retry import RetryConfig, retry

_V3_PATH = "/v3/osm_features"
# split_until_fit when the caller omitted limit. Free max_limit; paid keys allow more.
_V3_SPLIT_WHEN_LIMIT_OMITTED = 50_000
# Enterprise TIER_LIMITS max_limit. The API rejects limit_exceeds_tier above the key.
_V3_MAX_LIMIT = 1_000_000


def _resolve_query_limit(limit: Any) -> int | None:
    """Validate ``limit``. ``None`` omits it so the API uses the key's max_limit."""
    if limit is None:
        return None
    try:
        value = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError("limit must be an integer") from exc
    if value < 1 or value > _V3_MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {_V3_MAX_LIMIT}")
    return value


def _with_limit(params: dict[str, Any], limit: int | None) -> dict[str, Any]:
    out = dict(params)
    if limit is not None:
        out["limit"] = limit
    return out


DEFAULT_MAX_FEATURES = 1_000_000
_V3_SPLIT_DEPTH = 6
# Same keys /v2/osm_features/count refuses as group_by.
_COUNT_UNBOUNDED_KEYS = frozenset({"name", "ref", "addr:housenumber"})


def _is_result_too_large(exc: BaseException) -> bool:
    return (
        isinstance(exc, OSMFeaturesAPIError)
        and exc.status_code == 400
        and "result_too_large" in str(exc)
    )


def _count_group_key(tags: Any) -> str | None:
    """AND-tag key whose count ``total`` is the match count, or None."""
    if tags is None:
        return None
    items = [tags] if isinstance(tags, str) else list(tags)
    if not items:
        return None
    item = str(items[0])
    key = item
    for op in (">=", "<=", ">", "<", "="):
        if op in item:
            key = item.split(op, 1)[0]
            break
    key = key.strip()
    if not key or key in _COUNT_UNBOUNDED_KEYS:
        return None
    return key


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
        bbox_tiles: int = 1,
        split_until_fit: bool = False,
        max_features: int | None = DEFAULT_MAX_FEATURES,
        timeout: float | None = DEFAULT_QUERY_ALL_TIMEOUT_S,
        **params: Any,
    ) -> OSMFeatureCollection | BinaryQueryResult:
        """One ``/v3/osm_features`` call for a tile that fits in ``limit`` features.

        Omit ``limit`` for the key's ``max_limit``. A lower ``limit`` truncates
        (``meta.has_more``). A match set larger than the caller's ``max_limit``
        is HTTP 400 ``result_too_large``. ``split_until_fit=True`` counts first
        and quarters the bbox before that fetch. That adds latency.
        ``within``, radius, and ``osm_ids`` cannot be split, so the API error
        propagates.

        Parameters
        ----------
        bbox_tiles:
            Split the requested bbox into this many tiles (power of 2).
            Default 1 sends the bbox as one request. Use 2, 4, 8, ... to stay
            under a tier area cap.
        split_until_fit:
            Count matches first, then fetch. Quarter until each piece
            fits. Default False. Avoids a billed result_too_large when
            count can cover the same rows. Slower: a count per tile.
        max_features:
            Cap on merged features. Default 1_000_000. ``None`` means no cap.
        timeout:
            Wall-clock seconds for this call. Defaults to 60. ``None`` is no cap.
        **params:
            Filter kwargs: ``bbox``, ``location``, ``radius``, ``type``,
            ``way_shape``, ``shape``, ``osm_ids``, ``within``, ``tags``,
            ``or_tags``, ``not_tags``, ``zoom``, size bounds, ``centroid``,
            ``clip_geometry``, ``geometry``, ``accept``, ``limit``. No
            ``cursor``. ``disable_budget_warning`` is count-only. An AND
            ``tags`` key is the count ``group_by``.

            ``limit`` is the maximum features in the response (omit for the
            key's ``max_limit``, client max 1000000). A lower limit truncates. A match
            set larger than the caller's ``max_limit`` is HTTP 400
            ``result_too_large``. ``split_until_fit`` counts first and
            quarters the bbox before that fetch.

            ``accept`` selects the encoding (default GeoJSON). Non-GeoJSON
            (CSV, TSV, FlatGeobuf, GeoParquet) is one request and returns
            :class:`BinaryQueryResult`. Those encodings cannot tile or trim.
        """
        if "cursor" in params:
            raise ValueError("query does not take cursor")
        # v3 rejects this; keep it only for count() during split_until_fit.
        disable_budget_warning = bool(params.pop("disable_budget_warning", False))
        limit = _resolve_query_limit(params.pop("limit", None))
        accept = params.pop("accept", None)
        if not is_geojson_accept(accept):
            if split_until_fit:
                raise ValueError("split_until_fit only works with GeoJSON")
            if bbox_tiles != 1:
                raise ValueError("bbox_tiles only works with GeoJSON")
            if "geometry" in params:
                geom = params.pop("geometry")
                params["bbox"] = shapely_to_bbox(geom)
            resp = self._request(
                _with_limit(params, limit),
                accept=accept,
                path=_V3_PATH,
            )
            return BinaryQueryResult.from_http(resp.content, resp.headers)

        if "geometry" in params:
            geom = params.pop("geometry")
            params["bbox"] = shapely_to_bbox(geom)

        bbox = params.get("bbox")
        deadline = query_all_deadline(timeout)

        def _deadline() -> None:
            if deadline is not None and time.monotonic() >= deadline:
                raise OSMFeaturesTimeoutError(
                    f"query exceeded {timeout}s timeout",
                    timeout=timeout,
                )

        api_truncated = False

        def _query_tile(tile: str | None) -> list[dict[str, Any]] | None:
            nonlocal api_truncated
            qparams = _with_limit(params, limit)
            if tile is not None:
                qparams["bbox"] = tile
            try:
                collection = self._raw_query(qparams, path=_V3_PATH)
            except OSMFeaturesAPIError as exc:
                if _is_result_too_large(exc) and split_until_fit:
                    return None
                raise
            features = list(collection.get("features", []))
            if collection.meta.has_more:
                api_truncated = True
            return features

        def _match_count(tile: str) -> int | None:
            """Count total for this tile. None when count cannot cover the same rows."""
            key = _count_group_key(params.get("tags"))
            if key is None:
                return None
            stat: dict[str, Any] = {"bbox": tile}
            for name in (
                "location", "radius", "type", "within", "tags", "or_tags", "not_tags",
                "min_length_m", "max_length_m", "min_area_m2", "max_area_m2",
            ):
                if params.get(name) is not None:
                    stat[name] = params[name]
            way_shape = params.get("way_shape", params.get("shape"))
            if way_shape is not None:
                stat["way_shape"] = way_shape
            if disable_budget_warning:
                stat["disable_budget_warning"] = True
            body = self.count(group_by=key, limit=1, **stat)
            return int(body["total"])

        if not isinstance(bbox, str):
            if bbox_tiles != 1:
                raise ValueError("bbox_tiles requires bbox")
            collection = self._raw_query(
                _with_limit(params, limit),
                path=_V3_PATH,
            )
            page = list(collection.get("features", []))
            truncated = collection.meta.has_more
            if max_features is not None and len(page) > max_features:
                page = page[:max_features]
                truncated = True
            return OSMFeatureCollection(
                features=[OSMFeature.from_dict(f) for f in page],
                meta=ResponseMeta(returned=len(page), has_more=truncated),
            )

        tiles: deque[tuple[str, int]] = deque(
            (t, 0) for t in split_bbox_tiles(bbox, bbox_tiles)
        )
        feature_lists: list[list[dict[str, Any]]] = []
        count = 0
        truncated = False
        started = False
        tile_cap = limit if limit is not None else _V3_SPLIT_WHEN_LIMIT_OMITTED

        def _enqueue_quarters(tile: str, depth: int) -> None:
            if depth >= _V3_SPLIT_DEPTH:
                raise RuntimeError(
                    f"query tile still overflows after "
                    f"{_V3_SPLIT_DEPTH} splits: {tile}"
                )
            for quarter in split_bbox_tiles(tile, 4):
                tiles.append((quarter, depth + 1))

        while tiles:
            if started:
                _deadline()
            started = True
            if max_features is not None and count >= max_features:
                truncated = True
                break
            tile, depth = tiles.popleft()
            if split_until_fit:
                total = _match_count(tile)
                if total is not None and total > tile_cap:
                    _enqueue_quarters(tile, depth)
                    continue
                if total == 0:
                    continue
            page = _query_tile(tile)
            if page is None:
                _enqueue_quarters(tile, depth)
                continue
            if max_features is not None:
                room = max_features - count
                if len(page) > room:
                    if room > 0:
                        feature_lists.append(page[:room])
                    count += max(room, 0)
                    truncated = True
                    break
            feature_lists.append(page)
            count += len(page)

        all_features = merge_features(feature_lists)
        if max_features is not None and len(all_features) > max_features:
            all_features = all_features[:max_features]
            truncated = True
        return OSMFeatureCollection(
            features=[OSMFeature.from_dict(f) for f in all_features],
            meta=ResponseMeta(
                returned=len(all_features),
                has_more=truncated or api_truncated,
            ),
        )

    def query_all(self, **kwargs: Any) -> OSMFeatureCollection | BinaryQueryResult:
        """Same as :meth:`query`."""
        return self.query(**kwargs)

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

        Same tag filters as :meth:`query` except ``osm_ids``. Spatial windows are larger
        than :meth:`query` (country-scale on every tier) and billed count-only.
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
