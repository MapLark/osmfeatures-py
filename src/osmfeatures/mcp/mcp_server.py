"""MCP server. Thin wrapper over :class:`GeoAgentSession` / ``osmfeatures``.

Requires ``pip install 'osmfeatures[mcp]'``. Stdio auth is ``MAPLARK_API_KEY``.
HTTP auth is ``Authorization: Bearer <MAPLARK_API_KEY>`` per MCP session.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from ..nearest import MAX_COMPARISONS
from ._mcp_session import (
    SUMMARY_ITEM_CAP,
    GeoAgentSession,
    require_places_search_spatial,
)
from ._preview import PreviewServer

HTTP_HOST = "127.0.0.1"
HTTP_PORT = 8081
MCP_SESSION_ID_HEADER = "mcp-session-id"


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


def _load_instructions() -> str:
    # replace, not str.format: the markdown uses {lon, lat} as prose.
    raw = files(__package__).joinpath("AGENT_INSTRUCTIONS.md").read_text(encoding="utf-8")
    return (
        raw.replace("{SUMMARY_ITEM_CAP}", str(SUMMARY_ITEM_CAP))
        .replace("{MAX_COMPARISONS}", str(MAX_COMPARISONS))
    )


INSTRUCTIONS = _load_instructions()


def _preview_getter(
    get_session: Callable[[], GeoAgentSession],
    preview: PreviewServer | Callable[[], Any] | None,
) -> Callable[[], Any]:
    if callable(preview) and not isinstance(preview, PreviewServer):
        return preview
    if preview is not None:
        return lambda: preview
    held = PreviewServer(_SessionView(get_session))
    return lambda: held


class _SessionView:
    """Preview HTTP handler does not have the MCP session id; delegate live."""

    def __init__(self, get_session: Callable[[], GeoAgentSession]) -> None:
        self._get_session = get_session

    def get(self, collection_id: str) -> Any:
        return self._get_session().get(collection_id)

    def export_geojson(self, collection_id: str) -> Any:
        return self._get_session().export_geojson(collection_id)


def build_server(
    get_session: Callable[[], GeoAgentSession],
    preview: PreviewServer | Callable[[], Any] | None = None,
    *,
    mcp: FastMCP | None = None,
) -> FastMCP:
    mcp = mcp or FastMCP("maplark", instructions=INSTRUCTIONS)
    get_preview = _preview_getter(get_session, preview)

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
        return get_session().places_search(
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
        return get_session().places_nearby(
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
        return get_session().places_details(osm_type, osm_id)

    @mcp.tool()
    def geocode(q: str) -> dict[str, Any]:
        """Place name to lon/lat and bbox (Nominatim until MapLark geocode ships). Use bbox for places_search."""
        return get_session().geocode(q)

    @mcp.tool()
    def routes_isochrone(
        lon: float,
        lat: float,
        max_distance_m: float | None = None,
        duration_s: float | None = None,
        search_buffer_m: float | None = None,
        travel_mode: str | None = None,
    ) -> dict[str, Any]:
        """Walk/bike reach polygon. Provide exactly one of max_distance_m or duration_s. travel_mode is WALK or BICYCLE."""
        return get_session().routes_isochrone(
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
        """Given-order walk/bike path. Each stop is {lon, lat}. travel_mode is WALK or BICYCLE. Does not reorder."""
        return get_session().routes_path(
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
        """TSP from start (not in stops). loop=true returns to start. Start from a searched place. travel_mode is WALK or BICYCLE."""
        return get_session().routes_optimized_path(
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
        within: str | None = None,
        zoom: float | None = None,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """One GET /v3/osm_features tile (omit limit for the key's max_limit, no cursor). Pass limit to cap the tile. location is numeric lat,lng, not a place name. within is way/<id> or relation/<id>. Non-points include centroids for local joins."""
        return get_session().query(
            bbox=bbox,
            location=location,
            radius=radius,
            type=type,
            way_shape=way_shape,
            tags=tags,
            or_tags=or_tags,
            not_tags=not_tags,
            within=within,
            zoom=zoom,
            limit=limit,
        )

    @mcp.tool()
    def stats(
        group_by: str,
        bbox: str | None = None,
        location: str | None = None,
        radius: float | None = None,
        type: str | None = None,
        way_shape: str | None = None,
        tags: list[str] | None = None,
        or_tags: list[str] | None = None,
        not_tags: list[str] | None = None,
        within: str | None = None,
        limit: int | None = None,
        disable_budget_warning: bool = False,
    ) -> dict[str, Any]:
        """Count features grouped by a tag key. City/country histograms. Report total. Not a GeoJSON page. location is numeric lat,lng."""
        return get_session().stats(
            group_by=group_by,
            bbox=bbox,
            location=location,
            radius=radius,
            type=type,
            way_shape=way_shape,
            tags=tags,
            or_tags=or_tags,
            not_tags=not_tags,
            within=within,
            limit=limit,
            disable_budget_warning=disable_budget_warning,
        )

    @mcp.tool()
    def nearest_within(
        primary_id: str,
        secondary_id: str,
        max_distance_m: float,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """Nearest secondary for each primary within max_distance_m. Omit limit for every pair in the store; count is complete, items lists at most 40. Refuses joins over 500000 comparisons."""
        return get_session().nearest_within(primary_id, secondary_id, max_distance_m, limit)

    @mcp.tool()
    def pairs_within(
        primary_id: str,
        secondary_id: str,
        max_distance_m: float,
        min_distance_m: float = 0,
        limit: int | None = None,
    ) -> dict[str, Any]:
        """All pairs with min_distance_m <= d <= max_distance_m. Same collection_id on both sides emits each unordered pair once. Omit limit for every pair in the store; count is complete, items lists at most 40. Refuses joins over 500000 comparisons."""
        return get_session().pairs_within(
            primary_id, secondary_id, max_distance_m, min_distance_m, limit
        )

    @mcp.tool()
    def filter_open(collection_id: str) -> dict[str, Any]:
        """Keep stored places with openNow true. Search first with as_of."""
        return get_session().filter_open(collection_id)

    @mcp.tool()
    def point_in_polygon(collection_id: str, lon: float, lat: float) -> dict[str, Any]:
        """True if lon/lat is inside a stored isochrone or any polygon in a query collection."""
        return get_session().point_in_polygon(collection_id, lon, lat)

    @mcp.tool()
    def points_in_polygon(points_id: str, polygon_id: str) -> dict[str, Any]:
        """Keep stored point features that fall inside a stored polygon (isochrone)."""
        return get_session().points_in_polygon(points_id, polygon_id)

    @mcp.tool()
    def preview_map(collection_ids: list[str], open_browser: bool = True) -> dict[str, Any]:
        """Open MapLibre + OpenFreeMap for one or more stored collections on the same map. Returns a URL, not geometry."""
        return get_preview().open(collection_ids, open_browser=open_browser)

    @mcp.tool()
    def export_geojson(collection_id: str) -> dict[str, Any]:
        """Write a GeoJSON file for a stored collection. Returns a path, not geometry."""
        return get_session().export_geojson_file(collection_id)

    return mcp


def run_stdio(*, api_key: str, base_url: str | None = None) -> None:
    from .._http import DEFAULT_BASE_URL
    from ..client import OSMFeaturesClient

    with OSMFeaturesClient(api_key=api_key, base_url=base_url or DEFAULT_BASE_URL) as client:
        session = GeoAgentSession(client)
        preview = PreviewServer(session)
        try:
            build_server(lambda: session, lambda: preview).run(transport="stdio")
        finally:
            preview.close()


@dataclass
class _BoundHttpSession:
    api_key: str
    client: Any
    session: GeoAgentSession
    preview: PreviewServer


class HttpSessionStore:
    """One :class:`GeoAgentSession` per Streamable HTTP ``mcp-session-id``."""

    def __init__(
        self,
        *,
        base_url: str,
        client_factory: Callable[[str, str], Any],
    ) -> None:
        self._base_url = base_url
        self._client_factory = client_factory
        self._lock = threading.Lock()
        self._bound: dict[str, _BoundHttpSession] = {}
        self._mcp: FastMCP | None = None

    def get_or_create(self, session_id: str, api_key: str) -> _BoundHttpSession:
        with self._lock:
            existing = self._bound.get(session_id)
            if existing is not None:
                if existing.api_key != api_key:
                    raise ValueError("API key does not match this MCP session")
                return existing
            client = self._client_factory(api_key, self._base_url)
            session = GeoAgentSession(client)
            preview = PreviewServer(session)
            bound = _BoundHttpSession(
                api_key=api_key, client=client, session=session, preview=preview
            )
            self._bound[session_id] = bound
            return bound

    def close(self, session_id: str) -> None:
        with self._lock:
            bound = self._bound.pop(session_id, None)
        if bound is None:
            return
        bound.preview.close()
        close = getattr(bound.client, "close", None)
        if callable(close):
            close()

    def close_all(self) -> None:
        with self._lock:
            ids = list(self._bound)
        for session_id in ids:
            self.close(session_id)

    def current_session(self) -> GeoAgentSession:
        return self._require_bound().session

    def current_preview(self) -> PreviewServer:
        return self._require_bound().preview

    def _require_bound(self) -> _BoundHttpSession:
        # Streamable HTTP runs tools on the MCP session task, not the ASGI
        # POST task, so middleware ContextVars never reach get_session().
        request = self._mcp_http_request()
        if request is None:
            raise RuntimeError("MCP HTTP session is not bound")
        session_id = (request.headers.get(MCP_SESSION_ID_HEADER) or "").strip()
        api_key = _bearer_token(request.headers.get("authorization") or "")
        if not session_id or not api_key:
            raise RuntimeError("MCP HTTP session is not bound")
        return self.get_or_create(session_id, api_key)

    def _mcp_http_request(self) -> Request | None:
        mcp = self._mcp
        if mcp is None:
            return None
        try:
            raw = mcp.get_context().request_context.request
        except (LookupError, ValueError):
            return None
        return raw if isinstance(raw, Request) else None


def _bearer_token(authorization: str) -> str | None:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return None
    stripped = token.strip()
    return stripped or None


class BearerAuthMiddleware:
    """Require ``Authorization: Bearer`` and bind the MCP session store."""

    def __init__(self, app: ASGIApp, store: HttpSessionStore) -> None:
        self.app = app
        self.store = store

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive)
        if request.method == "OPTIONS":
            await self.app(scope, receive, send)
            return
        api_key = _bearer_token(request.headers.get("authorization") or "")
        if api_key is None:
            response = JSONResponse({"error": "unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        session_id = (request.headers.get(MCP_SESSION_ID_HEADER) or "").strip() or None
        if session_id:
            try:
                self.store.get_or_create(session_id, api_key)
            except ValueError:
                response = JSONResponse({"error": "unauthorized"}, status_code=401)
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
        if request.method == "DELETE" and session_id:
            self.store.close(session_id)


def _default_client(api_key: str, base_url: str) -> Any:
    from ..client import OSMFeaturesClient

    return OSMFeaturesClient(api_key=api_key, base_url=base_url)


def build_http_app(
    *,
    base_url: str | None = None,
    host: str = HTTP_HOST,
    port: int = HTTP_PORT,
    json_response: bool = False,
    client_factory: Callable[[str, str], Any] | None = None,
    transport_security: TransportSecuritySettings | None = None,
) -> tuple[Starlette, HttpSessionStore]:
    from .._http import DEFAULT_BASE_URL

    store = HttpSessionStore(
        base_url=base_url or DEFAULT_BASE_URL,
        client_factory=client_factory or _default_client,
    )
    mcp_kwargs: dict[str, Any] = {
        "host": host,
        "port": port,
        "stateless_http": False,
        "json_response": json_response,
    }
    if transport_security is not None:
        mcp_kwargs["transport_security"] = transport_security
    mcp = FastMCP("maplark", instructions=INSTRUCTIONS, **mcp_kwargs)
    store._mcp = mcp
    build_server(store.current_session, store.current_preview, mcp=mcp)
    app = mcp.streamable_http_app()
    app.add_middleware(BearerAuthMiddleware, store=store)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Mcp-Session-Id", MCP_SESSION_ID_HEADER],
    )
    return app, store


def run_http(
    *,
    host: str = HTTP_HOST,
    port: int = HTTP_PORT,
    base_url: str | None = None,
) -> None:
    import uvicorn

    app, store = build_http_app(host=host, port=port, base_url=base_url)
    try:
        uvicorn.run(app, host=host, port=port)
    finally:
        store.close_all()
