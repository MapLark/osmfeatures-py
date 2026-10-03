"""Tests for AsyncOSMFeaturesClient — happy path, pagination, and retry behaviour."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from osmfeatures import (
    BinaryQueryResult,
    OSMFeaturesAuthError,
    OSMFeaturesAPIError,
    OSMFeaturesRateLimitError,
    OSMFeaturesTooDenseError,
    OSMFeaturesTooManyTilesError,
    OSMFeatureCollection,
    RetryConfig,
)
from osmfeatures.async_client import AsyncOSMFeaturesClient
from osmfeatures.chunking import split_bbox_tiles
from tests.conftest import (
    BASE_URL,
    FAKE_API_KEY,
    FREE_TIER_MAX_LIMIT,
    make_account_tier,
    make_test_feature,
    make_feature_collection,
    features_page,
)

# ---------------------------------------------------------------------------
# Mock transport helpers
# ---------------------------------------------------------------------------

_ResponseTuple = (
    tuple[int, dict[str, Any] | bytes]
    | tuple[int, dict[str, Any] | bytes, dict[str, str]]
)


class _MockTransport(httpx.AsyncBaseTransport):
    """Queue-based async mock transport for httpx.

    Each call to ``handle_async_request`` pops the next
    ``(status, body)`` or ``(status, body, headers)`` triple.
    ``body`` may be a JSON-serializable dict or raw ``bytes``.
    """

    def __init__(self, responses: list[_ResponseTuple]) -> None:
        self._queue = list(responses)
        self._idx = 0
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._idx >= len(self._queue):
            raise RuntimeError("_MockTransport ran out of queued responses")
        item = self._queue[self._idx]
        self._idx += 1
        if len(item) == 2:
            status, body = item
            extra_headers: dict[str, str] = {}
        else:
            status, body, extra_headers = item
        if isinstance(body, (bytes, bytearray)):
            content = bytes(body)
            default_ct = "application/octet-stream"
        else:
            content = json.dumps(body).encode()
            default_ct = "application/json"
        return httpx.Response(
            status,
            content=content,
            headers={"content-type": default_ct, **extra_headers},
            request=request,
        )

    @property
    def call_count(self) -> int:
        return self._idx


def _make_client(
    transport: _MockTransport,
    *,
    max_retries: int = 0,
) -> AsyncOSMFeaturesClient:
    """Return an AsyncOSMFeaturesClient wired to *transport*.

    By pre-populating ``_client`` we bypass the lazy ``_get_client()`` so the
    mock transport is used for every request.
    """
    client = AsyncOSMFeaturesClient(
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


async def test_async_query_binary_accept_returns_bytes():
    body = b"id,geometry\nway/1,POINT(18 59)\n"
    transport = _MockTransport([(200, body, {"content-type": "text/csv"})])
    client = _make_client(transport)

    async with client:
        result = await client.query_async(
            bbox="18.06,59.32,18.09,59.34",
            accept="text/csv",
        )

    assert isinstance(result, BinaryQueryResult)
    assert result.content == body
    assert transport.requests[0].headers["accept"] == "text/csv"


async def test_async_query_raises_auth_error_on_401():
    transport = _MockTransport([(401, {"error": "unauthorized"})])
    client = _make_client(transport)

    async with client:
        with pytest.raises(OSMFeaturesAuthError):
            await client.query_async(bbox="18.06,59.32,18.09,59.34")


async def test_async_query_raises_api_error_on_500():
    transport = _MockTransport([(500, {"error": "internal"})])
    client = _make_client(transport)

    async with client:
        with pytest.raises(OSMFeaturesAPIError) as exc_info:
            await client.query_async(bbox="18.06,59.32,18.09,59.34")

    assert exc_info.value.status_code == 500


async def test_async_query_meta_populated():
    transport = _MockTransport(
        [features_page([make_test_feature()], has_more=False)]
    )
    client = _make_client(transport)

    async with client:
        result = await client.query_async(bbox="18.06,59.32,18.09,59.34")

    assert result.meta.has_more is False
    assert result.meta.next_cursor is None


async def test_async_query_forwards_zoom_length_and_area_filters():
    transport = _MockTransport([(200, make_feature_collection([]))])
    client = _make_client(transport)

    async with client:
        await client.query_async(
            bbox="18.06,59.32,18.09,59.34",
            type="way",
            way_shape="line",
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


async def test_async_query_sends_location_and_radius_not_around():
    transport = _MockTransport([(200, make_feature_collection([]))])
    client = _make_client(transport)

    async with client:
        await client.query_async(
            location="59.334,18.063", radius=500, tags=["amenity=cafe"]
        )

    params = dict(transport.requests[0].url.params)
    assert params["location"] == "59.334,18.063"
    assert params["radius"] == "500"
    assert "around" not in params
    assert "bbox" not in params


# ---------------------------------------------------------------------------
# query_all()
# ---------------------------------------------------------------------------


def _too_large() -> tuple[int, dict[str, Any]]:
    return (
        400,
        {
            "error": "bad_request",
            "detail": "result exceeds the 100000 feature limit",
            "status_code": 400,
            "subtype": "result_too_large",
        },
    )


def _stats_total(total: int) -> tuple[int, dict[str, Any]]:
    return (
        200,
        {
            "groups": [{"value": "yes", "count": total}],
            "total": total,
            "truncated": False,
        },
    )


def _account_tier(max_limit: int = FREE_TIER_MAX_LIMIT, *, tier_id: str = "free") -> tuple[int, dict[str, Any]]:
    return (200, make_account_tier(max_limit=max_limit, tier_id=tier_id))


def _bboxes(transport: _MockTransport, path: str) -> list[str]:
    out: list[str] = []
    for req in transport.requests:
        if req.url.path != path:
            continue
        out.append(str(req.url.params["bbox"]))
    return out


async def test_async_query_rejects_cursor():
    transport = _MockTransport([(200, make_feature_collection([]))])
    client = _make_client(transport)
    async with client:
        with pytest.raises(ValueError, match="cursor"):
            await client.query_async(bbox="18.06,59.32,18.09,59.34", cursor="abc")
    assert transport.call_count == 0


async def test_async_query_does_not_send_disable_budget_warning():
    transport = _MockTransport([(200, make_feature_collection([]))])
    client = _make_client(transport)
    async with client:
        await client.query_async(
            bbox="18.06,59.32,18.09,59.34", disable_budget_warning=True
        )
    assert "disable_budget_warning" not in dict(transport.requests[0].url.params)


async def test_async_query_binary_rejects_auto_split():
    transport = _MockTransport([(200, b"id,geometry\n")])
    client = _make_client(transport)
    async with client:
        with pytest.raises(ValueError, match="GeoJSON"):
            await client.query_async(
                bbox="18.06,59.32,18.09,59.34",
                accept="application/flatgeobuf",
                auto_split=True,
            )
    assert transport.call_count == 0


async def test_async_query_bbox_tiles_requires_bbox():
    transport = _MockTransport([])
    client = _make_client(transport)
    async with client:
        with pytest.raises(ValueError, match="bbox_tiles requires bbox"):
            await client.query_async(
                within="relation/155790", bbox_tiles=2, tags=["amenity"]
            )


async def test_async_query_bbox_tiles_splits_upfront():
    bbox = "0,0,2,1"
    tiles = split_bbox_tiles(bbox, 2)
    transport = _MockTransport(
        [
            features_page([make_test_feature("way/1")]),
            features_page([make_test_feature("way/2")]),
        ]
    )
    client = _make_client(transport)
    async with client:
        result = await client.query_async(bbox=bbox, bbox_tiles=2, tags=["building"])
    assert [f.id for f in result.features] == ["way/1", "way/2"]
    assert _bboxes(transport, "/v3/osm_features") == tiles


async def test_async_query_counts_before_feature_then_jumps_density():
    bbox = "0,0,2,2"
    tiles = split_bbox_tiles(bbox, 4)
    overflow = tiles[2]
    leaves = split_bbox_tiles(overflow, 4)

    queued: list[_ResponseTuple] = [_account_tier()]

    def _count(total: int) -> None:
        queued.append(_stats_total(total))

    def _feat(fid: str) -> None:
        queued.append(features_page([make_test_feature(fid)]))

    _count(1)
    _feat("way/0")
    _count(1)
    _feat("way/1")
    _count(200_000)
    _count(1)
    _feat("way/3")
    for fid in ("way/20", "way/21", "way/22", "way/23"):
        _count(1)
        _feat(fid)
    transport = _MockTransport(queued)
    client = _make_client(transport)
    async with client:
        result = await client.query_async(
            bbox=bbox, bbox_tiles=4, tags=["building"], auto_split=True,
        )

    assert {f.id for f in result.features} == {
        "way/0", "way/1", "way/3", "way/20", "way/21", "way/22", "way/23",
    }
    feature_bboxes = _bboxes(transport, "/v3/osm_features")
    assert feature_bboxes == [tiles[0], tiles[1], tiles[3], *leaves]
    assert overflow not in feature_bboxes
    assert _bboxes(transport, "/v2/osm_features/count") == [*tiles, *leaves]


async def test_async_query_auto_split_aborts_when_match_count_exceeds_32_tiles():
    transport = _MockTransport([_account_tier(), _stats_total(3_000_000)])
    client = _make_client(transport)
    async with client:
        with pytest.raises(OSMFeaturesTooDenseError, match="too dense"):
            await client.query_async(
                bbox="0,0,20,20",
                tags=["building"],
                auto_split=True,
            )
    assert transport.call_count == 2
    assert transport.requests[0].url.path.endswith("/v1/account/tier")
    assert transport.requests[1].url.path.endswith("/v2/osm_features/count")


async def test_async_query_auto_split_uses_paid_tier_max_limit():
    bbox = "0,0,2,2"
    leaves = split_bbox_tiles(bbox, 4)
    queued: list[_ResponseTuple] = [_account_tier(1_000_000, tier_id="enterprise")]
    queued.append(_stats_total(3_000_000))
    for fid in ("way/0", "way/1", "way/2", "way/3"):
        queued.append(_stats_total(1))
        queued.append(features_page([make_test_feature(fid)]))
    transport = _MockTransport(queued)
    client = _make_client(transport)
    async with client:
        result = await client.query_async(
            bbox=bbox, tags=["building"], auto_split=True,
        )
    assert [f.id for f in result.features] == ["way/0", "way/1", "way/2", "way/3"]
    assert _bboxes(transport, "/v3/osm_features") == leaves
    assert transport.requests[0].url.path.endswith("/v1/account/tier")


async def test_async_query_auto_split_aborts_when_area_jump_exceeds_32_tiles():
    transport = _MockTransport(
        [
            _account_tier(),
            (
                400,
                {
                    "error": "bad_request",
                    "detail": (
                        "bbox area 400.000000 exceeds the tagged tier "
                        "limit 0.040000 for the 'free' tier."
                    ),
                    "status_code": 400,
                },
            )
        ]
    )
    client = _make_client(transport)
    async with client:
        with pytest.raises(OSMFeaturesTooManyTilesError, match="too large"):
            await client.query_async(
                bbox="0,0,20,20",
                tags=["building"],
                auto_split=True,
            )
    assert transport.call_count == 2
    assert transport.requests[0].url.path.endswith("/v1/account/tier")
    assert transport.requests[1].url.path.endswith("/v2/osm_features/count")


async def test_async_query_overflow_is_the_api_error():
    transport = _MockTransport([_too_large()])
    client = _make_client(transport)
    async with client:
        with pytest.raises(OSMFeaturesAPIError, match="result_too_large") as exc:
            await client.query_async(bbox="0,0,2,2", tags=["building"])
    assert "auto_split" not in str(exc.value)
    assert transport.call_count == 1


async def test_async_query_all_forwards_to_query():
    features = [make_test_feature(f"way/{i}") for i in range(3)]
    transport = _MockTransport([features_page(features, has_more=False)])
    client = _make_client(transport)

    async with client:
        result = await client.query_all_async(bbox="18.06,59.32,18.09,59.34")

    assert len(result.features) == 3
    assert transport.call_count == 1
    assert "/v3/osm_features" in str(transport.requests[0].url)


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
    """All attempts returning 429 (retryable) should raise OSMFeaturesRateLimitError."""
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
        with pytest.raises(OSMFeaturesRateLimitError) as exc_info:
            await client.query_async(bbox="18.06,59.32,18.09,59.34")

    assert exc_info.value.error_code == "too_many_requests"
    assert transport.call_count == 4


async def test_async_monthly_limit_not_retried():
    """rate_limit_monthly subtype (hard cap) must surface on the first attempt without retrying."""
    monthly = {
        "error": "too_many_requests",
        "subtype": "rate_limit_monthly",
        "detail": "Need 50 credits; 1 remaining this month.",
        "units": 50,
        "tier": "free",
    }
    transport = _MockTransport(
        [
            (429, monthly),
            # Extra entries that must never be reached:
            (429, monthly),
            (429, monthly),
            (429, monthly),
        ]
    )
    client = _make_client(transport, max_retries=3)

    async with client:
        with pytest.raises(OSMFeaturesRateLimitError) as exc_info:
            await client.query_async(bbox="18.06,59.32,18.09,59.34")

    assert exc_info.value.units == 50
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
        with pytest.raises(OSMFeaturesAuthError):
            await client.query_async(bbox="18.06,59.32,18.09,59.34")

    assert transport.call_count == 1


async def test_async_usage():
    transport = _MockTransport([(200, {"tier": "standard", "usage_this_month": 1})])
    client = _make_client(transport)
    async with client:
        out = await client.usage_async()
    assert out["tier"] == "standard"
    assert transport.requests[0].url.path.endswith("/v1/usage")


async def test_async_count_forwards_group_by():
    from urllib.parse import parse_qs, urlsplit

    transport = _MockTransport(
        [(200, {"groups": [{"value": "cafe", "count": 12}], "total": 12, "truncated": False})]
    )
    client = _make_client(transport)
    async with client:
        out = await client.count_async(
            group_by="amenity", bbox="18.06,59.32,18.09,59.34", tags=["amenity"]
        )
    assert out["total"] == 12
    parsed = parse_qs(urlsplit(str(transport.requests[0].url)).query)
    assert parsed["group_by"] == ["amenity"]
    assert transport.requests[0].url.path.endswith("/v2/osm_features/count")


# ---------------------------------------------------------------------------
# places_search tiling
# ---------------------------------------------------------------------------


def _places_post_bboxes(transport: _MockTransport) -> list[str]:
    out: list[str] = []
    for req in transport.requests:
        if req.url.path.endswith("/v1/places/search"):
            out.append(json.loads(req.content)["bbox"])
    return out


def _place_fc(fid: str) -> dict[str, Any]:
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "id": fid, "geometry": None, "properties": {}}
        ],
    }


def _places_bbox_too_large() -> dict[str, Any]:
    return {
        "error": "bad_request",
        "detail": (
            "bbox area 4.000000 exceeds the tagged tier limit 1.000000 "
            "for the 'free' tier."
        ),
        "status_code": 400,
    }


async def test_async_places_search_bbox_tiles_splits_upfront():
    bbox = "0,0,2,1"
    tiles = split_bbox_tiles(bbox, 2)
    transport = _MockTransport(
        [(200, _place_fc("node/1")), (200, _place_fc("node/2"))]
    )
    client = _make_client(transport)
    async with client:
        result = await client.places_search_async(
            bbox=bbox, or_tags=["amenity=cafe"], bbox_tiles=2
        )
    assert [f["id"] for f in result["features"]] == ["node/1", "node/2"]
    assert _places_post_bboxes(transport) == tiles


async def test_async_places_search_bbox_tiles_requires_bbox():
    transport = _MockTransport([])
    client = _make_client(transport)
    async with client:
        with pytest.raises(ValueError, match="bbox_tiles only applies to bbox search"):
            await client.places_search_async(
                location={"lat": 59.3, "lng": 18.0},
                radius=500,
                or_tags=["amenity=cafe"],
                bbox_tiles=2,
            )


async def test_async_places_search_auto_split_counts_then_quarters_area_overflow():
    bbox = "0,0,2,2"
    quarters = split_bbox_tiles(bbox, 4)
    queued: list[_ResponseTuple] = [
        _account_tier(),
        _stats_total(1),
        (400, _places_bbox_too_large()),
    ]
    for i in range(4):
        queued.append(_stats_total(1))
        queued.append((200, _place_fc(f"node/{i}")))
    transport = _MockTransport(queued)
    client = _make_client(transport)
    async with client:
        result = await client.places_search_async(
            bbox=bbox, or_tags=["amenity=cafe"], auto_split=True
        )
    assert {f["id"] for f in result["features"]} == {
        "node/0", "node/1", "node/2", "node/3",
    }
    assert _places_post_bboxes(transport) == [bbox, *quarters]
    assert transport.requests[0].url.path.endswith("/v1/account/tier")
    assert transport.requests[1].url.path.endswith("/v2/osm_features/count")
    assert all(
        not req.url.path.endswith("/v1/places/count")
        for req in transport.requests
    )


async def test_async_count_scalar_places_set():
    from urllib.parse import parse_qs, urlsplit

    transport = _MockTransport(
        [(200, {"groups": [], "total": 12, "truncated": False})]
    )
    client = _make_client(transport)
    async with client:
        out = await client.count_async(
            bbox="18.05,59.31,18.10,59.33",
            or_tags=["amenity=cafe"],
            way_shape="polygon",
        )
    assert out["total"] == 12
    assert out["groups"] == []
    assert transport.requests[0].url.path.endswith("/v2/osm_features/count")
    parsed = parse_qs(urlsplit(str(transport.requests[0].url)).query)
    assert parsed["or_tags"] == ["amenity=cafe"]
    assert parsed["way_shape"] == ["polygon"]
    assert "group_by" not in parsed


async def test_async_places_search_auto_split_hours_skips_account_tier():
    transport = _MockTransport(
        [_stats_total(100), (200, _place_fc("node/1"))]
    )
    client = _make_client(transport)
    async with client:
        result = await client.places_search_async(
            bbox="0,0,1,1",
            or_tags=["amenity=cafe"],
            as_of="2026-08-10T18:00:00+02:00",
            auto_split=True,
        )
    assert [f["id"] for f in result["features"]] == ["node/1"]
    assert transport.requests[0].url.path.endswith("/v2/osm_features/count")
    assert all(
        not req.url.path.endswith("/v1/account/tier") for req in transport.requests
    )


async def test_async_places_search_auto_split_open_now_splits_on_scan_cap():
    bbox = "0,0,1,1"
    tiles = split_bbox_tiles(bbox, 2)
    transport = _MockTransport(
        [
            _stats_total(15_000),
            _stats_total(100),
            (200, _place_fc("node/0")),
            _stats_total(100),
            (200, _place_fc("node/1")),
        ]
    )
    client = _make_client(transport)
    async with client:
        result = await client.places_search_async(
            bbox=bbox,
            or_tags=["amenity=cafe"],
            open_now=True,
            auto_split=True,
        )
    assert {f["id"] for f in result["features"]} == {"node/0", "node/1"}
    assert _places_post_bboxes(transport) == tiles
    assert all(
        not req.url.path.endswith("/v1/account/tier") for req in transport.requests
    )
