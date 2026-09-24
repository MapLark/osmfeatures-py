"""Local nearest-neighbor and distance-band joins over two GeoJSON feature sets.

No HTTP. The planner picks tags/bbox; these functions are the deterministic metre
join (search + search + nearest_within / pairs_within).
"""

from __future__ import annotations

import math
from typing import Any

# Mean Earth radius. ponytail: sphere, not WGS84 ellipsoid; swap if you need
# centimetre-grade distances.
_EARTH_RADIUS_M = 6_371_000.0
# O(n×m) haversine. 500k is ~1000×500 or 10_000×50; search-sized joins fit.
MAX_COMPARISONS = 500_000


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
    limit: int | None = 20,
    max_comparisons: int | None = MAX_COMPARISONS,
) -> list[dict[str, Any]]:
    """Nearest secondary for each primary, within ``max_distance_m``.

    Accepts a FeatureCollection dict or a list of Features. Point comes from
    ``geometry`` when it is a Point, else ``properties.centroid``. A feature
    with neither raises ``ValueError``.

    Returns pairs sorted by ``distance_m``::

        {"feature": primary, "distance_m": metres, "nearest": secondary}

    ``limit`` keeps the closest pairs (SDK default 20). Pass ``None`` for every
    primary that has a match. O(n×m) haversine. Fine at search ``limit``
    (default 100). Joins whose ``len(primary) * len(secondary)`` exceeds
    ``max_comparisons`` (default 500_000) raise ``ValueError``. Pass
    ``max_comparisons=None`` for no cap.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be a positive int")
    if max_comparisons is not None and max_comparisons < 1:
        raise ValueError("max_comparisons must be a positive int")
    primaries = _features(primary)
    secondaries = _features(secondary)
    if not primaries or not secondaries:
        return []

    n, m = len(primaries), len(secondaries)
    if max_comparisons is not None and n * m > max_comparisons:
        raise ValueError(
            f"nearest_within join is {n}×{m} comparisons "
            f"(cap {max_comparisons}). Shrink the collections "
            "(places_search/nearby limit, not query)."
        )

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


def pairs_within(
    primary: Any,
    secondary: Any,
    max_distance_m: float,
    min_distance_m: float = 0,
    limit: int | None = 20,
    max_comparisons: int | None = MAX_COMPARISONS,
) -> list[dict[str, Any]]:
    """All primary/secondary pairs with ``min_distance_m <= d <= max_distance_m``.

    Same point/centroid rules and comparison cap as :func:`nearest_within`.
    Sorted by ``distance_m``, then sliced to ``limit`` (SDK default 20; pass
    ``None`` for every pair). Same-collection calls (``primary is secondary``)
    emit each unordered pair once, skip a feature paired with itself, and
    count ``n(n-1)/2`` toward the comparison cap.
    """
    if limit is not None and limit < 1:
        raise ValueError("limit must be a positive int")
    if max_comparisons is not None and max_comparisons < 1:
        raise ValueError("max_comparisons must be a positive int")
    if min_distance_m < 0:
        raise ValueError("min_distance_m must be greater than or equal to 0")
    if min_distance_m > max_distance_m:
        raise ValueError("min_distance_m must be less than or equal to max_distance_m")

    same_collection = primary is secondary
    primaries = _features(primary)
    secondaries = primaries if same_collection else _features(secondary)
    if not primaries or not secondaries:
        return []

    n, m = len(primaries), len(secondaries)
    comparisons = n * (n - 1) // 2 if same_collection else n * m
    if max_comparisons is not None and comparisons > max_comparisons:
        shape = f"{n} self-join" if same_collection else f"{n}×{m}"
        raise ValueError(
            f"pairs_within join is {shape} comparisons "
            f"(cap {max_comparisons}). Shrink the collections "
            "(places_search/nearby limit, not query)."
        )

    pairs: list[dict[str, Any]] = []
    if same_collection:
        pts = [(p, _lon_lat(p)) for p in primaries]
        for i, (p, (plon, plat)) in enumerate(pts):
            for s, (slon, slat) in pts[i + 1 :]:
                d = _haversine_m(plon, plat, slon, slat)
                if min_distance_m <= d <= max_distance_m:
                    pairs.append({"feature": p, "distance_m": d, "nearest": s})
    else:
        sec_pts = [(s, _lon_lat(s)) for s in secondaries]
        for p in primaries:
            plon, plat = _lon_lat(p)
            for s, (slon, slat) in sec_pts:
                d = _haversine_m(plon, plat, slon, slat)
                if min_distance_m <= d <= max_distance_m:
                    pairs.append({"feature": p, "distance_m": d, "nearest": s})
    pairs.sort(key=lambda item: item["distance_m"])
    return pairs[:limit]
