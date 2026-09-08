"""Unit tests for nearest_within (no HTTP)."""

from __future__ import annotations

import pytest

from osmfeatures import nearest_within
from osmfeatures.nearest import MAX_COMPARISONS


def _point(fid: str, lon: float, lat: float) -> dict:
    return {
        "type": "Feature",
        "id": fid,
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {},
    }


def _polygon_with_centroid(fid: str, lon: float, lat: float) -> dict:
    return {
        "type": "Feature",
        "id": fid,
        "geometry": {
            "type": "Polygon",
            "coordinates": [[[lon, lat], [lon + 0.01, lat], [lon + 0.01, lat + 0.01], [lon, lat]]],
        },
        "properties": {"centroid": {"type": "Point", "coordinates": [lon, lat]}},
    }


def test_true_nearest():
    primary = [_point("node/1", 18.07, 59.33)]
    close = _point("node/10", 18.0705, 59.33)
    far = _point("node/11", 18.09, 59.33)
    out = nearest_within(primary, [far, close], max_distance_m=500)
    assert len(out) == 1
    assert out[0]["nearest"]["id"] == "node/10"
    assert out[0]["distance_m"] < 50


def test_max_distance_excludes():
    primary = [_point("node/1", 18.07, 59.33)]
    far = _point("node/11", 18.09, 59.33)
    assert nearest_within(primary, [far], max_distance_m=50) == []


def test_limit_slices_nearest_pairs():
    primaries = [
        _point("node/1", 18.07, 59.33),
        _point("node/2", 18.071, 59.33),
        _point("node/3", 18.08, 59.33),
    ]
    stations = [_point("node/99", 18.07, 59.33)]
    out = nearest_within(primaries, stations, max_distance_m=2000, limit=2)
    assert len(out) == 2
    assert out[0]["feature"]["id"] == "node/1"
    assert out[1]["feature"]["id"] == "node/2"
    assert out[0]["distance_m"] <= out[1]["distance_m"]


def test_centroid_fallback():
    park = _polygon_with_centroid("way/1", 18.07, 59.33)
    cafe = _point("node/2", 18.0702, 59.33)
    out = nearest_within({"type": "FeatureCollection", "features": [park]}, [cafe], max_distance_m=100)
    assert len(out) == 1
    assert out[0]["feature"]["id"] == "way/1"
    assert out[0]["nearest"]["id"] == "node/2"


def test_limit_none_keeps_every_primary():
    primaries = [_point(f"node/{i}", 18.07 + i * 0.0001, 59.33) for i in range(25)]
    stations = [_point("node/99", 18.07, 59.33)]
    out = nearest_within(primaries, stations, max_distance_m=2000, limit=None)
    assert len(out) == 25


def test_limit_zero_raises():
    with pytest.raises(ValueError, match="positive"):
        nearest_within([_point("node/1", 18.07, 59.33)], [_point("node/2", 18.07, 59.33)], max_distance_m=100, limit=0)


def test_over_comparison_cap_raises():
    primaries = [_point(f"node/p{i}", 18.07, 59.33) for i in range(5)]
    secondaries = [_point(f"node/s{i}", 18.07, 59.33) for i in range(5)]
    with pytest.raises(ValueError, match=r"5×5 comparisons"):
        nearest_within(primaries, secondaries, max_distance_m=2000, max_comparisons=20)


def test_comparison_cap_allows_exact_limit():
    primaries = [_point(f"node/p{i}", 18.07, 59.33) for i in range(5)]
    secondaries = [_point(f"node/s{i}", 18.07, 59.33) for i in range(4)]
    out = nearest_within(primaries, secondaries, max_distance_m=2000, limit=None, max_comparisons=20)
    assert len(out) == 5


def test_max_comparisons_none_disables_cap():
    primaries = [_point(f"node/p{i}", 18.07, 59.33) for i in range(5)]
    secondaries = [_point(f"node/s{i}", 18.07, 59.33) for i in range(5)]
    out = nearest_within(primaries, secondaries, max_distance_m=2000, limit=None, max_comparisons=None)
    assert len(out) == 5


def test_max_comparisons_zero_raises():
    with pytest.raises(ValueError, match="max_comparisons"):
        nearest_within(
            [_point("node/1", 18.07, 59.33)],
            [_point("node/2", 18.07, 59.33)],
            max_distance_m=100,
            max_comparisons=0,
        )


def test_default_cap_constant():
    assert MAX_COMPARISONS == 500_000


def test_empty_secondary_is_empty():
    assert nearest_within([_point("node/1", 18.07, 59.33)], [], max_distance_m=100) == []


def test_missing_point_raises():
    poly = {
        "type": "Feature",
        "id": "way/1",
        "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
        "properties": {},
    }
    with pytest.raises(ValueError, match="centroid"):
        nearest_within([poly], [_point("node/1", 0, 0)], max_distance_m=100)
