"""Tests for GET /v3/osm_features via query() / query_all()."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
import responses as rsps

from osmfeatures import (
    OSMFeaturesAPIError,
    OSMFeaturesTooDenseError,
    OSMFeaturesTooManyTilesError,
    split_bbox_tiles,
)
from tests.conftest import (
    FEATURES_V3_URL,
    STATS_URL,
    add_account_tier_response,
    add_features_response,
    make_test_feature,
)


def test_query_rejects_non_positive_limit(client):
    with pytest.raises(ValueError, match="positive"):
        client.query(bbox="18.06,59.32,18.09,59.34", limit=0)


@rsps.activate
def test_query_forwards_limit_above_default(client):
    add_features_response([make_test_feature("way/1")], url=FEATURES_V3_URL)
    client.query(bbox="0,0,1,1", tags=["building"], limit=200_000)
    assert parse_qs(urlsplit(rsps.calls[0].request.url).query)["limit"] == ["200000"]


@rsps.activate
def test_query_forwards_limit_and_keeps_truncation(client):
    add_features_response([make_test_feature("way/1")], has_more=True, url=FEATURES_V3_URL)
    result = client.query(bbox="0,0,1,1", tags=["building"], limit=10)
    assert [f.id for f in result.features] == ["way/1"]
    assert result.meta.has_more is True
    assert parse_qs(urlsplit(rsps.calls[0].request.url).query)["limit"] == ["10"]


@rsps.activate
def test_query_all_forwards_to_query(client):
    add_features_response([make_test_feature("way/1")], url=FEATURES_V3_URL)
    result = client.query_all(bbox="0,0,1,1", tags=["building"])
    assert [f.id for f in result.features] == ["way/1"]
    assert urlsplit(rsps.calls[0].request.url).path == "/v3/osm_features"


@rsps.activate
def test_query_v3_fit_does_not_count(client):
    add_features_response([make_test_feature("way/1")], url=FEATURES_V3_URL)

    result = client.query(bbox="0,0,1,1", tags=["building"])

    assert [f.id for f in result.features] == ["way/1"]
    assert all(urlsplit(c.request.url).path != "/v2/osm_features/count" for c in rsps.calls)


@rsps.activate
def test_query_bbox_tiles_splits_upfront(client):
    bbox = "0,0,2,1"
    tiles = split_bbox_tiles(bbox, 2)
    add_features_response([make_test_feature("way/1")], url=FEATURES_V3_URL)
    add_features_response([make_test_feature("way/2")], url=FEATURES_V3_URL)

    result = client.query(bbox=bbox, bbox_tiles=2, tags=["building"])

    assert [f.id for f in result.features] == ["way/1", "way/2"]
    assert _bboxes("/v3/osm_features") == tiles


def test_query_bbox_tiles_requires_bbox(client):
    with pytest.raises(ValueError, match="bbox_tiles requires bbox"):
        client.query(within="relation/155790", bbox_tiles=2, tags=["amenity"])


def _add_too_large() -> None:
    rsps.add(
        rsps.GET,
        FEATURES_V3_URL,
        json={
            "error": "bad_request",
            "detail": "result exceeds the 100000 feature limit",
            "status_code": 400,
            "subtype": "result_too_large",
        },
        status=400,
    )


def _add_stats_total(total: int) -> None:
    rsps.add(
        rsps.GET,
        STATS_URL,
        json={
            "groups": [{"value": "yes", "count": total}],
            "total": total,
            "truncated": False,
        },
    )


def _bboxes(path: str) -> list[str]:
    out: list[str] = []
    for call in rsps.calls:
        parts = urlsplit(call.request.url)
        if parts.path != path:
            continue
        out.append(parse_qs(parts.query)["bbox"][0])
    return out


@rsps.activate
def test_query_v3_counts_before_feature_then_jumps_density(client):
    bbox = "0,0,2,2"
    tiles = split_bbox_tiles(bbox, 4)
    overflow = tiles[2]
    leaves = split_bbox_tiles(overflow, 4)

    add_account_tier_response()
    _add_stats_total(1)
    _add_stats_total(1)
    _add_stats_total(200_000)
    _add_stats_total(1)
    for _ in range(4):
        _add_stats_total(1)

    add_features_response([make_test_feature("way/0")], url=FEATURES_V3_URL)
    add_features_response([make_test_feature("way/1")], url=FEATURES_V3_URL)
    add_features_response([make_test_feature("way/3")], url=FEATURES_V3_URL)
    for fid in ("way/20", "way/21", "way/22", "way/23"):
        add_features_response([make_test_feature(fid)], url=FEATURES_V3_URL)

    result = client.query(
        bbox=bbox, bbox_tiles=4, tags=["building"], auto_split=True,
    )

    assert {f.id for f in result.features} == {
        "way/0", "way/1", "way/3", "way/20", "way/21", "way/22", "way/23",
    }
    feature_bboxes = _bboxes("/v3/osm_features")
    assert feature_bboxes == [tiles[0], tiles[1], tiles[3], *leaves]
    assert overflow not in feature_bboxes
    assert _bboxes("/v2/osm_features/count") == [*tiles, *leaves]


@rsps.activate
def test_query_auto_split_density_jumps_to_two_tiles(client):
    bbox = "0,0,2,1"
    leaves = split_bbox_tiles(bbox, 2)
    add_account_tier_response()
    _add_stats_total(80_001)
    _add_stats_total(1)
    _add_stats_total(1)
    add_features_response([make_test_feature("way/0")], url=FEATURES_V3_URL)
    add_features_response([make_test_feature("way/1")], url=FEATURES_V3_URL)
    result = client.query(bbox=bbox, tags=["building"], auto_split=True)
    assert [f.id for f in result.features] == ["way/0", "way/1"]
    assert _bboxes("/v3/osm_features") == leaves
    assert _bboxes("/v2/osm_features/count") == [bbox, *leaves]


@rsps.activate
def test_query_auto_split_one_extra_jump_on_dense_leaf(client):
    bbox = "0,0,2,1"
    leaves = split_bbox_tiles(bbox, 2)
    grand = split_bbox_tiles(leaves[0], 2)
    add_account_tier_response()
    _add_stats_total(80_001)
    _add_stats_total(80_001)
    _add_stats_total(1)
    _add_stats_total(1)
    _add_stats_total(1)
    add_features_response([make_test_feature("way/sparse")], url=FEATURES_V3_URL)
    add_features_response([make_test_feature("way/g0")], url=FEATURES_V3_URL)
    add_features_response([make_test_feature("way/g1")], url=FEATURES_V3_URL)
    result = client.query(bbox=bbox, tags=["building"], auto_split=True)
    assert [f.id for f in result.features] == ["way/sparse", "way/g0", "way/g1"]
    assert _bboxes("/v3/osm_features") == [leaves[1], *grand]
    assert _bboxes("/v2/osm_features/count") == [bbox, leaves[0], leaves[1], *grand]


@rsps.activate
def test_query_auto_split_third_density_jump_is_too_dense(client):
    bbox = "0,0,2,1"
    leaves = split_bbox_tiles(bbox, 2)
    grand = split_bbox_tiles(leaves[0], 2)
    add_account_tier_response()
    _add_stats_total(80_001)
    _add_stats_total(80_001)
    _add_stats_total(0)
    _add_stats_total(80_001)
    with pytest.raises(OSMFeaturesTooDenseError, match="too dense") as exc:
        client.query(bbox=bbox, tags=["building"], auto_split=True)
    assert exc.value.total == 80_001
    assert _bboxes("/v2/osm_features/count") == [
        bbox, leaves[0], leaves[1], grand[0],
    ]


@rsps.activate
def test_query_auto_split_result_too_large_after_fit_count_is_too_dense(client):
    add_account_tier_response()
    _add_stats_total(1)
    _add_too_large()
    with pytest.raises(OSMFeaturesTooDenseError, match="too dense"):
        client.query(bbox="0,0,1,1", tags=["building"], auto_split=True)
    assert [urlsplit(c.request.url).path for c in rsps.calls] == [
        "/v1/account/tier",
        "/v2/osm_features/count",
        "/v3/osm_features",
    ]


@rsps.activate
def test_query_auto_split_does_not_split_for_page_limit(client):
    """limit=10 truncates; 20 matches is not result_too_large."""
    add_account_tier_response()
    _add_stats_total(20)
    add_features_response([make_test_feature("way/1")], has_more=True, url=FEATURES_V3_URL)
    result = client.query(
        bbox="0,0,1,1",
        tags=["building"],
        limit=10,
        auto_split=True,
    )
    assert [f.id for f in result.features] == ["way/1"]
    assert result.meta.has_more is True
    assert _bboxes("/v3/osm_features") == ["0,0,1,1"]
    assert _bboxes("/v2/osm_features/count") == ["0,0,1,1"]


@rsps.activate
def test_query_auto_split_counts_or_tags(client):
    add_account_tier_response()
    _add_stats_total(1)
    add_features_response([make_test_feature("way/1")], url=FEATURES_V3_URL)
    client.query(bbox="0,0,1,1", or_tags=["amenity=cafe"], auto_split=True)
    count_call = next(
        c for c in rsps.calls
        if urlsplit(c.request.url).path == "/v2/osm_features/count"
    )
    assert urlsplit(rsps.calls[0].request.url).path == "/v1/account/tier"
    assert parse_qs(urlsplit(count_call.request.url).query)["group_by"] == ["amenity"]


@rsps.activate
def test_query_auto_split_aborts_when_match_count_exceeds_32_tiles(client):
    add_account_tier_response()
    _add_stats_total(3_000_000)
    with pytest.raises(OSMFeaturesTooDenseError, match="too dense") as exc:
        client.query(
            bbox="0,0,20,20",
            tags=["building"],
            auto_split=True,
        )
    assert exc.value.total == 3_000_000
    assert exc.value.tiles == 64
    assert [urlsplit(c.request.url).path for c in rsps.calls] == [
        "/v1/account/tier",
        "/v2/osm_features/count",
    ]


@rsps.activate
def test_query_auto_split_aborts_when_area_jump_exceeds_32_tiles(client):
    add_account_tier_response()
    rsps.add(
        rsps.GET,
        STATS_URL,
        json={
            "error": "bad_request",
            "detail": (
                "bbox area 400.000000 exceeds the tagged tier limit 0.040000 "
                "for the 'free' tier."
            ),
            "status_code": 400,
        },
        status=400,
    )
    with pytest.raises(OSMFeaturesTooManyTilesError, match="too large") as exc:
        client.query(
            bbox="0,0,20,20",
            tags=["building"],
            auto_split=True,
        )
    assert exc.value.tiles > 32
    assert exc.value.total is None
    assert [urlsplit(c.request.url).path for c in rsps.calls] == [
        "/v1/account/tier",
        "/v2/osm_features/count",
    ]


@rsps.activate
def test_query_auto_split_area_jump_includes_parent_match_total(client):
    add_account_tier_response()
    _add_stats_total(59)
    rsps.add(
        rsps.GET,
        FEATURES_V3_URL,
        json={
            "error": "bad_request",
            "detail": (
                "bbox area 5.009167 exceeds the tagged tier limit 0.040000 "
                "for the 'free' tier."
            ),
            "status_code": 400,
        },
        status=400,
    )
    with pytest.raises(OSMFeaturesTooManyTilesError, match="59 matches") as exc:
        client.query(
            bbox="17.24,58.49,20.01,60.30",
            tags=["amenity=cafe"],
            auto_split=True,
        )
    assert exc.value.total == 59
    assert exc.value.tiles == 128


@rsps.activate
def test_query_auto_split_rejects_bbox_tiles_over_32(client):
    add_account_tier_response()
    with pytest.raises(OSMFeaturesTooManyTilesError, match="too large") as exc:
        client.query(
            bbox="0,0,1,1",
            tags=["building"],
            bbox_tiles=64,
            auto_split=True,
        )
    assert exc.value.tiles == 64


@rsps.activate
def test_query_auto_split_uses_paid_tier_max_limit(client):
    """3M matches at enterprise max_limit=1M is 4 tiles, not too dense."""
    bbox = "0,0,2,2"
    leaves = split_bbox_tiles(bbox, 4)
    add_account_tier_response(max_limit=1_000_000, tier_id="enterprise")
    _add_stats_total(3_000_000)
    for fid in ("way/0", "way/1", "way/2", "way/3"):
        _add_stats_total(1)
        add_features_response([make_test_feature(fid)], url=FEATURES_V3_URL)

    result = client.query(bbox=bbox, tags=["building"], auto_split=True)

    assert [f.id for f in result.features] == ["way/0", "way/1", "way/2", "way/3"]
    assert _bboxes("/v3/osm_features") == leaves
    assert urlsplit(rsps.calls[0].request.url).path == "/v1/account/tier"


@rsps.activate
def test_query_auto_split_caches_account_tier(client):
    add_account_tier_response()
    _add_stats_total(1)
    add_features_response([make_test_feature("way/1")], url=FEATURES_V3_URL)
    _add_stats_total(1)
    add_features_response([make_test_feature("way/2")], url=FEATURES_V3_URL)

    client.query(bbox="0,0,1,1", tags=["building"], auto_split=True)
    client.query(bbox="0,0,1,1", tags=["building"], auto_split=True)

    tier_calls = [
        c for c in rsps.calls if urlsplit(c.request.url).path == "/v1/account/tier"
    ]
    assert len(tier_calls) == 1


@rsps.activate
def test_account_tier_returns_max_limit(client):
    add_account_tier_response(max_limit=200_000, tier_id="pro")
    tier = client.account_tier()
    assert tier["id"] == "pro"
    assert tier["max_limit"] == 200_000
    assert client.account_tier()["max_limit"] == 200_000
    assert len(rsps.calls) == 1


@rsps.activate
def test_query_without_auto_split_does_not_fetch_account_tier(client):
    add_features_response([make_test_feature("way/1")], url=FEATURES_V3_URL)
    client.query(bbox="0,0,1,1", tags=["building"])
    assert all(
        urlsplit(c.request.url).path != "/v1/account/tier" for c in rsps.calls
    )


@rsps.activate
def test_query_v3_has_more_is_not_a_client_error(client):
    add_features_response([make_test_feature("way/1")], has_more=True, url=FEATURES_V3_URL)
    result = client.query(bbox="0,0,2,2", tags=["building"])
    assert [f.id for f in result.features] == ["way/1"]
    assert result.meta.has_more is True
    assert len(rsps.calls) == 1


@rsps.activate
def test_query_v3_overflow_is_the_api_error(client):
    _add_too_large()
    with pytest.raises(OSMFeaturesAPIError, match="result_too_large") as exc:
        client.query(bbox="0,0,2,2", tags=["building"])
    assert "auto_split" not in str(exc.value)
    assert len(rsps.calls) == 1


@rsps.activate
def test_query_split_until_fit_alias_warns(client):
    add_account_tier_response()
    _add_stats_total(1)
    add_features_response([make_test_feature("way/1")], url=FEATURES_V3_URL)
    with pytest.warns(DeprecationWarning, match="use auto_split"):
        result = client.query(
            bbox="0,0,1,1", tags=["building"], split_until_fit=True,
        )
    assert [f.id for f in result.features] == ["way/1"]


@rsps.activate
def test_query_v3_within_overflow_raises(client):
    _add_too_large()
    with pytest.raises(OSMFeaturesAPIError, match="result_too_large"):
        client.query(within="relation/155790", type="node", tags=["amenity"])
    assert len(rsps.calls) == 1
