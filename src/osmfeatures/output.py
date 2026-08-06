"""Output converters: features -> pandas DataFrame / geopandas GeoDataFrame."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pandas as pd
    import geopandas as gpd


_GEO_INSTALL_HINT = (
    "pandas, geopandas, and shapely are required for DataFrame output. "
    "Install them with: pip install osmfeatures[geo]"
)


def to_dataframe(features: list[Any]) -> "pd.DataFrame":
    """Convert a list of :class:`~osmfeatures.Feature` objects (or raw feature dicts)
    to a flat ``pandas.DataFrame``.

    Each tag in ``properties.tags`` becomes its own column (prefixed with
    ``tag:``).  Point geometries (and centroids on polygon/line features) are
    expanded to ``lon`` / ``lat`` columns.

    Requires ``pip install osmfeatures[geo]``.
    """
    try:
        import pandas as pd
    except ImportError as exc:
        raise ImportError(_GEO_INSTALL_HINT) from exc

    rows: list[dict[str, Any]] = []

    for feat in features:
        # Support both Feature dataclass and raw dict
        if hasattr(feat, "id"):
            fid = feat.id
            geom = feat.geometry
            props = feat.properties
        else:
            fid = feat.get("id", "")
            geom = feat.get("geometry", {})
            props = feat.get("properties", {})

        row: dict[str, Any] = {"id": fid}

        # Coordinates
        geom_type = geom.get("type", "")
        coords = geom.get("coordinates")
        if geom_type == "Point" and coords:
            row["lon"] = coords[0]
            row["lat"] = coords[1]
        else:
            centroid = props.get("centroid")
            if centroid and centroid.get("coordinates"):
                row["lon"] = centroid["coordinates"][0]
                row["lat"] = centroid["coordinates"][1]
            else:
                row["lon"] = None
                row["lat"] = None

        # Flatten tags
        for k, v in props.get("tags", {}).items():
            row[f"tag:{k}"] = v

        rows.append(row)

    return pd.DataFrame(rows)


def to_geodataframe(features: list[Any]) -> "gpd.GeoDataFrame":
    """Convert a list of :class:`~osmfeatures.Feature` objects (or raw feature dicts)
    to a ``geopandas.GeoDataFrame`` with EPSG:4326 CRS.

    Non-point features include a ``centroid`` column (Shapely Point) in
    addition to the main ``geometry`` column.

    Requires ``pip install osmfeatures[geo]``.
    """
    try:
        import geopandas as gpd
        from shapely.geometry import shape
    except ImportError as exc:
        raise ImportError(_GEO_INSTALL_HINT) from exc

    rows: list[dict[str, Any]] = []

    for feat in features:
        if hasattr(feat, "id"):
            fid = feat.id
            geom_dict = feat.geometry
            props = feat.properties
        else:
            fid = feat.get("id", "")
            geom_dict = feat.get("geometry", {})
            props = feat.get("properties", {})

        row: dict[str, Any] = {"id": fid}

        try:
            row["geometry"] = shape(geom_dict)
        except Exception:
            row["geometry"] = None

        # Centroid column for non-point features
        centroid_dict = props.get("centroid")
        if centroid_dict:
            try:
                row["centroid"] = shape(centroid_dict)
            except Exception:
                row["centroid"] = None
        else:
            row["centroid"] = None

        for k, v in props.get("tags", {}).items():
            row[f"tag:{k}"] = v

        rows.append(row)

    if not rows:
        return gpd.GeoDataFrame(columns=["id", "geometry", "centroid"], geometry="geometry", crs="EPSG:4326")
    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    return gdf
