"""
App idea: overlay dedicated cycling infrastructure on a city basemap.

Combines cycleways, bike lanes (``cycleway=lane`` on roads), and shared
bus+bike lanes common in Stockholm.

On-road bike lanes in OSM use four key forms:
  - ``cycleway=*``        — undirected (applies to both sides of the road)
  - ``cycleway:left=*``   — left-side lane only
  - ``cycleway:right=*``  — right-side lane only
  - ``cycleway:both=*``   — explicit both-sides shorthand

All four keys are OR-combined so no lane is silently omitted.  Only
positive infrastructure values are matched; ``cycleway=no`` and
``cycleway=separate`` explicitly mean no lane is present.

API calls:
  - ``type=way&way_shape=line&tags=highway=cycleway``
  - ``type=way&way_shape=line&or_tags=[cycleway=lane, cycleway:left=lane, ...]``
"""

from haversine import Unit, haversine

from osmfeatures import OSMFeatureCollection, OSMFeaturesClient

SODERMALM_BBOX = "18.045,59.308,18.100,59.330"


def _linestring_length_m(coords: list) -> float:
    total = 0.0
    for i in range(len(coords) - 1):
        total += haversine(
            (coords[i][1], coords[i][0]),
            (coords[i + 1][1], coords[i + 1][0]),
            unit=Unit.METERS,
        )
    return total


def test_city_cycling_infrastructure(client: OSMFeaturesClient):
    # Dedicated cycleways
    cycleways = client.query(
        bbox=SODERMALM_BBOX,
        type="way",
        way_shape="line",
        tags="highway=cycleway",
        max_features=100,
    )
    # Streets with a marked cycle lane - undirected and directional key forms
    _lane_values = ("lane", "track", "share_busway", "shared_lane")
    _lane_keys = ("cycleway", "cycleway:left", "cycleway:right", "cycleway:both")
    bike_lanes = client.query(
        bbox=SODERMALM_BBOX,
        type="way",
        way_shape="line",
        or_tags=[f"{key}={val}" for key in _lane_keys for val in _lane_values],
        max_features=100,
    )

    assert isinstance(cycleways, OSMFeatureCollection)
    assert isinstance(bike_lanes, OSMFeatureCollection)

    assert len(cycleways["features"]) > 0, "Expected cycleways in Södermalm"

    total_segments = len(cycleways["features"]) + len(bike_lanes["features"])
    assert total_segments > 0, "Expected cycling infrastructure segments in Södermalm"

    # Measure total network length across all cycling infrastructure
    all_cycling_features = cycleways["features"] + bike_lanes["features"]
    total_length_km = sum(
        _linestring_length_m(f["geometry"]["coordinates"]) / 1000
        for f in all_cycling_features
        if f["geometry"]["type"] == "LineString"
    )

    print(
        f"\n[city cycling] {len(cycleways['features'])} cycleways, "
        f"{len(bike_lanes['features'])} streets with bike lane - "
        f"{total_length_km:.1f} km of combined cycling infrastructure"
    )
