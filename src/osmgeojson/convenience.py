"""Domain-specific convenience helpers built on top of OSMGeoJSONClient.

Each helper is a thin, typed wrapper that pre-fills the correct OSM tag
filters, geometry shape, and element type.  All accept ``**kwargs`` that
are forwarded directly to ``client.query_all()`` so you can still pass
``limit_per_page``, ``max_features``, ``disable_budget_warning``, etc.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .client import OSMGeoJSONClient
    from .models import OSMFeatureCollection


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------

def _q(
    client: "OSMGeoJSONClient",
    *,
    bbox: str | None = None,
    around: str | None = None,
    tags: list[str] | None = None,
    or_tags: list[str] | None = None,
    not_tags: list[str] | None = None,
    type: list[str] | None = None,
    shape: str | None = None,
    osm_ids: str | None = None,
    **extra: Any,
) -> "OSMFeatureCollection":
    params: dict[str, Any] = {}
    if bbox is not None:
        params["bbox"] = bbox
    if around is not None:
        params["around"] = around
    if tags:
        params["tags"] = tags
    if or_tags:
        params["or_tags"] = or_tags
    if not_tags:
        params["not_tags"] = not_tags
    if type:
        params["type"] = type
    if shape:
        params["shape"] = shape
    if osm_ids is not None:
        params["osm_ids"] = osm_ids
    params.update(extra)
    return client.query_all(**params)


# ---------------------------------------------------------------------------
# Built environment
# ---------------------------------------------------------------------------


def get_buildings(
    client: "OSMGeoJSONClient",
    bbox: str,
    *,
    building: str | None = None,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch building polygons within *bbox*.

    Parameters
    ----------
    building:
        Optional OSM ``building`` tag value to narrow results, e.g.
        ``"residential"``, ``"commercial"``, ``"yes"``.
    """
    tag = f"building={building}" if building else "building"
    return _q(client, bbox=bbox, tags=[tag], type=["way"], shape="polygon", **kwargs)


def get_building_polygons(
    client: "OSMGeoJSONClient",
    osm_ids: list[int] | tuple[int, ...],
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch polygon geometry for specific way IDs (direct OSM ID lookup).

    Mirrors the ``_geojson_fetch_polygons`` helper used in solis_aurum_app.
    """
    ids_str = ",".join(str(i) for i in osm_ids)
    return _q(client, osm_ids=ids_str, type=["way"], shape="polygon", **kwargs)


def get_barriers(
    client: "OSMGeoJSONClient",
    bbox: str,
    *,
    barrier: str | None = None,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch barrier features (walls, fences, hedges) within *bbox*.

    Useful for shadow / occlusion analysis.

    Parameters
    ----------
    barrier:
        Optional OSM ``barrier`` tag value, e.g. ``"wall"``, ``"fence"``.
    """
    tag = f"barrier={barrier}" if barrier else "barrier"
    return _q(client, bbox=bbox, tags=[tag], **kwargs)


def get_trees(
    client: "OSMGeoJSONClient",
    bbox: str,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch individual trees (OSM nodes with ``natural=tree``) within *bbox*.

    Useful as point-feature inputs for shadow cast estimation.
    """
    return _q(client, bbox=bbox, tags=["natural=tree"], type=["node"], **kwargs)


# ---------------------------------------------------------------------------
# Mobility & transport
# ---------------------------------------------------------------------------


def get_roads(
    client: "OSMGeoJSONClient",
    bbox: str,
    *,
    highway: str | None = None,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch road / highway line features within *bbox*.

    Parameters
    ----------
    highway:
        Optional OSM ``highway`` value to narrow results, e.g.
        ``"primary"``, ``"residential"``, ``"footway"``.
    """
    tag = f"highway={highway}" if highway else "highway"
    return _q(client, bbox=bbox, tags=[tag], type=["way"], shape="line", **kwargs)


def get_cycleways(
    client: "OSMGeoJSONClient",
    bbox: str,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch dedicated cycle-path line features within *bbox*."""
    return _q(client, bbox=bbox, tags=["highway=cycleway"], type=["way"], shape="line", **kwargs)


def get_public_transport_stops(
    client: "OSMGeoJSONClient",
    bbox: str,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch public-transport stops (bus, rail, tram) within *bbox*."""
    return _q(
        client,
        bbox=bbox,
        type=["node"],
        or_tags=[
            "public_transport=stop_position",
            "railway=stop",
            "highway=bus_stop",
            "railway=tram_stop",
        ],
        **kwargs,
    )


def get_parking(
    client: "OSMGeoJSONClient",
    bbox: str,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch parking areas and nodes within *bbox*."""
    return _q(client, bbox=bbox, tags=["amenity=parking"], **kwargs)


# ---------------------------------------------------------------------------
# POI / venues
# ---------------------------------------------------------------------------


def get_amenities(
    client: "OSMGeoJSONClient",
    bbox: str,
    *,
    amenity: str | None = None,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch OSM amenity features within *bbox*.

    Parameters
    ----------
    amenity:
        Optional value to narrow, e.g. ``"restaurant"``, ``"bank"``.
    """
    tag = f"amenity={amenity}" if amenity else "amenity"
    return _q(client, bbox=bbox, tags=[tag], **kwargs)


def get_restaurants(
    client: "OSMGeoJSONClient",
    bbox: str,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch restaurants within *bbox*."""
    return _q(client, bbox=bbox, tags=["amenity=restaurant"], **kwargs)


def get_cafes(
    client: "OSMGeoJSONClient",
    bbox: str,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch cafes within *bbox*."""
    return _q(client, bbox=bbox, tags=["amenity=cafe"], **kwargs)


def get_shops(
    client: "OSMGeoJSONClient",
    bbox: str,
    *,
    shop: str | None = None,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch shop nodes and polygons within *bbox*.

    Parameters
    ----------
    shop:
        Optional value to narrow, e.g. ``"supermarket"``, ``"bakery"``.
    """
    tag = f"shop={shop}" if shop else "shop"
    return _q(client, bbox=bbox, tags=[tag], **kwargs)


def get_healthcare(
    client: "OSMGeoJSONClient",
    bbox: str,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch healthcare facilities (hospitals, clinics, pharmacies) within *bbox*."""
    return _q(
        client,
        bbox=bbox,
        or_tags=[
            "amenity=hospital",
            "amenity=clinic",
            "amenity=pharmacy",
            "healthcare",
        ],
        **kwargs,
    )


def get_schools(
    client: "OSMGeoJSONClient",
    bbox: str,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch educational facilities (schools, universities, kindergartens) within *bbox*."""
    return _q(
        client,
        bbox=bbox,
        or_tags=[
            "amenity=school",
            "amenity=university",
            "amenity=college",
            "amenity=kindergarten",
        ],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Green space & nature
# ---------------------------------------------------------------------------


def get_parks(
    client: "OSMGeoJSONClient",
    bbox: str,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch parks, gardens, and playgrounds within *bbox*."""
    return _q(
        client,
        bbox=bbox,
        or_tags=[
            "leisure=park",
            "leisure=garden",
            "leisure=playground",
            "leisure=nature_reserve",
        ],
        **kwargs,
    )


def get_green_spaces(
    client: "OSMGeoJSONClient",
    bbox: str,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch green / vegetated land-use areas within *bbox*."""
    return _q(
        client,
        bbox=bbox,
        or_tags=[
            "landuse=grass",
            "landuse=forest",
            "landuse=meadow",
            "natural=wood",
            "natural=scrub",
        ],
        **kwargs,
    )


def get_water(
    client: "OSMGeoJSONClient",
    bbox: str,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch water bodies and waterways within *bbox*."""
    return _q(
        client,
        bbox=bbox,
        or_tags=[
            "natural=water",
            "waterway=river",
            "waterway=stream",
            "waterway=canal",
            "landuse=reservoir",
        ],
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Administrative & place
# ---------------------------------------------------------------------------


def get_landuse(
    client: "OSMGeoJSONClient",
    bbox: str,
    *,
    landuse: str | None = None,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch land-use polygons within *bbox*.

    Parameters
    ----------
    landuse:
        Optional value to narrow, e.g. ``"residential"``, ``"commercial"``.
    """
    tag = f"landuse={landuse}" if landuse else "landuse"
    return _q(client, bbox=bbox, tags=[tag], **kwargs)


def get_boundaries(
    client: "OSMGeoJSONClient",
    bbox: str,
    *,
    admin_level: int | None = None,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch administrative boundary relations within *bbox*.

    Parameters
    ----------
    admin_level:
        OSM ``admin_level`` value (2=country, 4=region, 6=county, 8=city, ...).
    """
    tags = ["boundary=administrative"]
    if admin_level is not None:
        tags.append(f"admin_level={admin_level}")
    return _q(client, bbox=bbox, tags=tags, type=["relation"], **kwargs)


def get_place(
    client: "OSMGeoJSONClient",
    lon: float,
    lat: float,
    radius_m: float,
    *,
    place: str | None = None,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch named place nodes within *radius_m* metres of *(lon, lat)*.

    Parameters
    ----------
    place:
        Optional OSM ``place`` value to narrow, e.g. ``"city"``,
        ``"suburb"``, ``"neighbourhood"``, ``"village"``.
    """
    around = f"{lon},{lat},{radius_m}"
    tag = f"place={place}" if place else "place"
    return _q(client, around=around, tags=[tag], type=["node"], **kwargs)


def get_elements_by_name(
    client: "OSMGeoJSONClient",
    bbox: str,
    name: str,
    **kwargs: Any,
) -> "OSMFeatureCollection":
    """Fetch all OSM elements with ``name=<name>`` within *bbox*.

    Useful for venue lookup by name.  Note that OSM name matching is
    exact (case-sensitive).
    """
    return _q(client, bbox=bbox, tags=[f"name={name}"], **kwargs)
