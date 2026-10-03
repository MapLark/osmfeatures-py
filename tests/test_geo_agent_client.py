"""Unit tests for geo-agent SDK methods (mocked HTTP)."""

from __future__ import annotations

import json

import pytest
import responses as rsps

from osmfeatures import OSMFeaturesClient
from osmfeatures._geo_agent import (
    normalize_travel_mode,
    parse_place_ref,
    places_details_path,
    routes_optimized_path_body,
)
from osmfeatures._places import _merge_place_collections
from osmfeatures.chunking import split_bbox_tiles, bbox_area_deg2, tile_count_for_max_area
from osmfeatures.models import OSMFeaturesAPIError
from tests.conftest import (
    ACCOUNT_TIER_URL,
    BASE_URL,
    FAKE_API_KEY,
    STATS_URL,
    add_account_tier_response,
)


@pytest.fixture
def client() -> OSMFeaturesClient:
    return OSMFeaturesClient(api_key=FAKE_API_KEY, base_url=BASE_URL, timeout=5.0)


@rsps.activate
def test_places_search_posts_camelcase_body(client: OSMFeaturesClient):
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json={"type": "FeatureCollection", "features": []},
    )
    out = client.places_search(
        location={"lat": 59.316, "lon": 18.075},
        radius=500,
        or_tags=["amenity=cafe"],
        open_now=True,
        as_of="2026-08-10T18:00:00+02:00",
        limit=10,
    )
    assert out["type"] == "FeatureCollection"
    body = json.loads(rsps.calls[0].request.body)
    assert body["location"] == {"lat": 59.316, "lng": 18.075}
    assert body["orTags"] == ["amenity=cafe"]
    assert body["openNow"] is True
    assert body["asOf"] == "2026-08-10T18:00:00+02:00"
    assert body["limit"] == 10


@rsps.activate
def test_routes_isochrone_and_optimized_path(client: OSMFeaturesClient):
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/routes/isochrone",
        json={"status": "ok", "geometry": {"type": "Polygon", "coordinates": []}, "distance_m": 500},
    )
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/routes/optimized_path",
        json={"status": "ok", "geometry": {"type": "LineString", "coordinates": [[18.0, 59.0], [18.1, 59.1]]}},
    )
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/routes/path",
        json={"status": "ok", "geometry": {"type": "LineString", "coordinates": [[18.0, 59.0], [18.1, 59.1]]}},
    )
    iso = client.routes_isochrone(origin={"lon": 18.075, "lat": 59.316}, max_distance_m=500)
    assert iso["status"] == "ok"
    opt = client.routes_optimized_path(
        start={"lon": 18.075, "lat": 59.316},
        stops=[{"lng": 18.08, "lat": 59.318}],
        travel_mode="BICYCLE",
    )
    assert opt["status"] == "ok"
    path = client.routes_path(
        stops=[{"lon": 18.075, "lat": 59.316}, {"lng": 18.08, "lat": 59.318}],
    )
    assert path["status"] == "ok"
    iso_body = json.loads(rsps.calls[0].request.body)
    assert "travelMode" not in iso_body
    assert iso_body["origin"] == {"lon": 18.075, "lat": 59.316}
    opt_body = json.loads(rsps.calls[1].request.body)
    assert opt_body["travelMode"] == "BICYCLE"
    assert opt_body["start"] == {"lon": 18.075, "lat": 59.316}
    assert opt_body["stops"][0] == {"lon": 18.08, "lat": 59.318}
    assert "loop" not in opt_body
    path_body = json.loads(rsps.calls[2].request.body)
    assert path_body["stops"][0] == {"lon": 18.075, "lat": 59.316}
    assert "travelMode" not in path_body


def test_normalize_travel_mode():
    assert normalize_travel_mode(None) is None
    assert normalize_travel_mode("walk") == "WALK"
    assert normalize_travel_mode("BICYCLE") == "BICYCLE"
    with pytest.raises(ValueError, match="WALK or BICYCLE"):
        normalize_travel_mode("drive")
    body = routes_optimized_path_body(
        start={"lon": 18.075, "lat": 59.316},
        stops=[{"lng": 18.08, "lat": 59.318}],
        travel_mode="walk",
    )
    assert body["travelMode"] == "WALK"


@rsps.activate
def test_usage(client: OSMFeaturesClient):
    rsps.add(
        rsps.GET,
        f"{BASE_URL}/v1/usage",
        json={"tier": "standard", "usage_this_month": 1},
    )
    out = client.usage()
    assert out["tier"] == "standard"
    assert rsps.calls[0].request.url.endswith("/v1/usage")


@rsps.activate
def test_places_nearby(client: OSMFeaturesClient):
    rsps.add(rsps.POST, f"{BASE_URL}/v1/places/nearby", json={"status": "ok", "items": []})
    nearby = client.places_nearby(
        location={"lat": 59.3, "lng": 18.0},
        type="cafe",
        limit=3,
        open_now=True,
        as_of="2026-08-10T18:00:00+02:00",
    )
    assert nearby["status"] == "ok"
    assert "/v1/places/nearby" in rsps.calls[0].request.url
    body = json.loads(rsps.calls[0].request.body)
    assert body["openNow"] is True
    assert body["asOf"] == "2026-08-10T18:00:00+02:00"
    assert body["limit"] == 3
    assert "radius" not in body


@rsps.activate
def test_places_and_routes_omit_api_defaults(client: OSMFeaturesClient):
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json={"type": "FeatureCollection", "features": []},
    )
    rsps.add(rsps.POST, f"{BASE_URL}/v1/places/nearby", json={"status": "ok", "items": []})
    client.places_search(bbox="18.05,59.31,18.10,59.33", or_tags=["amenity=cafe"])
    search_body = json.loads(rsps.calls[0].request.body)
    assert "limit" not in search_body
    assert "openNow" not in search_body
    client.places_nearby(location={"lat": 59.3, "lng": 18.0}, or_tags=["amenity=cafe"])
    nearby_body = json.loads(rsps.calls[1].request.body)
    assert "limit" not in nearby_body
    assert "radius" not in nearby_body


def _places_search_bboxes() -> list[str]:
    out: list[str] = []
    for call in rsps.calls:
        if "/v1/places/search" not in call.request.url:
            continue
        body = json.loads(call.request.body)
        out.append(body["bbox"])
    return out


def _bbox_too_large(*, area: str = "4.000000", limit: str = "1.000000") -> dict[str, object]:
    return {
        "error": "bad_request",
        "detail": (
            f"bbox area {area} exceeds the tagged tier limit {limit} "
            "for the 'free' tier. Add tag filters to unlock a larger bbox, "
            "or upgrade your tier."
        ),
        "status_code": 400,
    }


def _count_payload(total: int) -> str:
    return json.dumps({"groups": [], "total": total, "truncated": False})


def _features_count_always(
    total: int = 1,
    *,
    account_tier: bool = True,
) -> None:
    if account_tier:
        add_account_tier_response()
    payload = _count_payload(total)

    def _cb(request):  # noqa: ARG001
        return (200, {}, payload)

    rsps.add_callback(
        rsps.GET, STATS_URL, callback=_cb, content_type="application/json"
    )


def _features_count_urls() -> list[str]:
    return [c.request.url for c in rsps.calls if "/v2/osm_features/count" in c.request.url]


def _search_bodies() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for call in rsps.calls:
        if "/v1/places/search" not in call.request.url:
            continue
        out.append(json.loads(call.request.body))
    return out


@rsps.activate
def test_places_search_bbox_tiles_splits_upfront(client: OSMFeaturesClient):
    bbox = "0,0,2,1"
    tiles = split_bbox_tiles(bbox, 2)
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json={
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "id": "node/1", "geometry": None, "properties": {}}
            ],
            "metadata": {"units": 2, "evaluated_at": "2026-08-10T16:00:00Z"},
        },
    )
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json={
            "type": "FeatureCollection",
            "features": [
                {"type": "Feature", "id": "node/2", "geometry": None, "properties": {}}
            ],
            "metadata": {"units": 7, "evaluated_at": "2026-08-10T16:00:01Z"},
        },
    )
    out = client.places_search(bbox=bbox, or_tags=["amenity=cafe"], bbox_tiles=2)
    assert [f["id"] for f in out["features"]] == ["node/1", "node/2"]
    assert _places_search_bboxes() == tiles
    assert out["metadata"]["units"] == 9
    assert out["metadata"]["evaluated_at"] == "2026-08-10T16:00:00Z"


def test_places_search_bbox_tiles_requires_bbox(client: OSMFeaturesClient):
    with pytest.raises(ValueError, match="bbox_tiles only applies to bbox search"):
        client.places_search(
            location={"lat": 59.3, "lng": 18.0},
            radius=500,
            or_tags=["amenity=cafe"],
            bbox_tiles=2,
        )


def test_places_search_auto_split_requires_bbox(client: OSMFeaturesClient):
    with pytest.raises(ValueError, match="auto_split only applies to bbox search"):
        client.places_search(
            location={"lat": 59.3, "lng": 18.0},
            radius=500,
            or_tags=["amenity=cafe"],
            auto_split=True,
        )


@rsps.activate
def test_places_search_area_overflow_is_the_api_error(client: OSMFeaturesClient):
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json=_bbox_too_large(),
        status=400,
    )
    with pytest.raises(OSMFeaturesAPIError, match="bbox area") as exc:
        client.places_search(bbox="0,0,2,2", or_tags=["amenity=cafe"])
    assert "auto_split" not in str(exc.value)
    assert len(rsps.calls) == 1


@rsps.activate
def test_places_search_auto_split_counts_then_quarters_area_overflow(
    client: OSMFeaturesClient,
):
    bbox = "0,0,2,2"
    quarters = split_bbox_tiles(bbox, 4)
    _features_count_always(1)
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json=_bbox_too_large(),
        status=400,
    )
    for i in range(4):
        rsps.add(
            rsps.POST,
            f"{BASE_URL}/v1/places/search",
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": f"node/{i}",
                        "geometry": None,
                        "properties": {},
                    }
                ],
            },
        )
    out = client.places_search(
        bbox=bbox, or_tags=["amenity=cafe"], auto_split=True
    )
    assert {f["id"] for f in out["features"]} == {"node/0", "node/1", "node/2", "node/3"}
    assert _places_search_bboxes() == [bbox, *quarters]
    assert _features_count_urls()
    assert all("/v1/places/count" not in c.request.url for c in rsps.calls)
    assert any(c.request.url.endswith("/v1/account/tier") for c in rsps.calls)


@rsps.activate
def test_places_search_auto_split_does_not_jump_on_count_window(
    client: OSMFeaturesClient,
):
    add_account_tier_response()
    rsps.add(
        rsps.GET,
        STATS_URL,
        json={
            "error": "bad_request",
            "detail": (
                "count bbox area 500.000000 exceeds the tagged tier limit "
                "400.000000 for the 'free' tier."
            ),
            "status_code": 400,
        },
        status=400,
    )
    with pytest.raises(OSMFeaturesAPIError, match="count bbox area") as exc:
        client.places_search(
            bbox="0,0,30,20", or_tags=["amenity=cafe"], auto_split=True
        )
    assert "auto_split" not in str(exc.value)
    assert _places_search_bboxes() == []


@rsps.activate
def test_places_search_auto_split_keeps_page_limit(
    client: OSMFeaturesClient,
):
    _features_count_always(59)
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "node/1",
                    "geometry": None,
                    "properties": {},
                }
            ],
        },
    )
    out = client.places_search(
        bbox="0,0,1,1",
        or_tags=["amenity=cafe"],
        limit=40,
        auto_split=True,
    )
    assert [f["id"] for f in out["features"]] == ["node/1"]
    assert _places_search_bboxes() == ["0,0,1,1"]
    assert _search_bodies()[0]["limit"] == 40


@rsps.activate
def test_places_search_auto_split_jumps_area_without_probe_tree(
    client: OSMFeaturesClient,
):
    bbox = "0,0,1,1"
    max_area = 0.1
    leaves = split_bbox_tiles(bbox, tile_count_for_max_area(bbox, max_area))
    assert len(leaves) == 16
    _features_count_always(1)
    post_400s = {"n": 0}

    def _search(request):
        body = json.loads(request.body)
        area = bbox_area_deg2(body["bbox"])
        if area > max_area + 1e-9:
            post_400s["n"] += 1
            return (
                400,
                {},
                json.dumps(_bbox_too_large(area=f"{area:.6f}", limit=f"{max_area:.6f}")),
            )
        fid = body["bbox"].replace(",", "_")
        return (
            200,
            {},
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "id": f"node/{fid}",
                            "geometry": None,
                            "properties": {},
                        }
                    ],
                }
            ),
        )

    rsps.add_callback(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        callback=_search,
        content_type="application/json",
    )
    out = client.places_search(
        bbox=bbox, or_tags=["amenity=cafe"], auto_split=True
    )
    assert post_400s["n"] == 1
    assert _places_search_bboxes() == [bbox, *leaves]
    assert len(out["features"]) == 16


@rsps.activate
def test_places_search_auto_split_dedupes_shared_edges(client: OSMFeaturesClient):
    bbox = "0,0,2,1"
    tiles = split_bbox_tiles(bbox, 2)
    feat = {
        "type": "Feature",
        "id": "node/1",
        "geometry": None,
        "properties": {},
    }
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json={"type": "FeatureCollection", "features": [feat]},
    )
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json={"type": "FeatureCollection", "features": [feat]},
    )
    out = client.places_search(bbox=bbox, or_tags=["amenity=cafe"], bbox_tiles=2)
    assert [f["id"] for f in out["features"]] == ["node/1"]
    assert _places_search_bboxes() == tiles


def test_merge_place_collections_sums_tile_metadata():
    out = _merge_place_collections(
        [
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "node/1",
                        "geometry": None,
                        "properties": {},
                    }
                ],
                "metadata": {
                    "units": 3,
                    "evaluated_at": "2026-08-10T16:00:00Z",
                    "hint": "tile 0",
                    "input_count": 4,
                    "kept_count": 1,
                },
            },
            {
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": "node/2",
                        "geometry": None,
                        "properties": {},
                    }
                ],
                "metadata": {
                    "units": 5,
                    "evaluated_at": "2026-08-10T16:00:01Z",
                    "input_count": 6,
                    "kept_count": 2,
                    "hours_parse_capped": True,
                    "hours_parsed": 10000,
                },
            },
        ]
    )
    assert [f["id"] for f in out["features"]] == ["node/1", "node/2"]
    assert out["metadata"]["units"] == 8
    assert out["metadata"]["evaluated_at"] == "2026-08-10T16:00:00Z"
    assert out["metadata"]["input_count"] == 10
    assert out["metadata"]["kept_count"] == 3
    assert out["metadata"]["hours_parsed"] == 10000
    assert out["metadata"]["hours_parse_capped"] is True
    assert "hint" not in out["metadata"]


@rsps.activate
def test_count_scalar_places_set(client: OSMFeaturesClient):
    rsps.add(
        rsps.GET,
        STATS_URL,
        json={"groups": [], "total": 18420, "truncated": False},
    )
    out = client.count(
        bbox="18.05,59.31,18.10,59.33",
        or_tags=["amenity=cafe"],
        way_shape="polygon",
    )
    assert out["total"] == 18420
    assert out["groups"] == []
    url = rsps.calls[0].request.url
    assert "/v2/osm_features/count" in url
    assert "or_tags=amenity%3Dcafe" in url
    assert "way_shape=polygon" in url
    assert "group_by" not in url


@rsps.activate
def test_places_search_auto_split_hours_ands_opening_hours(
    client: OSMFeaturesClient,
):
    _features_count_always(100, account_tier=False)
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "node/1",
                    "geometry": None,
                    "properties": {},
                }
            ],
        },
    )
    out = client.places_search(
        bbox="0,0,1,1",
        or_tags=["amenity=cafe"],
        as_of="2026-08-10T18:00:00+02:00",
        auto_split=True,
    )
    assert [f["id"] for f in out["features"]] == ["node/1"]
    assert _places_search_bboxes() == ["0,0,1,1"]
    assert all("/v1/account/tier" not in c.request.url for c in rsps.calls)
    assert _search_bodies()[0]["asOf"] == "2026-08-10T18:00:00+02:00"
    count_url = _features_count_urls()[0]
    assert "tags=opening_hours" in count_url
    assert "way_shape=polygon" in count_url
    assert "or_tags=amenity%3Dcafe" in count_url


@rsps.activate
def test_places_search_auto_split_hours_page_limit_does_not_split(
    client: OSMFeaturesClient,
):
    """Omit limit (API default 100): 15k hours-tagged rows truncate, no 400."""
    bbox = "0,0,1,1"
    rsps.add(rsps.GET, STATS_URL, body=_count_payload(15_000))
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json={
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": "node/1",
                    "geometry": None,
                    "properties": {},
                }
            ],
        },
    )
    out = client.places_search(
        bbox=bbox,
        or_tags=["amenity=cafe"],
        as_of="2026-08-10T18:00:00+02:00",
        auto_split=True,
    )
    assert [f["id"] for f in out["features"]] == ["node/1"]
    assert _places_search_bboxes() == [bbox]
    assert all(c.request.url != ACCOUNT_TIER_URL for c in rsps.calls)


@rsps.activate
def test_places_search_auto_split_hours_keeps_parse_cap_when_limit_raised(
    client: OSMFeaturesClient,
):
    """Paid limit must not lift the 10k hours-parse split cap."""
    bbox = "0,0,1,1"
    tiles = split_bbox_tiles(bbox, 2)

    def _count(request):
        from urllib.parse import parse_qs, urlsplit

        tile = parse_qs(urlsplit(request.url).query)["bbox"][0]
        total = 15_000 if tile == bbox else 100
        return (200, {}, _count_payload(total))

    rsps.add_callback(
        rsps.GET, STATS_URL, callback=_count, content_type="application/json"
    )
    for i in range(2):
        rsps.add(
            rsps.POST,
            f"{BASE_URL}/v1/places/search",
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": f"node/{i}",
                        "geometry": None,
                        "properties": {},
                    }
                ],
            },
        )
    out = client.places_search(
        bbox=bbox,
        or_tags=["amenity=cafe"],
        as_of="2026-08-10T18:00:00+02:00",
        limit=20_000,
        auto_split=True,
    )
    assert {f["id"] for f in out["features"]} == {"node/0", "node/1"}
    assert _places_search_bboxes() == tiles
    assert _search_bodies()[0]["limit"] == 20_000
    assert all(c.request.url != ACCOUNT_TIER_URL for c in rsps.calls)


@rsps.activate
def test_places_search_auto_split_jumps_on_hours_parse_limit_fetch(
    client: OSMFeaturesClient,
):
    bbox = "0,0,1,1"
    tiles = split_bbox_tiles(bbox, 2)
    _features_count_always(100, account_tier=False)
    rsps.add(
        rsps.POST,
        f"{BASE_URL}/v1/places/search",
        json={
            "error": "bad_request",
            "detail": (
                "hours_parse_limit. More than 10000 places have opening_hours. "
                "Lower limit, shrink the spatial window, add tag filters, or omit asOf."
            ),
            "status_code": 400,
            "subtype": "hours_parse_limit",
        },
        status=400,
    )
    for i in range(2):
        rsps.add(
            rsps.POST,
            f"{BASE_URL}/v1/places/search",
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": f"node/{i}",
                        "geometry": None,
                        "properties": {},
                    }
                ],
            },
        )
    out = client.places_search(
        bbox=bbox,
        or_tags=["amenity=cafe"],
        as_of="2026-08-10T18:00:00+02:00",
        auto_split=True,
    )
    assert {f["id"] for f in out["features"]} == {"node/0", "node/1"}
    assert _places_search_bboxes() == [bbox, *tiles]


@rsps.activate
def test_places_search_auto_split_open_now_splits_on_scan_cap(
    client: OSMFeaturesClient,
):
    """openNow drains 10k hours-tagged rows with no 400; density-split on count."""
    bbox = "0,0,1,1"
    tiles = split_bbox_tiles(bbox, 2)

    def _count(request):
        from urllib.parse import parse_qs, urlsplit

        tile = parse_qs(urlsplit(request.url).query)["bbox"][0]
        total = 15_000 if tile == bbox else 100
        return (200, {}, _count_payload(total))

    rsps.add_callback(
        rsps.GET, STATS_URL, callback=_count, content_type="application/json"
    )
    for i in range(2):
        rsps.add(
            rsps.POST,
            f"{BASE_URL}/v1/places/search",
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": f"node/{i}",
                        "geometry": None,
                        "properties": {},
                    }
                ],
            },
        )
    out = client.places_search(
        bbox=bbox,
        or_tags=["amenity=cafe"],
        open_now=True,
        auto_split=True,
    )
    assert {f["id"] for f in out["features"]} == {"node/0", "node/1"}
    assert _places_search_bboxes() == tiles
    assert _search_bodies()[0]["openNow"] is True
    assert "limit" not in _search_bodies()[0]
    assert all(c.request.url != ACCOUNT_TIER_URL for c in rsps.calls)
    count_url = _features_count_urls()[0]
    assert "tags=opening_hours" in count_url


@rsps.activate
def test_places_search_auto_split_jumps_when_total_exceeds_tier(
    client: OSMFeaturesClient,
):
    add_account_tier_response()
    bbox = "0,0,1,1"
    tiles = split_bbox_tiles(bbox, 2)

    def _count(request):
        from urllib.parse import parse_qs, urlsplit

        tile = parse_qs(urlsplit(request.url).query)["bbox"][0]
        total = 80_000 if tile == bbox else 1
        return (
            200,
            {},
            _count_payload(total),
        )

    rsps.add_callback(
        rsps.GET, STATS_URL, callback=_count, content_type="application/json"
    )
    for i in range(2):
        rsps.add(
            rsps.POST,
            f"{BASE_URL}/v1/places/search",
            json={
                "type": "FeatureCollection",
                "features": [
                    {
                        "type": "Feature",
                        "id": f"node/{i}",
                        "geometry": None,
                        "properties": {},
                    }
                ],
            },
        )
    out = client.places_search(
        bbox=bbox, or_tags=["amenity=cafe"], auto_split=True
    )
    assert {f["id"] for f in out["features"]} == {"node/0", "node/1"}
    assert _places_search_bboxes() == tiles


def test_parse_place_ref():
    assert parse_place_ref("node", 123) == ("node", 123)
    assert parse_place_ref("WAY", " 456 ") == ("way", 456)
    assert parse_place_ref("node/789") == ("node", 789)
    assert places_details_path("node/1") == "/v1/places/node/1"
    with pytest.raises(ValueError):
        parse_place_ref("highway", 1)
    with pytest.raises(ValueError):
        parse_place_ref("node", 0)


@rsps.activate
def test_places_details_gets_path_and_query(client: OSMFeaturesClient):
    rsps.add(
        rsps.GET,
        f"{BASE_URL}/v1/places/node/123",
        json={"status": "ok", "feature": {"id": "node/123"}},
    )
    out = client.places_details("node/123")
    assert out["status"] == "ok"
    assert out["feature"]["id"] == "node/123"
    req = rsps.calls[0].request
    assert req.method == "GET"
    assert req.url.rstrip("/").endswith("/v1/places/node/123")

    rsps.add(
        rsps.GET,
        f"{BASE_URL}/v1/places/way/99",
        json={"status": "ok", "feature": {"id": "way/99"}},
    )
    split = client.places_details("way", 99)
    assert split["feature"]["id"] == "way/99"
