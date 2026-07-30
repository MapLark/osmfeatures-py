"""Tests for AsyncOSMGeoJSONClient — happy path, pagination, and retry behaviour."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from osmgeojson import (
    OSMGeoJSONAuthError,
    OSMGeoJSONAPIError,
    OSMGeoJSONRateLimitError,
    CostEstimate,
    OSMFeatureCollection,
    RetryConfig,
)
from osmgeojson.async_client import AsyncOSMGeoJSONClient
from tests.conftest import (
    BASE_URL,
    FAKE_API_KEY,
    ELEMENTS_URL,
    COST_URL,
    make_test_feature,
    make_feature_collection,
)

# ---------------------------------------------------------------------------
# Mock transport helpers
# ---------------------------------------------------------------------------

_ResponseTuple = tuple[int, dict[str, Any]]


class _MockTransport(httpx.AsyncBaseTransport):
    """Queue-based async mock transport for httpx.

    Each call to ``handle_async_request`` pops the next (status, body) pair
    from the queue so tests can stage an exact sequence of responses.
    """

    def __init__(self, responses: list[_ResponseTuple]) -> None:
        self._queue = list(responses)
        self._idx = 0
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._idx >= len(self._queue):
            raise RuntimeError("_MockTransport ran out of queued responses")
        status, body = self._queue[self._idx]
        self._idx += 1
        content = json.dumps(body).encode()
        return httpx.Response(
            status,
            content=content,
            headers={"content-type": "application/json"},
            request=request,
        )

    @property
    def call_count(self) -> int:
        return self._idx


def _make_client(
    transport: _MockTransport,
    *,
    max_retries: int = 0,
) -> AsyncOSMGeoJSONClient:
    """Return an AsyncOSMGeoJSONClient wired to *transport*.

    By pre-populating ``_client`` we bypass the lazy ``_get_client()`` so the
    mock transport is used for every request.
    """
    client = AsyncOSMGeoJSONClient(
        api_key=FAKE_API_KEY,
        base_url=BASE_URL,
        retry_config=RetryConfig(max_retries=max_retries, backoff_base=0.0, jitter=False),
    )
    # Inject the mock directly — mirrors how _get_client() would build it.
    client._client = httpx.AsyncClient(
        headers=client._headers,
        transport=transport,
    )
    return client


# ---------------------------------------------------------------------------
# query()
# ---------------------------------------------------------------------------


async def test_async_query_returns_feature_collection():
    features = [make_test_feature("way/1"), make_test_feature("way/2")]
    transport = _MockTransport([(200, make_feature_collection(features))])
    client = _make_client(transport)

    async with client:
        result = await client.query_async(bbox="18.06,59.32,18.09,59.34")

    assert isinstance(result, OSMFeatureCollection)
    assert len(result.features) == 2
    assert result.features[0].id == "way/1"
    assert result.features[0].osm_type == "way"
    assert result.features[0].osm_id == 1


async def test_async_query_sends_auth_header():
    transport = _MockTransport([(200, make_feature_collection([]))])
    client = _make_client(transport)

    async with client:
        await client.query_async(bbox="18.06,59.32,18.09,59.34")

    assert transport.requests[0].headers["authorization"] == f"Bearer {FAKE_API_KEY}"


async def test_async_query_raises_auth_error_on_401():
    transport = _MockTransport([(401, {"error": "unauthorized"})])
    client = _make_client(transport)

    async with client:
        with pytest.raises(OSMGeoJSONAuthError):
            await client.query_async(bbox="18.06,59.32,18.09,59.34")


async def test_async_query_raises_api_error_on_500():
    transport = _MockTransport([(500, {"error": "internal"})])
    client = _make_client(transport)

    async with client:
        with pytest.raises(OSMGeoJSONAPIError) as exc_info:
            await client.query_async(bbox="18.06,59.32,18.09,59.34")

    assert exc_info.value.status_code == 500


async def test_async_query_meta_populated():
    transport = _MockTransport(
        [(200, make_feature_collection([make_test_feature()], has_more=True, next_cursor="cursor-1"))]
    )
    client = _make_client(transport)

    async with client:
        result = await client.query_async(bbox="18.06,59.32,18.09,59.34")

    assert result.meta.has_more is True
    assert result.meta.next_cursor == "cursor-1"


async def test_async_query_forwards_zoom_length_and_area_filters():
    transport = _MockTransport([(200, make_feature_collection([]))])
    client = _make_client(transport)

    async with client:
        await client.query_async(
            bbox="18.06,59.32,18.09,59.34",
            type="way",
            shape="line",
            zoom=11,
            min_length_m=150,
            max_length_m=1500,
            min_area_m2=400,
            max_area_m2=4000,
        )

    params = dict(transport.requests[0].url.params)
    assert params["zoom"] == "11"
    assert params["min_length_m"] == "150"
    assert params["max_length_m"] == "1500"
    assert params["min_area_m2"] == "400"
    assert params["max_area_m2"] == "4000"


# ---------------------------------------------------------------------------
# query_all() — pagination
# ---------------------------------------------------------------------------


async def test_async_query_all_single_page():
    features = [make_test_feature(f"way/{i}") for i in range(3)]
    transport = _MockTransport([(200, make_feature_collection(features, has_more=False))])
    client = _make_client(transport)

    async with client:
        result = await client.query_all_async(bbox="18.06,59.32,18.09,59.34", bbox_tiles=1)

    assert len(result.features) == 3
    assert transport.call_count == 1


async def test_async_query_all_two_pages():
    page1 = [make_test_feature(f"way/{i}") for i in range(3)]
    page2 = [make_test_feature(f"way/{i}") for i in range(3, 6)]
    transport = _MockTransport(
        [
            (200, make_feature_collection(page1, has_more=True, next_cursor="cursor-1")),
            (200, make_feature_collection(page2, has_more=False)),
        ]
    )
    client = _make_client(transport)

    async with client:
        result = await client.query_all_async(bbox="18.06,59.32,18.09,59.34", bbox_tiles=1)

    assert len(result.features) == 6
    assert {f.id for f in result.features} == {f"way/{i}" for i in range(6)}
    assert transport.call_count == 2


async def test_async_query_all_deduplicates_across_pages():
    f_shared = make_test_feature("way/99")
    page1 = [make_test_feature("way/1"), f_shared]
    page2 = [f_shared, make_test_feature("way/2")]
    transport = _MockTransport(
        [
            (200, make_feature_collection(page1, has_more=True, next_cursor="cursor-1")),
            (200, make_feature_collection(page2, has_more=False)),
        ]
    )
    client = _make_client(transport)

    async with client:
        result = await client.query_all_async(bbox="18.06,59.32,18.09,59.34", bbox_tiles=1)

    ids = [f.id for f in result.features]
    assert len(ids) == len(set(ids))


async def test_async_query_all_raises_on_empty_page_with_has_more_true():
    transport = _MockTransport(
        [
            (200, make_feature_collection([], has_more=True, next_cursor="cursor-1")),
            # Must never be reached if paginator fails fast.
            (200, make_feature_collection([], has_more=False)),
        ]
    )
    client = _make_client(transport)

    async with client:
        with pytest.raises(RuntimeError, match="empty features page"):
            await client.query_all_async(bbox="18.06,59.32,18.09,59.34", bbox_tiles=1)

    assert transport.call_count == 1


async def test_async_query_all_default_tiles_two_requests():
    transport = _MockTransport(
        [
            (200, make_feature_collection([make_test_feature("way/1")], has_more=False)),
            (200, make_feature_collection([make_test_feature("way/2")], has_more=False)),
        ]
    )
    client = _make_client(transport)

    async with client:
        result = await client.query_all_async(bbox="18.06,59.32,18.09,59.34")

    assert transport.call_count == 2
    assert {f.id for f in result.features} == {"way/1", "way/2"}


# ---------------------------------------------------------------------------
# estimate_cost()
# ---------------------------------------------------------------------------


async def test_async_estimate_cost_returns_cost_estimate():
    cost_resp = {
        "estimated_credits": 42,
        "tier_limits": {"max_bbox_area_tagged": 1.0},
        "hints": ["Consider narrowing your bbox."],
    }
    transport = _MockTransport([(200, cost_resp)])
    client = _make_client(transport)

    async with client:
        result = await client.estimate_cost_async(bbox="18.06,59.32,18.09,59.34", tags=["building"])

    assert isinstance(result, CostEstimate)
    assert result.estimated_credits == 42
    assert result.hints == ["Consider narrowing your bbox."]


async def test_async_estimate_cost_forwards_zoom_length_and_area_filters():
    transport = _MockTransport([(200, {"estimated_credits": 1, "tier_limits": {}, "hints": []})])
    client = _make_client(transport)

    async with client:
        await client.estimate_cost_async(
            bbox="18.06,59.32,18.09,59.34",
            type="way",
            shape="polygon",
            zoom=8,
            min_length_m=250,
            max_length_m=2500,
            min_area_m2=500,
            max_area_m2=5000,
        )

    params = dict(transport.requests[0].url.params)
    assert params["zoom"] == "8"
    assert params["min_length_m"] == "250"
    assert params["max_length_m"] == "2500"
    assert params["min_area_m2"] == "500"
    assert params["max_area_m2"] == "5000"


async def test_async_estimate_cost_raises_auth_error_on_401():
    transport = _MockTransport([(401, {"error": "unauthorized"})])
    client = _make_client(transport)

    async with client:
        with pytest.raises(OSMGeoJSONAuthError):
            await client.estimate_cost_async(bbox="18.06,59.32,18.09,59.34")


# ---------------------------------------------------------------------------
# Retry behaviour
# ---------------------------------------------------------------------------


async def test_async_retries_on_429_then_succeeds():
    """Transient 429 (rate_limit_second) should be retried and succeed."""
    fc = make_feature_collection([make_test_feature()])
    transport = _MockTransport(
        [
            (429, {"error": "too_many_requests", "subtype": "rate_limit_second", "detail": "too fast"}),
            (200, fc),
        ]
    )
    client = _make_client(transport, max_retries=3)

    async with client:
        result = await client.query_async(bbox="18.06,59.32,18.09,59.34")

    assert len(result.features) == 1
    assert transport.call_count == 2


async def test_async_retries_exhausted_raises_rate_limit_error():
    """All attempts returning 429 (retryable) should raise OSMGeoJSONRateLimitError."""
    transport = _MockTransport(
        [
            (429, {"error": "too_many_requests", "subtype": "rate_limit_second", "detail": "too fast", "tier": "free"}),
            (429, {"error": "too_many_requests", "subtype": "rate_limit_second", "detail": "too fast", "tier": "free"}),
            (429, {"error": "too_many_requests", "subtype": "rate_limit_second", "detail": "too fast", "tier": "free"}),
            (429, {"error": "too_many_requests", "subtype": "rate_limit_second", "detail": "too fast", "tier": "free"}),
        ]
    )
    client = _make_client(transport, max_retries=3)

    async with client:
        with pytest.raises(OSMGeoJSONRateLimitError) as exc_info:
            await client.query_async(bbox="18.06,59.32,18.09,59.34")

    assert exc_info.value.error_code == "too_many_requests"
    assert transport.call_count == 4


async def test_async_monthly_limit_not_retried():
    """rate_limit_monthly subtype (hard cap) must surface on the first attempt without retrying."""
    transport = _MockTransport(
        [
            (429, {"error": "too_many_requests", "subtype": "rate_limit_monthly", "detail": "monthly budget exceeded", "tier": "free"}),
            # Extra entries that must never be reached:
            (429, {"error": "too_many_requests", "subtype": "rate_limit_monthly", "detail": "monthly budget exceeded", "tier": "free"}),
            (429, {"error": "too_many_requests", "subtype": "rate_limit_monthly", "detail": "monthly budget exceeded", "tier": "free"}),
            (429, {"error": "too_many_requests", "subtype": "rate_limit_monthly", "detail": "monthly budget exceeded", "tier": "free"}),
        ]
    )
    client = _make_client(transport, max_retries=3)

    async with client:
        with pytest.raises(OSMGeoJSONRateLimitError):
            await client.query_async(bbox="18.06,59.32,18.09,59.34")

    assert transport.call_count == 1


async def test_async_401_not_retried():
    """401 is not in retry_on_status — only one attempt should be made."""
    transport = _MockTransport(
        [
            (401, {"error": "unauthorized"}),
            (200, make_feature_collection([])),  # Must never be reached.
        ]
    )
    client = _make_client(transport, max_retries=3)

    async with client:
        with pytest.raises(OSMGeoJSONAuthError):
            await client.query_async(bbox="18.06,59.32,18.09,59.34")

    assert transport.call_count == 1
