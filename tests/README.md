# osmfeatures - Test Suite

Unit tests for the `osmfeatures` SDK. No live API or database required - all HTTP calls are intercepted by the [`responses`](https://pypi.org/project/responses/) library.

## Setup

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[all,test]"
```

This installs the SDK in editable mode plus all optional extras (`pandas`, `geopandas`, `shapely`, `mcp`) and the test dependencies (`pytest`, `responses`, `pytest-asyncio`).

## Running

From the repository root:

```bash
source .venv/bin/activate

# All tests
python3 -m pytest tests/ -v

# A specific file
python3 -m pytest tests/test_chunking.py -v

# A specific test
python3 -m pytest tests/test_client.py::test_query_returns_feature_collection -v
```

## Test files

| File | What it covers |
|------|----------------|
| `test_chunking.py` | `split_bbox_tiles`, `merge_features`, `around_to_bbox`, `parse_bbox` |
| `test_client.py` | `OSMFeaturesClient` - happy paths, auth header, error responses, cost estimate |
| `test_cli.py` | CLI `query` / `mcp` - `--output geojson/csv/table`, missing API key or MCP extra |
| `test_mcp_session.py` | Geo-agent session summaries, local joins, containment, file export |
| `test_mcp_server.py` | `build_server()` tool names, `instructions`, FastMCP `call_tool` wiring |
| `test_preview.py` | Local MapLibre preview HTTP (OpenFreeMap, no geometry in planner payload) |
| `test_retry.py` | Retry + backoff: 429->200, exhausted retries, monthly limit, `Retry-After` header |
| `test_pagination.py` | `query_all` single/multi-page, dedup, stale cursor guard |
| `test_output.py` | `to_dataframe` column names, `to_geodataframe` CRS/geometry (skipped if `pandas`/`geopandas` not installed) |

## Fixtures (`conftest.py`)

- `client` - `OSMFeaturesClient` with `max_retries=0` (no retries, fast failures)
- `client_with_retries` - `OSMFeaturesClient` with `max_retries=3, backoff_base=0.0` (instant retries for speed)
- `make_feature(fid, tags)` - builds a minimal GeoJSON Feature dict
- `make_feature_collection(features)` / `add_features_response(...)` - FeatureCollection body + pagination headers
