"""Tests for OSMFeaturesClient - happy paths and error handling."""

from __future__ import annotations

import pytest
import responses as rsps
from urllib.parse import parse_qs, urlsplit

from osmfeatures import (
    BinaryQueryResult,
    OSMFeaturesAuthError,
    OSMFeaturesAPIError,
    OSMFeaturesRateLimitError,
    CostEstimate,
    OSMFeatureCollection,
)
from tests.conftest import (
    FEATURES_URL,
    COST_URL,
    make_test_feature,
    make_feature_collection,
    add_features_response,
    pagination_headers,
)


# ---------------------------------------------------------------------------
# query()
# ---------------------------------------------------------------------------


@rsps.activate
def test_query_returns_feature_collection(client):
    features = [make_test_feature("way/1"), make_test_feature("way/2")]
    rsps.add(rsps.GET, FEATURES_URL, json=make_feature_collection(features))

    result = client.query(bbox="18.06,59.32,18.09,59.34")

    assert isinstance(result, OSMFeatureCollection)
    assert len(result.features) == 2
    assert result.features[0].id == "way/1"
    assert result.features[0].osm_type == "way"
    assert result.features[0].osm_id == 1


@rsps.activate
def test_query_sends_auth_header(client):
    rsps.add(rsps.GET, FEATURES_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34")

    assert rsps.calls[0].request.headers["Authorization"] == "Bearer sk-test-1234"


@rsps.activate
def test_query_repeatable_tags(client):
    rsps.add(rsps.GET, FEATURES_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34", tags=["building", "name=City Hall"])

    qs = rsps.calls[0].request.url
    assert "tags=building" in qs
    assert "tags=name" in qs


@rsps.activate
def test_query_single_type_param(client):
    rsps.add(rsps.GET, FEATURES_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34", type="way")

    qs = rsps.calls[0].request.url
    assert "type=way" in qs


@rsps.activate
def test_query_comma_separated_type_param(client):
    rsps.add(rsps.GET, FEATURES_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34", type="way,relation")

    qs = rsps.calls[0].request.url
    assert "type=way%2Crelation" in qs


@rsps.activate
def test_query_type_list_uses_single_comma_separated_query_value(client):
    """Regression: list-valued type must not emit repeated type keys."""
    rsps.add(rsps.GET, FEATURES_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34", type=["node", "way"])

    qs = rsps.calls[0].request.url
    assert "type=node%2Cway" in qs
    assert qs.count("type=") == 1

    parsed = parse_qs(urlsplit(qs).query)
    assert parsed["type"] == ["node,way"]


@rsps.activate
def test_query_forwards_shape_all(client):
    rsps.add(rsps.GET, FEATURES_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34", type="way", way_shape="all")

    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert parsed["way_shape"] == ["all"]


@rsps.activate
def test_query_forwards_deprecated_shape_as_way_shape(client):
    rsps.add(rsps.GET, FEATURES_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34", type="way", shape="all")

    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert parsed["way_shape"] == ["all"]
    assert "shape" not in parsed


@rsps.activate
def test_query_forwards_zoom_length_and_area_filters(client):
    rsps.add(rsps.GET, FEATURES_URL, json=make_feature_collection([]))

    client.query(
        bbox="18.06,59.32,18.09,59.34",
        type="way",
        way_shape="polygon",
        zoom=10,
        min_length_m=100,
        max_length_m=500,
        min_area_m2=250,
        max_area_m2=5000,
    )

    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert parsed["zoom"] == ["10"]
    assert parsed["min_length_m"] == ["100"]
    assert parsed["max_length_m"] == ["500"]
    assert parsed["min_area_m2"] == ["250"]
    assert parsed["max_area_m2"] == ["5000"]


@rsps.activate
def test_query_sends_location_and_radius_not_around(client):
    rsps.add(rsps.GET, FEATURES_URL, json=make_feature_collection([]))

    client.query(location="59.334,18.063", radius=500, tags=["amenity=cafe"])

    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert parsed["location"] == ["59.334,18.063"]
    assert parsed["radius"] == ["500"]
    assert "around" not in parsed
    assert "bbox" not in parsed


@rsps.activate
def test_query_raises_auth_error_on_401(client):
    rsps.add(rsps.GET, FEATURES_URL, status=401, body="Unauthorized")

    with pytest.raises(OSMFeaturesAuthError):
        client.query(bbox="18.06,59.32,18.09,59.34")


@rsps.activate
def test_query_raises_api_error_on_500(client):
    rsps.add(rsps.GET, FEATURES_URL, status=500, body="Internal Server Error")

    with pytest.raises(OSMFeaturesAPIError) as exc_info:
        client.query(bbox="18.06,59.32,18.09,59.34")

    assert exc_info.value.status_code == 500


@rsps.activate
def test_query_meta_populated(client):
    add_features_response([make_test_feature()], has_more=True, next_cursor="cursor-1")

    result = client.query(bbox="18.06,59.32,18.09,59.34")

    assert result.meta.has_more is True
    assert result.meta.next_cursor == "cursor-1"


@rsps.activate
def test_query_binary_format_keeps_pagination_meta(client):
    body = b"id,name\nway/1,Cafe\n"
    rsps.add(
        rsps.GET,
        FEATURES_URL,
        body=body,
        headers={
            "X-Returned": "1",
            "X-Has-More": "true",
            "X-Next-Cursor": "cursor-csv-1",
            "Content-Type": "text/csv",
        },
        status=200,
    )

    result = client.query(bbox="18.06,59.32,18.09,59.34", accept="text/csv", limit=1)

    assert isinstance(result, BinaryQueryResult)
    assert result.content == body
    assert result.meta.returned == 1
    assert result.meta.has_more is True
    assert result.meta.next_cursor == "cursor-csv-1"
    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert "format" not in parsed
    assert rsps.calls[0].request.headers["Accept"] == "text/csv"


@rsps.activate
def test_query_binary_format_manual_pagination_uses_cursor(client):
    page1 = b"id\nway/1\n"
    page2 = b"id\nway/2\n"
    rsps.add(
        rsps.GET,
        FEATURES_URL,
        body=page1,
        headers=pagination_headers([{"id": "1"}], has_more=True, next_cursor="c2"),
        status=200,
    )
    rsps.add(
        rsps.GET,
        FEATURES_URL,
        body=page2,
        headers=pagination_headers([{"id": "2"}], has_more=False),
        status=200,
    )

    first = client.query(bbox="18.06,59.32,18.09,59.34", accept="text/tab-separated-values", limit=1)
    assert isinstance(first, BinaryQueryResult)
    assert first.meta.has_more is True
    second = client.query(
        bbox="18.06,59.32,18.09,59.34",
        accept="text/tab-separated-values",
        limit=1,
        cursor=first.meta.next_cursor,
    )
    assert isinstance(second, BinaryQueryResult)
    assert second.content == page2
    assert second.meta.has_more is False
    assert parse_qs(urlsplit(rsps.calls[1].request.url).query)["cursor"] == ["c2"]


# ---------------------------------------------------------------------------
# estimate_cost()
# ---------------------------------------------------------------------------


@rsps.activate
def test_estimate_cost_returns_cost_estimate(client):
    cost_resp = {
        "estimated_credits": 42,
        "tier_limits": {"max_bbox_area_tagged": 1.0},
        "hints": ["Consider narrowing your bbox."],
    }
    rsps.add(rsps.GET, COST_URL, json=cost_resp)

    result = client.estimate_cost(bbox="18.06,59.32,18.09,59.34", tags=["building"])

    assert isinstance(result, CostEstimate)
    assert result.estimated_credits == 42
    assert result.hints == ["Consider narrowing your bbox."]


@rsps.activate
def test_estimate_cost_forwards_zoom_length_and_area_filters(client):
    rsps.add(rsps.GET, COST_URL, json={"estimated_credits": 1, "tier_limits": {}, "hints": []})

    client.estimate_cost(
        bbox="18.06,59.32,18.09,59.34",
        type="way",
        way_shape="line",
        zoom=9,
        min_length_m=200,
        max_length_m=2000,
        min_area_m2=300,
        max_area_m2=3000,
    )

    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert parsed["zoom"] == ["9"]
    assert parsed["min_length_m"] == ["200"]
    assert parsed["max_length_m"] == ["2000"]
    assert parsed["min_area_m2"] == ["300"]
    assert parsed["max_area_m2"] == ["3000"]


@rsps.activate
def test_estimate_cost_sends_location_and_radius_not_around(client):
    rsps.add(rsps.GET, COST_URL, json={"estimated_credits": 1, "tier_limits": {}, "hints": []})

    client.estimate_cost(location="59.334,18.063", radius=500, tags=["amenity=cafe"])

    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert parsed["location"] == ["59.334,18.063"]
    assert parsed["radius"] == ["500"]
    assert "around" not in parsed


@rsps.activate
def test_estimate_cost_raises_auth_error_on_401(client):
    rsps.add(rsps.GET, COST_URL, status=401, body="Unauthorized")

    with pytest.raises(OSMFeaturesAuthError):
        client.estimate_cost(bbox="18.06,59.32,18.09,59.34")


# ---------------------------------------------------------------------------
# Feature model helpers
# ---------------------------------------------------------------------------


def test_feature_properties():
    from osmfeatures import OSMFeature
    f = OSMFeature(
        id="relation/999",
        geometry={"type": "MultiPolygon", "coordinates": []},
        properties={"tags": {"name": "Stockholm"}, "centroid": {"type": "Point", "coordinates": [18.07, 59.33]}},
    )
    assert f.osm_type == "relation"
    assert f.osm_id == 999
    assert f.tags == {"name": "Stockholm"}
    assert f.centroid is not None


# ---------------------------------------------------------------------------
# Context manager
# ---------------------------------------------------------------------------


@rsps.activate
def test_client_context_manager(client):
    rsps.add(rsps.GET, FEATURES_URL, json=make_feature_collection([]))

    with client as c:
        result = c.query(bbox="18.06,59.32,18.09,59.34")

    assert isinstance(result, OSMFeatureCollection)
