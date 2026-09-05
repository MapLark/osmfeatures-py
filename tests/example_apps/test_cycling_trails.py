"""
App idea: adventure cycling layer showing unpaved trails where cycling is
explicitly permitted — ideal for MTB or gravel bike route planning.

OSM tags used:
  - ``highway=path`` + ``bicycle=yes`` or ``bicycle=designated``
  - ``highway=track`` + ``bicycle=yes`` or ``bicycle=designated``

API calls use ``or_tags`` to match either bicycle permission value.
"""

from osmfeatures import OSMFeature, OSMFeatureCollection, OSMFeaturesClient

DJURGARDEN_BBOX = "18.090,59.320,18.170,59.345"


def test_cycling_trails(client: OSMFeaturesClient):
    # Paths with cycling explicitly permitted (bicycle=yes or bicycle=designated)
    paths_cycling = client.query(
        bbox=DJURGARDEN_BBOX,
        type="way",
        way_shape="line",
        tags="highway=path",
        or_tags=["bicycle=yes", "bicycle=designated"],
        limit=50,
        disable_budget_warning=True,
    )
    # Dedicated cycling tracks (unpaved forestry-style)
    tracks = client.query(
        bbox=DJURGARDEN_BBOX,
        type="way",
        way_shape="line",
        tags="highway=track",
        or_tags=["bicycle=yes", "bicycle=designated"],
        limit=50,
        disable_budget_warning=True,
    )

    assert isinstance(paths_cycling, OSMFeatureCollection)
    assert isinstance(tracks, OSMFeatureCollection)

    all_trails = paths_cycling["features"] + tracks["features"]
    assert len(all_trails) > 0, "Expected cycling trail features in Djurgården"

    for f in all_trails:
        assert isinstance(f, OSMFeature)
        assert f.osm_type == "way"
        assert f["geometry"]["type"] == "LineString"
        tags = f.tags
        hw = tags.get("highway")
        assert hw in ("path", "track", "cycleway"), f"Unexpected highway tag: {hw}"
        # Every returned trail must carry an explicit cycling-permitted tag
        assert tags.get("bicycle") in ("yes", "designated"), (
            f"Trail {f['id']} (highway={hw}) lacks explicit bicycle access: "
            f"bicycle={tags.get('bicycle')!r}"
        )

    print(f"\n[cycling trails] {len(all_trails)} trail segments in Djurgården")
