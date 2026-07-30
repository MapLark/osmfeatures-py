"""
App idea: interactive park browser — click a park to see its boundary, name,
and a rough area estimate in hectares.

Parks in OSM are polygon features (ways or multipolygon relations).  The
centroid is used to place a label or map pin at the centre of each park.

API call: ``type=way,relation&shape=polygon&tags=leisure=park``
"""

from haversine import Unit, haversine

from osmgeojson import OSMFeature, OSMFeatureCollection, OSMGeoJSONClient

CENTRAL_EAST_BBOX = "18.020,59.310,18.180,59.365"


def _bbox_area_ha(coords_list: list) -> float:
    """Rough bounding-box area estimate in hectares for a polygon ring."""
    if not coords_list:
        return 0.0
    lons = [c[0] for c in coords_list]
    lats = [c[1] for c in coords_list]
    width_m = haversine((min(lats), min(lons)), (min(lats), max(lons)), unit=Unit.METERS)
    height_m = haversine((min(lats), min(lons)), (max(lats), min(lons)), unit=Unit.METERS)
    return (width_m * height_m) / 10_000


def test_park_explorer(client: OSMGeoJSONClient):
    data = client.query_all(
        bbox=CENTRAL_EAST_BBOX,
        type="way,relation",
        shape="polygon",
        tags="leisure=park",
        max_features=50,
        centroid=True,
    )
    assert isinstance(data, OSMFeatureCollection)
    assert len(data["features"]) > 0, "Expected park polygons in central Stockholm"

    for f in data["features"]:
        assert isinstance(f, OSMFeature)
        assert f.osm_type in ("way", "relation")
        assert f.tags.get("leisure") == "park"
        assert f.centroid is not None

        geom = f["geometry"]
        assert geom["type"] in ("Polygon", "MultiPolygon"), (
            f"Park {f['id']} has unexpected geometry type {geom['type']}"
        )

        outer_ring = (
            geom["coordinates"][0][0]
            if geom["type"] == "MultiPolygon"
            else geom["coordinates"][0]
        )
        area_ha = _bbox_area_ha(outer_ring)
        assert area_ha > 0, f"Park {f['id']} reported zero bounding-box area"

    named_parks = [f.tags.get("name") for f in data["features"] if f.tags.get("name")]
    assert len(named_parks) > 0, "Expected at least one named park"

    print(f"\n[park explorer] {len(data['features'])} parks, {len(named_parks)} named")
    print(f"  Sample park names: {named_parks[:5]}")
