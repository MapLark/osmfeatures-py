"""Request body helpers for MapLark geo-agent ``/v1/*`` endpoints."""

from __future__ import annotations

from typing import Any, Literal

RouteTravelMode = Literal["WALK", "BICYCLE"]

PLACES_SEARCH_PATH = "/v1/places/search"
PLACES_NEARBY_PATH = "/v1/places/nearby"
ROUTES_ISOCHRONE_PATH = "/v1/routes/isochrone"
ROUTES_PATH_PATH = "/v1/routes/path"
ROUTES_OPTIMIZED_PATH_PATH = "/v1/routes/optimized_path"

_PLACE_TYPES = frozenset({"node", "way", "relation"})


def parse_place_ref(osm_type: str, osm_id: int | str | None = None) -> tuple[str, int]:
    """``("node", 123)`` or a search feature id ``"node/123"`` as *osm_type* alone."""
    if osm_id is None:
        raw = (osm_type or "").strip()
        if "/" not in raw:
            raise ValueError("place id must be node|way|relation plus a positive osm_id")
        kind, _, rest = raw.partition("/")
        return parse_place_ref(kind, rest)
    kind = (osm_type or "").strip().lower()
    if kind not in _PLACE_TYPES:
        raise ValueError("osm_type must be node, way, or relation")
    try:
        n = int(str(osm_id).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("osm_id must be a positive integer") from exc
    if n <= 0:
        raise ValueError("osm_id must be a positive integer")
    return kind, n


def places_details_path(osm_type: str, osm_id: int | str | None = None) -> str:
    kind, n = parse_place_ref(osm_type, osm_id)
    return f"/v1/places/{kind}/{n}"


def latlng(point: dict[str, float]) -> dict[str, float]:
    """Normalize to Places ``{lat, lng}`` (accepts ``lon`` or ``lng``)."""
    lng = point["lng"] if "lng" in point else point["lon"]
    return {"lat": float(point["lat"]), "lng": float(lng)}


def lonlat(point: dict[str, float]) -> dict[str, float]:
    """Normalize to routing ``{lon, lat}`` (accepts ``lon`` or ``lng``)."""
    lon = point["lon"] if "lon" in point else point["lng"]
    return {"lon": float(lon), "lat": float(point["lat"])}


def places_search_body(
    *,
    bbox: str | None = None,
    location: dict[str, float] | None = None,
    radius: float | None = None,
    type: str | None = None,  # noqa: A002
    tags: list[str] | None = None,
    or_tags: list[str] | None = None,
    limit: int | None = None,
    open_now: bool = False,
    as_of: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {}
    if bbox is not None:
        body["bbox"] = bbox
    if location is not None:
        body["location"] = latlng(location)
    if radius is not None:
        body["radius"] = radius
    if type is not None:
        body["type"] = type
    if tags:
        body["tags"] = list(tags)
    if or_tags:
        body["orTags"] = list(or_tags)
    if limit is not None:
        body["limit"] = limit
    if open_now:
        body["openNow"] = True
    if as_of is not None:
        body["asOf"] = as_of
    return body


def places_nearby_body(
    *,
    location: dict[str, float],
    radius: float | None = None,
    type: str | None = None,  # noqa: A002
    tags: list[str] | None = None,
    or_tags: list[str] | None = None,
    limit: int | None = None,
    open_now: bool = False,
    as_of: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"location": latlng(location)}
    if radius is not None:
        body["radius"] = radius
    if type is not None:
        body["type"] = type
    if tags:
        body["tags"] = list(tags)
    if or_tags:
        body["orTags"] = list(or_tags)
    if limit is not None:
        body["limit"] = limit
    if open_now:
        body["openNow"] = True
    if as_of is not None:
        body["asOf"] = as_of
    return body


def routes_isochrone_body(
    *,
    origin: dict[str, float],
    max_distance_m: float | None = None,
    duration_s: float | None = None,
    search_buffer_m: float | None = None,
    travel_mode: RouteTravelMode | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"origin": lonlat(origin)}
    if max_distance_m is not None:
        body["max_distance_m"] = max_distance_m
    if duration_s is not None:
        body["duration_s"] = duration_s
    if search_buffer_m is not None:
        body["search_buffer_m"] = search_buffer_m
    if travel_mode is not None:
        body["travelMode"] = travel_mode
    return body


def routes_path_body(
    *,
    stops: list[dict[str, float]],
    search_buffer_m: float | None = None,
    travel_mode: RouteTravelMode | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"stops": [lonlat(s) for s in stops]}
    if search_buffer_m is not None:
        body["search_buffer_m"] = search_buffer_m
    if travel_mode is not None:
        body["travelMode"] = travel_mode
    return body


def routes_optimized_path_body(
    *,
    start: dict[str, float],
    stops: list[dict[str, float]],
    search_buffer_m: float | None = None,
    loop: bool | None = None,
    travel_mode: RouteTravelMode | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "start": lonlat(start),
        "stops": [lonlat(s) for s in stops],
    }
    if loop is not None:
        body["loop"] = loop
    if search_buffer_m is not None:
        body["search_buffer_m"] = search_buffer_m
    if travel_mode is not None:
        body["travelMode"] = travel_mode
    return body

