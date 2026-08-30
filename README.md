# MapLark OSM Features API

Official Python client for the [MapLark OSM Features API](https://maplark.com) (GeoJSON, FlatGeobuf, GeoParquet, CSV). 


Query OpenStreetMap features such as buildings, streets, and Points of Interest easily. Search for OSM features by bounding box, tags, and geometry shape and get GeoJSON back within less than 250ms (dependent on query size). No converting between formats manually. The API keeps OpenStreetMap semantics intact, like tags and ways, and returns GeoJSON Features you can feed straight into Leaflet, MapLibre, OpenLayers, or any geospatial toolchain. It is backed by postgis with tiered API keys and rate limiting to keep noisy neighbours out to give you predictable latency for real traffic. It also has self-host path for those willing to host complex infrastructure themselves.

The postgis translation layer is very simple:

- `node` - Point
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

This client library comes with auto-pagination, bbox tiling (enables larger bbox queries), retry/backoff, pandas/geopandas output, async support, convenience methods for common OSM layers (buildings, amenities, bike roads, and so on), and Geo-agent methods for places, opening hours, and walk/bike routing. 

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
- `location="lat,lng"` with `radius` in metres
- `tags=["amenity=restaurant"]` (AND)
- `or_tags=["bicycle=yes", "bicycle=designated"]` (OR)
- `not_tags=["access=private"]` (exclude)
- `type="node" | "way" | "relation"`
- `shape="polygon" | "line" | "all"` (omit = both shapes; `all` also means both)
- `clip_geometry=True | False` (`True` default; set `False` to keep full geometry outside bbox)
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

### 7) Geo Agent (places and routes)

`query()` is the generic OSM layer: buildings, roads, park polygons, any tag and geometry shape. Geo-agent is the place and mobility layer on top of the same data. You pick OSM tags, an area, a time, and a travel mode. The API returns coordinates, opening-hours status, straight-line ranks, and walk/bike geometry. You do not compute metres or parse `opening_hours` strings yourself. 

These endpoints can be used by an AI agent to generate responses such as "cafes near me" or "suggest a bar crawl in Stockholm". For example

#### Typical questions

| Prompt | SDK |
|------|-----|
| "Cafes near me" | `client.places_nearby()` or `client.places_search()` with `location` + `radius` |
| "Restaurants within 150 m of a station" | two `client.places_search()`, then `nearest_within()` |
| "Bars open at 20:00" | `client.places_search()` with `as_of`, keep `openingHours.status == "open"` |
| "Cafes within a 10-minute bike ride" | `client.routes_isochrone()` + `client.places_search()` in a covering radius + keep points inside the polygon |
| "A walking bar crawl in Stockholm" | `client.places_search()` + `client.routes_optimized_path()` (`loop=True`) |
| "Walk from my hotel to the cafe, then the office" | `client.routes_path()` with those stops in listed order |
| "Suggest a walk to a bar, a restaurant, and a cafe, no particular order" | `client.routes_optimized_path()` with `loop=False` |
| "Is the office a 20-minute walk from the apartment?" | `client.routes_isochrone()` from A, point-in-polygon for B |


Runnable prompts like these live in `tests/example_apps/test_geo_agent.py`. Full HTTP reference: [https://maplark.com/developer](https://maplark.com/developer).

#### Places search

`places_search()` finds places in a bounding box **or** a `location` plus `radius` (not both). Optional `tags` (AND) and `or_tags` (OR) use the same OSM filters as `query()`. Default `limit` is 100 (max 10_000).

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

#### Nearby (ranked from a point)

`places_nearby()` answers "X near this point". It requires `tags` or `or_tags`. Results are ranked by straight-line spheroid distance, nearest first. Default radius is 1000 m. Default `limit` is 10.

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

#### Place details

`places_details()` loads one place by the id that search or nearby returned (`node/123`). You can pass that string, or `osm_type` plus `osm_id`. Missing or non-place ids return HTTP 404.

```python
details = client.places_details(cafes["features"][0]["id"])
# same as: client.places_details("node", 123)
print(details["feature"]["properties"]["tags"])
print(details["timezone"], details["evaluated_at"])
```

Response: `{status, feature, estimated_units, evaluated_at, timezone}`. Hours are annotated at request time in the place's IANA zone (from its coordinates).

#### Opening hours

Every place feature includes `properties.openingHours`:

- `status`: `open`, `closed`, or `unknown`
- `openNow`: `true` / `false`, or `null` when unknown

Hours use each place's IANA timezone from its coordinates. There is no request `timezone` field.

- `open_now=True` keeps only known-open places. Missing or unparseable OSM `opening_hours` are dropped (same idea as Google Places `openNow`).
- `as_of` is the evaluation instant (default: now). A value with an offset (`Z` or `+02:00`) is an absolute instant. A naive value (`2026-08-10T20:00:00`, no offset) is that local clock at the search location or bbox center.
- Passing `as_of` or `open_now` also requires an OSM `opening_hours` tag, so untagged POIs do not fill the page.
- Closed places that have hours still return unless `open_now` is set.

#### "X near Y" (local join)

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

#### Walk and bike routes

Routing follows the OSM walk or bicycle network (query-time Dijkstra on tiled highways). Provide `travel_mode="WALK"` (default) or `"BICYCLE"`. Walk treats the graph as undirected (oneways ignored). Bicycle is directed and honors OSM oneway, `oneway:bicycle`, contraflow cycleways, and implied roundabout oneway. Car routing (`DRIVE`) is not available.

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


### 8) CLI usage

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
- `test_geo_agent.py`: geo-agent chains (bar crawl, bike parks, isochrone filter/compare/coverage, client-side open-at-clock).

Run all example apps:

```bash
pytest tests/example_apps -v
```

Run one example app:

```bash
pytest tests/example_apps/test_restaurant_guide.py -v
```

