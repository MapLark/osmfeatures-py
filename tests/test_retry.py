"""Tests for retry logic."""

from __future__ import annotations

import pytest
import responses as rsps

from osmfeatures import OSMFeaturesForbiddenError, OSMFeaturesRateLimitError, OSMFeaturesAuthError, OSMFeaturesAPIError
from tests.conftest import FEATURES_V3_URL, STATS_URL, make_test_feature, make_feature_collection


@rsps.activate
def test_retries_on_429_then_succeeds(client_with_retries):
    """429 -> 200 on the second attempt should return data normally."""
    fc = make_feature_collection([make_test_feature()])
    rsps.add(rsps.GET, FEATURES_V3_URL, status=429, json={"error": "too_many_requests", "subtype": "rate_limit_second", "detail": "too fast"})
    rsps.add(rsps.GET, FEATURES_V3_URL, status=200, json=fc)

    result = client_with_retries.query(bbox="18.06,59.32,18.09,59.34")
    assert len(result.features) == 1
    assert len(rsps.calls) == 2


@rsps.activate
def test_retries_exhausted_raises_rate_limit_error(client_with_retries):
    """All retries 429 -> OSMFeaturesRateLimitError raised after max_retries+1 attempts."""
    for _ in range(4):  # max_retries=3 -> 4 total attempts
        rsps.add(
            rsps.GET, FEATURES_V3_URL,
            status=429,
            json={"error": "too_many_requests", "subtype": "rate_limit_second", "detail": "too fast", "tier": "free"},
        )

    with pytest.raises(OSMFeaturesRateLimitError) as exc_info:
        client_with_retries.query(bbox="18.06,59.32,18.09,59.34")

    assert exc_info.value.error_code == "too_many_requests"
    assert exc_info.value.tier == "free"
    assert len(rsps.calls) == 4


@rsps.activate
def test_monthly_quota_403_is_forbidden_not_retried(client_with_retries):
    """Monthly quota is HTTP 403 quota_exceeded: ForbiddenError on the first attempt."""
    rsps.add(
        rsps.GET, FEATURES_V3_URL,
        status=403,
        json={"error": "quota_exceeded", "detail": "Monthly credit budget exhausted."},
    )

    with pytest.raises(OSMFeaturesForbiddenError, match="quota_exceeded"):
        client_with_retries.query(bbox="18.06,59.32,18.09,59.34")

    assert len(rsps.calls) == 1


@rsps.activate
def test_401_raises_auth_error_no_retry(client_with_retries):
    rsps.add(rsps.GET, FEATURES_V3_URL, status=401, body="Unauthorized")

    with pytest.raises(OSMFeaturesAuthError):
        client_with_retries.query(bbox="18.06,59.32,18.09,59.34")

    # 401 is not in retry_on_status -> only one attempt
    assert len(rsps.calls) == 1


@rsps.activate
def test_500_is_retried(client_with_retries):
    """500 errors should trigger retries."""
    fc = make_feature_collection([])
    rsps.add(rsps.GET, FEATURES_V3_URL, status=500, body="Internal Server Error")
    rsps.add(rsps.GET, FEATURES_V3_URL, status=200, json=fc)

    result = client_with_retries.query(bbox="18.06,59.32,18.09,59.34")
    assert len(rsps.calls) == 2


@rsps.activate
def test_places_post_500_is_not_retried(client_with_retries):
    """Places/routes POSTs may already be billed; do not retry 5xx."""
    from tests.conftest import BASE_URL

    url = f"{BASE_URL}/v1/places/search"
    rsps.add(rsps.POST, url, status=500, body="Internal Server Error")
    rsps.add(
        rsps.POST,
        url,
        json={"type": "FeatureCollection", "features": []},
    )

    with pytest.raises(OSMFeaturesAPIError) as exc_info:
        client_with_retries.places_search(
            bbox="18.05,59.31,18.10,59.33",
            tags=["amenity=cafe"],
        )

    assert exc_info.value.status_code == 500
    assert len(rsps.calls) == 1


@rsps.activate
def test_places_post_429_second_is_retried(client_with_retries):
    """Transient per-second 429 on POST is still safe to retry."""
    from tests.conftest import BASE_URL

    url = f"{BASE_URL}/v1/places/search"
    rsps.add(
        rsps.POST,
        url,
        status=429,
        json={
            "error": "too_many_requests",
            "subtype": "rate_limit_second",
            "detail": "too fast",
        },
    )
    rsps.add(
        rsps.POST,
        url,
        json={"type": "FeatureCollection", "features": []},
    )

    out = client_with_retries.places_search(
        bbox="18.05,59.31,18.10,59.33",
        tags=["amenity=cafe"],
    )
    assert out["type"] == "FeatureCollection"
    assert len(rsps.calls) == 2


def test_for_non_idempotent_keeps_backoff_only_retries_429():
    from osmfeatures import RetryConfig
    from osmfeatures.retry import NON_IDEMPOTENT_RETRY_ON_STATUS

    base = RetryConfig(max_retries=5, backoff_base=2.0, jitter=False)
    post = base.for_non_idempotent()
    assert post.max_retries == 5
    assert post.backoff_base == 2.0
    assert post.jitter is False
    assert post.retry_on_status == NON_IDEMPOTENT_RETRY_ON_STATUS
    assert 500 not in post.retry_on_status
    assert base.retry_on_status != post.retry_on_status


@rsps.activate
def test_504_is_not_retried_on_count(client_with_retries):
    """Gateway timeout means the first query is still running. Do not fan out."""
    rsps.add(rsps.GET, STATS_URL, status=504, body="Gateway Timeout")
    rsps.add(
        rsps.GET,
        STATS_URL,
        json={"groups": [], "total": 0, "truncated": False},
    )

    with pytest.raises(OSMFeaturesAPIError) as exc_info:
        client_with_retries.count(
            group_by="amenity",
            bbox="18.06,59.32,18.09,59.34",
            tags=["amenity"],
        )

    assert exc_info.value.status_code == 504
    assert len(rsps.calls) == 1


@rsps.activate
def test_retry_after_header_respected(client_with_retries, monkeypatch):
    """The Retry-After header value should be used as the sleep delay."""
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    fc = make_feature_collection([make_test_feature()])
    rsps.add(rsps.GET, FEATURES_V3_URL, status=429, json={"error": "too_many_requests", "subtype": "rate_limit_second"}, headers={"Retry-After": "2.5"})
    rsps.add(rsps.GET, FEATURES_V3_URL, status=200, json=fc)

    client_with_retries.query(bbox="18.06,59.32,18.09,59.34")
    assert sleeps == [2.5]
