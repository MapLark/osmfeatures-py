"""Typed data models for the osmfeatures SDK."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import geojson as _geojson


# ---------------------------------------------------------------------------
# GeoJSON types - built on the `geojson` package (RFC 7946)
# ---------------------------------------------------------------------------


class OSMFeature(_geojson.Feature):
    """A GeoJSON Feature from the OSM Features API with OSM-specific helpers.

    Subclasses :class:`geojson.Feature` (a ``dict``), so it serializes directly
    with ``json.dumps`` and is accepted anywhere a GeoJSON dict is expected.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self["type"] = "Feature"

    @property
    def osm_type(self) -> str:
        """The OSM element type prefix: ``node``, ``way``, or ``relation``."""
        return self["id"].split("/")[0]

    @property
    def osm_id(self) -> int:
        """The numeric OSM element id."""
        return int(self["id"].split("/")[1])

    @property
    def tags(self) -> dict[str, str]:
        """Shortcut to ``properties["tags"]``."""
        return self.get("properties", {}).get("tags", {})

    @property
    def centroid(self) -> dict[str, Any] | None:
        """GeoJSON Point centroid from properties when requested via ``centroid=true``."""
        return self.get("properties", {}).get("centroid")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "OSMFeature":
        return cls(
            id=d["id"],
            geometry=d["geometry"],
            properties=d.get("properties", {}),
        )


@dataclass
class ResponseMeta:
    """Counts from ``X-Returned`` / ``X-Has-More``. ``has_more`` means a tile was truncated."""

    returned: int
    has_more: bool
    next_cursor: str | None = None

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> "ResponseMeta":
        # requests / httpx headers are case-insensitive; Mapping.get is fine.
        returned_raw = headers.get("X-Returned") or headers.get("x-returned") or "0"
        has_more_raw = (
            headers.get("X-Has-More") or headers.get("x-has-more") or "false"
        ).strip().lower()
        next_cursor = headers.get("X-Next-Cursor") or headers.get("x-next-cursor") or None
        if next_cursor == "":
            next_cursor = None
        try:
            returned = int(returned_raw)
        except (TypeError, ValueError):
            returned = 0
        return cls(
            returned=returned,
            has_more=has_more_raw == "true",
            next_cursor=next_cursor,
        )


@dataclass(frozen=True)
class BinaryQueryResult:
    """Non-GeoJSON ``query`` tile: raw body plus response headers.

    Returned when ``accept`` is ``text/csv``, ``text/tab-separated-values``,
    ``application/flatgeobuf``, or ``application/vnd.apache.parquet``.
    """

    content: bytes
    meta: ResponseMeta

    @classmethod
    def from_http(cls, content: bytes, headers: Mapping[str, str]) -> "BinaryQueryResult":
        return cls(content=content, meta=ResponseMeta.from_headers(headers))


class OSMFeatureCollection(_geojson.FeatureCollection):
    """A GeoJSON FeatureCollection as returned by ``/v3/osm_features``.

    Subclasses :class:`geojson.FeatureCollection` (a ``dict``). ``meta`` is
    from response headers after the HTTP call.
    """

    def __init__(
        self,
        features: list[OSMFeature] | None = None,
        meta: "ResponseMeta | None" = None,
        **extra: Any,
    ) -> None:
        super().__init__(features=features or [], **extra)
        self._meta = meta if meta is not None else ResponseMeta(returned=0, has_more=False)

    @property
    def meta(self) -> ResponseMeta:
        return self._meta

    @classmethod
    def from_http(cls, body: dict[str, Any], headers: Mapping[str, str]) -> "OSMFeatureCollection":
        features = [OSMFeature.from_dict(f) for f in body.get("features", [])]
        return cls(features=features, meta=ResponseMeta.from_headers(headers))

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "FeatureCollection",
            "features": [dict(f) for f in self["features"]],
        }


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class OSMFeaturesError(Exception):
    """Base exception for all osmfeatures SDK errors."""


class OSMFeaturesAuthError(OSMFeaturesError):
    """Raised on HTTP 401 - missing or invalid API key."""


class OSMFeaturesForbiddenError(OSMFeaturesError):
    """Raised on HTTP 403 - unknown tier or access denied."""


class OSMFeaturesRateLimitError(OSMFeaturesError):
    """Raised on HTTP 429 - per-second or monthly budget exceeded, or query too expensive."""

    def __init__(
        self,
        message: str,
        error_code: str = "",
        tier: str = "",
        units: int | None = None,
        max_units_per_request: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.tier = tier
        self.units = units
        self.max_units_per_request = max_units_per_request
        self.retry_after = retry_after


class OSMFeaturesAPIError(OSMFeaturesError):
    """Raised on other non-2xx HTTP responses."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class OSMFeaturesTimeoutError(OSMFeaturesError):
    """Raised when a tiled query hits its wall-clock deadline mid-walk."""

    def __init__(self, message: str, *, timeout: float | None = None) -> None:
        super().__init__(message)
        self.timeout = timeout


class OSMFeaturesTooManyTilesError(OSMFeaturesError):
    """Raised when ``auto_split`` would need more than ``max_tiles`` to cover the bbox.

    This is an area problem (the window is too large), not a timeout and not
    a dense match set. Agents should geocode a smaller named place or ask
    the user; they must not bisect leftover rectangles.
    """

    def __init__(
        self,
        message: str,
        *,
        tiles: int,
        max_tiles: int,
        total: int | None = None,
    ) -> None:
        super().__init__(message)
        self.tiles = tiles
        self.max_tiles = max_tiles
        self.total = total


class OSMFeaturesTooDenseError(OSMFeaturesError):
    """Raised when a tile still overflows after ``auto_split`` hits ``max_tiles``.

    Count-first splitting was not enough. Agents should ask the user to
    shrink the area or add filters; if they do not answer, add tighter tags.
    """

    def __init__(
        self,
        message: str,
        *,
        max_tiles: int,
        tiles: int | None = None,
        total: int | None = None,
    ) -> None:
        super().__init__(message)
        self.max_tiles = max_tiles
        self.tiles = tiles
        self.total = total
