"""Local nearest-neighbor join over two GeoJSON feature sets.

No HTTP. The planner picks tags/bbox; this function is the deterministic metre
join (search + search + nearest_within).
"""

from __future__ import annotations

import math
from typing import Any

# Mean Earth radius. ponytail: sphere, not WGS84 ellipsoid; swap if you need
# centimetre-grade distances.
_EARTH_RADIUS_M = 6_371_000.0


def _haversine_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(min(a, 1.0)))


def _features(src: Any) -> list[Any]:
    if isinstance(src, list):
        return src
    if isinstance(src, dict) and "features" in src:
        return list(src["features"])
    feats = getattr(src, "features", None)
    if feats is not None:
        return list(feats)
    raise TypeError("expected a FeatureCollection or list of Features")


def _lon_lat(feat: Any) -> tuple[float, float]:
    if not isinstance(feat, dict):
        raise ValueError("feature must be a GeoJSON Feature dict")
    geom = feat.get("geometry")
    if isinstance(geom, dict) and geom.get("type") == "Point":
        coords = geom.get("coordinates") or []
        if len(coords) >= 2:
            return float(coords[0]), float(coords[1])
    props = feat.get("properties") or {}
    centroid = props.get("centroid") if isinstance(props, dict) else None
    if isinstance(centroid, dict) and centroid.get("type") == "Point":
        coords = centroid.get("coordinates") or []
        if len(coords) >= 2:
            return float(coords[0]), float(coords[1])
    raise ValueError("feature has no Point geometry or properties.centroid")


def nearest_within(
    primary: Any,
    secondary: Any,
    max_distance_m: float,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Nearest secondary for each primary, within ``max_distance_m``.

    Accepts a FeatureCollection dict or a list of Features. Point comes from
    ``geometry`` when it is a Point, else ``properties.centroid``. A feature
    with neither raises ``ValueError``.

    Returns pairs sorted by ``distance_m``, sliced to ``limit``::

        {"feature": primary, "distance_m": metres, "nearest": secondary}

    O(n×m) haversine. Fine at search ``limit`` (default 100).
    """
    primaries = _features(primary)
    secondaries = _features(secondary)
    if not primaries or not secondaries:
        return []

    sec_pts = [(s, _lon_lat(s)) for s in secondaries]
    pairs: list[dict[str, Any]] = []
    for p in primaries:
        plon, plat = _lon_lat(p)
        best: tuple[float, Any] | None = None
        for s, (slon, slat) in sec_pts:
            d = _haversine_m(plon, plat, slon, slat)
            if best is None or d < best[0]:
                best = (d, s)
        if best is not None and best[0] <= max_distance_m:
            pairs.append({"feature": p, "distance_m": best[0], "nearest": best[1]})
    pairs.sort(key=lambda item: item["distance_m"])
    return pairs[:limit]
