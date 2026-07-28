"""Typed data models for the osmgeojson SDK."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import geojson as _geojson


# ---------------------------------------------------------------------------
# GeoJSON types - built on the `geojson` package (RFC 7946)
# ---------------------------------------------------------------------------


class OSMFeature(_geojson.Feature):
    """A GeoJSON Feature from the OSM GeoJSON API with OSM-specific helpers.

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
    returned: int
    has_more: bool
    next_cursor: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ResponseMeta":
        return cls(
            returned=d.get("returned", 0),
            has_more=d.get("has_more", False),
            next_cursor=d.get("next_cursor"),
        )


class OSMFeatureCollection(_geojson.FeatureCollection):
    """A GeoJSON FeatureCollection as returned by ``/v2/osm_elements``.

    Subclasses :class:`geojson.FeatureCollection` (a ``dict``). The non-standard
    ``meta`` field carries API pagination info and is accessible as an attribute.
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
    def from_dict(cls, d: dict[str, Any]) -> "OSMFeatureCollection":
        features = [OSMFeature.from_dict(f) for f in d.get("features", [])]
        meta = ResponseMeta.from_dict(d.get("meta", {}))
        return cls(features=features, meta=meta)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "FeatureCollection",
            "features": [dict(f) for f in self["features"]],
            "meta": {
                "returned": self._meta.returned,
                "has_more": self._meta.has_more,
                "next_cursor": self._meta.next_cursor,
            },
        }


# ---------------------------------------------------------------------------
# Cost estimate
# ---------------------------------------------------------------------------


@dataclass
class CostEstimate:
    estimated_credits: int
    tier_limits: dict[str, Any]
    hints: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CostEstimate":
        return cls(
            estimated_credits=d["estimated_credits"],
            tier_limits=d.get("tier_limits", {}),
            hints=d.get("hints", []),
        )


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class OSMGeoJSONError(Exception):
    """Base exception for all osmgeojson SDK errors."""


class OSMGeoJSONAuthError(OSMGeoJSONError):
    """Raised on HTTP 401 - missing or invalid API key."""


class OSMGeoJSONForbiddenError(OSMGeoJSONError):
    """Raised on HTTP 403 - unknown tier or access denied."""


class OSMGeoJSONRateLimitError(OSMGeoJSONError):
    """Raised on HTTP 429 - per-second or monthly budget exceeded, or query too expensive."""

    def __init__(
        self,
        message: str,
        error_code: str = "",
        tier: str = "",
        estimated_units: int | None = None,
        max_units_per_request: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.tier = tier
        self.estimated_units = estimated_units
        self.max_units_per_request = max_units_per_request
        self.retry_after = retry_after


class OSMGeoJSONAPIError(OSMGeoJSONError):
    """Raised on other non-2xx HTTP responses."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code
