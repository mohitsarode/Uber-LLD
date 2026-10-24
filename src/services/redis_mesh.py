import json
import logging
import time
from typing import Any, AsyncGenerator
import redis.asyncio as aioredis
from src.core.config import settings
from src.engine.h3_indexer import H3SpatialIndexer

logger = logging.getLogger(__name__)

# Atomic Redis Lua script for two-phase dispatch reservation
# Prevents race conditions / double-dispatch under high concurrency
LUA_ATOMIC_RESERVE_DRIVER = """
local meta_key = KEYS[1]
local lease_key = KEYS[2]
local cell_key = KEYS[3]

local trip_id = ARGV[1]
local offer_ttl = tonumber(ARGV[2])
local driver_id = ARGV[3]

local current_status = redis.call('HGET', meta_key, 'status')

if current_status == 'IDLE' then
    redis.call('HSET', meta_key, 'status', 'DISPATCH_OFFERED', 'trip_id', trip_id)
    redis.call('SET', lease_key, trip_id, 'EX', offer_ttl)
    redis.call('SREM', cell_key, driver_id)
    return 1
else
    return 0
end
"""

# Atomic release script for driver if offer is rejected or timed out
LUA_RELEASE_DRIVER_RESERVATION = """
local meta_key = KEYS[1]
local lease_key = KEYS[2]
local cell_key = KEYS[3]
local driver_id = ARGV[1]

redis.call('DEL', lease_key)
redis.call('HSET', meta_key, 'status', 'IDLE', 'trip_id', '')
redis.call('SADD', cell_key, driver_id)
return 1
"""


class RedisSpatialMesh:
    """Distributed in-memory spatial mesh layer backed by Redis.

    Manages:
    - O(1) Driver-to-H3-cell mapping and spatial index sets
    - Driver real-time telemetry hashes and heartbeat TTLs
    - Atomic Lua-based dispatch reservations to prevent double-booking
    - Distributed Pub/Sub event bus for multi-node WebSocket broadcasting
    """

    def __init__(self) -> None:
        self.redis: aioredis.Redis | None = None
        self._reserve_sha: str | None = None
        self._release_sha: str | None = None
        self.is_connected = False

    async def connect(self) -> None:
        """Connect to Redis, falling back to fakeredis if Redis is unavailable or requested."""
        if settings.USE_FAKEREDIS:
            logger.info("Initializing in-memory FakeRedis mesh...")
            import fakeredis.aioredis
            self.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
            self.is_connected = True
        else:
            try:
                self.redis = aioredis.from_url(
                    settings.REDIS_URL,
                    decode_responses=True,
                    max_connections=settings.REDIS_POOL_SIZE,
                    socket_connect_timeout=2.0,
                )
                await self.redis.ping()
                self.is_connected = True
                logger.info("Connected to Redis instance successfully.")
            except Exception as e:
                logger.warning(
                    f"Unable to connect to Redis at {settings.REDIS_URL} ({e}). "
                    "Falling back to in-memory FakeRedis mesh for standalone execution."
                )
                import fakeredis.aioredis
                self.redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
                self.is_connected = True

        # Pre-register Lua scripts for atomic operations
        try:
            self._reserve_sha = await self.redis.script_load(LUA_ATOMIC_RESERVE_DRIVER)
            self._release_sha = await self.redis.script_load(LUA_RELEASE_DRIVER_RESERVATION)
        except Exception:
            # Script loading may not be supported by older fakeredis; eval fallback is handled below
            self._reserve_sha = None
            self._release_sha = None

    async def disconnect(self) -> None:
        if self.redis:
            await self.redis.aclose()
            self.is_connected = False
            logger.info("Redis mesh disconnected.")

    # ---------------------------------------------------------
    # Spatial Discretization & Driver State Operations
    # ---------------------------------------------------------

    async def _ensure_connected(self) -> None:
        if not self.is_connected or self.redis is None:
            await self.connect()

    async def update_driver_telemetry(
        self,
        driver_id: str,
        lat: float,
        lng: float,
        speed: float = 0.0,
        heading: float = 0.0,
        status: str = "IDLE",
        resolution: int | None = None,
        driver_name: str | None = None,
        rating: float = 4.9,
        vehicle_model: str = "Sedan",
        license_plate: str | None = None,
    ) -> str:
        """Process incoming GPS telemetry point, compute H3 index, and atomically update mesh."""
        await self._ensure_connected()
        res = resolution if resolution is not None else settings.H3_DEFAULT_RESOLUTION
        new_cell = H3SpatialIndexer.latlng_to_cell(lat, lng, resolution=res)

        meta_key = f"driver:{driver_id}:meta"
        old_cell = await self.redis.hget(meta_key, "cell")
        current_status = await self.redis.hget(meta_key, "status") or status

        now = time.time()
        pipeline = self.redis.pipeline(transaction=True)

        # Update metadata hash
        driver_data = {
            "driver_id": driver_id,
            "lat": str(lat),
            "lng": str(lng),
            "cell": new_cell,
            "speed": str(speed),
            "heading": str(heading),
            "status": current_status,
            "rating": str(rating),
            "vehicle_model": vehicle_model,
            "last_heartbeat": str(now),
        }
        if driver_name:
            driver_data["name"] = driver_name
        if license_plate:
            driver_data["license_plate"] = license_plate

        pipeline.hset(meta_key, mapping=driver_data)

        # Set heartbeat TTL on driver key
        pipeline.expire(meta_key, settings.DRIVER_HEARTBEAT_TIMEOUT_SEC * 2)

        # If driver cell changed and driver is IDLE, move between cell sets
        if current_status == "IDLE":
            if old_cell and old_cell != new_cell:
                pipeline.srem(f"cell:{old_cell}:drivers", driver_id)
            pipeline.sadd(f"cell:{new_cell}:drivers", driver_id)
            pipeline.sadd("active_cells", new_cell)

        await pipeline.execute()
        return new_cell

    async def get_driver_meta(self, driver_id: str) -> dict[str, Any] | None:
        """Fetch real-time metadata hash for a driver."""
        await self._ensure_connected()
        meta = await self.redis.hgetall(f"driver:{driver_id}:meta")
        if not meta:
            return None
        return {
            "driver_id": meta.get("driver_id", driver_id),
            "lat": float(meta.get("lat", 0.0)),
            "lng": float(meta.get("lng", 0.0)),
            "cell": meta.get("cell", ""),
            "speed": float(meta.get("speed", 0.0)),
            "heading": float(meta.get("heading", 0.0)),
            "status": meta.get("status", "OFFLINE"),
            "rating": float(meta.get("rating", 4.9)),
            "name": meta.get("name", "Driver"),
            "vehicle_model": meta.get("vehicle_model", "Standard"),
            "trip_id": meta.get("trip_id", ""),
            "last_heartbeat": float(meta.get("last_heartbeat", 0.0)),
        }

    async def get_drivers_in_cells(self, cells: list[str] | set[str]) -> list[dict[str, Any]]:
        """Retrieve all available (IDLE) drivers across a collection of H3 cells in one pipelined roundtrip."""
        await self._ensure_connected()
        if not cells:
            return []

        pipeline = self.redis.pipeline(transaction=False)
        for cell in cells:
            pipeline.smembers(f"cell:{cell}:drivers")

        cell_driver_sets = await pipeline.execute()

        all_driver_ids: list[str] = []
        for d_set in cell_driver_sets:
            if d_set:
                all_driver_ids.extend(list(d_set))

        if not all_driver_ids:
            return []

        # Pipelined retrieval of driver metadata
        meta_pipeline = self.redis.pipeline(transaction=False)
        for d_id in all_driver_ids:
            meta_pipeline.hgetall(f"driver:{d_id}:meta")

        driver_metas = await meta_pipeline.execute()
        results: list[dict[str, Any]] = []

        for d_id, raw in zip(all_driver_ids, driver_metas):
            if raw and raw.get("status") == "IDLE":
                results.append({
                    "driver_id": d_id,
                    "lat": float(raw.get("lat", 0.0)),
                    "lng": float(raw.get("lng", 0.0)),
                    "cell": raw.get("cell", ""),
                    "speed": float(raw.get("speed", 0.0)),
                    "heading": float(raw.get("heading", 0.0)),
                    "status": raw.get("status", "IDLE"),
                    "rating": float(raw.get("rating", 4.9)),
                    "name": raw.get("name", "Driver"),
                    "vehicle_model": raw.get("vehicle_model", "Sedan"),
                    "license_plate": raw.get("license_plate", "7HEX001"),
                })

        return results

    async def get_all_active_drivers(self) -> list[dict[str, Any]]:
        """Fetch all online drivers across all active cells."""
        await self._ensure_connected()
        active_cells = await self.redis.smembers("active_cells")
        if not active_cells:
            return []
        return await self.get_drivers_in_cells(list(active_cells))

    async def get_active_cells(self) -> list[str]:
        """Fetch all cells currently containing drivers."""
        await self._ensure_connected()
        cells = await self.redis.smembers("active_cells")
        return list(cells)

    # ---------------------------------------------------------
    # Atomic Dispatch Reservation via Lua
    # ---------------------------------------------------------

    async def atomic_reserve_driver(
        self,
        driver_id: str,
        trip_id: str,
        cell: str,
        ttl_sec: int | None = None,
    ) -> bool:
        """Atomically reserve an IDLE driver for a dispatch offer lease.

        Uses Redis Lua script (EVAL) for microsecond O(1) atomicity.
        Falls back to Redis WATCH/MULTI optimistic concurrency transaction if EVAL is unsupported.
        """
        await self._ensure_connected()
        ttl = ttl_sec or settings.DISPATCH_OFFER_TTL_SEC
        meta_key = f"driver:{driver_id}:meta"
        lease_key = f"driver:{driver_id}:lease"
        cell_key = f"cell:{cell}:drivers"

        keys = [meta_key, lease_key, cell_key]
        args = [trip_id, str(ttl), driver_id]

        try:
            if self._reserve_sha:
                res = await self.redis.evalsha(self._reserve_sha, len(keys), *keys, *args)
            else:
                res = await self.redis.eval(LUA_ATOMIC_RESERVE_DRIVER, len(keys), *keys, *args)
            return bool(res == 1)
        except Exception:
            # Fallback to WATCH/MULTI/EXEC optimistic locking
            try:
                async with self.redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(meta_key)
                    current_status = await self.redis.hget(meta_key, "status")
                    if current_status == "IDLE":
                        pipe.multi()
                        pipe.hset(meta_key, mapping={"status": "DISPATCH_OFFERED", "trip_id": trip_id})
                        pipe.set(lease_key, trip_id, ex=ttl)
                        pipe.srem(cell_key, driver_id)
                        await pipe.execute()
                        return True
                    else:
                        await pipe.unwatch()
                        return False
            except Exception as ex:
                logger.debug(f"WATCH/MULTI reservation failed or conflicted: {ex}")
                return False

    async def release_driver_reservation(
        self,
        driver_id: str,
        cell: str,
    ) -> bool:
        """Release a driver back to IDLE and re-insert into spatial cell set."""
        meta_key = f"driver:{driver_id}:meta"
        lease_key = f"driver:{driver_id}:lease"
        cell_key = f"cell:{cell}:drivers"

        keys = [meta_key, lease_key, cell_key]
        args = [driver_id]

        try:
            if self._release_sha:
                res = await self.redis.evalsha(self._release_sha, len(keys), *keys, *args)
            else:
                res = await self.redis.eval(LUA_RELEASE_DRIVER_RESERVATION, len(keys), *keys, *args)
            return bool(res == 1)
        except Exception:
            # Fallback pipeline
            pipeline = self.redis.pipeline(transaction=True)
            pipeline.delete(lease_key)
            pipeline.hset(meta_key, mapping={"status": "IDLE", "trip_id": ""})
            pipeline.sadd(cell_key, driver_id)
            await pipeline.execute()
            return True

    async def confirm_driver_assignment(
        self,
        driver_id: str,
        trip_id: str,
    ) -> None:
        """Confirm trip assignment, clear temporary lease, and set status to ON_TRIP."""
        meta_key = f"driver:{driver_id}:meta"
        lease_key = f"driver:{driver_id}:lease"
        pipeline = self.redis.pipeline(transaction=True)
        pipeline.hset(meta_key, mapping={"status": "ON_TRIP", "trip_id": trip_id})
        pipeline.delete(lease_key)
        await pipeline.execute()

    async def complete_trip_for_driver(
        self,
        driver_id: str,
    ) -> None:
        """Reset driver status to IDLE and add back to current cell set upon trip completion."""
        meta_key = f"driver:{driver_id}:meta"
        cell = await self.redis.hget(meta_key, "cell")

        pipeline = self.redis.pipeline(transaction=True)
        pipeline.hset(meta_key, mapping={"status": "IDLE", "trip_id": ""})
        if cell:
            pipeline.sadd(f"cell:{cell}:drivers", driver_id)
        await pipeline.execute()

    # ---------------------------------------------------------
    # Distributed Pub/Sub Messaging Layer
    # ---------------------------------------------------------

    async def publish(self, channel: str, message: dict[str, Any]) -> int:
        """Publish a JSON payload to a Redis Pub/Sub channel."""
        await self._ensure_connected()
        payload = json.dumps(message)
        return await self.redis.publish(channel, payload)

    async def publish_driver_offer(self, driver_id: str, offer_data: dict[str, Any]) -> int:
        return await self.publish(f"channel:driver:{driver_id}", offer_data)

    async def publish_rider_update(self, rider_id: str, trip_data: dict[str, Any]) -> int:
        return await self.publish(f"channel:rider:{rider_id}", trip_data)

    async def publish_monitor_event(self, event_type: str, data: dict[str, Any]) -> int:
        payload = {"event": event_type, "timestamp": time.time(), "data": data}
        return await self.publish("channel:monitor", payload)

    async def subscribe_channel(self, channel: str) -> AsyncGenerator[dict[str, Any], None]:
        """Async generator that yields messages from a Redis Pub/Sub channel."""
        pubsub = self.redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                if message and message["type"] == "message":
                    try:
                        data = json.loads(message["data"])
                        yield data
                    except json.JSONDecodeError:
                        yield {"raw": message["data"]}
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.aclose()


# Singleton instance
redis_mesh = RedisSpatialMesh()
