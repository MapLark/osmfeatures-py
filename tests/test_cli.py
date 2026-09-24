"""Tests for the osmgeojson CLI - query output formats and basic error paths."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from osmfeatures.cli import cli
from osmfeatures import OSMFeatureCollection
from osmfeatures.models import BinaryQueryResult, OSMFeature, ResponseMeta
from tests.conftest import make_test_feature


def make_fc(
    feature_dicts: list[dict] | None = None,
    *,
    has_more: bool = False,
    next_cursor: str | None = None,
) -> OSMFeatureCollection:
    feature_dicts = feature_dicts or [make_test_feature("way/1")]
    return OSMFeatureCollection(
        features=[OSMFeature.from_dict(f) for f in feature_dicts],
        meta=ResponseMeta(
            returned=len(feature_dicts),
            has_more=has_more,
            next_cursor=next_cursor,
        ),
    )


# ---------------------------------------------------------------------------
# --output geojson (default)
# ---------------------------------------------------------------------------

def test_query_output_geojson():
    fc = make_fc()
    runner = CliRunner()
    with patch("osmfeatures.cli.OSMFeaturesClient") as MockClient:
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
    with patch("osmfeatures.cli.OSMFeaturesClient") as MockClient:
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
    with patch("osmfeatures.cli.OSMFeaturesClient") as MockClient:
        MockClient.return_value.query.return_value = fc
        result = runner.invoke(cli, [
            "query",
            "--api-key", "sk-test",
            "--bbox", "18.06,59.32,18.09,59.34",
            "--output", "table",
        ])
    assert result.exit_code == 0, result.output
    assert "id" in result.output


def test_query_accept_writes_raw_bytes():
    raw = BinaryQueryResult(
        content=b"id,geometry\nway/1,POINT(18 59)\n",
        meta=ResponseMeta(returned=1, has_more=False),
    )
    runner = CliRunner()
    with patch("osmfeatures.cli.OSMFeaturesClient") as MockClient:
        MockClient.return_value.query.return_value = raw
        result = runner.invoke(cli, [
            "query",
            "--api-key", "sk-test",
            "--bbox", "18.06,59.32,18.09,59.34",
            "--accept", "text/csv",
        ])

    assert result.exit_code == 0, result.output
    assert result.output == raw.content.decode()
    kwargs = MockClient.return_value.query.call_args.kwargs
    assert kwargs["accept"] == "text/csv"


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
    with patch("osmfeatures.cli.OSMFeaturesClient") as MockClient:
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
    with patch("osmfeatures.cli.OSMFeaturesClient") as MockClient:
        MockClient.return_value.query.return_value = fc
        result = runner.invoke(cli, [
            "query",
            "--api-key", "sk-test",
            "--bbox", "18.06,59.32,18.09,59.34",
            "--type", "way",
            "--way-shape", "polygon",
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
        way_shape="polygon",
        zoom=10.0,
        min_length_m=100.0,
        max_length_m=1000.0,
        min_area_m2=250.0,
        max_area_m2=2500.0,
    )


def test_mcp_missing_api_key(monkeypatch):
    monkeypatch.delenv("MAPLARK_API_KEY", raising=False)
    result = CliRunner().invoke(cli, ["mcp"])
    assert result.exit_code != 0
    assert "API key" in result.output


def test_mcp_missing_extra(monkeypatch):
    import builtins
    import sys

    monkeypatch.delitem(sys.modules, "osmfeatures.mcp.mcp_server", raising=False)
    for key in list(sys.modules):
        if key == "mcp" or key.startswith("mcp."):
            monkeypatch.delitem(sys.modules, key, raising=False)

    real_import = builtins.__import__

    def blocked(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "mcp" or name.startswith("mcp."):
            raise ModuleNotFoundError("No module named 'mcp'", name="mcp")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", blocked)
    result = CliRunner().invoke(cli, ["mcp", "--api-key", "sk-test"])
    assert result.exit_code != 0
    assert "osmfeatures[mcp]" in result.output


def test_mcp_import_error_is_not_missing_extra(monkeypatch):
    import sys
    import types

    monkeypatch.setitem(sys.modules, "osmfeatures.mcp.mcp_server", types.ModuleType("osmfeatures.mcp.mcp_server"))
    result = CliRunner().invoke(cli, ["mcp", "--api-key", "sk-test"])
    assert result.exit_code != 0
    assert "osmfeatures[mcp]" not in result.output
    assert result.exception is not None


def test_mcp_missing_instructions_is_not_missing_extra(monkeypatch):
    import sys

    class Boom:
        def __getattr__(self, name):
            raise FileNotFoundError("AGENT_INSTRUCTIONS.md missing")

    monkeypatch.setitem(sys.modules, "osmfeatures.mcp.mcp_server", Boom())
    result = CliRunner().invoke(cli, ["mcp", "--api-key", "sk-test"])
    assert result.exit_code != 0
    assert "osmfeatures[mcp]" not in result.output
    assert "AGENT_INSTRUCTIONS.md" in result.output


def test_stats_forwards_group_by():
    runner = CliRunner()
    with patch("osmfeatures.cli.OSMFeaturesClient") as MockClient:
        MockClient.return_value.count.return_value = {
            "groups": [{"value": "cafe", "count": 12}],
            "total": 12,
            "truncated": False,
        }
        result = runner.invoke(cli, [
            "stats",
            "--api-key", "sk-test",
            "--group-by", "amenity",
            "--bbox", "18.06,59.32,18.09,59.34",
            "--tags", "amenity",
        ])
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert parsed["total"] == 12
    MockClient.return_value.count.assert_called_once_with(
        group_by="amenity",
        bbox="18.06,59.32,18.09,59.34",
        tags=["amenity"],
    )
