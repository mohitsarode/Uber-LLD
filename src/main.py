from contextlib import asynccontextmanager
import logging
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from src.api.v1.analytics import router as analytics_router
from src.api.v1.dispatch import router as dispatch_router
from src.api.v1.drivers import router as drivers_router
from src.api.v1.spatial import router as spatial_router
from src.api.websockets.driver_stream import router as driver_ws_router
from src.api.websockets.monitor_stream import router as monitor_ws_router
from src.api.websockets.rider_stream import router as rider_ws_router
from src.core.config import settings
from src.db.init_db import seed_initial_data
from src.db.models import DriverProfile, User, UserRole
from src.db.session import AsyncSessionLocal, init_database
from src.services.redis_mesh import redis_mesh
from src.services.telemetry_buffer import telemetry_buffer

# Setup structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("hexdispatch")


async def sync_seeded_drivers_to_mesh() -> None:
    """Pre-populate Redis mesh with seeded drivers from database on startup."""
    try:
        async with AsyncSessionLocal() as session:
            stmt = (
                select(User, DriverProfile)
                .join(DriverProfile, User.id == DriverProfile.user_id)
                .where(User.role == UserRole.DRIVER)
            )
            res = await session.execute(stmt)
            records = res.all()

            for user, profile in records:
                # Approximate default coordinates for SF demo if cell is known
                from src.engine.h3_indexer import H3SpatialIndexer
                cell = profile.current_h3_cell
                if cell:
                    lat, lng = H3SpatialIndexer.cell_to_latlng(cell)
                else:
                    lat, lng = settings.SIMULATION_CENTER_LAT, settings.SIMULATION_CENTER_LNG

                await redis_mesh.update_driver_telemetry(
                    driver_id=user.id,
                    lat=lat,
                    lng=lng,
                    status="IDLE",
                    driver_name=user.name,
                    rating=user.rating,
                    vehicle_model=profile.vehicle_model,
                    license_plate=profile.license_plate,
                )
        logger.info("Synchronized database drivers into Redis spatial mesh.")
    except Exception as e:
        logger.warning(f"Failed to pre-populate drivers into Redis mesh: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager handling startup initialization and graceful shutdown."""
    logger.info("Starting up H3 Spatial Indexing & Dispatch Engine...")

    # 1. Initialize DB and Seed Data
    await init_database()
    await seed_initial_data()

    # 2. Connect to Redis mesh
    await redis_mesh.connect()

    # 3. Pre-load drivers to spatial mesh
    await sync_seeded_drivers_to_mesh()

    # 4. Start high-frequency telemetry buffer worker
    telemetry_buffer.start()

    logger.info("Engine initialized and ready for WebSocket and HTTP traffic.")
    yield

    # Graceful Shutdown
    logger.info("Shutting down engine...")
    await telemetry_buffer.stop()
    await redis_mesh.disconnect()
    logger.info("Engine shutdown complete.")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="High-frequency spatial indexing and dispatch engine discretizing physical space via Uber's H3.",
    lifespan=lifespan,
)

# Enable CORS for frontend and API consumers
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount REST API Routers
app.include_router(dispatch_router, prefix=settings.API_V1_PREFIX)
app.include_router(drivers_router, prefix=settings.API_V1_PREFIX)
app.include_router(spatial_router, prefix=settings.API_V1_PREFIX)
app.include_router(analytics_router, prefix=settings.API_V1_PREFIX)

# Mount WebSocket Routers
app.include_router(driver_ws_router)
app.include_router(rider_ws_router)
app.include_router(monitor_ws_router)

# Serve Web Dashboard Static Files
static_dir = Path(__file__).resolve().parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.get("/", include_in_schema=False)
async def serve_dashboard():
    """Serve the interactive real-time visual operations dashboard."""
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return {"message": "H3 Spatial Indexing Engine API is running. Visit /docs for Swagger UI."}
