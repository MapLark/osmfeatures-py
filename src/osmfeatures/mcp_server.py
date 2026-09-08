"""stdio MCP server. Thin wrapper over :class:`GeoAgentSession` / ``osmfeatures``.

Requires ``pip install 'osmfeatures[mcp]'``. Auth is ``MAPLARK_API_KEY``.
"""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from ._mcp_session import (
    QUERY_ALL_MAX_FEATURES,
    SUMMARY_ITEM_CAP,
    GeoAgentSession,
    require_places_search_spatial,
)
from .nearest import MAX_COMPARISONS
from ._preview import PreviewServer


def places_search_point(
    lat: float | None,
    lng: float | None,
    radius: float | None,
) -> dict[str, float] | None:
    """Places ``{lat, lng}`` when using location+radius; ``None`` for bbox-only.

    ``lat``, ``lng``, and ``radius`` must all be set or all omitted. A lone
    coordinate was previously dropped, so the request went out without a point.
    """
    bits = (lat is not None, lng is not None, radius is not None)
    if not any(bits):
        return None
    if not all(bits):
        raise ValueError(
            "places_search location+radius requires lat, lng, and radius "
            "(or omit all three and pass bbox)"
        )
    return {"lat": float(lat), "lng": float(lng)}

INSTRUCTIONS = f"""You are a geo-agent planner over MapLark tools. You choose *what* to ask
(OSM tags, a bbox or location+radius, a distance budget, a travel mode, which tool
next). Code computes every metre.

You must not: compute haversine, decide which place is nearest another set, parse
opening_hours strings, invent coordinates, distances, or walk times.

Spatial default: if the user did not name a place, they must give a viewport bbox
or location+radius as "here". There is no geocode tool yet — do not invent a
lon/lat for a named city. Ask for a bbox, a lat/lng, or use a point the user
supplied.

Tool results are summaries (ids, names, lon/lat scalars, distance_m,
openingHours.status) plus a collection_id. count is the full hit total; items
lists at most {SUMMARY_ITEM_CAP} (items_truncated is true when more were stored).
Do not treat len(items) as the total. They never include GeoJSON coordinate
arrays. Call preview_map(collection_id) to draw on an OpenFreeMap basemap (the
browser fetches GeoJSON; you only get a URL). Call export_geojson only when the
user asked for a raw GeoJSON file: it writes a file and returns a filesystem
path. Do not read that file or paste coordinate arrays.

Local tools (no HTTP): nearest_within(primary_id, secondary_id, max_distance_m),
filter_open(collection_id), point_in_polygon / points_in_polygon.
nearest_within is O(n×m) and refuses joins over {MAX_COMPARISONS} comparisons;
shrink with places_search/nearby limit, not query_all.
Do not invent places_near_to or places_open_after.

Prompt shapes:
- cafes near me → places_nearby or places_search with location+radius / bbox
- how many vegan restaurants → places_search, report count (items may be a prefix)
- restaurants within 150 m of a station → two places_search + nearest_within
- bars open past midnight / cafes open at 8pm → places_search with as_of
  (no open_now so closed hits stay), then filter_open; if short of N and the
  page was full, raise limit and search again
- is A a 20-minute walk from B → routes_isochrone from A, point_in_polygon for B
- walk from hotel to office → routes_path (listed order, lon/lat from the user
  or from a prior search summary)
- walking loop of bars → places_search + routes_optimized_path (loop true)
- show this on a map → preview_map(collection_id) after a search or route

query is one page of generic OSM (parks, highways), not place/route primitives.
For a larger bbox, call query_all with bbox_tiles (power of 2; default 2, use 1 to
disable tiling) and limit_per_page. Default max_features is {QUERY_ALL_MAX_FEATURES}; raise it if
has_more is true. That is CLI --all-pages --bbox-tiles. Do not page with query +
cursor yourself.
"""


def build_server(session: GeoAgentSession, preview: PreviewServer | None = None) -> FastMCP:
    mcp = FastMCP("maplark", instructions=INSTRUCTIONS)
    preview_server = preview or PreviewServer(session)

    @mcp.tool()
    def places_search(
        bbox: str | None = None,
        lat: float | None = None,
        lng: float | None = None,
        radius: float | None = None,
        tags: list[str] | None = None,
        or_tags: list[str] | None = None,
        limit: int | None = None,
        open_now: bool = False,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        """Places in a bbox or location+radius (not both). Polygon POIs come back as centroid points."""
        location = places_search_point(lat, lng, radius)
        require_places_search_spatial(bbox, location)
        return session.places_search(
            bbox=bbox,
            location=location,
            radius=radius,
            tags=tags,
            or_tags=or_tags,
            limit=limit,
            open_now=open_now,
            as_of=as_of,
        )

    @mcp.tool()
    def places_nearby(
        lat: float,
        lng: float,
        radius: float | None = None,
        tags: list[str] | None = None,
        or_tags: list[str] | None = None,
        limit: int | None = None,
        open_now: bool = False,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        """Places ranked from a point, nearest first (straight-line metres)."""
        return session.places_nearby(
            location={"lat": lat, "lng": lng},
            radius=radius,
            tags=tags,
            or_tags=or_tags,
            limit=limit,
            open_now=open_now,
            as_of=as_of,
        )

    @mcp.tool()
    def places_details(osm_type: str, osm_id: int | str | None = None) -> dict[str, Any]:
        """One place by node|way|relation id, or a search id like node/123 as osm_type."""
        return session.places_details(osm_type, osm_id)

    @mcp.tool()
    def routes_isochrone(
        lon: float,
        lat: float,
        max_distance_m: float | None = None,
        duration_s: float | None = None,
        search_buffer_m: float | None = None,
        travel_mode: str | None = None,
    ) -> dict[str, Any]:
        """Walk/bike reach polygon. Provide exactly one of max_distance_m or duration_s."""
        return session.routes_isochrone(
            origin={"lon": lon, "lat": lat},
            max_distance_m=max_distance_m,
            duration_s=duration_s,
            search_buffer_m=search_buffer_m,
            travel_mode=travel_mode,
        )

    @mcp.tool()
    def routes_path(
        stops: list[dict[str, float]],
        search_buffer_m: float | None = None,
        travel_mode: str | None = None,
    ) -> dict[str, Any]:
        """Given-order walk/bike path. Each stop is {lon, lat}. Does not reorder."""
        return session.routes_path(
            stops=stops,
            search_buffer_m=search_buffer_m,
            travel_mode=travel_mode,
        )

    @mcp.tool()
    def routes_optimized_path(
        start: dict[str, float],
        stops: list[dict[str, float]],
        loop: bool | None = None,
        search_buffer_m: float | None = None,
        travel_mode: str | None = None,
    ) -> dict[str, Any]:
        """TSP from start (not in stops). loop=true returns to start."""
        return session.routes_optimized_path(
            start=start,
            stops=stops,
            search_buffer_m=search_buffer_m,
            loop=loop,
            travel_mode=travel_mode,
        )

    @mcp.tool()
    def query(
        bbox: str | None = None,
        location: str | None = None,
        radius: float | None = None,
        type: str | None = None,
        way_shape: str | None = None,
        tags: list[str] | None = None,
        or_tags: list[str] | None = None,
        not_tags: list[str] | None = None,
        limit: int | None = None,
        zoom: float | None = None,
    ) -> dict[str, Any]:
        """Generic OSM features (parks, highways). Non-points include centroids for local joins."""
        return session.query(
            bbox=bbox,
            location=location,
            radius=radius,
            type=type,
            way_shape=way_shape,
            tags=tags,
            or_tags=or_tags,
            not_tags=not_tags,
            limit=limit,
            zoom=zoom,
        )

    @mcp.tool()
    def query_all(
        bbox: str | None = None,
        location: str | None = None,
        radius: float | None = None,
        type: str | None = None,
        way_shape: str | None = None,
        tags: list[str] | None = None,
        or_tags: list[str] | None = None,
        not_tags: list[str] | None = None,
        zoom: float | None = None,
        limit_per_page: int | None = None,
        bbox_tiles: int = 2,
        max_features: int = QUERY_ALL_MAX_FEATURES,
    ) -> dict[str, Any]:
        """All pages of generic OSM features. Splits bbox into bbox_tiles (power of 2). Caps at max_features (default 10000). Centroids included."""
        return session.query_all(
            bbox=bbox,
            location=location,
            radius=radius,
            type=type,
            way_shape=way_shape,
            tags=tags,
            or_tags=or_tags,
            not_tags=not_tags,
            zoom=zoom,
            limit_per_page=limit_per_page,
            bbox_tiles=bbox_tiles,
            max_features=max_features,
        )

    @mcp.tool()
    def nearest_within(
        primary_id: str,
        secondary_id: str,
        max_distance_m: float,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Nearest secondary for each primary within max_distance_m. Omit limit for every pair in the store; count is complete, items lists at most 40. Refuses joins over 250000 comparisons."""
        return session.nearest_within(primary_id, secondary_id, max_distance_m, limit)

    @mcp.tool()
    def filter_open(collection_id: str) -> dict[str, Any]:
        """Keep stored places with openingHours.status == open. Search first with as_of."""
        return session.filter_open(collection_id)

    @mcp.tool()
    def point_in_polygon(collection_id: str, lon: float, lat: float) -> dict[str, Any]:
        """True if lon/lat is inside a stored isochrone or any polygon in a query collection."""
        return session.point_in_polygon(collection_id, lon, lat)

    @mcp.tool()
    def points_in_polygon(points_id: str, polygon_id: str) -> dict[str, Any]:
        """Keep stored point features that fall inside a stored polygon (isochrone)."""
        return session.points_in_polygon(points_id, polygon_id)

    @mcp.tool()
    def preview_map(collection_id: str, open_browser: bool = True) -> dict[str, Any]:
        """Open MapLibre + OpenFreeMap for a stored collection. Returns a URL, not geometry."""
        return preview_server.open(collection_id, open_browser=open_browser)

    @mcp.tool()
    def export_geojson(collection_id: str) -> dict[str, Any]:
        """Write a GeoJSON file for a stored collection. Returns a path, not geometry."""
        return session.export_geojson_file(collection_id)

    return mcp


def run_stdio(*, api_key: str, base_url: str | None = None) -> None:
    from ._http import DEFAULT_BASE_URL
    from .client import OSMFeaturesClient

    with OSMFeaturesClient(api_key=api_key, base_url=base_url or DEFAULT_BASE_URL) as client:
        session = GeoAgentSession(client)
        preview = PreviewServer(session)
        try:
            build_server(session, preview).run(transport="stdio")
        finally:
            preview.close()
