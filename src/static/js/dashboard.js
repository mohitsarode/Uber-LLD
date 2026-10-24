// =========================================================================
// Uber HexDispatch - Core Frontend Controller & Simulation Engine
// OpenFreeMap Vector Layer + OSRM Real Road Routing + Mathematical Audit
// =========================================================================

const SF_CENTER = [37.7850, -122.4080];

// Presets Coordinates (San Francisco Hubs)
const ROUTE_PRESETS = {
    "financial-ferry": {
        pickupName: "Financial District (Montgomery St)",
        pickup: [37.7895, -122.4010],
        dropoffName: "Ferry Building (The Embarcadero)",
        dropoff: [37.7955, -122.3937],
        baseFare: 12.50,
    },
    "soma-chase": {
        pickupName: "SoMa Tech Hub (4th & Howard)",
        pickup: [37.7820, -122.4015],
        dropoffName: "Chase Center (Mission Bay)",
        dropoff: [37.7680, -122.3877],
        baseFare: 16.80,
    },
    "mission-marina": {
        pickupName: "Mission Dolores (18th & Dolores)",
        pickup: [37.7599, -122.4260],
        dropoffName: "Marina Green (Marina Blvd)",
        dropoff: [37.8050, -122.4380],
        baseFare: 22.40,
    },
};

// Application State
let map;
let ws;
let driverMarkers = {};
let hexLayers = {};
let routeLayers = [];
let rippleLayers = [];
let candidateDistanceLayers = [];
let pickupMarker = null;
let dropoffMarker = null;

let currentPresetKey = "financial-ferry";
let currentPickup = ROUTE_PRESETS["financial-ferry"].pickup;
let currentDropoff = ROUTE_PRESETS["financial-ferry"].dropoff;
let selectedTier = "UberX";
let selectedMultiplier = 1.0;
let simulationSpeedMultiplier = 2.0;

let isFleetSimActive = false;
let fleetSimulationInterval = null;
let activeDriversList = [];
let activeTripAnimId = null;
let tripStartTime = null;
let currentTripData = null;
let currentAuditData = null;
let telemetryFrameCount = 0;
let mapClickMode = null; // 'pickup' | 'dropoff' | null
let showCandidateDistances = true;

// Top-Down Sleek Uber Car SVG
const UBER_CAR_SVG = `
<svg class="uber-car-marker-svg" viewBox="0 0 40 60" fill="none" xmlns="http://www.w3.org/2000/svg">
  <rect x="7" y="5" width="26" height="50" rx="9" fill="#000" opacity="0.5" />
  <rect x="8" y="4" width="24" height="48" rx="8" fill="#1C1C1E" stroke="#FFFFFF" stroke-width="1.8"/>
  <path d="M12 16C12 14.5 13 13 15 13H25C27 13 28 14.5 28 16L27 21H13L12 16Z" fill="#38BDF8" opacity="0.85"/>
  <rect x="13" y="21" width="14" height="15" rx="3" fill="#2C2C2E"/>
  <path d="M13 36H27L28 40C28 41.5 27 42.5 25 42.5H15C13 42.5 12 41.5 12 40L13 36Z" fill="#38BDF8" opacity="0.6"/>
  <rect x="9.5" y="4.5" width="4.5" height="2.5" rx="1" fill="#FEF08A"/>
  <rect x="26" y="4.5" width="4.5" height="2.5" rx="1" fill="#FEF08A"/>
  <rect x="10" y="50" width="4" height="1.8" rx="0.5" fill="#EF4444"/>
  <rect x="26" y="50" width="4" height="1.8" rx="0.5" fill="#EF4444"/>
</svg>
`;

// -------------------------------------------------------------
// Initialization
// -------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
    initMap();
    initEventListeners();
    connectMonitorWebSocket();
    loadActiveHexGrid();
    loadRealDriversFromBackend();
});

function initMap() {
    map = L.map("map", {
        center: SF_CENTER,
        zoom: 14,
        zoomControl: false,
    });

    // 1. Primary: OpenFreeMap Vector Tiles (Dark Style - zero watermark, no API key)
    let openFreeMapLoaded = false;
    try {
        if (typeof L.maplibreGL === "function") {
            L.maplibreGL({
                style: "https://tiles.openfreemap.org/styles/dark",
            }).addTo(map);
            openFreeMapLoaded = true;
            logTerminal("OpenFreeMap vector basemap (Dark Style) connected.", "sys");
        }
    } catch (e) {
        console.warn("OpenFreeMap vector tile load failed, using dark raster fallback:", e);
    }

    // 2. Resilient Fallback: Esri Dark Gray Canvas
    if (!openFreeMapLoaded) {
        L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}", {
            attribution: '&copy; OpenFreeMap &copy; OpenStreetMap',
            maxZoom: 19,
        }).addTo(map);

        L.tileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}", {
            attribution: '',
            maxZoom: 19,
            opacity: 0.75,
        }).addTo(map);
        logTerminal("Connected to high-contrast dark canvas tiles.", "sys");
    }

    L.control.zoom({ position: "bottomright" }).addTo(map);

    // Initial markers for preset
    updateRouteMarkers(currentPickup, currentDropoff);

    map.on("click", (e) => {
        if (mapClickMode === "pickup") {
            currentPickup = [e.latlng.lat, e.latlng.lng];
            document.getElementById("input-pickup").value = `Pinned (${e.latlng.lat.toFixed(4)}, ${e.latlng.lng.toFixed(4)})`;
            updateRouteMarkers(currentPickup, currentDropoff);
            setMapClickMode(null);
        } else if (mapClickMode === "dropoff") {
            currentDropoff = [e.latlng.lat, e.latlng.lng];
            document.getElementById("input-dropoff").value = `Pinned (${e.latlng.lat.toFixed(4)}, ${e.latlng.lng.toFixed(4)})`;
            updateRouteMarkers(currentPickup, currentDropoff);
            setMapClickMode(null);
        } else {
            inspectPoint(e.latlng.lat, e.latlng.lng);
        }
    });
}

function updateRouteMarkers(pickup, dropoff) {
    if (pickupMarker) map.removeLayer(pickupMarker);
    if (dropoffMarker) map.removeLayer(dropoffMarker);

    const pickupIcon = L.divIcon({
        className: "",
        html: `<div class="uber-pickup-pin" title="Pickup Location"></div>`,
        iconSize: [26, 26],
        iconAnchor: [13, 13],
    });

    const dropoffIcon = L.divIcon({
        className: "",
        html: `<div class="uber-dropoff-pin" title="Destination"></div>`,
        iconSize: [24, 24],
        iconAnchor: [12, 12],
    });

    pickupMarker = L.marker(pickup, { icon: pickupIcon }).addTo(map);
    dropoffMarker = L.marker(dropoff, { icon: dropoffIcon }).addTo(map);

    // Calculate Fares
    const distM = calculateDistanceM(pickup[0], pickup[1], dropoff[0], dropoff[1]);
    const distKm = distM / 1000.0;
    const base = Math.max(8.0, 4.0 + (distKm * 2.10));
    document.getElementById("fare-uberx").textContent = `$${base.toFixed(2)}`;
    document.getElementById("fare-comfort").textContent = `$${(base * 1.35).toFixed(2)}`;
    document.getElementById("fare-black").textContent = `$${(base * 1.95).toFixed(2)}`;

    // Auto fit with left padding so the panel doesn't block the route
    fitMapToRoute(pickup, dropoff);
}

function fitMapToRoute(p1, p2) {
    const bounds = L.latLngBounds([p1, p2]);
    map.fitBounds(bounds, {
        paddingTopLeft: [420, 60],
        paddingBottomRight: [60, 60],
        maxZoom: 15,
    });
}

// -------------------------------------------------------------
// Real Driver Fleet Management (Single Source of Truth)
// -------------------------------------------------------------
async function loadRealDriversFromBackend() {
    try {
        const res = await fetch("/api/v1/drivers");
        const drivers = await res.json();
        activeDriversList = drivers;

        drivers.forEach(d => {
            updateCarMarker(d.driver_id, d.lat, d.lng, 90, d.status || "IDLE", d.name, d.vehicle_model);
        });

        document.getElementById("stat-drivers").textContent = Object.keys(driverMarkers).length;
        logTerminal(`Synchronized ${drivers.length} real fleet drivers into spatial mesh.`, "sys");
    } catch (e) {
        console.error("Failed to fetch drivers from backend:", e);
    }
}

function updateCarMarker(driverId, lat, lng, heading = 0, status = "IDLE", name = "Driver", vehicle = "Sedan") {
    if (!lat || !lng) return;

    let markerClass = "uber-car-marker-container";
    if (status === "DISPATCH_OFFERED") markerClass += " matched";
    if (status === "ON_TRIP") markerClass += " on-trip";

    const customIcon = L.divIcon({
        className: "",
        html: `
            <div class="${markerClass}" id="car-wrap-${driverId}" style="transform: rotate(${Math.round(heading)}deg);">
                ${UBER_CAR_SVG}
            </div>
        `,
        iconSize: [36, 48],
        iconAnchor: [18, 24],
    });

    if (driverMarkers[driverId]) {
        driverMarkers[driverId].setLatLng([lat, lng]);
        driverMarkers[driverId].setIcon(customIcon);
        // Update stored position in active list
        const found = activeDriversList.find(d => d.driver_id === driverId);
        if (found) {
            found.lat = lat;
            found.lng = lng;
            found.status = status;
        }
    } else {
        const marker = L.marker([lat, lng], { icon: customIcon }).addTo(map);
        marker.on("click", () => inspectDriver(driverId, name, vehicle));
        driverMarkers[driverId] = marker;
    }

    document.getElementById("stat-drivers").textContent = Object.keys(driverMarkers).length;
}

// -------------------------------------------------------------
// WebSocket Monitor Connection
// -------------------------------------------------------------
function connectMonitorWebSocket() {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/monitor`;

    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        logTerminal("Connected to distributed Redis mesh monitor.", "sys");
    };

    ws.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleMonitorMessage(msg);
        } catch (e) {
            console.error("Failed to parse WS msg", e);
        }
    };

    ws.onclose = () => {
        setTimeout(connectMonitorWebSocket, 3000);
    };
}

function handleMonitorMessage(msg) {
    if (msg.event === "DRIVER_PING") {
        telemetryFrameCount++;
        document.getElementById("stat-ingested").textContent = telemetryFrameCount;
        const d = msg.data;
        // Only update if not the car in the active ride simulation to prevent jitter
        if (!currentTripData || currentTripData.matched_driver.driver_id !== d.driver_id) {
            updateCarMarker(d.driver_id, d.lat, d.lng, d.heading || 0, d.status || "IDLE");
        }
    } else if (msg.event === "DISPATCH_MATCH") {
        const d = msg.data;
        document.getElementById("stat-latency").textContent = `${d.latency_ms} ms`;
        logTerminal(`[MATCH] Trip ${d.trip_id.slice(0, 8)} matched to Driver ${d.driver_id.slice(0, 8)} in ${d.latency_ms}ms`, "match");
    }
}

// -------------------------------------------------------------
// Real Road Routing via OSRM & Dense Resampling
// -------------------------------------------------------------
async function fetchRoadRoute(startLat, startLng, endLat, endLng) {
    try {
        const url = `/api/v1/dispatch/road-route?start_lat=${startLat}&start_lng=${startLng}&end_lat=${endLat}&end_lng=${endLng}`;
        const res = await fetch(url);
        if (res.ok) {
            const data = await res.json();
            if (data.waypoints && data.waypoints.length > 0) {
                // Resample along street segments every ~8m for smooth 60fps animation
                const denseWaypoints = resamplePath(data.waypoints, 8.0);
                return {
                    waypoints: denseWaypoints,
                    distanceM: data.distance_m || calculateDistanceM(startLat, startLng, endLat, endLng),
                    durationSec: data.duration_sec || 60,
                    source: data.source || "OSRM (OpenStreetMap Road Network)",
                };
            }
        }
    } catch (e) {
        console.warn("OSRM road routing unavailable, falling back to street grid:", e);
    }

    // Resilient fallback: Manhattan street grid
    const fallbackPath = generateStreetGridPath([startLat, startLng], [endLat, endLng], 80);
    const distM = calculateDistanceM(startLat, startLng, endLat, endLng) * 1.28;
    return {
        waypoints: fallbackPath,
        distanceM: distM,
        durationSec: Math.max(20, Math.round(distM / 8.33)),
        source: "Street Grid Fallback",
    };
}

function resamplePath(points, stepMeters = 8.0) {
    if (!points || points.length < 2) return points;
    const result = [points[0]];
    let accumulated = 0;

    for (let i = 0; i < points.length - 1; i++) {
        const p1 = points[i];
        const p2 = points[i + 1];
        const segDist = calculateDistanceM(p1[0], p1[1], p2[0], p2[1]);
        if (segDist <= 0.001) continue;

        let curDist = stepMeters - accumulated;
        while (curDist < segDist) {
            const t = curDist / segDist;
            const lat = p1[0] + (p2[0] - p1[0]) * t;
            const lng = p1[1] + (p2[1] - p1[1]) * t;
            result.push([lat, lng]);
            curDist += stepMeters;
        }
        accumulated = segDist - (curDist - stepMeters);
    }
    result.push(points[points.length - 1]);
    return result;
}

function generateStreetGridPath(start, end, numIntermediateSteps = 80) {
    const [lat1, lng1] = start;
    const [lat2, lng2] = end;

    const midLat = lat1 + (lat2 - lat1) * 0.55;
    const midLng = lng1 + (lng2 - lng1) * 0.45;

    const corner1 = [lat1, midLng];
    const corner2 = [midLat, lng2];

    const anchors = [start, corner1, corner2, end];
    const path = [];

    for (let i = 0; i < anchors.length - 1; i++) {
        const pA = anchors[i];
        const pB = anchors[i + 1];
        const stepsPerLeg = Math.floor(numIntermediateSteps / (anchors.length - 1));

        for (let s = 0; s < stepsPerLeg; s++) {
            const t = s / stepsPerLeg;
            const lat = pA[0] + (pB[0] - pA[0]) * t;
            const lng = pA[1] + (pB[1] - pA[1]) * t;
            path.push([lat, lng]);
        }
    }
    path.push(end);
    return path;
}

function calculateDistanceM(lat1, lon1, lat2, lon2) {
    const R = 6371000;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a =
        Math.sin(dLat / 2) * Math.sin(dLat / 2) +
        Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
        Math.sin(dLon / 2) * Math.sin(dLon / 2);
    const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    return R * c;
}

function calculateBearing(lat1, lon1, lat2, lon2) {
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const y = Math.sin(dLon) * Math.cos(lat2 * Math.PI / 180);
    const x = Math.cos(lat1 * Math.PI / 180) * Math.sin(lat2 * Math.PI / 180) -
        Math.sin(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.cos(dLon);
    let brng = Math.atan2(y, x) * 180 / Math.PI;
    return (brng + 360) % 360;
}

// -------------------------------------------------------------
// Interactive Ride Booking & Multi-Phase Drive Simulation
// -------------------------------------------------------------
async function triggerUberRide() {
    const btn = document.getElementById("btn-request-uber");
    btn.disabled = true;

    // Switch Left Panel View: Booking -> Active Trip
    switchPanelView("view-trip");
    clearRouteGraphics();
    clearCandidateDistances();

    logTerminal(`Initiating H3 hexagonal dispatch for ${selectedTier}...`, "trip");

    // 1. Trigger Visual H3 Ripple Expansion around Pickup
    visualizeHexRipple(currentPickup, parseInt(document.getElementById("slider-kring").value));

    // 2. Call Backend Dispatch API
    let dispatchRes;
    try {
        const response = await fetch("/api/v1/dispatch/request", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                rider_id: "uber-rider-" + Math.floor(Math.random() * 8999 + 1000),
                pickup_lat: currentPickup[0],
                pickup_lng: currentPickup[1],
                dropoff_lat: currentDropoff[0],
                dropoff_lng: currentDropoff[1],
                max_k_ring: parseInt(document.getElementById("slider-kring").value),
            }),
        });
        dispatchRes = await response.json();
    } catch (e) {
        logTerminal(`Dispatch API error: ${e}`, "sys");
        switchPanelView("view-booking");
        btn.disabled = false;
        return;
    }

    if (!dispatchRes.success) {
        logTerminal("No available drivers in search radius. Expanding radius...", "sys");
        setTimeout(() => {
            switchPanelView("view-booking");
            btn.disabled = false;
        }, 2500);
        return;
    }

    currentTripData = dispatchRes;
    currentAuditData = dispatchRes.audit || {
        trip_id: dispatchRes.trip_id,
        candidates_matrix: dispatchRes.candidates_matrix || [],
        mathematical_rationale: dispatchRes.mathematical_rationale || "",
    };

    const matchedDriver = dispatchRes.matched_driver;
    const tripId = dispatchRes.trip_id;
    const candidates = dispatchRes.candidates_matrix || currentAuditData.candidates_matrix || [];

    logTerminal(
        `Matched nearby Driver ${matchedDriver.driver_id.slice(0, 8)} (${matchedDriver.extra_meta?.name || "Driver"}) via Redis Lua script in ${dispatchRes.latency_ms}ms`,
        "match"
    );

    // Update Left Panel Driver Card
    document.getElementById("driver-name-val").textContent = matchedDriver.extra_meta?.name || "Dmitri Volkov";
    document.getElementById("driver-rating-val").textContent = matchedDriver.rating || "4.95";
    document.getElementById("driver-vehicle-val").textContent = `${matchedDriver.extra_meta?.vehicle_model || "BMW i4"} • Electric Blue`;
    document.getElementById("driver-plate-val").textContent = matchedDriver.extra_meta?.license_plate || "7HEX005";
    document.getElementById("driver-hex-val").textContent = matchedDriver.cell;

    // Update Audit Trigger Card Subtitle
    if (candidates.length > 1) {
        const runnerUp = candidates[1];
        const distSaved = Math.round(runnerUp.distance_m - candidates[0].distance_m);
        document.getElementById("audit-summary-sub").textContent = `Rank #1 of ${candidates.length} cabs • Saved ${distSaved > 0 ? distSaved : 0}m vs Runner-up`;
    } else {
        document.getElementById("audit-summary-sub").textContent = `Rank #1 • Evaluated ${candidates.length} active fleet cabs`;
    }

    // Render Candidate Distances on Map (Data-Driven Visual Rationale)
    renderCandidateDistanceLines(currentPickup, candidates);

    // Find car start position: use existing marker position if available!
    let driverStart = [matchedDriver.lat, matchedDriver.lng];
    if (driverMarkers[matchedDriver.driver_id]) {
        const existingLatLng = driverMarkers[matchedDriver.driver_id].getLatLng();
        driverStart = [existingLatLng.lat, existingLatLng.lng];
    }

    // =========================================================================
    // Phase 2: Driver En Route to Customer (OSRM Predefined Road Routing)
    // =========================================================================
    document.getElementById("trip-stage-title").textContent = "Driver is on the way to you";
    document.getElementById("prog-trip-status").textContent = "Picking up customer";

    logTerminal("Fetching real road route from OpenFreeMap/OSRM for driver pickup...", "trip");
    const pickupRoadRoute = await fetchRoadRoute(driverStart[0], driverStart[1], currentPickup[0], currentPickup[1]);
    const pickupPath = pickupRoadRoute.waypoints;

    // Render Clean Glowing Pickup Route Line
    renderCleanRouteLine(pickupPath, "#00D2FF", true);

    // Frame the pickup journey with left-panel offset
    fitMapToRoute(driverStart, currentPickup);

    // Smoothly animate the EXACT VISIBLE CAR driving along real roads to Customer
    const pickupDuration = Math.max(3500, Math.min(8000, (pickupRoadRoute.distanceM / 20.0) * 1000 / simulationSpeedMultiplier));
    await animateCarAlongPath(matchedDriver.driver_id, pickupPath, {
        onProgress: (p, distRem, currentPos) => {
            document.getElementById("trip-distance-rem").textContent = `${Math.round(distRem)} m away`;
            const pct = Math.round(p * 50);
            document.getElementById("progress-bar-line").style.width = `${pct}%`;
            document.getElementById("progress-bar-car").style.left = `${pct}%`;
            sendTelemetryPing(matchedDriver.driver_id, currentPos[0], currentPos[1], "DISPATCH_OFFERED");
        },
        durationMs: pickupDuration,
    });

    // =========================================================================
    // Phase 3: Customer Picked Up & Driving to Destination (OSRM Real Roads)
    // =========================================================================
    logTerminal(`Customer picked up! En route to destination via predefined roads...`, "trip");
    document.getElementById("trip-stage-title").textContent = "En Route to Destination";
    document.getElementById("prog-trip-status").textContent = "On Trip";
    document.getElementById("trip-stage-pill").style.borderColor = "rgba(39, 110, 241, 0.4)";
    document.getElementById("trip-stage-pill").style.color = "#60A5FA";

    createPulseAura(currentPickup, "#06C167");

    // Clear pickup line and candidate lines so destination view is pristine
    clearRouteGraphics();
    clearCandidateDistances();

    logTerminal("Fetching real road network waypoints to destination...", "trip");
    const destRoadRoute = await fetchRoadRoute(currentPickup[0], currentPickup[1], currentDropoff[0], currentDropoff[1]);
    const tripPath = destRoadRoute.waypoints;

    renderCleanRouteLine(tripPath, "#276EF1", false);
    fitMapToRoute(currentPickup, currentDropoff);
    tripStartTime = Date.now();

    const tripDuration = Math.max(4500, Math.min(10000, (destRoadRoute.distanceM / 20.0) * 1000 / simulationSpeedMultiplier));
    await animateCarAlongPath(matchedDriver.driver_id, tripPath, {
        onProgress: (p, distRem, currentPos) => {
            document.getElementById("trip-distance-rem").textContent = `${(distRem / 1000).toFixed(1)} km left`;
            const pct = 50 + Math.round(p * 50);
            document.getElementById("progress-bar-line").style.width = `${pct}%`;
            document.getElementById("progress-bar-car").style.left = `${pct}%`;
            sendTelemetryPing(matchedDriver.driver_id, currentPos[0], currentPos[1], "ON_TRIP");
        },
        durationMs: tripDuration,
    });

    // =========================================================================
    // Phase 4: Arrival & Trip Completion
    // =========================================================================
    logTerminal(`Arrived at destination. Event persisted to TimescaleDB.`, "trip");
    createPulseAura(currentDropoff, "#38BDF8");

    // Complete trip on backend
    try {
        await fetch("/api/v1/dispatch/complete", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ trip_id: tripId }),
        });
    } catch (e) {}

    // Switch Left Panel View: Active Trip -> Receipt
    switchPanelView("view-receipt");
    const elapsedSec = Math.round((Date.now() - tripStartTime) / 1000);
    const finalDistKm = (destRoadRoute.distanceM / 1000.0).toFixed(1);

    document.getElementById("receipt-fare").textContent = document.getElementById("fare-uberx").textContent;
    document.getElementById("receipt-dist").textContent = `${finalDistKm} km`;
    document.getElementById("receipt-duration").textContent = `${Math.floor(elapsedSec / 60)}m ${elapsedSec % 60}s`;
    document.getElementById("receipt-latency").textContent = `${dispatchRes.latency_ms} ms`;
}

// -------------------------------------------------------------
// Candidate Distance Lines (Explainability on Map)
// -------------------------------------------------------------
function renderCandidateDistanceLines(pickup, candidates) {
    clearCandidateDistances();
    if (!candidates || candidates.length === 0) return;

    candidates.forEach(cand => {
        const cabLatLng = [cand.lat, cand.lng];
        const isWinner = cand.is_selected;

        // Line from pickup to candidate cab
        const line = L.polyline([pickup, cabLatLng], {
            color: isWinner ? "#06C167" : "#FFFFFF",
            weight: isWinner ? 2.5 : 1.2,
            opacity: isWinner ? 0.85 : 0.28,
            dashArray: isWinner ? "6, 8" : "3, 6",
        });

        // Floating badge at line midpoint
        const midLat = (pickup[0] + cand.lat) / 2.0;
        const midLng = (pickup[1] + cand.lng) / 2.0;

        const badgeHtml = isWinner
            ? `<div class="cab-dist-badge chosen"><i class="fa-solid fa-check"></i> ${cand.name}: ${Math.round(cand.distance_m)}m (Selected)</div>`
            : `<div class="cab-dist-badge">${cand.name}: ${Math.round(cand.distance_m)}m (+${Math.round(cand.distance_diff_vs_selected_m)}m)</div>`;

        const badgeIcon = L.divIcon({
            className: "",
            html: badgeHtml,
            iconSize: [160, 22],
            iconAnchor: [80, 11],
        });

        const badgeMarker = L.marker([midLat, midLng], { icon: badgeIcon });

        if (showCandidateDistances) {
            line.addTo(map);
            badgeMarker.addTo(map);
        }

        candidateDistanceLayers.push(line, badgeMarker);
    });
}

function clearCandidateDistances() {
    candidateDistanceLayers.forEach(l => map.removeLayer(l));
    candidateDistanceLayers = [];
}

// -------------------------------------------------------------
// Mathematical Dispatch Audit Modal (Data-Driven Explainability)
// -------------------------------------------------------------
function openAuditModal() {
    const modal = document.getElementById("audit-modal");
    if (!modal) return;

    const data = currentAuditData || (currentTripData ? (currentTripData.audit || currentTripData) : null);
    if (!data) {
        alert("No dispatch event has occurred yet. Request a ride first to view real-time candidate matrix.");
        return;
    }

    const tripId = data.trip_id || "TRIP-LIVE";
    const candidates = data.candidates_matrix || [];
    const rationale = data.mathematical_rationale || "All candidate cabs evaluated across physical road distance, ETA, and compass heading alignment.";

    document.getElementById("modal-trip-meta").textContent = `Trip ID: ${tripId.slice(0, 12)}... • Evaluated ${candidates.length} Available Cabs in Real-Time`;
    document.getElementById("audit-rationale-text").textContent = rationale;

    const tbody = document.getElementById("audit-table-body");
    tbody.innerHTML = "";

    candidates.forEach(c => {
        const tr = document.createElement("tr");
        if (c.is_selected) tr.classList.add("selected-row");

        const deltaDistStr = c.is_selected ? "—" : `+${Math.round(c.distance_diff_vs_selected_m)} m`;
        const deltaColor = c.is_selected ? "#a7f3d0" : "#f87171";

        tr.innerHTML = `
            <td>
                <span class="badge-rank ${c.is_selected ? 'gold' : ''}">#${c.rank}</span>
            </td>
            <td>
                <strong>${c.name}</strong><br>
                <span style="color: #a6a6a6; font-size: 0.7rem;">${c.vehicle_model} • ${c.license_plate}</span>
            </td>
            <td><strong>${Math.round(c.distance_m)} m</strong></td>
            <td style="color: ${deltaColor}; font-weight: 600;">${deltaDistStr}</td>
            <td>~${c.eta_seconds} s</td>
            <td>${c.bearing_diff_deg !== undefined ? c.bearing_diff_deg : 0}°</td>
            <td><strong>${c.final_score.toFixed(4)}</strong></td>
            <td>
                <span class="${c.is_selected ? 'badge-status-chosen' : 'badge-status-other'}">
                    ${c.status || (c.is_selected ? 'SELECTED (Winner)' : 'EVALUATED')}
                </span>
            </td>
        `;

        // Interactive row click: pan map to that cab and create a pulse aura
        tr.addEventListener("click", () => {
            if (c.lat && c.lng) {
                map.panTo([c.lat, c.lng]);
                createPulseAura([c.lat, c.lng], c.is_selected ? "#06C167" : "#00D2FF");
            }
        });

        tbody.appendChild(tr);
    });

    modal.classList.remove("hidden");
}

function closeAuditModal() {
    const modal = document.getElementById("audit-modal");
    if (modal) modal.classList.add("hidden");
}

// -------------------------------------------------------------
// Smooth 60 FPS Car Driving Animation
// -------------------------------------------------------------
function animateCarAlongPath(driverId, pathCoords, options) {
    return new Promise((resolve) => {
        const startTime = performance.now();
        const duration = options.durationMs || 7000;
        const totalPoints = pathCoords.length;

        function step(now) {
            const elapsed = now - startTime;
            const progress = Math.min(1.0, elapsed / duration);

            const exactIdx = progress * (totalPoints - 1);
            const idxA = Math.floor(exactIdx);
            const idxB = Math.min(totalPoints - 1, idxA + 1);
            const frac = exactIdx - idxA;

            const pA = pathCoords[idxA];
            const pB = pathCoords[idxB];

            const curLat = pA[0] + (pB[0] - pA[0]) * frac;
            const curLng = pA[1] + (pB[1] - pA[1]) * frac;
            const heading = calculateBearing(pA[0], pA[1], pB[0], pB[1]);

            updateCarMarker(driverId, curLat, curLng, heading, "ON_TRIP");

            const lastPt = pathCoords[pathCoords.length - 1];
            const distRem = calculateDistanceM(curLat, curLng, lastPt[0], lastPt[1]);

            if (options.onProgress) {
                options.onProgress(progress, distRem, [curLat, curLng]);
            }

            if (progress < 1.0) {
                activeTripAnimId = requestAnimationFrame(step);
            } else {
                activeTripAnimId = null;
                resolve();
            }
        }

        activeTripAnimId = requestAnimationFrame(step);
    });
}

// -------------------------------------------------------------
// Clean Route Graphics & Hex Visualizations
// -------------------------------------------------------------
function renderCleanRouteLine(pathCoords, colorHex = "#276EF1", isDashed = false) {
    // 1. Soft Ambient Underlay Glow
    const glowLine = L.polyline(pathCoords, {
        color: colorHex,
        weight: 8,
        opacity: 0.35,
        lineCap: "round",
        lineJoin: "round",
    }).addTo(map);

    // 2. Crisp Core Route Line
    const coreLine = L.polyline(pathCoords, {
        color: "#FFFFFF",
        weight: isDashed ? 3 : 3.5,
        opacity: 0.95,
        dashArray: isDashed ? "8, 12" : null,
        lineCap: "round",
        lineJoin: "round",
    }).addTo(map);

    routeLayers.push(glowLine, coreLine);
}

function clearRouteGraphics() {
    routeLayers.forEach(l => map.removeLayer(l));
    routeLayers = [];
    rippleLayers.forEach(l => map.removeLayer(l));
    rippleLayers = [];
}

async function visualizeHexRipple(centerCoords, maxK = 3) {
    try {
        const res = await fetch(`/api/v1/spatial/kring?lat=${centerCoords[0]}&lng=${centerCoords[1]}&k=${maxK}&resolution=8`);
        const data = await res.json();

        const rippleLayer = L.geoJSON(data, {
            style: (feature) => {
                const isCenter = feature.properties.is_center;
                const hops = feature.properties.grid_distance_hops || 0;
                return {
                    color: isCenter ? "#06C167" : "#00D2FF",
                    weight: isCenter ? 2.5 : 1.5,
                    fillColor: isCenter ? "#06C167" : "#00D2FF",
                    fillOpacity: Math.max(0.04, 0.22 - (hops * 0.05)),
                    dashArray: isCenter ? null : "4, 6",
                };
            }
        }).addTo(map);

        rippleLayers.push(rippleLayer);
    } catch (e) {
        console.error("Failed to render H3 ripple", e);
    }
}

function createPulseAura(coords, color = "#00D2FF") {
    const aura = L.circle(coords, {
        radius: 60,
        color: color,
        fillColor: color,
        fillOpacity: 0.5,
        weight: 2,
    }).addTo(map);

    let rad = 60;
    let op = 0.5;
    const interval = setInterval(() => {
        rad += 30;
        op -= 0.08;
        if (op <= 0) {
            clearInterval(interval);
            map.removeLayer(aura);
        } else {
            aura.setRadius(rad);
            aura.setStyle({ fillOpacity: op, opacity: op });
        }
    }, 60);
}

// -------------------------------------------------------------
// Autonomous Fleet Cruising Simulation
// -------------------------------------------------------------
function toggleFleetSimulation() {
    isFleetSimActive = !isFleetSimActive;
    const btnText = document.getElementById("sim-btn-text");
    const btn = document.getElementById("btn-toggle-sim");

    if (isFleetSimActive) {
        btnText.textContent = "Pause Fleet";
        btn.classList.add("active");
        logTerminal("Autonomous Uber fleet cruising started across San Francisco.", "sys");

        fleetSimulationInterval = setInterval(() => {
            activeDriversList.forEach(car => {
                // Don't move the car if it's currently on an active dispatched trip!
                if (currentTripData && currentTripData.matched_driver.driver_id === car.driver_id) {
                    return;
                }

                const heading = car.heading || Math.floor(Math.random() * 360);
                const rad = heading * Math.PI / 180.0;
                car.lat += Math.cos(rad) * 0.0003;
                car.lng += Math.sin(rad) * 0.0003;

                // Random slight street turn
                if (Math.random() < 0.25) {
                    car.heading = (heading + (Math.random() * 40 - 20) + 360) % 360;
                } else {
                    car.heading = heading;
                }

                updateCarMarker(car.driver_id, car.lat, car.lng, car.heading, "IDLE", car.name, car.vehicle_model);
                sendTelemetryPing(car.driver_id, car.lat, car.lng, "IDLE");
            });
        }, 1500);
    } else {
        btnText.textContent = "Start Fleet";
        btn.classList.remove("active");
        if (fleetSimulationInterval) {
            clearInterval(fleetSimulationInterval);
            fleetSimulationInterval = null;
        }
        logTerminal("Autonomous fleet simulation paused.", "sys");
    }
}

async function sendTelemetryPing(driverId, lat, lng, status = "IDLE") {
    try {
        await fetch("/api/v1/drivers/telemetry", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                driver_id: driverId,
                lat: lat,
                lng: lng,
                speed: 32.0,
                heading: 90.0,
                status: status,
            }),
        });
    } catch (e) {}
}

// -------------------------------------------------------------
// Active H3 Grid Overlay
// -------------------------------------------------------------
async function loadActiveHexGrid() {
    try {
        const res = await fetch("/api/v1/spatial/cells");
        const data = await res.json();

        Object.values(hexLayers).forEach(l => map.removeLayer(l));
        hexLayers = {};

        if (data.features) {
            data.features.forEach(f => {
                const cell = f.properties.cell;
                const poly = L.geoJSON(f, {
                    style: {
                        color: "#00D2FF",
                        weight: 1.2,
                        fillColor: "#0284C7",
                        fillOpacity: 0.08,
                    }
                }).addTo(map);

                poly.on("click", () => inspectCell(cell));
                hexLayers[cell] = poly;
            });
            document.getElementById("stat-cells").textContent = data.features.length;
        }
    } catch (e) {}
}

// -------------------------------------------------------------
// Inspectors & UI Helpers
// -------------------------------------------------------------
function switchPanelView(viewId) {
    ["view-booking", "view-trip", "view-receipt"].forEach(id => {
        const el = document.getElementById(id);
        if (id === viewId) el.classList.remove("hidden");
        else el.classList.add("hidden");
    });
}

async function inspectPoint(lat, lng) {
    try {
        const res = await (await fetch(`/api/v1/spatial/resolve?lat=${lat}&lng=${lng}&resolution=8`)).json();
        inspectCell(res.h3_cell, res);
    } catch (e) {}
}

async function inspectCell(cellId, cachedInfo = null) {
    document.getElementById("dev-drawer").classList.remove("hidden");
    const container = document.getElementById("inspector-cell-info");

    try {
        const drivers = await (await fetch(`/api/v1/drivers?cell=${cellId}`)).json();
        container.innerHTML = `
            <div><strong>H3 Index:</strong> <code>${cellId}</code></div>
            <div><strong>Resolution:</strong> Res 8 (~460m edge)</div>
            <div><strong>Available Drivers:</strong> ${drivers.length}</div>
        `;
    } catch (e) {
        container.innerHTML = `<div>Cell: <code>${cellId}</code></div>`;
    }
}

function inspectDriver(driverId, name, vehicle) {
    document.getElementById("dev-drawer").classList.remove("hidden");
    const container = document.getElementById("inspector-cell-info");
    container.innerHTML = `
        <div><strong>Driver:</strong> ${name}</div>
        <div><strong>Vehicle:</strong> ${vehicle}</div>
        <div><strong>Driver ID:</strong> <code>${driverId.slice(0, 12)}...</code></div>
        <div><strong>Status:</strong> LIVE / TELEMETRY ACTIVE</div>
    `;
}

function logTerminal(msg, category = "sys") {
    const log = document.getElementById("terminal-event-log");
    if (!log) return;
    const line = document.createElement("div");
    line.className = `log-line ${category}`;
    const time = new Date().toLocaleTimeString();
    line.textContent = `[${time}] ${msg}`;
    log.appendChild(line);
    log.scrollTop = log.scrollHeight;
}

function setMapClickMode(mode) {
    mapClickMode = mode;
    const body = document.body;
    if (mode) {
        body.style.cursor = "crosshair";
    } else {
        body.style.cursor = "default";
    }
}

// -------------------------------------------------------------
// Event Listeners
// -------------------------------------------------------------
function initEventListeners() {
    // Preset Route Selection
    document.querySelectorAll(".preset-pill").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".preset-pill").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");

            currentPresetKey = btn.dataset.preset;
            const preset = ROUTE_PRESETS[currentPresetKey];
            currentPickup = preset.pickup;
            currentDropoff = preset.dropoff;

            document.getElementById("input-pickup").value = preset.pickupName;
            document.getElementById("input-dropoff").value = preset.dropoffName;

            updateRouteMarkers(currentPickup, currentDropoff);
            clearRouteGraphics();
            clearCandidateDistances();
        });
    });

    // Tier Selection (UberX, Comfort, Black)
    document.querySelectorAll(".ride-tier-card").forEach(card => {
        card.addEventListener("click", () => {
            document.querySelectorAll(".ride-tier-card").forEach(c => c.classList.remove("selected"));
            card.classList.add("selected");
            selectedTier = card.dataset.tier;
            selectedMultiplier = parseFloat(card.dataset.multiplier);
            document.getElementById("cta-tier-label").textContent = selectedTier;
        });
    });

    // Speed Multiplier Buttons (1x, 2x, 4x)
    document.querySelectorAll(".speed-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            document.querySelectorAll(".speed-btn").forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            simulationSpeedMultiplier = parseFloat(btn.dataset.speed);
        });
    });

    // Request Uber Button
    document.getElementById("btn-request-uber").addEventListener("click", triggerUberRide);

    // Cancel Trip
    document.getElementById("btn-cancel-trip").addEventListener("click", () => {
        if (activeTripAnimId) {
            cancelAnimationFrame(activeTripAnimId);
            activeTripAnimId = null;
        }
        switchPanelView("view-booking");
        document.getElementById("btn-request-uber").disabled = false;
        clearRouteGraphics();
        clearCandidateDistances();
        currentTripData = null;
        logTerminal("Ride cancelled by user.", "sys");
    });

    // Instant Arrive / Skip
    document.getElementById("btn-skip-trip").addEventListener("click", () => {
        if (activeTripAnimId) {
            cancelAnimationFrame(activeTripAnimId);
            activeTripAnimId = null;
        }
        if (currentTripData) {
            updateCarMarker(currentTripData.matched_driver.driver_id, currentDropoff[0], currentDropoff[1], 0, "IDLE");
        }
        clearCandidateDistances();
        switchPanelView("view-receipt");
        logTerminal("Simulation fast-forwarded to destination arrival.", "trip");
    });

    // Book Another / Done
    document.getElementById("btn-book-another").addEventListener("click", () => {
        switchPanelView("view-booking");
        document.getElementById("btn-request-uber").disabled = false;
        clearRouteGraphics();
        clearCandidateDistances();
        currentTripData = null;
    });

    // Toggle Fleet Cruising
    document.getElementById("btn-toggle-sim").addEventListener("click", toggleFleetSimulation);

    // Toggle H3 Hexagons
    document.getElementById("btn-toggle-hexes").addEventListener("click", (e) => {
        e.currentTarget.classList.toggle("active");
        const show = e.currentTarget.classList.contains("active");
        Object.values(hexLayers).forEach(l => show ? map.addLayer(l) : map.removeLayer(l));
    });

    // Toggle Fleet Distances to all Cabs
    const btnDistances = document.getElementById("btn-toggle-distances");
    if (btnDistances) {
        btnDistances.addEventListener("click", (e) => {
            e.currentTarget.classList.toggle("active");
            showCandidateDistances = e.currentTarget.classList.contains("active");
            candidateDistanceLayers.forEach(l => {
                if (showCandidateDistances) map.addLayer(l);
                else map.removeLayer(l);
            });
        });
    }

    // Mathematical Audit Modal Triggers & Close
    const btnOpenAudit = document.getElementById("btn-open-audit");
    if (btnOpenAudit) btnOpenAudit.addEventListener("click", openAuditModal);

    const btnReceiptAudit = document.getElementById("btn-receipt-open-audit");
    if (btnReceiptAudit) btnReceiptAudit.addEventListener("click", openAuditModal);

    const btnCloseAudit = document.getElementById("btn-close-audit-modal");
    if (btnCloseAudit) btnCloseAudit.addEventListener("click", closeAuditModal);

    const auditModalBackdrop = document.getElementById("audit-modal");
    if (auditModalBackdrop) {
        auditModalBackdrop.addEventListener("click", (e) => {
            if (e.target === auditModalBackdrop) closeAuditModal();
        });
    }

    // Drawer Toggles
    document.getElementById("btn-toggle-drawer").addEventListener("click", () => {
        document.getElementById("dev-drawer").classList.toggle("hidden");
    });
    document.getElementById("btn-close-drawer").addEventListener("click", () => {
        document.getElementById("dev-drawer").classList.add("hidden");
    });

    // Click on map to set pickup / dropoff
    document.getElementById("btn-set-pickup-map").addEventListener("click", () => {
        setMapClickMode("pickup");
        alert("Click anywhere on the map to set your Pickup location.");
    });
    document.getElementById("btn-set-dropoff-map").addEventListener("click", () => {
        setMapClickMode("dropoff");
        alert("Click anywhere on the map to set your Destination.");
    });

    // K-ring slider
    document.getElementById("slider-kring").addEventListener("input", (e) => {
        const val = e.target.value;
        const cellCount = 1 + 3 * val * (parseInt(val) + 1);
        document.getElementById("val-kring").textContent = `${val} rings (${cellCount} cells)`;
    });
}
