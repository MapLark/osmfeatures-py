"""Fake-host tests for the geo-agent MCP session (no live LLM, no HTTP)."""

from __future__ import annotations

import json

import pytest

from osmfeatures._mcp_session import (
    GeoAgentSession,
    QUERY_ALL_MAX_FEATURES,
    SUMMARY_ITEM_CAP,
    assert_no_coordinate_arrays,
)
from osmfeatures.geometry import point_in_geometry
from osmfeatures.models import OSMFeature, OSMFeatureCollection, ResponseMeta


def _feat(fid: str, lon: float, lat: float, *, name: str | None = None, status: str = "unknown") -> dict:
    tags = {"name": name} if name else {}
    return {
        "type": "Feature",
        "id": fid,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "tags": tags,
            "openingHours": {"status": status, "openNow": status == "open"},
        },
    }


def _fc(*feats: dict) -> dict:
    return {
        "type": "FeatureCollection",
        "features": list(feats),
        "metadata": {"estimated_units": 1, "evaluated_at": "2026-08-10T18:00:00Z"},
    }


SQUARE = {
    "type": "Polygon",
    "coordinates": [[[18.07, 59.31], [18.09, 59.31], [18.09, 59.33], [18.07, 59.33], [18.07, 59.31]]],
}

# Hole around the square's centre (18.08, 59.32).
SQUARE_WITH_HOLE = {
    "type": "Polygon",
    "coordinates": [
        SQUARE["coordinates"][0],
        [[18.075, 59.315], [18.085, 59.315], [18.085, 59.325], [18.075, 59.325], [18.075, 59.315]],
    ],
}

WEST_SQUARE = {
    "type": "Polygon",
    "coordinates": [[[18.00, 59.31], [18.02, 59.31], [18.02, 59.33], [18.00, 59.33], [18.00, 59.31]]],
}


def _poly_feat(
    fid: str,
    geom: dict,
    *,
    name: str | None = None,
    centroid: tuple[float, float] | None = None,
) -> dict:
    tags = {"name": name} if name else {}
    props: dict = {"tags": tags, "openingHours": {"status": "unknown", "openNow": False}}
    if centroid is not None:
        props["centroid"] = {"type": "Point", "coordinates": [centroid[0], centroid[1]]}
    return {"type": "Feature", "id": fid, "geometry": geom, "properties": props}


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.search: dict | None = None
        self.nearby: dict | None = None
        self.details: dict | None = None
        self.isochrone: dict | None = None
        self.path: dict | None = None
        self.optimized: dict | None = None
        self.query_result: dict | None = None
        self.query_all_result: dict | None = None

    def places_search(self, **kwargs):
        self.calls.append(("places_search", kwargs))
        return self.search

    def places_nearby(self, **kwargs):
        self.calls.append(("places_nearby", kwargs))
        return self.nearby

    def places_details(self, osm_type, osm_id=None):
        self.calls.append(("places_details", {"osm_type": osm_type, "osm_id": osm_id}))
        return self.details

    def routes_isochrone(self, **kwargs):
        self.calls.append(("routes_isochrone", kwargs))
        return self.isochrone

    def routes_path(self, **kwargs):
        self.calls.append(("routes_path", kwargs))
        return self.path

    def routes_optimized_path(self, **kwargs):
        self.calls.append(("routes_optimized_path", kwargs))
        return self.optimized

    def query(self, **kwargs):
        self.calls.append(("query", kwargs))
        return self.query_result

    def query_all(self, **kwargs):
        self.calls.append(("query_all", kwargs))
        return self.query_all_result


def test_point_in_geometry_square():
    assert point_in_geometry(18.08, 59.32, SQUARE) is True
    assert point_in_geometry(18.05, 59.32, SQUARE) is False
    assert point_in_geometry(18.08, 59.32, {"type": "LineString", "coordinates": []}) is False


def test_point_in_geometry_hole_and_multipolygon():
    assert point_in_geometry(18.08, 59.32, SQUARE_WITH_HOLE) is False
    assert point_in_geometry(18.072, 59.312, SQUARE_WITH_HOLE) is True
    multi = {"type": "MultiPolygon", "coordinates": [WEST_SQUARE["coordinates"], SQUARE["coordinates"]]}
    assert point_in_geometry(18.01, 59.32, multi) is True
    assert point_in_geometry(18.08, 59.32, multi) is True
    assert point_in_geometry(18.05, 59.32, multi) is False
    holed_multi = {"type": "MultiPolygon", "coordinates": [SQUARE_WITH_HOLE["coordinates"]]}
    assert point_in_geometry(18.08, 59.32, holed_multi) is False


def test_cafes_near_me_place_list():
    client = FakeClient()
    client.nearby = {
        "status": "ok",
        "items": [
            {"feature": _feat("node/1", 18.075, 59.316, name="Drop Coffee"), "distance_m": 84.0},
            {"feature": _feat("node/2", 18.076, 59.317, name="Cafe Pascal"), "distance_m": 210.0},
        ],
        "estimated_units": 2,
        "evaluated_at": "2026-08-10T16:00:00Z",
    }
    session = GeoAgentSession(client)
    out = session.places_nearby(location={"lat": 59.316, "lng": 18.075}, or_tags=["amenity=cafe"], limit=5)
    assert_no_coordinate_arrays(out)
    assert out["collection_id"] == "fc_1"
    assert out["count"] == 2
    assert out["evaluated_at"] == "2026-08-10T16:00:00Z"
    assert out["items"][0]["name"] == "Drop Coffee"
    assert out["items"][0]["distance_m"] == 84.0
    assert out["items"][0]["lon"] == 18.075
    assert "items_truncated" not in out
    exported = session.export_geojson("fc_1")
    assert exported["features"][0]["geometry"]["coordinates"] == [18.075, 59.316]


def test_vegan_count_and_names():
    client = FakeClient()
    client.search = _fc(
        _feat("node/10", 18.07, 59.32, name="Hermitage"),
        _feat("node/11", 18.071, 59.321, name="Kaffeverket"),
    )
    session = GeoAgentSession(client)
    out = session.places_search(bbox="18.05,59.31,18.10,59.33", tags=["amenity=restaurant", "diet:vegan=yes"])
    assert_no_coordinate_arrays(out)
    assert out["count"] == 2
    assert out["evaluated_at"] == "2026-08-10T18:00:00Z"
    assert [i["name"] for i in out["items"]] == ["Hermitage", "Kaffeverket"]


def test_places_search_rejects_bbox_and_location_or_neither():
    session = GeoAgentSession(FakeClient())
    with pytest.raises(ValueError, match="not both"):
        session.places_search(
            bbox="18.05,59.31,18.10,59.33",
            location={"lat": 59.316, "lng": 18.075},
            radius=800,
        )
    with pytest.raises(ValueError, match="requires bbox"):
        session.places_search()


def test_restaurants_near_stations_pairs():
    client = FakeClient()
    session = GeoAgentSession(client)
    client.search = _fc(_feat("node/r1", 18.0702, 59.316, name="Pelikan"))
    restaurants = session.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=restaurant"])
    client.search = _fc(_feat("node/s1", 18.07, 59.316, name="Medborgarplatsen"))
    stations = session.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["railway=station"])
    pairs = session.nearest_within(restaurants["collection_id"], stations["collection_id"], max_distance_m=150)
    assert_no_coordinate_arrays(pairs)
    assert pairs["count"] == 1
    assert pairs["items"][0]["name"] == "Pelikan"
    assert pairs["items"][0]["nearest_name"] == "Medborgarplatsen"
    assert pairs["items"][0]["distance_m"] < 150
    assert pairs["items"][0]["lon"] == 18.0702
    assert pairs["items"][0]["lat"] == 59.316
    assert pairs["items"][0]["nearest_lon"] == 18.07
    assert pairs["items"][0]["nearest_lat"] == 59.316
    exported = session.export_geojson(pairs["collection_id"])
    assert exported["features"][0]["properties"]["nearest_id"] == "node/s1"
    assert exported["features"][0]["properties"]["distance_m"] < 150


def test_nearest_within_keeps_every_primary_and_caps_items():
    client = FakeClient()
    session = GeoAgentSession(client)
    client.search = _fc(*[_feat(f"node/r{i}", 18.0702, 59.316, name=f"R{i}") for i in range(45)])
    restaurants = session.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=restaurant"])
    client.search = _fc(_feat("node/s1", 18.07, 59.316, name="Medborgarplatsen"))
    stations = session.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["railway=station"])
    pairs = session.nearest_within(restaurants["collection_id"], stations["collection_id"], max_distance_m=150)
    assert_no_coordinate_arrays(pairs)
    assert pairs["count"] == 45
    assert len(pairs["items"]) == SUMMARY_ITEM_CAP
    assert pairs["items_truncated"] is True


def test_nearest_within_rejects_over_comparison_cap(monkeypatch):
    monkeypatch.setattr("osmfeatures._mcp_session.MAX_COMPARISONS", 20)
    client = FakeClient()
    session = GeoAgentSession(client)
    client.search = _fc(*[_feat(f"node/r{i}", 18.0702, 59.316, name=f"R{i}") for i in range(5)])
    restaurants = session.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=restaurant"])
    client.search = _fc(*[_feat(f"node/s{i}", 18.07, 59.316, name=f"S{i}") for i in range(5)])
    stations = session.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["railway=station"])
    with pytest.raises(ValueError, match=r"5×5 comparisons"):
        session.nearest_within(restaurants["collection_id"], stations["collection_id"], max_distance_m=150)


def test_open_at_clock_filter():
    client = FakeClient()
    client.search = _fc(
        _feat("node/1", 18.07, 59.316, name="Open Bar", status="open"),
        _feat("node/2", 18.071, 59.316, name="Closed Bar", status="closed"),
        _feat("node/3", 18.072, 59.316, name="Unknown Pub", status="unknown"),
    )
    session = GeoAgentSession(client)
    page = session.places_search(
        location={"lat": 59.316, "lng": 18.075},
        radius=1200,
        or_tags=["amenity=bar", "amenity=pub"],
        as_of="2026-08-10T20:00:00",
        open_now=False,
    )
    assert_no_coordinate_arrays(page)
    assert page["evaluated_at"] == "2026-08-10T18:00:00Z"
    opened = session.filter_open(page["collection_id"])
    assert_no_coordinate_arrays(opened)
    assert opened["count"] == 1
    assert opened["evaluated_at"] == "2026-08-10T18:00:00Z"
    assert opened["items"][0]["name"] == "Open Bar"
    assert client.calls[0][1]["as_of"] == "2026-08-10T20:00:00"
    assert client.calls[0][1]["open_now"] is False


def test_filter_open_keeps_nearby_distance():
    client = FakeClient()
    client.nearby = {
        "status": "ok",
        "items": [
            {"feature": _feat("node/1", 18.075, 59.316, name="Open Near", status="open"), "distance_m": 84.0},
            {"feature": _feat("node/2", 18.076, 59.317, name="Closed Near", status="closed"), "distance_m": 90.0},
            {"feature": _feat("node/3", 18.08, 59.32, name="Open Far", status="open"), "distance_m": 210.0},
        ],
        "estimated_units": 3,
        "evaluated_at": "2026-08-10T16:00:00Z",
    }
    session = GeoAgentSession(client)
    nearby = session.places_nearby(location={"lat": 59.316, "lng": 18.075}, or_tags=["amenity=cafe"])
    opened = session.filter_open(nearby["collection_id"])
    assert_no_coordinate_arrays(opened)
    assert opened["count"] == 2
    assert opened["evaluated_at"] == "2026-08-10T16:00:00Z"
    assert [i["name"] for i in opened["items"]] == ["Open Near", "Open Far"]
    assert opened["items"][0]["distance_m"] == 84.0
    assert opened["items"][1]["distance_m"] == 210.0
    exported = session.export_geojson(opened["collection_id"])
    assert exported["features"][0]["properties"]["distance_m"] == 84.0
    assert exported["features"][1]["properties"]["distance_m"] == 210.0


def test_walk_time_yes_no():
    client = FakeClient()
    client.isochrone = {
        "status": "ok",
        "geometry": SQUARE,
        "distance_m": 1680.0,
        "duration_s": 1200.0,
    }
    session = GeoAgentSession(client)
    iso = session.routes_isochrone(origin={"lon": 18.075, "lat": 59.316}, duration_s=1200, travel_mode="WALK")
    assert_no_coordinate_arrays(iso)
    assert iso["status"] == "ok"
    assert iso["geometry_type"] == "Polygon"
    near = session.point_in_polygon(iso["collection_id"], 18.08, 59.32)
    far = session.point_in_polygon(iso["collection_id"], 18.05, 59.35)
    assert near["inside"] is True
    assert far["inside"] is False
    exported = session.export_geojson(iso["collection_id"])
    assert exported["features"][0]["geometry"]["type"] == "Polygon"


def test_path_and_optimized_route_summary():
    client = FakeClient()
    client.path = {
        "status": "ok",
        "distance_m": 640.0,
        "duration_s": 457.0,
        "stop_distances_m": [640.0],
        "ordered_stops": [{"lon": 18.07, "lat": 59.316}, {"lon": 18.08, "lat": 59.318}],
        "geometry": {"type": "LineString", "coordinates": [[18.07, 59.316], [18.08, 59.318]]},
    }
    # Wire format from /v1/routes/optimized_path: lon/lat only, visit order.
    client.optimized = {
        "status": "ok",
        "distance_m": 1800.0,
        "duration_s": 1286.0,
        "stop_distances_m": [400.0, 500.0, 900.0],
        "ordered_stops": [
            {"lon": 18.075, "lat": 59.316},
            {"lon": 18.08, "lat": 59.32},
            {"lon": 18.07, "lat": 59.318},
        ],
        "geometry": {"type": "LineString", "coordinates": [[18.07, 59.316], [18.08, 59.32]]},
    }
    session = GeoAgentSession(client)
    path = session.routes_path(stops=[{"lon": 18.07, "lat": 59.316}, {"lon": 18.08, "lat": 59.318}])
    assert_no_coordinate_arrays(path)
    assert path["distance_m"] == 640.0
    assert path["stop_distances_m"] == [640.0]
    assert path["ordered_stops"] == [
        {"lon": 18.07, "lat": 59.316},
        {"lon": 18.08, "lat": 59.318},
    ]
    opt = session.routes_optimized_path(
        start={"lon": 18.075, "lat": 59.316},
        stops=[{"lon": 18.08, "lat": 59.32}, {"lon": 18.07, "lat": 59.318}],
        loop=True,
    )
    assert_no_coordinate_arrays(opt)
    assert opt["ordered_stop_count"] == 3
    assert opt["ordered_stops"] == [
        {"lon": 18.075, "lat": 59.316},
        {"lon": 18.08, "lat": 59.32},
        {"lon": 18.07, "lat": 59.318},
    ]


def test_optimized_route_summary_keeps_id_and_lon_lat():
    client = FakeClient()
    client.optimized = {
        "status": "ok",
        "distance_m": 1800.0,
        "duration_s": 1286.0,
        "ordered_stops": [
            {"id": "start", "lon": 18.075, "lat": 59.316},
            {"id": "node/1", "name": "Bar A", "lon": 18.08, "lat": 59.32},
        ],
        "geometry": {"type": "LineString", "coordinates": [[18.075, 59.316], [18.08, 59.32]]},
    }
    session = GeoAgentSession(client)
    opt = session.routes_optimized_path(
        start={"lon": 18.075, "lat": 59.316},
        stops=[{"lon": 18.08, "lat": 59.32}],
        loop=False,
    )
    assert_no_coordinate_arrays(opt)
    assert opt["ordered_stops"][0]["id"] == "start"
    assert opt["ordered_stops"][1]["name"] == "Bar A"
    assert opt["ordered_stops"][1]["lon"] == 18.08


def test_points_in_polygon_filters_search():
    client = FakeClient()
    client.search = _fc(
        _feat("node/1", 18.08, 59.32, name="Inside Cafe"),
        _feat("node/2", 18.05, 59.32, name="Outside Cafe"),
    )
    client.isochrone = {"status": "ok", "geometry": SQUARE, "distance_m": 1000.0, "duration_s": 714.0}
    session = GeoAgentSession(client)
    cafes = session.places_search(location={"lat": 59.316, "lng": 18.075}, radius=1500, or_tags=["amenity=cafe"])
    iso = session.routes_isochrone(origin={"lon": 18.075, "lat": 59.316}, max_distance_m=1000)
    inside = session.points_in_polygon(cafes["collection_id"], iso["collection_id"])
    assert_no_coordinate_arrays(inside)
    assert inside["count"] == 1
    assert inside["items"][0]["name"] == "Inside Cafe"


def test_points_in_polygon_keeps_nearby_distance():
    client = FakeClient()
    client.nearby = {
        "status": "ok",
        "items": [
            {"feature": _feat("node/1", 18.08, 59.32, name="Inside Near"), "distance_m": 84.0},
            {"feature": _feat("node/2", 18.05, 59.32, name="Outside Near"), "distance_m": 210.0},
        ],
        "evaluated_at": "2026-08-10T16:00:00Z",
    }
    client.isochrone = {"status": "ok", "geometry": SQUARE, "distance_m": 1000.0, "duration_s": 714.0}
    session = GeoAgentSession(client)
    nearby = session.places_nearby(location={"lat": 59.316, "lng": 18.075}, or_tags=["amenity=cafe"])
    iso = session.routes_isochrone(origin={"lon": 18.075, "lat": 59.316}, max_distance_m=1000)
    inside = session.points_in_polygon(nearby["collection_id"], iso["collection_id"])
    assert_no_coordinate_arrays(inside)
    assert inside["count"] == 1
    assert inside["items"][0]["name"] == "Inside Near"
    assert inside["items"][0]["distance_m"] == 84.0
    exported = session.export_geojson(inside["collection_id"])
    assert exported["features"][0]["properties"]["distance_m"] == 84.0


def test_query_and_details_summaries():
    client = FakeClient()
    client.query_result = _fc(_feat("way/1", 18.07, 59.32, name="Tantolunden"))
    client.details = {
        "status": "ok",
        "feature": _feat("node/1", 18.075, 59.316, name="Drop Coffee", status="open"),
        "estimated_units": 1,
        "evaluated_at": "2026-08-10T16:00:00Z",
        "timezone": "Europe/Stockholm",
    }
    session = GeoAgentSession(client)
    parks = session.query(bbox="18.05,59.31,18.10,59.33", tags=["leisure=park"], way_shape="polygon")
    assert_no_coordinate_arrays(parks)
    assert parks["count"] == 1
    assert client.calls[0][1]["centroid"] is True
    detail = session.places_details("node", 1)
    assert_no_coordinate_arrays(detail)
    assert detail["item"]["name"] == "Drop Coffee"
    assert detail["evaluated_at"] == "2026-08-10T16:00:00Z"
    assert detail["timezone"] == "Europe/Stockholm"
    assert detail["estimated_units"] == 1
    exported = session.export_geojson(detail["collection_id"])
    assert exported["features"][0]["id"] == "node/1"
    assert exported["features"][0]["geometry"]["coordinates"] == [18.075, 59.316]


def test_query_all_forwards_bbox_tiles():
    client = FakeClient()
    client.query_all_result = _fc(
        _feat("way/1", 18.07, 59.32, name="Park A"),
        _feat("way/2", 18.08, 59.32, name="Park B"),
    )
    session = GeoAgentSession(client)
    out = session.query_all(
        bbox="18.05,59.31,18.12,59.36",
        tags=["leisure=park"],
        way_shape="polygon",
        bbox_tiles=4,
        limit_per_page=500,
        max_features=10_000,
    )
    assert_no_coordinate_arrays(out)
    assert out["count"] == 2
    assert client.calls[-1][0] == "query_all"
    assert client.calls[-1][1]["bbox_tiles"] == 4
    assert client.calls[-1][1]["limit_per_page"] == 500
    assert client.calls[-1][1]["max_features"] == 10_000
    assert client.calls[-1][1]["centroid"] is True
    assert "limit" not in client.calls[-1][1]


def test_query_summaries_include_has_more():
    client = FakeClient()
    truncated = OSMFeatureCollection(
        features=[OSMFeature.from_dict(_feat("way/1", 18.07, 59.32, name="Park A"))],
        meta=ResponseMeta(returned=1, has_more=True),
    )
    client.query_result = truncated
    client.query_all_result = truncated
    session = GeoAgentSession(client)
    page = session.query(bbox="18.05,59.31,18.10,59.33", tags=["leisure=park"])
    drained = session.query_all(bbox="18.05,59.31,18.12,59.36", tags=["leisure=park"])
    assert page["has_more"] is True
    assert drained["has_more"] is True
    assert client.calls[-1][1]["max_features"] == QUERY_ALL_MAX_FEATURES
    assert QUERY_ALL_MAX_FEATURES == 10000


def test_query_all_rejects_unlimited_max_features():
    session = GeoAgentSession(FakeClient())
    with pytest.raises(ValueError, match="cannot be None"):
        session.query_all(bbox="18.05,59.31,18.12,59.36", max_features=None)
    with pytest.raises(ValueError, match="positive"):
        session.query_all(bbox="18.05,59.31,18.12,59.36", max_features=0)


def test_query_summary_caps_items_not_count():
    client = FakeClient()
    client.query_result = _fc(*[_feat(f"way/{i}", 18.07, 59.32, name=f"Park {i}") for i in range(45)])
    client.search = _fc(*[_feat(f"node/{i}", 18.07, 59.32, name=f"Cafe {i}") for i in range(45)])
    session = GeoAgentSession(client)
    parks = session.query(bbox="18.05,59.31,18.10,59.33", tags=["leisure=park"])
    assert parks["count"] == 45
    assert len(parks["items"]) == SUMMARY_ITEM_CAP
    assert parks["items_truncated"] is True
    cafes = session.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=cafe"])
    assert cafes["count"] == 45
    assert len(cafes["items"]) == SUMMARY_ITEM_CAP
    assert cafes["items_truncated"] is True


def test_query_polygons_join_via_centroid():
    client = FakeClient()
    client.query_result = _fc(
        _poly_feat("way/park", SQUARE, name="Tantolunden", centroid=(18.0702, 59.316)),
        _poly_feat("way/far", WEST_SQUARE, name="Far Park", centroid=(18.01, 59.32)),
    )
    session = GeoAgentSession(client)
    parks = session.query(bbox="18.00,59.31,18.10,59.33", tags=["leisure=park"], way_shape="polygon")
    client.search = _fc(_feat("node/s1", 18.07, 59.316, name="Medborgarplatsen"))
    stations = session.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["railway=station"])
    pairs = session.nearest_within(parks["collection_id"], stations["collection_id"], max_distance_m=150)
    assert_no_coordinate_arrays(pairs)
    assert pairs["count"] == 1
    assert pairs["items"][0]["name"] == "Tantolunden"
    assert parks["items"][0]["lon"] == 18.0702


def test_point_in_polygon_query_collection():
    client = FakeClient()
    client.query_result = _fc(_poly_feat("way/park", SQUARE, name="Tantolunden", centroid=(18.08, 59.32)))
    session = GeoAgentSession(client)
    parks = session.query(bbox="18.05,59.31,18.10,59.33", tags=["leisure=park"], way_shape="polygon")
    inside = session.point_in_polygon(parks["collection_id"], 18.08, 59.32)
    outside = session.point_in_polygon(parks["collection_id"], 18.05, 59.32)
    assert inside["inside"] is True
    assert outside["inside"] is False


def test_points_in_polygon_uses_centroid_fallback():
    client = FakeClient()
    client.query_result = _fc(
        _poly_feat("way/in", SQUARE, name="Inside Park", centroid=(18.08, 59.32)),
        _poly_feat("way/out", WEST_SQUARE, name="Outside Park", centroid=(18.01, 59.32)),
    )
    client.isochrone = {"status": "ok", "geometry": SQUARE, "distance_m": 1000.0, "duration_s": 714.0}
    session = GeoAgentSession(client)
    parks = session.query(bbox="18.00,59.31,18.10,59.33", tags=["leisure=park"], way_shape="polygon")
    iso = session.routes_isochrone(origin={"lon": 18.075, "lat": 59.316}, max_distance_m=1000)
    inside = session.points_in_polygon(parks["collection_id"], iso["collection_id"])
    assert_no_coordinate_arrays(inside)
    assert inside["count"] == 1
    assert inside["items"][0]["name"] == "Inside Park"


def test_unknown_collection_and_missing_polygon():
    session = GeoAgentSession(FakeClient())
    with pytest.raises(KeyError, match="fc_9"):
        session.get("fc_9")
    client = FakeClient()
    client.search = _fc(_feat("node/1", 18.07, 59.32, name="Cafe"))
    session = GeoAgentSession(client)
    page = session.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=cafe"])
    with pytest.raises(ValueError, match="Polygon"):
        session.point_in_polygon(page["collection_id"], 18.07, 59.32)


def test_export_geojson_file_returns_path_not_geometry(tmp_path):
    client = FakeClient()
    client.details = {
        "status": "ok",
        "feature": _feat("node/1", 18.075, 59.316, name="Drop Coffee", status="open"),
    }
    session = GeoAgentSession(client)
    detail = session.places_details("node", 1)
    out = session.export_geojson_file(detail["collection_id"], directory=tmp_path)
    assert_no_coordinate_arrays(out)
    assert out["collection_id"] == detail["collection_id"]
    assert out["feature_count"] == 1
    saved = json.loads((tmp_path / f"maplark-{detail['collection_id']}.geojson").read_text())
    assert saved["features"][0]["geometry"]["coordinates"] == [18.075, 59.316]


def test_session_store_evicts_lru_collections():
    client = FakeClient()
    client.search = _fc(_feat("node/1", 18.07, 59.32, name="Cafe"))
    session = GeoAgentSession(client, max_collections=2)
    first = session.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=cafe"])
    second = session.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=cafe"])
    session.get(first["collection_id"])
    third = session.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=cafe"])
    session.get(first["collection_id"])
    session.get(third["collection_id"])
    with pytest.raises(KeyError):
        session.get(second["collection_id"])

    unused = GeoAgentSession(client, max_collections=2)
    a = unused.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=cafe"])
    b = unused.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=cafe"])
    c = unused.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=cafe"])
    with pytest.raises(KeyError):
        unused.get(a["collection_id"])
    unused.get(b["collection_id"])
    unused.get(c["collection_id"])
