from dataclasses import dataclass
import math
from typing import Any
from src.core.config import settings
from src.engine.h3_indexer import H3SpatialIndexer


@dataclass(slots=True)
class CandidateScore:
    driver_id: str
    cell: str
    lat: float
    lng: float
    distance_m: float
    eta_seconds: int
    bearing_diff_deg: float
    rating: float
    distance_score: float
    rating_score: float
    bearing_score: float
    final_score: float
    extra_meta: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "driver_id": self.driver_id,
            "name": self.extra_meta.get("name", "Driver"),
            "vehicle_model": self.extra_meta.get("vehicle_model", "Tesla Model 3"),
            "license_plate": self.extra_meta.get("license_plate", "7HEX001"),
            "cell": self.cell,
            "lat": round(self.lat, 6),
            "lng": round(self.lng, 6),
            "distance_m": round(self.distance_m, 1),
            "eta_seconds": self.eta_seconds,
            "bearing_diff_deg": round(self.bearing_diff_deg, 1),
            "rating": round(self.rating, 2),
            "distance_score": round(self.distance_score, 4),
            "rating_score": round(self.rating_score, 4),
            "bearing_score": round(self.bearing_score, 4),
            "final_score": round(self.final_score, 4),
            "extra_meta": self.extra_meta,
        }


class DispatchScorer:
    """Multi-attribute candidate driver ranking engine.

    Scores candidate drivers based on:
    1. Distance decay (exponential decay from pickup location)
    2. ETA estimation (based on dynamic speed or urban transit model)
    3. Bearing alignment (avoids U-turns by rewarding drivers heading towards pickup)
    4. Driver reputation / rating
    """

    def __init__(
        self,
        weight_distance: float | None = None,
        weight_rating: float | None = None,
        weight_bearing: float | None = None,
        average_urban_speed_kmh: float = 30.0,
    ):
        self.w_dist = weight_distance if weight_distance is not None else settings.WEIGHT_DISTANCE
        self.w_rating = weight_rating if weight_rating is not None else settings.WEIGHT_RATING
        self.w_bearing = weight_bearing if weight_bearing is not None else settings.WEIGHT_BEARING

        # Normalize weights to sum to 1.0
        total_weight = self.w_dist + self.w_rating + self.w_bearing
        if total_weight > 0:
            self.w_dist /= total_weight
            self.w_rating /= total_weight
            self.w_bearing /= total_weight

        self.urban_speed_mps = (average_urban_speed_kmh * 1000.0) / 3600.0

    def score_candidate(
        self,
        driver_meta: dict[str, Any],
        pickup_lat: float,
        pickup_lng: float,
    ) -> CandidateScore:
        driver_id = driver_meta.get("driver_id", "")
        driver_lat = float(driver_meta.get("lat", 0.0))
        driver_lng = float(driver_meta.get("lng", 0.0))
        driver_cell = str(driver_meta.get("cell", ""))
        driver_heading = float(driver_meta.get("heading", 0.0))
        driver_speed = float(driver_meta.get("speed", 0.0))
        driver_rating = float(driver_meta.get("rating", 4.8))

        # 1. Haversine distance in meters
        dist_m = H3SpatialIndexer.haversine_distance_m(driver_lat, driver_lng, pickup_lat, pickup_lng)

        # 2. ETA calculation: use actual speed if moving, otherwise standard urban speed
        effective_speed_mps = (driver_speed * 1000.0 / 3600.0) if driver_speed > 10.0 else self.urban_speed_mps
        eta_seconds = max(15, int(dist_m / effective_speed_mps))

        # 3. Bearing alignment: calculate bearing from driver to pickup
        desired_bearing = H3SpatialIndexer.calculate_bearing(driver_lat, driver_lng, pickup_lat, pickup_lng)
        bearing_diff = H3SpatialIndexer.bearing_difference(driver_heading, desired_bearing)

        # 4. Score components:
        # Distance score: Exponential decay (half-life ~ 1500m)
        distance_score = math.exp(-dist_m / 1500.0)

        # Rating score: Normalized between [0, 1] assuming ratings in [3.0, 5.0]
        rating_score = max(0.0, min(1.0, (driver_rating - 3.0) / 2.0))

        # Bearing score: 1.0 if perfectly aligned, 0.0 if facing opposite direction (180 deg)
        bearing_score = 1.0 - (bearing_diff / 180.0)

        # Composite score
        final_score = (
            (self.w_dist * distance_score)
            + (self.w_rating * rating_score)
            + (self.w_bearing * bearing_score)
        )

        return CandidateScore(
            driver_id=driver_id,
            cell=driver_cell,
            lat=driver_lat,
            lng=driver_lng,
            distance_m=dist_m,
            eta_seconds=eta_seconds,
            bearing_diff_deg=bearing_diff,
            rating=driver_rating,
            distance_score=distance_score,
            rating_score=rating_score,
            bearing_score=bearing_score,
            final_score=final_score,
            extra_meta=driver_meta,
        )

    def rank_candidates(
        self,
        candidates: list[dict[str, Any]],
        pickup_lat: float,
        pickup_lng: float,
    ) -> list[CandidateScore]:
        scored = [
            self.score_candidate(c, pickup_lat, pickup_lng)
            for c in candidates
            if c.get("driver_id")
        ]
        # Sort descending by final_score
        scored.sort(key=lambda x: x.final_score, reverse=True)
        return scored
