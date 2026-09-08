"""Local geometry helpers. No HTTP. The planner must not invent containment."""

from __future__ import annotations

from typing import Any


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    """Even-odd point-in-polygon test against a single ring."""
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > lat) != (y2 > lat):
            x_at_lat = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < x_at_lat:
                inside = not inside
    return inside


def _point_in_polygon_rings(lon: float, lat: float, rings: list[Any]) -> bool:
    """Even-odd over every ring of one polygon (exterior plus holes)."""
    inside = False
    for ring in rings:
        if ring and _point_in_ring(lon, lat, ring):
            inside = not inside
    return inside


def point_in_geometry(lon: float, lat: float, geom: dict[str, Any]) -> bool:
    """Point-in-polygon for GeoJSON Polygon/MultiPolygon, including holes.

    Each polygon is even-odd over all of its rings (exterior plus holes), so a
    point in a hole is outside. A MultiPolygon contains the point if any part
    does. Other geometry types return False.
    """
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    if gtype == "Polygon":
        return _point_in_polygon_rings(lon, lat, coords)
    if gtype == "MultiPolygon":
        return any(_point_in_polygon_rings(lon, lat, poly) for poly in coords if poly)
    return False
