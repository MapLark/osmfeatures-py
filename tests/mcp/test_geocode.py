"""Nominatim interim geocode (no live HTTP)."""

from __future__ import annotations

import pytest
import responses as rsps

from osmfeatures.mcp._geocode import NOMINATIM_SEARCH_URL, nominatim_geocode
from osmfeatures.mcp._mcp_session import GeoAgentSession, assert_no_coordinate_arrays


@pytest.fixture(autouse=True)
def no_nominatim_throttle(monkeypatch):
    monkeypatch.setattr("osmfeatures.mcp._geocode._MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr("osmfeatures.mcp._geocode._last_call_mono", 0.0)


@rsps.activate
def test_nominatim_geocode_maps_bbox_and_point():
    rsps.add(
        rsps.GET,
        NOMINATIM_SEARCH_URL,
        json=[
            {
                "lat": "59.3123782",
                "lon": "18.0697558",
                "display_name": "Södermalm, Stockholm, Sverige",
                "boundingbox": ["59.3032122", "59.3213931", "18.0260682", "18.1071724"],
                "osm_type": "relation",
                "osm_id": 2017432,
            }
        ],
    )
    out = nominatim_geocode("Södermalm, Stockholm")
    assert out["status"] == "ok"
    place = out["place"]
    assert place["lat"] == pytest.approx(59.3123782)
    assert place["lon"] == pytest.approx(18.0697558)
    assert place["bbox"] == "18.0260682,59.3032122,18.1071724,59.3213931"
    assert place["label"] == "Södermalm, Stockholm, Sverige"
    assert place["osm_type"] == "relation"
    assert place["osm_id"] == 2017432
    req = rsps.calls[0].request
    assert "Södermalm" in req.url or "S%C3%B6dermalm" in req.url
    assert req.headers["User-Agent"].startswith("osmfeatures-mcp/")


@rsps.activate
def test_nominatim_geocode_empty_and_no_hits():
    assert nominatim_geocode("  ") == {
        "status": "no_results",
        "reason": "Empty query.",
        "place": None,
    }
    rsps.add(rsps.GET, NOMINATIM_SEARCH_URL, json=[])
    out = nominatim_geocode("nowhere-that-exists-xyz")
    assert out["status"] == "no_results"
    assert out["place"] is None


def test_session_geocode_does_not_store_a_collection(monkeypatch):
    monkeypatch.setattr(
        "osmfeatures.mcp._mcp_session.nominatim_geocode",
        lambda q: {
            "status": "ok",
            "place": {
                "label": "Södermalm, Stockholm",
                "lon": 18.07,
                "lat": 59.31,
                "bbox": "18.02,59.30,18.10,59.32",
            },
        },
    )
    session = GeoAgentSession(object())
    out = session.geocode("Södermalm")
    assert_no_coordinate_arrays(out)
    assert out["place"]["bbox"].startswith("18.02")
    with pytest.raises(KeyError):
        session.get("fc_1")
