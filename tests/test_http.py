"""HTTP helper logging on API errors."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from osmfeatures import DEFAULT_QUERY_ALL_TIMEOUT_S, DEFAULT_TIMEOUT, OSMFeaturesAPIError
from osmfeatures._http import raise_for_response


def test_raise_for_response_logs_request_body(caplog):
    resp = SimpleNamespace(
        status_code=400,
        text='{"detail":"bbox too large"}',
        headers={},
        request=SimpleNamespace(
            method="POST",
            url="http://api:8080/v1/places/search",
            content=b'{"bbox":"18.0,59.0,19.0,60.0","tags":["amenity=cafe"]}',
        ),
    )
    caplog.set_level(logging.WARNING, logger="osmfeatures.http")
    with pytest.raises(OSMFeaturesAPIError):
        raise_for_response(resp)
    assert "POST http://api:8080/v1/places/search" in caplog.text
    assert "amenity=cafe" in caplog.text
    assert "bbox too large" in caplog.text


def test_query_all_timeout_matches_http_default():
    assert DEFAULT_QUERY_ALL_TIMEOUT_S == DEFAULT_TIMEOUT


def test_timeout_constants_are_public():
    import osmfeatures

    assert "DEFAULT_TIMEOUT" in osmfeatures.__all__
    assert "DEFAULT_QUERY_ALL_TIMEOUT_S" in osmfeatures.__all__
    assert osmfeatures.DEFAULT_TIMEOUT == 610.0


def test_raise_for_response_names_empty_body():
    resp = SimpleNamespace(
        status_code=502,
        text="",
        headers={},
        request=SimpleNamespace(method="GET", url="http://api/v2/osm_features/count", content=b""),
    )
    with pytest.raises(OSMFeaturesAPIError, match=r"API error \(HTTP 502\): empty response body") as exc:
        raise_for_response(resp)
    assert exc.value.status_code == 502
