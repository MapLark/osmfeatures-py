"""
App idea: show benches on a park map so visitors know where to rest.

Djurgården is Stockholm's main recreational island; it has many benches.

API call: ``type=node&tags=amenity=bench``
"""

from osmfeatures import OSMFeature, OSMFeatureCollection, OSMFeaturesClient

DJURGARDEN_BBOX = "18.090,59.320,18.170,59.345"


def test_park_bench_finder(client: OSMFeaturesClient):
    data = client.query(bbox=DJURGARDEN_BBOX, type="node", tags="amenity=bench", max_features=200)
    assert isinstance(data, OSMFeatureCollection)
    assert len(data["features"]) > 0, "Expected bench nodes in Djurgården"

    # All features must be Point nodes with the correct tag
    for f in data["features"]:
        assert isinstance(f, OSMFeature)
        assert f.osm_type == "node"
        assert f["geometry"]["type"] == "Point"
        assert f.tags.get("amenity") == "bench"
        # Nodes are already points - no centroid needed
        assert f.centroid is None

    # Extract lon/lat to demonstrate placing map markers
    bench_coords = [f["geometry"]["coordinates"] for f in data["features"]]
    assert all(len(c) == 2 for c in bench_coords)

    lons = [c[0] for c in bench_coords]
    lats = [c[1] for c in bench_coords]
    # Sanity-check all coordinates fall inside Stockholm
    assert all(17.5 < lon < 18.5 for lon in lons)
    assert all(59.0 < lat < 60.0 for lat in lats)

    print(f"\n[bench finder] {len(bench_coords)} benches found in Djurgården")
