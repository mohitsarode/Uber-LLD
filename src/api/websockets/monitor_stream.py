import asyncio
import json
import logging
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from src.services.redis_mesh import redis_mesh

logger = logging.getLogger(__name__)
router = APIRouter()


@router.websocket("/ws/monitor")
async def monitor_websocket_endpoint(websocket: WebSocket):
    """Real-time monitoring WebSocket for the live web dashboard.

    Streams:
    - Real-time driver GPS movements
    - Dispatch matches and k-ring visual expansion ripples
    - Trip completions
    """
    await websocket.accept()
    logger.info("Operations dashboard connected to monitor WebSocket.")

    # Send initial state snapshot
    try:
        active_drivers = await redis_mesh.get_all_active_drivers()
        active_cells = await redis_mesh.get_active_cells()
        await websocket.send_text(
            json.dumps({
                "event": "SNAPSHOT",
                "drivers": active_drivers,
                "active_cells": active_cells,
            })
        )
    except Exception as e:
        logger.warning(f"Could not send snapshot to monitor: {e}")

    async def forward_monitor_events():
        try:
            async for msg in redis_mesh.subscribe_channel("channel:monitor"):
                await websocket.send_text(json.dumps(msg))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.debug(f"Monitor stream forwarder closed: {e}")

    monitor_task = asyncio.create_task(forward_monitor_events())

    try:
        while True:
            # Keepalive receiver
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.info("Operations dashboard disconnected.")
    except Exception as e:
        logger.debug(f"Monitor websocket disconnected: {e}")
    finally:
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
