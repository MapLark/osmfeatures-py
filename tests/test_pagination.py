"""Tests for auto-pagination via query_all()."""

from __future__ import annotations

import pytest
import responses as rsps

from tests.conftest import ELEMENTS_URL, make_test_feature, make_feature_collection


@rsps.activate
def test_query_all_single_page(client):
    features = [make_test_feature(f"way/{i}") for i in range(3)]
    rsps.add(rsps.GET, ELEMENTS_URL, json=make_feature_collection(features, has_more=False))

    result = client.query_all(bbox="18.06,59.32,18.09,59.34")

    assert len(result.features) == 3
    assert result.meta.has_more is False
    assert len(rsps.calls) == 1


@rsps.activate
def test_query_all_two_pages(client):
    page1 = [make_test_feature(f"way/{i}") for i in range(3)]
    page2 = [make_test_feature(f"way/{i}") for i in range(3, 6)]

    rsps.add(rsps.GET, ELEMENTS_URL, json=make_feature_collection(page1, has_more=True, next_cursor="cursor-1"))
    rsps.add(rsps.GET, ELEMENTS_URL, json=make_feature_collection(page2, has_more=False))

    result = client.query_all(bbox="18.06,59.32,18.09,59.34")

    assert len(result.features) == 6
    assert {f.id for f in result.features} == {f"way/{i}" for i in range(6)}
    assert len(rsps.calls) == 2


@rsps.activate
def test_query_all_deduplicates_across_pages(client):
    """If the API returns the same feature id on two pages it should appear only once."""
    f_shared = make_test_feature("way/99")
    page1 = [make_test_feature("way/1"), f_shared]
    page2 = [f_shared, make_test_feature("way/2")]

    rsps.add(rsps.GET, ELEMENTS_URL, json=make_feature_collection(page1, has_more=True, next_cursor="cursor-1"))
    rsps.add(rsps.GET, ELEMENTS_URL, json=make_feature_collection(page2, has_more=False))

    result = client.query_all(bbox="18.06,59.32,18.09,59.34")
    ids = [f.id for f in result.features]
    assert len(ids) == len(set(ids)), "Duplicate feature ids found"


@rsps.activate
def test_query_all_raises_on_cursor_not_advancing(client):
    """API returning has_more=true but stale next_cursor should raise RuntimeError."""
    page1 = [make_test_feature("way/1")]
    rsps.add(rsps.GET, ELEMENTS_URL, json=make_feature_collection(page1, has_more=True, next_cursor="cursor-1"))
    rsps.add(rsps.GET, ELEMENTS_URL, json=make_feature_collection(page1, has_more=True, next_cursor="cursor-1"))

    with pytest.raises(RuntimeError, match="did not advance"):
        client.query_all(bbox="18.06,59.32,18.09,59.34")


@rsps.activate
def test_query_all_raises_on_empty_page_with_has_more_true(client):
    """API returning has_more=true with empty features should fail fast."""
    rsps.add(
        rsps.GET,
        ELEMENTS_URL,
        json=make_feature_collection([], has_more=True, next_cursor="cursor-1"),
    )

    with pytest.raises(RuntimeError, match="empty features page"):
        client.query_all(bbox="18.06,59.32,18.09,59.34")

    assert len(rsps.calls) == 1
