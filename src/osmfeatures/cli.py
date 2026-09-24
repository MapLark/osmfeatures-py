"""CLI for osmfeatures - ``osmfeatures query``, ``osmfeatures stats``, and ``osmfeatures mcp``."""

from __future__ import annotations

import json
import os
import sys
from typing import Any

import click

from .client import OSMFeaturesClient
from ._http import DEFAULT_BASE_URL
from .models import (
    BinaryQueryResult,
    OSMFeaturesAuthError,
    OSMFeaturesRateLimitError,
    OSMFeaturesAPIError,
    OSMFeaturesTimeoutError,
)
from .retry import RetryConfig


def _make_client(api_key: str | None, base_url: str | None, retries: int) -> OSMFeaturesClient:
    resolved_key = api_key or os.environ.get("MAPLARK_API_KEY", "")
    if not resolved_key:
        raise click.UsageError(
            "No API key provided. Pass --api-key or set MAPLARK_API_KEY environment variable."
        )
    resolved_url = base_url or os.environ.get("MAPLARK_BASE_URL", DEFAULT_BASE_URL)
    return OSMFeaturesClient(
        api_key=resolved_key,
        base_url=resolved_url,
        retry_config=RetryConfig(max_retries=retries),
    )


def _apply_output(features: list[Any], output_format: str, fc_dict: dict[str, Any]) -> None:
    if output_format == "geojson":
        click.echo(json.dumps(fc_dict, indent=2))
    elif output_format == "csv":
        try:
            from .output import to_dataframe
            df = to_dataframe(features)
            click.echo(df.to_csv(index=False))
        except ImportError as exc:
            raise click.UsageError(str(exc)) from exc
    elif output_format == "table":
        try:
            from .output import to_dataframe
            df = to_dataframe(features)
            click.echo(df.to_string(index=False))
        except ImportError as exc:
            raise click.UsageError(str(exc)) from exc
    else:
        raise click.UsageError(f"Unknown output format: {output_format!r}")


# ---------------------------------------------------------------------------
# Common options shared between query and stats subcommands
# ---------------------------------------------------------------------------

_SPATIAL_OPTIONS = [
    click.option("--bbox", default=None, help="Bounding box: min_lon,min_lat,max_lon,max_lat"),
    click.option("--location", default=None, help="Radius search point: lat,lng (requires --radius)"),
    click.option("--radius", default=None, type=float, help="Search radius in metres (requires --location)"),
    click.option("--osm-ids", "osm_ids", default=None, help="Comma-separated OSM IDs (direct lookup)"),
    click.option("--within", default=None, help="Polygon spatial anchor: way/<id> or relation/<id>"),
    click.option("--tags", multiple=True, help="Tag filter key=value, key, or key>n (AND, repeatable)"),
    click.option("--or-tags", "or_tags", multiple=True, help="Tag filter OR group (repeatable)"),
    click.option("--not-tags", "not_tags", multiple=True, help="Exclusion tag filter (repeatable)"),
    click.option("--type", "element_type", default=None, help="Comma-separated element types: node,way,relation"),
    click.option("--way-shape", "way_shape", default=None, type=click.Choice(["line", "polygon", "all"]), help="Geometry class for ways/relations"),
    click.option("--shape", default=None, type=click.Choice(["line", "polygon", "all"]), help="Deprecated alias for --way-shape"),
    click.option("--zoom", default=None, type=float, help="Map zoom level for geometry simplification"),
    click.option("--min-length-m", "min_length_m", default=None, type=float, help="Minimum line length in metres"),
    click.option("--max-length-m", "max_length_m", default=None, type=float, help="Maximum line length in metres"),
    click.option("--min-area-m2", "min_area_m2", default=None, type=float, help="Minimum polygon area in square metres"),
    click.option("--max-area-m2", "max_area_m2", default=None, type=float, help="Maximum polygon area in square metres"),
]


def _add_options(options: list[Any]) -> Any:
    def decorator(fn: Any) -> Any:
        for opt in reversed(options):
            fn = opt(fn)
        return fn
    return decorator


@click.group()
def cli() -> None:
    """osmfeatures - MapLark OSM Features API SDK CLI."""


@cli.command("query")
@_add_options(_SPATIAL_OPTIONS)
@click.option("--limit", default=None, type=int, help="Maximum features to return (omit for API default, max 1000000)")
@click.option(
    "--output",
    "output_format",
    default="geojson",
    type=click.Choice(["geojson", "csv", "table"]),
    show_default=True,
    help="Client-side display format when the response is GeoJSON",
)
@click.option(
    "--accept",
    "accept",
    default=None,
    type=click.Choice([
        "application/geo+json",
        "text/csv",
        "text/tab-separated-values",
        "application/flatgeobuf",
        "application/vnd.apache.parquet",
    ]),
    help="Accept media type. Non-GeoJSON writes raw bytes to stdout",
)
@click.option("--api-key", default=None, envvar="MAPLARK_API_KEY", help="MapLark API key")
@click.option("--base-url", default=None, envvar="MAPLARK_BASE_URL", help="API base URL")
@click.option("--retries", default=3, show_default=True, type=int, help="Max retry attempts")
def query_cmd(
    bbox: str | None,
    location: str | None,
    radius: float | None,
    osm_ids: str | None,
    within: str | None,
    tags: tuple[str, ...],
    or_tags: tuple[str, ...],
    not_tags: tuple[str, ...],
    element_type: str | None,
    way_shape: str | None,
    shape: str | None,
    zoom: float | None,
    min_length_m: float | None,
    max_length_m: float | None,
    min_area_m2: float | None,
    max_area_m2: float | None,
    limit: int | None,
    output_format: str,
    accept: str | None,
    api_key: str | None,
    base_url: str | None,
    retries: int,
) -> None:
    """Query OSM features from the MapLark OSM Features API."""
    try:
        client = _make_client(api_key, base_url, retries)
    except click.UsageError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    params: dict[str, Any] = {}
    if bbox:
        params["bbox"] = bbox
    if location:
        params["location"] = location
    if radius is not None:
        params["radius"] = radius
    if osm_ids:
        params["osm_ids"] = osm_ids
    if within:
        params["within"] = within
    if tags:
        params["tags"] = list(tags)
    if or_tags:
        params["or_tags"] = list(or_tags)
    if not_tags:
        params["not_tags"] = list(not_tags)
    if element_type:
        params["type"] = [t.strip() for t in element_type.split(",")]
    resolved_shape = way_shape or shape
    if resolved_shape:
        params["way_shape"] = resolved_shape
    if zoom is not None:
        params["zoom"] = zoom
    if min_length_m is not None:
        params["min_length_m"] = min_length_m
    if max_length_m is not None:
        params["max_length_m"] = max_length_m
    if min_area_m2 is not None:
        params["min_area_m2"] = min_area_m2
    if max_area_m2 is not None:
        params["max_area_m2"] = max_area_m2
    if limit is not None:
        params["limit"] = limit
    if accept:
        params["accept"] = accept

    try:
        with client:
            fc = client.query(**params)
    except OSMFeaturesAuthError as exc:
        click.echo(f"Authentication error: {exc}", err=True)
        sys.exit(1)
    except OSMFeaturesRateLimitError as exc:
        click.echo(f"Rate limit: {exc}", err=True)
        sys.exit(1)
    except OSMFeaturesAPIError as exc:
        click.echo(f"API error (HTTP {exc.status_code}): {exc}", err=True)
        sys.exit(1)
    except OSMFeaturesTimeoutError as exc:
        click.echo(f"Timeout: {exc}", err=True)
        sys.exit(1)
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    if isinstance(fc, BinaryQueryResult):
        sys.stdout.buffer.write(fc.content)
        return

    _apply_output(fc.features, output_format, fc.to_dict())


@cli.command("stats")
@_add_options(_SPATIAL_OPTIONS)
@click.option("--group-by", "group_by", required=True, help="Tag key to group on (e.g. amenity)")
@click.option("--limit", default=None, type=int, help="Max histogram groups")
@click.option(
    "--disable-budget-warning",
    "disable_budget_warning",
    is_flag=True,
    default=False,
    help="Bypass per-request unit cap (maps to disable_budget_warning=true)",
)
@click.option("--api-key", default=None, envvar="MAPLARK_API_KEY", help="MapLark API key")
@click.option("--base-url", default=None, envvar="MAPLARK_BASE_URL", help="API base URL")
@click.option("--retries", default=3, show_default=True, type=int, help="Max retry attempts")
def stats_cmd(
    bbox: str | None,
    location: str | None,
    radius: float | None,
    osm_ids: str | None,
    within: str | None,
    tags: tuple[str, ...],
    or_tags: tuple[str, ...],
    not_tags: tuple[str, ...],
    element_type: str | None,
    way_shape: str | None,
    shape: str | None,
    zoom: float | None,
    min_length_m: float | None,
    max_length_m: float | None,
    min_area_m2: float | None,
    max_area_m2: float | None,
    group_by: str,
    limit: int | None,
    disable_budget_warning: bool,
    api_key: str | None,
    base_url: str | None,
    retries: int,
) -> None:
    """Count OSM features grouped by a tag key (GET /v2/osm_features/count)."""
    if osm_ids:
        raise click.UsageError("stats does not take --osm-ids")
    if zoom is not None:
        raise click.UsageError("stats does not take --zoom")
    try:
        client = _make_client(api_key, base_url, retries)
    except click.UsageError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    params: dict[str, Any] = {"group_by": group_by}
    if bbox:
        params["bbox"] = bbox
    if location:
        params["location"] = location
    if radius is not None:
        params["radius"] = radius
    if within:
        params["within"] = within
    if tags:
        params["tags"] = list(tags)
    if or_tags:
        params["or_tags"] = list(or_tags)
    if not_tags:
        params["not_tags"] = list(not_tags)
    if element_type:
        params["type"] = [t.strip() for t in element_type.split(",")]
    resolved_shape = way_shape or shape
    if resolved_shape:
        params["way_shape"] = resolved_shape
    if min_length_m is not None:
        params["min_length_m"] = min_length_m
    if max_length_m is not None:
        params["max_length_m"] = max_length_m
    if min_area_m2 is not None:
        params["min_area_m2"] = min_area_m2
    if max_area_m2 is not None:
        params["max_area_m2"] = max_area_m2
    if limit is not None:
        params["limit"] = limit
    if disable_budget_warning:
        params["disable_budget_warning"] = True

    try:
        with client:
            out = client.count(**params)
    except OSMFeaturesAuthError as exc:
        click.echo(f"Authentication error: {exc}", err=True)
        sys.exit(1)
    except OSMFeaturesRateLimitError as exc:
        click.echo(f"Rate limit: {exc}", err=True)
        sys.exit(1)
    except OSMFeaturesAPIError as exc:
        click.echo(f"API error (HTTP {exc.status_code}): {exc}", err=True)
        sys.exit(1)
    except ValueError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)

    click.echo(json.dumps(out, indent=2))


@cli.command("mcp")
@click.option("--api-key", default=None, envvar="MAPLARK_API_KEY", help="MapLark API key")
@click.option("--base-url", default=None, envvar="MAPLARK_BASE_URL", help="API base URL")
def mcp_cmd(api_key: str | None, base_url: str | None) -> None:
    """Run the MapLark geo-agent MCP server on stdio (requires osmfeatures[mcp])."""
    resolved_key = api_key or os.environ.get("MAPLARK_API_KEY", "")
    if not resolved_key:
        click.echo(
            "Error: No API key provided. Pass --api-key or set MAPLARK_API_KEY.",
            err=True,
        )
        sys.exit(1)
    try:
        from .mcp.mcp_server import run_stdio
    except ModuleNotFoundError as exc:
        if exc.name != "mcp" and not (exc.name or "").startswith("mcp."):
            raise
        click.echo(
            "Error: MCP extra not installed. pip install 'osmfeatures[mcp]'",
            err=True,
        )
        sys.exit(1)
    except FileNotFoundError as exc:
        click.echo(f"Error: {exc}", err=True)
        sys.exit(1)
    run_stdio(api_key=resolved_key, base_url=base_url)
