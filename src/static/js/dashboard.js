// H3 Spatial Indexing & Dispatch Engine - Dashboard Controller

const SF_CENTER = [37.774929, -122.419416];
let map;
let ws;
let driverMarkers = {};
let hexLayers = {};
let routeLines = [];
let pickupMarker = null;
let currentPickupCoords = null;

// Metrics
let totalTelemetryCount = 0;
let simulationInterval = null;
let simulatedDriverSockets = [];

// DOM Elements
const statDrivers = document.getElementById("stat-drivers");
const statCells = document.getElementById("stat-cells");
const statIngested = document.getElementById("stat-ingested");
const statLatency = document.getElementById("stat-latency");
const wsStatus = document.getElementById("ws-status");
const eventLog = document.getElementById("event-log");
const inspectorContent = document.getElementById("inspector-content");
const btnStartSim = document.getElementById("btn-start-sim");
const btnStopSim = document.getElementById("btn-stop-sim");
const btnRequestRide = document.getElementById("btn-request-ride");
const sliderKring = document.getElementById("slider-kring");
const valKring = document.getElementById("val-kring");
const sliderDrivers = document.getElementById("slider-drivers");
const valFleetSize = document.getElementById("val-fleet-size");
const selectPickupMode = document.getElementById("select-pickup-mode");

// Preset Locations
const PRESET_LOCATIONS = {
    downtown: [37.7895, -122.4010],
    soma: [37.7785, -122.4056],
    mission: [37.7599, -122.4148],
};

function initMap() {
    map = L.map("map", {
        center: SF_CENTER,
        zoom: 13,
        zoomControl: true,
    });

    // Dark Matter CartoDB tiles
    L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
        attribution: '&copy; <a href="https://carto.com/">CARTO</a> &copy; Uber H3',
        subdomains: "abcd",
        maxZoom: 19,
    }).addTo(map);

    // Map Click Listener
    map.on("click", (e) => {
        handleMapClick(e.latlng.lat, e.latlng.lng);
    });
}

function logEvent(text, category = "log-system") {
    const entry = document.createElement("div");
    entry.className = `log-entry ${category}`;
    const timeStr = new Date().toLocaleTimeString();
    entry.textContent = `[${timeStr}] ${text}`;
    eventLog.appendChild(entry);
    eventLog.scrollTop = eventLog.scrollHeight;
}

// -------------------------------------------------------------
// WebSocket Connection to Backend Monitor
// -------------------------------------------------------------
function connectMonitorWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/monitor`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        wsStatus.textContent = "Mesh Connected";
        wsStatus.parentElement.style.borderColor = "rgba(16, 185, 129, 0.4)";
        logEvent("Connected to distributed Redis mesh monitor stream.", "log-system");
        refreshActiveHexGrid();
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleMonitorMessage(msg);
        } catch (e) {
            console.error("Failed to parse WS frame:", e);
        }
    };

    ws.onclose = () => {
        wsStatus.textContent = "Disconnected";
        wsStatus.parentElement.style.borderColor = "rgba(244, 63, 94, 0.4)";
        logEvent("Monitor WebSocket disconnected. Reconnecting in 3s...", "log-system");
        setTimeout(connectMonitorWebSocket, 3000);
    };
}

function handleMonitorMessage(msg) {
    if (msg.event === "SNAPSHOT") {
        if (msg.drivers) {
            msg.drivers.forEach(updateDriverMarker);
            statDrivers.textContent = Object.keys(driverMarkers).length;
        }
    } else if (msg.event === "DRIVER_PING") {
        totalTelemetryCount++;
        statIngested.textContent = totalTelemetryCount;
        updateDriverMarker(msg.data);
    } else if (msg.event === "DISPATCH_MATCH") {
        const d = msg.data;
        statLatency.textContent = `${d.latency_ms} ms`;
        logEvent(`MATCH FOUND: Trip ${d.trip_id.slice(0, 8)} -> Driver ${d.driver_id.slice(0, 8)} in ${d.latency_ms}ms (searched ${d.rings_searched} rings)`, "log-match");
        visualizeDispatchMatch(d);
    } else if (msg.event === "DISPATCH_FAILED") {
        const d = msg.data;
        logEvent(`DISPATCH FAILED: No available driver within ${d.rings_searched} rings (${d.latency_ms}ms)`, "log-system");
    } else if (msg.event === "TRIP_ACCEPTED") {
        logEvent(`TRIP ACCEPTED by driver ${msg.data.driver_id.slice(0, 8)}`, "log-trip");
    } else if (msg.event === "TRIP_COMPLETED") {
        logEvent(`TRIP COMPLETED: Trip ${msg.data.trip_id.slice(0, 8)} fare $${msg.data.fare_amount}`, "log-trip");
        clearRoutes();
    }
}

// -------------------------------------------------------------
// Driver Markers Management
// -------------------------------------------------------------
function updateDriverMarker(driver) {
    const id = driver.driver_id;
    const lat = parseFloat(driver.lat);
    const lng = parseFloat(driver.lng);
    const status = driver.status || "IDLE";

    if (!lat || !lng) return;

    let iconClass = "driver-marker-icon";
    if (status === "DISPATCH_OFFERED") iconClass += " busy";
    if (status === "ON_TRIP") iconClass += " on-trip";

    const customIcon = L.divIcon({
        className: "",
        html: `<div class="${iconClass}" style="transform: rotate(${driver.heading || 0}deg);">
                 <i class="fa-solid fa-car"></i>
               </div>`,
        iconSize: [32, 32],
        iconAnchor: [16, 16],
    });

    if (driverMarkers[id]) {
        driverMarkers[id].setLatLng([lat, lng]);
        driverMarkers[id].setIcon(customIcon);
    } else {
        const marker = L.marker([lat, lng], { icon: customIcon }).addTo(map);
        marker.bindPopup(`
            <strong>Driver:</strong> ${driver.name || id.slice(0, 8)}<br>
            <strong>Status:</strong> ${status}<br>
            <strong>Rating:</strong> ★ ${driver.rating || 4.9}<br>
            <strong>H3 Cell:</strong> <code>${driver.cell || ""}</code>
        `);
        driverMarkers[id] = marker;
    }

    statDrivers.textContent = Object.keys(driverMarkers).length;
}

// -------------------------------------------------------------
// Spatial Hexagons (Uber H3 Grid)
// -------------------------------------------------------------
async function refreshActiveHexGrid() {
    try {
        const res = await fetch("/api/v1/spatial/cells");
        const data = await res.json();
        
        // Clear existing
        Object.values(hexLayers).forEach(layer => map.removeLayer(layer));
        hexLayers = {};

        if (data.features) {
            data.features.forEach(feature => {
                const cell = feature.properties.cell;
                const polygon = L.geoJSON(feature, {
                    style: {
                        color: "#06b6d4",
                        weight: 1.5,
                        fillColor: "#0284c7",
                        fillOpacity: 0.12,
                    }
                }).addTo(map);

                polygon.on("click", () => inspectCell(cell));
                hexLayers[cell] = polygon;
            });
            statCells.textContent = data.features.length;
        }
    } catch (e) {
        console.error("Failed to load active hex grid:", e);
    }
}

// -------------------------------------------------------------
// Map Click & Pickup Selection
// -------------------------------------------------------------
async function handleMapClick(lat, lng) {
    currentPickupCoords = [lat, lng];

    if (pickupMarker) {
        map.removeLayer(pickupMarker);
    }

    const pickupIcon = L.divIcon({
        className: "",
        html: `<div class="pickup-marker-icon"><i class="fa-solid fa-person-walking-luggage"></i></div>`,
        iconSize: [30, 30],
        iconAnchor: [15, 15],
    });

    pickupMarker = L.marker([lat, lng], { icon: pickupIcon }).addTo(map);

    // Resolve H3 cell for clicked point
    try {
        const res = await fetch(`/api/v1/spatial/resolve?lat=${lat}&lng=${lng}&resolution=8`);
        const info = await res.json();
        inspectCell(info.h3_cell, info);
    } catch (e) {
        console.error("Failed to resolve cell:", e);
    }
}

async function inspectCell(cellId, cachedInfo = null) {
    try {
        const info = cachedInfo || await (await fetch(`/api/v1/spatial/resolve?lat=${SF_CENTER[0]}&lng=${SF_CENTER[1]}&resolution=8`)).json();
        const driversInCell = await (await fetch(`/api/v1/drivers?cell=${cellId}`)).json();

        inspectorContent.innerHTML = `
            <table class="inspector-table">
                <tr><td>H3 Index:</td><td><code>${cellId}</code></td></tr>
                <tr><td>Resolution:</td><td>Res 8 (~460m edge)</td></tr>
                <tr><td>Drivers in Cell:</td><td><strong>${driversInCell.length}</strong></td></tr>
            </table>
        `;
    } catch (e) {
        inspectorContent.innerHTML = `<span class="inspector-placeholder">Cell: ${cellId}</span>`;
    }
}

// -------------------------------------------------------------
// Dispatch Match Visualization (k-Ring Ripple & Route Line)
// -------------------------------------------------------------
async function visualizeDispatchMatch(matchData) {
    clearRoutes();

    const pickup = matchData.pickup_coords;
    const driver = matchData.driver_coords;

    // 1. Fetch & visualize k-ring expansion search rings
    try {
        const kringRes = await fetch(`/api/v1/spatial/kring?lat=${pickup[0]}&lng=${pickup[1]}&k=${matchData.rings_searched}&resolution=8`);
        const kringGeoJSON = await kringRes.json();

        const searchLayer = L.geoJSON(kringGeoJSON, {
            style: (feature) => {
                const isCenter = feature.properties.is_center;
                return {
                    color: isCenter ? "#10b981" : "#f59e0b",
                    weight: 2,
                    fillColor: isCenter ? "#10b981" : "#f59e0b",
                    fillOpacity: 0.15,
                    dashArray: isCenter ? null : "4, 4",
                };
            }
        }).addTo(map);
        routeLines.push(searchLayer);
    } catch (e) {
        console.error("Could not render k-ring search disk:", e);
    }

    // 2. Draw connecting dispatch line
    if (pickup && driver) {
        const line = L.polyline([pickup, driver], {
            color: "#38bdf8",
            weight: 3.5,
            dashArray: "6, 8",
            opacity: 0.9,
        }).addTo(map);
        routeLines.push(line);

        map.fitBounds(L.latLngBounds([pickup, driver]).pad(0.3));
    }
}

function clearRoutes() {
    routeLines.forEach(layer => map.removeLayer(layer));
    routeLines = [];
}

// -------------------------------------------------------------
// Dispatch Request Trigger
// -------------------------------------------------------------
async function triggerRideDispatch() {
    let pickup = currentPickupCoords;
    const mode = selectPickupMode.value;

    if (mode !== "click" && PRESET_LOCATIONS[mode]) {
        pickup = PRESET_LOCATIONS[mode];
        handleMapClick(pickup[0], pickup[1]);
    }

    if (!pickup) {
        alert("Please click anywhere on the map or select a preset location to set pickup.");
        return;
    }

    // Generate random dropoff ~2-4 km away
    const dropoffLat = pickup[0] + (Math.random() - 0.5) * 0.03;
    const dropoffLng = pickup[1] + (Math.random() - 0.5) * 0.03;

    btnRequestRide.disabled = true;
    btnRequestRide.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Dispatched...`;

    try {
        const res = await fetch("/api/v1/dispatch/request", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                rider_id: "demo-rider-" + Math.floor(Math.random() * 1000),
                pickup_lat: pickup[0],
                pickup_lng: pickup[1],
                dropoff_lat: dropoffLat,
                dropoff_lng: dropoffLng,
                max_k_ring: parseInt(sliderKring.value),
            }),
        });

        const data = await res.json();
        if (data.success) {
            logEvent(`Dispatch offer sent to driver ${data.matched_driver.driver_id.slice(0, 8)} in ${data.latency_ms}ms`, "log-match");
        } else {
            logEvent(`No driver found within ${data.rings_searched} rings (${data.latency_ms}ms)`, "log-system");
        }
    } catch (e) {
        logEvent(`Dispatch request failed: ${e}`, "log-system");
    } finally {
        btnRequestRide.disabled = false;
        btnRequestRide.innerHTML = `<i class="fa-solid fa-magnifying-glass-location"></i> Request Ride & Dispatch`;
    }
}

// -------------------------------------------------------------
// Client-Side Simulated Telemetry Streams (WebSocket)
// -------------------------------------------------------------
function startTelemetrySimulation() {
    btnStartSim.disabled = true;
    btnStopSim.disabled = false;
    logEvent("Spawning simulated driver fleet via persistent WebSockets...", "log-system");

    const fleetCount = parseInt(sliderDrivers.value);
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";

    for (let i = 0; i < fleetCount; i++) {
        const driverId = `sim-driver-${1000 + i}`;
        let lat = SF_CENTER[0] + (Math.random() - 0.5) * 0.04;
        let lng = SF_CENTER[1] + (Math.random() - 0.5) * 0.05;
        let heading = Math.floor(Math.random() * 360);
        let speed = 25.0 + Math.random() * 20.0;

        const ws = new WebSocket(`${protocol}//${window.location.host}/ws/driver/${driverId}`);

        ws.onopen = () => {
            // Send initial ping
            ws.send(JSON.stringify({
                action: "TELEMETRY",
                lat: lat,
                lng: lng,
                speed: speed,
                heading: heading,
                status: "IDLE",
                name: `Fleet Unit #${100 + i}`,
                vehicle: ["Tesla Model 3", "Toyota Prius", "Hyundai Ioniq", "BMW i4"][i % 4],
            }));
        };

        ws.onmessage = (e) => {
            try {
                const msg = JSON.parse(e.data);
                // Automatically accept dispatch offers in simulation!
                if (msg.event === "DISPATCH_OFFER") {
                    setTimeout(() => {
                        ws.send(JSON.stringify({
                            action: "RESPOND_OFFER",
                            trip_id: msg.trip_id,
                            accepted: true,
                        }));
                    }, 800);
                }
            } catch (err) {}
        };

        simulatedDriverSockets.push({ ws, lat, lng, heading, speed });
    }

    // Continuous motion interval
    simulationInterval = setInterval(() => {
        simulatedDriverSockets.forEach(item => {
            if (item.ws.readyState === WebSocket.OPEN) {
                // Advance coordinate in current heading direction
                const rad = (item.heading * Math.PI) / 180.0;
                item.lat += Math.cos(rad) * 0.0003;
                item.lng += Math.sin(rad) * 0.0003;

                // Slightly randomize heading periodically
                if (Math.random() < 0.2) {
                    item.heading = (item.heading + (Math.random() * 40 - 20) + 360) % 360;
                }

                item.ws.send(JSON.stringify({
                    action: "TELEMETRY",
                    lat: item.lat,
                    lng: item.lng,
                    speed: item.speed,
                    heading: Math.round(item.heading),
                    status: "IDLE",
                }));
            }
        });
    }, 1500);
}

function stopTelemetrySimulation() {
    btnStartSim.disabled = false;
    btnStopSim.disabled = true;

    if (simulationInterval) {
        clearInterval(simulationInterval);
        simulationInterval = null;
    }

    simulatedDriverSockets.forEach(item => {
        if (item.ws.readyState === WebSocket.OPEN) item.ws.close();
    });
    simulatedDriverSockets = [];
    logEvent("Driver telemetry simulation paused.", "log-system");
}

// -------------------------------------------------------------
// Event Listeners & Bootstrapping
// -------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
    initMap();
    connectMonitorWebSocket();

    btnStartSim.addEventListener("click", startTelemetrySimulation);
    btnStopSim.addEventListener("click", stopTelemetrySimulation);
    btnRequestRide.addEventListener("click", triggerRideDispatch);

    sliderKring.addEventListener("input", (e) => valKring.textContent = e.target.value);
    sliderDrivers.addEventListener("input", (e) => valFleetSize.textContent = e.target.value);

    document.getElementById("btn-clear-routes").addEventListener("click", clearRoutes);
    document.getElementById("btn-toggle-hexes").addEventListener("click", (e) => {
        e.target.classList.toggle("active");
        const visible = e.target.classList.contains("active");
        Object.values(hexLayers).forEach(l => visible ? map.addLayer(l) : map.removeLayer(l));
    });
});
