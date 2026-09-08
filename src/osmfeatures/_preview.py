"""Local MapLibre preview. The browser fetches GeoJSON; the planner only gets a URL."""

from __future__ import annotations

import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

_COLLECTION_ID = re.compile(r"^fc_[0-9]+$")

# Vector basemap, no API key. Overlay GeoJSON is fetched from this process.
_MAP_STYLE = "https://tiles.openfreemap.org/styles/liberty"
_MAPLIBRE_JS = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"
_MAPLIBRE_CSS = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"


def feature_popup_name(feat: dict[str, Any]) -> str | None:
    """OSM ``tags.name``, else ``properties.name``. MapLibre keeps top-level strings."""
    props = feat.get("properties")
    if not isinstance(props, dict):
        return None
    tags = props.get("tags")
    if isinstance(tags, dict) and tags.get("name"):
        return str(tags["name"])
    if props.get("name"):
        return str(props["name"])
    return None


def geojson_for_map(fc: dict[str, Any]) -> dict[str, Any]:
    """Copy a FeatureCollection with ``properties.name`` as a string MapLibre can show."""
    features: list[dict[str, Any]] = []
    for feat in fc.get("features") or []:
        if not isinstance(feat, dict):
            continue
        out = dict(feat)
        props = dict(out.get("properties") or {})
        name = feature_popup_name(out)
        if name is not None:
            props["name"] = name
        out["properties"] = props
        features.append(out)
    return {"type": "FeatureCollection", "features": features}


def validate_collection_id(collection_id: str) -> str:
    cid = (collection_id or "").strip()
    if not _COLLECTION_ID.match(cid):
        raise ValueError(f"invalid collection_id {collection_id!r}")
    return cid


def preview_html(collection_id: str) -> str:
    """MapLibre page that loads ``/collections/{id}.geojson`` on a street basemap."""
    cid = validate_collection_id(collection_id)
    cid_js = json.dumps(cid)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>MapLark {cid}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <link rel="stylesheet" href="{_MAPLIBRE_CSS}"/>
  <style>
    html, body, #map {{ margin: 0; height: 100%; background: #e8e4dc; }}
    #bar {{
      position: absolute; z-index: 1; top: 10px; left: 10px;
      font: 13px/1.4 system-ui, sans-serif; background: #fff; padding: 6px 10px;
      border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,.15);
    }}
  </style>
</head>
<body>
  <div id="bar">MapLark {cid}</div>
  <div id="map"></div>
  <script src="{_MAPLIBRE_JS}"></script>
  <script>
  const COLLECTION_ID = {cid_js};
  const map = new maplibregl.Map({{
    container: "map",
    style: {_MAP_STYLE!r},
    center: [0, 20],
    zoom: 1
  }});
  map.addControl(new maplibregl.NavigationControl(), "top-right");

  function walkCoords(c, fn) {{
    if (typeof c[0] === "number") fn(c);
    else for (const x of c) walkCoords(x, fn);
  }}
  function boundsOf(fc) {{
    let minX = 180, minY = 90, maxX = -180, maxY = -90, n = 0;
    for (const f of fc.features || []) {{
      const g = f && f.geometry;
      if (!g || !g.coordinates) continue;
      walkCoords(g.coordinates, (c) => {{
        n += 1;
        minX = Math.min(minX, c[0]); minY = Math.min(minY, c[1]);
        maxX = Math.max(maxX, c[0]); maxY = Math.max(maxY, c[1]);
      }});
    }}
    return n ? [[minX, minY], [maxX, maxY]] : null;
  }}

  map.on("load", async () => {{
    const res = await fetch("/collections/" + COLLECTION_ID + ".geojson");
    if (!res.ok) {{
      document.getElementById("bar").textContent = "No geometry for " + COLLECTION_ID;
      return;
    }}
    const fc = await res.json();
    map.addSource("overlay", {{ type: "geojson", data: fc }});
    map.addLayer({{
      id: "overlay-fill",
      type: "fill",
      source: "overlay",
      filter: ["in", ["geometry-type"], ["literal", ["Polygon", "MultiPolygon"]]],
      paint: {{ "fill-color": "#1d4ed8", "fill-opacity": 0.22 }}
    }});
    map.addLayer({{
      id: "overlay-line",
      type: "line",
      source: "overlay",
      filter: ["in", ["geometry-type"], ["literal", ["LineString", "MultiLineString", "Polygon", "MultiPolygon"]]],
      paint: {{ "line-color": "#1d4ed8", "line-width": 2.5 }}
    }});
    map.addLayer({{
      id: "overlay-point",
      type: "circle",
      source: "overlay",
      filter: ["in", ["geometry-type"], ["literal", ["Point", "MultiPoint"]]],
      paint: {{
        "circle-radius": 6,
        "circle-color": "#1d4ed8",
        "circle-stroke-width": 1.5,
        "circle-stroke-color": "#fff"
      }}
    }});
    const b = boundsOf(fc);
    if (b) map.fitBounds(b, {{ padding: 48, maxZoom: 16 }});
    function popupLabel(hit) {{
      const props = (hit && hit.properties) || {{}};
      let tags = props.tags;
      if (typeof tags === "string") {{
        try {{ tags = JSON.parse(tags); }} catch (err) {{ tags = null; }}
      }}
      const fromTags = tags && typeof tags === "object" ? tags.name : undefined;
      return fromTags || props.name || props.id || hit.id || COLLECTION_ID;
    }}
    map.on("click", (e) => {{
      const hit = map.queryRenderedFeatures(e.point, {{
        layers: ["overlay-fill", "overlay-line", "overlay-point"]
      }})[0];
      if (!hit) return;
      new maplibregl.Popup().setLngLat(e.lngLat).setText(String(popupLabel(hit))).addTo(map);
    }});
  }});
  </script>
</body>
</html>
"""


class PreviewServer:
    """127.0.0.1 HTTP server: preview HTML + GeoJSON for one :class:`GeoAgentSession`."""

    def __init__(self, session: Any, *, host: str = "127.0.0.1") -> None:
        self._session = session
        handler = _handler_for(session)
        self._httpd = ThreadingHTTPServer((host, 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    def preview_url(self, collection_id: str) -> str:
        cid = validate_collection_id(collection_id)
        self._session.get(cid)
        return f"{self.base_url}/preview/{cid}"

    def geojson_url(self, collection_id: str) -> str:
        cid = validate_collection_id(collection_id)
        self._session.get(cid)
        return f"{self.base_url}/collections/{cid}.geojson"

    def open(
        self,
        collection_id: str,
        *,
        open_browser: bool = True,
    ) -> dict[str, Any]:
        """Return preview URLs. Does not include geometry."""
        cid = validate_collection_id(collection_id)
        self._session.get(cid)
        preview_url = f"{self.base_url}/preview/{cid}"
        opened = False
        if open_browser:
            opened = bool(webbrowser.open(preview_url))
        return {
            "collection_id": cid,
            "preview_url": preview_url,
            "opened": opened,
        }

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2.0)


def _handler_for(session: Any) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:
            path = urlparse(self.path).path.rstrip("/")
            if path.startswith("/preview/"):
                cid = path[len("/preview/") :]
                try:
                    session.get(validate_collection_id(cid))
                except (KeyError, ValueError):
                    self._send(404, b"unknown collection\n", "text/plain; charset=utf-8")
                    return
                body = preview_html(cid).encode("utf-8")
                self._send(200, body, "text/html; charset=utf-8")
                return
            if path.startswith("/collections/") and path.endswith(".geojson"):
                cid = path[len("/collections/") : -len(".geojson")]
                try:
                    payload = geojson_for_map(session.export_geojson(validate_collection_id(cid)))
                except (KeyError, ValueError, TypeError):
                    self._send(404, b"unknown collection\n", "text/plain; charset=utf-8")
                    return
                body = json.dumps(payload).encode("utf-8")
                self._send(200, body, "application/geo+json; charset=utf-8")
                return
            self._send(404, b"not found\n", "text/plain; charset=utf-8")

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

    return Handler
