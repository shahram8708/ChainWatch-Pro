let dashboardMap = null;
let dashboardMarkerLayer = null;
let dashboardApiUrl = "/api/v1/shipments/map-data";
const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute("content") || "";

function darkenHexColor(hex, percent = 20) {
    const cleaned = (hex || "#000000").replace("#", "");
    const num = parseInt(cleaned, 16);
    const amt = Math.round(2.55 * percent);
    const r = Math.max((num >> 16) - amt, 0);
    const g = Math.max(((num >> 8) & 0x00ff) - amt, 0);
    const b = Math.max((num & 0x0000ff) - amt, 0);
    return `#${(0x1000000 + (r << 16) + (g << 8) + b).toString(16).slice(1)}`;
}

function setMapOverlay(message, visible) {
    const overlay = document.getElementById("shipment-map-overlay");
    if (!overlay) {
        return;
    }
    overlay.textContent = message || "";
    overlay.classList.toggle("d-none", !visible);
}

function createLegendControl(L) {
    return L.control({ position: "bottomright" });
}

function buildLegendHTML() {
    return `
        <div class="map-legend">
            <div class="map-legend-item"><span class="map-legend-dot" style="background:#D32F2F"></span>Critical</div>
            <div class="map-legend-item"><span class="map-legend-dot" style="background:#FF8C00"></span>Warning</div>
            <div class="map-legend-item"><span class="map-legend-dot" style="background:#F59E0B"></span>Watch</div>
            <div class="map-legend-item"><span class="map-legend-dot" style="background:#00A86B"></span>On Track</div>
        </div>
    `;
}

function addLegendToMap(L, map) {
    const legend = createLegendControl(L);
    legend.onAdd = () => {
        const div = L.DomUtil.create("div");
        div.innerHTML = buildLegendHTML();
        return div;
    };
    legend.addTo(map);
}

function markerPopup(shipment) {
    const eta = shipment.estimated_arrival
        ? new Date(shipment.estimated_arrival).toLocaleString()
        : "Pending";

    return `
        <div class="small">
            <div class="fw-semibold mb-1">${shipment.external_reference || shipment.id}</div>
            <div><strong>Carrier:</strong> ${shipment.carrier_name || "Unassigned"}</div>
            <div><strong>Status:</strong> ${String(shipment.status || "").replace(/_/g, " ")}</div>
            <div><strong>DRS:</strong> ${Number(shipment.drs || 0).toFixed(1)} (${shipment.risk_level})</div>
            <div><strong>Route:</strong> ${shipment.origin_port_code || "-"} -> ${shipment.destination_port_code || "-"}</div>
            <div><strong>ETA:</strong> ${eta}</div>
            <a class="btn btn-sm btn-primary mt-2" href="/shipments/${shipment.id}">View Details →</a>
        </div>
    `;
}

function markerTooltip(shipment) {
    return `
        <div class="small">
            <div class="fw-semibold">${shipment.external_reference || shipment.id}</div>
            <div>${shipment.carrier_name || "Unassigned"}</div>
            <div>DRS ${Number(shipment.drs || 0).toFixed(1)} · ${String(shipment.risk_level || "").toUpperCase()}</div>
            <div>${shipment.current_location_name || "Location pending"}</div>
        </div>
    `;
}

function renderDashboardMarkers(L, shipments) {
    if (!dashboardMap || !dashboardMarkerLayer) {
        return;
    }

    dashboardMarkerLayer.clearLayers();

    if (!shipments || shipments.length === 0) {
        setMapOverlay(
            "No GPS data available yet. GPS coordinates are updated when carrier tracking data is received.",
            true
        );
        return;
    }

    setMapOverlay("", false);

    const bounds = [];

    shipments.forEach((shipment) => {
        const lat = Number(shipment.lat);
        const lng = Number(shipment.lng);
        if (Number.isNaN(lat) || Number.isNaN(lng)) {
            return;
        }

        bounds.push([lat, lng]);

        const fillColor = shipment.risk_color || "#00A86B";
        const borderColor = darkenHexColor(fillColor, 22);
        const radius = 8 + Number(shipment.drs || 0) / 20;

        const marker = L.circleMarker([lat, lng], {
            radius,
            fillColor,
            color: borderColor,
            fillOpacity: 0.85,
            weight: 2
        });

        marker.bindTooltip(markerTooltip(shipment), {
            direction: "top",
            opacity: 0.95,
            sticky: true
        });

        marker.bindPopup(markerPopup(shipment));
        marker.addTo(dashboardMarkerLayer);
    });

    if (bounds.length > 0) {
        dashboardMap.fitBounds(bounds, { padding: [30, 30], maxZoom: 6 });
    }
}

export async function refreshDashboardMap() {
    if (!dashboardMap || !window.L) {
        return;
    }

    try {
        const response = await fetch(dashboardApiUrl, {
            method: "GET",
            headers: {
                "Accept": "application/json",
                "X-Requested-With": "XMLHttpRequest",
                ...(csrfToken ? { "X-CSRFToken": csrfToken } : {})
            }
        });

        if (!response.ok) {
            throw new Error(`Map data request failed with ${response.status}`);
        }

        const payload = await response.json();
        renderDashboardMarkers(window.L, payload);
    } catch (error) {
        console.error("Unable to load dashboard map data", error);
        setMapOverlay("Unable to load map data. Refresh to retry.", true);
    }
}

export function initDashboardMap(mapElementId = "shipment-map", apiUrl = "/api/v1/shipments/map-data") {
    if (!window.L) {
        return;
    }

    const mapElement = document.getElementById(mapElementId);
    if (!mapElement) {
        return;
    }

    dashboardApiUrl = apiUrl;

    if (dashboardMap) {
        refreshDashboardMap();
        return;
    }

    dashboardMap = window.L.map(mapElementId, {
        zoomControl: true,
        worldCopyJump: true
    }).setView([20, 0], 2);

    window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 19,
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
    }).addTo(dashboardMap);

    dashboardMarkerLayer = window.L.layerGroup().addTo(dashboardMap);
    addLegendToMap(window.L, dashboardMap);

    refreshDashboardMap();
}

function normalizeCoordinates(point) {
    if (!point || !Array.isArray(point) || point.length < 2) {
        return null;
    }

    const lat = Number(point[0]);
    const lng = Number(point[1]);
    if (Number.isNaN(lat) || Number.isNaN(lng)) {
        return null;
    }
    if (lat < -90 || lat > 90 || lng < -180 || lng > 180) {
        return null;
    }
    return [lat, lng];
}

function renderRouteMapEmptyState(container, shipmentData) {
    const originCode = shipmentData?.origin?.code || "Origin";
    const destinationCode = shipmentData?.destination?.code || "Destination";
    const status = String(shipmentData?.status || "pending").replace(/_/g, " ");

    container.innerHTML = `
        <div class="h-100 d-flex align-items-center justify-content-center text-center px-3">
            <div>
                <svg width="220" height="80" viewBox="0 0 220 80" fill="none" aria-hidden="true">
                    <circle cx="25" cy="40" r="8" fill="#00A86B"></circle>
                    <line x1="34" y1="40" x2="186" y2="40" stroke="#7f8ea8" stroke-width="2" stroke-dasharray="6 5"></line>
                    <circle cx="195" cy="40" r="8" fill="#D32F2F"></circle>
                </svg>
                <p class="mb-1 fw-semibold">${originCode} → ${status} → ${destinationCode}</p>
                <p class="text-muted mb-0 small">No GPS data available yet. Coordinates will appear when carrier tracking sync completes.</p>
            </div>
        </div>
    `;
}

function createTriangleIcon(L) {
    return L.divIcon({
        className: "",
        html: '<svg width="16" height="16" viewBox="0 0 16 16" fill="none"><path d="M8 1.5L15 14.5H1L8 1.5Z" fill="#D32F2F"/></svg>',
        iconSize: [16, 16],
        iconAnchor: [8, 14],
        popupAnchor: [0, -12]
    });
}

function addRouteMapTiles(map, L) {
    const providers = [
        {
            name: "osm",
            url: "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        },
        {
            name: "carto",
            url: "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png",
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors &copy; CARTO'
        },
        {
            name: "osm-hot",
            url: "https://{s}.tile.openstreetmap.fr/hot/{z}/{x}/{y}.png",
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors, tiles by HOT'
        }
    ];

    let activeLayer = null;
    let switchToken = 0;

    const loadProvider = (providerIndex) => {
        if (providerIndex >= providers.length) {
            return;
        }

        const provider = providers[providerIndex];
        const token = ++switchToken;
        let tileLoaded = false;
        let tileErrorCount = 0;

        if (activeLayer && map.hasLayer(activeLayer)) {
            map.removeLayer(activeLayer);
        }

        const layer = L.tileLayer(provider.url, {
            maxZoom: 19,
            attribution: provider.attribution,
            crossOrigin: true
        });
        activeLayer = layer;

        const tryFallback = () => {
            if (token !== switchToken) {
                return;
            }
            loadProvider(providerIndex + 1);
        };

        layer.on("tileload", () => {
            tileLoaded = true;
        });

        layer.on("tileerror", () => {
            tileErrorCount += 1;
            if (!tileLoaded && tileErrorCount >= 4) {
                tryFallback();
            }
        });

        layer.addTo(map);

        window.setTimeout(() => {
            if (token !== switchToken) {
                return;
            }
            if (!tileLoaded && tileErrorCount > 0) {
                tryFallback();
            }
        }, 3500);
    };

    loadProvider(0);
}

export function initRouteMap(mapElementId, shipmentData = {}) {
    if (!window.L) {
        return;
    }

    const mapElement = document.getElementById(mapElementId);
    if (!mapElement) {
        return;
    }

    let currentPosition = normalizeCoordinates(shipmentData.current_position);
    const actualPath = (shipmentData.actual_path || []).map(normalizeCoordinates).filter(Boolean);
    if (!currentPosition && actualPath.length === 1) {
        currentPosition = actualPath[0];
    }

    const originCoordinates = normalizeCoordinates(shipmentData?.origin?.coordinates);
    const destinationCoordinates = normalizeCoordinates(shipmentData?.destination?.coordinates);
    const waypoints = (shipmentData.waypoints || []).map(normalizeCoordinates).filter(Boolean);

    const hasAnyPoint = Boolean(currentPosition) || actualPath.length > 0 || Boolean(originCoordinates) || Boolean(destinationCoordinates) || waypoints.length > 0;

    if (!hasAnyPoint) {
        renderRouteMapEmptyState(mapElement, shipmentData);
        return;
    }

    mapElement.innerHTML = "";

    const map = window.L.map(mapElementId, {
        zoomControl: true
    });
    addRouteMapTiles(map, window.L);

    const routePoints = [];
    if (originCoordinates) {
        routePoints.push(originCoordinates);
    }
    routePoints.push(...waypoints);
    if (destinationCoordinates) {
        routePoints.push(destinationCoordinates);
    }

    const bounds = [];

    if (routePoints.length >= 2) {
        const planned = window.L.polyline(routePoints, {
            color: "#8EA2BC",
            weight: 3,
            dashArray: "8 8",
            opacity: 0.9
        }).addTo(map);
        bounds.push(...planned.getLatLngs());
    }

    if (actualPath.length >= 2) {
        const traveled = window.L.polyline(actualPath, {
            color: "#0077CC",
            weight: 4,
            opacity: 0.95
        }).addTo(map);
        bounds.push(...traveled.getLatLngs());
    }

    if (originCoordinates) {
        window.L.marker(originCoordinates, {
            icon: window.L.divIcon({
                className: "",
                html: '<span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:#00A86B;border:2px solid #fff"></span>',
                iconSize: [12, 12],
                iconAnchor: [6, 6]
            })
        })
            .addTo(map)
            .bindPopup(`<strong>${shipmentData?.origin?.code || "Origin"}</strong><br>${shipmentData?.origin?.address || ""}`);
        bounds.push(originCoordinates);
    }

    if (destinationCoordinates) {
        window.L.marker(destinationCoordinates, {
            icon: window.L.divIcon({
                className: "",
                html: '<span style="display:inline-block;width:12px;height:12px;border-radius:50%;background:#D32F2F;border:2px solid #fff"></span>',
                iconSize: [12, 12],
                iconAnchor: [6, 6]
            })
        })
            .addTo(map)
            .bindPopup(`<strong>${shipmentData?.destination?.code || "Destination"}</strong><br>${shipmentData?.destination?.address || ""}`);
        bounds.push(destinationCoordinates);
    }

    if (currentPosition) {
        window.L.marker(currentPosition, {
            icon: window.L.divIcon({
                className: "",
                html: '<span class="cw-pulse-marker"></span>',
                iconSize: [14, 14],
                iconAnchor: [7, 7]
            })
        })
            .addTo(map)
            .bindPopup("Current shipment position");
        bounds.push(currentPosition);
    }

    const optionColors = {
        A: "#FF8C00",
        B: "#7C3AED",
        C: "#14B8A6"
    };

    const alternativeLayers = {};
    (shipmentData.route_alternatives || []).forEach((option) => {
        const coords = (option.coordinates || []).map(normalizeCoordinates).filter(Boolean);
        if (coords.length < 2) {
            return;
        }
        const optionLabel = String(option.option || option.option_label || "").toUpperCase();
        const layer = window.L.polyline(coords, {
            color: optionColors[optionLabel] || "#64748B",
            weight: 3,
            dashArray: "6 6",
            opacity: 0
        }).addTo(map);
        alternativeLayers[optionLabel] = layer;
        bounds.push(...coords);
    });

    document.querySelectorAll("[data-route-option]").forEach((toggle) => {
        toggle.addEventListener("change", () => {
            const label = String(toggle.getAttribute("data-route-option") || "").toUpperCase();
            const layer = alternativeLayers[label];
            if (!layer) {
                return;
            }
            layer.setStyle({ opacity: toggle.checked ? 0.85 : 0 });
        });
    });

    const triangleIcon = createTriangleIcon(window.L);
    const ehsSignals = Array.isArray(shipmentData.ehs_signals)
        ? shipmentData.ehs_signals
        : (shipmentData.ehs_signals && typeof shipmentData.ehs_signals === "object")
            ? Object.values(shipmentData.ehs_signals)
            : [];

    ehsSignals.forEach((signal) => {
        const coordinates = Array.isArray(signal?.coordinates)
            ? normalizeCoordinates(signal.coordinates)
            : normalizeCoordinates([signal?.lat, signal?.lng]);

        if (!coordinates) {
            return;
        }

        const description = signal?.description || signal?.title || "Hazard signal";
        window.L.marker(coordinates, { icon: triangleIcon })
            .addTo(map)
            .bindPopup(`<strong>Disruption Signal</strong><br>${description}`);
        bounds.push(coordinates);
    });

    if (bounds.length > 1) {
        map.fitBounds(bounds, { padding: [30, 30], maxZoom: 8 });
    } else if (bounds.length === 1) {
        map.setView(bounds[0], 6);
    } else {
        map.setView([20, 0], 2);
    }

    // Leaflet can initialize before layout settles; force a size recalculation.
    window.setTimeout(() => {
        map.invalidateSize();
    }, 120);
}

let riskHeatMap = null;
const riskHeatState = {
    uiBound: false,
    mapElementId: "risk-heat-map",
    shipmentRows: [],
    shipmentLayers: [],
    portRows: [],
    weatherRows: [],
    filters: {
        mode: "all",
        riskLevels: new Set(["critical", "warning", "watch", "green"]),
        carrier: "all"
    },
    overlays: {
        activeShipments: true,
        portCongestion: false,
        weatherAlerts: false
    },
    feeds: {
        portCongestionUrl: null,
        weatherAlertsUrl: null,
        portLoading: false,
        weatherLoading: false,
        portLoaded: false,
        weatherLoaded: false
    },
    layers: {
        shipments: null,
        shipmentAura: null,
        portCongestion: null,
        weatherAlerts: null
    }
};

function riskColor(level, score = 0) {
    const normalized = String(level || "").toLowerCase();
    if (normalized === "critical") {
        return "#D32F2F";
    }
    if (normalized === "warning") {
        return "#FF8C00";
    }
    if (normalized === "watch") {
        return "#F59E0B";
    }
    if (score >= 80) {
        return "#D32F2F";
    }
    if (score >= 60) {
        return "#FF8C00";
    }
    if (score >= 35) {
        return "#F59E0B";
    }
    return "#00A86B";
}

function createRiskPulseIcon(L, level, color) {
    const normalized = String(level || "green").toLowerCase();
    return L.divIcon({
        className: "risk-pulse-icon-wrapper",
        html: `<span class="risk-pulse-marker risk-${normalized}" style="--risk-color:${color}"></span>`,
        iconSize: [18, 18],
        iconAnchor: [9, 9],
        popupAnchor: [0, -14]
    });
}

function normalizeRiskHeatShipmentRows(shipmentRows) {
    return (shipmentRows || [])
        .map((row) => ({
            ...row,
            lat: Number(row.lat),
            lng: Number(row.lng),
            drs: Number(row.drs || 0),
            risk_level: String(row.risk_level || "green").toLowerCase(),
            mode_family: String(row.mode_family || row.mode || "").toLowerCase(),
            carrier_name: row.carrier_name || "Unassigned"
        }))
        .filter((row) => !Number.isNaN(row.lat) && !Number.isNaN(row.lng));
}

function updateRiskMapFilterUI() {
    const modeIndicator = document.getElementById("map-filter-mode-indicator");
    if (modeIndicator) {
        const mode = riskHeatState.filters.mode;
        modeIndicator.textContent = `Mode: ${mode === "all" ? "All" : mode.charAt(0).toUpperCase() + mode.slice(1)}`;
    }

    const modeButtons = document.querySelectorAll("[data-mode-filter]");
    modeButtons.forEach((button) => {
        const buttonMode = button.getAttribute("data-mode-filter") || "all";
        const active = buttonMode === riskHeatState.filters.mode;
        button.classList.toggle("btn-primary", active);
        button.classList.toggle("btn-outline-primary", !active);
    });
}

function updateRiskMapStats(visibleCount, totalCount) {
    const bar = document.getElementById("map-stats-bar");
    if (!bar) {
        return;
    }

    if (!riskHeatState.overlays.activeShipments) {
        bar.textContent = `Showing 0 of ${totalCount} active shipments (overlay hidden)`;
        return;
    }

    bar.textContent = `Showing ${visibleCount} of ${totalCount} active shipments`;
}

function shipmentMatchesRiskFilters(shipment) {
    if (!riskHeatState.filters.riskLevels.has(shipment.risk_level)) {
        return false;
    }

    if (riskHeatState.filters.mode !== "all" && shipment.mode_family !== riskHeatState.filters.mode) {
        return false;
    }

    if (riskHeatState.filters.carrier !== "all" && shipment.carrier_name !== riskHeatState.filters.carrier) {
        return false;
    }

    return true;
}

function openShipmentSlidePanel(shipment) {
    const panel = document.getElementById("shipment-slide-panel");
    const panelBody = document.getElementById("shipment-slide-panel-body");
    const panelTitle = document.getElementById("shipment-slide-panel-title");

    if (!panel || !panelBody) {
        return;
    }

    if (panelTitle) {
        panelTitle.textContent = shipment.external_reference || "Shipment Summary";
    }

    panel.classList.add("open");
    panelBody.innerHTML = '<div class="text-muted small">Loading shipment summary...</div>';

    fetch(`/risk-map/shipment-summary/${shipment.id}`, {
        method: "GET",
        headers: {
            "X-Requested-With": "XMLHttpRequest",
            "Accept": "text/html"
        }
    })
        .then((response) => {
            if (!response.ok) {
                throw new Error(`Failed loading shipment summary (${response.status})`);
            }
            return response.text();
        })
        .then((html) => {
            panelBody.innerHTML = html;
        })
        .catch((error) => {
            console.error("Unable to load shipment summary panel", error);
            panelBody.innerHTML = '<div class="text-danger small">Unable to load shipment details right now. Please retry.</div>';
        });
}

function closeShipmentSlidePanel() {
    const panel = document.getElementById("shipment-slide-panel");
    if (panel) {
        panel.classList.remove("open");
    }
}

function buildRiskHeatShipmentLayers(L) {
    riskHeatState.shipmentLayers = riskHeatState.shipmentRows.map((shipment) => {
        const fillColor = riskColor(shipment.risk_level, shipment.drs);
        const borderColor = darkenHexColor(fillColor, 24);
        const icon = createRiskPulseIcon(L, shipment.risk_level, fillColor);

        const marker = L.marker([shipment.lat, shipment.lng], { icon, riseOnHover: true })
            .bindTooltip(markerTooltip(shipment), {
                direction: "top",
                opacity: 0.95,
                sticky: true
            })
            .on("click", () => openShipmentSlidePanel(shipment));

        let aura = null;
        if (shipment.drs >= 60) {
            aura = L.circle([shipment.lat, shipment.lng], {
                radius: Math.max(25000, 12000 + shipment.drs * 850),
                color: borderColor,
                weight: 1,
                fillColor,
                fillOpacity: Math.min(0.32, 0.08 + shipment.drs / 350),
                interactive: false
            });
        }

        return {
            shipment,
            marker,
            aura
        };
    });
}

function applyRiskHeatShipmentFilters() {
    if (!riskHeatState.layers.shipments || !riskHeatState.layers.shipmentAura) {
        return;
    }

    riskHeatState.layers.shipments.clearLayers();
    riskHeatState.layers.shipmentAura.clearLayers();

    const visibleLayers = riskHeatState.shipmentLayers.filter((entry) => shipmentMatchesRiskFilters(entry.shipment));

    if (riskHeatState.overlays.activeShipments) {
        visibleLayers.forEach((entry) => {
            entry.marker.addTo(riskHeatState.layers.shipments);
            if (entry.aura) {
                entry.aura.addTo(riskHeatState.layers.shipmentAura);
            }
        });
    }

    updateRiskMapStats(riskHeatState.overlays.activeShipments ? visibleLayers.length : 0, riskHeatState.shipmentRows.length);
    updateRiskMapFilterUI();
}

function renderPortCongestionLayer(L) {
    if (!riskHeatState.layers.portCongestion) {
        return;
    }

    riskHeatState.layers.portCongestion.clearLayers();

    riskHeatState.portRows.forEach((zone) => {
        const lat = Number(zone.latitude);
        const lng = Number(zone.longitude);
        const score = Number(zone.congestion_score || 0);
        if (Number.isNaN(lat) || Number.isNaN(lng)) {
            return;
        }

        const color = riskColor(zone.congestion_level, score);
        const circle = L.circle([lat, lng], {
            radius: Math.max(18000, 8000 + score * 900),
            color,
            weight: 1,
            fillColor: color,
            fillOpacity: 0.18
        });

        const popup = `
            <div class="small">
                <div class="fw-semibold mb-1">${zone.port_name || zone.port_code || "Port"}</div>
                <div><strong>Port:</strong> ${zone.port_code || "-"}</div>
                <div><strong>Congestion:</strong> ${score.toFixed(1)} / 100</div>
                <div><strong>Level:</strong> ${String(zone.congestion_level || "").toUpperCase()}</div>
            </div>
        `;

        circle.bindPopup(popup);
        circle.addTo(riskHeatState.layers.portCongestion);
    });
}

function renderWeatherAlertsLayer(L) {
    if (!riskHeatState.layers.weatherAlerts) {
        return;
    }

    riskHeatState.layers.weatherAlerts.clearLayers();

    const icon = L.divIcon({
        className: "weather-alert-icon-wrapper",
        html: '<span class="weather-alert-marker">⚠</span>',
        iconSize: [20, 20],
        iconAnchor: [10, 10]
    });

    riskHeatState.weatherRows.forEach((alert) => {
        const lat = Number(alert.latitude);
        const lng = Number(alert.longitude);
        const score = Number(alert.risk_score || 0);
        if (Number.isNaN(lat) || Number.isNaN(lng)) {
            return;
        }

        const popup = `
            <div class="small">
                <div class="fw-semibold mb-1">Weather Alert</div>
                <div><strong>Risk:</strong> ${score.toFixed(1)} / 100</div>
                <div><strong>Description:</strong> ${alert.description || "Hazard detected"}</div>
                <div><strong>Type:</strong> ${String(alert.kind || "unknown").toUpperCase()}</div>
            </div>
        `;

        L.marker([lat, lng], { icon }).bindPopup(popup).addTo(riskHeatState.layers.weatherAlerts);
    });
}

function syncRiskHeatOverlayVisibility() {
    if (!riskHeatMap) {
        return;
    }

    const { portCongestion, weatherAlerts } = riskHeatState.layers;

    if (portCongestion) {
        if (riskHeatState.overlays.portCongestion) {
            if (!riskHeatMap.hasLayer(portCongestion)) {
                portCongestion.addTo(riskHeatMap);
            }
        } else if (riskHeatMap.hasLayer(portCongestion)) {
            riskHeatMap.removeLayer(portCongestion);
        }
    }

    if (weatherAlerts) {
        if (riskHeatState.overlays.weatherAlerts) {
            if (!riskHeatMap.hasLayer(weatherAlerts)) {
                weatherAlerts.addTo(riskHeatMap);
            }
        } else if (riskHeatMap.hasLayer(weatherAlerts)) {
            riskHeatMap.removeLayer(weatherAlerts);
        }
    }
}

async function fetchRiskMapJson(url) {
    const response = await fetch(url, {
        method: "GET",
        headers: {
            "Accept": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            ...(csrfToken ? { "X-CSRFToken": csrfToken } : {})
        }
    });

    if (!response.ok) {
        throw new Error(`Risk map feed request failed (${response.status})`);
    }

    return response.json();
}

async function refreshPortCongestionFeed(force = false) {
    const feedUrl = riskHeatState.feeds.portCongestionUrl;
    if (!feedUrl || riskHeatState.feeds.portLoading || !window.L) {
        return;
    }

    riskHeatState.feeds.portLoading = true;
    try {
        const url = new URL(feedUrl, window.location.origin);
        if (force) {
            url.searchParams.set("refresh", "1");
        }

        const payload = await fetchRiskMapJson(url.toString());
        if (Array.isArray(payload?.zones)) {
            riskHeatState.portRows = payload.zones;
            renderPortCongestionLayer(window.L);
            syncRiskHeatOverlayVisibility();
            riskHeatState.feeds.portLoaded = true;
        }
    } catch (error) {
        console.error("Unable to refresh port congestion feed", error);
    } finally {
        riskHeatState.feeds.portLoading = false;
    }
}

async function refreshWeatherAlertFeed(force = false) {
    const feedUrl = riskHeatState.feeds.weatherAlertsUrl;
    if (!feedUrl || riskHeatState.feeds.weatherLoading || !window.L) {
        return;
    }

    riskHeatState.feeds.weatherLoading = true;
    try {
        const url = new URL(feedUrl, window.location.origin);
        if (force) {
            url.searchParams.set("refresh", "1");
        }

        const payload = await fetchRiskMapJson(url.toString());
        if (Array.isArray(payload?.locations)) {
            riskHeatState.weatherRows = payload.locations;
            renderWeatherAlertsLayer(window.L);
            syncRiskHeatOverlayVisibility();
            riskHeatState.feeds.weatherLoaded = true;
        }
    } catch (error) {
        console.error("Unable to refresh weather alert feed", error);
    } finally {
        riskHeatState.feeds.weatherLoading = false;
    }
}

function fitRiskHeatMapBounds(L) {
    if (!riskHeatMap) {
        return;
    }

    const bounds = [];
    riskHeatState.shipmentRows.forEach((shipment) => {
        bounds.push([shipment.lat, shipment.lng]);
    });

    if (bounds.length > 1) {
        riskHeatMap.fitBounds(bounds, { padding: [28, 28], maxZoom: 6 });
        return;
    }

    if (bounds.length === 1) {
        riskHeatMap.setView(bounds[0], 6);
        return;
    }

    riskHeatMap.setView([20, 0], 2);
}

function bindRiskHeatUIEvents() {
    if (riskHeatState.uiBound) {
        return;
    }

    const filterPanel = document.getElementById("map-filter-panel");
    const overlayPanel = document.querySelector(".map-overlay-toggles");
    const legendPanel = document.querySelector(".map-legend-card");
    [filterPanel, overlayPanel, legendPanel].forEach((element) => {
        if (element && window.L) {
            window.L.DomEvent.disableClickPropagation(element);
            window.L.DomEvent.disableScrollPropagation(element);
        }
    });

    document.querySelectorAll("[data-mode-filter]").forEach((button) => {
        button.addEventListener("click", () => {
            riskHeatState.filters.mode = String(button.getAttribute("data-mode-filter") || "all").toLowerCase();
            applyRiskHeatShipmentFilters();
        });
    });

    document.querySelectorAll("[data-risk-filter]").forEach((checkbox) => {
        checkbox.addEventListener("change", () => {
            const checked = new Set();
            document.querySelectorAll("[data-risk-filter]").forEach((item) => {
                if (item.checked) {
                    checked.add(String(item.getAttribute("data-risk-filter") || "").toLowerCase());
                }
            });
            riskHeatState.filters.riskLevels = checked;
            applyRiskHeatShipmentFilters();
        });
    });

    const carrierSelect = document.getElementById("carrier-filter-select");
    if (carrierSelect) {
        carrierSelect.addEventListener("change", () => {
            riskHeatState.filters.carrier = carrierSelect.value || "all";
            applyRiskHeatShipmentFilters();
        });
    }

    const toggleActive = document.getElementById("toggle-active-shipments");
    if (toggleActive) {
        toggleActive.addEventListener("change", () => {
            riskHeatState.overlays.activeShipments = Boolean(toggleActive.checked);
            applyRiskHeatShipmentFilters();
        });
    }

    const togglePorts = document.getElementById("toggle-port-congestion");
    if (togglePorts) {
        togglePorts.addEventListener("change", () => {
            riskHeatState.overlays.portCongestion = Boolean(togglePorts.checked);
            syncRiskHeatOverlayVisibility();

            if (togglePorts.checked && !riskHeatState.feeds.portLoaded) {
                void refreshPortCongestionFeed(false);
            }
        });
    }

    const toggleWeather = document.getElementById("toggle-weather-alerts");
    if (toggleWeather) {
        toggleWeather.addEventListener("change", () => {
            riskHeatState.overlays.weatherAlerts = Boolean(toggleWeather.checked);
            syncRiskHeatOverlayVisibility();

            if (toggleWeather.checked && !riskHeatState.feeds.weatherLoaded) {
                void refreshWeatherAlertFeed(false);
            }
        });
    }

    const resetButton = document.getElementById("reset-map-filters");
    if (resetButton) {
        resetButton.addEventListener("click", () => {
            riskHeatState.filters.mode = "all";
            riskHeatState.filters.carrier = "all";
            riskHeatState.filters.riskLevels = new Set(["critical", "warning", "watch", "green"]);

            document.querySelectorAll("[data-risk-filter]").forEach((input) => {
                input.checked = true;
            });
            document.querySelectorAll("[data-mode-filter]").forEach((button) => {
                const mode = String(button.getAttribute("data-mode-filter") || "all").toLowerCase();
                button.classList.toggle("btn-primary", mode === "all");
                button.classList.toggle("btn-outline-primary", mode !== "all");
            });
            if (carrierSelect) {
                carrierSelect.value = "all";
            }

            applyRiskHeatShipmentFilters();
        });
    }

    const closePanelBtn = document.getElementById("close-shipment-panel");
    if (closePanelBtn) {
        closePanelBtn.addEventListener("click", closeShipmentSlidePanel);
    }

    const collapseBtn = document.getElementById("toggle-filter-panel");
    const filterContent = document.getElementById("map-filter-panel-content");
    if (collapseBtn && filterContent) {
        collapseBtn.addEventListener("click", () => {
            const hidden = filterContent.classList.toggle("d-none");
            collapseBtn.innerHTML = hidden
                ? '<i class="bi bi-chevron-down"></i>'
                : '<i class="bi bi-chevron-up"></i>';
        });
    }

    if (riskHeatMap) {
        riskHeatMap.on("click", () => {
            closeShipmentSlidePanel();
        });
    }

    riskHeatState.uiBound = true;
}

export function initRiskHeatMap(
    mapElementId = "risk-heat-map",
    shipmentMapData = [],
    portCongestionData = [],
    weatherAlertData = [],
    options = {}
) {
    if (!window.L) {
        return;
    }

    const mapElement = document.getElementById(mapElementId);
    if (!mapElement) {
        return;
    }

    riskHeatState.mapElementId = mapElementId;
    riskHeatState.shipmentRows = normalizeRiskHeatShipmentRows(shipmentMapData);
    riskHeatState.portRows = Array.isArray(portCongestionData) ? portCongestionData : [];
    riskHeatState.weatherRows = Array.isArray(weatherAlertData) ? weatherAlertData : [];
    riskHeatState.feeds.portCongestionUrl = options?.portCongestionFeedUrl || null;
    riskHeatState.feeds.weatherAlertsUrl = options?.weatherAlertFeedUrl || null;

    if (!riskHeatMap) {
        riskHeatMap = window.L.map(mapElementId, {
            zoomControl: true,
            worldCopyJump: true
        }).setView([20, 0], 2);

        window.L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            maxZoom: 19,
            attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
        }).addTo(riskHeatMap);

        riskHeatState.layers.shipments = window.L.layerGroup().addTo(riskHeatMap);
        riskHeatState.layers.shipmentAura = window.L.layerGroup().addTo(riskHeatMap);
        riskHeatState.layers.portCongestion = window.L.layerGroup();
        riskHeatState.layers.weatherAlerts = window.L.layerGroup();
    }

    buildRiskHeatShipmentLayers(window.L);
    renderPortCongestionLayer(window.L);
    renderWeatherAlertsLayer(window.L);
    syncRiskHeatOverlayVisibility();
    bindRiskHeatUIEvents();
    applyRiskHeatShipmentFilters();
    fitRiskHeatMapBounds(window.L);

    // Refresh overlays asynchronously so the page opens fast while live signals stream in.
    void refreshPortCongestionFeed(false);
    void refreshWeatherAlertFeed(false);
}
