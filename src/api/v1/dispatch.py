from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.db.models import DispatchAudit
from src.db.session import get_db
from src.services.dispatch_service import dispatch_service
from src.services.routing_service import routing_service

router = APIRouter(prefix="/dispatch", tags=["Dispatch Engine"])


class RideRequestSchema(BaseModel):
    rider_id: str = Field(..., description="Unique ID of requesting rider")
    pickup_lat: float = Field(..., ge=-90.0, le=90.0, description="Pickup latitude")
    pickup_lng: float = Field(..., ge=-180.0, le=180.0, description="Pickup longitude")
    dropoff_lat: float = Field(..., ge=-90.0, le=90.0, description="Dropoff latitude")
    dropoff_lng: float = Field(..., ge=-180.0, le=180.0, description="Dropoff longitude")
    fare_amount: float | None = Field(default=None, description="Optional predetermined fare")
    max_k_ring: int | None = Field(default=None, ge=0, le=10, description="Max hexagonal ring search depth")


class OfferResponseSchema(BaseModel):
    driver_id: str = Field(..., description="ID of responding driver")
    trip_id: str = Field(..., description="ID of trip being offered")
    accepted: bool = Field(..., description="True if driver accepts, False if rejected")


class TripCompletionSchema(BaseModel):
    trip_id: str = Field(..., description="ID of trip to complete")


@router.post("/request", status_code=status.HTTP_200_OK)
async def request_dispatch(payload: RideRequestSchema) -> dict[str, Any]:
    """Trigger H3 k-ring spatial dispatch to find and atomically reserve the best driver."""
    result = await dispatch_service.request_ride(
        rider_id=payload.rider_id,
        pickup_lat=payload.pickup_lat,
        pickup_lng=payload.pickup_lng,
        dropoff_lat=payload.dropoff_lat,
        dropoff_lng=payload.dropoff_lng,
        fare_amount=payload.fare_amount,
        max_k_ring=payload.max_k_ring,
    )
    return result


@router.post("/respond", status_code=status.HTTP_200_OK)
async def respond_to_offer(payload: OfferResponseSchema) -> dict[str, Any]:
    """Process a driver's acceptance or rejection of a dispatch offer."""
    result = await dispatch_service.respond_to_offer(
        driver_id=payload.driver_id,
        trip_id=payload.trip_id,
        accepted=payload.accepted,
    )
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to process response"))
    return result


@router.post("/complete", status_code=status.HTTP_200_OK)
async def complete_trip(payload: TripCompletionSchema) -> dict[str, Any]:
    """Complete an active trip upon destination arrival."""
    result = await dispatch_service.complete_trip(payload.trip_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Failed to complete trip"))
    return result


@router.get("/audit/{trip_id}", response_model=dict[str, Any])
async def get_dispatch_audit(
    trip_id: str,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Retrieve full mathematical evaluation matrix and audit rationale for a dispatch decision."""
    res = await db.execute(select(DispatchAudit).where(DispatchAudit.trip_id == trip_id))
    audit = res.scalars().first()
    if not audit:
        raise HTTPException(status_code=404, detail="Dispatch audit record not found")

    return {
        "trip_id": audit.trip_id,
        "rider_id": audit.rider_id,
        "selected_driver_id": audit.selected_driver_id,
        "selected_driver_name": audit.selected_driver_name,
        "selected_driver_distance_m": audit.selected_driver_distance_m,
        "selected_driver_eta_seconds": audit.selected_driver_eta_seconds,
        "selected_score": audit.selected_score,
        "total_candidates_evaluated": audit.total_candidates_evaluated,
        "mathematical_rationale": audit.mathematical_rationale,
        "candidates_matrix": audit.candidates_matrix,
        "created_at": audit.created_at.isoformat() if audit.created_at else None,
    }


@router.get("/audits", response_model=list[dict[str, Any]])
async def list_recent_audits(
    limit: int = Query(default=10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """List recent dispatch decision audits persisted in the database."""
    res = await db.execute(
        select(DispatchAudit).order_by(DispatchAudit.created_at.desc()).limit(limit)
    )
    records = res.scalars().all()
    return [
        {
            "trip_id": a.trip_id,
            "rider_id": a.rider_id,
            "selected_driver_id": a.selected_driver_id,
            "selected_driver_name": a.selected_driver_name,
            "selected_driver_distance_m": a.selected_driver_distance_m,
            "selected_score": a.selected_score,
            "total_candidates_evaluated": a.total_candidates_evaluated,
            "mathematical_rationale": a.mathematical_rationale,
            "candidates_matrix": a.candidates_matrix,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in records
    ]


@router.get("/road-route", response_model=dict[str, Any])
async def get_road_route(
    start_lat: float = Query(...),
    start_lng: float = Query(...),
    end_lat: float = Query(...),
    end_lng: float = Query(...),
) -> dict[str, Any]:
    """Calculate actual street road routing following OpenStreetMap street network."""
    return await routing_service.get_road_route(start_lat, start_lng, end_lat, end_lng)
