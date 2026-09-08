"""In-process geo-agent tool session. No MCP dependency.

HTTP tools call :class:`OSMFeaturesClient`. Local tools use ``nearest_within``,
``point_in_geometry``, and an opening-hours status filter. Results are stored
by ``fc_N``; planner-facing summaries omit coordinate arrays.
"""

from __future__ import annotations

import json
import tempfile
from collections import OrderedDict
from pathlib import Path
from typing import Any

from .geometry import point_in_geometry
from .models import OSMFeatureCollection
from .nearest import MAX_COMPARISONS, nearest_within
from ._preview import validate_collection_id

# Planner summaries list at most this many items; ``count`` is still the full total.
SUMMARY_ITEM_CAP = 40
# MCP ``query_all`` default cap (SDK ``query_all`` stays 55_000). Raise if ``has_more``.
QUERY_ALL_MAX_FEATURES = 10000
_STORE_MAX_COLLECTIONS = 64
_CONTEXT_KEYS = ("evaluated_at", "timezone", "estimated_units")
_HOURS_KEYS = ("evaluated_at", "timezone")


def _context_fields(
    payload: Any,
    *,
    keys: tuple[str, ...] = _CONTEXT_KEYS,
) -> dict[str, Any]:
    """Copy hours/billing fields from a payload or its ``metadata`` object."""
    out: dict[str, Any] = {}
    if not isinstance(payload, dict):
        return out
    blobs: list[dict[str, Any]] = []
    meta = payload.get("metadata")
    if isinstance(meta, dict):
        blobs.append(meta)
    blobs.append(payload)
    for blob in blobs:
        for key in keys:
            if key in out:
                continue
            value = blob.get(key)
            if value is not None:
                out[key] = value
    return out


def opening_status(feat: dict[str, Any]) -> str:
    hours = ((feat.get("properties") or {}).get("openingHours") or {})
    return hours.get("status") or "unknown"


def feature_name(feat: dict[str, Any]) -> str | None:
    tags = ((feat.get("properties") or {}).get("tags") or {})
    name = tags.get("name")
    return str(name) if name else None


def _as_fc_dict(payload: Any) -> dict[str, Any]:
    if isinstance(payload, OSMFeatureCollection):
        return payload.to_dict()
    if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
        return {
            "type": "FeatureCollection",
            "features": list(payload.get("features") or []),
        }
    raise TypeError("expected a GeoJSON FeatureCollection")


def _features_from_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [p for p in payload if isinstance(p, dict) and p.get("type") == "Feature"]
    if isinstance(payload, OSMFeatureCollection):
        return [dict(f) for f in payload.get("features") or []]
    if not isinstance(payload, dict):
        return []
    if payload.get("type") == "FeatureCollection":
        return list(payload.get("features") or [])
    if payload.get("type") == "Feature":
        return [payload]
    items = payload.get("items")
    if isinstance(items, list):
        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            feat = item.get("feature")
            if isinstance(feat, dict):
                out.append(feat)
        return out
    feat = payload.get("feature")
    if isinstance(feat, dict):
        return [feat]
    return []


_POLYGON_TYPES = frozenset({"Polygon", "MultiPolygon"})


def _geometry_from_payload(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    geom = payload.get("geometry")
    if isinstance(geom, dict) and geom.get("type"):
        return geom
    return None


def _is_polygon_geom(geom: Any) -> bool:
    return isinstance(geom, dict) and geom.get("type") in _POLYGON_TYPES


def _polygon_geoms(payload: Any) -> list[dict[str, Any]]:
    """Polygon/MultiPolygon geoms: top-level (isochrone) or per-feature (query)."""
    geom = _geometry_from_payload(payload)
    if _is_polygon_geom(geom):
        return [geom]
    out: list[dict[str, Any]] = []
    for feat in _features_from_payload(payload):
        feat_geom = feat.get("geometry")
        if _is_polygon_geom(feat_geom):
            out.append(feat_geom)
    return out


def _lon_lat(feat: dict[str, Any]) -> tuple[float, float] | None:
    geom = feat.get("geometry") or {}
    if isinstance(geom, dict) and geom.get("type") == "Point":
        coords = geom.get("coordinates") or []
        if len(coords) >= 2:
            return float(coords[0]), float(coords[1])
    centroid = ((feat.get("properties") or {}).get("centroid") or {})
    if isinstance(centroid, dict) and centroid.get("type") == "Point":
        coords = centroid.get("coordinates") or []
        if len(coords) >= 2:
            return float(coords[0]), float(coords[1])
    return None


def _require_positive_max_features(max_features: int | None) -> int:
    """MCP must not pass ``None`` through to the SDK (that means no cap)."""
    if max_features is None:
        raise ValueError(
            "max_features cannot be None (unlimited). "
            f"Omit it to use {QUERY_ALL_MAX_FEATURES}, or pass a positive int."
        )
    if max_features < 1:
        raise ValueError("max_features must be a positive int")
    return max_features


def require_places_search_spatial(
    bbox: str | None,
    location: dict[str, float] | None,
) -> None:
    """Places search is bbox *or* location+radius, never both or neither."""
    if bbox and location is not None:
        raise ValueError("places_search accepts bbox or location+radius, not both")
    if not bbox and location is None:
        raise ValueError("places_search requires bbox or location+radius")


def _stop_item(stop: Any, index: int) -> dict[str, Any]:
    """Planner-facing stop: lon/lat scalars plus id/name when the API sent them."""
    if not isinstance(stop, dict):
        return {"index": index, "value": stop}
    item: dict[str, Any] = {}
    if stop.get("id") is not None:
        item["id"] = stop["id"]
    if stop.get("name") is not None:
        item["name"] = stop["name"]
    lon = stop.get("lon", stop.get("lng"))
    lat = stop.get("lat")
    if lon is not None and lat is not None:
        item["lon"] = float(lon)
        item["lat"] = float(lat)
    if not item:
        item["index"] = index
    return item


def _place_item(feat: dict[str, Any], *, distance_m: float | None = None) -> dict[str, Any]:
    hours = ((feat.get("properties") or {}).get("openingHours") or {})
    item: dict[str, Any] = {
        "id": feat.get("id"),
        "name": feature_name(feat),
        "openingHours": {
            "status": hours.get("status") or "unknown",
            "openNow": hours.get("openNow"),
        },
    }
    pt = _lon_lat(feat)
    if pt is not None:
        item["lon"], item["lat"] = pt
    if distance_m is not None:
        item["distance_m"] = distance_m
    return item


def _feature_seq(payload: Any) -> list[Any]:
    """Feature list without copying each feature to a new dict."""
    if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
        feats = payload.get("features") or []
        return feats if isinstance(feats, list) else list(feats)
    return _features_from_payload(payload)


def _nearby_items(
    payload: dict[str, Any],
    *,
    cap: int = SUMMARY_ITEM_CAP,
) -> tuple[int, list[dict[str, Any]]]:
    count = 0
    out: list[dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        feat = item.get("feature")
        if not isinstance(feat, dict):
            continue
        dist = item.get("distance_m")
        count += 1
        if len(out) < cap:
            out.append(_place_item(feat, distance_m=float(dist) if dist is not None else None))
    return count, out


def _summary_items(
    collection_id: str,
    count: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Planner summary: full ``count``, ``items`` capped. Flag when the list is a prefix."""
    out: dict[str, Any] = {
        "collection_id": collection_id,
        "count": count,
        "items": items,
    }
    if count > len(items):
        out["items_truncated"] = True
    return out


def assert_no_coordinate_arrays(obj: Any) -> None:
    """Raise if a planner-facing payload still has GeoJSON coordinate arrays."""
    if isinstance(obj, dict):
        if "coordinates" in obj:
            raise AssertionError("planner payload must not include coordinates")
        for value in obj.values():
            assert_no_coordinate_arrays(value)
    elif isinstance(obj, list):
        for value in obj:
            assert_no_coordinate_arrays(value)


class GeoAgentSession:
    """Per-connection collection store plus thin client/local tool methods."""

    def __init__(
        self,
        client: Any,
        *,
        max_collections: int = _STORE_MAX_COLLECTIONS,
    ) -> None:
        self._client = client
        self._store: OrderedDict[str, Any] = OrderedDict()
        self._next = 1
        self._max_collections = max_collections

    def _put(self, payload: Any) -> str:
        key = f"fc_{self._next}"
        self._next += 1
        self._store[key] = payload
        while len(self._store) > self._max_collections:
            self._store.popitem(last=False)
        return key

    def get(self, collection_id: str) -> Any:
        try:
            payload = self._store[collection_id]
        except KeyError as exc:
            raise KeyError(f"unknown collection_id {collection_id!r}") from exc
        self._store.move_to_end(collection_id)
        return payload

    def _summarize_places(self, collection_id: str, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            count, items = _nearby_items(payload)
        else:
            feats = _feature_seq(payload)
            count = len(feats)
            items = [_place_item(f) for f in feats[:SUMMARY_ITEM_CAP]]
        summary = _summary_items(collection_id, count, items)
        summary.update(_context_fields(payload))
        return summary

    def _summarize_route(self, collection_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        ordered = payload.get("ordered_stops") or []
        stops: list[dict[str, Any]] = []
        if isinstance(ordered, list):
            stops = [_stop_item(stop, i) for i, stop in enumerate(ordered)]
        return {
            "collection_id": collection_id,
            "status": payload.get("status"),
            "distance_m": payload.get("distance_m"),
            "duration_s": payload.get("duration_s"),
            "stop_distances_m": payload.get("stop_distances_m"),
            "ordered_stop_count": len(stops),
            "ordered_stops": stops,
        }

    def places_search(
        self,
        *,
        bbox: str | None = None,
        location: dict[str, float] | None = None,
        radius: float | None = None,
        tags: list[str] | None = None,
        or_tags: list[str] | None = None,
        limit: int | None = None,
        open_now: bool = False,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        require_places_search_spatial(bbox, location)
        payload = self._client.places_search(
            bbox=bbox,
            location=location,
            radius=radius,
            tags=tags,
            or_tags=or_tags,
            limit=limit,
            open_now=open_now,
            as_of=as_of,
        )
        cid = self._put(payload)
        return self._summarize_places(cid, payload)

    def places_nearby(
        self,
        *,
        location: dict[str, float],
        radius: float | None = None,
        tags: list[str] | None = None,
        or_tags: list[str] | None = None,
        limit: int | None = None,
        open_now: bool = False,
        as_of: str | None = None,
    ) -> dict[str, Any]:
        payload = self._client.places_nearby(
            location=location,
            radius=radius,
            tags=tags,
            or_tags=or_tags,
            limit=limit,
            open_now=open_now,
            as_of=as_of,
        )
        cid = self._put(payload)
        return self._summarize_places(cid, payload)

    def places_details(self, osm_type: str, osm_id: int | str | None = None) -> dict[str, Any]:
        payload = self._client.places_details(osm_type, osm_id)
        cid = self._put(payload)
        feats = _features_from_payload(payload)
        item = _place_item(feats[0]) if feats else None
        out: dict[str, Any] = {"collection_id": cid, "item": item}
        out.update(_context_fields(payload))
        return out

    def routes_isochrone(
        self,
        *,
        origin: dict[str, float],
        max_distance_m: float | None = None,
        duration_s: float | None = None,
        search_buffer_m: float | None = None,
        travel_mode: str | None = None,
    ) -> dict[str, Any]:
        payload = self._client.routes_isochrone(
            origin=origin,
            max_distance_m=max_distance_m,
            duration_s=duration_s,
            search_buffer_m=search_buffer_m,
            travel_mode=travel_mode,
        )
        cid = self._put(payload)
        out = {
            "collection_id": cid,
            "status": payload.get("status") if isinstance(payload, dict) else None,
            "distance_m": payload.get("distance_m") if isinstance(payload, dict) else None,
            "duration_s": payload.get("duration_s") if isinstance(payload, dict) else None,
            "geometry_type": (payload.get("geometry") or {}).get("type")
            if isinstance(payload, dict)
            else None,
        }
        return out

    def routes_path(
        self,
        *,
        stops: list[dict[str, float]],
        search_buffer_m: float | None = None,
        travel_mode: str | None = None,
    ) -> dict[str, Any]:
        payload = self._client.routes_path(
            stops=stops,
            search_buffer_m=search_buffer_m,
            travel_mode=travel_mode,
        )
        cid = self._put(payload)
        return self._summarize_route(cid, payload if isinstance(payload, dict) else {})

    def routes_optimized_path(
        self,
        *,
        start: dict[str, float],
        stops: list[dict[str, float]],
        search_buffer_m: float | None = None,
        loop: bool | None = None,
        travel_mode: str | None = None,
    ) -> dict[str, Any]:
        payload = self._client.routes_optimized_path(
            start=start,
            stops=stops,
            search_buffer_m=search_buffer_m,
            loop=loop,
            travel_mode=travel_mode,
        )
        cid = self._put(payload)
        return self._summarize_route(cid, payload if isinstance(payload, dict) else {})

    def query(
        self,
        *,
        bbox: str | None = None,
        location: str | None = None,
        radius: float | None = None,
        type: str | list[str] | None = None,  # noqa: A002
        way_shape: str | None = None,
        tags: list[str] | str | None = None,
        or_tags: list[str] | str | None = None,
        not_tags: list[str] | str | None = None,
        limit: int | None = None,
        zoom: float | None = None,
    ) -> dict[str, Any]:
        payload = self._client.query(
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
            centroid=True,
        )
        cid = self._put(payload)
        return self._summarize_query(cid, payload)

    def query_all(
        self,
        *,
        bbox: str | None = None,
        location: str | None = None,
        radius: float | None = None,
        type: str | list[str] | None = None,  # noqa: A002
        way_shape: str | None = None,
        tags: list[str] | str | None = None,
        or_tags: list[str] | str | None = None,
        not_tags: list[str] | str | None = None,
        zoom: float | None = None,
        limit_per_page: int | None = None,
        bbox_tiles: int = 2,
        max_features: int | None = QUERY_ALL_MAX_FEATURES,
    ) -> dict[str, Any]:
        """Drain every page (and bbox tiles). Same as CLI ``--all-pages --bbox-tiles``."""
        cap = _require_positive_max_features(max_features)
        payload = self._client.query_all(
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
            max_features=cap,
            centroid=True,
        )
        cid = self._put(payload)
        return self._summarize_query(cid, payload)

    def _summarize_query(self, collection_id: str, payload: Any) -> dict[str, Any]:
        feats = _feature_seq(payload)
        out = _summary_items(
            collection_id,
            len(feats),
            [_place_item(f) for f in feats[:SUMMARY_ITEM_CAP]],
        )
        meta = getattr(payload, "meta", None)
        if meta is not None:
            out["has_more"] = bool(getattr(meta, "has_more", False))
        return out

    def nearest_within(
        self,
        primary_id: str,
        secondary_id: str,
        max_distance_m: float,
        limit: int | None = None,
    ) -> dict[str, Any]:
        pairs = nearest_within(
            _features_from_payload(self.get(primary_id)),
            _features_from_payload(self.get(secondary_id)),
            max_distance_m=max_distance_m,
            limit=limit,
            max_comparisons=MAX_COMPARISONS,
        )
        cid = self._put({"type": "PairList", "pairs": pairs})
        items = []
        for pair in pairs:
            feat = pair["feature"]
            near = pair["nearest"]
            item: dict[str, Any] = {
                "id": feat.get("id"),
                "name": feature_name(feat),
                "distance_m": pair["distance_m"],
                "nearest_id": near.get("id"),
                "nearest_name": feature_name(near),
            }
            pt = _lon_lat(feat)
            if pt is not None:
                item["lon"], item["lat"] = pt
            npt = _lon_lat(near)
            if npt is not None:
                item["nearest_lon"], item["nearest_lat"] = npt
            items.append(item)
        return _summary_items(cid, len(items), items[:SUMMARY_ITEM_CAP])

    def filter_open(self, collection_id: str) -> dict[str, Any]:
        src = self.get(collection_id)
        if isinstance(src, dict) and isinstance(src.get("items"), list):
            opened_items = []
            for item in src["items"]:
                if not isinstance(item, dict):
                    continue
                feat = item.get("feature")
                if isinstance(feat, dict) and opening_status(feat) == "open":
                    opened_items.append(item)
            payload: dict[str, Any] = {"items": opened_items}
        else:
            opened = [f for f in _features_from_payload(src) if opening_status(f) == "open"]
            payload = {"type": "FeatureCollection", "features": opened}
        hours = _context_fields(src, keys=_HOURS_KEYS)
        if hours:
            payload["metadata"] = hours
        cid = self._put(payload)
        return self._summarize_places(cid, payload)

    def point_in_polygon(self, collection_id: str, lon: float, lat: float) -> dict[str, Any]:
        geoms = _polygon_geoms(self.get(collection_id))
        if not geoms:
            raise ValueError(f"{collection_id} has no Polygon/MultiPolygon geometry")
        inside = any(point_in_geometry(lon, lat, geom) for geom in geoms)
        return {"collection_id": collection_id, "inside": inside}

    def points_in_polygon(self, points_id: str, polygon_id: str) -> dict[str, Any]:
        geoms = _polygon_geoms(self.get(polygon_id))
        if not geoms:
            raise ValueError(f"{polygon_id} has no Polygon/MultiPolygon geometry")
        src = self.get(points_id)

        def _inside(feat: dict[str, Any]) -> bool:
            pt = _lon_lat(feat)
            if pt is None:
                return False
            lon, lat = pt
            return any(point_in_geometry(lon, lat, geom) for geom in geoms)

        if isinstance(src, dict) and isinstance(src.get("items"), list):
            kept_items = []
            for item in src["items"]:
                if not isinstance(item, dict):
                    continue
                feat = item.get("feature")
                if isinstance(feat, dict) and _inside(feat):
                    kept_items.append(item)
            payload: dict[str, Any] = {"items": kept_items}
        else:
            kept = [f for f in _features_from_payload(src) if _inside(f)]
            payload = {"type": "FeatureCollection", "features": kept}
        hours = _context_fields(src, keys=_HOURS_KEYS)
        if hours:
            payload["metadata"] = hours
        cid = self._put(payload)
        return self._summarize_places(cid, payload)

    def export_geojson(self, collection_id: str) -> dict[str, Any]:
        payload = self.get(collection_id)
        if isinstance(payload, dict) and payload.get("type") == "PairList":
            features = []
            for pair in payload.get("pairs") or []:
                feat = dict(pair["feature"])
                props = dict(feat.get("properties") or {})
                props["distance_m"] = pair["distance_m"]
                near = pair.get("nearest") or {}
                props["nearest_id"] = near.get("id")
                feat["properties"] = props
                features.append(feat)
            return {"type": "FeatureCollection", "features": features}
        if isinstance(payload, dict) and isinstance(payload.get("items"), list):
            features = []
            for item in payload["items"]:
                if not isinstance(item, dict) or not isinstance(item.get("feature"), dict):
                    continue
                feat = dict(item["feature"])
                props = dict(feat.get("properties") or {})
                if item.get("distance_m") is not None:
                    props["distance_m"] = item["distance_m"]
                feat["properties"] = props
                features.append(feat)
            return {"type": "FeatureCollection", "features": features}
        geom = _geometry_from_payload(payload)
        if geom is not None and not _features_from_payload(payload):
            props = {
                k: v
                for k, v in (payload.items() if isinstance(payload, dict) else [])
                if k != "geometry"
            }
            return {
                "type": "FeatureCollection",
                "features": [{"type": "Feature", "geometry": geom, "properties": props}],
            }
        if isinstance(payload, dict) and payload.get("type") == "Feature":
            return {"type": "FeatureCollection", "features": [payload]}
        if isinstance(payload, OSMFeatureCollection) or (
            isinstance(payload, dict) and payload.get("type") == "FeatureCollection"
        ):
            return _as_fc_dict(payload)
        feats = _features_from_payload(payload)
        if feats:
            return {"type": "FeatureCollection", "features": feats}
        return _as_fc_dict(payload)

    def export_geojson_file(
        self,
        collection_id: str,
        *,
        directory: str | Path | None = None,
    ) -> dict[str, Any]:
        """Write GeoJSON to disk. Planner result is a path, not geometry."""
        cid = validate_collection_id(collection_id)
        fc = self.export_geojson(cid)
        dest_dir = Path(directory) if directory is not None else Path(tempfile.gettempdir())
        dest_dir.mkdir(parents=True, exist_ok=True)
        path = dest_dir / f"maplark-{cid}.geojson"
        path.write_text(json.dumps(fc), encoding="utf-8")
        return {
            "collection_id": cid,
            "path": str(path.resolve()),
            "feature_count": len(fc.get("features") or []),
        }
