"""
App idea: deterministic geo-agent tool chains (no LLM planner).

Scenario 1 — "find a bar crawl in Södermalm in Stockholm"
  1. hardcoded Södermalm origin (geocode skipped until Photon is wired)
  2. client.places_search      — bars/pubs (optional openNow+asOf for evening)
  3. client.routes_optimized_path — walkable loop through 10 stops

Scenario 2 — "bike through parks in Stockholm with a cafe open at 3pm"
  1. hardcoded Djurgården origin (geocode skipped until Photon is wired)
  2. client.places_nearby      — cafes/restaurants/bars openNow at 15:00, nearest first
  3. client.routes_optimized_path — bicycle loop through the stops
  4. client.query              — park polygons; assert route crosses one

Scenario 3 — filter then search (isochrone)
  "cafes open at 8pm within a 10-minute bike ride of my hotel"
  1. hardcoded hotel / Södermalm anchor
  2. client.routes_isochrone   — 10 min BICYCLE reach polygon
  3. client.places_search      — cafes openNow at 20:00 local in a covering radius
  4. Client filter             — keep only points inside the polygon

Scenario 4 — compare locations (isochrone)
  "is the office within a 20-minute walk of the apartment?"
  1. client.routes_isochrone   — 20 min WALK from apartment
  2. Point-in-polygon          — near office inside, far point outside

Scenario 5 — site / coverage (isochrone)
  "how far can I get on foot from this park entrance in 1 km on the network?"
  1. hardcoded Djurgården / park entrance
  2. client.routes_isochrone   — max_distance_m=1000 WALK
  3. Assert Polygon + distance/duration contract

Scenario 6 — client-side openAfter
  "bars open at 20:00"
  1. Pin `asOf` to that local clock (client picks the IANA zone)
  2. client.places_search      — no openNow; hours annotated as of `asOf`
  3. Keep features with openNow true
  4. If short of N open hits, raise limit and search again

Uses the osmfeatures SDK (no raw HTTP). Requires MAPLARK_API_KEY and Stockholm
data with ``node_ids`` populated for network routing.
"""

from __future__ import annotations

import math
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from haversine import Unit, haversine

from osmfeatures import OSMFeaturesAPIError, OSMFeaturesClient, point_in_geometry
from osmfeatures.chunking import (
    around_to_bbox,
    merge_features,
    split_bbox_tiles,
    tile_count_for_corridor,
)

# Hardcoded Stockholm anchors. Restore client.geocode via _resolve_place when Photon
# is actually served (public photon.komoot.io 403s / times out from this host).
_SODERMALM_BBOX = "18.055,59.308,18.095,59.325"
_SODERMALM_CENTER = {"lon": 18.075, "lat": 59.316}
_WALK_RADIUS_M = 1200
_TARGET_BARS = 10

# Hardcoded Djurgården (same reason as Södermalm).
_DJURGARDEN_BBOX = "18.085,59.318,18.125,59.335"
_DJURGARDEN_CENTER = {"lon": 18.105, "lat": 59.3265}
_BIKE_RADIUS_M = 1500
_TARGET_STOPS = 3
_REFRESHMENT_AMENITIES = ("cafe", "restaurant", "bar")
# Low zoom = server-side geometry simplification — fewer vertices per park.
_PARK_ZOOM = 15
# Client cap. v3 does not order by osm_id; keep this high enough for the corridor.
_PARK_LIMIT = 200
# Documented /v1/routes/* duration→distance conversion.
_WALK_SPEED_M_PER_S = 1.4  # ~5 km/h
_BICYCLE_SPEED_M_PER_S = 4.2  # ~15 km/h

# Compare-locations anchors (Södermalm apartment vs nearby / far points).
_APARTMENT = {"lon": 18.075, "lat": 59.316}
_NEAR_OFFICE = {"lon": 18.080, "lat": 59.318}  # ~400 m straight-line
_FAR_POINT = {"lon": 18.050, "lat": 59.350}  # ~4 km north — outside a 20 min walk


def _at_clock(hour: int, minute: int = 0) -> str:
    """ISO instant for today at this clock in Europe/Stockholm."""
    now = datetime.now(ZoneInfo("Europe/Stockholm"))
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0).isoformat()


def _is_open(feat: dict) -> bool:
    return (feat.get("properties") or {}).get("openNow") is True


def _search_open_at_clock(
    client: OSMFeaturesClient,
    *,
    location: dict[str, float],
    radius: float,
    or_tags: list[str],
    hour: int,
    want: int,
    page: int = 30,
    max_limit: int = 200,
) -> list[dict]:
    """Client-side openAfter: pin `asOf`, filter known-open, raise limit if the page is short."""
    as_of = _at_clock(hour)
    limit = page
    while True:
        fc = client.places_search(
            location=location,
            radius=radius,
            or_tags=or_tags,
            as_of=as_of,
            limit=limit,
        )
        feats = fc.get("features") or []
        opened = [f for f in feats if _is_open(f)]
        if len(opened) >= want or len(feats) < limit or limit >= max_limit:
            return opened[:want]
        limit = min(max(limit * 2, want), max_limit)


def _point_from_feature(feat: dict) -> dict[str, float] | None:
    geom = feat.get("geometry") or {}
    if geom.get("type") != "Point":
        return None
    coords = geom.get("coordinates") or []
    if len(coords) < 2:
        return None
    lon, lat = float(coords[0]), float(coords[1])
    if not (math.isfinite(lon) and math.isfinite(lat)):
        return None
    return {"lon": lon, "lat": lat}


def _haversine_m(a: dict[str, float], b: dict[str, float]) -> float:
    return float(haversine((a["lat"], a["lon"]), (b["lat"], b["lon"]), unit=Unit.METERS))


def _iter_exterior_rings(geom: dict):
    gtype = geom.get("type")
    coords = geom.get("coordinates") or []
    if gtype == "Polygon":
        if coords:
            yield coords[0]
    elif gtype == "MultiPolygon":
        for poly in coords:
            if poly:
                yield poly[0]


def _geometry_vertex_count(geom: dict) -> int:
    """Count distinct ring vertices (closed rings drop the repeated first point)."""
    n = 0
    for ring in _iter_exterior_rings(geom):
        n += max(0, len(ring) - 1) if len(ring) >= 2 and ring[0] == ring[-1] else len(ring)
    return n


def _fetch_parks(
    client: OSMFeaturesClient, origin: dict[str, float], radius_m: float
) -> list[dict]:
    """Fetch park polygons via /v3/osm_features, tiled to the key's tagged bbox cap."""
    corridor = around_to_bbox(origin["lon"], origin["lat"], radius_m)
    # Free tagged bbox cap. /v1/tiers if the caller needs their own max.
    tiles = split_bbox_tiles(corridor, tile_count_for_corridor(corridor, 0.04))

    feature_lists: list[list[dict]] = []
    for tile in tiles:
        page = client.query(
            bbox=tile,
            tags="leisure=park",
            type=["way", "relation"],
            way_shape="polygon",
            zoom=_PARK_ZOOM,
            max_features=_PARK_LIMIT,
            clip_geometry=False,
        )
        feature_lists.append(list(page["features"]))

    return merge_features(feature_lists)


def _bikeable_loop(
    client: OSMFeaturesClient,
    candidates: list[dict[str, float]],
    target: int,
    *,
    search_buffer_m: float,
) -> tuple[dict, list[dict[str, float]]]:
    """Try consecutive windows of *target* candidates until routes_optimized_path is ok."""
    route: dict = {}
    stops: list[dict[str, float]] = []
    for start in range(0, max(1, len(candidates) - target + 1)):
        stops = candidates[start : start + target]
        if len(stops) < target:
            break
        try:
            route = client.routes_optimized_path(
                start=stops[0],
                stops=stops[1:],
                search_buffer_m=search_buffer_m,
                travel_mode="BICYCLE",
            )
        except OSMFeaturesAPIError as exc:
            route = {"reason": str(exc)[:300]}
            continue
        if route.get("status") == "ok":
            return route, stops
    return route, stops


def test_bar_crawl_sodermalm_stockholm(client: OSMFeaturesClient):
    """Hardcoded tool chain for a Södermalm bar crawl — 10 bars, walkable loop."""
    bbox, origin = _SODERMALM_BBOX, dict(_SODERMALM_CENTER)

    location = {"lat": origin["lat"], "lng": origin["lon"]}
    search = client.places_search(
        location=location,
        radius=_WALK_RADIUS_M,
        or_tags=["amenity=bar", "amenity=pub"],
        limit=30,
    )
    features = search.get("features") or []
    assert len(features) >= _TARGET_BARS, (
        f"Expected at least {_TARGET_BARS} bars/pubs near Södermalm, got {len(features)}. "
        f"location={location} radius={_WALK_RADIUS_M} bbox_fallback={bbox}"
    )

    evening = client.places_search(
        location=location,
        radius=_WALK_RADIUS_M,
        or_tags=["amenity=bar", "amenity=pub"],
        limit=30,
        open_now=True,
        as_of=_at_clock(18),
    )
    evening_feats = evening.get("features") or []

    picked: list[dict] = []
    seen_ids: set[str] = set()
    for pool in (evening_feats, features):
        for feat in pool:
            fid = str(feat.get("id") or "")
            if fid in seen_ids:
                continue
            if _point_from_feature(feat) is None:
                continue
            amenity = ((feat.get("properties") or {}).get("tags") or {}).get("amenity")
            if amenity not in ("bar", "pub"):
                continue
            seen_ids.add(fid)
            picked.append(feat)
            if len(picked) >= _TARGET_BARS:
                break
        if len(picked) >= _TARGET_BARS:
            break

    assert len(picked) >= _TARGET_BARS, (
        f"Could not collect {_TARGET_BARS} point bar/pub features, got {len(picked)}"
    )
    stops = [_point_from_feature(f) for f in picked[:_TARGET_BARS]]
    assert all(s is not None for s in stops)

    for stop in stops:
        dist = _haversine_m(origin, stop)  # type: ignore[arg-type]
        assert dist <= _WALK_RADIUS_M + 50, (
            f"Stop {stop} is {dist:.0f}m from origin (limit {_WALK_RADIUS_M}m)"
        )

    route = client.routes_optimized_path(
        start=origin, stops=stops, search_buffer_m=800  # type: ignore[arg-type]
    )
    assert route.get("status") == "ok", (
        f"Expected walkable loop via node_ids graph, got {route.get('status')}: "
        f"{route.get('reason')}"
    )
    assert route.get("geometry", {}).get("type") == "LineString"
    coords = route["geometry"]["coordinates"]
    assert len(coords) >= 2
    assert route.get("distance_m") is not None and route["distance_m"] > 0
    assert len(route.get("ordered_stops") or []) == _TARGET_BARS + 1
    assert route["distance_m"] < 20_000, (
        f"Loop distance {route['distance_m']:.0f}m looks too long for a bar crawl"
    )
    print(f"[bar crawl] {len(stops)} stops, {route['distance_m']:.0f}m loop")


def test_bike_parks_cafe_stockholm(client: OSMFeaturesClient):
    """Djurgården bike ride — afternoon refreshment stops, park traversal."""
    bbox, origin = _DJURGARDEN_BBOX, dict(_DJURGARDEN_CENTER)

    afternoon = client.places_nearby(
        location={"lat": origin["lat"], "lng": origin["lon"]},
        radius=_BIKE_RADIUS_M,
        limit=50,
        or_tags=[f"amenity={a}" for a in _REFRESHMENT_AMENITIES],
        open_now=True,
        as_of=_at_clock(15),
    )
    features = [item["feature"] for item in (afternoon.get("items") or []) if item.get("feature")]
    candidates = [
        f
        for f in features
        if (f.get("properties") or {}).get("tags", {}).get("amenity") in _REFRESHMENT_AMENITIES
        and _point_from_feature(f) is not None
    ]
    assert len(candidates) >= _TARGET_STOPS, (
        f"Expected at least {_TARGET_STOPS} cafe/restaurant/bar open after 15:00 near Djurgården, "
        f"got {len(candidates)}. location={origin} radius={_BIKE_RADIUS_M} bbox_fallback={bbox}"
    )
    candidate_points = [_point_from_feature(f) for f in candidates]
    assert all(p is not None for p in candidate_points)

    # ponytail: keep buffer bike-scale so tiled highway fetches stay complete.
    route, stops = _bikeable_loop(
        client,
        candidate_points,  # type: ignore[arg-type]
        _TARGET_STOPS,
        search_buffer_m=800.0,
    )
    assert route.get("status") == "ok", (
        f"Expected bikeable loop via node_ids graph, got {route.get('status')}: "
        f"{route.get('reason')}"
    )
    assert route.get("geometry", {}).get("type") == "LineString"
    coords = route["geometry"]["coordinates"]
    assert len(coords) >= 2
    assert route.get("distance_m") is not None and route["distance_m"] > 0
    assert len(route.get("ordered_stops") or []) == len(stops)
    assert route["distance_m"] < 30_000, (
        f"Loop distance {route['distance_m']:.0f}m looks too long for a park bike ride"
    )
    expected_duration_s = route["distance_m"] / _BICYCLE_SPEED_M_PER_S
    assert route["duration_s"] == pytest.approx(expected_duration_s, rel=1e-6)

    parks = _fetch_parks(client, origin, _BIKE_RADIUS_M)
    assert parks, f"Expected at least one park near Djurgården, got 0. bbox_fallback={bbox}"
    route_through_park = any(
        point_in_geometry(lon, lat, feat["geometry"])
        for lon, lat in coords
        for feat in parks
    )
    assert route_through_park, (
        f"Bike loop never passes through any of the {len(parks)} parks found near Djurgården"
    )
    print(
        f"[bike parks] {len(stops)} stops, {route['distance_m']:.0f}m loop, "
        f"{len(parks)} parks checked"
    )


def test_filter_then_search_bike_isochrone_stockholm(client: OSMFeaturesClient):
    """Cafes open after 8pm inside a 10-minute bike isochrone from the hotel."""
    hotel = dict(_SODERMALM_CENTER)
    duration_s = 10 * 60
    budget_m = duration_s * _BICYCLE_SPEED_M_PER_S

    iso = client.routes_isochrone(
        origin=hotel,
        travel_mode="BICYCLE",
        duration_s=duration_s,
        search_buffer_m=budget_m,
    )
    assert iso.get("status") == "ok", (
        f"Expected bike isochrone, got {iso.get('status')}: {iso.get('reason')}"
    )
    geom = iso.get("geometry") or {}
    assert geom.get("type") in ("Polygon", "MultiPolygon")
    assert iso.get("distance_m") == pytest.approx(budget_m, rel=1e-6)
    assert iso.get("duration_s") == pytest.approx(duration_s, rel=1e-6)

    search = client.places_search(
        location={"lat": hotel["lat"], "lng": hotel["lon"]},
        radius=budget_m,
        or_tags=["amenity=cafe", "amenity=restaurant"],
        open_now=True,
        as_of=_at_clock(20),
        limit=50,
    )
    candidates = [
        f
        for f in (search.get("features") or [])
        if (f.get("properties") or {}).get("tags", {}).get("amenity") in ("cafe", "restaurant")
        and _point_from_feature(f) is not None
    ]
    inside = []
    for f in candidates:
        pt = _point_from_feature(f)
        assert pt is not None
        if point_in_geometry(pt["lon"], pt["lat"], geom):
            inside.append(f)
    assert inside, (
        f"Expected at least one cafe/restaurant open after 20:00 inside the "
        f"{budget_m:.0f}m bike isochrone; candidates={len(candidates)} hotel={hotel}"
    )
    print(
        f"[filter+search] hotel={hotel} isochrone={budget_m:.0f}m bike, "
        f"{len(inside)}/{len(candidates)} open-after-20:00 inside polygon"
    )


def test_compare_locations_walk_isochrone_stockholm(client: OSMFeaturesClient):
    """Is the office within a 20-minute walk of the apartment?"""
    duration_s = 20 * 60
    budget_m = duration_s * _WALK_SPEED_M_PER_S

    iso = client.routes_isochrone(
        origin=_APARTMENT,
        travel_mode="WALK",
        duration_s=duration_s,
        search_buffer_m=budget_m,
    )
    assert iso.get("status") == "ok", (
        f"Expected walk isochrone, got {iso.get('status')}: {iso.get('reason')}"
    )
    geom = iso.get("geometry") or {}
    assert geom.get("type") in ("Polygon", "MultiPolygon")
    assert iso.get("distance_m") == pytest.approx(budget_m, rel=1e-6)
    assert iso.get("duration_s") == pytest.approx(duration_s, rel=1e-6)

    near_inside = point_in_geometry(_NEAR_OFFICE["lon"], _NEAR_OFFICE["lat"], geom)
    far_inside = point_in_geometry(_FAR_POINT["lon"], _FAR_POINT["lat"], geom)
    assert near_inside, (
        f"Expected nearby office {_NEAR_OFFICE} inside 20 min walk isochrone "
        f"from {_APARTMENT} (~{budget_m:.0f}m network)"
    )
    assert not far_inside, (
        f"Expected far point {_FAR_POINT} outside 20 min walk isochrone from {_APARTMENT}"
    )
    print(
        f"[compare] apartment={_APARTMENT} budget={budget_m:.0f}m walk: "
        f"near_office inside={near_inside}, far outside={not far_inside}"
    )


def test_site_coverage_walk_isochrone_stockholm(client: OSMFeaturesClient):
    """Coverage from a park entrance: how far on foot within 1 km on the network."""
    entrance = dict(_DJURGARDEN_CENTER)
    max_distance_m = 1000.0

    iso = client.routes_isochrone(
        origin=entrance,
        travel_mode="WALK",
        max_distance_m=max_distance_m,
        search_buffer_m=max_distance_m,
    )
    assert iso.get("status") == "ok", (
        f"Expected walk coverage isochrone, got {iso.get('status')}: {iso.get('reason')}"
    )
    geom = iso.get("geometry") or {}
    assert geom.get("type") in ("Polygon", "MultiPolygon")
    assert iso.get("distance_m") == pytest.approx(max_distance_m, rel=1e-6)
    assert iso.get("duration_s") == pytest.approx(
        max_distance_m / _WALK_SPEED_M_PER_S, rel=1e-6
    )
    n_verts = _geometry_vertex_count(geom)
    assert n_verts >= 4, f"Expected a closed polygon, got {n_verts} vertices"
    assert point_in_geometry(entrance["lon"], entrance["lat"], geom), (
        "Park entrance should lie inside its own coverage polygon"
    )
    print(
        f"[coverage] entrance={entrance} walk {max_distance_m:.0f}m → "
        f"{n_verts} vertices, duration_s={iso['duration_s']:.0f}"
    )


def test_client_open_after_clock_stockholm(client: OSMFeaturesClient):
    """Bars open at 20:00 local: search with `asOf`, keep known-open, raise limit if short."""
    location = {"lat": _SODERMALM_CENTER["lat"], "lng": _SODERMALM_CENTER["lon"]}
    want = 5
    opened = _search_open_at_clock(
        client,
        location=location,
        radius=_WALK_RADIUS_M,
        or_tags=["amenity=bar", "amenity=pub"],
        hour=20,
        want=want,
    )
    assert len(opened) >= want, (
        f"Expected at least {want} bars/pubs open at 20:00 local near Södermalm, "
        f"got {len(opened)}. location={location} radius={_WALK_RADIUS_M}"
    )
    assert all(_is_open(f) for f in opened)
    assert all(_point_from_feature(f) is not None for f in opened)
    print(f"[open after] {len(opened)} bars/pubs open at 20:00 local")
