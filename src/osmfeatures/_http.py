"""Shared HTTP helpers used by both the sync and async clients."""

from __future__ import annotations

from typing import Any, Literal

from .models import (
    OSMFeaturesAPIError,
    OSMFeaturesAuthError,
    OSMFeaturesForbiddenError,
    OSMFeaturesRateLimitError,
)

DEFAULT_BASE_URL = "https://api.maplark.com"
GEOJSON_ACCEPT = "application/geo+json"

ElementType = Literal["node", "way", "relation"]
ShapeType = Literal["line", "polygon", "all"]


def is_geojson_accept(accept: str | None) -> bool:
    """True when Accept is omitted or asks for GeoJSON (default encoding)."""
    if not accept or not accept.strip():
        return True
    media = accept.split(",", 1)[0].split(";", 1)[0].strip().lower()
    return media in ("*/*", "*", GEOJSON_ACCEPT)


def build_params(kwargs: dict[str, Any]) -> list[tuple[str, Any]]:
    """Convert SDK kwargs to a list of (key, value) pairs for requests.

    Handles repeatable parameters (tags, or_tags, not_tags) - if a
    list/tuple is provided it becomes multiple query-string values.
    The ``type`` parameter is encoded as a single comma-separated value
    because the backend expects ``type=node,way`` instead of repeated keys.
    """
    repeatable = {"tags", "or_tags", "not_tags"}
    params: list[tuple[str, Any]] = []
    for key, value in kwargs.items():
        wire_key = "clipGeometry" if key == "clip_geometry" else key
        if key == "type" and isinstance(value, (list, tuple)):
            params.append((wire_key, ",".join(str(v) for v in value)))
        elif key in repeatable and isinstance(value, (list, tuple)):
            for v in value:
                params.append((wire_key, v))
        elif isinstance(value, bool):
            params.append((wire_key, "true" if value else "false"))
        else:
            params.append((wire_key, value))
    return params


def raise_for_response(resp: Any) -> None:
    status = resp.status_code
    if status == 401:
        raise OSMFeaturesAuthError(f"Authentication failed (HTTP 401): {resp.text[:200]}")
    if status == 403:
        raise OSMFeaturesForbiddenError(f"Access denied (HTTP 403): {resp.text[:200]}")
    if status == 429:
        body: dict[str, Any] = {}
        try:
            body = resp.json()
        except Exception:
            pass
        retry_after_raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        retry_after: float | None = None
        if retry_after_raw is not None:
            try:
                retry_after = float(retry_after_raw)
            except (TypeError, ValueError):
                pass
        raise OSMFeaturesRateLimitError(
            body.get("detail", body.get("message", f"Rate limit exceeded (HTTP 429): {resp.text[:200]}")),
            error_code=body.get("error", ""),
            tier=body.get("tier", ""),
            estimated_units=body.get("estimated_units"),
            max_units_per_request=body.get("max_units_per_request"),
            retry_after=retry_after,
        )
    if not (200 <= status < 300):
        raise OSMFeaturesAPIError(
            f"API error (HTTP {status}): {resp.text[:200]}",
            status_code=status,
        )


def is_429_retryable(resp: Any) -> bool:
    """Return True if the 429 should be retried (transient per-second limit).

    The backend emits ``error == "too_many_requests"`` for all 429s and
    distinguishes the two cases via a ``subtype`` field:
    - ``"rate_limit_second"`` — transient, safe to retry with back-off.
    - ``"rate_limit_monthly"`` — hard monthly cap, do not retry.

    If ``subtype`` is absent (e.g. unknown proxy / future backend version),
    fall back to retrying so the SDK does not silently swallow genuine
    transient limits.
    """
    try:
        body = resp.json()
        subtype = body.get("subtype", "")
        if subtype == "rate_limit_monthly":
            return False
        if subtype == "rate_limit_second":
            return True
        # Legacy codes emitted by older backend versions — kept for
        # compatibility during a rolling deploy.
        error = body.get("error", "")
        if error == "rate_limit_monthly":
            return False
        # Default: retry unknown 429s (fail open for transient limits).
        return True
    except Exception:
        return True


def build_rate_limit_error(resp: Any) -> OSMFeaturesRateLimitError:
    body: dict[str, Any] = {}
    try:
        body = resp.json()
    except Exception:
        pass
    retry_after_raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
    retry_after: float | None = None
    if retry_after_raw is not None:
        try:
            retry_after = float(retry_after_raw)
        except (TypeError, ValueError):
            pass
    return OSMFeaturesRateLimitError(
        body.get("detail", body.get("message", "Rate limit exceeded")),
        error_code=body.get("error", ""),
        tier=body.get("tier", ""),
        estimated_units=body.get("estimated_units"),
        max_units_per_request=body.get("max_units_per_request"),
        retry_after=retry_after,
    )
