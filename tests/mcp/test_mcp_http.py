"""HTTP MCP: Bearer auth, session isolation, initialize + tools/list."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("mcp")

from mcp.server.transport_security import TransportSecuritySettings
from starlette.testclient import TestClient

from osmfeatures.mcp.mcp_server import HTTP_SESSION_IDLE_TIMEOUT, HttpSessionStore, build_http_app

_MCP_HEADERS = {
    "Authorization": "Bearer sk-a",
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

# TestClient Host is "testserver"; FastMCP auto-enables DNS rebinding on 127.0.0.1.
_TEST_SECURITY = TransportSecuritySettings(enable_dns_rebinding_protection=False)


def _app(**kwargs):
    kwargs.setdefault("base_url", "http://api.test")
    kwargs.setdefault("json_response", True)
    kwargs.setdefault("client_factory", _factory)
    kwargs.setdefault("transport_security", _TEST_SECURITY)
    return build_http_app(**kwargs)


class _FakeClient:
    def __init__(self, api_key: str, base_url: str) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.closed = False
        self.count_calls: list[dict] = []

    def close(self) -> None:
        self.closed = True

    def count(self, **kwargs) -> dict:
        self.count_calls.append(kwargs)
        return {
            "total": 12,
            "truncated": False,
            "groups": [{"value": "cafe", "count": 12}],
        }


def _factory(api_key: str, base_url: str) -> _FakeClient:
    return _FakeClient(api_key, base_url)


def test_http_session_store_isolates_collections():
    store = HttpSessionStore(base_url="http://api.test", client_factory=_factory)
    try:
        a = store.get_or_create("sess-a", "sk-a")
        b = store.get_or_create("sess-b", "sk-b")
        assert a.session is not b.session
        a.session._put({"type": "FeatureCollection", "features": []})
        assert list(a.session._store) == ["fc_1"]
        assert list(b.session._store) == []
        same = store.get_or_create("sess-a", "sk-a")
        assert same.session is a.session
        with pytest.raises(ValueError, match="API key"):
            store.get_or_create("sess-a", "sk-other")
    finally:
        store.close_all()
        assert a.client.closed is True
        assert b.client.closed is True


def test_http_session_store_expires_idle():
    store = HttpSessionStore(
        base_url="http://api.test",
        client_factory=_factory,
        idle_timeout=60,
    )
    try:
        a = store.get_or_create("sess-a", "sk-a")
        b = store.get_or_create("sess-b", "sk-b")
        a.last_used = time.monotonic() - 61
        store.expire_idle()
        assert store.peek("sess-a") is None
        assert a.client.closed is True
        assert store.peek("sess-b") is b
        assert b.client.closed is False
        again = store.get_or_create("sess-a", "sk-a")
        assert again.session is not a.session
        assert again.client is not a.client
    finally:
        store.close_all()


def test_http_session_store_idle_timeout_none_never_expires():
    store = HttpSessionStore(
        base_url="http://api.test",
        client_factory=_factory,
        idle_timeout=None,
    )
    try:
        a = store.get_or_create("sess-a", "sk-a")
        a.last_used = time.monotonic() - 10_000
        store.expire_idle()
        assert store.peek("sess-a") is a
        assert a.client.closed is False
    finally:
        store.close_all()


def test_http_requires_bearer():
    app, store = _app()
    try:
        with TestClient(app) as client:
            missing = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
            assert missing.status_code == 401
            empty = client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={"Authorization": "Bearer "},
            )
            assert empty.status_code == 401
    finally:
        store.close_all()


def _rpc(method: str, params: dict | None = None, req_id: int = 1) -> dict:
    body: dict = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        body["params"] = params
    return body


def _handshake(client: TestClient, api_key: str = "sk-a") -> dict[str, str]:
    init = client.post(
        "/mcp",
        headers={**_MCP_HEADERS, "Authorization": f"Bearer {api_key}"},
        json=_rpc(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        ),
    )
    assert init.status_code == 200, init.text
    session_id = init.headers.get("mcp-session-id")
    assert session_id
    headers = {
        **_MCP_HEADERS,
        "Authorization": f"Bearer {api_key}",
        "mcp-session-id": session_id,
    }
    ready = client.post(
        "/mcp",
        headers=headers,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
    )
    assert ready.status_code in (200, 202)
    return headers


def test_http_initialize_and_tools_list():
    app, store = _app()
    try:
        assert store._idle_timeout == HTTP_SESSION_IDLE_TIMEOUT == 60 * 60
        assert store._mcp is not None
        assert store._mcp.settings.session_idle_timeout == HTTP_SESSION_IDLE_TIMEOUT
        with TestClient(app) as client:
            init = client.post(
                "/mcp",
                headers=_MCP_HEADERS,
                json=_rpc(
                    "initialize",
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "0"},
                    },
                ),
            )
            assert init.status_code == 200, init.text
            session_id = init.headers.get("mcp-session-id")
            assert session_id
            payload = init.json()
            assert payload["result"]["serverInfo"]["name"] == "maplark"
            headers = {**_MCP_HEADERS, "mcp-session-id": session_id}
            ready = client.post(
                "/mcp",
                headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            )
            assert ready.status_code in (200, 202)
            listed = client.post(
                "/mcp",
                headers=headers,
                json=_rpc("tools/list", {}),
            )
            assert listed.status_code == 200, listed.text
            names = {tool["name"] for tool in listed.json()["result"]["tools"]}
            assert "places_search" in names
            assert "nearest_within" in names
            assert "preview_map" in names
            assert "export_geojson" in names
    finally:
        store.close_all()


def test_http_tools_call_binds_session_from_mcp_request():
    app, store = _app()
    try:
        with TestClient(app) as client:
            headers = _handshake(client)
            called = client.post(
                "/mcp",
                headers=headers,
                json=_rpc(
                    "tools/call",
                    {
                        "name": "stats",
                        "arguments": {
                            "group_by": "amenity",
                            "tags": ["amenity=cafe"],
                        },
                    },
                ),
            )
            assert called.status_code == 200, called.text
            payload = called.json()
            assert "error" not in payload, payload
            result = payload["result"]
            assert result.get("isError") is not True, result
            structured = result.get("structuredContent") or {}
            assert structured["total"] == 12
            bound = store.get_or_create(headers["mcp-session-id"], "sk-a")
            assert bound.client.count_calls
    finally:
        store.close_all()


def test_http_sessions_do_not_share_collections():
    app, store = _app()
    try:
        with TestClient(app) as client:
            def start(key: str) -> str:
                res = client.post(
                    "/mcp",
                    headers={**_MCP_HEADERS, "Authorization": f"Bearer {key}"},
                    json=_rpc(
                        "initialize",
                        {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {},
                            "clientInfo": {"name": "test", "version": "0"},
                        },
                    ),
                )
                assert res.status_code == 200, res.text
                sid = res.headers.get("mcp-session-id")
                assert sid
                return sid

            sid_a = start("sk-a")
            sid_b = start("sk-b")
            bound_a = store.get_or_create(sid_a, "sk-a")
            bound_b = store.get_or_create(sid_b, "sk-b")
            bound_a.session._put({"type": "FeatureCollection", "features": []})
            assert "fc_1" in bound_a.session._store
            assert bound_b.session._store == {}
    finally:
        store.close_all()


def _cafe_fc() -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": "node/1",
                "geometry": {"type": "Point", "coordinates": [18.075, 59.316]},
                "properties": {"tags": {"name": "Drop Coffee"}},
            }
        ],
    }


def test_http_preview_and_export_use_public_urls():
    app, store = _app()
    try:
        with TestClient(app) as client:
            headers = _handshake(client)
            sid = headers["mcp-session-id"]
            store.get_or_create(sid, "sk-a").session._put(_cafe_fc())
            tool_headers = {
                **headers,
                "x-forwarded-proto": "https",
                "x-forwarded-host": "api.maplark.com",
            }
            preview = client.post(
                "/mcp",
                headers=tool_headers,
                json=_rpc(
                    "tools/call",
                    {
                        "name": "preview_map",
                        "arguments": {
                            "collection_ids": ["fc_1"],
                            "open_browser": True,
                        },
                    },
                ),
            )
            assert preview.status_code == 200, preview.text
            preview_out = preview.json()["result"]["structuredContent"]
            assert preview_out["opened"] is False
            assert preview_out["preview_url"] == (
                f"https://api.maplark.com/mcp/preview/{sid}/fc_1"
            )

            page = client.get(f"/mcp/preview/{sid}/fc_1")
            assert page.status_code == 200
            assert "text/html" in page.headers["content-type"]
            assert 'fetch(COLLECTION_ID + ".geojson")' in page.text
            assert "Drop Coffee" not in page.text

            geo = client.get(f"/mcp/preview/{sid}/fc_1.geojson")
            assert geo.status_code == 200
            body = geo.json()
            assert body["features"][0]["geometry"]["coordinates"] == [18.075, 59.316]
            assert body["features"][0]["properties"]["name"] == "Drop Coffee"

            exported = client.post(
                "/mcp",
                headers=tool_headers,
                json=_rpc(
                    "tools/call",
                    {"name": "export_geojson", "arguments": {"collection_id": "fc_1"}},
                ),
            )
            assert exported.status_code == 200, exported.text
            export_out = exported.json()["result"]["structuredContent"]
            assert export_out["url"] == (
                f"https://api.maplark.com/mcp/preview/{sid}/fc_1.geojson"
            )
            assert "path" not in export_out
            assert export_out["feature_count"] == 1
    finally:
        store.close_all()


def test_http_preview_is_session_scoped_and_requires_no_bearer():
    app, store = _app()
    try:
        with TestClient(app) as client:
            headers_a = _handshake(client, "sk-a")
            headers_b = _handshake(client, "sk-b")
            sid_a = headers_a["mcp-session-id"]
            sid_b = headers_b["mcp-session-id"]
            store.get_or_create(sid_a, "sk-a").session._put(_cafe_fc())
            missing = client.get(f"/mcp/preview/{sid_b}/fc_1")
            assert missing.status_code == 404
            unknown = client.get("/mcp/preview/not-a-session/fc_1")
            assert unknown.status_code == 404
            page = client.get(f"/mcp/preview/{sid_a}/fc_1")
            assert page.status_code == 200
    finally:
        store.close_all()
