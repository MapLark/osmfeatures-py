"""HTTP helper logging on API errors."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from osmfeatures import OSMFeaturesAPIError
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
