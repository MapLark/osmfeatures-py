# MapLark OSM Features API

Official Python client for the [MapLark OSM Features API](https://maplark.com) (GeoJSON, FlatGeobuf, GeoParquet, CSV).

## Contents

- [Python SDK](#python-sdk)
- [Quick start](#quick-start)
- [Basic API usage](#basic-api-usage)
  - [Create a client](#create-a-client)
  - [Query OSM features](#query-osm-features)
  - [Auto-pagination and bbox tiling](#auto-pagination-and-bbox-tiling)
  - [Async client](#async-client)
  - [Convenience helpers](#convenience-helpers)
  - [Cost and usage](#cost-and-usage)
- [AI](#ai)
  - [MCP Server](#mcp-server)
  - [Cursor / Claude Desktop](#cursor--claude-desktop)
  - [Typical AI questions](#typical-ai-questions)
- [Places and routes](#places-and-routes)
  - [Places search](#places-search)
  - [Nearby](#nearby-ranked-from-a-point)
  - [Place details](#place-details)
  - [Opening hours](#opening-hours)
  - [X near Y](#x-near-y-local-join)
  - [Walk and bike routes](#walk-and-bike-routes)
- [CLI](#cli)
- [Example apps](#example-apps)

Query OpenStreetMap features such as buildings, streets, and Points of Interest easily. Search for OSM features by bounding box, tags, and geometry shape and get GeoJSON back within less than 250ms (dependent on query size). No converting between formats manually. The API keeps OpenStreetMap semantics intact, like tags and ways, and returns GeoJSON Features you can feed straight into Leaflet, MapLibre, OpenLayers, or any geospatial toolchain. It is backed by postgis with tiered API keys and rate limiting to keep noisy neighbours out to give you predictable latency for real traffic. It also has self-host path for those willing to host complex infrastructure themselves.

The postgis translation layer is very simple:

- `node` - Point
- `way` - LineString or Polygon
- `relation` - MultiPolygon or grouped geometries

You filter with the same tags mappers already use (`amenity=cafe`, `building=yes`, and so on). Knowledge from OSM, Overpass, and tagging docs transfers immediately.

To narrow down between "open ways" and "closed ways", use the `way_shape` parameter:

- `way_shape=line` - open ways (roads, paths, rivers) or line-shaped relations (routes, boundaries)
- `way_shape=polygon` - closed ways (buildings, parks) or multipolygon relations.
- `way_shape=all` - both shapes (default when way_shape is omitted).

For example, to get all buildings in an area:

`type=way & tags=building`

This is the equivalent of the Overpass query `way[building]`.

Read the full API reference here [https://maplark.com/developer](https://maplark.com/developer).

## Python SDK

This client library comes with auto-pagination, bbox tiling (enables larger bbox queries), retry/backoff, pandas/geopandas output, async support, convenience methods for common OSM layers (buildings, amenities, bike roads, and so on), a stdio MCP server for Claude / Cursor / Custom agents, and places/routes methods for opening hours and walk/bike routing. 

```
pip install osmfeatures
pip install "osmfeatures[geo]"   # pandas / geopandas / shapely support
pip install "osmfeatures[mcp]"   # stdio MCP server for Claude / Cursor
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



### Create a client

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



### Query OSM features

`query()` fetches a single page:

```python
fc = client.query(
    bbox="18.063,59.322,18.082,59.332",
    type="way",
    way_shape="line",
    tags=["highway=cycleway"],
    limit=500,
)

for feature in fc.features:
    print(feature["id"], feature["geometry"]["type"], feature.tags)
```

Common filters:

- `bbox="min_lon,min_lat,max_lon,max_lat"`
- `location="lat,lng"` with `radius` in metres
- `tags=["amenity=restaurant"]` (AND)
- `or_tags=["bicycle=yes", "bicycle=designated"]` (OR)
- `not_tags=["access=private"]` (exclude)
- `type="node" | "way" | "relation"`
- `way_shape="polygon" | "line" | "all"` (omit = both shapes; `all` also means both)
- `clip_geometry=True | False` (omit for the API default `True`; set `False` to keep full geometry outside bbox)
- `cursor` (pagination; use SDK `meta.next_cursor` from previous page, sourced from `X-Next-Cursor`)



### Auto-pagination and bbox tiling

Use `query_all()` to fetch all pages and deduplicate by OSM feature id. By default it splits the bbox into 2 tiles (power of 2) so large areas use more requests; pass `bbox_tiles=1` to disable, or raise it (`4`, `8`, …) for bigger areas:

```python
all_restaurants = client.query_all(
    bbox="18.063,59.322,18.082,59.332",
    tags="amenity=restaurant",
    max_features=55_000,  # client total cap; pass None for no cap
)

print(all_restaurants.meta.returned)
```



### Async client

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



### Convenience helpers

For common datasets, use convenience methods built on top of `query_all()`:

```python
from osmfeatures import OSMFeaturesClient, get_buildings, get_restaurants

with OSMFeaturesClient(api_key="sk-...") as client:
    buildings = get_buildings(client, bbox="18.063,59.322,18.082,59.332")
    restaurants = get_restaurants(client, bbox="18.063,59.322,18.082,59.332")
    print(len(buildings.features), len(restaurants.features))
```



### Cost and usage

```python
estimate = client.estimate_cost(
    bbox="18.063,59.322,18.082,59.332",
    tags=["building"],
)
print("estimated credits:", estimate.estimated_credits)

usage = client.usage()
print("Usage:", usage)
```

## AI
Add real geospatial intelligence to your Artificial Intelligence agents. Use Maplark in Claude Code or to new agentic apps.

### MCP Server

The MCP server is the agent surface. Your LLM is the planner: it chooses OSM tags, a bbox or location+radius, a time, a travel mode, and the next tool. The tools compute metres, ranks, opening-hours status, and walk/bike paths. You do not compute haversine, parse `opening_hours` strings, or invent coordinates.

Results come back as summaries (ids, names, OSM tags, lon/lat scalars, `distance_m`, `openNow`) plus a `collection_id`. They never include GeoJSON coordinate arrays. Call `preview_map(collection_id)` to draw: a local page loads [OpenFreeMap](https://openfreemap.org/) (Liberty) in MapLibre and fetches GeoJSON from localhost, so coordinates never enter the model. Call `export_geojson` only when the user asked for a raw file: it writes GeoJSON to disk and returns a path, not coordinates.

There is no geocode tool yet: pass a bbox or lat/lng as "here". For a large bbox, call `query_all` with `bbox_tiles` (power of 2; `1` disables tiling), not page `query` by hand. MCP `query_all` defaults to `max_features=10000` (raise it if `has_more`); the SDK default remains 55_000. Do not invent `places_near_to` or `places_open_after`. The server ships these rules as `instructions`.

#### Tools

- Places: `places_search`, `places_nearby`, `places_details`
- Routes: `routes_isochrone`, `routes_path`, `routes_optimized_path`
- Generic OSM: `query` (one page), `query_all` (tiled pages)
- Local (no HTTP): `nearest_within`, `filter_open`, `point_in_polygon`, `points_in_polygon`
- Draw / export: `preview_map`, `export_geojson`

#### Run MCP server manually
```bash
pip install "osmfeatures[mcp]"
export MAPLARK_API_KEY="sk-..."
osmfeatures mcp
```

#### Cursor / Claude Desktop
First install [uv](https://docs.astral.sh/uv/) for one-click server start.

```json
{
  "mcpServers": {
    "maplark": {
      "command": "uvx",
      "args": ["--from", "osmfeatures[mcp]", "osmfeatures", "mcp"],
      "env": { "MAPLARK_API_KEY": "YOUR_KEY" }
    }
  }
}
```

Planner rules: you pick tags, bbox or location+radius, budgets, `openNow`/`asOf`, and the next tool. Code computes metres, ranks, network paths, and opening-hours status.

### Typical AI questions

| Prompt | MCP tools |
|------|-----|
| "Cafes near me" | `places_nearby` or `places_search` with location+radius / bbox |
| "Restaurants within 150 m of a station" | two `places_search`, then `nearest_within` |
| "Bars open at 20:00" | `places_search` with `as_of` (no `open_now` so closed hits stay), then `filter_open` |
| "Cafes within a 10-minute bike ride" | `routes_isochrone` + `places_search` in a covering radius + `points_in_polygon` |
| "A walking bar crawl in Stockholm" | `places_search` + `routes_optimized_path` (`loop=true`) |
| "Walk from my hotel to the cafe, then the office" | `routes_path` with those stops in listed order |
| "Suggest a walk to a bar, a restaurant, and a cafe, no particular order" | `routes_optimized_path` with `loop=false` |
| "Is the office a 20-minute walk from the apartment?" | `routes_isochrone` from A, `point_in_polygon` for B |
| "Show this on a map" | `preview_map(collection_id)` after a search or route |

The same operations exist on `OSMFeaturesClient` when you are not going through an LLM (see [Places and routes](#places-and-routes)). Full HTTP reference: [https://maplark.com/developer](https://maplark.com/developer).


## Places and routes

`query()` is the generic OSM layer: buildings, roads, park polygons, any tag and geometry shape. Places and routes are the place and mobility layer on top of the same data. You pick OSM tags, an area, a time, and a travel mode. The API returns coordinates, opening-hours status, straight-line ranks, and walk/bike geometry. You do not compute metres or parse `opening_hours` strings yourself.

These are the same operations as the MCP tools; call them on `OSMFeaturesClient` when you are not going through an LLM.

Runnable Python chains live in `tests/example_apps/test_geo_agent.py`. Full HTTP reference: [https://maplark.com/developer](https://maplark.com/developer).

### Places search

`places_search()` finds places in a bounding box **or** a `location` plus `radius` (not both). Optional `tags` (AND) and `or_tags` (OR) use the same OSM filters as `query()`. Omit `limit` to use the API default (100, max 10_000).

```python
cafes = client.places_search(
    location={"lat": 59.316, "lng": 18.075},
    radius=800,
    or_tags=["amenity=cafe"],
    open_now=True,
    as_of="2026-08-10T18:00:00+02:00",
)
print(len(cafes["features"]), "open cafes")
print(cafes["metadata"]["evaluated_at"])
```

Response is a GeoJSON FeatureCollection plus `metadata.evaluated_at` (UTC instant used for hours).

### Nearby (ranked from a point)

`places_nearby()` answers "X near this point". It requires `tags` or `or_tags`. Results are ranked by straight-line spheroid distance, nearest first. Omit `radius` / `limit` to use the API defaults (1000 m / 100).

```python
nearby = client.places_nearby(
    location={"lat": 59.316, "lng": 18.075},
    or_tags=["amenity=cafe"],
    limit=5,
    open_now=True,
    as_of="2026-08-10T18:00:00+02:00",
)
for item in nearby["items"]:
    print(item["distance_m"], item["feature"]["id"])
```

Response: `{status, items: [{feature, distance_m}], estimated_units, evaluated_at}`.

### Place details

`places_details()` loads one place by the id that search or nearby returned (`node/123`). You can pass that string, or `osm_type` plus `osm_id`. Missing or non-place ids return HTTP 404.

```python
details = client.places_details(cafes["features"][0]["id"])
# same as: client.places_details("node", 123)
print(details["feature"]["properties"]["tags"])
print(details["timezone"], details["evaluated_at"])
```

Response: `{status, feature, estimated_units, evaluated_at, timezone}`. Hours are annotated at request time in the place's IANA zone (from its coordinates).

### Opening hours

Every place feature includes `properties.openNow` (`true` / `false`) when hours are evaluable. The field is omitted when hours are missing or unparseable.

Hours use each place's IANA timezone from its coordinates. There is no request `timezone` field.

- `open_now=True` keeps only known-open places. Missing or unparseable OSM `opening_hours` are dropped (same idea as Google Places `openNow`).
- `as_of` is the evaluation instant (default: now). A value with an offset (`Z` or `+02:00`) is an absolute instant. A naive value (`2026-08-10T20:00:00`, no offset) is that local clock at the search location or bbox center.
- Passing `as_of` or `open_now` also requires an OSM `opening_hours` tag, so untagged POIs do not fill the page.
- Closed places that have hours still return unless `open_now` is set.

### "X near Y" (local join)

`nearby` ranks against one point. "Restaurants within 150 m of a station" is two searches plus a local join. `nearest_within` does no HTTP.

```python
from osmfeatures import nearest_within

bbox = "18.05,59.33,18.10,59.36"
restaurants = client.places_search(bbox=bbox, or_tags=["amenity=restaurant"])
stations = client.places_search(bbox=bbox, or_tags=["railway=station"])
pairs = nearest_within(restaurants, stations, max_distance_m=150, limit=20)

for pair in pairs:
    print(pair["distance_m"], pair["feature"]["id"], "near", pair["nearest"]["id"])
```

Each pair is `{"feature": <primary>, "distance_m": <float>, "nearest": <secondary>}`. The point comes from `geometry` when it is a Point, else `properties.centroid`. A feature with neither raises `ValueError`. Empty secondary returns `[]`. Distances are spherical haversine (mean Earth radius 6371000 m). Fine at search `limit` (default 100).

### Walk and bike routes

Routing follows the OSM walk or bicycle network (query-time Dijkstra on tiled highways). Omit `travel_mode` to use the API default (`WALK`), or pass `"BICYCLE"`. Walk treats the graph as undirected (oneways ignored). Bicycle is directed and honors OSM oneway, `oneway:bicycle`, contraflow cycleways, and implied roundabout oneway. Car routing (`DRIVE`) is not available.

Duration budgets convert at about 5 km/h for walk (1.4 m/s) and 15 km/h for bicycle (4.2 m/s). Optional `search_buffer_m` widens the highway fetch corridor if a path cannot be formed in the default area.

Router endpoints return an OSRM-style `status` in a 200 body (not always HTTP 4xx):

- `ok`
- `area_too_large_for_tier` (no graph fetch)
- `tile_too_dense`
- `start_unreachable` / `end_unreachable`
- `no_path_within_area`

Always check `status` before reading `geometry`.

**Isochrone.** Reach polygon from `origin`. Provide exactly one of `max_distance_m` or `duration_s`. Geometry is a buffered union of reachable edges (city blocks stay holes).

```python
origin = {"lon": 18.075, "lat": 59.316}
iso = client.routes_isochrone(origin=origin, duration_s=600, travel_mode="WALK")
if iso["status"] == "ok":
    print(iso["geometry"]["type"], iso["distance_m"], iso["duration_s"])
```

**Path.** Given-order walk or bike through 2 to 250 stops. Does not reorder stops or close a loop. Two stops is A to B. Three or more stitches legs and returns `stop_distances_m`. To walk a known sequence home, repeat home as the last stop. Unordered search hits belong on `routes_optimized_path`.

```python
path = client.routes_path(
    stops=[origin, {"lon": 18.08, "lat": 59.318}],
    travel_mode="WALK",
)
```

**Optimized path.** Tour from `start` through unordered `stops` (nearest-neighbour + 2-opt). Do not put `start` in `stops`. `loop=True` (default) returns to start. `loop=False` is an open path that ends at the last ordered stop. Response includes `ordered_stops` (start first).

```python
opt = client.routes_optimized_path(
    start=origin,
    stops=[{"lon": 18.08, "lat": 59.318}, {"lon": 18.07, "lat": 59.320}],
    loop=True,
    travel_mode="WALK",
)
print(opt["status"], opt.get("ordered_stops"), opt.get("distance_m"))
```

Points accept `lon` or `lng`. Places methods send `{lat, lng}`. Route methods send `{lon, lat}`.

Local helpers (no HTTP): `nearest_within(primary, secondary, max_distance_m)` for "X near Y", and `point_in_geometry(lon, lat, geom)` for isochrone containment.

## CLI

If the package is installed, the CLI is available as `osmfeatures`:

```bash
export MAPLARK_API_KEY="sk-..."
osmfeatures query --bbox "18.063,59.322,18.082,59.332" --tags building --type way
osmfeatures query --bbox "18.063,59.322,18.082,59.332" --tags building --all-pages --bbox-tiles 4
osmfeatures mcp
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
- `test_geo_agent.py`: Python places/routes chains (bar crawl, bike parks, isochrone filter/compare/coverage, client-side open-at-clock).

Run all example apps:

```bash
pytest tests/example_apps -v
```

Run one example app:

```bash
pytest tests/example_apps/test_restaurant_guide.py -v
```

