"""Local MapLibre preview server (no live tiles, no browser)."""

from __future__ import annotations

import json
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from osmfeatures.mcp._mcp_session import GeoAgentSession, assert_no_coordinate_arrays
from osmfeatures.mcp._preview import (
    PreviewServer,
    geojson_for_map,
    geojson_for_map_many,
    parse_collection_ids,
    preview_html,
    validate_collection_id,
)


def _feat(fid: str, lon: float, lat: float, *, name: str, **extra_tags: str) -> dict:
    return {
        "type": "Feature",
        "id": fid,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"tags": {"name": name, **extra_tags}},
    }


@pytest.fixture
def preview():
    client = type("C", (), {})()
    session = GeoAgentSession(client)
    session._put(
        {
            "type": "FeatureCollection",
            "features": [
                _feat("node/1", 18.075, 59.316, name="Drop Coffee", amenity="cafe", cuisine="coffee_shop")
            ],
        }
    )
    server = PreviewServer(session)
    try:
        yield session, server
    finally:
        server.close()


def test_validate_collection_id():
    assert validate_collection_id("fc_1") == "fc_1"
    with pytest.raises(ValueError):
        validate_collection_id("../secret")
    with pytest.raises(ValueError):
        validate_collection_id("fc_1/extra")


def test_parse_collection_ids():
    assert parse_collection_ids("fc_1") == ["fc_1"]
    assert parse_collection_ids("fc_2,fc_1,fc_2") == ["fc_2", "fc_1"]
    assert parse_collection_ids(["fc_3", "fc_4"]) == ["fc_3", "fc_4"]
    with pytest.raises(ValueError):
        parse_collection_ids("")
    with pytest.raises(ValueError):
        parse_collection_ids("fc_1,../secret")


def test_preview_html_uses_openfreemap_and_relative_geojson():
    html = preview_html("fc_3")
    assert "tiles.openfreemap.org/styles/liberty" in html
    assert '"fc_3"' in html
    assert "/collections/" in html
    assert ".geojson" in html
    assert "FeatureCollection" not in html
    assert "JSON.parse(tags)" in html
    assert "popupHtml" in html
    assert "SKIP_PROP_KEYS" in html
    assert "openNow" in html
    assert "distance_m" in html
    assert "pointLonLat" in html
    assert "search_origin" in html
    assert "ml-origin-marker" in html
    assert "ml-popup-tags" in html
    assert "setHTML" in html
    assert "escapeHtml" in html
    # Origin marker popup escapes lon/lat (same helper as feature popups).
    assert 'escapeHtml(fc.search_origin.lng)' in html
    assert 'escapeHtml(fc.search_origin.lat)' in html


def test_geojson_for_map_promotes_tags_name():
    fc = {
        "type": "FeatureCollection",
        "features": [
            _feat("node/1", 18.075, 59.316, name="Drop Coffee", amenity="cafe", cuisine="coffee_shop")
        ],
        "search_origin": {"lat": 59.316, "lng": 18.075},
    }
    out = geojson_for_map(fc)
    assert out["features"][0]["properties"]["name"] == "Drop Coffee"
    assert out["features"][0]["properties"]["tags"] == {
        "name": "Drop Coffee",
        "amenity": "cafe",
        "cuisine": "coffee_shop",
    }
    assert out["search_origin"] == {"lat": 59.316, "lng": 18.075}
    assert fc["features"][0]["properties"].get("name") is None


def test_preview_open_returns_url_without_geometry(preview, monkeypatch):
    session, server = preview
    monkeypatch.setattr("osmfeatures.mcp._preview.webbrowser.open", lambda url: True)
    out = server.open("fc_1", open_browser=True)
    assert_no_coordinate_arrays(out)
    assert out["collection_ids"] == ["fc_1"]
    assert out["preview_url"] == f"{server.base_url}/preview/fc_1"
    assert out["opened"] is True


def test_http_preview_and_geojson(preview):
    _session, server = preview
    with urlopen(f"{server.base_url}/preview/fc_1", timeout=2) as resp:
        html = resp.read().decode()
        assert resp.status == 200
        assert "tiles.openfreemap.org/styles/liberty" in html
        assert "search_origin" in html
        assert "SKIP_PROP_KEYS" in html
    with urlopen(f"{server.base_url}/collections/fc_1.geojson", timeout=2) as resp:
        body = json.loads(resp.read())
        assert body["features"][0]["geometry"]["coordinates"] == [18.075, 59.316]
        assert body["features"][0]["properties"]["name"] == "Drop Coffee"
        assert body["features"][0]["properties"]["tags"] == {
            "name": "Drop Coffee",
            "amenity": "cafe",
            "cuisine": "coffee_shop",
        }


def test_http_geojson_includes_search_origin():
    client = type("C", (), {})()
    session = GeoAgentSession(client)
    session._put(
        {
            "type": "FeatureCollection",
            "features": [_feat("node/1", 18.075, 59.316, name="Drop Coffee")],
            "search_origin": {"lat": 59.316, "lng": 18.075},
        }
    )
    server = PreviewServer(session)
    try:
        with urlopen(f"{server.base_url}/collections/fc_1.geojson", timeout=2) as resp:
            body = json.loads(resp.read())
        assert body["search_origin"] == {"lat": 59.316, "lng": 18.075}
    finally:
        server.close()


def test_http_unknown_and_invalid(preview):
    _session, server = preview
    with pytest.raises(HTTPError) as missing:
        urlopen(f"{server.base_url}/preview/fc_9", timeout=2)
    assert missing.value.code == 404
    with pytest.raises(HTTPError) as invalid:
        urlopen(f"{server.base_url}/preview/not-an-id", timeout=2)
    assert invalid.value.code == 404


def test_preview_unknown_collection(preview):
    _session, server = preview
    with pytest.raises(KeyError):
        server.open("fc_9", open_browser=False)


def test_http_route_geojson_pins_places_like_cafe_search():
    client = type("C", (), {})()
    session = GeoAgentSession(client)
    session._put(
        {
            "type": "FeatureCollection",
            "features": [
                _feat("node/1", 18.07, 59.316, name="Akkurat", amenity="pub"),
                _feat("node/2", 18.08, 59.318, name="Oliver Twist", amenity="pub"),
            ],
        }
    )
    session._put(
        {
            "status": "ok",
            "distance_m": 640.0,
            "ordered_stops": [
                {"lon": 18.07, "lat": 59.316},
                {"lon": 18.08, "lat": 59.318},
            ],
            "geometry": {"type": "LineString", "coordinates": [[18.07, 59.316], [18.08, 59.318]]},
        }
    )
    server = PreviewServer(session)
    try:
        with urlopen(f"{server.base_url}/collections/fc_2.geojson", timeout=2) as resp:
            body = json.loads(resp.read())
        points = [f for f in body["features"] if f["geometry"]["type"] == "Point"]
        assert len(points) == 2
        assert points[0]["properties"]["name"] == "Akkurat"
        assert points[0]["properties"]["tags"]["amenity"] == "pub"
        assert points[1]["properties"]["name"] == "Oliver Twist"
        assert any(f["geometry"]["type"] == "LineString" for f in body["features"])
    finally:
        server.close()


def test_geojson_for_map_many_concatenates_and_keeps_first_origin():
    places = {
        "type": "FeatureCollection",
        "features": [_feat("node/1", 18.075, 59.316, name="Drop Coffee")],
        "search_origin": {"lat": 59.316, "lng": 18.075},
    }
    route = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[18.07, 59.316], [18.08, 59.318]]},
                "properties": {"name": "walk"},
            }
        ],
    }
    out = geojson_for_map_many([places, route])
    assert [f["geometry"]["type"] for f in out["features"]] == ["Point", "LineString"]
    assert out["features"][0]["properties"]["name"] == "Drop Coffee"
    assert out["search_origin"] == {"lat": 59.316, "lng": 18.075}


def test_http_combined_places_and_route(preview):
    session, server = preview
    session._put(
        {
            "status": "ok",
            "distance_m": 640.0,
            "geometry": {"type": "LineString", "coordinates": [[18.07, 59.316], [18.08, 59.318]]},
        }
    )
    with urlopen(f"{server.base_url}/preview/fc_1,fc_2", timeout=2) as resp:
        html = resp.read().decode()
        assert '"fc_1,fc_2"' in html
    with urlopen(f"{server.base_url}/collections/fc_1,fc_2.geojson", timeout=2) as resp:
        body = json.loads(resp.read())
    kinds = {f["geometry"]["type"] for f in body["features"]}
    assert kinds == {"Point", "LineString"}
    names = [f["properties"].get("name") for f in body["features"]]
    assert "Drop Coffee" in names
    combined = server.open(["fc_1", "fc_2"], open_browser=False)
    assert combined["collection_ids"] == ["fc_1", "fc_2"]
    assert combined["preview_url"].endswith("/preview/fc_1,fc_2")
