"""osmfeatures - Python SDK for the MapLark OSM Features API."""

from __future__ import annotations

from .async_client import AsyncOSMFeaturesClient
from .chunking import (
    around_to_bbox,
    bbox_area_deg2,
    corridor_bbox,
    merge_features,
    parse_bbox,
    shapely_to_bbox,
    split_bbox_tiles,
    tile_count_for_corridor,
)
from .client import OSMFeaturesClient
from .geometry import point_in_geometry
from .nearest import nearest_within, pairs_within
from .convenience import (
    get_amenities,
    get_barriers,
    get_boundaries,
    get_building_polygons,
    get_buildings,
    get_cafes,
    get_cycleways,
    get_elements_by_name,
    get_green_spaces,
    get_healthcare,
    get_landuse,
    get_parking,
    get_parks,
    get_place,
    get_public_transport_stops,
    get_restaurants,
    get_roads,
    get_schools,
    get_shops,
    get_trees,
    get_water,
)
from .models import (
    BinaryQueryResult,
    CostEstimate,
    OSMFeature,
    OSMFeatureCollection,
    OSMFeaturesAPIError,
    OSMFeaturesAuthError,
    OSMFeaturesError,
    OSMFeaturesForbiddenError,
    OSMFeaturesRateLimitError,
    ResponseMeta,
)
from .output import to_dataframe, to_geodataframe
from .retry import RetryConfig

__all__ = [
    # Clients
    "OSMFeaturesClient",
    "AsyncOSMFeaturesClient",
    # Config
    "RetryConfig",
    # Models
    "OSMFeature",
    "OSMFeatureCollection",
    "BinaryQueryResult",
    "ResponseMeta",
    "CostEstimate",
    # Exceptions
    "OSMFeaturesError",
    "OSMFeaturesAuthError",
    "OSMFeaturesForbiddenError",
    "OSMFeaturesRateLimitError",
    "OSMFeaturesAPIError",
    # Output
    "to_dataframe",
    "to_geodataframe",
    # Chunking utilities
    "split_bbox_tiles",
    "merge_features",
    "parse_bbox",
    "bbox_area_deg2",
    "around_to_bbox",
    "corridor_bbox",
    "tile_count_for_corridor",
    "shapely_to_bbox",
    "nearest_within",
    "pairs_within",
    "point_in_geometry",
    # Convenience helpers - built environment
    "get_buildings",
    "get_building_polygons",
    "get_barriers",
    "get_trees",
    # Convenience helpers - mobility
    "get_roads",
    "get_cycleways",
    "get_public_transport_stops",
    "get_parking",
    # Convenience helpers - POI
    "get_amenities",
    "get_restaurants",
    "get_cafes",
    "get_shops",
    "get_healthcare",
    "get_schools",
    # Convenience helpers - green space
    "get_parks",
    "get_green_spaces",
    "get_water",
    # Convenience helpers - admin / place
    "get_landuse",
    "get_boundaries",
    "get_place",
    "get_elements_by_name",
]

__version__ = "0.3.0"

