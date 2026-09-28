"""HTTP MCP: Bearer auth, session isolation, initialize + tools/list."""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from mcp.server.transport_security import TransportSecuritySettings
from starlette.testclient import TestClient

from osmfeatures.mcp.mcp_server import HttpSessionStore, build_http_app

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
