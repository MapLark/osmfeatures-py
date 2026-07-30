"""Tests for the osmgeojson CLI - query output formats and basic error paths."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from osmgeojson.cli import cli
from osmgeojson import OSMFeatureCollection
from tests.conftest import make_test_feature, make_feature_collection


def make_fc(
    feature_dicts: list[dict] | None = None,
    has_more: bool = False,
    next_cursor: str | None = None,
) -> OSMFeatureCollection:
    feature_dicts = feature_dicts or [make_test_feature("way/1")]
    return OSMFeatureCollection.from_dict(
        make_feature_collection(feature_dicts, has_more=has_more, next_cursor=next_cursor)
    )


# ---------------------------------------------------------------------------
# --output geojson (default)
# ---------------------------------------------------------------------------

def test_query_output_geojson():
    fc = make_fc()
    runner = CliRunner()
    with patch("osmgeojson.cli.OSMGeoJSONClient") as MockClient:
        MockClient.return_value.query.return_value = fc
        result = runner.invoke(cli, [
            "query",
            "--api-key", "sk-test",
            "--bbox", "18.06,59.32,18.09,59.34",
            "--output", "geojson",
        ])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["type"] == "FeatureCollection"
    assert len(parsed["features"]) == 1


# ---------------------------------------------------------------------------
# --output csv
# ---------------------------------------------------------------------------

pandas = pytest.importorskip("pandas", reason="pandas not installed - skipping csv/table CLI tests")


def test_query_output_csv():
    fc = make_fc()
    runner = CliRunner()
    with patch("osmgeojson.cli.OSMGeoJSONClient") as MockClient:
        MockClient.return_value.query.return_value = fc
        result = runner.invoke(cli, [
            "query",
            "--api-key", "sk-test",
            "--bbox", "18.06,59.32,18.09,59.34",
            "--output", "csv",
        ])
    assert result.exit_code == 0, result.output
    lines = result.output.strip().splitlines()
    # First line is the CSV header
    assert "id" in lines[0]
    assert len(lines) >= 2  # header + at least one data row


def test_query_output_table():
    fc = make_fc()
    runner = CliRunner()
    with patch("osmgeojson.cli.OSMGeoJSONClient") as MockClient:
        MockClient.return_value.query.return_value = fc
        result = runner.invoke(cli, [
            "query",
            "--api-key", "sk-test",
            "--bbox", "18.06,59.32,18.09,59.34",
            "--output", "table",
        ])
    assert result.exit_code == 0, result.output
    assert "id" in result.output


# ---------------------------------------------------------------------------
# Missing API key
# ---------------------------------------------------------------------------

def test_query_missing_api_key(monkeypatch):
    monkeypatch.delenv("MAPLARK_API_KEY", raising=False)
    runner = CliRunner()
    result = runner.invoke(cli, ["query", "--bbox", "18.06,59.32,18.09,59.34"])
    assert result.exit_code != 0
    assert "API key" in result.output


def test_query_limit_is_forwarded_to_single_page_query():
    fc = make_fc()
    runner = CliRunner()
    with patch("osmgeojson.cli.OSMGeoJSONClient") as MockClient:
        MockClient.return_value.query.return_value = fc
        result = runner.invoke(cli, [
            "query",
            "--api-key", "sk-test",
            "--bbox", "18.06,59.32,18.09,59.34",
            "--limit", "25",
        ])

    assert result.exit_code == 0, result.output
    MockClient.return_value.query.assert_called_once_with(
        bbox="18.06,59.32,18.09,59.34",
        limit=25,
    )


def test_query_forwards_zoom_length_and_area_filters():
    fc = make_fc()
    runner = CliRunner()
    with patch("osmgeojson.cli.OSMGeoJSONClient") as MockClient:
        MockClient.return_value.query.return_value = fc
        result = runner.invoke(cli, [
            "query",
            "--api-key", "sk-test",
            "--bbox", "18.06,59.32,18.09,59.34",
            "--type", "way",
            "--shape", "polygon",
            "--zoom", "10",
            "--min-length-m", "100",
            "--max-length-m", "1000",
            "--min-area-m2", "250",
            "--max-area-m2", "2500",
        ])

    assert result.exit_code == 0, result.output
    MockClient.return_value.query.assert_called_once_with(
        bbox="18.06,59.32,18.09,59.34",
        type=["way"],
        shape="polygon",
        zoom=10.0,
        min_length_m=100.0,
        max_length_m=1000.0,
        min_area_m2=250.0,
        max_area_m2=2500.0,
    )


def test_query_limit_is_forwarded_as_limit_per_page_for_all_pages():
    fc = make_fc()
    runner = CliRunner()
    with patch("osmgeojson.cli.OSMGeoJSONClient") as MockClient:
        MockClient.return_value.query_all.return_value = fc
        result = runner.invoke(cli, [
            "query",
            "--api-key", "sk-test",
            "--bbox", "18.06,59.32,18.09,59.34",
            "--all-pages",
            "--limit", "25",
        ])

    assert result.exit_code == 0, result.output
    MockClient.return_value.query_all.assert_called_once_with(
        bbox="18.06,59.32,18.09,59.34",
        limit_per_page=25,
        bbox_tiles=2,
    )


def test_query_all_pages_forwards_bbox_tiles():
    fc = make_fc()
    runner = CliRunner()
    with patch("osmgeojson.cli.OSMGeoJSONClient") as MockClient:
        MockClient.return_value.query_all.return_value = fc
        result = runner.invoke(cli, [
            "query",
            "--api-key", "sk-test",
            "--bbox", "18.06,59.32,18.09,59.34",
            "--all-pages",
            "--bbox-tiles", "4",
            "--limit", "25",
        ])

    assert result.exit_code == 0, result.output
    MockClient.return_value.query_all.assert_called_once_with(
        bbox="18.06,59.32,18.09,59.34",
        limit_per_page=25,
        bbox_tiles=4,
    )


def test_cost_forwards_zoom_length_and_area_filters():
    runner = CliRunner()
    with patch("osmgeojson.cli.OSMGeoJSONClient") as MockClient:
        MockClient.return_value.estimate_cost.return_value = type(
            "Estimate",
            (),
            {"estimated_credits": 1, "hints": [], "tier_limits": {}},
        )()
        result = runner.invoke(cli, [
            "cost",
            "--api-key", "sk-test",
            "--bbox", "18.06,59.32,18.09,59.34",
            "--type", "way",
            "--shape", "line",
            "--zoom", "9",
            "--min-length-m", "200",
            "--max-length-m", "2000",
            "--min-area-m2", "500",
            "--max-area-m2", "5000",
        ])

    assert result.exit_code == 0, result.output
    MockClient.return_value.estimate_cost.assert_called_once_with(
        bbox="18.06,59.32,18.09,59.34",
        type=["way"],
        shape="line",
        zoom=9.0,
        min_length_m=200.0,
        max_length_m=2000.0,
        min_area_m2=500.0,
        max_area_m2=5000.0,
    )
