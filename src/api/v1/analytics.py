from typing import Any
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.config import settings
from src.db.models import DriverProfile, DriverStatus, Trip, User, UserRole
from src.db.session import get_db
from src.services.redis_mesh import redis_mesh
from src.services.telemetry_buffer import telemetry_buffer

router = APIRouter(prefix="/analytics", tags=["Analytics & System Metrics"])


@router.get("/stats", response_model=dict[str, Any])
async def get_system_stats(db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Retrieve operational telemetry metrics, active driver counts, and queue buffer health."""
    buffer_metrics = telemetry_buffer.get_metrics()
    active_cells = await redis_mesh.get_active_cells()
    active_drivers = await redis_mesh.get_all_active_drivers()

    # Query persistent database counts
    driver_count_res = await db.execute(select(func.count(User.id)).where(User.role == UserRole.DRIVER))
    total_drivers = driver_count_res.scalar() or 0

    rider_count_res = await db.execute(select(func.count(User.id)).where(User.role == UserRole.RIDER))
    total_riders = rider_count_res.scalar() or 0

    trip_count_res = await db.execute(select(func.count(Trip.id)))
    total_trips = trip_count_res.scalar() or 0

    return {
        "engine_status": "ONLINE",
        "spatial_indexing": {
            "default_resolution": settings.H3_DEFAULT_RESOLUTION,
            "max_kring_search": settings.H3_DISPATCH_MAX_KRING,
            "active_cells_count": len(active_cells),
        },
        "mesh_state": {
            "online_drivers_count": len(active_drivers),
            "idle_drivers": sum(1 for d in active_drivers if d.get("status") == "IDLE"),
            "busy_drivers": sum(1 for d in active_drivers if d.get("status") in ("DISPATCH_OFFERED", "ON_TRIP")),
        },
        "telemetry_buffer": buffer_metrics,
        "database_registry": {
            "total_registered_drivers": total_drivers,
            "total_registered_riders": total_riders,
            "total_trips_logged": total_trips,
            "storage_backend": "SQLite (Local Fallback)" if settings.USE_SQLITE else "TimescaleDB (PostgreSQL)",
        },
    }


@router.get("/trips", response_model=list[dict[str, Any]])
async def list_recent_trips(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List recent trips with spatial dispatch status and timestamps."""
    stmt = select(Trip).order_by(Trip.created_at.desc()).limit(limit)
    res = await db.execute(stmt)
    trips = res.scalars().all()

    return [
        {
            "id": t.id,
            "rider_id": t.rider_id,
            "driver_id": t.driver_id,
            "status": t.status.value,
            "pickup": {"lat": t.pickup_lat, "lng": t.pickup_lng, "h3_cell": t.pickup_h3},
            "dropoff": {"lat": t.dropoff_lat, "lng": t.dropoff_lng},
            "fare_amount": t.fare_amount,
            "distance_km": t.distance_km,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "completed_at": t.completed_at.isoformat() if t.completed_at else None,
        }
        for t in trips
    ]
