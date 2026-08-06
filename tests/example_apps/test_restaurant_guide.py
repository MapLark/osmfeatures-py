"""
App idea: restaurant discovery card list with name, cuisine, and a map pin.

Gamla Stan is Stockholm's old town and a popular tourist dining destination.
Restaurants can be mapped as nodes (a single point) or as building footprints
(ways); the centroid is used as the display coordinate for polygons.

API call: ``type=node,way&tags=amenity=restaurant``
"""

from collections import defaultdict

from osmfeatures import OSMFeature, OSMFeatureCollection, OSMFeaturesClient

GAMLA_STAN_BBOX = "18.063,59.322,18.082,59.332"


def test_restaurant_guide(client: OSMFeaturesClient):
    data = client.query(
        bbox=GAMLA_STAN_BBOX,
        type="node,way",
        tags="amenity=restaurant",
        limit=50,
    )
    assert isinstance(data, OSMFeatureCollection)
    assert len(data["features"]) > 0, "Expected restaurants in Gamla Stan"

    named = []
    cuisines: dict[str, int] = defaultdict(int)

    for f in data["features"]:
        assert isinstance(f, OSMFeature)
        tags = f.tags
        assert tags.get("amenity") == "restaurant"

        # Derive a display coordinate: use the centroid for polygon restaurants,
        # or the point geometry directly for node restaurants.
        if f["geometry"]["type"] == "Point":
            display_coord = f["geometry"]["coordinates"]
        else:
            assert f.centroid is not None
            display_coord = f.centroid["coordinates"]

        assert len(display_coord) == 2

        if tags.get("name"):
            named.append(tags["name"])
        if tags.get("cuisine"):
            cuisines[tags["cuisine"]] += 1

    assert len(named) > 0, "Expected at least some named restaurants"

    print(f"\n[restaurant guide] {len(data['features'])} restaurants, {len(named)} named")
    print(f"  Sample names: {named[:5]}")
    print(f"  Cuisines: {dict(sorted(cuisines.items(), key=lambda x: -x[1])[:5])}")
