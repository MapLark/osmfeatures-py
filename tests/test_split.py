"""Tests for shared auto_split helpers."""

from __future__ import annotations

import pytest

from osmfeatures._split import (
    MAX_DENSITY_JUMP_DEPTH,
    MAX_SPLIT_TILES,
    count_group_key,
    fold_auto_split,
    match_split_cap,
    max_limit_from_tier,
    parse_bbox_area_limit,
    tiles_for_match_count,
    too_dense_message,
    too_many_tiles_message,
)
from osmfeatures.models import OSMFeaturesAPIError


def test_count_group_key_prefers_and_tags():
    assert count_group_key(["amenity=cafe"], ["cuisine=coffee_shop"]) == "amenity"


def test_count_group_key_falls_back_to_or_tags():
    assert count_group_key(None, ["amenity=cafe", "amenity=restaurant"]) == "amenity"


def test_count_group_key_skips_unbounded_and_uses_or_tags():
    assert count_group_key(["name=Cafe"], ["amenity=cafe"]) == "amenity"


def test_count_group_key_none_without_filters():
    assert count_group_key(None) is None
    assert count_group_key(["name=Cafe"]) is None


def test_match_split_cap_ignores_page_limit():
    assert match_split_cap(None, 75_000) == 75_000
    assert match_split_cap(10, 75_000) == 75_000
    assert match_split_cap(200_000, 75_000) == 200_000


def test_max_limit_from_tier():
    assert max_limit_from_tier({"max_limit": 200_000}) == 200_000
    with pytest.raises(ValueError, match="max_limit"):
        max_limit_from_tier({})
    with pytest.raises(ValueError, match="integer"):
        max_limit_from_tier({"max_limit": "n/a"})
    with pytest.raises(ValueError, match=">= 1"):
        max_limit_from_tier({"max_limit": 0})


def test_parse_bbox_area_limit():
    exc = OSMFeaturesAPIError(
        "API error (HTTP 400): bbox area 5.009167 exceeds the tagged tier "
        "limit 0.040000 for the 'free' tier.",
        400,
    )
    assert parse_bbox_area_limit(exc) == 0.04
    assert parse_bbox_area_limit(OSMFeaturesAPIError("result_too_large", 400)) is None
    assert parse_bbox_area_limit(OSMFeaturesAPIError("bbox area", 500)) is None


def test_fold_auto_split_default():
    assert fold_auto_split() is False
    assert fold_auto_split(True) is True


def test_fold_auto_split_deprecated_alias():
    with pytest.warns(DeprecationWarning, match="use auto_split"):
        assert fold_auto_split(split_until_fit=True) is True


def test_fold_auto_split_disagree():
    with pytest.warns(DeprecationWarning, match="use auto_split"):
        with pytest.raises(ValueError, match="disagree"):
            fold_auto_split(True, False)


def test_max_split_tiles_is_five_bisections():
    assert MAX_SPLIT_TILES == 32
    assert MAX_DENSITY_JUMP_DEPTH == 1


def test_too_many_tiles_message_includes_match_total():
    text = too_many_tiles_message(128, total=59)
    assert "128 bbox tiles" in text
    assert "59 matches" in text
    assert "too large" in text
    assert "Do not split the leftover bbox" in text


def test_too_dense_message():
    text = too_dense_message(total=3_000_000)
    assert "max 32 tiles" in text
    assert "too dense" in text
    assert "power-of-2 jump" in text
    assert "3000000 matches" in text


def test_tiles_for_match_count_is_power_of_two():
    assert tiles_for_match_count(50_000, 50_000) == 1
    assert tiles_for_match_count(50_001, 50_000) == 2
    assert tiles_for_match_count(100_000, 50_000) == 2
    assert tiles_for_match_count(100_001, 50_000) == 4
    assert tiles_for_match_count(200_000, 50_000) == 4
    assert tiles_for_match_count(3_000_000, 50_000) == 64
