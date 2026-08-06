# MapLark OSM Features API

**Deprecated** in favor of [osmfeatures](https://pypi.org/project/osmfeatures/).
Please switch to the new package for continued development and support.


Query OpenStreetMap features such as buildings, streets, and Points of Interest easily. Search for OSM features by bounding box, tags, and geometry shape and get GeoJSON back within less than 250ms (dependent on query size). No converting between formats manually. The API keeps OpenStreetMap semantics intact, like tags and ways, and returns GeoJSON Features you can feed straight into Leaflet, MapLibre, OpenLayers, or any geospatial toolchain. It is backed by postgis with tiered API keys and rate limiting to keep noisy neighbours out to give you predictable latency for real traffic. It also has self-host path for those willing to host complex infrastructure themselves.

The translation layer is very simple:

- `node` - GeoJSON Point
- `way` - LineString or Polygon
- `relation` - MultiPolygon or grouped geometries

You filter with the same tags mappers already use (`amenity=cafe`, `building=yes`, and so on). Knowledge from OSM, Overpass, and tagging docs transfers immediately.

To narrow down between "open ways" and "closed ways", use the `shape` parameter:

- `shape=line` - open ways (roads, paths, rivers) or line-shaped relations (routes, boundaries)
- `shape=polygon` - closed ways (buildings, parks) or multipolygon relations.
- `shape=all` - both shapes (default when shape is omitted).

For example, to get all buildings in an area:

`type=way & tags=building`

This is the equivalent of the Overpass query `way[building]`.

Read the full API reference here [https://maplark.com/developer](https://maplark.com/developer).

## Python SDK

This client library comes with auto-pagination, bbox tiling (enables larger bbox queries), retry/backoff, pandas/geopandas output, async support, and convenience methods to get common OSM data such as buildings, amenities, bike roads, etc. 

```
pip install osmfeatures
pip install "osmfeatures[geo]"   # pandas / geopandas / shapely support
```

Official client for the MapLark OSM Features API (GeoJSON, FlatGeobuf, GeoParquet, CSV).
The SDK talks to `api.maplark.com` by default.

## Quick start

```python
from osmfeatures import OSMFeaturesClient

with OSMFeaturesClient(api_key="sk-...") as client:
    fc = client.query(bbox="18.06,59.32,18.09,59.34", tags=["building"])
    print(len(fc.features), "buildings found")
```



## Basic API usage



### 1) Create a client

```python
from osmfeatures import OSMFeaturesClient

client = OSMFeaturesClient(api_key="sk-...")
```

You can use the client directly and close it when done, or use a context manager:

```python
from osmfeatures import OSMFeaturesClient

with OSMFeaturesClient(api_key="sk-...") as client:
    ...
```



### 2) Query OSM features

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

Common filters:

- `bbox="min_lon,min_lat,max_lon,max_lat"`
- `around="lon,lat,radius_m"`
- `tags=["amenity=restaurant"]` (AND)
- `or_tags=["bicycle=yes", "bicycle=designated"]` (OR)
- `not_tags=["access=private"]` (exclude)
- `type="node" | "way" | "relation"`
- `shape="polygon" | "line" | "all"` (omit = both shapes; `all` also means both)
- `cursor` (pagination; use SDK `meta.next_cursor` from previous page, sourced from `X-Next-Cursor`)



### 3) Auto-pagination and bbox tiling

Use `query_all()` to fetch all pages and deduplicate by OSM feature id. By default it splits the bbox into 2 tiles (power of 2) so large areas use more requests; pass `bbox_tiles=1` to disable, or raise it (`4`, `8`, …) for bigger areas:

```python
all_restaurants = client.query_all(
    bbox="18.063,59.322,18.082,59.332",
    tags="amenity=restaurant",
    limit_per_page=1000,  # page size per HTTP request
    max_features=55_000,  # total cap; pass None for no cap
    bbox_tiles=2,  # default
)

print(all_restaurants.meta.returned)
```



### 4) Async client

Async methods mirror the sync API (`query_async`, `query_all_async`):

```python
import asyncio
from osmfeatures import AsyncOSMFeaturesClient


async def main() -> None:
    async with AsyncOSMFeaturesClient(api_key="sk-...") as client:
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
from osmfeatures import OSMFeaturesClient, get_buildings, get_restaurants

with OSMFeaturesClient(api_key="sk-...") as client:
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
print("Usage:", usage)
```



### 7) CLI usage

If the package is installed, the CLI is available as `osmfeatures`:

```bash
export MAPLARK_API_KEY="sk-..."
osmfeatures query --bbox "18.063,59.322,18.082,59.332" --tags building --type way
osmfeatures query --bbox "18.063,59.322,18.082,59.332" --tags building --all-pages --bbox-tiles 4
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

