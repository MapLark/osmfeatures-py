"""
App idea: "walking directions" feature.

Fetch the pedestrian footway network inside a small Stockholm bbox, build an
undirected weighted graph, then use Dijkstra to find the shortest walking
route between two points in the network.

The route is returned as an ordered list of (lon, lat) waypoints that a
frontend could render as a highlighted path on the map.

Graph construction:
  - Nodes: ``(lon, lat, layer)`` tuples — the layer prevents grade-separated
    crossings (bridges, tunnels) from being falsely joined to at-grade ways
    sharing the same coordinate.
  - Edge weights: haversine distance in metres.

API calls: multiple ``type=way&shape=line&tags=highway=<type>`` queries,
one per pedestrian highway type, merged client-side with deduplication.
"""

import pytest
import networkx as nx

from osmfeatures import OSMFeatureCollection, OSMFeaturesClient
from tests.example_apps.graph_utils import build_nx_graph

GAMLA_STAN_BBOX = "18.063,59.322,18.082,59.332"

PEDESTRIAN_HIGHWAY_TYPES = {
    "footway", "pedestrian", "steps", "path", "service", "residential", "living_street",
}


def test_pedestrian_shortest_path(client: OSMFeaturesClient):
    pedestrian_features = []
    seen_feature_ids: set[str] = set()

    for highway_type in PEDESTRIAN_HIGHWAY_TYPES:
        data = client.query(
            bbox=GAMLA_STAN_BBOX,
            type="way",
            shape="line",
            tags=f"highway={highway_type}",
            limit=300,
        )
        assert isinstance(data, OSMFeatureCollection)

        for feature in data["features"]:
            feature_id = feature["id"]
            if feature_id in seen_feature_ids:
                continue
            if feature.tags.get("foot") in {"no", "private"}:
                continue
            if feature.tags.get("access") in {"no", "private"}:
                continue
            seen_feature_ids.add(feature_id)
            pedestrian_features.append(feature)

    if len(pedestrian_features) < 5:
        pytest.skip("Not enough pedestrian way features to build a meaningful graph")

    G = build_nx_graph(pedestrian_features)
    assert G.number_of_nodes() > 10, "Graph too small - expected more nodes in Gamla Stan"

    # Work within the largest connected component so Dijkstra always finds a path
    largest_cc = max(nx.connected_components(G), key=len)
    H = G.subgraph(largest_cc)

    # Pick a start node that is an actual intersection (degree >= 2)
    start = next((n for n in H.nodes if H.degree(n) >= 2), None)
    assert start is not None, "No intersection nodes found in graph"

    # Compute shortest-path distances from start using networkx Dijkstra
    dist = nx.single_source_dijkstra_path_length(H, start, weight="weight")

    # Find reachable nodes more than 50 m away as candidate endpoints
    candidates = [n for n, d in dist.items() if d > 50]
    assert candidates, "No reachable nodes found more than 50 m from start"

    # Pick the farthest reachable node as the destination
    end = max(candidates, key=lambda n: dist[n])

    # Retrieve the actual waypoint list
    path = nx.dijkstra_path(H, start, end, weight="weight")
    total_dist_m = dist[end]

    assert len(path) >= 2, "Path must have at least 2 waypoints"
    assert path[0] == start
    assert path[-1] == end
    assert total_dist_m > 0

    # Verify each step is a real edge in the graph
    for i in range(len(path) - 1):
        assert H.has_edge(path[i], path[i + 1]), (
            f"Path step {path[i]} -> {path[i + 1]} is not a graph edge"
        )

    # Convert to GeoJSON LineString (what the frontend would render)
    route_geojson = {
        "type": "Feature",
        "geometry": {
            "type": "LineString",
            "coordinates": [[n[0], n[1]] for n in path],
        },
        "properties": {
            "total_distance_m": round(total_dist_m, 1),
            "waypoints": len(path),
        },
    }

    assert route_geojson["geometry"]["type"] == "LineString"
    assert route_geojson["properties"]["total_distance_m"] > 0

    print(
        f"\n[shortest path] {len(pedestrian_features)} pedestrian segments -> "
        f"{G.number_of_nodes()} graph nodes"
    )
    print(
        f"  Route: {len(path)} waypoints, "
        f"{route_geojson['properties']['total_distance_m']} m"
    )
    print(f"  Start : {start}")
    print(f"  End   : {end}")
