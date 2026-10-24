from typing import Any
from fastapi import APIRouter, Query
from src.core.config import settings
from src.engine.h3_indexer import H3SpatialIndexer
from src.services.redis_mesh import redis_mesh

router = APIRouter(prefix="/spatial", tags=["Spatial Indexing (H3)"])


@router.get("/cells", response_model=dict[str, Any])
async def get_active_cells_geojson() -> dict[str, Any]:
    """Return GeoJSON FeatureCollection of all active H3 hexagonal cells populated with drivers."""
    active_cells = await redis_mesh.get_active_cells()
    features: list[dict[str, Any]] = []

    for cell in active_cells:
        drivers = await redis_mesh.get_drivers_in_cells([cell])
        feature = H3SpatialIndexer.get_cell_geojson_feature(
            cell,
            properties={
                "driver_count": len(drivers),
                "is_active": len(drivers) > 0,
            },
        )
        features.append(feature)

    return {
        "type": "FeatureCollection",
        "features": features,
    }


@router.get("/kring", response_model=dict[str, Any])
def get_kring_geojson(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lng: float = Query(..., ge=-180.0, le=180.0),
    k: int = Query(default=1, ge=0, le=5),
    resolution: int = Query(default=8, ge=0, le=15),
) -> dict[str, Any]:
    """Compute and return GeoJSON FeatureCollection of an H3 k-ring disk around coordinates."""
    center_cell = H3SpatialIndexer.latlng_to_cell(lat, lng, resolution=resolution)
    disk = H3SpatialIndexer.get_k_ring(center_cell, k)

    features: list[dict[str, Any]] = []
    for cell in disk:
        step_dist = H3SpatialIndexer.grid_distance(center_cell, cell)
        feature = H3SpatialIndexer.get_cell_geojson_feature(
            cell,
            properties={
                "center_cell": center_cell,
                "grid_distance_hops": step_dist,
                "is_center": bool(cell == center_cell),
            },
        )
        features.append(feature)

    return {
        "type": "FeatureCollection",
        "center_cell": center_cell,
        "k": k,
        "total_cells": len(features),
        "features": features,
    }


@router.get("/resolve", response_model=dict[str, Any])
def resolve_coordinate(
    lat: float = Query(..., ge=-90.0, le=90.0),
    lng: float = Query(..., ge=-180.0, le=180.0),
    resolution: int = Query(default=8, ge=0, le=15),
) -> dict[str, Any]:
    """Discretize continuous coordinates into H3 hierarchical indices."""
    cell = H3SpatialIndexer.latlng_to_cell(lat, lng, resolution=resolution)
    centroid_lat, centroid_lng = H3SpatialIndexer.cell_to_latlng(cell)
    parent_cell = H3SpatialIndexer.cell_to_parent(cell, max(0, resolution - 2))

    return {
        "lat": lat,
        "lng": lng,
        "resolution": resolution,
        "h3_cell": cell,
        "centroid": {"lat": centroid_lat, "lng": centroid_lng},
        "regional_parent": parent_cell,
        "boundary_geojson": H3SpatialIndexer.get_cell_boundary_geojson(cell),
    }
