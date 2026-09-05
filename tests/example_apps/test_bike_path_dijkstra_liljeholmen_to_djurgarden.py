"""
App idea: "cycle route planner" feature.

Fetches the cycling-way network across the Liljeholmen → Djurgården corridor
and uses client-side Dijkstra to find the shortest-distance bike path between
the two landmarks.

Why multiple small bbox queries instead of one large one:
  A single bbox covering the full ~9 km corridor would exceed practical API
  result limits.  Instead the corridor is split into three overlapping tiles
  and each tile is queried separately.  The results are merged client-side
  before graph construction — the standard pattern for map apps that
  progressively load data as the user pans or zooms.

Why client-side Dijkstra:
  The API exposes raw GeoJSON elements; it does not support graph traversal
  or path-search server-side.  Dijkstra is performed in the client over the
  merged graph built from the tiled responses.

Algorithm notes:
  - Dijkstra, weighted by haversine edge length in metres (minimises distance).
  - Grade-separated crossings are handled via the layer-aware node tuples
    produced by ``build_nx_graph`` (see ``graph_utils.py``).

API calls: ``query_all`` per tile with ``or_tags=[bicycle, highway=cycleway]``
and ``not_tags`` filtering non-rideable values.
"""

import pytest
import networkx as nx
from haversine import Unit, haversine

from osmfeatures import OSMFeatureCollection, OSMFeaturesClient
from tests.example_apps.graph_utils import build_nx_graph

CORRIDOR_TILES = [
    "18.000,59.305,18.070,59.340",  # Liljeholmen / Södermalm west
    "18.060,59.308,18.130,59.340",  # Södermalm central / Slussen
    "18.120,59.308,18.180,59.340",  # Djurgården
]


def test_bike_path_dijkstra_liljeholmen_to_djurgarden(client: OSMFeaturesClient):
    # Query each corridor tile separately; results are merged client-side.
    # or_tags=bicycle matches any way carrying a bicycle key.
    # or_tags=highway=cycleway catches dedicated cycleways that carry no
    # explicit bicycle tag (permission implied by way type).
    # not_tags excludes the known non-bikeable values.
    all_features: list = []
    seen_tile_ids: set[str] = set()

    for tile_bbox in CORRIDOR_TILES:
        tile_data = client.query_all(
            limit_per_page=500,
            bbox=tile_bbox,
            type="way",
            way_shape="line",
            clip_geometry=False,
            or_tags=["bicycle", "highway=cycleway"],
            not_tags=["bicycle=no", "bicycle=private", "bicycle=dismount", "bicycle=use_sidepath"],
            disable_budget_warning=True,
        )
        assert isinstance(tile_data, OSMFeatureCollection)
        for f in tile_data["features"]:
            if f["id"] not in seen_tile_ids:
                seen_tile_ids.add(f["id"])
                all_features.append(f)

    if not all_features:
        pytest.fail(
            "No cycling features returned across all corridor tiles. "
            "The OSM data for this area has likely not been imported into the backend. "
            "Run 'make import' (or 'make import_prod') to load the Stockholm dataset."
        )

    if len(all_features) < 10:
        pytest.skip("Not enough cycling-way features across the corridor tiles")

    G = build_nx_graph(all_features)
    if G.number_of_nodes() < 20:
        pytest.skip("Cycling graph too sparse to route across the corridor tiles")

    # Work within the largest connected component
    largest_cc = max(nx.connected_components(G), key=len)
    H = G.subgraph(largest_cc)

    # Approximate centroids for the two landmarks
    START_LON, START_LAT = 18.025876, 59.310419   # Liljeholmen
    END_LON,   END_LAT   = 18.130, 59.333          # Djurgården

    # --- Data-coverage check: use the full graph G ---
    g_start = min(G.nodes, key=lambda n: haversine((n[1], n[0]), (START_LAT, START_LON), unit=Unit.METERS))
    g_end   = min(G.nodes, key=lambda n: haversine((n[1], n[0]), (END_LAT,   END_LON),   unit=Unit.METERS))

    g_snap_start_m = haversine((g_start[1], g_start[0]), (START_LAT, START_LON), unit=Unit.METERS)
    g_snap_end_m   = haversine((g_end[1],   g_end[0]),   (END_LAT,   END_LON),   unit=Unit.METERS)

    if g_snap_start_m > 500:
        pytest.fail(
            f"Nearest cycling node to Liljeholmen is {g_snap_start_m:.0f} m away "
            "(threshold 500 m)."
        )
    else:
        print(f"Start snap: Liljeholmen -> {g_start} ({g_snap_start_m:.0f} m)")

    if g_snap_end_m > 500:
        pytest.fail(
            f"Nearest cycling node to Djurgården target is {g_snap_end_m:.0f} m away "
            "(threshold 500 m)."
        )
    else:
        print(f"End snap: Djurgården -> {g_end} ({g_snap_end_m:.0f} m)")

    # --- Dijkstra snap: use the largest connected component H ---
    start = min(H.nodes, key=lambda n: haversine((n[1], n[0]), (START_LAT, START_LON), unit=Unit.METERS))
    end   = min(H.nodes, key=lambda n: haversine((n[1], n[0]), (END_LAT,   END_LON),   unit=Unit.METERS))

    h_snap_start_m = haversine((start[1], start[0]), (START_LAT, START_LON), unit=Unit.METERS)
    h_snap_end_m   = haversine((end[1],   end[0]),   (END_LAT,   END_LON),   unit=Unit.METERS)

    if h_snap_start_m > 500:
        pytest.fail(
            f"Nearest reachable (largest-CC) node to Liljeholmen is {h_snap_start_m:.0f} m away "
            "(threshold 500 m) - Dijkstra would route from an unrelated location."
        )
    if h_snap_end_m > 500:
        pytest.fail(
            f"Nearest reachable (largest-CC) node to Djurgården is {h_snap_end_m:.0f} m away "
            "(threshold 500 m) - Dijkstra would route to an unrelated location."
        )

    if start == end:
        pytest.skip("Start and end snapped to the same node - graph too sparse")

    if not nx.has_path(H, start, end):
        pytest.skip(
            "No path found between Liljeholmen and Djurgården "
            "- cycling graph may be disconnected across the corridor tiles"
        )

    path = nx.dijkstra_path(H, start, end, weight="weight")
    total_dist_m = nx.dijkstra_path_length(H, start, end, weight="weight")
    route_dist_km = total_dist_m / 1000

    assert path[0] == start
    assert path[-1] == end
    assert len(path) >= 2

    for i in range(len(path) - 1):
        assert H.has_edge(path[i], path[i + 1]), (
            f"Path step {path[i]} -> {path[i + 1]} is not a graph edge"
        )

    print(
        f"\n[bike route] Liljeholmen -> Djurgården: "
        f"{len(path) - 1} segments, {route_dist_km:.1f} km"
    )
    print(f"  Graph : {H.number_of_nodes()} nodes, {H.number_of_edges()} edges ({len(CORRIDOR_TILES)} tile queries)")
    print(f"  Snap G: start {g_snap_start_m:.0f} m, end {g_snap_end_m:.0f} m")
    print(f"  Snap H: start {h_snap_start_m:.0f} m, end {h_snap_end_m:.0f} m")
    print(f"  Start : {start}")
