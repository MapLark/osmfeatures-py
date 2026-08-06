"""Tests for to_dataframe() and to_geodataframe()."""

from __future__ import annotations

import pytest

pytest.importorskip("pandas", reason="pandas not installed - skipping output tests")
pytest.importorskip("geopandas", reason="geopandas not installed - skipping output tests")

from osmfeatures import to_dataframe, to_geodataframe
from tests.conftest import make_test_feature


def _features():
    return [
        make_test_feature("way/1", tags={"building": "yes", "name": "City Hall"}),
        make_test_feature("node/2", tags={"amenity": "cafe"}),
    ]


class TestToDataframe:
    def test_returns_dataframe(self):
        import pandas as pd
        df = to_dataframe(_features())
        assert isinstance(df, pd.DataFrame)

    def test_id_column(self):
        df = to_dataframe(_features())
        assert list(df["id"]) == ["way/1", "node/2"]

    def test_tag_columns_prefixed(self):
        df = to_dataframe(_features())
        assert "tag:building" in df.columns
        assert "tag:name" in df.columns
        assert "tag:amenity" in df.columns

    def test_lon_lat_from_centroid(self):
        df = to_dataframe(_features())
        assert df["lon"].notna().all()
        assert df["lat"].notna().all()

    def test_empty_input(self):
        import pandas as pd
        df = to_dataframe([])
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0


class TestToGeoDataframe:
    def test_returns_geodataframe(self):
        import geopandas as gpd
        gdf = to_geodataframe(_features())
        assert isinstance(gdf, gpd.GeoDataFrame)

    def test_crs_is_epsg4326(self):
        gdf = to_geodataframe(_features())
        assert gdf.crs.to_epsg() == 4326

    def test_geometry_column_present(self):
        gdf = to_geodataframe(_features())
        assert "geometry" in gdf.columns
        assert gdf["geometry"].notna().all()

    def test_centroid_column_present(self):
        gdf = to_geodataframe(_features())
        assert "centroid" in gdf.columns

    def test_empty_input(self):
        import geopandas as gpd
        gdf = to_geodataframe([])
        assert isinstance(gdf, gpd.GeoDataFrame)
        assert len(gdf) == 0
        assert gdf.crs is not None
        assert gdf.crs.to_epsg() == 4326
