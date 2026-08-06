"""Tests for auto-pagination via query_all()."""

from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest
import responses as rsps

from osmfeatures import split_bbox_tiles
from tests.conftest import add_features_response, make_test_feature


@rsps.activate
def test_query_all_single_page(client):
    features = [make_test_feature(f"way/{i}") for i in range(3)]
    add_features_response(features, has_more=False)

    result = client.query_all(bbox="18.06,59.32,18.09,59.34", bbox_tiles=1)

    assert len(result.features) == 3
    assert result.meta.has_more is False
    assert len(rsps.calls) == 1


@rsps.activate
def test_query_all_two_pages(client):
    page1 = [make_test_feature(f"way/{i}") for i in range(3)]
    page2 = [make_test_feature(f"way/{i}") for i in range(3, 6)]

    add_features_response(page1, has_more=True, next_cursor="cursor-1")
    add_features_response(page2, has_more=False)

    result = client.query_all(bbox="18.06,59.32,18.09,59.34", bbox_tiles=1)

    assert len(result.features) == 6
    assert {f.id for f in result.features} == {f"way/{i}" for i in range(6)}
    assert len(rsps.calls) == 2


@rsps.activate
def test_query_all_deduplicates_across_pages(client):
    """If the API returns the same feature id on two pages it should appear only once."""
    f_shared = make_test_feature("way/99")
    page1 = [make_test_feature("way/1"), f_shared]
    page2 = [f_shared, make_test_feature("way/2")]

    add_features_response(page1, has_more=True, next_cursor="cursor-1")
    add_features_response(page2, has_more=False)

    result = client.query_all(bbox="18.06,59.32,18.09,59.34", bbox_tiles=1)
    ids = [f.id for f in result.features]
    assert len(ids) == len(set(ids)), "Duplicate feature ids found"


@rsps.activate
def test_query_all_raises_on_cursor_not_advancing(client):
    """API returning has_more=true but stale next_cursor should raise RuntimeError."""
    page1 = [make_test_feature("way/1")]
    add_features_response(page1, has_more=True, next_cursor="cursor-1")
    add_features_response(page1, has_more=True, next_cursor="cursor-1")

    with pytest.raises(RuntimeError, match="did not advance"):
        client.query_all(bbox="18.06,59.32,18.09,59.34", bbox_tiles=1)


@rsps.activate
def test_query_all_raises_on_empty_page_with_has_more_true(client):
    """API returning has_more=true with empty features should fail fast."""
    add_features_response([], has_more=True, next_cursor="cursor-1")

    with pytest.raises(RuntimeError, match="empty features page"):
        client.query_all(bbox="18.06,59.32,18.09,59.34", bbox_tiles=1)

    assert len(rsps.calls) == 1


@rsps.activate
def test_query_all_tiles_bbox(client):
    bbox = "18.06,59.32,18.09,59.34"
    tiles = split_bbox_tiles(bbox, 2)
    assert len(tiles) == 2

    add_features_response([make_test_feature("way/1")], has_more=False)
    add_features_response([make_test_feature("way/2")], has_more=False)

    result = client.query_all(bbox=bbox, bbox_tiles=2)

    assert {f.id for f in result.features} == {"way/1", "way/2"}
    assert len(rsps.calls) == 2
    requested = [parse_qs(urlsplit(c.request.url).query)["bbox"][0] for c in rsps.calls]
    assert set(requested) == set(tiles)


@rsps.activate
def test_query_all_deduplicates_across_tiles(client):
    bbox = "18.06,59.32,18.09,59.34"
    shared = make_test_feature("way/shared")
    add_features_response([shared, make_test_feature("way/a")], has_more=False)
    add_features_response([shared, make_test_feature("way/b")], has_more=False)

    result = client.query_all(bbox=bbox, bbox_tiles=2)

    assert [f.id for f in result.features] == ["way/shared", "way/a", "way/b"]


@rsps.activate
def test_query_all_max_features_caps_and_stops(client):
    add_features_response(
        [make_test_feature("way/1"), make_test_feature("way/2")],
        has_more=True,
        next_cursor="p2",
    )
    add_features_response(
        [make_test_feature("way/3"), make_test_feature("way/4")],
        has_more=False,
    )

    result = client.query_all(
        bbox="18.06,59.32,18.09,59.34",
        bbox_tiles=1,
        max_features=3,
    )

    assert len(result.features) == 3
    assert result.meta.has_more is True
    assert len(rsps.calls) == 2


@rsps.activate
def test_query_all_max_features_none_has_no_cap(client):
    add_features_response([make_test_feature("way/only")], has_more=False)

    result = client.query_all(
        bbox="18.06,59.32,18.09,59.34",
        bbox_tiles=1,
        max_features=None,
    )

    assert len(result.features) == 1
    assert result.meta.has_more is False


def test_query_all_rejects_limit_kwarg(client):
    try:
        client.query_all(bbox="18.06,59.32,18.09,59.34", limit=10)  # type: ignore[call-arg]
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "limit_per_page" in str(exc)
