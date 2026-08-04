"""Shared fixtures for osmgeojson unit tests.

No live API is required - all HTTP calls are intercepted by the
``responses`` library.
"""

from __future__ import annotations
from typing import Any

import pytest

from osmgeojson import OSMGeoJSONClient, RetryConfig, OSMFeatureCollection, ResponseMeta
from osmgeojson.models import OSMFeature


FAKE_API_KEY = "sk-test-1234"
BASE_URL = "http://testserver"
FEATURES_URL = f"{BASE_URL}/v2/osm_features"
COST_URL = f"{BASE_URL}/v2/osm_features/cost"


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


def make_feature_collection(
    features: list[dict[str, Any]],
    has_more: bool = False,
    next_cursor: str | None = None,
) -> dict[str, Any]:
    return OSMFeatureCollection(
        features=[OSMFeature.from_dict(f) for f in features],
        meta=ResponseMeta(returned=len(features), has_more=has_more, next_cursor=next_cursor),
    ).to_dict()


@pytest.fixture
def client() -> OSMGeoJSONClient:
    return OSMGeoJSONClient(
        api_key=FAKE_API_KEY,
        base_url=BASE_URL,
        retry_config=RetryConfig(max_retries=0),  # no retries in most tests
    )


@pytest.fixture
def client_with_retries() -> OSMGeoJSONClient:
    return OSMGeoJSONClient(
        api_key=FAKE_API_KEY,
        base_url=BASE_URL,
        retry_config=RetryConfig(max_retries=3, backoff_base=0.0, jitter=False),
    )
