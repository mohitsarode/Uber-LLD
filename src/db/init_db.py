import logging
from sqlalchemy import select
from src.core.config import settings
from src.db.models import User, UserRole, DriverProfile, DriverStatus
from src.db.session import AsyncSessionLocal, init_database
from src.engine.h3_indexer import H3SpatialIndexer

logger = logging.getLogger(__name__)

# Sample coordinates around San Francisco
SEED_DRIVERS = [
    {"name": "Marcus Vance", "email": "marcus.v@uberhex.io", "lat": 37.7749, "lng": -122.4194, "vehicle": "Tesla Model 3", "plate": "7HEX001", "rating": 4.95},
    {"name": "Elena Rostova", "email": "elena.r@uberhex.io", "lat": 37.7794, "lng": -122.4180, "vehicle": "Toyota Prius", "plate": "7HEX002", "rating": 4.88},
    {"name": "Carlos Gomez", "email": "carlos.g@uberhex.io", "lat": 37.7712, "lng": -122.4130, "vehicle": "Honda Accord", "plate": "7HEX003", "rating": 4.92},
    {"name": "Aisha Patel", "email": "aisha.p@uberhex.io", "lat": 37.7830, "lng": -122.4060, "vehicle": "Hyundai Ioniq 5", "plate": "7HEX004", "rating": 4.98},
    {"name": "Dmitri Volkov", "email": "dmitri.v@uberhex.io", "lat": 37.7885, "lng": -122.4010, "vehicle": "BMW i4", "plate": "7HEX005", "rating": 4.85},
    {"name": "Sarah Jenkins", "email": "sarah.j@uberhex.io", "lat": 37.7650, "lng": -122.4210, "vehicle": "Ford Mustang Mach-E", "plate": "7HEX006", "rating": 4.91},
    {"name": "Kenji Sato", "email": "kenji.s@uberhex.io", "lat": 37.7680, "lng": -122.4350, "vehicle": "Toyota Camry Hybrid", "plate": "7HEX007", "rating": 4.89},
    {"name": "Fatima Al-Mansoor", "email": "fatima.a@uberhex.io", "lat": 37.7910, "lng": -122.4150, "vehicle": "Chevrolet Bolt", "plate": "7HEX008", "rating": 4.94},
    {"name": "Liam O'Connor", "email": "liam.o@uberhex.io", "lat": 37.7800, "lng": -122.4290, "vehicle": "Kia EV6", "plate": "7HEX009", "rating": 4.86},
    {"name": "Zoe Chen", "email": "zoe.c@uberhex.io", "lat": 37.7725, "lng": -122.3980, "vehicle": "Polestar 2", "plate": "7HEX010", "rating": 4.97},
]

SEED_RIDERS = [
    {"name": "Alex Mercer", "email": "alex.m@client.io", "phone": "+1-555-0101"},
    {"name": "Chloe Bennett", "email": "chloe.b@client.io", "phone": "+1-555-0102"},
    {"name": "Jordan Lee", "email": "jordan.l@client.io", "phone": "+1-555-0103"},
]


async def seed_initial_data() -> None:
    """Populate database with realistic initial drivers and riders if empty."""
    await init_database()

    async with AsyncSessionLocal() as session:
        # Check if users already exist
        result = await session.execute(select(User).limit(1))
        existing_user = result.scalars().first()
        if existing_user:
            logger.info("Database already seeded. Skipping initial seeding.")
            return

        logger.info("Seeding initial drivers and riders...")

        # Insert Riders
        for r_data in SEED_RIDERS:
            rider = User(
                name=r_data["name"],
                email=r_data["email"],
                phone=r_data["phone"],
                role=UserRole.RIDER,
                rating=5.0,
            )
            session.add(rider)

        # Insert Drivers
        for d_data in SEED_DRIVERS:
            driver_user = User(
                name=d_data["name"],
                email=d_data["email"],
                phone="+1-555-" + d_data["plate"][-4:],
                role=UserRole.DRIVER,
                rating=d_data["rating"],
            )
            session.add(driver_user)
            await session.flush()  # get driver_user.id

            cell = H3SpatialIndexer.latlng_to_cell(
                d_data["lat"], d_data["lng"], resolution=settings.H3_DEFAULT_RESOLUTION
            )

            profile = DriverProfile(
                user_id=driver_user.id,
                vehicle_model=d_data["vehicle"],
                license_plate=d_data["plate"],
                is_active=True,
                current_status=DriverStatus.IDLE,
                current_h3_cell=cell,
            )
            session.add(profile)

        await session.commit()
        logger.info(f"Successfully seeded {len(SEED_DRIVERS)} drivers and {len(SEED_RIDERS)} riders.")


if __name__ == "__main__":
    import asyncio
    logging.basicConfig(level=logging.INFO)
    asyncio.run(seed_initial_data())
