from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Server Info
    PROJECT_NAME: str = "H3 Spatial Indexing & Dispatch Engine"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api/v1"
    DEBUG: bool = False
    ENVIRONMENT: Literal["development", "production", "test"] = "development"
    HOST: str = "0.0.0.0"
    PORT: int = 8000

    # H3 Spatial Indexing
    # Resolution 8 has ~460m average edge length and ~0.73 km² area (ideal for dispatch)
    H3_DEFAULT_RESOLUTION: int = Field(default=8, ge=0, le=15)
    # Resolution 6 for macro-level surge calculation (~3.2 km edge)
    H3_REGIONAL_RESOLUTION: int = Field(default=6, ge=0, le=15)
    # Max k-ring search radius for driver dispatch lookup
    H3_DISPATCH_MAX_KRING: int = Field(default=3, ge=1, le=10)

    # In-Memory Mesh (Redis)
    REDIS_URL: str = "redis://localhost:6379/0"
    USE_FAKEREDIS: bool = False
    REDIS_POOL_SIZE: int = 20

    # Database (PostgreSQL / TimescaleDB or SQLite)
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/hexdispatch"
    USE_SQLITE: bool = True
    SQLITE_DB_PATH: str = "./hexdispatch.db"

    # High-Throughput Telemetry Ingestion Buffer
    TELEMETRY_BUFFER_BATCH_SIZE: int = 50
    TELEMETRY_BUFFER_FLUSH_INTERVAL_SEC: float = 1.0

    # Dispatch Engine Parameters
    DRIVER_HEARTBEAT_TIMEOUT_SEC: int = 30
    DISPATCH_OFFER_TTL_SEC: int = 12
    DISPATCH_SEARCH_TIMEOUT_SEC: float = 5.0

    # Candidate Ranking Weights (Normalized in engine)
    WEIGHT_DISTANCE: float = 0.60
    WEIGHT_RATING: float = 0.25
    WEIGHT_BEARING: float = 0.15

    # Simulated City Center (San Francisco Financial District by default)
    SIMULATION_CENTER_LAT: float = 37.774929
    SIMULATION_CENTER_LNG: float = -122.419416


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
