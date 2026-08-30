"""Tests for chunking.py."""

from __future__ import annotations

import pytest

from osmfeatures import (
    around_to_bbox,
    bbox_area_deg2,
    corridor_bbox,
    merge_features,
    parse_bbox,
    split_bbox_tiles,
    tile_count_for_corridor,
)


class TestParseBbox:
    def test_valid(self) -> None:
        assert parse_bbox("1.0,2.0,3.0,4.0") == (1.0, 2.0, 3.0, 4.0)

    def test_invalid_parts(self) -> None:
        with pytest.raises(ValueError, match="Invalid bbox format"):
            parse_bbox("1.0,2.0,3.0")


class TestBboxAreaDeg2:
    def test_small_area(self) -> None:
        area = bbox_area_deg2("18.0,59.0,18.1,59.1")
        assert abs(area - 0.01) < 1e-9

    def test_zero_span(self) -> None:
        assert bbox_area_deg2("18.0,59.0,18.0,59.0") == 0.0


class TestSplitBboxTiles:
    def test_one_tile_unchanged(self) -> None:
        bbox = "18.0,59.0,18.1,59.1"
        assert split_bbox_tiles(bbox, 1) == [bbox]

    def test_two_tiles_longest_side(self) -> None:
        assert split_bbox_tiles("0,0,2,1", 2) == ["0.0,0.0,1.0,1.0", "1.0,0.0,2.0,1.0"]

    def test_four_tiles(self) -> None:
        tiles = split_bbox_tiles("0,0,2,2", 4)
        assert len(tiles) == 4
        total = sum(bbox_area_deg2(t) for t in tiles)
        assert abs(total - 4.0) < 1e-9

    def test_rejects_non_power_of_two(self) -> None:
        with pytest.raises(ValueError, match="power of 2"):
            split_bbox_tiles("0,0,1,1", 3)
        with pytest.raises(ValueError, match="power of 2"):
            split_bbox_tiles("0,0,1,1", 6)

    def test_returns_valid_bbox_strings(self) -> None:
        for chunk in split_bbox_tiles("0.0,0.0,1.0,1.0", 4):
            min_lon, min_lat, max_lon, max_lat = parse_bbox(chunk)
            assert min_lon < max_lon
            assert min_lat < max_lat


class TestMergeFeatures:
    def test_deduplicates_by_id(self) -> None:
        f1 = {"id": "way/1", "geometry": {}, "properties": {}}
        f2 = {"id": "way/2", "geometry": {}, "properties": {}}
        f1_dup = {"id": "way/1", "geometry": {}, "properties": {"extra": True}}

        result = merge_features([[f1, f2], [f1_dup]])
        assert len(result) == 2
        ids = [f["id"] for f in result]
        assert ids == ["way/1", "way/2"]

    def test_preserves_order(self) -> None:
        features = [{"id": f"way/{i}", "geometry": {}, "properties": {}} for i in range(5)]
        result = merge_features([features])
        assert [f["id"] for f in result] == [f"way/{i}" for i in range(5)]

    def test_empty_input(self) -> None:
        assert merge_features([]) == []
        assert merge_features([[]]) == []


class TestTileCountForCorridor:
    def test_fits_returns_one(self) -> None:
        assert tile_count_for_corridor("0,0,1,1", 1.0) == 1
        assert tile_count_for_corridor("0,0,1,1", None) == 1
        assert tile_count_for_corridor("0,0,1,1", 0) == 1

    def test_rounds_up_to_power_of_two(self) -> None:
        # area 4 / max 1 → need 4 tiles
        assert tile_count_for_corridor("0,0,2,2", 1.0) == 4
        # area 3 / max 1 → need 3 → round up to 4
        assert tile_count_for_corridor("0,0,3,1", 1.0) == 4

    def test_caps_at_256(self) -> None:
        assert tile_count_for_corridor("0,0,1000,1000", 1.0) == 256


class TestCorridorBbox:
    def test_single_point_matches_around(self) -> None:
        assert corridor_bbox([(18.065, 59.33)], 1000) == around_to_bbox(18.065, 59.33, 1000)

    def test_covers_all_points_plus_buffer(self) -> None:
        bbox = corridor_bbox([(0.0, 0.0), (1.0, 0.5)], 0)
        assert parse_bbox(bbox) == (0.0, 0.0, 1.0, 0.5)
        buffered = parse_bbox(corridor_bbox([(0.0, 0.0), (1.0, 0.5)], 1000))
        assert buffered[0] < 0.0 < buffered[2]
        assert buffered[1] < 0.0
        assert buffered[3] > 0.5

    def test_rejects_empty(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            corridor_bbox([], 100)


class TestAroundToBbox:
    def test_symmetric(self) -> None:
        bbox_str = around_to_bbox(18.065, 59.33, 1000)
        min_lon, min_lat, max_lon, max_lat = parse_bbox(bbox_str)
        assert min_lon < 18.065 < max_lon
        assert min_lat < 59.33 < max_lat

    def test_larger_radius_gives_larger_bbox(self) -> None:
        small = bbox_area_deg2(around_to_bbox(0.0, 0.0, 500))
        large = bbox_area_deg2(around_to_bbox(0.0, 0.0, 5000))
        assert large > small
