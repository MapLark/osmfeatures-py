"""MCP server. Thin wrapper over :class:`GeoAgentSession` / ``osmfeatures``.

Requires ``pip install 'osmfeatures[mcp]'``. Stdio auth is ``MAPLARK_API_KEY``.
HTTP auth is ``Authorization: Bearer <MAPLARK_API_KEY>`` per MCP session.
"""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from importlib.resources import files
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from ..nearest import MAX_COMPARISONS
from ._mcp_session import (
    SUMMARY_ITEM_CAP,
    GeoAgentSession,
    require_places_search_spatial,
)
from ._preview import PreviewServer, mapped_geojson, parse_collection_ids, preview_html, validate_collection_id

HTTP_HOST = "127.0.0.1"
HTTP_PORT = 8081
MCP_SESSION_ID_HEADER = "mcp-session-id"
_HTTP_SESSION_ID = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
_PREVIEW_PREFIX = "/mcp/preview/"
# Standing GET SSE does not refresh last_used; tool POSTs do. None disables expiry.
HTTP_SESSION_IDLE_TIMEOUT = 60 * 60


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
        """Stdio writes a GeoJSON file (path). HTTP returns a download URL. Never geometry."""
        export_file = getattr(get_preview(), "export_file", None)
        if callable(export_file):
            return export_file(collection_id)
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


def public_origin(request: Request) -> str:
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    ).split(",")[0].strip()
    return f"{proto}://{host}"


def _is_public_preview(request: Request) -> bool:
    return request.method in ("GET", "HEAD") and request.url.path.startswith(_PREVIEW_PREFIX)


def _preview_response(store: HttpSessionStore, session_id: str, ids_raw: str) -> Response:
    if not _HTTP_SESSION_ID.match(session_id):
        return Response(b"not found\n", status_code=404, media_type="text/plain")
    bound = store.peek(session_id)
    if bound is None:
        return Response(b"not found\n", status_code=404, media_type="text/plain")
    as_geojson = ids_raw.endswith(".geojson")
    raw = ids_raw[: -len(".geojson")] if as_geojson else ids_raw
    try:
        ids = parse_collection_ids(raw)
        if as_geojson:
            body = json.dumps(mapped_geojson(bound.session, ids)).encode("utf-8")
            return Response(
                body,
                media_type="application/geo+json; charset=utf-8",
                headers={"Cache-Control": "no-store"},
            )
        for item in ids:
            bound.session.get(item)
        return Response(
            preview_html(ids).encode("utf-8"),
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": "no-store"},
        )
    except (KeyError, ValueError, TypeError):
        return Response(b"unknown collection\n", status_code=404, media_type="text/plain")


def _register_http_preview_routes(mcp: FastMCP, store: HttpSessionStore) -> None:
    @mcp.custom_route("/mcp/preview/{session_id}/{ids:path}", methods=["GET", "HEAD"])
    async def preview_get(request: Request) -> Response:
        return _preview_response(
            store,
            request.path_params["session_id"],
            request.path_params["ids"],
        )


@dataclass
class _BoundHttpSession:
    session_id: str
    api_key: str
    client: Any
    session: GeoAgentSession
    last_used: float = field(default_factory=time.monotonic)


def _close_client(bound: _BoundHttpSession) -> None:
    close = getattr(bound.client, "close", None)
    if callable(close):
        close()


class HttpPreview:
    """Preview/export URLs on the MCP HTTP app. No localhost sidecar ports."""

    def __init__(self, store: HttpSessionStore) -> None:
        self._store = store

    def open(
        self,
        collection_ids: str | list[str],
        *,
        open_browser: bool = True,
    ) -> dict[str, Any]:
        del open_browser
        bound = self._store._require_bound()
        ids = parse_collection_ids(collection_ids)
        for cid in ids:
            bound.session.get(cid)
        return {
            "collection_ids": ids,
            "preview_url": self._url(bound, ",".join(ids)),
            "opened": False,
        }

    def export_file(self, collection_id: str) -> dict[str, Any]:
        bound = self._store._require_bound()
        cid = validate_collection_id(collection_id)
        fc = bound.session.export_geojson(cid)
        return {
            "collection_id": cid,
            "url": self._url(bound, f"{cid}.geojson"),
            "feature_count": len(fc.get("features") or []),
        }

    def _url(self, bound: _BoundHttpSession, rest: str) -> str:
        request = self._store._mcp_http_request()
        if request is None:
            raise RuntimeError("MCP HTTP session is not bound")
        return f"{public_origin(request)}{_PREVIEW_PREFIX}{bound.session_id}/{rest}"


class HttpSessionStore:
    """One :class:`GeoAgentSession` per Streamable HTTP ``mcp-session-id``."""

    def __init__(
        self,
        *,
        base_url: str,
        client_factory: Callable[[str, str], Any],
        idle_timeout: float | None = HTTP_SESSION_IDLE_TIMEOUT,
    ) -> None:
        self._base_url = base_url
        self._client_factory = client_factory
        self._idle_timeout = idle_timeout
        self._lock = threading.Lock()
        self._bound: dict[str, _BoundHttpSession] = {}
        self._mcp: FastMCP | None = None
        self._http_preview = HttpPreview(self)
        self._stop = threading.Event()
        self._reaper: threading.Thread | None = None
        if idle_timeout is not None and idle_timeout > 0:
            interval = min(60.0, max(1.0, idle_timeout / 2))
            self._reaper = threading.Thread(
                target=self._reap_loop,
                args=(interval,),
                name="mcp-http-reaper",
                daemon=True,
            )
            self._reaper.start()

    def _reap_loop(self, interval: float) -> None:
        while not self._stop.wait(interval):
            self.expire_idle()

    def expire_idle(self, now: float | None = None) -> None:
        if self._idle_timeout is None:
            return
        now = time.monotonic() if now is None else now
        stale: list[_BoundHttpSession] = []
        with self._lock:
            for session_id, bound in list(self._bound.items()):
                if now - bound.last_used >= self._idle_timeout:
                    stale.append(self._bound.pop(session_id))
        for bound in stale:
            _close_client(bound)

    def peek(self, session_id: str) -> _BoundHttpSession | None:
        self.expire_idle()
        with self._lock:
            return self._bound.get(session_id)

    def get_or_create(self, session_id: str, api_key: str) -> _BoundHttpSession:
        self.expire_idle()
        with self._lock:
            existing = self._bound.get(session_id)
            if existing is not None:
                if existing.api_key != api_key:
                    raise ValueError("API key does not match this MCP session")
                existing.last_used = time.monotonic()
                return existing
            client = self._client_factory(api_key, self._base_url)
            bound = _BoundHttpSession(
                session_id=session_id,
                api_key=api_key,
                client=client,
                session=GeoAgentSession(client),
            )
            self._bound[session_id] = bound
            return bound

    def close(self, session_id: str) -> None:
        with self._lock:
            bound = self._bound.pop(session_id, None)
        if bound is None:
            return
        _close_client(bound)

    def close_all(self) -> None:
        self._stop.set()
        if self._reaper is not None:
            self._reaper.join(timeout=1.0)
            self._reaper = None
        with self._lock:
            bound_list = list(self._bound.values())
            self._bound.clear()
        for bound in bound_list:
            _close_client(bound)

    def current_session(self) -> GeoAgentSession:
        return self._require_bound().session

    def current_preview(self) -> HttpPreview:
        return self._http_preview

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
        if request.method == "OPTIONS" or _is_public_preview(request):
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
        "session_idle_timeout": HTTP_SESSION_IDLE_TIMEOUT,
    }
    if transport_security is not None:
        mcp_kwargs["transport_security"] = transport_security
    mcp = FastMCP("maplark", instructions=INSTRUCTIONS, **mcp_kwargs)
    store._mcp = mcp
    build_server(store.current_session, store.current_preview, mcp=mcp)
    _register_http_preview_routes(mcp, store)
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
