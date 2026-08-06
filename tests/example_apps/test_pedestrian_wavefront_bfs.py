"""
App idea: "accessibility ring map" — colour every street node by how many
segment-steps it is away from a chosen origin.

BFS on the unweighted pedestrian graph assigns each node an exact hop level:
0 for the origin, 1 for direct neighbours, 2 for their not-yet-seen
neighbours, and so on.  Nodes at the same level form one "ring" that can be
rendered in a single colour — a classic transit-accessibility visualisation.

Why BFS and not Dijkstra?
  Dijkstra minimises total edge-weight (metres travelled).  Here we want
  equal-hop contour shells: the question is "how many segments from the
  square?" not "how far in metres?".  BFS on the unweighted graph produces
  exact hop shells in O(V + E) time and requires no edge weights at all.

Area: Gamla Stan, Stockholm's old town.  Stortorget (the central square) is
the BFS origin.  The dense medieval grid produces many distinct hop levels in
a compact footprint, making the wavefront clearly visible.

API calls: multiple ``type=way&shape=line&tags=highway=<type>`` queries per
pedestrian highway type, merged with deduplication.
"""

import time
from collections import deque

import pytest
import networkx as nx
from haversine import Unit, haversine

from osmfeatures import OSMFeatureCollection, OSMFeaturesClient
from tests.example_apps.graph_utils import build_nx_graph

GAMLA_STAN_BBOX = "18.063,59.322,18.082,59.332"

PEDESTRIAN_HIGHWAY_TYPES = (
    "footway", "pedestrian", "steps", "path", "residential", "living_street",
)


def test_pedestrian_wavefront_bfs(client: OSMFeaturesClient):
    features: list = []
    seen_ids: set[str] = set()
    blocked_access_values = {"no", "private"}

    for hw in PEDESTRIAN_HIGHWAY_TYPES:
        data = client.query(
            bbox=GAMLA_STAN_BBOX,
            type="way",
            shape="line",
            tags=f"highway={hw}",
            limit=300,
        )
        assert isinstance(data, OSMFeatureCollection)
        for f in data["features"]:
            tags = f.tags
            if (
                tags.get("foot") in blocked_access_values
                or tags.get("access") in blocked_access_values
            ):
                continue
            if f["id"] not in seen_ids:
                seen_ids.add(f["id"])
                features.append(f)

        # Brief pause between queries to avoid overwhelming the API
        time.sleep(0.3)

    if len(features) < 5:
        pytest.skip("Not enough pedestrian features in Gamla Stan for a wavefront test")

    G = build_nx_graph(features)
    if G.number_of_nodes() < 10:
        pytest.skip("Pedestrian graph too sparse to demonstrate BFS wavefront")

    # Work within the largest connected component
    largest_cc = max(nx.connected_components(G), key=len)
    H = G.subgraph(largest_cc)

    # Snap the BFS origin to the nearest graph node to Stortorget
    STORTORGET_LON, STORTORGET_LAT = 18.0712, 59.3252
    origin = min(
        H.nodes,
        key=lambda n: haversine((n[1], n[0]), (STORTORGET_LAT, STORTORGET_LON), unit=Unit.METERS),
    )
    origin_snap_m = haversine(
        (origin[1], origin[0]), (STORTORGET_LAT, STORTORGET_LON), unit=Unit.METERS
    )
    if origin_snap_m > 200:
        pytest.skip(
            f"Nearest graph node to Stortorget is {origin_snap_m:.0f} m away "
            "(threshold 200 m) - data may not cover the square"
        )

    # -----------------------------------------------------------------------
    # Iterative BFS - level-order traversal from the origin
    # -----------------------------------------------------------------------
    hop_level: dict[tuple, int] = {origin: 0}
    queue: deque = deque([origin])
    levels: dict[int, list] = {0: [origin]}

    while queue:
        node = queue.popleft()
        next_hop = hop_level[node] + 1
        for neighbour in H.neighbors(node):
            if neighbour not in hop_level:
                hop_level[neighbour] = next_hop
                queue.append(neighbour)
                levels.setdefault(next_hop, []).append(neighbour)

    # -----------------------------------------------------------------------
    # Assertions
    # -----------------------------------------------------------------------
    assert len(hop_level) == H.number_of_nodes(), (
        "BFS did not reach all nodes in the largest connected component"
    )

    max_level = max(levels)
    assert max_level >= 3, (
        f"BFS only reached hop level {max_level}; expected at least 3 levels "
        "in Gamla Stan's dense street grid"
    )

    # Every level must be non-empty (no phantom gaps in the wavefront)
    for lvl in range(max_level + 1):
        assert lvl in levels and len(levels[lvl]) > 0, (
            f"BFS level {lvl} is missing or empty - wavefront has a gap"
        )

    assert all(v >= 0 for v in hop_level.values())

    # -----------------------------------------------------------------------
    # GeoJSON output - what a frontend would consume for ring colouring
    # -----------------------------------------------------------------------
    ring_collection = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [n[0], n[1]]},
                "properties": {"hop_level": hop_level[n]},
            }
            for n in H.nodes
        ],
    }
    assert ring_collection["type"] == "FeatureCollection"
    assert len(ring_collection["features"]) == H.number_of_nodes()
    assert all(f["properties"]["hop_level"] >= 0 for f in ring_collection["features"])

    print(
        f"\n[wavefront BFS] Stortorget origin (snap {origin_snap_m:.0f} m), "
        f"{H.number_of_nodes()} nodes across {max_level + 1} hop levels"
    )
    for lvl in sorted(levels):
        bar = "#" * min(len(levels[lvl]), 40)
        print(f"  level {lvl:2d}: {len(levels[lvl]):4d} nodes  {bar}")
