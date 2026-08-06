"""Tests for retry logic."""

from __future__ import annotations

import pytest
import responses as rsps

from osmfeatures import OSMFeaturesRateLimitError, OSMFeaturesAuthError, OSMFeaturesAPIError
from tests.conftest import FEATURES_URL, make_test_feature, make_feature_collection


@rsps.activate
def test_retries_on_429_then_succeeds(client_with_retries):
    """429 -> 200 on the second attempt should return data normally."""
    fc = make_feature_collection([make_test_feature()])
    rsps.add(rsps.GET, FEATURES_URL, status=429, json={"error": "too_many_requests", "subtype": "rate_limit_second", "detail": "too fast"})
    rsps.add(rsps.GET, FEATURES_URL, status=200, json=fc)

    result = client_with_retries.query(bbox="18.06,59.32,18.09,59.34")
    assert len(result.features) == 1
    assert len(rsps.calls) == 2


@rsps.activate
def test_retries_exhausted_raises_rate_limit_error(client_with_retries):
    """All retries 429 -> OSMFeaturesRateLimitError raised after max_retries+1 attempts."""
    for _ in range(4):  # max_retries=3 -> 4 total attempts
        rsps.add(
            rsps.GET, FEATURES_URL,
            status=429,
            json={"error": "too_many_requests", "subtype": "rate_limit_second", "detail": "too fast", "tier": "free"},
        )

    with pytest.raises(OSMFeaturesRateLimitError) as exc_info:
        client_with_retries.query(bbox="18.06,59.32,18.09,59.34")

    assert exc_info.value.error_code == "too_many_requests"
    assert exc_info.value.tier == "free"
    assert len(rsps.calls) == 4


@rsps.activate
def test_monthly_limit_not_retried(client_with_retries):
    """rate_limit_monthly subtype (hard cap) should not be retried."""
    rsps.add(
        rsps.GET, FEATURES_URL,
        status=429,
        json={"error": "too_many_requests", "subtype": "rate_limit_monthly", "detail": "monthly budget exceeded", "tier": "free"},
    )
    rsps.add(
        rsps.GET, FEATURES_URL,
        status=429,
        json={"error": "too_many_requests", "subtype": "rate_limit_monthly", "detail": "monthly budget exceeded", "tier": "free"},
    )
    rsps.add(
        rsps.GET, FEATURES_URL,
        status=429,
        json={"error": "too_many_requests", "subtype": "rate_limit_monthly", "detail": "monthly budget exceeded", "tier": "free"},
    )
    rsps.add(
        rsps.GET, FEATURES_URL,
        status=429,
        json={"error": "too_many_requests", "subtype": "rate_limit_monthly", "detail": "monthly budget exceeded", "tier": "free"},
    )

    with pytest.raises(OSMFeaturesRateLimitError):
        client_with_retries.query(bbox="18.06,59.32,18.09,59.34")

    assert len(rsps.calls) == 1


@rsps.activate
def test_401_raises_auth_error_no_retry(client_with_retries):
    rsps.add(rsps.GET, FEATURES_URL, status=401, body="Unauthorized")

    with pytest.raises(OSMFeaturesAuthError):
        client_with_retries.query(bbox="18.06,59.32,18.09,59.34")

    # 401 is not in retry_on_status -> only one attempt
    assert len(rsps.calls) == 1


@rsps.activate
def test_500_is_retried(client_with_retries):
    """500 errors should trigger retries."""
    fc = make_feature_collection([])
    rsps.add(rsps.GET, FEATURES_URL, status=500, body="Internal Server Error")
    rsps.add(rsps.GET, FEATURES_URL, status=200, json=fc)

    result = client_with_retries.query(bbox="18.06,59.32,18.09,59.34")
    assert len(rsps.calls) == 2


@rsps.activate
def test_retry_after_header_respected(client_with_retries, monkeypatch):
    """The Retry-After header value should be used as the sleep delay."""
    sleeps: list[float] = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

    fc = make_feature_collection([make_test_feature()])
    rsps.add(rsps.GET, FEATURES_URL, status=429, json={"error": "too_many_requests", "subtype": "rate_limit_second"}, headers={"Retry-After": "2.5"})
    rsps.add(rsps.GET, FEATURES_URL, status=200, json=fc)

    client_with_retries.query(bbox="18.06,59.32,18.09,59.34")
    assert sleeps == [2.5]
