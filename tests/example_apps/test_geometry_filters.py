"""
App ideas:
  - show only large buildings when zoomed out
  - show only long roads for corridor-scale maps

These tests exercise live API filtering for:
  - ``zoom`` + ``min_area_m2`` on polygon buildings
  - ``min_length_m`` on line highways
"""

from osmfeatures import OSMFeature, OSMFeatureCollection, OSMFeaturesClient

from tests.example_apps.conftest import CENTRAL_EAST_BBOX

GAMLA_STAN_CORE_BBOX = "18.070,59.323,18.075,59.327"

def _feature_ids(data: OSMFeatureCollection) -> set[str]:
    return {f["id"] for f in data["features"]}


def test_zoomed_out_large_buildings_only(client: OSMFeaturesClient):
    baseline = client.query(
        bbox=GAMLA_STAN_CORE_BBOX,
        type="way,relation",
        way_shape="polygon",
        tags="building",
        zoom=11,
        max_features=300,
    )
    assert isinstance(baseline, OSMFeatureCollection)
    assert len(baseline["features"]) > 0, "Expected buildings in central Stockholm"

    large = client.query(
        bbox=GAMLA_STAN_CORE_BBOX,
        type="way,relation",
        way_shape="polygon",
        tags="building",
        zoom=11,
        min_area_m2=3000,
        max_features=300,
    )
    assert isinstance(large, OSMFeatureCollection)
    assert len(large["features"]) > 0, "Expected at least one large building at zoomed-out level"
    assert len(large["features"]) < len(baseline["features"]), "Area filter should exclude smaller buildings"

    large_ids = _feature_ids(large)
    baseline_ids = _feature_ids(baseline)
    assert large_ids <= baseline_ids, "Filtered large-building set should be subset of baseline set"

    medium = client.query(
        bbox=GAMLA_STAN_CORE_BBOX,
        type="way,relation",
        way_shape="polygon",
        tags="building",
        zoom=11,
        min_area_m2=500,
        max_area_m2=2999,
        max_features=300,
    )
    assert isinstance(medium, OSMFeatureCollection)
    assert len(medium["features"]) > 0, "Expected medium buildings in Gamla Stan core"
    assert _feature_ids(medium).isdisjoint(large_ids), "Large and medium building buckets should not overlap"

    for f in large["features"]:
        assert isinstance(f, OSMFeature)
        assert f.osm_type in ("way", "relation")
        assert f.tags.get("building") is not None
        assert f["geometry"]["type"] in ("Polygon", "MultiPolygon")

    print(f"\n[geometry filters] buildings baseline={len(baseline['features'])} large={len(large['features'])}")


def test_only_long_roads(client: OSMFeaturesClient):
    baseline = client.query_all(
        bbox=CENTRAL_EAST_BBOX,
        type="way",
        way_shape="line",
        tags="highway",
        clip_geometry=False,
        max_features=300,
    )
    assert isinstance(baseline, OSMFeatureCollection)
    assert len(baseline["features"]) > 0, "Expected roads in central Stockholm"

    long_roads = client.query_all(
        bbox=CENTRAL_EAST_BBOX,
        type="way",
        way_shape="line",
        tags="highway",
        min_length_m=1200,
        clip_geometry=False,
        max_features=300,
    )
    assert isinstance(long_roads, OSMFeatureCollection)
    assert len(long_roads["features"]) > 0, "Expected at least one long road"
    assert len(long_roads["features"]) < len(baseline["features"]), "Length filter should exclude shorter roads"

    long_ids = _feature_ids(long_roads)
    medium_roads = client.query_all(
        bbox=CENTRAL_EAST_BBOX,
        type="way",
        way_shape="line",
        tags="highway",
        min_length_m=200,
        max_length_m=1199,
        clip_geometry=False,
        max_features=300,
    )
    assert isinstance(medium_roads, OSMFeatureCollection)
    assert len(medium_roads["features"]) > 0, "Expected medium roads in central Stockholm"
    assert _feature_ids(medium_roads).isdisjoint(long_ids), "Long and medium road buckets should not overlap"

    for f in long_roads["features"]:
        assert isinstance(f, OSMFeature)
        assert f.osm_type == "way"
        assert f.tags.get("highway") is not None
        assert f["geometry"]["type"] in ("LineString", "MultiLineString")

    print(f"\n[geometry filters] roads baseline={len(baseline['features'])} long={len(long_roads['features'])}")
