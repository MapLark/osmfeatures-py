"""Shared HTTP helpers used by both the sync and async clients."""

from __future__ import annotations

import logging
import warnings
from typing import Any, Literal

from .models import (
    OSMFeaturesAPIError,
    OSMFeaturesAuthError,
    OSMFeaturesForbiddenError,
    OSMFeaturesRateLimitError,
)

DEFAULT_BASE_URL = "https://api.maplark.com"
ACCOUNT_TIER_PATH = "/v1/account/tier"
GEOJSON_ACCEPT = "application/geo+json"
# SDK ceiling for HTTP reads. The API enforces statement timeout; this only
# prevents indefinite hangs (610s > 600s statement timeout so JSON can arrive).
DEFAULT_TIMEOUT = 610.0
_LOG_BODY_MAX = 4000
logger = logging.getLogger("osmfeatures.http")

ElementType = Literal["node", "way", "relation"]
ShapeType = Literal["line", "polygon", "all"]


def is_geojson_accept(accept: str | None) -> bool:
    """True when Accept is omitted or asks for GeoJSON (default encoding)."""
    if not accept or not accept.strip():
        return True
    media = accept.split(",", 1)[0].split(";", 1)[0].strip().lower()
    return media in ("*/*", "*", GEOJSON_ACCEPT)


def fold_way_shape(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Map deprecated ``shape`` onto ``way_shape``. Returns a shallow copy."""
    out = dict(kwargs)
    shape = out.pop("shape", None)
    way_shape = out.get("way_shape")
    if way_shape is not None and shape is not None and way_shape != shape:
        raise ValueError("way_shape and deprecated shape disagree. Pass only way_shape.")
    if shape is not None:
        warnings.warn(
            "shape is deprecated; use way_shape",
            DeprecationWarning,
            stacklevel=3,
        )
        if way_shape is None:
            out["way_shape"] = shape
    return out


def response_headers_dict(headers: Any) -> dict[str, str]:
    """Plain copy of upstream response headers for MCP / SDK forwarding."""
    items = headers.items() if hasattr(headers, "items") else headers
    return {str(k): str(v) for k, v in items}


_USAGE_UNITS_HEADER = "X-Usage-Units-Charged"


def merge_response_headers(blobs: list[dict[str, str]]) -> dict[str, str] | None:
    """Merge tiled ``response_headers``. Sum billed units; keep first tile otherwise.

    Returns ``None`` when no tile carried headers.
    """
    if not blobs:
        return None
    out = dict(blobs[0])
    total = 0
    seen = False
    for blob in blobs:
        for key, value in blob.items():
            if key.lower() != "x-usage-units-charged":
                continue
            try:
                total += int(value)
            except (TypeError, ValueError):
                continue
            seen = True
    if seen:
        for key in list(out):
            if key.lower() == "x-usage-units-charged":
                del out[key]
        out[_USAGE_UNITS_HEADER] = str(total)
    return out


def normalize_tags(tags: Any) -> list[str] | None:
    """Non-empty tag filter strings, in order. A bare string is one item."""
    if tags is None:
        return None
    items = [tags] if isinstance(tags, str) else tags
    return [s for t in items if (s := str(t).strip())] or None


def join_tags(tags: Any) -> str | None:
    """One ``tags`` query value. A list is joined with ``,`` (AND); ``|`` inside an item is OR.

    Items join as-is: the server splits on top-level ``,`` first, so ``a|(b,c)``
    and ``a|b`` keep their meaning when another item is appended after them.
    """
    items = normalize_tags(tags)
    return ",".join(items) if items else None


def build_params(kwargs: dict[str, Any]) -> list[tuple[str, Any]]:
    """Convert SDK kwargs to a list of (key, value) pairs for requests.

    ``tags`` is always one query-string value (see :func:`join_tags`). The
    ``type`` parameter is encoded as a single comma-separated value because
    the backend expects ``type=node,way`` instead of repeated keys.
    """
    kwargs = fold_way_shape(kwargs)
    params: list[tuple[str, Any]] = []
    for key, value in kwargs.items():
        wire_key = "clipGeometry" if key == "clip_geometry" else key
        if key == "tags":
            joined = join_tags(value)
            if joined:
                params.append((wire_key, joined))
        elif key == "type" and isinstance(value, (list, tuple)):
            params.append((wire_key, ",".join(str(v) for v in value)))
        elif isinstance(value, bool):
            params.append((wire_key, "true" if value else "false"))
        else:
            params.append((wire_key, value))
    return params


def _clip(text: str, limit: int = _LOG_BODY_MAX) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def _response_text(resp: Any, limit: int = 300) -> str:
    """Body snippet for exception messages. Empty bodies still need a phrase for MCP."""
    text = (getattr(resp, "text", None) or "").strip()
    return text[:limit] if text else "empty response body"


def _log_failed_response(resp: Any) -> None:
    req = getattr(resp, "request", None)
    raw = b""
    if req is not None:
        raw = getattr(req, "content", None)
        if raw is None:
            raw = getattr(req, "body", b"") or b""
    if isinstance(raw, str):
        body = raw
    elif raw:
        body = bytes(raw).decode("utf-8", errors="replace")
    else:
        body = ""
    logger.warning(
        "API error HTTP %s %s %s body=%s response=%s",
        resp.status_code,
        getattr(req, "method", "?") if req is not None else "?",
        getattr(req, "url", "") if req is not None else "",
        _clip(body),
        _clip(getattr(resp, "text", "") or "", 500),
    )


def raise_for_response(resp: Any) -> None:
    status = resp.status_code
    if not (200 <= status < 300):
        _log_failed_response(resp)
    if status == 401:
        raise OSMFeaturesAuthError(
            f"Authentication failed (HTTP 401): {_response_text(resp)}"
        )
    if status == 403:
        raise OSMFeaturesForbiddenError(
            f"Access denied (HTTP 403): {_response_text(resp)}"
        )
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
            body.get(
                "detail",
                body.get("message", f"Rate limit exceeded (HTTP 429): {_response_text(resp)}"),
            ),
            error_code=body.get("error", ""),
            tier=body.get("tier", ""),
            units=body.get("units"),
            max_units_per_request=body.get("max_units_per_request"),
            retry_after=retry_after,
        )
    if not (200 <= status < 300):
        raise OSMFeaturesAPIError(
            f"API error (HTTP {status}): {_response_text(resp)}",
            status_code=status,
        )


def is_429_retryable(resp: Any) -> bool:
    """Return True: every 429 is a per-second limit and safe to retry.

    The monthly quota is HTTP 403 ``quota_exceeded`` (``OSMFeaturesForbiddenError``),
    never a 429, so there is no monthly 429 to exclude here.
    """
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
        units=body.get("units"),
        max_units_per_request=body.get("max_units_per_request"),
        retry_after=retry_after,
    )
