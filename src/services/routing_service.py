import asyncio
import logging
import math
from typing import Any
import httpx

logger = logging.getLogger(__name__)

OSRM_BASE_URL = "https://router.project-osrm.org/route/v1/driving"


class RoutingService:
    """Real road network routing engine interfacing OpenStreetMap / OSRM.

    Provides true street road distances, turn-by-turn geometry waypoints,
    and ETA calculations based on real road situations.
    """

    @staticmethod
    async def get_road_route(
        start_lat: float,
        start_lng: float,
        end_lat: float,
        end_lng: float,
    ) -> dict[str, Any]:
        """Fetch actual road route following street network.

        Falls back to street-grid waypoints if the routing service is unreachable.
        """
        url = f"{OSRM_BASE_URL}/{start_lng:.6f},{start_lat:.6f};{end_lng:.6f},{end_lat:.6f}?overview=full&geometries=geojson"

        try:
            async with httpx.AsyncClient(timeout=3.5) as client:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    routes = data.get("routes", [])
                    if routes:
                        route = routes[0]
                        coords = route.get("geometry", {}).get("coordinates", [])
                        # OSRM returns [lng, lat]; convert to [lat, lng] for Leaflet
                        lat_lng_path = [[round(pt[1], 6), round(pt[0], 6)] for pt in coords]
                        return {
                            "source": "OSRM (OpenStreetMap Road Network)",
                            "distance_m": round(route.get("distance", 0.0), 1),
                            "duration_sec": int(route.get("duration", 0)),
                            "waypoints": lat_lng_path,
                        }
        except Exception as e:
            logger.warning(f"OSRM road routing unavailable ({e}). Falling back to street grid interpolation.")

        # Resilient fallback: Manhattan street grid interpolation
        return RoutingService._generate_fallback_street_grid(start_lat, start_lng, end_lat, end_lng)

    @staticmethod
    def _generate_fallback_street_grid(
        start_lat: float, start_lng: float, end_lat: float, end_lng: float
    ) -> dict[str, Any]:
        """Generate street-grid waypoints with Manhattan-style orthogonal street turns."""
        R = 6371000.0
        dlat = math.radians(end_lat - start_lat)
        dlon = math.radians(end_lng - start_lng)
        a = (
            math.sin(dlat / 2.0) ** 2
            + math.cos(math.radians(start_lat))
            * math.cos(math.radians(end_lat))
            * math.sin(dlon / 2.0) ** 2
        )
        hav_dist = R * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        # Urban driving factor ~1.28x Haversine distance
        est_road_dist = hav_dist * 1.28

        mid_lat = start_lat + (end_lat - start_lat) * 0.55
        mid_lng = start_lng + (end_lng - start_lng) * 0.45

        corner1 = [start_lat, mid_lng]
        corner2 = [mid_lat, end_lng]
        anchors = [[start_lat, start_lng], corner1, corner2, [end_lat, end_lng]]

        path = []
        for i in range(len(anchors) - 1):
            pA = anchors[i]
            pB = anchors[i + 1]
            steps = 25
            for s in range(steps):
                t = s / steps
                path.append([
                    round(pA[0] + (pB[0] - pA[0]) * t, 6),
                    round(pA[1] + (pB[1] - pA[1]) * t, 6),
                ])
        path.append([round(end_lat, 6), round(end_lng, 6)])

        return {
            "source": "Street Grid Fallback",
            "distance_m": round(est_road_dist, 1),
            "duration_sec": max(15, int(est_road_dist / 8.33)),  # ~30 km/h
            "waypoints": path,
        }


routing_service = RoutingService()
