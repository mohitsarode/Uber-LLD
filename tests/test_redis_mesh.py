import pytest
from src.services.redis_mesh import redis_mesh


@pytest.mark.asyncio
async def test_update_driver_telemetry_and_metadata():
    driver_id = "test-driver-001"
    lat, lng = 37.7749, -122.4194

    cell = await redis_mesh.update_driver_telemetry(
        driver_id=driver_id,
        lat=lat,
        lng=lng,
        speed=35.0,
        heading=120.0,
        status="IDLE",
        driver_name="Test Driver",
        rating=4.95,
    )
    assert cell.startswith("8828")

    # Verify metadata retrieval
    meta = await redis_mesh.get_driver_meta(driver_id)
    assert meta is not None
    assert meta["driver_id"] == driver_id
    assert meta["status"] == "IDLE"
    assert meta["cell"] == cell
    assert meta["speed"] == 35.0
    assert meta["rating"] == 4.95


@pytest.mark.asyncio
async def test_dynamic_cell_transfer():
    driver_id = "test-driver-moving"
    # SF City Hall
    cell1 = await redis_mesh.update_driver_telemetry(
        driver_id=driver_id, lat=37.7794, lng=-122.4180, status="IDLE"
    )
    # Move to SF Embarcadero (different H3 cell)
    cell2 = await redis_mesh.update_driver_telemetry(
        driver_id=driver_id, lat=37.7955, lng=-122.3937, status="IDLE"
    )
    assert cell1 != cell2

    # Driver should no longer be in cell1 set
    c1_drivers = await redis_mesh.get_drivers_in_cells([cell1])
    assert not any(d["driver_id"] == driver_id for d in c1_drivers)

    # Driver should be present in cell2 set
    c2_drivers = await redis_mesh.get_drivers_in_cells([cell2])
    assert any(d["driver_id"] == driver_id for d in c2_drivers)


@pytest.mark.asyncio
async def test_atomic_lua_reservation_race_condition():
    """Verify that only ONE concurrent reservation succeeds on an IDLE driver."""
    driver_id = "test-driver-atomic"
    cell = await redis_mesh.update_driver_telemetry(
        driver_id=driver_id, lat=37.7749, lng=-122.4194, status="IDLE"
    )

    # First reservation attempt: should succeed
    res1 = await redis_mesh.atomic_reserve_driver(
        driver_id=driver_id, trip_id="trip-101", cell=cell, ttl_sec=10
    )
    assert res1 is True

    # Second concurrent reservation attempt for same driver: must fail!
    res2 = await redis_mesh.atomic_reserve_driver(
        driver_id=driver_id, trip_id="trip-102", cell=cell, ttl_sec=10
    )
    assert res2 is False

    # Driver status must be DISPATCH_OFFERED
    meta = await redis_mesh.get_driver_meta(driver_id)
    assert meta["status"] == "DISPATCH_OFFERED"

    # Driver should have been removed from cell available set
    drivers = await redis_mesh.get_drivers_in_cells([cell])
    assert not any(d["driver_id"] == driver_id for d in drivers)

    # Test release back to IDLE
    await redis_mesh.release_driver_reservation(driver_id=driver_id, cell=cell)
    meta_after = await redis_mesh.get_driver_meta(driver_id)
    assert meta_after["status"] == "IDLE"

    # Driver is available again
    res3 = await redis_mesh.atomic_reserve_driver(
        driver_id=driver_id, trip_id="trip-103", cell=cell, ttl_sec=10
    )
    assert res3 is True
