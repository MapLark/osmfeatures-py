"""Unit tests for geo-agent SDK methods (mocked HTTP)."""

from __future__ import annotations

import json

import pytest
import responses as rsps

from osmfeatures import OSMFeaturesClient
from osmfeatures._geo_agent import parse_place_ref, places_details_path
from tests.conftest import BASE_URL, FAKE_API_KEY


@pytest.fixture
def client() -> OSMFeaturesClient:
    return OSMFeaturesClient(api_key=FAKE_API_KEY, base_url=BASE_URL, timeout=5.0)


@rsps.activate
def test_places_search_posts_camelcase_body(client: OSMFeaturesClient):
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json={"type": "FeatureCollection", "features": []},
    )
    out = client.places_search(
        location={"lat": 59.316, "lon": 18.075},
        radius=500,
        or_tags=["amenity=cafe"],
        open_now=True,
        as_of="2026-08-10T18:00:00+02:00",
        limit=10,
    )
    assert out["type"] == "FeatureCollection"
    body = json.loads(rsps.calls[0].request.body)
    assert body["location"] == {"lat": 59.316, "lng": 18.075}
    assert body["orTags"] == ["amenity=cafe"]
    assert body["openNow"] is True
    assert body["asOf"] == "2026-08-10T18:00:00+02:00"
    assert body["limit"] == 10


@rsps.activate
def test_routes_isochrone_and_optimized_path(client: OSMFeaturesClient):
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/routes/isochrone",
        json={"status": "ok", "geometry": {"type": "Polygon", "coordinates": []}, "distance_m": 500},
    )
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/routes/optimized_path",
        json={"status": "ok", "geometry": {"type": "LineString", "coordinates": [[18.0, 59.0], [18.1, 59.1]]}},
    )
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/routes/path",
        json={"status": "ok", "geometry": {"type": "LineString", "coordinates": [[18.0, 59.0], [18.1, 59.1]]}},
    )
    iso = client.routes_isochrone(origin={"lon": 18.075, "lat": 59.316}, max_distance_m=500)
    assert iso["status"] == "ok"
    opt = client.routes_optimized_path(
        start={"lon": 18.075, "lat": 59.316},
        stops=[{"lng": 18.08, "lat": 59.318}],
        travel_mode="BICYCLE",
    )
    assert opt["status"] == "ok"
    path = client.routes_path(
        stops=[{"lon": 18.075, "lat": 59.316}, {"lng": 18.08, "lat": 59.318}],
    )
    assert path["status"] == "ok"
    iso_body = json.loads(rsps.calls[0].request.body)
    assert "travelMode" not in iso_body
    assert iso_body["origin"] == {"lon": 18.075, "lat": 59.316}
    opt_body = json.loads(rsps.calls[1].request.body)
    assert opt_body["travelMode"] == "BICYCLE"
    assert opt_body["start"] == {"lon": 18.075, "lat": 59.316}
    assert opt_body["stops"][0] == {"lon": 18.08, "lat": 59.318}
    assert "loop" not in opt_body
    path_body = json.loads(rsps.calls[2].request.body)
    assert path_body["stops"][0] == {"lon": 18.075, "lat": 59.316}
    assert "travelMode" not in path_body


@rsps.activate
def test_usage(client: OSMFeaturesClient):
    rsps.add(
        rsps.GET,
        f"{BASE_URL}/v1/usage",
        json={"tier": "standard", "usage_this_month": 1},
    )
    out = client.usage()
    assert out["tier"] == "standard"
    assert rsps.calls[0].request.url.endswith("/v1/usage")


@rsps.activate
def test_places_nearby(client: OSMFeaturesClient):
    rsps.add(rsps.POST, f"{BASE_URL}/v1/places/nearby", json={"status": "ok", "items": []})
    nearby = client.places_nearby(
        location={"lat": 59.3, "lng": 18.0},
        type="cafe",
        limit=3,
        open_now=True,
        as_of="2026-08-10T18:00:00+02:00",
    )
    assert nearby["status"] == "ok"
    assert "/v1/places/nearby" in rsps.calls[0].request.url
    body = json.loads(rsps.calls[0].request.body)
    assert body["openNow"] is True
    assert body["asOf"] == "2026-08-10T18:00:00+02:00"
    assert body["limit"] == 3
    assert "radius" not in body


@rsps.activate
def test_places_and_routes_omit_api_defaults(client: OSMFeaturesClient):
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json={"type": "FeatureCollection", "features": []},
    )
    rsps.add(rsps.POST, f"{BASE_URL}/v1/places/nearby", json={"status": "ok", "items": []})
    client.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=cafe"])
    search_body = json.loads(rsps.calls[0].request.body)
    assert "limit" not in search_body
    assert "openNow" not in search_body
    client.places_nearby(location={"lat": 59.3, "lng": 18.0}, or_tags=["amenity=cafe"])
    nearby_body = json.loads(rsps.calls[1].request.body)
    assert "limit" not in nearby_body
    assert "radius" not in nearby_body


def test_parse_place_ref():
    assert parse_place_ref("node", 123) == ("node", 123)
    assert parse_place_ref("WAY", " 456 ") == ("way", 456)
    assert parse_place_ref("node/789") == ("node", 789)
    assert places_details_path("node/1") == "/v1/places/node/1"
    with pytest.raises(ValueError):
        parse_place_ref("highway", 1)
    with pytest.raises(ValueError):
        parse_place_ref("node", 0)


@rsps.activate
def test_places_details_gets_path_and_query(client: OSMFeaturesClient):
    rsps.add(
        rsps.GET,
        f"{BASE_URL}/v1/places/node/123",
        json={"status": "ok", "feature": {"id": "node/123"}},
    )
    out = client.places_details("node/123")
    assert out["status"] == "ok"
    assert out["feature"]["id"] == "node/123"
    req = rsps.calls[0].request
    assert req.method == "GET"
    assert req.url.rstrip("/").endswith("/v1/places/node/123")

    rsps.add(
        rsps.GET,
        f"{BASE_URL}/v1/places/way/99",
        json={"status": "ok", "feature": {"id": "way/99"}},
    )
    split = client.places_details("way", 99)
    assert split["feature"]["id"] == "way/99"
