"""Asynchronous OSM GeoJSON API client (httpx-based)."""

from __future__ import annotations

from typing import Any

import httpx

from .chunking import merge_features, shapely_to_bbox, split_bbox_tiles
from ._http import DEFAULT_BASE_URL, ElementType, ShapeType, build_params, build_rate_limit_error, is_429_retryable, raise_for_response
from .models import (
    CostEstimate,
    OSMFeature,
    OSMFeatureCollection,
    ResponseMeta,
)
from ._pagination import paginate_all_async
from .retry import RetryConfig, retry_async



class AsyncOSMGeoJSONClient:
    """Asynchronous client for the OSM GeoJSON API (MapLark).

    Use as an async context manager::

        async with AsyncOSMGeoJSONClient(api_key="...") as client:
            fc = await client.query(bbox="18.06,59.32,18.09,59.34", tags=["building"])

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

    async def _raw_query(self, params: dict[str, Any]) -> dict[str, Any]:
        param_list = build_params(params)
        client = await self._get_client()

        async def _do() -> httpx.Response:
            return await client.get(
                f"{self._base_url}/v2/osm_elements",
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

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def query_async(
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
        zoom: float | None = None,
        min_length_m: float | None = None,
        max_length_m: float | None = None,
        min_area_m2: float | None = None,
        max_area_m2: float | None = None,
        disable_budget_warning: bool = False,
        geometry: Any = None,
        centroid: bool = False,
    ) -> OSMFeatureCollection:
        """Fetch a single page of OSM elements asynchronously.

        Accepts the same parameters as :meth:`OSMGeoJSONClient.query`.
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

        data = await self._raw_query(params)
        return OSMFeatureCollection.from_dict(data)

    async def query_all_async(
        self,
        *,
        limit_per_page: int = 1000,
        bbox_tiles: int = 2,
        max_features: int | None = 55_000,
        **params: Any,
    ) -> OSMFeatureCollection:
        """Fetch *all* pages of OSM elements asynchronously, auto-paginating.

        When ``bbox`` is present, splits it into *bbox_tiles* sub-bboxes
        (power of 2; default 2), paginates each tile sequentially, then
        merges and deduplicates by feature ``id``. Use ``bbox_tiles=1`` to
        disable tiling.

        ``max_features`` defaults to 55_000; pass ``None`` for no upper limit.
        Do not pass ``limit`` or ``cursor`` (use ``limit_per_page`` / managed
        pagination).
        """
        if "limit" in params:
            raise ValueError(
                "query_all_async does not take limit; use limit_per_page (page size) "
                "and max_features (total cap)"
            )
        if "cursor" in params:
            raise ValueError("query_all_async manages cursors; do not pass cursor")

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
            async for page_features in paginate_all_async(
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

        deduped = merge_features(feature_lists)
        if max_features is not None and len(deduped) > max_features:
            deduped = deduped[:max_features]
            truncated = True
        return OSMFeatureCollection(
            features=[OSMFeature.from_dict(f) for f in deduped],
            meta=ResponseMeta(returned=len(deduped), has_more=truncated),
        )

    async def estimate_cost_async(self, **params: Any) -> CostEstimate:
        """Call ``/v2/osm_elements/cost`` to preflight the credit cost."""
        if "geometry" in params:
            geom = params.pop("geometry")
            params["bbox"] = shapely_to_bbox(geom)

        param_list = build_params(params)
        client = await self._get_client()

        async def _do() -> httpx.Response:
            return await client.get(
                f"{self._base_url}/v2/osm_elements/cost",
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
        return CostEstimate.from_dict(resp.json())

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "AsyncOSMGeoJSONClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
