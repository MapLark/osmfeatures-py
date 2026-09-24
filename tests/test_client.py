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
    OSMFeatureCollection,
)
from tests.conftest import (
    FEATURES_V3_URL,
    STATS_URL,
    make_test_feature,
    make_feature_collection,
    add_features_response,
)


# ---------------------------------------------------------------------------
# query()
# ---------------------------------------------------------------------------


@rsps.activate
def test_query_returns_feature_collection(client):
    features = [make_test_feature("way/1"), make_test_feature("way/2")]
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection(features))

    result = client.query(bbox="18.06,59.32,18.09,59.34")

    assert isinstance(result, OSMFeatureCollection)
    assert len(result.features) == 2
    assert result.features[0].id == "way/1"
    assert result.features[0].osm_type == "way"
    assert result.features[0].osm_id == 1


@rsps.activate
def test_query_sends_auth_header(client):
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34")

    assert rsps.calls[0].request.headers["Authorization"] == "Bearer sk-test-1234"


@rsps.activate
def test_query_repeatable_tags(client):
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34", tags=["building", "name=City Hall"])

    qs = rsps.calls[0].request.url
    assert "tags=building" in qs
    assert "tags=name" in qs


@rsps.activate
def test_query_single_type_param(client):
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34", type="way")

    qs = rsps.calls[0].request.url
    assert "type=way" in qs


@rsps.activate
def test_query_comma_separated_type_param(client):
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34", type="way,relation")

    qs = rsps.calls[0].request.url
    assert "type=way%2Crelation" in qs


@rsps.activate
def test_query_type_list_uses_single_comma_separated_query_value(client):
    """Regression: list-valued type must not emit repeated type keys."""
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34", type=["node", "way"])

    qs = rsps.calls[0].request.url
    assert "type=node%2Cway" in qs
    assert qs.count("type=") == 1

    parsed = parse_qs(urlsplit(qs).query)
    assert parsed["type"] == ["node,way"]


@rsps.activate
def test_query_forwards_shape_all(client):
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34", type="way", way_shape="all")

    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert parsed["way_shape"] == ["all"]


@rsps.activate
def test_query_forwards_deprecated_shape_as_way_shape(client):
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34", type="way", shape="all")

    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert parsed["way_shape"] == ["all"]
    assert "shape" not in parsed


@rsps.activate
def test_query_forwards_within_and_numeric_tag(client):
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection([]))

    client.query(within="relation/155790", type="node", tags=["ele>500"])

    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert parsed["within"] == ["relation/155790"]
    assert parsed["tags"] == ["ele>500"]


@rsps.activate
def test_query_does_not_send_disable_budget_warning(client):
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34", disable_budget_warning=True)

    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert "disable_budget_warning" not in parsed


@rsps.activate
def test_count_forwards_group_by(client):
    rsps.add(
        rsps.GET,
        STATS_URL,
        json={"groups": [{"value": "cafe", "count": 12}], "total": 12, "truncated": False},
    )
    out = client.count(group_by="amenity", bbox="18.06,59.32,18.09,59.34", tags=["amenity"])
    assert out["total"] == 12
    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert parsed["group_by"] == ["amenity"]
    assert parsed["tags"] == ["amenity"]


@rsps.activate
def test_query_all_within_does_not_tile(client):
    add_features_response([], has_more=False, url=FEATURES_V3_URL)

    client.query_all(within="relation/155790", type="node", tags=["amenity"])

    assert len(rsps.calls) == 1
    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert parsed["within"] == ["relation/155790"]
    assert "bbox" not in parsed


@rsps.activate
def test_query_forwards_zoom_length_and_area_filters(client):
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection([]))

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
def test_query_omits_api_defaults(client):
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection([]))

    client.query(bbox="18.06,59.32,18.09,59.34")

    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert "limit" not in parsed
    assert "centroid" not in parsed
    assert "clip_geometry" not in parsed
    assert "clipGeometry" not in parsed


@rsps.activate
def test_query_binary_accept_returns_bytes(client):
    body = b"id,geometry\nway/1,POINT(18 59)\n"
    rsps.add(
        rsps.GET,
        FEATURES_V3_URL,
        body=body,
        headers={"Content-Type": "text/csv", "X-Returned": "1", "X-Has-More": "false"},
        status=200,
    )

    result = client.query(bbox="18.06,59.32,18.09,59.34", accept="text/csv")

    assert isinstance(result, BinaryQueryResult)
    assert result.content == body
    assert rsps.calls[0].request.headers["Accept"] == "text/csv"


@rsps.activate
def test_query_binary_rejects_split_until_fit(client):
    with pytest.raises(ValueError, match="GeoJSON"):
        client.query(
            bbox="18.06,59.32,18.09,59.34",
            accept="application/flatgeobuf",
            split_until_fit=True,
        )
    assert not rsps.calls


@rsps.activate
def test_query_sends_location_and_radius_not_around(client):
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection([]))

    client.query(location="59.334,18.063", radius=500, tags=["amenity=cafe"])

    parsed = parse_qs(urlsplit(rsps.calls[0].request.url).query)
    assert parsed["location"] == ["59.334,18.063"]
    assert parsed["radius"] == ["500"]
    assert "around" not in parsed
    assert "bbox" not in parsed


@rsps.activate
def test_query_raises_auth_error_on_401(client):
    rsps.add(rsps.GET, FEATURES_V3_URL, status=401, body="Unauthorized")

    with pytest.raises(OSMFeaturesAuthError):
        client.query(bbox="18.06,59.32,18.09,59.34")


@rsps.activate
def test_query_raises_api_error_on_500(client):
    rsps.add(rsps.GET, FEATURES_V3_URL, status=500, body="Internal Server Error")

    with pytest.raises(OSMFeaturesAPIError) as exc_info:
        client.query(bbox="18.06,59.32,18.09,59.34")

    assert exc_info.value.status_code == 500


@rsps.activate
def test_query_meta_populated(client):
    add_features_response([make_test_feature()], has_more=False, url=FEATURES_V3_URL)

    result = client.query(bbox="18.06,59.32,18.09,59.34")

    assert result.meta.has_more is False
    assert result.meta.next_cursor is None


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
    rsps.add(rsps.GET, FEATURES_V3_URL, json=make_feature_collection([]))

    with client as c:
        result = c.query(bbox="18.06,59.32,18.09,59.34")

    assert isinstance(result, OSMFeatureCollection)
