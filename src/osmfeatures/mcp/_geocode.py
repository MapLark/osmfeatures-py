"""Nominatim geocode for MCP until MapLark ``POST /v1/geocode`` ships.

Public Nominatim is 1 request/s and requires a identifying User-Agent.
Swap this module for the SDK ``client.geocode`` call when the API is public.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import requests

NOMINATIM_SEARCH_URL = os.environ.get(
    "NOMINATIM_SEARCH_URL",
    "https://nominatim.openstreetmap.org/search",
)
# Nominatim usage policy: identify the app. Override with NOMINATIM_USER_AGENT.
_USER_AGENT = os.environ.get(
    "NOMINATIM_USER_AGENT",
    "osmfeatures-mcp/0.3 (interim geocode; +https://github.com/MapLark/osmfeatures-py)",
)
# ponytail: public Nominatim 1 req/s. Drop this throttle when MapLark geocode is live.
_MIN_INTERVAL_S = 1.0
_lock = threading.Lock()
_last_call_mono = 0.0


def _throttle() -> None:
    global _last_call_mono
    wait = 0.0
    with _lock:
        now = time.monotonic()
        wait = _MIN_INTERVAL_S - (now - _last_call_mono)
        if wait <= 0:
            _last_call_mono = now
            return
        _last_call_mono = now + wait
    if wait > 0:
        time.sleep(wait)


def _bbox_from_nominatim(boundingbox: Any) -> str | None:
    """Nominatim ``[south, north, west, east]`` → MapLark ``west,south,east,north``."""
    if not isinstance(boundingbox, (list, tuple)) or len(boundingbox) != 4:
        return None
    south, north, west, east = (str(x).strip() for x in boundingbox)
    if not all((south, north, west, east)):
        return None
    return f"{west},{south},{east},{north}"


def nominatim_geocode(q: str, *, timeout_s: float = 8.0) -> dict[str, Any]:
    """Top Nominatim hit. Planner result is lon/lat + bbox, no GeoJSON."""
    text = (q or "").strip()
    if not text:
        return {"status": "no_results", "reason": "Empty query.", "place": None}

    params: dict[str, str | int] = {"q": text, "format": "json", "limit": 1}
    email = os.environ.get("NOMINATIM_EMAIL")
    if email:
        params["email"] = email

    _throttle()
    resp = requests.get(
        NOMINATIM_SEARCH_URL,
        params=params,
        headers={"User-Agent": _USER_AGENT},
        timeout=timeout_s,
    )
    resp.raise_for_status()
    hits = resp.json()
    if not isinstance(hits, list) or not hits:
        return {"status": "no_results", "reason": "Nominatim returned no candidates.", "place": None}

    hit = hits[0]
    if not isinstance(hit, dict):
        return {"status": "no_results", "reason": "Nominatim returned no candidates.", "place": None}
    try:
        lon = float(hit["lon"])
        lat = float(hit["lat"])
    except (KeyError, TypeError, ValueError):
        return {"status": "no_results", "reason": "Nominatim hit had no lon/lat.", "place": None}

    label = hit.get("display_name") or hit.get("name") or text
    place: dict[str, Any] = {
        "label": str(label),
        "lon": lon,
        "lat": lat,
        "bbox": _bbox_from_nominatim(hit.get("boundingbox")),
    }
    if hit.get("osm_type") is not None:
        place["osm_type"] = hit["osm_type"]
    if hit.get("osm_id") is not None:
        place["osm_id"] = hit["osm_id"]
    return {"status": "ok", "place": place}
