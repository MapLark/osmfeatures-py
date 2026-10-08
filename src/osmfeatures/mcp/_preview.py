"""Local MapLibre preview. The browser fetches GeoJSON; the planner only gets a URL."""

from __future__ import annotations

import json
import re
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

# Planner save_as labels (pubs) or server-minted fc_N. No slashes/commas (URL path).
_COLLECTION_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SAVE_AS_SEP = re.compile(r"[\s-]+")
_OSM_REF = re.compile(r"^(node|way|relation)/[1-9]\d*$", re.IGNORECASE)

# Vector basemap, no API key. Overlay GeoJSON is fetched from this process.
_MAP_STYLE = "https://tiles.openfreemap.org/styles/liberty"
_MAPLIBRE_JS = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.js"
_MAPLIBRE_CSS = "https://unpkg.com/maplibre-gl@4.7.1/dist/maplibre-gl.css"


def is_density_grid(payload: Any) -> bool:
    """Occupied density bins: Point features with ``properties.count`` and no OSM id."""
    if not isinstance(payload, dict):
        return False
    feats = payload.get("features") or []
    if not isinstance(feats, list) or not feats or not isinstance(feats[0], dict):
        return False
    first = feats[0]
    if first.get("id"):
        return False
    props = first.get("properties") or {}
    return isinstance(props, dict) and "count" in props


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


def feature_osm_id(feat: dict[str, Any]) -> str | None:
    """GeoJSON ``id`` when it is ``node|way|relation/<positive id>``.

    MapLibre drops string feature ids on click, so the preview copies this into
    ``properties.osm_id`` for the popup.
    """
    fid = feat.get("id")
    if not isinstance(fid, str):
        return None
    raw = fid.strip()
    if not _OSM_REF.match(raw):
        return None
    kind, n = raw.split("/", 1)
    return f"{kind.lower()}/{n}"


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
        osm_id = feature_osm_id(out)
        if osm_id is not None:
            props["osm_id"] = osm_id
        out["properties"] = props
        features.append(out)
    mapped: dict[str, Any] = {"type": "FeatureCollection", "features": features}
    if is_density_grid(fc):
        mapped["kind"] = "density"
        if fc.get("total") is not None:
            mapped["total"] = fc["total"]
        if "truncated" in fc:
            mapped["truncated"] = bool(fc["truncated"])
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
    if any(is_density_grid(fc) for fc in fcs):
        out["kind"] = "density"
        totals = [fc.get("total") for fc in fcs if isinstance(fc, dict) and fc.get("total") is not None]
        if totals:
            out["total"] = sum(int(t) for t in totals)
    return out


def validate_collection_id(collection_id: str) -> str:
    cid = (collection_id or "").strip()
    if not _COLLECTION_ID.match(cid):
        raise ValueError(f"invalid collection_id {collection_id!r}")
    return cid


def normalize_save_as(label: str) -> str:
    """Hyphens and whitespace become ``_``. Other invalid labels still raise."""
    compact = _SAVE_AS_SEP.sub("_", (label or "").strip()).strip("_")
    return validate_collection_id(compact)


def parse_collection_ids(raw: str | list[str]) -> list[str]:
    """One or more collection ids. URLs use a comma-separated path segment."""
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
    """MapLibre page that loads a relative ``{ids}.geojson`` next to this HTML."""
    cid = collection_ids_path(collection_ids)
    cid_js = json.dumps(cid)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <title>MapLark {cid}</title>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <meta name="referrer" content="no-referrer"/>
  <link rel="stylesheet" href="{_MAPLIBRE_CSS}"/>
  <style>
    html, body, #map {{ margin: 0; height: 100%; background: #e8e4dc; }}
    #bar {{
      position: absolute; z-index: 1; top: 10px; left: 10px;
      font: 13px/1.4 system-ui, sans-serif; background: #fff; padding: 8px 12px;
      border-radius: 6px; box-shadow: 0 1px 4px rgba(0,0,0,.15);
      max-width: min(280px, calc(100vw - 24px));
    }}
    #heat-controls {{
      display: flex; flex-direction: column; gap: 6px; margin-top: 8px;
      padding-top: 8px; border-top: 1px solid #e5e5e5;
    }}
    #heat-controls[hidden] {{ display: none !important; }}
    .heat-row {{
      display: grid; grid-template-columns: 68px 1fr 52px;
      align-items: center; gap: 6px; font-size: 12px; color: #333;
    }}
    .heat-row input[type="range"] {{ width: 100%; margin: 0; }}
    .heat-row output {{
      font-variant-numeric: tabular-nums; text-align: right; color: #555;
    }}
    .heat-check {{
      display: flex; align-items: center; gap: 6px; font-size: 12px; color: #333;
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
    .ml-popup-tags a {{ color: #1d4ed8; }}
    .ml-origin-marker {{
      width: 16px; height: 16px; border-radius: 50%;
      background: #c2410c; border: 2px solid #fff;
      box-shadow: 0 0 0 2px #c2410c;
    }}
  </style>
</head>
<body>
  <div id="bar">
    <div id="bar-title">MapLark {cid}</div>
    <div id="heat-controls" hidden>
      <label class="heat-row">
        <span>Intensity</span>
        <input id="heat-intensity" type="range" min="0" max="100" value="76"/>
        <output id="heat-intensity-val" for="heat-intensity">0.80</output>
      </label>
      <label class="heat-row">
        <span>Radius</span>
        <input id="heat-radius" type="range" min="1" max="120" value="28"/>
        <output id="heat-radius-val" for="heat-radius">28</output>
      </label>
      <label class="heat-row">
        <span>Opacity</span>
        <input id="heat-opacity" type="range" min="0" max="100" value="88"/>
        <output id="heat-opacity-val" for="heat-opacity">0.88</output>
      </label>
      <label class="heat-check">
        <input id="heat-zoom" type="checkbox" checked/>
        Scale with zoom
      </label>
    </div>
  </div>
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

  function intensityFromSlider(v) {{
    return Math.pow(10, -4 + (Number(v) / 100) * 5);
  }}
  function formatIntensity(x) {{
    if (x >= 0.01) return x.toFixed(2);
    if (x >= 0.001) return x.toFixed(4);
    return x.toExponential(1);
  }}
  function applyHeatPaint() {{
    const intensity = intensityFromSlider(document.getElementById("heat-intensity").value);
    const radius = Number(document.getElementById("heat-radius").value);
    const opacity = Number(document.getElementById("heat-opacity").value) / 100;
    const scaleZoom = document.getElementById("heat-zoom").checked;
    document.getElementById("heat-intensity-val").textContent = formatIntensity(intensity);
    document.getElementById("heat-radius-val").textContent = String(radius);
    document.getElementById("heat-opacity-val").textContent = opacity.toFixed(2);
    if (!map.getLayer("overlay-heat")) return;
    if (scaleZoom) {{
      map.setPaintProperty("overlay-heat", "heatmap-intensity", [
        "interpolate", ["linear"], ["zoom"],
        2, intensity * 0.06,
        5, intensity * 0.22,
        8, intensity * 0.7,
        11, intensity,
        14, intensity * 1.55
      ]);
      map.setPaintProperty("overlay-heat", "heatmap-radius", [
        "interpolate", ["exponential", 2], ["zoom"],
        2, Math.max(6, radius * 0.3),
        6, Math.max(10, radius * 0.55),
        9, radius,
        12, Math.min(120, radius * 2.8),
        15, Math.min(120, radius * 5.5)
      ]);
    }} else {{
      map.setPaintProperty("overlay-heat", "heatmap-intensity", intensity);
      map.setPaintProperty("overlay-heat", "heatmap-radius", radius);
    }}
    map.setPaintProperty("overlay-heat", "heatmap-opacity", opacity);
  }}

  map.on("load", async () => {{
    const res = await fetch(COLLECTION_ID + ".geojson");
    if (!res.ok) {{
      document.getElementById("bar-title").textContent = "No geometry for " + COLLECTION_ID;
      return;
    }}
    const fc = await res.json();
    const isDensity = fc.kind === "density";
    map.addSource("overlay", {{ type: "geojson", data: fc }});
    if (isDensity) {{
      const counts = (fc.features || []).map((f) => Number(f.properties && f.properties.count) || 0);
      const maxCount = Math.max(1, ...counts);
      const lnMax = Math.log(1 + maxCount);
      map.addLayer({{
        id: "overlay-heat",
        type: "heatmap",
        source: "overlay",
        paint: {{
          "heatmap-weight": [
            "interpolate", ["linear"],
            ["/", ["ln", ["+", ["coalesce", ["to-number", ["get", "count"]], 0], 1]], lnMax],
            0, 0, 1, 1
          ],
          "heatmap-intensity": 0.8,
          "heatmap-radius": 28,
          "heatmap-opacity": 0.88,
          "heatmap-color": [
            "interpolate", ["linear"], ["heatmap-density"],
            0, "rgba(29,78,216,0)",
            0.08, "rgba(29,78,216,0.35)",
            0.25, "#38bdf8",
            0.45, "#fbbf24",
            0.7, "#ea580c",
            1, "#9f1239"
          ]
        }}
      }});
      map.addLayer({{
        id: "overlay-point",
        type: "circle",
        source: "overlay",
        paint: {{
          "circle-radius": 10,
          "circle-color": "#1d4ed8",
          "circle-opacity": 0,
          "circle-stroke-width": 0
        }}
      }});
      document.getElementById("heat-controls").hidden = false;
      applyHeatPaint();
      ["heat-intensity", "heat-radius", "heat-opacity", "heat-zoom"].forEach((id) => {{
        document.getElementById(id).addEventListener("input", applyHeatPaint);
      }});
      const n = fc.total != null ? fc.total : (fc.features || []).length;
      document.getElementById("bar-title").textContent =
        "MapLark " + COLLECTION_ID + " · heatmap · " + n + " features";
    }} else {{
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
    }}
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
      if (!isDensity) {{
        document.getElementById("bar-title").textContent = "MapLark " + COLLECTION_ID + " · origin marked";
      }}
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
    function isWebsiteKey(k) {{
      const key = String(k);
      return key === "url" || key.endsWith(":url") || key === "website" || key.includes("website");
    }}
    function safeHttpHref(raw, allowBareHost) {{
      const s = String(raw).trim();
      if (!s) return null;
      let href = s;
      if (!/^[a-z][a-z0-9+.-]*:/i.test(href)) {{
        if (!allowBareHost) return null;
        href = "https://" + href;
      }}
      try {{
        const u = new URL(href);
        if (u.protocol !== "http:" && u.protocol !== "https:") return null;
        return u.href;
      }} catch (err) {{
        return null;
      }}
    }}
    function osmOrgHref(raw) {{
      const m = String(raw).trim().match(/^(node|way|relation)\\/([1-9]\\d*)$/i);
      if (!m) return null;
      return "https://www.openstreetmap.org/" + m[1].toLowerCase() + "/" + m[2];
    }}
    function hrefFor(key, value) {{
      if (key === "osm_id") return osmOrgHref(value);
      return safeHttpHref(value, isWebsiteKey(key));
    }}
    function formatCell(key, value) {{
      const text = formatPropValue(value);
      const href = hrefFor(key, text);
      if (href) {{
        return '<a href="' + escapeHtml(href) + '" target="_blank" rel="noopener noreferrer">'
          + escapeHtml(text) + "</a>";
      }}
      return escapeHtml(text);
    }}
    function propPriority(k) {{
      if (k === "osm_id") return 0;
      if (k === "name") return 1;
      if (k === "openNow") return 2;
      if (k === "distance_m") return 3;
      if (k === "lon" || k === "lng") return 4;
      if (k === "lat") return 5;
      return 6;
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
          "<tr><th>" + escapeHtml(pair[0]) + "</th><td>" + formatCell(pair[0], pair[1]) + "</td></tr>"
        );
        return '<div class="ml-popup"><table class="ml-popup-tags">' + htmlRows.join("") + "</table></div>";
      }}
      const fallback = props.id || hit.id || COLLECTION_ID;
      return "<div class=\\"ml-popup\\">" + escapeHtml(fallback) + "</div>";
    }}
    map.on("click", (e) => {{
      const hitLayers = isDensity
        ? ["overlay-point"]
        : ["overlay-fill", "overlay-line", "overlay-point"];
      const hit = map.queryRenderedFeatures(e.point, {{
        layers: hitLayers.filter((id) => map.getLayer(id))
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

    def export_file(self, collection_id: str) -> dict[str, Any]:
        return self._session.export_geojson_file(collection_id)

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2.0)


def mapped_geojson(session: Any, collection_ids: str | list[str]) -> dict[str, Any]:
    ids = parse_collection_ids(collection_ids)
    return geojson_for_map_many([session.export_geojson(item) for item in ids])


def _handler_for(session: Any) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:
            path = urlparse(self.path).path.rstrip("/")
            if path.startswith("/preview/"):
                rest = path[len("/preview/") :]
                if rest.endswith(".geojson"):
                    self._send_geojson(rest[: -len(".geojson")])
                    return
                try:
                    ids = parse_collection_ids(rest)
                    for item in ids:
                        session.get(item)
                except (KeyError, ValueError):
                    self._send(404, b"unknown collection\n", "text/plain; charset=utf-8")
                    return
                body = preview_html(ids).encode("utf-8")
                self._send(200, body, "text/html; charset=utf-8")
                return
            if path.startswith("/collections/") and path.endswith(".geojson"):
                self._send_geojson(path[len("/collections/") : -len(".geojson")])
                return
            self._send(404, b"not found\n", "text/plain; charset=utf-8")

        def _send_geojson(self, raw_ids: str) -> None:
            try:
                payload = mapped_geojson(session, raw_ids)
            except (KeyError, ValueError, TypeError):
                self._send(404, b"unknown collection\n", "text/plain; charset=utf-8")
                return
            body = json.dumps(payload).encode("utf-8")
            self._send(200, body, "application/geo+json; charset=utf-8")

        def _send(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            self.wfile.write(body)

    return Handler
