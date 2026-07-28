"""Synchronous OSM GeoJSON API client."""

from __future__ import annotations

import asyncio
from typing import Any

import requests

from .async_client import AsyncOSMGeoJSONClient
from .chunking import shapely_to_bbox
from ._http import (
    DEFAULT_BASE_URL,
    ElementType,
    ShapeType,
    build_params,
    build_rate_limit_error,
    is_429_retryable,
    raise_for_response,
)
from .models import (
    CostEstimate,
    OSMFeature,
    OSMFeatureCollection,
    ResponseMeta,
)
from ._pagination import paginate_all
from .retry import RetryConfig, retry


class OSMGeoJSONClient:
    """Synchronous client for the OSM GeoJSON API (MapLark).

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
        self._api_key = api_key
        self._session = requests.Session()
        self._session.headers.update({"Authorization": f"Bearer {api_key}"})

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raw_query(self, params: dict[str, Any]) -> dict[str, Any]:
        """Execute a single HTTP request and return the parsed JSON body."""
        param_list = build_params(params)

        def _do() -> requests.Response:
            return self._session.get(
                f"{self._base_url}/v2/osm_elements",
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
    # Public API
    # ------------------------------------------------------------------

    def query(
        self,
        *,
        bbox: str | None = None,
        around: str | None = None,
        type: ElementType | list[ElementType] | None = None,  # noqa: A002
        shape: ShapeType | None = None,
        osm_ids: str | None = None,
        tags: list[str] | str | None = None,
        or_tags: list[str] | str | None = None,
        not_tags: list[str] | str | None = None,
        limit: int = 1000,
        cursor: str | None = None,
        disable_budget_warning: bool = False,
        geometry: Any = None,
        centroid: bool = False,
    ) -> OSMFeatureCollection:
        """Fetch a single page of OSM elements.

        Parameters
        ----------
        bbox:
            Spatial filter as ``"min_lon,min_lat,max_lon,max_lat"``.
        around:
            Radius search as ``"lon,lat,radius_m"``.
        type:
            Element type(s) to return: ``"node"``, ``"way"``, or
            ``"relation"``.  Pass a list to request multiple types.
        shape:
            Geometry shape filter: ``"polygon"``, ``"line"``, or ``"all"``.
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
            Maximum features per page.  Defaults to 1000.
        cursor:
            Pagination cursor; use ``meta.next_cursor`` from the previous
            response. Omit to start from the first page.
        disable_budget_warning:
            Bypass the per-request unit cap.  The query runs and credits are
            still charged.
        geometry:
            Shapely geometry object.  Converted to ``bbox`` automatically
            (requires ``pip install osmgeojson[geo]``).
        centroid:
            When True, request ``properties.centroid`` on non-point features.
            Default False.
        """
        if geometry is not None:
            bbox = shapely_to_bbox(geometry)

        params: dict[str, Any] = {}
        if bbox is not None:
            params["bbox"] = bbox
        if around is not None:
            params["around"] = around
        if type is not None:
            params["type"] = type
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
        params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor
        if disable_budget_warning:
            params["disable_budget_warning"] = disable_budget_warning
        if centroid:
            params["centroid"] = True

        data = self._raw_query(params)
        return OSMFeatureCollection.from_dict(data)

    def query_all(self, *, page_size: int = 1000, **params: Any) -> OSMFeatureCollection:
        """Fetch *all* pages of OSM elements, auto-paginating until complete.

        Deduplicates features by their ``id`` field across pages.

        Parameters
        ----------
        page_size:
            Number of features to request per page.  Defaults to 1000.
        **params:
            Same as :meth:`query`.
        """
        if "geometry" in params:
            geom = params.pop("geometry")
            params["bbox"] = shapely_to_bbox(geom)

        all_features: list[dict[str, Any]] = []
        seen: set[str] = set()

        for page_features in paginate_all(self._raw_query, params, page_size=page_size):
            for feat in page_features:
                fid = feat.get("id", "")
                if fid not in seen:
                    seen.add(fid)
                    all_features.append(feat)

        return OSMFeatureCollection(
            features=[OSMFeature.from_dict(f) for f in all_features],
            meta=ResponseMeta(returned=len(all_features), has_more=False),
        )

    def query_large_area(
        self,
        bbox: str,
        *,
        max_chunk_area_deg2: float = 0.25,
        concurrency: int = 4,
        page_size: int = 1000,
        **params: Any,
    ) -> OSMFeatureCollection:
        """Query a large bounding box by splitting it into smaller chunks.

        The bbox is divided into a grid of cells each <= *max_chunk_area_deg2*
        square degrees.  Chunks are fetched in parallel (up to *concurrency*
        workers) and the results are merged and deduplicated.

        Parameters
        ----------
        bbox:
            ``"min_lon,min_lat,max_lon,max_lat"`` string.
        max_chunk_area_deg2:
            Maximum area per chunk in square degrees.  Tune this to stay
            within your tier's bbox area limit.
        concurrency:
            Number of parallel HTTP workers.
        page_size:
            Features per page within each chunk.
        **params:
            Extra query parameters passed to each chunk request.
        """
        async def _run() -> OSMFeatureCollection:
            async with AsyncOSMGeoJSONClient(
                api_key=self._api_key,
                base_url=self._base_url,
                retry_config=self._retry,
                timeout=self._timeout,
            ) as aclient:
                return await aclient.query_large_area_async(
                    bbox,
                    max_chunk_area_deg2=max_chunk_area_deg2,
                    concurrency=concurrency,
                    page_size=page_size,
                    **params,
                )

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(_run())

        raise RuntimeError(
            "query_large_area() cannot be called when an event loop is already running. "
            "Use AsyncOSMGeoJSONClient.query_large_area_async() instead."
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
        """Call ``/v2/osm_elements/cost`` to preflight the credit cost.

        Returns a :class:`CostEstimate` with ``estimated_credits``,
        ``tier_limits``, and any ``hints``.  No OSM data is queried.
        """
        if "geometry" in params:
            geom = params.pop("geometry")
            params["bbox"] = shapely_to_bbox(geom)

        param_list = build_params(params)

        def _do() -> requests.Response:
            return self._session.get(
                f"{self._base_url}/v2/osm_elements/cost",
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

    def close(self) -> None:
        """Close the underlying HTTP session."""
        self._session.close()

    def __enter__(self) -> "OSMGeoJSONClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
