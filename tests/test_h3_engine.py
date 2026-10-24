import math
from src.engine.h3_indexer import H3SpatialIndexer


def test_latlng_to_cell_discretization():
    lat, lng = 37.7749, -122.4194
    cell_res8 = H3SpatialIndexer.latlng_to_cell(lat, lng, resolution=8)
    cell_res9 = H3SpatialIndexer.latlng_to_cell(lat, lng, resolution=9)

    assert isinstance(cell_res8, str)
    assert len(cell_res8) == 15
    assert cell_res8 != cell_res9


def test_cell_to_latlng_centroid():
    lat, lng = 37.7749, -122.4194
    cell = H3SpatialIndexer.latlng_to_cell(lat, lng, resolution=8)
    c_lat, c_lng = H3SpatialIndexer.cell_to_latlng(cell)

    # Centroid must be within ~500m of original coordinate
    dist = H3SpatialIndexer.haversine_distance_m(lat, lng, c_lat, c_lng)
    assert dist < 600.0


def test_kring_expansion_disk_sizes():
    lat, lng = 37.7749, -122.4194
    cell = H3SpatialIndexer.latlng_to_cell(lat, lng, resolution=8)

    # Disk formulas: 1 + 3 * k * (k + 1)
    disk_k0 = H3SpatialIndexer.get_k_ring(cell, k=0)
    assert len(disk_k0) == 1
    assert cell in disk_k0

    disk_k1 = H3SpatialIndexer.get_k_ring(cell, k=1)
    assert len(disk_k1) == 7
    assert cell in disk_k1

    disk_k2 = H3SpatialIndexer.get_k_ring(cell, k=2)
    assert len(disk_k2) == 19


def test_incremental_ring_at_k():
    lat, lng = 37.7749, -122.4194
    cell = H3SpatialIndexer.latlng_to_cell(lat, lng, resolution=8)

    ring0 = H3SpatialIndexer.get_ring_at_k(cell, k=0)
    ring1 = H3SpatialIndexer.get_ring_at_k(cell, k=1)
    ring2 = H3SpatialIndexer.get_ring_at_k(cell, k=2)

    assert len(ring0) == 1
    assert len(ring1) == 6  # 6 immediate hexagonal neighbors
    assert len(ring2) == 12  # 12 cells on perimeter of radius 2

    # Verify rings are mutually exclusive
    assert ring0.isdisjoint(ring1)
    assert ring1.isdisjoint(ring2)


def test_geojson_boundary_compliance():
    lat, lng = 37.7749, -122.4194
    cell = H3SpatialIndexer.latlng_to_cell(lat, lng, resolution=8)
    coords = H3SpatialIndexer.get_cell_boundary_geojson(cell)

    # A hexagon closed polygon has 7 coordinate pairs (first == last)
    assert len(coords) >= 7
    assert coords[0] == coords[-1]  # Closed ring RFC 7946

    # Verify GeoJSON order: [longitude, latitude]
    for pt in coords:
        assert len(pt) == 2
        lng_val, lat_val = pt
        assert -180.0 <= lng_val <= 180.0
        assert -90.0 <= lat_val <= 90.0


def test_haversine_distance():
    # San Francisco to Oakland (~13 km)
    sf_lat, sf_lng = 37.7749, -122.4194
    oak_lat, oak_lng = 37.8044, -122.2712

    dist = H3SpatialIndexer.haversine_distance_m(sf_lat, sf_lng, oak_lat, oak_lng)
    assert 12000 < dist < 15000  # Between 12km and 15km


def test_bearing_calculation():
    # Due North: lat increases, lng stays same -> bearing 0 deg
    b_north = H3SpatialIndexer.calculate_bearing(37.0, -122.0, 38.0, -122.0)
    assert math.isclose(b_north, 0.0, abs_tol=1.0)

    # Due East: lat stays same, lng increases -> bearing 90 deg
    b_east = H3SpatialIndexer.calculate_bearing(37.0, -122.0, 37.0, -121.0)
    assert math.isclose(b_east, 90.0, abs_tol=1.0)

    # Bearing difference
    diff = H3SpatialIndexer.bearing_difference(10.0, 350.0)
    assert diff == 20.0
