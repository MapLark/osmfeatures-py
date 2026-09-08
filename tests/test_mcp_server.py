"""Unit tests for MCP server wiring (tools + planner instructions)."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("mcp")

from mcp.server.fastmcp.exceptions import ToolError

from osmfeatures._mcp_session import (
    QUERY_ALL_MAX_FEATURES,
    SUMMARY_ITEM_CAP,
    GeoAgentSession,
    assert_no_coordinate_arrays,
)
from osmfeatures.mcp_server import INSTRUCTIONS, build_server, places_search_point
from osmfeatures.nearest import MAX_COMPARISONS

_EXPECTED_TOOLS = frozenset(
    {
        "places_search",
        "places_nearby",
        "places_details",
        "routes_isochrone",
        "routes_path",
        "routes_optimized_path",
        "query",
        "query_all",
        "nearest_within",
        "filter_open",
        "point_in_polygon",
        "points_in_polygon",
        "preview_map",
        "export_geojson",
    }
)

SQUARE = {
    "type": "Polygon",
    "coordinates": [[[18.07, 59.31], [18.09, 59.31], [18.09, 59.33], [18.07, 59.33], [18.07, 59.31]]],
}


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
    return {"type": "FeatureCollection", "features": list(feats)}


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


class _FakePreview:
    def open(self, collection_id: str, *, open_browser: bool = True) -> dict:
        return {
            "collection_id": collection_id,
            "preview_url": f"http://127.0.0.1:9/preview/{collection_id}",
            "opened": False,
        }


async def _call(mcp, name: str, **arguments: Any) -> dict[str, Any]:
    """Invoke a FastMCP tool the way a planner would and unwrap the payload."""
    result = await mcp.call_tool(name, arguments)
    if isinstance(result, tuple) and len(result) == 2 and isinstance(result[1], dict):
        payload = result[1]
    elif isinstance(result, dict):
        payload = result
    else:
        raise AssertionError(f"unexpected call_tool result: {type(result)!r}")
    assert_no_coordinate_arrays(payload)
    return payload


@pytest.fixture
def stack():
    client = FakeClient()
    session = GeoAgentSession(client)
    mcp = build_server(session, preview=_FakePreview())
    return client, mcp


@pytest.mark.asyncio
async def test_build_server_wires_instructions_and_tools():
    mcp = build_server(GeoAgentSession(object()), preview=_FakePreview())
    assert mcp.name == "maplark"
    assert mcp.instructions == INSTRUCTIONS
    assert "preview_map" in mcp.instructions
    assert "export_geojson" in mcp.instructions
    assert "writes a file" in mcp.instructions
    assert f"max_features is {QUERY_ALL_MAX_FEATURES}" in mcp.instructions
    assert f"at most {SUMMARY_ITEM_CAP}" in mcp.instructions
    assert "items_truncated" in mcp.instructions
    assert f"over {MAX_COMPARISONS} comparisons" in mcp.instructions
    tools = await mcp.list_tools()
    names = {tool.name for tool in tools}
    assert names == _EXPECTED_TOOLS
    query_all = next(t for t in tools if t.name == "query_all")
    assert f"default {QUERY_ALL_MAX_FEATURES}" in (query_all.description or "")
    cap = query_all.inputSchema["properties"]["max_features"]
    assert cap.get("type") == "integer"
    assert cap.get("default") == QUERY_ALL_MAX_FEATURES
    assert "null" not in {opt.get("type") for opt in cap.get("anyOf") or []}
    nearest = next(t for t in tools if t.name == "nearest_within")
    assert (nearest.inputSchema["properties"]["limit"].get("default") or 0) != 20
    assert "keep every match" not in (nearest.description or "")
    assert "count is complete" in (nearest.description or "")
    assert "250000 comparisons" in (nearest.description or "")


def test_run_stdio_closes_client_and_preview(monkeypatch):
    closed: list[str] = []

    class FakeClient:
        def __init__(self, api_key: str, base_url: str | None = None) -> None:
            pass

        def close(self) -> None:
            closed.append("client")

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_: object) -> None:
            self.close()

    class FakePreview:
        def __init__(self, session: object) -> None:
            pass

        def close(self) -> None:
            closed.append("preview")

    class FakeMCP:
        def run(self, transport: str = "stdio") -> None:
            assert transport == "stdio"

    import osmfeatures.client as client_mod
    import osmfeatures.mcp_server as mcp_server

    monkeypatch.setattr(client_mod, "OSMFeaturesClient", FakeClient)
    monkeypatch.setattr(mcp_server, "PreviewServer", FakePreview)
    monkeypatch.setattr(mcp_server, "build_server", lambda session, preview: FakeMCP())
    mcp_server.run_stdio(api_key="sk-test")
    assert closed == ["preview", "client"]


def test_places_search_point_location_and_radius():
    assert places_search_point(59.316, 18.075, 800) == {"lat": 59.316, "lng": 18.075}
    assert places_search_point(None, None, None) is None


@pytest.mark.parametrize(
    ("lat", "lng", "radius"),
    [
        (59.3, None, 800.0),
        (None, 18.0, 800.0),
        (59.3, 18.0, None),
        (59.3, None, None),
        (None, 18.0, None),
        (None, None, 800.0),
    ],
)
def test_places_search_point_partial_raises(lat, lng, radius):
    with pytest.raises(ValueError, match="lat, lng, and radius"):
        places_search_point(lat, lng, radius)


@pytest.mark.asyncio
async def test_call_tool_places_search_bbox_and_location(stack):
    client, mcp = stack
    client.search = _fc(_feat("node/1", 18.07, 59.32, name="Drop Coffee"))
    by_bbox = await _call(
        mcp,
        "places_search",
        bbox="18.05,59.31,18.10,59.33",
        or_tags=["amenity=cafe"],
        as_of="2026-08-10T18:00:00",
    )
    assert by_bbox["collection_id"] == "fc_1"
    assert by_bbox["items"][0]["name"] == "Drop Coffee"
    assert client.calls[-1][1]["location"] is None
    assert client.calls[-1][1]["as_of"] == "2026-08-10T18:00:00"
    assert client.calls[-1][1]["open_now"] is False
    assert client.calls[-1][1]["limit"] is None

    by_point = await _call(
        mcp,
        "places_search",
        lat=59.316,
        lng=18.075,
        radius=800,
        or_tags=["amenity=cafe"],
    )
    assert client.calls[-1][1]["location"] == {"lat": 59.316, "lng": 18.075}
    assert client.calls[-1][1]["radius"] == 800
    assert by_point["items"][0]["lon"] == 18.07


@pytest.mark.asyncio
async def test_call_tool_places_search_partial_location_errors(stack):
    _client, mcp = stack
    with pytest.raises(ToolError, match="lat, lng, and radius"):
        await mcp.call_tool("places_search", {"lat": 59.316, "radius": 800})


@pytest.mark.asyncio
async def test_call_tool_places_search_bbox_and_point_errors(stack):
    _client, mcp = stack
    with pytest.raises(ToolError, match="not both"):
        await mcp.call_tool(
            "places_search",
            {
                "bbox": "18.05,59.31,18.10,59.33",
                "lat": 59.316,
                "lng": 18.075,
                "radius": 800,
            },
        )
    with pytest.raises(ToolError, match="requires bbox"):
        await mcp.call_tool("places_search", {"or_tags": ["amenity=cafe"]})


@pytest.mark.asyncio
async def test_call_tool_places_nearby_details_and_filter_open(stack):
    client, mcp = stack
    client.nearby = {
        "items": [
            {"feature": _feat("node/1", 18.075, 59.316, name="Open Near", status="open"), "distance_m": 84.0},
            {"feature": _feat("node/2", 18.076, 59.317, name="Closed Near", status="closed"), "distance_m": 90.0},
        ],
        "evaluated_at": "2026-08-10T16:00:00Z",
    }
    client.details = {
        "feature": _feat("node/1", 18.075, 59.316, name="Open Near", status="open"),
        "evaluated_at": "2026-08-10T16:00:00Z",
    }
    nearby = await _call(
        mcp,
        "places_nearby",
        lat=59.316,
        lng=18.075,
        or_tags=["amenity=cafe"],
        limit=5,
    )
    assert client.calls[-1][1]["location"] == {"lat": 59.316, "lng": 18.075}
    assert nearby["items"][0]["distance_m"] == 84.0
    opened = await _call(mcp, "filter_open", collection_id=nearby["collection_id"])
    assert opened["count"] == 1
    assert opened["items"][0]["name"] == "Open Near"
    detail = await _call(mcp, "places_details", osm_type="node/1")
    assert detail["item"]["name"] == "Open Near"
    assert client.calls[-1][1] == {"osm_type": "node/1", "osm_id": None}


@pytest.mark.asyncio
async def test_call_tool_points_in_polygon_keeps_nearby_distance(stack):
    client, mcp = stack
    client.nearby = {
        "items": [
            {"feature": _feat("node/1", 18.08, 59.32, name="Inside Near"), "distance_m": 84.0},
            {"feature": _feat("node/2", 18.05, 59.32, name="Outside Near"), "distance_m": 210.0},
        ]
    }
    client.isochrone = {"status": "ok", "geometry": SQUARE, "distance_m": 1000.0, "duration_s": 714.0}
    nearby = await _call(mcp, "places_nearby", lat=59.316, lng=18.075, or_tags=["amenity=cafe"])
    iso = await _call(mcp, "routes_isochrone", lon=18.075, lat=59.316, max_distance_m=1000)
    inside = await _call(
        mcp, "points_in_polygon", points_id=nearby["collection_id"], polygon_id=iso["collection_id"]
    )
    assert inside["count"] == 1
    assert inside["items"][0]["name"] == "Inside Near"
    assert inside["items"][0]["distance_m"] == 84.0


@pytest.mark.asyncio
async def test_call_tool_nearest_within_chain(stack):
    client, mcp = stack
    client.search = _fc(_feat("node/r1", 18.0702, 59.316, name="Pelikan"))
    restaurants = await _call(
        mcp, "places_search", bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=restaurant"]
    )
    client.search = _fc(_feat("node/s1", 18.07, 59.316, name="Medborgarplatsen"))
    stations = await _call(
        mcp, "places_search", bbox="18.05,59.31,18.10,59.33", or_tags=["railway=station"]
    )
    pairs = await _call(
        mcp,
        "nearest_within",
        primary_id=restaurants["collection_id"],
        secondary_id=stations["collection_id"],
        max_distance_m=150,
    )
    assert pairs["count"] == 1
    assert pairs["items"][0]["name"] == "Pelikan"
    assert pairs["items"][0]["nearest_name"] == "Medborgarplatsen"
    assert pairs["items"][0]["lon"] == 18.0702
    assert pairs["items"][0]["nearest_lon"] == 18.07


@pytest.mark.asyncio
async def test_call_tool_nearest_within_keeps_every_primary(stack):
    client, mcp = stack
    client.search = _fc(*[_feat(f"node/r{i}", 18.0702, 59.316, name=f"R{i}") for i in range(25)])
    restaurants = await _call(
        mcp, "places_search", bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=restaurant"]
    )
    client.search = _fc(_feat("node/s1", 18.07, 59.316, name="Medborgarplatsen"))
    stations = await _call(
        mcp, "places_search", bbox="18.05,59.31,18.10,59.33", or_tags=["railway=station"]
    )
    pairs = await _call(
        mcp,
        "nearest_within",
        primary_id=restaurants["collection_id"],
        secondary_id=stations["collection_id"],
        max_distance_m=150,
    )
    assert pairs["count"] == 25
    assert len(pairs["items"]) == 25
    assert "items_truncated" not in pairs


@pytest.mark.asyncio
async def test_call_tool_nearest_within_flags_truncated_items(stack):
    client, mcp = stack
    client.search = _fc(*[_feat(f"node/r{i}", 18.0702, 59.316, name=f"R{i}") for i in range(45)])
    restaurants = await _call(
        mcp, "places_search", bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=restaurant"]
    )
    client.search = _fc(_feat("node/s1", 18.07, 59.316, name="Medborgarplatsen"))
    stations = await _call(
        mcp, "places_search", bbox="18.05,59.31,18.10,59.33", or_tags=["railway=station"]
    )
    pairs = await _call(
        mcp,
        "nearest_within",
        primary_id=restaurants["collection_id"],
        secondary_id=stations["collection_id"],
        max_distance_m=150,
    )
    assert pairs["count"] == 45
    assert len(pairs["items"]) == SUMMARY_ITEM_CAP
    assert pairs["items_truncated"] is True


@pytest.mark.asyncio
async def test_call_tool_nearest_within_rejects_over_comparison_cap(stack, monkeypatch):
    monkeypatch.setattr("osmfeatures._mcp_session.MAX_COMPARISONS", 20)
    client, mcp = stack
    client.search = _fc(*[_feat(f"node/r{i}", 18.0702, 59.316, name=f"R{i}") for i in range(5)])
    restaurants = await _call(
        mcp, "places_search", bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=restaurant"]
    )
    client.search = _fc(*[_feat(f"node/s{i}", 18.07, 59.316, name=f"S{i}") for i in range(5)])
    stations = await _call(
        mcp, "places_search", bbox="18.05,59.31,18.10,59.33", or_tags=["railway=station"]
    )
    with pytest.raises(ToolError, match=r"5×5 comparisons"):
        await mcp.call_tool(
            "nearest_within",
            {
                "primary_id": restaurants["collection_id"],
                "secondary_id": stations["collection_id"],
                "max_distance_m": 150,
            },
        )


@pytest.mark.asyncio
async def test_call_tool_routes_containment_and_preview(stack):
    client, mcp = stack
    client.isochrone = {
        "status": "ok",
        "geometry": SQUARE,
        "distance_m": 1680.0,
        "duration_s": 1200.0,
    }
    client.path = {
        "status": "ok",
        "distance_m": 640.0,
        "duration_s": 457.0,
        "ordered_stops": [{"lon": 18.07, "lat": 59.316}, {"lon": 18.08, "lat": 59.318}],
        "geometry": {"type": "LineString", "coordinates": [[18.07, 59.316], [18.08, 59.318]]},
    }
    client.optimized = {
        "status": "ok",
        "distance_m": 1800.0,
        "duration_s": 1286.0,
        "ordered_stops": [
            {"lon": 18.075, "lat": 59.316},
            {"lon": 18.08, "lat": 59.32},
            {"lon": 18.07, "lat": 59.318},
        ],
        "geometry": {"type": "LineString", "coordinates": [[18.075, 59.316], [18.08, 59.32]]},
    }
    client.search = _fc(
        _feat("node/1", 18.08, 59.32, name="Inside Cafe"),
        _feat("node/2", 18.05, 59.32, name="Outside Cafe"),
    )
    iso = await _call(mcp, "routes_isochrone", lon=18.075, lat=59.316, duration_s=1200, travel_mode="WALK")
    assert iso["geometry_type"] == "Polygon"
    assert client.calls[-1][1]["origin"] == {"lon": 18.075, "lat": 59.316}
    near = await _call(mcp, "point_in_polygon", collection_id=iso["collection_id"], lon=18.08, lat=59.32)
    far = await _call(mcp, "point_in_polygon", collection_id=iso["collection_id"], lon=18.05, lat=59.35)
    assert near["inside"] is True
    assert far["inside"] is False
    cafes = await _call(mcp, "places_search", lat=59.316, lng=18.075, radius=1500, or_tags=["amenity=cafe"])
    inside = await _call(
        mcp, "points_in_polygon", points_id=cafes["collection_id"], polygon_id=iso["collection_id"]
    )
    assert inside["count"] == 1
    assert inside["items"][0]["name"] == "Inside Cafe"
    path = await _call(
        mcp,
        "routes_path",
        stops=[{"lon": 18.07, "lat": 59.316}, {"lon": 18.08, "lat": 59.318}],
    )
    assert path["ordered_stops"] == [
        {"lon": 18.07, "lat": 59.316},
        {"lon": 18.08, "lat": 59.318},
    ]
    opt = await _call(
        mcp,
        "routes_optimized_path",
        start={"lon": 18.075, "lat": 59.316},
        stops=[{"lon": 18.08, "lat": 59.32}, {"lon": 18.07, "lat": 59.318}],
        loop=True,
    )
    assert opt["ordered_stops"][0]["lon"] == 18.075
    assert opt["ordered_stops"][1]["lat"] == 59.32
    preview = await _call(mcp, "preview_map", collection_id=iso["collection_id"], open_browser=False)
    assert preview["preview_url"].endswith("/preview/" + iso["collection_id"])
    assert "coordinates" not in preview


@pytest.mark.asyncio
async def test_call_tool_query_query_all_and_export(stack, tmp_path, monkeypatch):
    client, mcp = stack
    monkeypatch.setattr("osmfeatures._mcp_session.tempfile.gettempdir", lambda: str(tmp_path))
    client.query_result = _fc(_feat("way/1", 18.07, 59.32, name="Tantolunden"))
    client.query_all_result = _fc(
        _feat("way/1", 18.07, 59.32, name="Park A"),
        _feat("way/2", 18.08, 59.32, name="Park B"),
    )
    page = await _call(
        mcp, "query", bbox="18.05,59.31,18.10,59.33", tags=["leisure=park"], way_shape="polygon"
    )
    assert page["count"] == 1
    assert client.calls[-1][1]["centroid"] is True
    drained = await _call(
        mcp, "query_all", bbox="18.05,59.31,18.12,59.36", tags=["leisure=park"], way_shape="polygon"
    )
    assert drained["count"] == 2
    assert client.calls[-1][1]["max_features"] == QUERY_ALL_MAX_FEATURES
    assert client.calls[-1][1]["bbox_tiles"] == 2
    assert client.calls[-1][1]["centroid"] is True
    exported = await _call(mcp, "export_geojson", collection_id=page["collection_id"])
    assert exported["feature_count"] == 1
    assert exported["path"].endswith(f"maplark-{page['collection_id']}.geojson")
    assert (tmp_path / f"maplark-{page['collection_id']}.geojson").is_file()


@pytest.mark.asyncio
async def test_call_tool_query_all_rejects_null_max_features(stack):
    _client, mcp = stack
    with pytest.raises(ToolError, match="valid integer"):
        await mcp.call_tool("query_all", {"bbox": "18.05,59.31,18.12,59.36", "max_features": None})
