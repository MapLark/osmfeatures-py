"""Bounding-box utilities: tiling bboxes and merging results."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any


_METRES_PER_DEG_LAT = 111_320.0


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


def _next_power_of_two(n: int) -> int:
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


def tile_count_for_corridor(corridor: str, max_tile_area: float | None) -> int:
    """Power-of-2 tile count so each tile's area is at most *max_tile_area* (deg²).

    Caps at 256 tiles. Returns 1 when *max_tile_area* is unset/non-positive or
    the corridor already fits.
    """
    area = bbox_area_deg2(corridor)
    if max_tile_area is None or max_tile_area <= 0 or area <= max_tile_area:
        return 1
    needed = math.ceil(area / max_tile_area)
    return min(_next_power_of_two(needed), 256)


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


def corridor_bbox(points: Sequence[tuple[float, float]], buffer_m: float) -> str:
    """Axis-aligned bbox covering *points* ``(lon, lat)`` expanded by *buffer_m* metres."""
    if not points:
        raise ValueError("points must be non-empty")
    lons = [p[0] for p in points]
    lats = [p[1] for p in points]
    min_lon, max_lon = min(lons), max(lons)
    min_lat, max_lat = min(lats), max(lats)
    mid_lat = (min_lat + max_lat) / 2.0
    dlat = buffer_m / _METRES_PER_DEG_LAT
    dlon = buffer_m / (_METRES_PER_DEG_LAT * max(math.cos(math.radians(mid_lat)), 1e-9))
    return f"{min_lon - dlon},{min_lat - dlat},{max_lon + dlon},{max_lat + dlat}"


def around_to_bbox(lon: float, lat: float, radius_m: float) -> str:
    """Convert a radius search to a bounding-box string (approximation).

    Useful when you want to use bbox-tiling logic on an ``around`` query
    area.  The result is a square bbox that fully contains the circle.
    """
    return corridor_bbox([(lon, lat)], radius_m)


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
