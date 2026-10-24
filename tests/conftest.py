import asyncio
import os
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Force test configuration before importing settings
os.environ["USE_SQLITE"] = "true"
os.environ["USE_FAKEREDIS"] = "true"
os.environ["SQLITE_DB_PATH"] = "./test_hexdispatch.db"

from src.core.config import settings
from src.db.session import init_database, engine
from src.db.init_db import seed_initial_data
from src.db.models import Base
from src.services.redis_mesh import redis_mesh
from src.services.telemetry_buffer import telemetry_buffer
from src.main import app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_test_environment():
    """Initialize database and redis mesh for tests."""
    await init_database()
    await seed_initial_data()
    await redis_mesh.connect()
    telemetry_buffer.start()

    yield

    await telemetry_buffer.stop()
    await redis_mesh.disconnect()

    # Clean up test database
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    if os.path.exists("./test_hexdispatch.db"):
        try:
            os.remove("./test_hexdispatch.db")
        except OSError:
            pass


@pytest_asyncio.fixture
async def async_client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
