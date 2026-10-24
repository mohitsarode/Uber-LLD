import math
from typing import Any
import h3


class H3SpatialIndexer:
    """Enterprise spatial discretization engine wrapping Uber's H3 hierarchical indexing.

    Discretizes continuous geographical coordinates into discrete hexagonal cells,
    enabling O(1) spatial bucketing, uniform equidistant k-ring neighborhood expansion,
    and hierarchical spatial aggregation.
    """

    @staticmethod
    def latlng_to_cell(lat: float, lng: float, resolution: int = 8) -> str:
        """Discretize continuous GPS latitude/longitude into a unique H3 cell index."""
        return h3.latlng_to_cell(lat, lng, resolution)

    @staticmethod
    def cell_to_latlng(cell: str) -> tuple[float, float]:
        """Compute the centroid (latitude, longitude) of an H3 cell."""
        lat, lng = h3.cell_to_latlng(cell)
        return float(lat), float(lng)

    @staticmethod
    def get_k_ring(cell: str, k: int) -> set[str]:
        """Compute the closed hexagonal disk (all cells within distance <= k hops).

        At k=0: 1 cell (itself)
        At k=1: 7 cells (self + 6 adjacent neighbors)
        At k=2: 19 cells
        Formula for ring size: 1 + 3 * k * (k + 1)
        """
        return set(h3.grid_disk(cell, k))

    @staticmethod
    def get_ring_at_k(cell: str, k: int) -> set[str]:
        """Compute the hollow hexagonal boundary ring at exact distance k.

        Used for ring-by-ring incremental dispatch expansion without re-evaluating
        inner cells that were already searched.
        """
        if k == 0:
            return {cell}
        return set(h3.grid_ring(cell, k))

    @staticmethod
    def get_cell_boundary_geojson(cell: str) -> list[list[float]]:
        """Return the polygon boundary vertices in GeoJSON standard format: [[lng, lat], ...].

        Note: H3 boundary returns (lat, lng); GeoJSON specification (RFC 7946) requires [lng, lat].
        The polygon is closed by appending the first vertex at the end.
        """
        boundary = h3.cell_to_boundary(cell)
        coords = [[float(lng), float(lat)] for lat, lng in boundary]
        if coords and coords[0] != coords[-1]:
            coords.append(coords[0])  # Close the ring for valid GeoJSON
        return coords

    @staticmethod
    def get_cell_geojson_feature(cell: str, properties: dict[str, Any] | None = None) -> dict[str, Any]:
        """Generate a complete GeoJSON Feature for an H3 cell."""
        coords = H3SpatialIndexer.get_cell_boundary_geojson(cell)
        lat, lng = H3SpatialIndexer.cell_to_latlng(cell)
        props = {
            "cell": cell,
            "resolution": h3.get_resolution(cell),
            "centroid_lat": round(lat, 6),
            "centroid_lng": round(lng, 6),
        }
        if properties:
            props.update(properties)

        return {
            "type": "Feature",
            "properties": props,
            "geometry": {
                "type": "Polygon",
                "coordinates": [coords],
            },
        }

    @staticmethod
    def grid_distance(cell1: str, cell2: str) -> int:
        """Compute the grid step distance (number of hexagonal hops) between two cells."""
        return h3.grid_distance(cell1, cell2)

    @staticmethod
    def are_neighbors(cell1: str, cell2: str) -> bool:
        """Check if two H3 cells share an edge (distance == 1)."""
        return h3.are_neighbor_cells(cell1, cell2)

    @staticmethod
    def cell_to_parent(cell: str, parent_res: int) -> str:
        """Get the coarser parent cell for regional aggregation or surge pricing."""
        return h3.cell_to_parent(cell, parent_res)

    @staticmethod
    def cell_to_children(cell: str, child_res: int) -> set[str]:
        """Get the finer child cells contained within this cell."""
        return set(h3.cell_to_children(cell, child_res))

    @staticmethod
    def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Compute great-circle distance between two GPS points in meters using the Haversine formula."""
        r = 6371000.0  # Earth's mean radius in meters
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_phi = math.radians(lat2 - lat1)
        delta_lambda = math.radians(lon2 - lon1)

        a = (
            math.sin(delta_phi / 2.0) ** 2
            + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
        )
        c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
        return r * c

    @staticmethod
    def calculate_bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate the initial compass bearing (azimuth) from point 1 to point 2 in degrees [0, 360)."""
        phi1 = math.radians(lat1)
        phi2 = math.radians(lat2)
        delta_lambda = math.radians(lon2 - lon1)

        y = math.sin(delta_lambda) * math.cos(phi2)
        x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(delta_lambda)
        bearing = math.degrees(math.atan2(y, x))
        return (bearing + 360.0) % 360.0

    @staticmethod
    def bearing_difference(bearing1: float, bearing2: float) -> float:
        """Compute the smallest angular difference between two compass bearings in degrees [0, 180]."""
        diff = abs(bearing1 - bearing2) % 360.0
        return 360.0 - diff if diff > 180.0 else diff
