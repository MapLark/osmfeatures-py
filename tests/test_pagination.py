"""Tests for GET /v3/osm_features via query() / query_all()."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
import responses as rsps

from osmfeatures import OSMFeaturesAPIError, split_bbox_tiles
from tests.conftest import FEATURES_V3_URL, STATS_URL, add_features_response, make_test_feature


def test_query_rejects_limit_above_cap(client):
    with pytest.raises(ValueError, match="1000000"):
        client.query(bbox="18.06,59.32,18.09,59.34", limit=1_000_001)


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
def test_query_v3_counts_before_feature_then_quarters(client):
    bbox = "0,0,2,2"
    tiles = split_bbox_tiles(bbox, 4)
    overflow = tiles[2]
    quarters = split_bbox_tiles(overflow, 4)
    grandchildren = split_bbox_tiles(quarters[0], 4)

    _add_stats_total(1)
    _add_stats_total(1)
    _add_stats_total(200_000)
    _add_stats_total(1)
    _add_stats_total(200_000)
    _add_stats_total(1)
    _add_stats_total(1)
    _add_stats_total(1)
    for _ in range(4):
        _add_stats_total(1)

    add_features_response([make_test_feature("way/0")], url=FEATURES_V3_URL)
    add_features_response([make_test_feature("way/1")], url=FEATURES_V3_URL)
    add_features_response([make_test_feature("way/3")], url=FEATURES_V3_URL)
    for fid in ("way/21", "way/22", "way/23", "way/40", "way/41", "way/42", "way/43"):
        add_features_response([make_test_feature(fid)], url=FEATURES_V3_URL)

    result = client.query(
        bbox=bbox, bbox_tiles=4, tags=["building"], split_until_fit=True,
    )

    assert {f.id for f in result.features} == {
        "way/0", "way/1", "way/3", "way/21", "way/22", "way/23",
        "way/40", "way/41", "way/42", "way/43",
    }
    feature_bboxes = _bboxes("/v3/osm_features")
    assert feature_bboxes == [
        tiles[0], tiles[1], tiles[3],
        quarters[1], quarters[2], quarters[3],
        *grandchildren,
    ]
    assert overflow not in feature_bboxes
    assert quarters[0] not in feature_bboxes
    assert _bboxes("/v2/osm_features/count") == [
        *tiles, quarters[0], quarters[1], quarters[2], quarters[3], *grandchildren,
    ]


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
    assert "split_until_fit" not in str(exc.value)
    assert len(rsps.calls) == 1


@rsps.activate
def test_query_v3_within_overflow_raises(client):
    _add_too_large()
    with pytest.raises(OSMFeaturesAPIError, match="result_too_large"):
        client.query(within="relation/155790", type="node", tags=["amenity"])
    assert len(rsps.calls) == 1
