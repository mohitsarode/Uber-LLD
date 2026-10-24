import pytest
from httpx import AsyncClient
from src.services.redis_mesh import redis_mesh


@pytest.mark.asyncio
async def test_spatial_resolve_api(async_client: AsyncClient):
    resp = await async_client.get("/api/v1/spatial/resolve?lat=37.7749&lng=-122.4194&resolution=8")
    assert resp.status_code == 200
    data = resp.json()
    assert "h3_cell" in data
    assert data["resolution"] == 8
    assert len(data["boundary_geojson"]) >= 7


@pytest.mark.asyncio
async def test_spatial_kring_api(async_client: AsyncClient):
    resp = await async_client.get("/api/v1/spatial/kring?lat=37.7749&lng=-122.4194&k=2&resolution=8")
    assert resp.status_code == 200
    data = resp.json()
    assert data["type"] == "FeatureCollection"
    assert data["total_cells"] == 19
    assert len(data["features"]) == 19


@pytest.mark.asyncio
async def test_analytics_stats_api(async_client: AsyncClient):
    resp = await async_client.get("/api/v1/analytics/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["engine_status"] == "ONLINE"
    assert "telemetry_buffer" in data
    assert "spatial_indexing" in data


@pytest.mark.asyncio
async def test_dispatch_request_api(async_client: AsyncClient):
    # Ensure an IDLE driver exists near San Francisco center
    driver_id = "test-dispatch-rider-target"
    await redis_mesh.update_driver_telemetry(
        driver_id=driver_id,
        lat=37.7750,
        lng=-122.4195,
        status="IDLE",
        driver_name="Speedy Driver",
        rating=4.95,
    )

    resp = await async_client.post(
        "/api/v1/dispatch/request",
        json={
            "rider_id": "test-rider-99",
            "pickup_lat": 37.7749,
            "pickup_lng": -122.4194,
            "dropoff_lat": 37.7850,
            "dropoff_lng": -122.4100,
            "max_k_ring": 3,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["status"] == "OFFERED"
    assert "trip_id" in data
    assert "latency_ms" in data
    assert data["matched_driver"]["driver_id"] is not None

    # Test driver responding ACCEPT to offer
    trip_id = data["trip_id"]
    matched_driver_id = data["matched_driver"]["driver_id"]

    resp_accept = await async_client.post(
        "/api/v1/dispatch/respond",
        json={
            "driver_id": matched_driver_id,
            "trip_id": trip_id,
            "accepted": True,
        },
    )
    assert resp_accept.status_code == 200
    accept_data = resp_accept.json()
    assert accept_data["status"] == "ACCEPTED"

    # Complete the trip
    resp_complete = await async_client.post(
        "/api/v1/dispatch/complete",
        json={"trip_id": trip_id},
    )
    assert resp_complete.status_code == 200
    complete_data = resp_complete.json()
    assert complete_data["status"] == "COMPLETED"
