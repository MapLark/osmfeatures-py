"""
App idea: ``auto_split`` for two different overflows.

1. Huge bbox — cafes across greater Stockholm (window larger than the
   tagged area cap). Count is small; the 400 names the deg² limit and the
   client jumps to tiles that fit, then merges. It must not probe every
   quarter with 400s (the original county-search thrash).

2. Dense window — building polygons in inner Stockholm (window small
   enough for the area cap). Count can exceed the key's ``max_limit``
   (from ``GET /v1/account/tier``); the client counts first, jumps to a
   power-of-2 grid (one extra
   jump if a leftover leaf is still dense), then merges. A lower ``limit`` is a page size and does not split.

API calls: ``places_search(..., auto_split=True)`` and
``query(..., auto_split=True)``.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs, urlsplit

import requests

from osmfeatures import OSMFeature, OSMFeatureCollection, OSMFeaturesClient
from osmfeatures._split import parse_bbox_area_limit
from osmfeatures.chunking import bbox_area_deg2, parse_bbox, tile_count_for_max_area
from osmfeatures.models import OSMFeaturesAPIError

# ~0.125 deg². Free tagged bbox cap is 0.04, so this cannot be one search.
GREATER_STOCKHOLM_BBOX = "17.80,59.22,18.30,59.47"

# ~0.018 deg². Fits the tagged cap; building count can still exceed max_limit.
INNER_STOCKHOLM_BBOX = "17.95,59.30,18.15,59.38"


def _places_search_posts(resp: requests.Response) -> dict[str, Any] | None:
    url = str(resp.request.url)
    if "/v1/places/search" not in url:
        return None
    raw = resp.request.body or b"{}"
    if isinstance(raw, bytes):
        raw = raw.decode()
    body = json.loads(raw)
    return {
        "status": resp.status_code,
        "bbox": body.get("bbox"),
        "text": resp.text or "",
    }


def test_auto_split_huge_bbox_cafes(client: OSMFeaturesClient):
    """List cafes in a city-scale bbox that exceeds the tagged area cap."""
    counted = client.count(
        group_by="amenity",
        bbox=GREATER_STOCKHOLM_BBOX,
        tags=["amenity=cafe"],
        limit=1,
    )
    total = int(counted["total"])
    print(f"\n[huge bbox] {total} cafes in greater Stockholm (count, not a map)")

    posts: list[dict[str, Any]] = []

    def _track(resp: requests.Response, *_args: Any, **_kwargs: Any) -> None:
        row = _places_search_posts(resp)
        if row is not None:
            posts.append(row)

    client._session.hooks.setdefault("response", []).append(_track)
    try:
        data = client.places_search(
            bbox=GREATER_STOCKHOLM_BBOX,
            tags=["amenity=cafe"],
            limit=10_000,
            auto_split=True,
            timeout=120,
        )
    finally:
        client._session.hooks["response"].remove(_track)

    area_400s = [
        p for p in posts
        if p["status"] == 400 and "bbox area" in p["text"].lower()
    ]
    ok = [p for p in posts if 200 <= p["status"] < 300]
    print(
        f"[huge bbox] places_search POSTs: {len(posts)} "
        f"({len(area_400s)} area-400, {len(ok)} ok)"
    )
    # Probe at most once, then jump. The old walk 400'd every quarter.
    assert len(area_400s) <= 1, (
        f"bbox-area thrash: {len(area_400s)} failed POSTs before fitting "
        f"(want 0 if the bbox already fits, else 1 jump). statuses="
        f"{[p['status'] for p in posts]}"
    )
    if area_400s:
        assert posts[0] is area_400s[0], (
            "area 400 must be the first search POST, not a later probe"
        )
        assert all(p["status"] == 200 for p in posts[1:]), (
            f"search POSTs after the cap 400 must succeed, got "
            f"{[p['status'] for p in posts[1:]]}"
        )
        limit = parse_bbox_area_limit(
            OSMFeaturesAPIError(area_400s[0]["text"], 400)
        )
        assert limit is not None
        n = tile_count_for_max_area(GREATER_STOCKHOLM_BBOX, limit)
        assert len(ok) == n, (
            f"expected one jump to {n} tiles for limit {limit}, got {len(ok)}"
        )
        for p in ok:
            area = bbox_area_deg2(p["bbox"])
            assert area <= limit + 1e-9, (
                f"leaf {p['bbox']} area {area} still exceeds cap {limit}"
            )
    else:
        assert len(posts) == 1 and posts[0]["status"] == 200

    assert data["type"] == "FeatureCollection"
    features = data["features"]
    assert len(features) > 0, "Expected cafes in greater Stockholm"

    named = []
    for f in features:
        tags = f.get("properties", {}).get("tags", {})
        assert tags.get("amenity") == "cafe"
        if tags.get("name"):
            named.append(tags["name"])

    assert len(named) > 0, "Expected at least some named cafes"
    print(f"[huge bbox] returned {len(features)} cafes, {len(named)} named")
    print(f"  Sample: {named[:5]}")


def _query_split_call(resp: requests.Response) -> dict[str, Any] | None:
    parts = urlsplit(str(resp.request.url))
    qs = parse_qs(parts.query)
    bbox = qs.get("bbox", [None])[0]
    if parts.path.endswith("/v2/osm_features/count"):
        total = None
        if resp.status_code == 200:
            total = int(resp.json()["total"])
        return {
            "kind": "count",
            "status": resp.status_code,
            "bbox": bbox,
            "total": total,
        }
    if parts.path.endswith("/v3/osm_features"):
        return {
            "kind": "query",
            "status": resp.status_code,
            "bbox": bbox,
            "text": resp.text or "",
        }
    return None


def test_auto_split_dense_buildings(client: OSMFeaturesClient):
    """Fetch building polygons where the match set can exceed max_limit."""
    counted = client.count(
        group_by="building",
        bbox=INNER_STOCKHOLM_BBOX,
        tags=["building"],
        type="way",
        way_shape="polygon",
        limit=1,
    )
    total = int(counted["total"])
    print(f"\n[density] {total} building polygons in inner Stockholm")

    calls: list[dict[str, Any]] = []

    def _track(resp: requests.Response, *_args: Any, **_kwargs: Any) -> None:
        row = _query_split_call(resp)
        if row is not None:
            calls.append(row)

    client._session.hooks.setdefault("response", []).append(_track)
    try:
        data = client.query(
            bbox=INNER_STOCKHOLM_BBOX,
            tags=["building"],
            type="way",
            way_shape="polygon",
            centroid=True,
            auto_split=True,
            timeout=120,
            # Page size so the example does not download every polygon.
            limit=3_000,
        )
    finally:
        client._session.hooks["response"].remove(_track)

    counts = [c for c in calls if c["kind"] == "count"]
    fetches = [c for c in calls if c["kind"] == "query"]
    too_large = [
        c for c in fetches
        if c["status"] == 400 and "result_too_large" in c["text"].lower()
    ]
    print(
        f"[density] query() HTTP: {len(counts)} count, {len(fetches)} feature "
        f"({len(too_large)} result_too_large)"
    )
    assert calls, "query() made no HTTP calls"
    assert calls[0]["kind"] == "count", (
        f"must count before fetch; first call was {calls[0]['kind']} "
        f"{calls[0]['status']}"
    )
    assert parse_bbox(calls[0]["bbox"]) == parse_bbox(INNER_STOCKHOLM_BBOX)
    assert too_large == [], (
        f"density thrash: {len(too_large)} result_too_large feature GETs "
        f"(count should split before fetch). statuses="
        f"{[(c['kind'], c['status']) for c in calls]}"
    )
    counted_keys: set[tuple[float, float, float, float]] = set()
    overflow_keys: set[tuple[float, float, float, float]] = set()
    max_limit = int(client.account_tier()["max_limit"])
    for c in calls:
        key = parse_bbox(c["bbox"]) if c["bbox"] else None
        if c["kind"] == "count" and c["status"] == 200 and key is not None:
            counted_keys.add(key)
            if c["total"] is not None and c["total"] > max_limit:
                overflow_keys.add(key)
            continue
        if c["kind"] != "query":
            continue
        assert key in counted_keys, f"fetched {c['bbox']} without counting first"
        assert key not in overflow_keys, (
            f"fetched overflow tile {c['bbox']} instead of splitting on count"
        )
    assert isinstance(data, OSMFeatureCollection)
    assert len(data["features"]) > 0, "Expected buildings in inner Stockholm"

    for f in data["features"]:
        assert isinstance(f, OSMFeature)
        assert f.osm_type == "way"
        assert "building" in f.tags
        assert f.centroid is not None

    fetched = len(data["features"])
    print(f"[density] query returned {fetched} buildings (limit=3000)")
    assert fetched > 0
    if total <= 3_000:
        assert fetched == total
    else:
        assert data.meta.has_more or fetched >= 3_000
