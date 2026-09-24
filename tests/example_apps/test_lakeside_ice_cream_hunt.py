"""
App idea: recommend ice cream shops that are close to a lake or waterfront —
a classic summer Stockholm activity.

Two-step query:
  1. Fetch water bodies (``natural=water``) with polygon geometry
  2. Fetch ice cream shops (``amenity=ice_cream``) as nodes
  3. Compute haversine distance from each shop to the nearest point on each
     water body's polygon *boundary* (measuring to edge segments, not just
     vertices) and collect the closest matches.

Measuring to segments rather than vertices means a shop beside the middle of
a long edge reports the true perpendicular distance instead of the distance to
the nearest endpoint, keeping rankings accurate for water bodies with sparse
vertex spacing.

API calls:
  - ``type=way,relation&way_shape=polygon&tags=natural=water``
  - ``type=node,way&tags=amenity=ice_cream``
"""

import pytest
from haversine import Unit, haversine

from osmfeatures import OSMFeatureCollection, OSMFeaturesClient

CENTRAL_EAST_BBOX = "18.020,59.310,18.180,59.365"


def _polygon_edges(geom: dict) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Return all consecutive edge pairs ``((lon, lat), (lon, lat))`` for a polygon."""

    def ring_edges(ring):
        coords = [(c[0], c[1]) for c in ring]
        return [(coords[i], coords[i + 1]) for i in range(len(coords) - 1)]

    if geom["type"] == "Polygon":
        return [e for ring in geom["coordinates"] for e in ring_edges(ring)]
    if geom["type"] == "MultiPolygon":
        return [
            e
            for polygon in geom["coordinates"]
            for ring in polygon
            for e in ring_edges(ring)
        ]
    return []


def _dist_point_to_segment(
    slat: float,
    slon: float,
    lon1: float,
    lat1: float,
    lon2: float,
    lat2: float,
) -> float:
    """Minimum haversine distance from ``(slat, slon)`` to the segment.

    The closest point on the segment is located by linear interpolation in
    (lon, lat) space — valid for the short edge lengths typical in OSM water
    polygons — and clamped to [0, 1] so only points *on* the edge are
    considered.
    """
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    seg_sq = dlon * dlon + dlat * dlat
    if seg_sq == 0.0:
        return haversine((slat, slon), (lat1, lon1), unit=Unit.METERS)
    t = max(0.0, min(1.0, ((slon - lon1) * dlon + (slat - lat1) * dlat) / seg_sq))
    return haversine((slat, slon), (lat1 + t * dlat, lon1 + t * dlon), unit=Unit.METERS)


def test_lakeside_ice_cream_hunt(client: OSMFeaturesClient):
    # Step 1: water bodies
    water = client.query_all(
        bbox=CENTRAL_EAST_BBOX,
        type="way,relation",
        way_shape="polygon",
        tags="natural=water",
        max_features=50,
    )
    assert isinstance(water, OSMFeatureCollection)

    # Step 2: ice cream shops (nodes + possible small area cafés; exclude relations)
    ice_cream = client.query_all(
        bbox=CENTRAL_EAST_BBOX,
        type="node,way",
        tags="amenity=ice_cream",
        max_features=50,
    )
    assert isinstance(ice_cream, OSMFeatureCollection)

    if not water["features"] or not ice_cream["features"]:
        pytest.skip("Not enough data to run lakeside ice cream hunt in this bbox")

    # Build list of (boundary_edges, name) for each water body.
    # Polygon edges are used instead of the centroid so that distance is
    # measured to the nearest point on the shoreline rather than an inland
    # point.  Using segments (not just vertices) avoids overestimating the
    # distance for shops located near the middle of a long edge.
    water_bodies: list[tuple[list, str]] = []
    for f in water["features"]:
        name = f.tags.get("name", f["id"])
        geom = f["geometry"]
        if geom["type"] == "Point":
            lon, lat = geom["coordinates"]
            water_bodies.append(([((lon, lat), (lon, lat))], name))
        else:
            edges = _polygon_edges(geom)
            if not edges:
                c = f.centroid["coordinates"] if f.centroid else None
                if c:
                    water_bodies.append(([((c[0], c[1]), (c[0], c[1]))], name))
            else:
                water_bodies.append((edges, name))

    assert water_bodies, "No usable water geometries found"

    # For each ice cream shop find the nearest water body by shoreline distance
    results: list[dict] = []
    for shop in ice_cream["features"]:
        if shop["geometry"]["type"] == "Point":
            slon, slat = shop["geometry"]["coordinates"]
        elif shop.centroid:
            slon, slat = shop.centroid["coordinates"]
        else:
            continue

        best_dist = float("inf")
        best_name = ""
        for edges, wname in water_bodies:
            d = min(
                _dist_point_to_segment(slat, slon, lon1, lat1, lon2, lat2)
                for (lon1, lat1), (lon2, lat2) in edges
            )
            if d < best_dist:
                best_dist = d
                best_name = wname

        results.append(
            {
                "shop": shop.tags.get("name", shop["id"]),
                "nearest_water": best_name,
                "distance_m": round(best_dist),
            }
        )

    assert results, "No ice cream shops could be matched to water bodies"

    results.sort(key=lambda r: r["distance_m"])

    print(f"\n[lakeside ice cream] Top 5 closest ice cream shops to water:")
    for r in results[:5]:
        print(f"  {r['shop']} -> {r['nearest_water']} ({r['distance_m']} m)")
