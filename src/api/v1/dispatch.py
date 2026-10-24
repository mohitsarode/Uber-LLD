from typing import Any
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from src.services.dispatch_service import dispatch_service

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
