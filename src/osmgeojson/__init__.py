"""osmgeojson - Python SDK for the OSM GeoJSON API (MapLark)."""

from __future__ import annotations

from .async_client import AsyncOSMGeoJSONClient
from .chunking import around_to_bbox, bbox_area_deg2, merge_features, parse_bbox, shapely_to_bbox, split_bbox_tiles
from .client import OSMGeoJSONClient
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
    CostEstimate,
    OSMFeature,
    OSMFeatureCollection,
    OSMGeoJSONAPIError,
    OSMGeoJSONAuthError,
    OSMGeoJSONError,
    OSMGeoJSONForbiddenError,
    OSMGeoJSONRateLimitError,
    ResponseMeta,
)
from .output import to_dataframe, to_geodataframe
from .retry import RetryConfig

__all__ = [
    # Clients
    "OSMGeoJSONClient",
    "AsyncOSMGeoJSONClient",
    # Config
    "RetryConfig",
    # Models
    "OSMFeature",
    "OSMFeatureCollection",
    "ResponseMeta",
    "CostEstimate",
    # Exceptions
    "OSMGeoJSONError",
    "OSMGeoJSONAuthError",
    "OSMGeoJSONForbiddenError",
    "OSMGeoJSONRateLimitError",
    "OSMGeoJSONAPIError",
    # Output
    "to_dataframe",
    "to_geodataframe",
    # Chunking utilities
    "split_bbox_tiles",
    "merge_features",
    "parse_bbox",
    "bbox_area_deg2",
    "around_to_bbox",
    "shapely_to_bbox",
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

__version__ = "0.1.0"
