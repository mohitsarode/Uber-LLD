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


@pytest.mark.asyncio
async def test_road_route_api(async_client: AsyncClient):
    resp = await async_client.get(
        "/api/v1/dispatch/road-route?start_lat=37.7895&start_lng=-122.4010&end_lat=37.7955&end_lng=-122.3937"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "waypoints" in data
    assert len(data["waypoints"]) > 0
    assert "distance_m" in data
    assert data["distance_m"] > 0
    assert "duration_sec" in data
    assert "source" in data


@pytest.mark.asyncio
async def test_dispatch_audit_persistence_api(async_client: AsyncClient):
    # Ensure IDLE driver exists
    driver_id = "test-audit-driver"
    await redis_mesh.update_driver_telemetry(
        driver_id=driver_id,
        lat=37.7890,
        lng=-122.4005,
        status="IDLE",
        driver_name="Audit Candidate",
        rating=4.92,
        vehicle_model="Audi e-tron",
        license_plate="7HEX099",
    )

    req_resp = await async_client.post(
        "/api/v1/dispatch/request",
        json={
            "rider_id": "test-rider-audit",
            "pickup_lat": 37.7895,
            "pickup_lng": -122.4010,
            "dropoff_lat": 37.7955,
            "dropoff_lng": -122.3937,
            "max_k_ring": 3,
        },
    )
    assert req_resp.status_code == 200
    trip_data = req_resp.json()
    trip_id = trip_data["trip_id"]

    # Verify audit retrieval endpoint
    audit_resp = await async_client.get(f"/api/v1/dispatch/audit/{trip_id}")
    assert audit_resp.status_code == 200
    audit = audit_resp.json()
    assert audit["trip_id"] == trip_id
    assert audit["selected_driver_id"] is not None
    assert audit["selected_driver_name"] is not None
    assert len(audit["candidates_matrix"]) > 0
    assert audit["mathematical_rationale"] is not None

    # Verify recent audits list
    list_resp = await async_client.get("/api/v1/dispatch/audits?limit=5")
    assert list_resp.status_code == 200
    audits_list = list_resp.json()
    assert isinstance(audits_list, list)
    assert any(a["trip_id"] == trip_id for a in audits_list)


@pytest.mark.asyncio
async def test_drivers_list_and_dashboard_html_api(async_client: AsyncClient):
    # Test drivers endpoint
    drivers_resp = await async_client.get("/api/v1/drivers")
    assert drivers_resp.status_code == 200
    drivers = drivers_resp.json()
    assert isinstance(drivers, list)

    # Test root dashboard serves HTML
    root_resp = await async_client.get("/")
    assert root_resp.status_code == 200
    assert "text/html" in root_resp.headers["content-type"]
    assert "OpenFreeMap" in root_resp.text
    assert "Mathematical Dispatch Decision Audit" in root_resp.text

