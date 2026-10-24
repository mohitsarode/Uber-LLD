import json
import math
import random
import time
from locust import HttpUser, User, between, events, task
import websocket

# San Francisco bounding coordinates for load generation
SF_LAT_MIN, SF_LAT_MAX = 37.7500, 37.8000
SF_LNG_MIN, SF_LNG_MAX = -122.4400, -122.3900


class DriverWebSocketUser(User):
    """Simulates high-frequency driver WebSocket telemetry connections.

    Streams real-time GPS coordinates, listens for incoming dispatch offers,
    and replies with ACCEPT responses.
    """
    wait_time = between(1.0, 2.0)

    def on_start(self):
        self.driver_id = f"locust-drv-{random.randint(10000, 99999)}"
        self.lat = random.uniform(SF_LAT_MIN, SF_LAT_MAX)
        self.lng = random.uniform(SF_LNG_MIN, SF_LNG_MAX)
        self.heading = random.uniform(0, 360)
        self.speed = random.uniform(20, 45)

        # Build WebSocket URL from host
        host = self.host or "http://localhost:8000"
        ws_host = host.replace("http://", "ws://").replace("https://", "wss://")
        self.ws_url = f"{ws_host}/ws/driver/{self.driver_id}"

        start_time = time.time()
        try:
            self.ws = websocket.create_connection(self.ws_url, timeout=5)
            total_time = int((time.time() - start_time) * 1000)
            events.request.fire(
                request_type="WebSocket",
                name="Connect Driver WS",
                response_time=total_time,
                response_length=0,
                exception=None,
            )
        except Exception as e:
            total_time = int((time.time() - start_time) * 1000)
            events.request.fire(
                request_type="WebSocket",
                name="Connect Driver WS",
                response_time=total_time,
                response_length=0,
                exception=e,
            )
            self.ws = None

    def on_stop(self):
        if self.ws:
            self.ws.close()

    @task(5)
    def send_gps_telemetry(self):
        """Simulate dynamic vehicle movement along compass heading."""
        if not self.ws:
            return

        rad = math.radians(self.heading)
        self.lat += math.cos(rad) * 0.0003
        self.lng += math.sin(rad) * 0.0003

        # Periodic random heading deviation
        if random.random() < 0.2:
            self.heading = (self.heading + random.uniform(-30, 30)) % 360

        payload = {
            "action": "TELEMETRY",
            "lat": self.lat,
            "lng": self.lng,
            "speed": self.speed,
            "heading": round(self.heading, 1),
            "status": "IDLE",
            "name": f"Locust Fleet #{self.driver_id[-4:]}",
        }

        start_time = time.time()
        try:
            self.ws.send(json.dumps(payload))
            # Non-blocking check for incoming messages
            self.ws.settimeout(0.3)
            try:
                msg = self.ws.recv()
                data = json.loads(msg)
                # Auto-accept if offer arrived
                if data.get("event") == "DISPATCH_OFFER":
                    self.ws.send(json.dumps({
                        "action": "RESPOND_OFFER",
                        "trip_id": data["trip_id"],
                        "accepted": True,
                    }))
            except (websocket.WebSocketTimeoutException, TimeoutError):
                pass

            total_time = int((time.time() - start_time) * 1000)
            events.request.fire(
                request_type="WebSocket",
                name="Driver Telemetry Ping",
                response_time=total_time,
                response_length=len(json.dumps(payload)),
                exception=None,
            )
        except Exception as e:
            total_time = int((time.time() - start_time) * 1000)
            events.request.fire(
                request_type="WebSocket",
                name="Driver Telemetry Ping",
                response_time=total_time,
                response_length=0,
                exception=e,
            )


class RiderHttpUser(HttpUser):
    """Simulates riders issuing concurrent ride requests via REST API."""
    wait_time = between(2.0, 4.0)

    @task(3)
    def request_ride(self):
        p_lat = random.uniform(SF_LAT_MIN, SF_LAT_MAX)
        p_lng = random.uniform(SF_LNG_MIN, SF_LNG_MAX)
        d_lat = p_lat + random.uniform(-0.02, 0.02)
        d_lng = p_lng + random.uniform(-0.02, 0.02)

        payload = {
            "rider_id": f"locust-rider-{random.randint(1000, 9999)}",
            "pickup_lat": p_lat,
            "pickup_lng": p_lng,
            "dropoff_lat": d_lat,
            "dropoff_lng": d_lng,
            "max_k_ring": 3,
        }

        with self.client.post(
            "/api/v1/dispatch/request",
            json=payload,
            catch_response=True,
            name="POST /api/v1/dispatch/request",
        ) as response:
            if response.status_code == 200:
                data = response.json()
                if data.get("success"):
                    response.success()
                else:
                    # Match wasn't found (valid business case if all drivers busy)
                    response.success()
            else:
                response.failure(f"HTTP {response.status_code}: {response.text}")

    @task(1)
    def get_active_hexes(self):
        self.client.get("/api/v1/spatial/cells", name="GET /api/v1/spatial/cells")

    @task(1)
    def get_system_metrics(self):
        self.client.get("/api/v1/analytics/stats", name="GET /api/v1/analytics/stats")
