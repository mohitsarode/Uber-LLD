import asyncio
import json
import logging
import time
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from src.core.config import settings
from src.services.dispatch_service import dispatch_service
from src.services.redis_mesh import redis_mesh
from src.services.telemetry_buffer import telemetry_buffer

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/driver/{driver_id}")
async def driver_websocket_endpoint(websocket: WebSocket, driver_id: str):
    """High-frequency bidirectional WebSocket connection for drivers.

    - Ingests GPS telemetry updates (lat, lng, speed, heading, status)
    - Receives dispatch offers from Redis mesh Pub/Sub
    - Accepts driver responses to offers (ACCEPT / REJECT)
    """
    await websocket.accept()
    logger.info(f"Driver {driver_id} connected via WebSocket.")

    # Task to forward Redis Pub/Sub events down to driver WebSocket
    async def forward_pubsub_events():
        channel = f"channel:driver:{driver_id}"
        try:
            async for msg in redis_mesh.subscribe_channel(channel):
                await websocket.send_text(json.dumps(msg))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Pub/sub forwarding closed for driver {driver_id}: {e}")

    pubsub_task = asyncio.create_task(forward_pubsub_events())

    try:
        while True:
            raw_data = await websocket.receive_text()
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"error": "Invalid JSON format"}))
                continue

            action = data.get("action", "TELEMETRY")

            if action == "TELEMETRY":
                lat = float(data.get("lat", 0.0))
                lng = float(data.get("lng", 0.0))
                speed = float(data.get("speed", 0.0))
                heading = float(data.get("heading", 0.0))
                status = data.get("status", "IDLE")

                # 1. Atomically update in-memory Redis spatial index
                cell = await redis_mesh.update_driver_telemetry(
                    driver_id=driver_id,
                    lat=lat,
                    lng=lng,
                    speed=speed,
                    heading=heading,
                    status=status,
                    driver_name=data.get("name"),
                    rating=float(data.get("rating", 4.9)),
                    vehicle_model=data.get("vehicle", "Sedan"),
                )

                # 2. Asynchronously buffer into TimescaleDB batcher (non-blocking)
                await telemetry_buffer.push({
                    "time": time.time(),
                    "driver_id": driver_id,
                    "latitude": lat,
                    "longitude": lng,
                    "h3_cell": cell,
                    "speed_kmh": speed,
                    "heading": heading,
                    "trip_id": data.get("trip_id"),
                })

                # 3. Publish to live monitor
                await redis_mesh.publish_monitor_event(
                    "DRIVER_PING",
                    {
                        "driver_id": driver_id,
                        "lat": lat,
                        "lng": lng,
                        "cell": cell,
                        "speed": speed,
                        "heading": heading,
                        "status": status,
                    },
                )

                # Send lightweight ACK
                await websocket.send_text(json.dumps({"status": "ACK", "cell": cell}))

            elif action == "RESPOND_OFFER":
                trip_id = data.get("trip_id", "")
                accepted = bool(data.get("accepted", False))
                res = await dispatch_service.respond_to_offer(
                    driver_id=driver_id,
                    trip_id=trip_id,
                    accepted=accepted,
                )
                await websocket.send_text(json.dumps(res))

            elif action == "PING":
                await websocket.send_text(json.dumps({"status": "PONG", "timestamp": time.time()}))

    except WebSocketDisconnect:
        logger.info(f"Driver {driver_id} disconnected.")
    except Exception as e:
        logger.error(f"Error in driver {driver_id} websocket: {e}", exc_info=True)
    finally:
        pubsub_task.cancel()
        try:
            await pubsub_task
        except asyncio.CancelledError:
            pass
