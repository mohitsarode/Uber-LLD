import asyncio
from datetime import datetime, timezone
import logging
import time
from typing import Any
from sqlalchemy import select, update
from src.core.config import settings
from src.db.models import Trip, TripStatus, User
from src.db.session import AsyncSessionLocal
from src.engine.dispatch_scorer import DispatchScorer, CandidateScore
from src.engine.h3_indexer import H3SpatialIndexer
from src.services.redis_mesh import redis_mesh

logger = logging.getLogger(__name__)


class DispatchService:
    """Enterprise dispatch engine orchestrating hexagonal spatial matching,
    atomic distributed driver locking, and multi-node event routing.
    """

    def __init__(self) -> None:
        self.scorer = DispatchScorer()

    async def request_ride(
        self,
        rider_id: str,
        pickup_lat: float,
        pickup_lng: float,
        dropoff_lat: float,
        dropoff_lng: float,
        fare_amount: float | None = None,
        max_k_ring: int | None = None,
    ) -> dict[str, Any]:
        """Process an on-demand ride request and dispatch to the optimal driver via k-ring expansion."""
        start_time = time.perf_counter()
        k_max = max_k_ring if max_k_ring is not None else settings.H3_DISPATCH_MAX_KRING

        # 1. Spatial discretization to H3 index
        pickup_h3 = H3SpatialIndexer.latlng_to_cell(
            pickup_lat, pickup_lng, resolution=settings.H3_DEFAULT_RESOLUTION
        )

        # 2. Estimate trip distance and fare
        trip_distance_m = H3SpatialIndexer.haversine_distance_m(
            pickup_lat, pickup_lng, dropoff_lat, dropoff_lng
        )
        trip_distance_km = round(trip_distance_m / 1000.0, 2)
        base_fare = fare_amount if fare_amount is not None else max(5.0, round(3.50 + (trip_distance_km * 1.85), 2))

        # 3. Create persistent Trip record in database
        trip_id: str
        async with AsyncSessionLocal() as session:
            trip = Trip(
                rider_id=rider_id,
                pickup_lat=pickup_lat,
                pickup_lng=pickup_lng,
                dropoff_lat=dropoff_lat,
                dropoff_lng=dropoff_lng,
                pickup_h3=pickup_h3,
                fare_amount=base_fare,
                distance_km=trip_distance_km,
                status=TripStatus.REQUESTED,
            )
            session.add(trip)
            await session.commit()
            trip_id = trip.id

        # 4. Incremental k-ring hexagonal expansion
        matched_candidate: CandidateScore | None = None
        searched_cells: list[str] = []
        rings_explored = 0

        for k in range(k_max + 1):
            rings_explored = k
            # Search the hollow ring at radius k
            ring_cells = H3SpatialIndexer.get_ring_at_k(pickup_h3, k)
            searched_cells.extend(list(ring_cells))

            # Fetch candidate IDLE drivers in this ring from Redis mesh
            candidates_raw = await redis_mesh.get_drivers_in_cells(ring_cells)
            if not candidates_raw:
                continue

            # Multi-attribute scoring
            ranked_candidates = self.scorer.rank_candidates(
                candidates_raw, pickup_lat, pickup_lng
            )

            # Attempt atomic distributed reservation on the highest-ranked candidates
            for candidate in ranked_candidates:
                reserved = await redis_mesh.atomic_reserve_driver(
                    driver_id=candidate.driver_id,
                    trip_id=trip_id,
                    cell=candidate.cell,
                    ttl_sec=settings.DISPATCH_OFFER_TTL_SEC,
                )
                if reserved:
                    matched_candidate = candidate
                    break

            if matched_candidate:
                break

        latency_ms = (time.perf_counter() - start_time) * 1000.0

        if matched_candidate:
            # Update trip status to OFFERED with assigned candidate
            async with AsyncSessionLocal() as session:
                await session.execute(
                    update(Trip)
                    .where(Trip.id == trip_id)
                    .values(driver_id=matched_candidate.driver_id, status=TripStatus.OFFERED)
                )
                await session.commit()

            offer_payload = {
                "event": "DISPATCH_OFFER",
                "trip_id": trip_id,
                "rider_id": rider_id,
                "pickup": {"lat": pickup_lat, "lng": pickup_lng, "h3_cell": pickup_h3},
                "dropoff": {"lat": dropoff_lat, "lng": dropoff_lng},
                "fare_amount": base_fare,
                "distance_km": trip_distance_km,
                "ttl_seconds": settings.DISPATCH_OFFER_TTL_SEC,
                "candidate_metrics": matched_candidate.to_dict(),
            }

            # Publish dispatch offer to driver's private channel
            await redis_mesh.publish_driver_offer(matched_candidate.driver_id, offer_payload)

            # Publish update to rider's private channel
            await redis_mesh.publish_rider_update(
                rider_id,
                {
                    "event": "DRIVER_LOCATED",
                    "trip_id": trip_id,
                    "driver_id": matched_candidate.driver_id,
                    "eta_seconds": matched_candidate.eta_seconds,
                    "distance_m": matched_candidate.distance_m,
                    "driver_name": matched_candidate.extra_meta.get("name", "Driver"),
                    "vehicle": matched_candidate.extra_meta.get("vehicle_model", "Car"),
                    "rating": matched_candidate.rating,
                },
            )

            # Broadcast to live monitor dashboard
            await redis_mesh.publish_monitor_event(
                "DISPATCH_MATCH",
                {
                    "trip_id": trip_id,
                    "rider_id": rider_id,
                    "driver_id": matched_candidate.driver_id,
                    "pickup_h3": pickup_h3,
                    "rings_searched": rings_explored,
                    "latency_ms": round(latency_ms, 2),
                    "driver_coords": [matched_candidate.lat, matched_candidate.lng],
                    "pickup_coords": [pickup_lat, pickup_lng],
                },
            )

            return {
                "success": True,
                "status": "OFFERED",
                "trip_id": trip_id,
                "pickup_h3": pickup_h3,
                "rings_searched": rings_explored,
                "latency_ms": round(latency_ms, 2),
                "matched_driver": matched_candidate.to_dict(),
                "searched_cell_count": len(searched_cells),
            }
        else:
            # No available driver found within max k-ring radius
            await redis_mesh.publish_monitor_event(
                "DISPATCH_FAILED",
                {
                    "trip_id": trip_id,
                    "pickup_h3": pickup_h3,
                    "rings_searched": rings_explored,
                    "latency_ms": round(latency_ms, 2),
                },
            )
            return {
                "success": False,
                "status": "NO_DRIVERS_AVAILABLE",
                "trip_id": trip_id,
                "pickup_h3": pickup_h3,
                "rings_searched": rings_explored,
                "latency_ms": round(latency_ms, 2),
                "searched_cell_count": len(searched_cells),
            }

    async def respond_to_offer(
        self,
        driver_id: str,
        trip_id: str,
        accepted: bool,
    ) -> dict[str, Any]:
        """Handle driver's response to an active dispatch offer."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Trip).where(Trip.id == trip_id))
            trip = result.scalars().first()
            if not trip:
                return {"success": False, "error": "Trip not found"}

            driver_meta = await redis_mesh.get_driver_meta(driver_id)
            driver_cell = driver_meta.get("cell", "") if driver_meta else ""

            if accepted:
                # 1. Confirm driver assignment in Redis mesh
                await redis_mesh.confirm_driver_assignment(driver_id, trip_id)

                # 2. Update trip in database
                trip.status = TripStatus.ACCEPTED
                trip.driver_id = driver_id
                trip.accepted_at = datetime.now(timezone.utc)
                await session.commit()

                # 3. Notify rider & driver via Pub/Sub
                payload = {
                    "event": "MATCH_ACCEPTED",
                    "trip_id": trip_id,
                    "driver_id": driver_id,
                    "status": "ACCEPTED",
                }
                await redis_mesh.publish_rider_update(trip.rider_id, payload)
                await redis_mesh.publish_driver_offer(driver_id, payload)
                await redis_mesh.publish_monitor_event("TRIP_ACCEPTED", payload)

                return {"success": True, "status": "ACCEPTED", "trip_id": trip_id}
            else:
                # Rejection or timeout: release driver reservation back to IDLE
                if driver_cell:
                    await redis_mesh.release_driver_reservation(driver_id, driver_cell)

                # Reset trip status in database
                trip.driver_id = None
                trip.status = TripStatus.REQUESTED
                await session.commit()

                # Trigger cascading re-dispatch for the trip in background
                asyncio.create_task(
                    self.request_ride(
                        rider_id=trip.rider_id,
                        pickup_lat=trip.pickup_lat,
                        pickup_lng=trip.pickup_lng,
                        dropoff_lat=trip.dropoff_lat,
                        dropoff_lng=trip.dropoff_lng,
                        fare_amount=trip.fare_amount,
                    )
                )

                return {"success": True, "status": "REJECTED_AND_CASCADED", "trip_id": trip_id}

    async def complete_trip(self, trip_id: str) -> dict[str, Any]:
        """Finalize trip upon rider dropoff."""
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(Trip).where(Trip.id == trip_id))
            trip = result.scalars().first()
            if not trip:
                return {"success": False, "error": "Trip not found"}

            trip.status = TripStatus.COMPLETED
            trip.completed_at = datetime.now(timezone.utc)
            await session.commit()

            if trip.driver_id:
                await redis_mesh.complete_trip_for_driver(trip.driver_id)

            payload = {
                "event": "TRIP_COMPLETED",
                "trip_id": trip_id,
                "driver_id": trip.driver_id,
                "fare_amount": trip.fare_amount,
            }
            await redis_mesh.publish_rider_update(trip.rider_id, payload)
            if trip.driver_id:
                await redis_mesh.publish_driver_offer(trip.driver_id, payload)
            await redis_mesh.publish_monitor_event("TRIP_COMPLETED", payload)

            return {"success": True, "status": "COMPLETED", "trip_id": trip_id}


# Singleton instance
dispatch_service = DispatchService()
