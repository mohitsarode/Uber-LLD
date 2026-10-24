"""Standalone Driver Telemetry & Dispatch Fleet Simulator.

Spawns N concurrent driver WebSocket streams sending continuous GPS telemetry
over San Francisco city street coordinates, with periodic rider dispatch requests.
"""

import asyncio
import json
import math
from pathlib import Path
import random
import sys
import time

# Ensure project root is in python path
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import websockets
import httpx

API_BASE = "http://localhost:8000"
WS_BASE = "ws://localhost:8000"
SF_CENTER = (37.774929, -122.419416)


async def simulate_driver(driver_id: str, name: str, start_lat: float, start_lng: float, stop_event: asyncio.Event):
    uri = f"{WS_BASE}/ws/driver/{driver_id}"
    lat = start_lat
    lng = start_lng
    heading = random.uniform(0, 360)
    speed = random.uniform(20, 45)

    while not stop_event.is_set():
        try:
            async with websockets.connect(uri) as ws:
                print(f"[Driver {name}] Connected to mesh.")
                while not stop_event.is_set():
                    # Move driver along heading
                    rad = math.radians(heading)
                    lat += math.cos(rad) * 0.0003
                    lng += math.sin(rad) * 0.0003

                    # Slight heading shift
                    if random.random() < 0.25:
                        heading = (heading + random.uniform(-30, 30)) % 360

                    payload = {
                        "action": "TELEMETRY",
                        "lat": lat,
                        "lng": lng,
                        "speed": speed,
                        "heading": round(heading, 1),
                        "status": "IDLE",
                        "name": name,
                    }
                    await ws.send(json.dumps(payload))

                    # Check for incoming offers with brief timeout
                    try:
                        resp = await asyncio.wait_for(ws.recv(), timeout=1.2)
                        data = json.loads(resp)
                        if data.get("event") == "DISPATCH_OFFER":
                            print(f"--> [Driver {name}] Received DISPATCH OFFER for trip {data['trip_id'][:8]}! Accepting...")
                            await asyncio.sleep(0.5)
                            await ws.send(json.dumps({
                                "action": "RESPOND_OFFER",
                                "trip_id": data["trip_id"],
                                "accepted": True,
                            }))
                    except asyncio.TimeoutError:
                        pass

                    await asyncio.sleep(1.0)
        except Exception as e:
            if not stop_event.is_set():
                print(f"[Driver {name}] Connection lost ({e}), reconnecting in 2s...")
                await asyncio.sleep(2.0)


async def simulate_riders(stop_event: asyncio.Event):
    """Periodically triggers ride requests to test dispatch engine matching."""
    client = httpx.AsyncClient(base_url=API_BASE, timeout=10.0)
    rider_seq = 1

    while not stop_event.is_set():
        await asyncio.sleep(4.0)
        pickup_lat = SF_CENTER[0] + random.uniform(-0.02, 0.02)
        pickup_lng = SF_CENTER[1] + random.uniform(-0.02, 0.02)
        dropoff_lat = pickup_lat + random.uniform(-0.03, 0.03)
        dropoff_lng = pickup_lng + random.uniform(-0.03, 0.03)

        try:
            start_t = time.perf_counter()
            res = await client.post(
                "/api/v1/dispatch/request",
                json={
                    "rider_id": f"rider-{rider_seq:04d}",
                    "pickup_lat": pickup_lat,
                    "pickup_lng": pickup_lng,
                    "dropoff_lat": dropoff_lat,
                    "dropoff_lng": dropoff_lng,
                    "max_k_ring": 3,
                },
            )
            lat_ms = (time.perf_counter() - start_t) * 1000.0
            data = res.json()
            if data.get("success"):
                print(f"[Rider #{rider_seq}] MATCHED in {data['latency_ms']}ms (API roundtrip {lat_ms:.1f}ms). Driver: {data['matched_driver']['driver_id'][:8]}")
            else:
                print(f"[Rider #{rider_seq}] NO DRIVER in {data.get('rings_searched', 3)} rings ({lat_ms:.1f}ms)")
            rider_seq += 1
        except Exception as e:
            print(f"[Rider] Request error: {e}")

    await client.aclose()


async def main():
    driver_count = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    print(f"=== Starting Fleet Simulation ({driver_count} drivers) ===")
    print(f"Targeting backend at {WS_BASE} ...")

    stop_event = asyncio.Event()
    tasks = []

    for i in range(driver_count):
        d_id = f"sim-fleet-{i+1:03d}"
        d_name = f"Unit #{i+1:02d}"
        start_lat = SF_CENTER[0] + random.uniform(-0.025, 0.025)
        start_lng = SF_CENTER[1] + random.uniform(-0.025, 0.025)
        t = asyncio.create_task(simulate_driver(d_id, d_name, start_lat, start_lng, stop_event))
        tasks.append(t)

    rider_task = asyncio.create_task(simulate_riders(stop_event))
    tasks.append(rider_task)

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\nStopping simulation...")
        stop_event.set()
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
