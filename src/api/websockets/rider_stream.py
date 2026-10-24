import asyncio
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from src.services.dispatch_service import dispatch_service
from src.services.redis_mesh import redis_mesh

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/rider/{rider_id}")
async def rider_websocket_endpoint(websocket: WebSocket, rider_id: str):
    """Rider WebSocket connection for real-time trip lifecycle updates and requests."""
    await websocket.accept()
    logger.info(f"Rider {rider_id} connected via WebSocket.")

    async def forward_pubsub_events():
        channel = f"channel:rider:{rider_id}"
        try:
            async for msg in redis_mesh.subscribe_channel(channel):
                await websocket.send_text(json.dumps(msg))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Pub/sub forwarding closed for rider {rider_id}: {e}")

    pubsub_task = asyncio.create_task(forward_pubsub_events())

    try:
        while True:
            raw_data = await websocket.receive_text()
            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"error": "Invalid JSON format"}))
                continue

            action = data.get("action")

            if action == "REQUEST_RIDE":
                pickup_lat = float(data.get("pickup_lat", 0.0))
                pickup_lng = float(data.get("pickup_lng", 0.0))
                dropoff_lat = float(data.get("dropoff_lat", 0.0))
                dropoff_lng = float(data.get("dropoff_lng", 0.0))

                result = await dispatch_service.request_ride(
                    rider_id=rider_id,
                    pickup_lat=pickup_lat,
                    pickup_lng=pickup_lng,
                    dropoff_lat=dropoff_lat,
                    dropoff_lng=dropoff_lng,
                    fare_amount=data.get("fare_amount"),
                )
                await websocket.send_text(json.dumps({"event": "REQUEST_RESULT", "result": result}))

    except WebSocketDisconnect:
        logger.info(f"Rider {rider_id} disconnected.")
    except Exception as e:
        logger.error(f"Error in rider {rider_id} websocket: {e}", exc_info=True)
    finally:
        pubsub_task.cancel()
        try:
            await pubsub_task
        except asyncio.CancelledError:
            pass
