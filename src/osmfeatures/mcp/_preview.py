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
    mapped: dict[str, Any] = {"type": "FeatureCollection", "features": features}
    origin = fc.get("search_origin")
    if isinstance(origin, dict) and origin.get("lat") is not None:
        lng = origin.get("lng", origin.get("lon"))
        if lng is not None:
            mapped["search_origin"] = {"lat": float(origin["lat"]), "lng": float(lng)}
    return mapped


def geojson_for_map_many(fcs: list[dict[str, Any]]) -> dict[str, Any]:
    """Concatenate collections so one MapLibre overlay can show places and a route."""
    if len(fcs) == 1:
        return geojson_for_map(fcs[0])
    features: list[dict[str, Any]] = []
    origin: dict[str, float] | None = None
    for fc in fcs:
        mapped = geojson_for_map(fc)
        features.extend(mapped["features"])
        if origin is None and mapped.get("search_origin") is not None:
            origin = mapped["search_origin"]
    out: dict[str, Any] = {"type": "FeatureCollection", "features": features}
    if origin is not None:
        out["search_origin"] = origin
    return out


def validate_collection_id(collection_id: str) -> str:
    cid = (collection_id or "").strip()
    if not _COLLECTION_ID.match(cid):
        raise ValueError(f"invalid collection_id {collection_id!r}")
    return cid


def parse_collection_ids(raw: str | list[str]) -> list[str]:
    """One or more ``fc_N`` ids. URLs use a comma-separated path segment."""
    parts = raw.split(",") if isinstance(raw, str) else list(raw)
    ids = [validate_collection_id(str(p)) for p in parts if str(p).strip()]
    if not ids:
        raise ValueError("expected at least one collection_id")
    seen: set[str] = set()
    unique: list[str] = []
    for cid in ids:
        if cid not in seen:
            seen.add(cid)
            unique.append(cid)
    return unique


def collection_ids_path(collection_ids: str | list[str]) -> str:
    return ",".join(parse_collection_ids(collection_ids))


def preview_html(collection_ids: str | list[str]) -> str:
    """MapLibre page that loads ``/collections/{ids}.geojson`` on a street basemap."""
    cid = collection_ids_path(collection_ids)
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
    .ml-popup {{
      font: 12px/1.35 system-ui, sans-serif;
      max-width: 320px; max-height: 360px; overflow: auto;
    }}
    .ml-popup-tags {{ border-collapse: collapse; width: 100%; }}
    .ml-popup-tags th, .ml-popup-tags td {{
      text-align: left; vertical-align: top; padding: 2px 0;
    }}
    .ml-popup-tags th {{
      color: #555; font-weight: 600; padding-right: 12px; white-space: nowrap;
    }}
    .ml-popup-tags td {{ word-break: break-word; }}
    .ml-origin-marker {{
      width: 16px; height: 16px; border-radius: 50%;
      background: #c2410c; border: 2px solid #fff;
      box-shadow: 0 0 0 2px #c2410c;
    }}
  </style>
</head>
<body>
  <div id="bar">MapLark {cid}</div>
  <div id="map"></div>
  <script src="{_MAPLIBRE_JS}"></script>
  <script>
  const COLLECTION_ID = {cid_js};
  const SKIP_PROP_KEYS = new Set(["tags", "centroid"]);
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
    const origin = fc.search_origin;
    if (origin && origin.lng != null && origin.lat != null) {{
      n += 1;
      minX = Math.min(minX, origin.lng); minY = Math.min(minY, origin.lat);
      maxX = Math.max(maxX, origin.lng); maxY = Math.max(maxY, origin.lat);
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
    function escapeHtml(s) {{
      return String(s)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;");
    }}
    if (fc.search_origin && fc.search_origin.lng != null && fc.search_origin.lat != null) {{
      const el = document.createElement("div");
      el.className = "ml-origin-marker";
      el.title = "Search origin";
      const originRows =
        "<tr><th>" + escapeHtml("label") + "</th><td>" + escapeHtml("Search origin") + "</td></tr>" +
        "<tr><th>" + escapeHtml("lon") + "</th><td>" + escapeHtml(fc.search_origin.lng) + "</td></tr>" +
        "<tr><th>" + escapeHtml("lat") + "</th><td>" + escapeHtml(fc.search_origin.lat) + "</td></tr>";
      new maplibregl.Marker({{ element: el }})
        .setLngLat([fc.search_origin.lng, fc.search_origin.lat])
        .setPopup(new maplibregl.Popup({{ maxWidth: "240px" }}).setHTML(
          '<div class="ml-popup"><table class="ml-popup-tags">' + originRows + "</table></div>"
        ))
        .addTo(map);
      document.getElementById("bar").textContent = "MapLark " + COLLECTION_ID + " · origin marked";
    }}
    const b = boundsOf(fc);
    if (b) map.fitBounds(b, {{ padding: 48, maxZoom: 16 }});
    function parseTags(props) {{
      let tags = props && props.tags;
      if (typeof tags === "string") {{
        try {{ tags = JSON.parse(tags); }} catch (err) {{ tags = null; }}
      }}
      if (!tags || typeof tags !== "object" || Array.isArray(tags)) return null;
      return tags;
    }}
    function formatPropValue(v) {{
      if (v === null || v === undefined) return "";
      if (typeof v === "object") return JSON.stringify(v);
      return String(v);
    }}
    function propPriority(k) {{
      if (k === "name") return 0;
      if (k === "openNow") return 1;
      if (k === "distance_m") return 2;
      if (k === "lon" || k === "lng") return 3;
      if (k === "lat") return 4;
      return 5;
    }}
    function pointLonLat(hit) {{
      const g = hit && hit.geometry;
      if (!g || g.type !== "Point" || !Array.isArray(g.coordinates)) return null;
      const lon = g.coordinates[0], lat = g.coordinates[1];
      if (typeof lon !== "number" || typeof lat !== "number") return null;
      return {{ lon, lat }};
    }}
    function popupHtml(hit) {{
      const props = (hit && hit.properties) || {{}};
      const tags = parseTags(props);
      const rows = [];
      const seen = new Set();
      const flatKeys = Object.keys(props).filter((k) =>
        !SKIP_PROP_KEYS.has(k) && props[k] !== undefined && props[k] !== null
      );
      flatKeys.sort((a, b) => {{
        const d = propPriority(a) - propPriority(b);
        return d !== 0 ? d : a.localeCompare(b);
      }});
      for (const k of flatKeys) {{
        rows.push([k, formatPropValue(props[k])]);
        seen.add(k);
      }}
      if (!seen.has("lon") && !seen.has("lng") && !seen.has("lat")) {{
        const pt = pointLonLat(hit);
        if (pt) {{
          rows.push(["lon", formatPropValue(pt.lon)]);
          rows.push(["lat", formatPropValue(pt.lat)]);
          seen.add("lon");
          seen.add("lat");
        }}
      }}
      if (tags) {{
        const tagKeys = Object.keys(tags).filter((k) => !seen.has(k));
        tagKeys.sort((a, b) => {{
          if (a === "name") return -1;
          if (b === "name") return 1;
          return a.localeCompare(b);
        }});
        for (const k of tagKeys) {{
          rows.push([k, formatPropValue(tags[k])]);
        }}
      }}
      if (rows.length) {{
        const htmlRows = rows.map((pair) =>
          "<tr><th>" + escapeHtml(pair[0]) + "</th><td>" + escapeHtml(pair[1]) + "</td></tr>"
        );
        return '<div class="ml-popup"><table class="ml-popup-tags">' + htmlRows.join("") + "</table></div>";
      }}
      const fallback = props.id || hit.id || COLLECTION_ID;
      return "<div class=\\"ml-popup\\">" + escapeHtml(fallback) + "</div>";
    }}
    map.on("click", (e) => {{
      const hit = map.queryRenderedFeatures(e.point, {{
        layers: ["overlay-fill", "overlay-line", "overlay-point"]
      }})[0];
      if (!hit) return;
      new maplibregl.Popup({{ maxWidth: "360px" }})
        .setLngLat(e.lngLat)
        .setHTML(popupHtml(hit))
        .addTo(map);
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

    def preview_url(self, collection_ids: str | list[str]) -> str:
        ids = parse_collection_ids(collection_ids)
        for cid in ids:
            self._session.get(cid)
        return f"{self.base_url}/preview/{','.join(ids)}"

    def geojson_url(self, collection_ids: str | list[str]) -> str:
        ids = parse_collection_ids(collection_ids)
        for cid in ids:
            self._session.get(cid)
        return f"{self.base_url}/collections/{','.join(ids)}.geojson"

    def open(
        self,
        collection_ids: str | list[str],
        *,
        open_browser: bool = True,
    ) -> dict[str, Any]:
        """Return preview URLs. Does not include geometry."""
        ids = parse_collection_ids(collection_ids)
        for cid in ids:
            self._session.get(cid)
        preview_url = f"{self.base_url}/preview/{','.join(ids)}"
        opened = False
        if open_browser:
            opened = bool(webbrowser.open(preview_url))
        return {
            "collection_ids": ids,
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
                    ids = parse_collection_ids(cid)
                    for item in ids:
                        session.get(item)
                except (KeyError, ValueError):
                    self._send(404, b"unknown collection\n", "text/plain; charset=utf-8")
                    return
                body = preview_html(ids).encode("utf-8")
                self._send(200, body, "text/html; charset=utf-8")
                return
            if path.startswith("/collections/") and path.endswith(".geojson"):
                cid = path[len("/collections/") : -len(".geojson")]
                try:
                    ids = parse_collection_ids(cid)
                    payload = geojson_for_map_many(
                        [session.export_geojson(item) for item in ids]
                    )
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
