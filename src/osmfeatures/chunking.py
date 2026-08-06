"""Bounding-box utilities: tiling bboxes and merging results."""

from __future__ import annotations

import math
from typing import Any


# Degrees-per-metre at the equator (approximate, sufficient for splitting)
_DEG_PER_METRE_LAT = 1.0 / 111_320.0


def parse_bbox(bbox: str) -> tuple[float, float, float, float]:
    """Parse ``"min_lon,min_lat,max_lon,max_lat"`` -> ``(min_lon, min_lat, max_lon, max_lat)``."""
    parts = bbox.split(",")
    if len(parts) != 4:
        raise ValueError(
            f"Invalid bbox format {bbox!r}. Expected 'min_lon,min_lat,max_lon,max_lat'."
        )
    min_lon, min_lat, max_lon, max_lat = (float(p) for p in parts)
    return min_lon, min_lat, max_lon, max_lat


def bbox_area_deg2(bbox: str) -> float:
    """Return the area of *bbox* in square degrees."""
    min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
    return (max_lon - min_lon) * (max_lat - min_lat)


def _is_power_of_two(n: int) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and n >= 1 and (n & (n - 1)) == 0


def split_bbox_tiles(bbox: str, tile_count: int) -> list[str]:
    """Split *bbox* into *tile_count* tiles by repeated longest-side bisection.

    *tile_count* must be a power of 2 (``1, 2, 4, 8, …``). Returns a list of
    ``"min_lon,min_lat,max_lon,max_lat"`` strings. Shared edges are intentional;
    callers should dedupe features across tiles.
    """
    if not _is_power_of_two(tile_count):
        raise ValueError(
            f"tile_count must be a power of 2 (1, 2, 4, 8, …); got {tile_count!r}."
        )

    min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox)
    tiles: list[tuple[float, float, float, float]] = [
        (min_lon, min_lat, max_lon, max_lat)
    ]

    while len(tiles) < tile_count:
        next_tiles: list[tuple[float, float, float, float]] = []
        for t_min_lon, t_min_lat, t_max_lon, t_max_lat in tiles:
            lon_span = t_max_lon - t_min_lon
            lat_span = t_max_lat - t_min_lat
            if lon_span >= lat_span:
                mid_lon = t_min_lon + lon_span / 2
                next_tiles.append((t_min_lon, t_min_lat, mid_lon, t_max_lat))
                next_tiles.append((mid_lon, t_min_lat, t_max_lon, t_max_lat))
            else:
                mid_lat = t_min_lat + lat_span / 2
                next_tiles.append((t_min_lon, t_min_lat, t_max_lon, mid_lat))
                next_tiles.append((t_min_lon, mid_lat, t_max_lon, t_max_lat))
        tiles = next_tiles

    return [f"{a},{b},{c},{d}" for a, b, c, d in tiles]


def merge_features(feature_lists: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Merge multiple feature lists, deduplicating by ``feature["id"]``."""
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for lst in feature_lists:
        for feat in lst:
            fid = feat.get("id", "")
            if fid not in seen:
                seen.add(fid)
                merged.append(feat)
    return merged


def around_to_bbox(lon: float, lat: float, radius_m: float) -> str:
    """Convert a radius search to a bounding-box string (approximation).

    Useful when you want to use bbox-tiling logic on an ``around`` query
    area.  The result is a square bbox that fully contains the circle.
    """
    delta_lat = radius_m * _DEG_PER_METRE_LAT
    delta_lon = radius_m * _DEG_PER_METRE_LAT / max(math.cos(math.radians(lat)), 1e-9)
    return f"{lon - delta_lon},{lat - delta_lat},{lon + delta_lon},{lat + delta_lat}"


def shapely_to_bbox(geometry: Any) -> str:
    """Convert a Shapely geometry to a ``"min_lon,min_lat,max_lon,max_lat"`` bbox string.

    Requires the ``[geo]`` extra (``pip install osmfeatures[geo]``).
    """
    try:
        from shapely.geometry.base import BaseGeometry  # type: ignore[import-untyped]
    except ImportError as exc:
        raise ImportError(
            "shapely is required for geometry input. "
            "Install it with: pip install osmfeatures[geo]"
        ) from exc

    if isinstance(geometry, BaseGeometry):
        min_x, min_y, max_x, max_y = geometry.bounds
        return f"{min_x},{min_y},{max_x},{max_y}"

    # Accept a plain (min_lon, min_lat, max_lon, max_lat) tuple/list as well
    if hasattr(geometry, "__len__") and len(geometry) == 4:
        return f"{geometry[0]},{geometry[1]},{geometry[2]},{geometry[3]}"

    raise TypeError(
        f"Unsupported geometry type {type(geometry).__name__}. "
        "Expected a shapely geometry or a (min_lon, min_lat, max_lon, max_lat) tuple."
    )
