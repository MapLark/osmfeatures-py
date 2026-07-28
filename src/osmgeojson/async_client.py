"""Asynchronous OSM GeoJSON API client (httpx-based)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from .chunking import merge_features, shapely_to_bbox, split_bbox
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
        if disable_budget_warning:
            params["disable_budget_warning"] = disable_budget_warning
        if centroid:
            params["centroid"] = True

        data = await self._raw_query(params)
        return OSMFeatureCollection.from_dict(data)

    async def query_all_async(self, *, page_size: int = 1000, **params: Any) -> OSMFeatureCollection:
        """Fetch *all* pages of OSM elements asynchronously, auto-paginating."""
        if "geometry" in params:
            geom = params.pop("geometry")
            params["bbox"] = shapely_to_bbox(geom)

        all_raw = await paginate_all_async(self._raw_query, params, page_size=page_size)

        seen: set[str] = set()
        deduped: list[dict[str, Any]] = []
        for feat in all_raw:
            fid = feat.get("id", "")
            if fid not in seen:
                seen.add(fid)
                deduped.append(feat)

        return OSMFeatureCollection(
            features=[OSMFeature.from_dict(f) for f in deduped],
            meta=ResponseMeta(returned=len(deduped), has_more=False),
        )

    async def query_large_area_async(
        self,
        bbox: str,
        *,
        max_chunk_area_deg2: float = 0.25,
        concurrency: int = 4,
        page_size: int = 1000,
        **params: Any,
    ) -> OSMFeatureCollection:
        """Query a large bounding box by splitting into chunks fetched concurrently."""
        chunks = split_bbox(bbox, max_chunk_area_deg2)

        semaphore = asyncio.Semaphore(concurrency)

        async def _fetch_chunk(chunk_bbox: str) -> list[dict[str, Any]]:
            async with semaphore:
                fc = await self.query_all_async(bbox=chunk_bbox, page_size=page_size, **params)
                return [dict(f) for f in fc.features]

        chunk_results = await asyncio.gather(*[_fetch_chunk(c) for c in chunks])
        merged = merge_features(list(chunk_results))
        return OSMFeatureCollection(
            features=[OSMFeature.from_dict(f) for f in merged],
            meta=ResponseMeta(returned=len(merged), has_more=False),
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
