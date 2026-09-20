"""Shared fixtures for osmgeojson unit tests.

No live API is required - all HTTP calls are intercepted by the
``responses`` library.
"""

from __future__ import annotations
from typing import Any

import pytest
import responses as rsps

from osmfeatures import OSMFeaturesClient, RetryConfig


FAKE_API_KEY = "sk-test-1234"
BASE_URL = "http://testserver"
FEATURES_URL = f"{BASE_URL}/v2/osm_features"
COST_URL = f"{BASE_URL}/v2/osm_features/cost"
STATS_URL = f"{BASE_URL}/v2/osm_features/stats"


def make_test_feature(fid: str = "way/1", tags: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "type": "Feature",
        "id": fid,
        "geometry": {"type": "Polygon", "coordinates": [[[18.06, 59.32], [18.07, 59.32], [18.07, 59.33], [18.06, 59.32]]]},
        "properties": {
            "tags": tags or {"building": "yes"},
            "centroid": {"type": "Point", "coordinates": [18.065, 59.325]},
        },
    }


def make_feature_collection(features: list[dict[str, Any]]) -> dict[str, Any]:
    return {"type": "FeatureCollection", "features": features}


def pagination_headers(
    features: list[dict[str, Any]],
    *,
    has_more: bool = False,
    next_cursor: str | None = None,
) -> dict[str, str]:
    headers = {
        "X-Returned": str(len(features)),
        "X-Has-More": "true" if has_more else "false",
    }
    if next_cursor is not None:
        headers["X-Next-Cursor"] = next_cursor
    return headers


def features_page(
    features: list[dict[str, Any]],
    *,
    has_more: bool = False,
    next_cursor: str | None = None,
    status: int = 200,
) -> tuple[int, dict[str, Any], dict[str, str]]:
    """(status, body, headers) for async mock transport queues."""
    return (
        status,
        make_feature_collection(features),
        pagination_headers(features, has_more=has_more, next_cursor=next_cursor),
    )


def add_features_response(
    features: list[dict[str, Any]],
    *,
    has_more: bool = False,
    next_cursor: str | None = None,
    status: int = 200,
) -> None:
    """Register a mocked GET /v2/osm_features with body + pagination headers."""
    rsps.add(
        rsps.GET,
        FEATURES_URL,
        json=make_feature_collection(features),
        headers=pagination_headers(features, has_more=has_more, next_cursor=next_cursor),
        status=status,
    )


@pytest.fixture
def client() -> OSMFeaturesClient:
    return OSMFeaturesClient(
        api_key=FAKE_API_KEY,
        base_url=BASE_URL,
        retry_config=RetryConfig(max_retries=0),  # no retries in most tests
    )


@pytest.fixture
def client_with_retries() -> OSMFeaturesClient:
    return OSMFeaturesClient(
        api_key=FAKE_API_KEY,
        base_url=BASE_URL,
        retry_config=RetryConfig(max_retries=3, backoff_base=0.0, jitter=False),
    )
