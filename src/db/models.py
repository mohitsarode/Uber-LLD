from datetime import datetime, timezone
import enum
import uuid
from sqlalchemy import (
    Column,
    String,
    Float,
    Integer,
    Boolean,
    DateTime,
    ForeignKey,
    Enum,
    Index,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


def generate_uuid() -> str:
    return str(uuid.uuid4())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    DRIVER = "DRIVER"
    RIDER = "RIDER"
    ADMIN = "ADMIN"


class DriverStatus(str, enum.Enum):
    OFFLINE = "OFFLINE"
    IDLE = "IDLE"
    DISPATCH_OFFERED = "DISPATCH_OFFERED"
    ON_TRIP = "ON_TRIP"


class TripStatus(str, enum.Enum):
    REQUESTED = "REQUESTED"
    OFFERED = "OFFERED"
    ACCEPTED = "ACCEPTED"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    name = Column(String(100), nullable=False)
    email = Column(String(120), unique=True, nullable=False)
    phone = Column(String(30), nullable=True)
    role = Column(Enum(UserRole), default=UserRole.RIDER, nullable=False)
    rating = Column(Float, default=4.9, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    driver_profile = relationship("DriverProfile", back_populates="user", uselist=False)
    trips_as_rider = relationship("Trip", foreign_keys="Trip.rider_id", back_populates="rider")
    trips_as_driver = relationship("Trip", foreign_keys="Trip.driver_id", back_populates="driver")


class DriverProfile(Base):
    __tablename__ = "driver_profiles"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    user_id = Column(String(36), ForeignKey("users.id"), nullable=False, unique=True)
    vehicle_model = Column(String(100), default="Toyota Camry")
    license_plate = Column(String(20), default="6XYZ987")
    is_active = Column(Boolean, default=True, nullable=False)
    current_status = Column(Enum(DriverStatus), default=DriverStatus.OFFLINE, nullable=False)
    current_h3_cell = Column(String(20), nullable=True, index=True)
    last_seen_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)

    user = relationship("User", back_populates="driver_profile")


class Trip(Base):
    __tablename__ = "trips"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    rider_id = Column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    driver_id = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    status = Column(Enum(TripStatus), default=TripStatus.REQUESTED, nullable=False, index=True)
    pickup_lat = Column(Float, nullable=False)
    pickup_lng = Column(Float, nullable=False)
    dropoff_lat = Column(Float, nullable=False)
    dropoff_lng = Column(Float, nullable=False)
    pickup_h3 = Column(String(20), nullable=False, index=True)
    fare_amount = Column(Float, default=0.0, nullable=False)
    distance_km = Column(Float, default=0.0, nullable=False)

    created_at = Column(DateTime(timezone=True), default=utc_now, nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)

    rider = relationship("User", foreign_keys=[rider_id], back_populates="trips_as_rider")
    driver = relationship("User", foreign_keys=[driver_id], back_populates="trips_as_driver")


class DriverTelemetry(Base):
    """TimescaleDB Hypertable model for high-frequency GPS telemetry logging.

    In TimescaleDB, the composite primary key must contain the partitioning time column.
    """
    __tablename__ = "driver_telemetry"

    time = Column(DateTime(timezone=True), primary_key=True, default=utc_now, index=True)
    driver_id = Column(String(36), primary_key=True, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    h3_cell = Column(String(20), nullable=False, index=True)
    speed_kmh = Column(Float, default=0.0)
    heading = Column(Float, default=0.0)
    trip_id = Column(String(36), nullable=True, index=True)

    __table_args__ = (
        Index("idx_telemetry_driver_time", "driver_id", "time"),
        Index("idx_telemetry_cell_time", "h3_cell", "time"),
    )
