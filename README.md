# osmgeojson-python

Python SDK for the **MapLark** OSM GeoJSON API with auto-pagination, bounding box chunking, retry/backoff, pandas/geopandas output, async support, and convenience methods to get common OSM data such as buildings, amenities, bike roads, etc.

```
pip install osmgeojson
pip install "osmgeojson[geo]"   # pandas / geopandas / shapely support
```

The SDK talks to the MapLark API at `https://api.maplark.com`.

## Quick start

```python
from osmgeojson import OSMGeoJSONClient

with OSMGeoJSONClient(api_key="sk-...") as client:
    fc = client.query(bbox="18.06,59.32,18.09,59.34", tags=["building"])
    print(len(fc.features), "buildings found")
```

## Basic API usage

### 1) Create a client

```python
from osmgeojson import OSMGeoJSONClient

client = OSMGeoJSONClient(api_key="sk-...")
```

You can use the client directly and close it when done, or use a context manager:

```python
from osmgeojson import OSMGeoJSONClient

with OSMGeoJSONClient(api_key="sk-...") as client:
    ...
```

### 2) Query OSM elements

`query()` fetches a single page:

```python
fc = client.query(
    bbox="18.063,59.322,18.082,59.332",
    type="way",
    shape="line",
    tags=["highway=cycleway"],
    limit=500,
)

for feature in fc.features:
    print(feature["id"], feature["geometry"]["type"], feature.tags)
```

Geometry size and zoom filters:

```python
# Large buildings at a low zoom (simplified geometry)
large_buildings = client.query(
    bbox="18.070,59.323,18.075,59.327",
    type="way,relation",
    shape="polygon",
    tags="building",
    zoom=11,
    min_area_m2=3000,
    limit=300,
)

# Long roads only
long_roads = client.query(
    bbox="18.020,59.310,18.180,59.365",
    type="way",
    shape="line",
    tags="highway",
    min_length_m=1200,
    max_length_m=20000,
    limit=300,
)
```

Common filters:

- `bbox="min_lon,min_lat,max_lon,max_lat"`
- `around="lon,lat,radius_m"`
- `tags=["amenity=restaurant"]` (AND)
- `or_tags=["bicycle=yes", "bicycle=designated"]` (OR)
- `not_tags=["access=private"]` (exclude)
- `type="node" | "way" | "relation"`
- `shape="polygon" | "line" | "all"` (omit = both shapes; `all` also means both)
- `zoom=11` (simplify geometry at lower zooms)
- `min_length_m` / `max_length_m` (line length in metres)
- `min_area_m2` / `max_area_m2` (polygon area in square metres)
- `cursor` (pagination; use `meta.next_cursor` from previous page)

### 3) Auto-pagination

Use `query_all()` to fetch all pages and deduplicate by OSM feature id:

```python
all_restaurants = client.query_all(
    bbox="18.063,59.322,18.082,59.332",
    tags="amenity=restaurant",
    page_size=1000,
)

print(all_restaurants.meta.returned)
```

### 4) Async client

Async methods mirror the sync API (`query_async`, `query_all_async`, `query_large_area_async`):

```python
import asyncio
from osmgeojson import AsyncOSMGeoJSONClient


async def main() -> None:
    async with AsyncOSMGeoJSONClient(api_key="sk-...") as client:
        fc = await client.query_async(
            bbox="18.06,59.32,18.09,59.34",
            tags=["building"],
        )
        print(len(fc.features))


asyncio.run(main())
```

### 5) Convenience helpers

For common datasets, use convenience methods built on top of `query_all()`:

```python
from osmgeojson import OSMGeoJSONClient, get_buildings, get_restaurants

with OSMGeoJSONClient(api_key="sk-...") as client:
    buildings = get_buildings(client, bbox="18.063,59.322,18.082,59.332")
    restaurants = get_restaurants(client, bbox="18.063,59.322,18.082,59.332")
    print(len(buildings.features), len(restaurants.features))
```

### 6) Cost and usage

```python
estimate = client.estimate_cost(
    bbox="18.063,59.322,18.082,59.332",
    tags=["building"],
)
print("estimated credits:", estimate.estimated_credits)

usage = client.usage()
print("remaining this month:", usage.get("remaining_this_month"))
```

### 7) CLI usage

If the package is installed, the CLI is available as `osmgeojson`:

```bash
export MAPLARK_API_KEY="sk-..."
osmgeojson query --bbox "18.063,59.322,18.082,59.332" --tags building

# Large buildings at low zoom
osmgeojson query \
  --bbox "18.070,59.323,18.075,59.327" \
  --type way,relation \
  --shape polygon \
  --tags building \
  --zoom 11 \
  --min-area-m2 3000

# Long roads only
osmgeojson query \
  --bbox "18.020,59.310,18.180,59.365" \
  --type way \
  --shape line \
  --tags highway \
  --min-length-m 1200 \
  --max-length-m 20000
```

## Example apps

The repository includes runnable example-app tests in `tests/example_apps/` showing end-to-end usage patterns against real OSM data.

- `test_restaurant_guide.py`: restaurant discovery list with names/cuisines and map coordinates.
- `test_park_bench_finder.py`: bench finder for park maps (`amenity=bench`).
- `test_park_explorer.py`: park browser with polygon boundaries, centroids, and area estimates.
- `test_cycling_trails.py`: unpaved cycling trail layer for MTB/gravel planning.
- `test_city_cycling_infrastructure.py`: city cycling overlay combining cycleways and bike lanes.
- `test_lakeside_ice_cream_hunt.py`: nearest ice cream shops to waterfront edges.
- `test_pedestrian_shortest_path.py`: shortest walking route via graph + Dijkstra.
- `test_pedestrian_wavefront_bfs.py`: hop-based accessibility rings via BFS.
- `test_bike_path_dijkstra_liljeholmen_to_djurgarden.py`: tiled corridor bike routing from Liljeholmen to Djurgarden.
- `test_geometry_filters.py`: zoom + area/length filters for large buildings and long roads.

Run all example apps:

```bash
pytest tests/example_apps -v
```

Run one example app:

```bash
pytest tests/example_apps/test_restaurant_guide.py -v
```

## Release notes

### 0.1.0 (2026-06-01)

- First PyPI release of `osmgeojson`.
- Includes sync and async clients with query, auto-pagination, and large-area chunked querying.
- Adds CLI commands and convenience helpers for common OSM datasets.
- Ships typed models and optional geospatial extras (`pandas`, `geopandas`, `shapely`).

See [https://maplark.com/developer](https://maplark.com/developer) for full API docs.
