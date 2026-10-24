from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.config import settings
from src.db.models import DriverProfile, User, UserRole
from src.db.session import get_db
from src.engine.h3_indexer import H3SpatialIndexer
from src.services.redis_mesh import redis_mesh
from src.services.telemetry_buffer import telemetry_buffer

router = APIRouter(prefix="/drivers", tags=["Drivers"])


class TelemetryUpdateSchema(BaseModel):
    driver_id: str
    lat: float = Field(..., ge=-90.0, le=90.0)
    lng: float = Field(..., ge=-180.0, le=180.0)
    speed: float = Field(default=0.0, ge=0.0)
    heading: float = Field(default=0.0, ge=0.0, le=360.0)
    status: str = Field(default="IDLE")
    trip_id: str | None = None


@router.get("", response_model=list[dict[str, Any]])
async def list_drivers(
    cell: str | None = Query(default=None, description="Filter by exact H3 cell"),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List drivers with current real-time state from Redis mesh."""
    if cell:
        drivers = await redis_mesh.get_drivers_in_cells([cell])
        return drivers

    # Fetch from Redis mesh active drivers
    active_drivers = await redis_mesh.get_all_active_drivers()
    if active_drivers:
        return active_drivers

    # Fallback to database if Redis has not yet observed active pings
    stmt = select(User, DriverProfile).join(DriverProfile, User.id == DriverProfile.user_id).where(User.role == UserRole.DRIVER)
    res = await db.execute(stmt)
    records = res.all()

    output = []
    for user, profile in records:
        if profile.current_h3_cell:
            d_lat, d_lng = H3SpatialIndexer.cell_to_latlng(profile.current_h3_cell)
        else:
            d_lat, d_lng = settings.SIMULATION_CENTER_LAT, settings.SIMULATION_CENTER_LNG

        output.append({
            "driver_id": user.id,
            "name": user.name,
            "rating": user.rating,
            "vehicle_model": profile.vehicle_model,
            "license_plate": profile.license_plate,
            "status": profile.current_status.value if profile.current_status else "IDLE",
            "cell": profile.current_h3_cell or "",
            "lat": round(d_lat, 6),
            "lng": round(d_lng, 6),
        })
    return output


@router.get("/{driver_id}", response_model=dict[str, Any])
async def get_driver_detail(
    driver_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Retrieve driver profile combined with real-time in-memory telemetry state."""
    # Check in-memory Redis state first
    meta = await redis_mesh.get_driver_meta(driver_id)

    # Check relational DB
    stmt = select(User, DriverProfile).join(DriverProfile, User.id == DriverProfile.user_id).where(User.id == driver_id)
    res = await db.execute(stmt)
    record = res.first()

    if not record and not meta:
        raise HTTPException(status_code=404, detail="Driver not found")

    user, profile = record if record else (None, None)
    return {
        "driver_id": driver_id,
        "name": user.name if user else (meta.get("name") if meta else "Unknown"),
        "email": user.email if user else "",
        "rating": user.rating if user else (meta.get("rating") if meta else 4.9),
        "vehicle_model": profile.vehicle_model if profile else (meta.get("vehicle_model") if meta else "Car"),
        "license_plate": profile.license_plate if profile else "",
        "live_state": meta or {"status": "OFFLINE", "cell": profile.current_h3_cell if profile else ""},
    }


@router.post("/telemetry", status_code=200)
async def ingest_rest_telemetry(payload: TelemetryUpdateSchema) -> dict[str, Any]:
    """REST telemetry ingestion fallback for HTTP clients."""
    cell = await redis_mesh.update_driver_telemetry(
        driver_id=payload.driver_id,
        lat=payload.lat,
        lng=payload.lng,
        speed=payload.speed,
        heading=payload.heading,
        status=payload.status,
    )
    import time
    await telemetry_buffer.push({
        "time": time.time(),
        "driver_id": payload.driver_id,
        "latitude": payload.lat,
        "longitude": payload.lng,
        "h3_cell": cell,
        "speed_kmh": payload.speed,
        "heading": payload.heading,
        "trip_id": payload.trip_id,
    })
    return {"status": "ACK", "driver_id": payload.driver_id, "h3_cell": cell}
