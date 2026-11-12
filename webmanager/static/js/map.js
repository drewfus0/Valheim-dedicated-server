/**
 * Valheim Dedicated Server - Interactive World Map Controller
 * Integrates Leaflet with 1:1 Valheim game coordinates, portal networks,
 * full-world zoom out, deep hub zoom-in, and automatic portal hub de-overlapping.
 */

(function () {
    window.WORLD_MAP_VERSION = '2.4';

    let mapInstance = null;
    let spawnLayer = null;
    let tethersLayer = null;
    let portalsLayer = null;
    let linesLayer = null;
    let allPortalsData = [];
    let currentFilteredPortals = [];
    let portalMarkersMap = {};
    let displayedLatLngMap = {};
    let portalLines = [];
    let currentMapData = null;
    let currentlySelectedPortal = null;
    let currentlyHoveredPortal = null;
    let currentlyHoveredLine = null;
    let suppressMapClickUntil = 0;
    let cameraActionToken = 0;
    let lastMouseLatLng = null;
    let telemetryView = null;
    let telemetryLoading = false;

    const MAP_WIDTH = 6144;
    const MAP_HEIGHT = 6144;
    const CENTER_X = 3072;
    const CENTER_Y = 3072;
    const WORLD_WIDTH = 24000;
    const WORLD_HALF_WIDTH = 12000;
    const GAME_RADIUS = 10500;
    const SCALE = CENTER_X / WORLD_HALF_WIDTH; // Exact 0.256 CRS units per game meter
    const DISK_RADIUS_PX = GAME_RADIUS * SCALE; // 2688.0
    const TILE_SIZE = 1024;

    // With CRS_SCALE = 1024 / 6144, zoom 0 is 1024x1024 px and zoom 4 is 16384x16384 px (16x16 tiles)
    const MIN_ZOOM_FALLBACK = -1.5;
    const MAX_ZOOM = 10.5;
    const DEOVERLAP_MIN_ZOOM = 6.0;
    const INLINE_LABEL_MIN_ZOOM = 7.75;

    const BIOME_META = {
        0: { name: 'World Edge', icon: '🌌', color: '#94a3b8', bg: 'rgba(148, 163, 184, 0.16)' },
        1: { name: 'Meadows', icon: '🌿', color: '#4ade80', bg: 'rgba(74, 222, 128, 0.16)' },
        2: { name: 'Swamp', icon: '☠️', color: '#a3e635', bg: 'rgba(163, 230, 53, 0.16)' },
        3: { name: 'Mountain', icon: '🏔️', color: '#e2e8f0', bg: 'rgba(226, 232, 240, 0.18)' },
        4: { name: 'Black Forest', icon: '🌲', color: '#2dd4bf', bg: 'rgba(45, 212, 191, 0.16)' },
        5: { name: 'Plains', icon: '🌾', color: '#fbbf24', bg: 'rgba(251, 191, 36, 0.16)' },
        6: { name: 'Ashlands', icon: '🔥', color: '#f87171', bg: 'rgba(248, 113, 113, 0.18)' },
        7: { name: 'Deep North', icon: '❄️', color: '#93c5fd', bg: 'rgba(147, 197, 253, 0.18)' },
        8: { name: 'Ocean', icon: '🌊', color: '#38bdf8', bg: 'rgba(56, 189, 248, 0.18)' },
        9: { name: 'Mistlands', icon: '🌫️', color: '#c084fc', bg: 'rgba(192, 132, 252, 0.18)' }
    };

    function loadTelemetryGrid(url) {
        if (telemetryView || telemetryLoading) return;
        telemetryLoading = true;
        fetch(url || '/static/img/tiles/telemetry_1024.bin')
            .then(res => {
                if (!res.ok) throw new Error('Telemetry fetch failed');
                return res.arrayBuffer();
            })
            .then(buf => {
                if (buf && buf.byteLength >= 1024 * 1024 * 4) {
                    telemetryView = new DataView(buf);
                    if (lastMouseLatLng) {
                        updateCoordHud(lastMouseLatLng);
                    }
                }
            })
            .catch(() => {})
            .finally(() => {
                telemetryLoading = false;
            });
    }

    function sampleTelemetry(rawX, rawZ) {
        if (!telemetryView) return null;
        const sx = Math.max(0, Math.min(1023, Math.floor(((rawX + WORLD_HALF_WIDTH) / WORLD_WIDTH) * 1024)));
        const sy = Math.max(0, Math.min(1023, Math.floor(((WORLD_HALF_WIDTH - rawZ) / WORLD_WIDTH) * 1024)));
        const off = (sy * 1024 + sx) * 4;
        const biomeCode = telemetryView.getUint8(off);
        const forestRaw = telemetryView.getUint8(off + 1);
        const heightDm = telemetryView.getInt16(off + 2, true);
        const height = heightDm / 10.0;
        const forest = forestRaw / 100.0;
        return { biomeCode, height, forest };
    }

    function describeTerrainFeature(biomeCode, height, forest) {
        const alt = height - 30.0;
        if (alt < 0) {
            const depth = -alt;
            if (biomeCode === 8) {
                return depth >= 12.0 ? '🐍 Deep Ocean (Serpents)' : '🌊 Ocean Waters';
            }
            if (depth < 2.2) return '🏖️ Shallow Wade / Shore';
            return '⛵ Coastal Waters';
        }
        if (biomeCode === 1) {
            if (alt < 1.3) return '🏖️ Meadow Shoreline';
            if (forest < 0.85) return '🌳 Dense Forest';
            if (forest < 1.15) return '🌲 Wooded Meadow';
            return '☀️ Open Clearing';
        }
        if (biomeCode === 4) {
            return forest < 0.6 ? '🌲 Dense Pine Woods' : '🌲 Black Forest';
        }
        if (biomeCode === 2) {
            return alt < 1.0 ? '💧 Marsh / Leech Bog' : '🌧️ Sunken Swamp';
        }
        if (biomeCode === 3) {
            if (height >= 120.0) return '💎 High Peak (Silver/Obsidian)';
            if (height >= 95.0) return '🐺 Alpine Snow (Obsidian)';
            return '🪨 Lower Mountain Slope';
        }
        if (biomeCode === 5) {
            return forest < 1.15 ? '🌳 Birch Grove' : '🌾 Open Plains';
        }
        if (biomeCode === 9) {
            return alt > 35.0 ? '⛰️ Mistlands Spire' : '🌫️ Shrouded Valley';
        }
        if (biomeCode === 6) {
            return alt > 12.0 ? '🌋 Volcanic Interior' : '🔥 Ashlands Coast';
        }
        if (biomeCode === 7) {
            return '❄️ Frozen Tundra';
        }
        return '🗺️ Midgard';
    }

    function gameToLeaflet(x, z) {
        const px = CENTER_X + x * SCALE;
        const py = CENTER_Y - z * SCALE;
        // Leaflet L.CRS.Simple: lat = MAP_HEIGHT - py, lng = px
        return [MAP_HEIGHT - py, px];
    }

    function leafletToGame(lat, lng, decimals = 0) {
        const px = lng;
        const py = MAP_HEIGHT - lat;
        const rawX = (px - CENTER_X) / SCALE;
        const rawZ = (CENTER_Y - py) / SCALE;
        const factor = decimals > 0 ? Math.pow(10, decimals) : 1;
        const x = decimals > 0 ? Math.round(rawX * factor) / factor : Math.round(rawX);
        const z = decimals > 0 ? Math.round(rawZ * factor) / factor : Math.round(rawZ);
        return { x, z, rawX, rawZ, px: Math.round(px), py: Math.round(py) };
    }

    function syncDynamicMinZoom() {
        if (!mapInstance) return;
        const bounds = [[0, 0], [MAP_HEIGHT, MAP_WIDTH]];
        const fitZoom = mapInstance.getBoundsZoom(bounds, false, L.point(60, 110));
        const computedMin = Math.min(-1.25, Math.floor((fitZoom - 0.5) * 4) / 4);
        if (Number.isFinite(computedMin)) {
            mapInstance.setMinZoom(computedMin);
        }
    }

    function updateCoordHud(latlng) {
        const hudEl = document.getElementById('map-coord-hud');
        if (!hudEl || !mapInstance || !latlng) return;

        const zLevel = mapInstance.getZoom();
        const decimals = zLevel >= 6.5 ? 1 : 0;
        const coords = leafletToGame(latlng.lat, latlng.lng, decimals);
        const distFromCenter = Math.sqrt(coords.rawX * coords.rawX + coords.rawZ * coords.rawZ);

        if (distFromCenter > GAME_RADIUS + 150) {
            hudEl.innerHTML = `
                <span class="telem-biome-badge" style="color: #fca5a5; background: rgba(239, 68, 68, 0.16); border-color: rgba(239, 68, 68, 0.35);">
                    ⚠️ Edge of Midgard (Void)
                </span>
                <span class="telem-sep">•</span>
                <span class="telem-seg">🧭 <strong>X: ${coords.x}</strong>, <strong>Z: ${coords.z}</strong></span>
                <span class="telem-sep">•</span>
                <span class="telem-seg" style="opacity:0.75;">📏 ${Math.round(distFromCenter)}m</span>
            `;
            return;
        }

        const sample = sampleTelemetry(coords.rawX, coords.rawZ);
        if (!sample) {
            hudEl.innerHTML = `
                <span class="telem-seg">🧭 In-Game: <strong>X: ${coords.x}</strong>, <strong>Z: ${coords.z}</strong></span>
                <span class="telem-sep">•</span>
                <span class="telem-seg" style="opacity:0.75;">📏 ${Math.round(distFromCenter)}m</span>
            `;
            return;
        }

        const meta = BIOME_META[sample.biomeCode] || BIOME_META[0];
        const alt = sample.height - 30.0;
        const isWater = alt < 0;
        const elevLabel = isWater
            ? `🌊 Depth: <strong style="color:#38bdf8;">${alt.toFixed(1)}m</strong> <span style="opacity:0.65;">(Y: ${sample.height.toFixed(1)}m)</span>`
            : `⛰️ Alt: <strong style="color:#facc15;">+${alt.toFixed(1)}m</strong> <span style="opacity:0.65;">(Y: ${sample.height.toFixed(1)}m)</span>`;
        const featureText = describeTerrainFeature(sample.biomeCode, sample.height, sample.forest);

        hudEl.innerHTML = `
            <span class="telem-biome-badge" style="color: ${meta.color}; background: ${meta.bg}; border-color: ${meta.color}55;">
                ${meta.icon} <strong>${meta.name}</strong>
            </span>
            <span class="telem-sep">•</span>
            <span class="telem-seg">${elevLabel}</span>
            <span class="telem-sep">•</span>
            <span class="telem-seg telem-feature">${featureText}</span>
            <span class="telem-sep">•</span>
            <span class="telem-seg">🧭 <strong>X: ${coords.x}</strong>, <strong>Z: ${coords.z}</strong> <span style="opacity:0.65;">(${Math.round(distFromCenter)}m)</span></span>
        `;
    }

    window.initWorldMap = function (containerId, initialData) {
        const container = document.getElementById(containerId);
        if (!container) return;

        // If map already exists in container, destroy and re-create cleanly
        if (mapInstance) {
            try {
                mapInstance.remove();
            } catch (e) {}
            mapInstance = null;
        }

        currentMapData = initialData;
        allPortalsData = initialData.portals || [];
        currentFilteredPortals = allPortalsData;
        currentlySelectedPortal = null;
        currentlyHoveredPortal = null;
        currentlyHoveredLine = null;
        suppressMapClickUntil = 0;
        cameraActionToken = 0;

        // Load binary telemetry grid for instant 60fps cursor height/biome lookups
        loadTelemetryGrid(initialData.telemetry_url);

        // 1. Initialize Leaflet Map with Tile-Calibrated Simple CRS
        const bounds = [[0, 0], [MAP_HEIGHT, MAP_WIDTH]];
        const crsScale = TILE_SIZE / MAP_WIDTH; // 1024 / 6144
        const valheimCRS = L.extend({}, L.CRS.Simple, {
            transformation: new L.Transformation(crsScale, 0, -crsScale, TILE_SIZE)
        });

        mapInstance = L.map(containerId, {
            crs: valheimCRS,
            minZoom: MIN_ZOOM_FALLBACK,
            maxZoom: MAX_ZOOM,
            zoomSnap: 0.25,
            zoomDelta: 0.5,
            wheelPxPerZoomLevel: 80,
            attributionControl: false,
            maxBounds: [[-3000, -3000], [MAP_HEIGHT + 3000, MAP_WIDTH + 3000]],
            maxBoundsViscosity: 0.8
        });

        // 2. Multi-Zoom High-Res WebP Tile Pyramid (16384x16384 native at z=4)
        const tilesUrl = initialData.tiles_url || '/static/img/tiles/{z}/{x}/{y}.webp?v=2';
        L.tileLayer(tilesUrl, {
            tileSize: TILE_SIZE,
            minZoom: MIN_ZOOM_FALLBACK,
            maxZoom: MAX_ZOOM,
            minNativeZoom: 0,
            maxNativeZoom: initialData.max_native_zoom || 4,
            bounds: bounds,
            noWrap: true,
            keepBuffer: 2,
            updateWhenIdle: false
        }).addTo(mapInstance);

        // 3. Setup Layers (tethers underneath lines & pins)
        tethersLayer = L.layerGroup().addTo(mapInstance);
        linesLayer = L.layerGroup().addTo(mapInstance);
        portalsLayer = L.layerGroup().addTo(mapInstance);
        spawnLayer = L.layerGroup().addTo(mapInstance);

        // 4. Calibrate dynamic minZoom so whole map always fits any viewport
        syncDynamicMinZoom();

        // 5. Initial View: Center on Spawn
        const spawnPos = gameToLeaflet(initialData.spawn.x, initialData.spawn.z);
        mapInstance.setView(spawnPos, 2.0);

        // 6. Render POIs
        renderSpawn(initialData.spawn);
        renderPortals(allPortalsData);

        // 7. Live Coordinates HUD on Mouse Move & Zoom Change
        mapInstance.on('mousemove', function (e) {
            lastMouseLatLng = e.latlng;
            updateCoordHud(e.latlng);
        });

        mapInstance.on('zoomend', function () {
            updatePortalLayoutForZoom();
            if (lastMouseLatLng) {
                updateCoordHud(lastMouseLatLng);
            }
        });

        mapInstance.on('resize', function () {
            syncDynamicMinZoom();
        });

        // Deselect portal when clicking empty map canvas
        mapInstance.on('click', function (e) {
            if (Date.now() < suppressMapClickUntil) return;
            if (currentlySelectedPortal && (!e.originalEvent || !e.originalEvent._portalClicked)) {
                deselectPortal();
            }
        });

        // 8. Ensure size calculation & minZoom are accurate after HTMX load
        setTimeout(() => {
            if (mapInstance) {
                mapInstance.invalidateSize();
                syncDynamicMinZoom();
            }
        }, 150);

        // Populate drawer and player filter dropdown
        populatePlayerFilter(allPortalsData);
        renderPortalDrawerList(allPortalsData);
        updateFullscreenButtonState();
    };

    function renderSpawn(spawn) {
        if (!spawnLayer) return;
        spawnLayer.clearLayers();

        const latlng = gameToLeaflet(spawn.x, spawn.z);

        const spawnIcon = L.divIcon({
            className: 'valheim-spawn-icon-wrapper',
            html: `
                <div class="spawn-marker-pin" title="Sacrificial Stones (World Spawn)">
                    <div class="spawn-pulse"></div>
                    <div class="spawn-badge">🏛️</div>
                </div>
            `,
            iconSize: [36, 36],
            iconAnchor: [18, 18]
        });

        const marker = L.marker(latlng, { icon: spawnIcon }).addTo(spawnLayer);
        marker.bindPopup(`
            <div class="viking-map-popup">
                <div class="popup-header">
                    <span class="popup-icon">🏛️</span>
                    <div>
                        <h4>${spawn.name || 'Sacrificial Stones'}</h4>
                        <div class="popup-subtitle">Midgard Awakening Altar</div>
                    </div>
                </div>
                <div class="popup-body">
                    <div class="popup-row">
                        <span>Coordinates:</span>
                        <strong>X: ${spawn.x}, Z: ${spawn.z}</strong>
                    </div>
                    <div class="popup-row">
                        <span>Elevation (Y):</span>
                        <strong>${spawn.y}m</strong>
                    </div>
                    <div class="popup-row">
                        <span>Status:</span>
                        <span class="badge" style="background: rgba(16, 185, 129, 0.2); color: #34d399;">Active Altar</span>
                    </div>
                    <p style="margin-top:0.5rem; font-size:0.78rem; color:var(--text-secondary);">
                        The mystical stone ring where all Vikings first arrive in Midgard.
                    </p>
                </div>
            </div>
        `);
    }

    function computeStackAndHubMetadata(portalsList) {
        const metaMap = {};
        portalsList.forEach(p => {
            const stackedPeers = [];
            let hubPeerCount = 0;

            portalsList.forEach(q => {
                if (q.id === p.id) return;
                const d = distance(p.x, p.z, q.x, q.z);
                if (d <= 2.2) {
                    stackedPeers.push(q);
                }
                if (d <= 28.0) {
                    hubPeerCount++;
                }
            });

            // Sort co-located stack from highest elevation (top floor) to lowest
            stackedPeers.sort((a, b) => (b.y - a.y) || a.tag.localeCompare(b.tag));

            let floorArrow = '';
            let floorDesc = '';
            if (stackedPeers.length > 0) {
                const allYs = [p.y, ...stackedPeers.map(s => s.y)];
                const maxY = Math.max(...allYs);
                const minY = Math.min(...allYs);
                if (maxY - minY >= 1.5) {
                    if (p.y >= maxY - 0.3) {
                        floorArrow = '↑';
                        floorDesc = 'Upper Floor';
                    } else if (p.y <= minY + 0.3) {
                        floorArrow = '↓';
                        floorDesc = 'Lower Floor';
                    } else {
                        floorArrow = '↕';
                        floorDesc = 'Mid Floor';
                    }
                }
            }

            metaMap[p.id] = {
                stackedPeers,
                hubPeerCount,
                floorArrow,
                floorDesc
            };
        });
        return metaMap;
    }

    function renderPortals(portalsList) {
        if (!portalsLayer || !linesLayer || !tethersLayer) return;
        portalsLayer.clearLayers();
        linesLayer.clearLayers();
        tethersLayer.clearLayers();
        portalMarkersMap = {};
        displayedLatLngMap = {};
        portalLines = [];
        currentFilteredPortals = portalsList;

        const stackMeta = computeStackAndHubMetadata(portalsList);

        // 1. Connection lines: always visible, highlighted yellow on hover or selection
        const drawnPairs = new Set();
        portalsList.forEach(p => {
            if (p.linked && p.paired_with && p.paired_with.length > 0) {
                p.paired_with.forEach(peer => {
                    const pairKey = [p.id, peer.id].sort().join('--');
                    if (!drawnPairs.has(pairKey)) {
                        drawnPairs.add(pairKey);
                        const start = gameToLeaflet(p.x, p.z);
                        const end = gameToLeaflet(peer.x, peer.z);

                        const isCurrentlyActive = currentlySelectedPortal &&
                            (currentlySelectedPortal === p.id || currentlySelectedPortal === peer.id);

                        const defaultColor = p.author_color || '#38bdf8';

                        const polyline = L.polyline([start, end], {
                            color: isCurrentlyActive ? '#facc15' : defaultColor,
                            weight: isCurrentlyActive ? 4.0 : 2.5,
                            opacity: isCurrentlyActive ? 1.0 : 0.75,
                            dashArray: isCurrentlyActive ? null : '6, 8',
                            className: 'portal-network-route' + (isCurrentlyActive ? ' portal-network-route-yellow' : '')
                        }).addTo(linesLayer);

                        polyline.bindTooltip(`⚡ Connected Tag: "${escapeHtml(p.tag)}" (${escapeHtml(p.author)} ↔ ${escapeHtml(peer.author)}) — Click to view route`, {
                            sticky: true,
                            className: 'viking-line-tooltip'
                        });

                        const lineRecord = {
                            polyline,
                            pairKey,
                            p1: p,
                            p2: peer,
                            start,
                            end,
                            tag: p.tag,
                            defaultColor: defaultColor
                        };

                        // Hover on line: highlight line and both endpoints in yellow
                        polyline.on('mouseover', function () {
                            currentlyHoveredLine = lineRecord;
                            syncSelectionAndHoverHighlights();
                        });

                        polyline.on('mouseout', function () {
                            if (currentlyHoveredLine === lineRecord) {
                                currentlyHoveredLine = null;
                            }
                            syncSelectionAndHoverHighlights();
                        });

                        // Click on line: select nearest endpoint and zoom to show both ends
                        polyline.on('click', function (e) {
                            L.DomEvent.stopPropagation(e);
                            if (e.originalEvent) e.originalEvent._portalClicked = true;
                            suppressMapClickUntil = Date.now() + 350;

                            const p1LL = L.latLng(displayedLatLngMap[p.id] || start);
                            const p2LL = L.latLng(displayedLatLngMap[peer.id] || end);
                            const d1 = e.latlng.distanceTo(p1LL);
                            const d2 = e.latlng.distanceTo(p2LL);

                            const nearest = d1 <= d2 ? p : peer;
                            window.zoomToShowPortalRoute(nearest.id);
                        });

                        portalLines.push(lineRecord);
                    }
                });
            }
        });

        // 2. Render Portal Markers
        portalsList.forEach(p => {
            const latlng = gameToLeaflet(p.x, p.z);
            displayedLatLngMap[p.id] = latlng;

            const isLinked = p.linked;
            const pColor = p.author_color || '#38bdf8';
            const isSelected = currentlySelectedPortal === p.id;
            const meta = stackMeta[p.id] || { stackedPeers: [], hubPeerCount: 0, floorArrow: '', floorDesc: '' };

            const floorBadgeHtml = meta.floorArrow
                ? `<span class="portal-floor-badge" title="${meta.floorDesc} (${p.y}m)">${meta.floorArrow}</span>`
                : '';
            const labelPrefix = meta.floorArrow ? `${meta.floorArrow} ` : '';

            const portalIcon = L.divIcon({
                className: 'valheim-portal-icon-wrapper',
                html: `
                    <div class="portal-marker-pin ${isLinked ? 'is-linked' : 'is-solo'} ${isSelected ? 'portal-highlight-yellow' : ''}" 
                         id="pin-${p.id}" 
                         style="--portal-color: ${pColor};"
                         title="Portal: ${escapeHtml(p.tag)} (${escapeHtml(p.author)})${meta.floorDesc ? ' • ' + meta.floorDesc + ' ' + p.y + 'm' : ''}">
                        <div class="portal-halo"></div>
                        <div class="portal-gem">${isLinked ? '⚡' : '◇'}</div>
                        ${floorBadgeHtml}
                        <span class="portal-player-badge" style="background: ${pColor};" title="Built by ${escapeHtml(p.author)}">${p.author_avatar || '🛡️'}</span>
                        <span class="portal-inline-label">${labelPrefix}${escapeHtml(p.tag)}</span>
                    </div>
                `,
                iconSize: [32, 32],
                iconAnchor: [16, 16]
            });

            const marker = L.marker(latlng, {
                icon: portalIcon,
                riseOnHover: true,
                riseOffset: 300,
                zIndexOffset: isSelected ? 1000 : 0
            }).addTo(portalsLayer);
            portalMarkersMap[p.id] = marker;

            // Hover tooltip
            const tooltipFloorText = meta.floorDesc ? ` &bull; <span style="color:#facc15;">${meta.floorArrow} ${p.y}m</span>` : '';
            const tooltipBiomeText = p.biome ? ` &bull; <span>${p.biome_icon || '🌲'} ${escapeHtml(p.biome)}</span>` : '';
            marker.bindTooltip(`
                <div style="font-weight:600; font-size:0.82rem; display:flex; align-items:center; gap:0.35rem;">
                    <span>${isLinked ? '⚡' : '◇'}</span>
                    <span style="color: var(--nordic-cyan);">"${escapeHtml(p.tag)}"</span>
                </div>
                <div style="font-size:0.75rem; color: var(--text-secondary);">
                    <span>${escapeHtml(p.author)}</span>${tooltipBiomeText} &bull; <span>[${p.x}, ${p.z}]</span>${tooltipFloorText}
                </div>
            `, {
                direction: 'top',
                offset: [0, -14],
                className: 'viking-portal-tooltip'
            });

            // Stacked siblings section (for 2-story / multi-story hub bays sharing X/Z)
            let stackedHtml = '';
            if (meta.stackedPeers.length > 0) {
                stackedHtml = `
                    <div class="popup-stacked-siblings">
                        <div class="popup-dest-title" style="color: #facc15; margin-bottom: 0.25rem;">
                            🏢 Co-Located at This Spot (${meta.stackedPeers.length + 1} Stacked):
                        </div>
                        <div class="stacked-siblings-list">
                            ${meta.stackedPeers.map(sib => {
                                const sibMeta = stackMeta[sib.id] || {};
                                const arrow = sibMeta.floorArrow || (sib.y > p.y ? '↑' : '↓');
                                return `
                                    <button type="button" class="stacked-sibling-chip" onclick="event.stopPropagation(); selectPortal('${sib.id}', false)" title="Switch selection to ${escapeHtml(sib.tag)} (Y: ${sib.y}m)">
                                        <span>${sib.linked ? '⚡' : '◇'}</span>
                                        <strong>"${escapeHtml(sib.tag)}"</strong>
                                        <span style="opacity:0.75;">${arrow}${sib.y}m</span>
                                    </button>
                                `;
                            }).join('')}
                        </div>
                    </div>
                `;
            }

            // Connected destinations
            let pairedHtml = '';
            if (isLinked && p.paired_with && p.paired_with.length > 0) {
                pairedHtml = `
                    <div class="popup-destinations">
                        <div class="popup-dest-title">⚡ Connected Destinations:</div>
                        ${p.paired_with.map(peer => `
                            <div class="popup-dest-item">
                                <div>
                                    <strong style="color: ${peer.author_color || 'var(--text-primary)'};">${escapeHtml(peer.author)}</strong>'s Node
                                    <div style="font-size:0.72rem; color:var(--text-muted);">[${peer.x}, ${peer.z}] (${Math.round(distance(p.x, p.z, peer.x, peer.z))}m away)</div>
                                </div>
                                <div style="display:flex; gap:0.3rem;">
                                    <button type="button" class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); zoomToShowPortalRoute('${p.id}', '${peer.id}')" title="Zoom out to show both ends">
                                        🔭 Both Ends
                                    </button>
                                    <button type="button" class="btn btn-secondary btn-sm" onclick="event.stopPropagation(); flyToPortal('${peer.id}')" title="Pan Camera to this portal">
                                        ✈️ Fly To
                                    </button>
                                </div>
                            </div>
                        `).join('')}
                    </div>
                `;
            }

            // Hub zoom & route action buttons
            const showHubBtn = meta.hubPeerCount >= 1;
            const actionsRowHtml = (isLinked || showHubBtn) ? `
                <div style="margin-top:0.65rem; padding-top:0.5rem; border-top:1px dashed rgba(255,255,255,0.1); display:flex; gap:0.4rem; flex-wrap:wrap;">
                    ${showHubBtn ? `
                        <button type="button" class="btn btn-secondary btn-sm popup-hub-zoom-row" style="flex:1; display:flex; align-items:center; justify-content:center; gap:0.35rem;" onclick="event.stopPropagation(); zoomIntoPortalHub('${p.id}')" title="Zoom closely into this portal hub to separate overlapping pins & show tags">
                            🔍 Zoom Hub
                        </button>
                    ` : ''}
                    ${isLinked ? `
                        <button type="button" class="btn btn-secondary btn-sm" style="flex:1; display:flex; align-items:center; justify-content:center; gap:0.35rem;" onclick="event.stopPropagation(); zoomToShowPortalRoute('${p.id}')" title="Zoom out to show both ends of this route">
                            🔭 View Both Ends
                        </button>
                    ` : ''}
                </div>
            ` : '';

            marker.bindPopup(`
                <div class="viking-map-popup">
                    <div class="popup-header">
                        <span class="popup-icon" style="color: ${pColor}; font-size: 1.3rem;">⚡</span>
                        <div>
                            <h4>Portal "${escapeHtml(p.tag)}"</h4>
                            <div class="popup-subtitle" style="display:flex; align-items:center; gap:0.4rem;">
                                <span>${p.author_avatar || '🛡️'}</span>
                                <span style="color: ${pColor}; font-weight:600;">${escapeHtml(p.author)}</span>
                            </div>
                        </div>
                    </div>
                    <div class="popup-body">
                        <div class="popup-row">
                            <span>Status:</span>
                            <span class="badge ${isLinked ? 'badge-running' : 'badge-starting'}">
                                ${isLinked ? `⚡ Linked (${p.pair_count} nodes)` : '◇ Solo (Unmatched)'}
                            </span>
                        </div>
                        ${p.biome ? `
                        <div class="popup-row">
                            <span>Biome:</span>
                            <strong>${p.biome_icon || '🌲'} ${escapeHtml(p.biome)}</strong>
                        </div>
                        ` : ''}
                        <div class="popup-row">
                            <span>Coordinates:</span>
                            <strong>X: ${p.x}, Z: ${p.z}</strong>
                        </div>
                        <div class="popup-row">
                            <span>Elevation (Y):</span>
                            <strong>${p.y}m${meta.floorDesc ? ` <span style="color:#facc15; font-size:0.75rem;">(${meta.floorArrow} ${meta.floorDesc})</span>` : ''}</strong>
                        </div>
                        ${stackedHtml}
                        ${pairedHtml}
                        ${actionsRowHtml}
                    </div>
                </div>
            `);

            // Hover: highlight portal and connecting line in yellow
            marker.on('mouseover', function () {
                currentlyHoveredPortal = p.id;
                syncSelectionAndHoverHighlights();
            });

            marker.on('mouseout', function () {
                if (currentlyHoveredPortal === p.id) {
                    currentlyHoveredPortal = null;
                }
                syncSelectionAndHoverHighlights();
            });

            // Click: select portal
            marker.on('click', function (e) {
                if (e.originalEvent) e.originalEvent._portalClicked = true;
                suppressMapClickUntil = Date.now() + 350;
                selectPortal(p.id, false);
            });
        });

        // 3. Apply zoom-aware screen-space separation if currently zoomed into a hub
        updatePortalLayoutForZoom();
    }

    /**
     * Automatically separates overlapping & vertically-stacked portal pins in screen space
     * when zoomed in (>= DEOVERLAP_MIN_ZOOM), drawing subtle ground-anchor tethers and
     * toggling inline portal name labels at deep zoom (>= INLINE_LABEL_MIN_ZOOM).
     */
    function updatePortalLayoutForZoom() {
        if (!mapInstance || !tethersLayer) return;

        const zoom = mapInstance.getZoom();
        const canvasEl = document.getElementById('world-map-canvas');
        const showLabels = zoom >= INLINE_LABEL_MIN_ZOOM;

        if (canvasEl) {
            canvasEl.classList.toggle('map-zoom-labels', showLabels);
            canvasEl.classList.toggle('map-hub-zoomed', zoom >= 8.25);
        }

        tethersLayer.clearLayers();

        // At overview zoom levels, keep markers at exact world coordinates
        if (zoom < DEOVERLAP_MIN_ZOOM || currentFilteredPortals.length <= 1) {
            currentFilteredPortals.forEach(p => {
                const exactLL = gameToLeaflet(p.x, p.z);
                displayedLatLngMap[p.id] = exactLL;
                const m = portalMarkersMap[p.id];
                if (m) m.setLatLng(exactLL);
            });
            portalLines.forEach(l => {
                l.polyline.setLatLngs([l.start, l.end]);
            });
            syncSelectionAndHoverHighlights();
            return;
        }

        const sepX = showLabels ? 68.0 : 34.0;
        const sepY = showLabels ? 46.0 : 34.0;

        // Project portals into CRS pixel space at current zoom
        const nodes = currentFilteredPortals.map(p => {
            const trueLL = gameToLeaflet(p.x, p.z);
            const pt = mapInstance.project(trueLL, zoom);
            return {
                p,
                trueLL,
                ox: pt.x,
                oy: pt.y,
                x: pt.x,
                y: pt.y
            };
        });

        // Partition into spatial connected components so isolated portals do zero work
        const n = nodes.length;
        const visited = new Array(n).fill(false);
        const reachX = sepX * 2.2;
        const reachY = sepY * 2.2;

        for (let i = 0; i < n; i++) {
            if (visited[i]) continue;
            visited[i] = true;
            const queue = [i];
            const comp = [];

            while (queue.length > 0) {
                const cur = queue.pop();
                comp.push(nodes[cur]);
                const cx = nodes[cur].ox;
                const cy = nodes[cur].oy;
                for (let j = 0; j < n; j++) {
                    if (!visited[j]) {
                        if (Math.abs(cx - nodes[j].ox) < reachX && Math.abs(cy - nodes[j].oy) < reachY) {
                            visited[j] = true;
                            queue.push(j);
                        }
                    }
                }
            }

            if (comp.length === 1) {
                const single = comp[0];
                displayedLatLngMap[single.p.id] = single.trueLL;
                const m = portalMarkersMap[single.p.id];
                if (m) m.setLatLng(single.trueLL);
                continue;
            }

            // Sort deterministically: highest elevation (Y) first so upper-floor pins stay above lower-floor pins
            comp.sort((a, b) => (b.p.y - a.p.y) || a.p.tag.localeCompare(b.p.tag) || a.p.id.localeCompare(b.p.id));

            // Seed co-located vertical stacks before relaxation
            const seeded = new Array(comp.length).fill(false);
            for (let a = 0; a < comp.length; a++) {
                if (seeded[a]) continue;
                const stack = [a];
                seeded[a] = true;
                for (let b = a + 1; b < comp.length; b++) {
                    if (!seeded[b] && Math.hypot(comp[b].ox - comp[a].ox, comp[b].oy - comp[a].oy) < 4.0) {
                        stack.push(b);
                        seeded[b] = true;
                    }
                }
                if (stack.length === 2) {
                    comp[stack[0]].y -= sepY * 0.45;
                    comp[stack[1]].y += sepY * 0.45;
                } else if (stack.length > 2) {
                    const mid = (stack.length - 1) / 2.0;
                    stack.forEach((idx, k) => {
                        comp[idx].y += (k - mid) * sepY * 0.65;
                        comp[idx].x += (k % 2 === 0 ? 4.0 : -4.0);
                    });
                }
            }

            // Elliptical pairwise separation with gentle anchor spring
            for (let iter = 0; iter < 45; iter++) {
                for (let a = 0; a < comp.length; a++) {
                    for (let b = a + 1; b < comp.length; b++) {
                        const dx = comp[b].x - comp[a].x;
                        const dy = comp[b].y - comp[a].y;
                        const nx = dx / sepX;
                        const ny = dy / sepY;
                        const nd = Math.hypot(nx, ny);
                        if (nd < 1.0) {
                            let ux, uy, push;
                            if (nd < 0.001) {
                                ux = 0.0;
                                uy = 1.0;
                                push = 0.5;
                            } else {
                                ux = nx / nd;
                                uy = ny / nd;
                                push = (1.0 - nd) * 0.52;
                            }
                            comp[a].x -= ux * push * sepX;
                            comp[a].y -= uy * push * sepY;
                            comp[b].x += ux * push * sepX;
                            comp[b].y += uy * push * sepY;
                        }
                    }
                }
                if (iter < 30) {
                    for (let a = 0; a < comp.length; a++) {
                        comp[a].x += (comp[a].ox - comp[a].x) * 0.06;
                        comp[a].y += (comp[a].oy - comp[a].y) * 0.06;
                    }
                }
            }

            // Apply separated coordinates and draw subtle ground-anchor tethers
            const drawnAnchorDots = [];
            comp.forEach(node => {
                const dispLatLngObj = mapInstance.unproject([node.x, node.y], zoom);
                const dispArr = [dispLatLngObj.lat, dispLatLngObj.lng];
                displayedLatLngMap[node.p.id] = dispArr;

                const m = portalMarkersMap[node.p.id];
                if (m) m.setLatLng(dispArr);

                const dispPx = Math.hypot(node.x - node.ox, node.y - node.oy);
                if (dispPx > 5.0) {
                    const pColor = node.p.author_color || '#38bdf8';
                    L.polyline([node.trueLL, dispArr], {
                        color: pColor,
                        weight: 1.5,
                        opacity: 0.6,
                        dashArray: '2, 3',
                        interactive: false,
                        className: 'portal-anchor-tether'
                    }).addTo(tethersLayer);

                    const alreadyMarked = drawnAnchorDots.some(pt => Math.hypot(pt.x - node.ox, pt.y - node.oy) < 2.5);
                    if (!alreadyMarked) {
                        drawnAnchorDots.push({ x: node.ox, y: node.oy });
                        L.circleMarker(node.trueLL, {
                            radius: 2.5,
                            color: '#facc15',
                            weight: 1,
                            fillColor: '#0f172a',
                            fillOpacity: 0.9,
                            interactive: false,
                            className: 'portal-anchor-tether'
                        }).addTo(tethersLayer);
                    }
                }
            });
        }

        // Update route polyline endpoints so routes connect seamlessly into displaced pins
        portalLines.forEach(l => {
            const s = displayedLatLngMap[l.p1.id] || l.start;
            const e = displayedLatLngMap[l.p2.id] || l.end;
            l.polyline.setLatLngs([s, e]);
        });
        syncSelectionAndHoverHighlights();
    }

    function setLineState(l, isHighlighted) {
        if (!l || !l.polyline) return;
        if (isHighlighted) {
            l.polyline.setStyle({
                color: '#facc15',
                weight: 4.0,
                opacity: 1.0,
                dashArray: null
            });
            if (l.polyline._path) {
                l.polyline._path.classList.add('portal-network-route-yellow');
            }
            l.polyline.bringToFront();
        } else {
            l.polyline.setStyle({
                color: l.defaultColor || '#38bdf8',
                weight: 2.5,
                opacity: 0.75,
                dashArray: '6, 8'
            });
            if (l.polyline._path) {
                l.polyline._path.classList.remove('portal-network-route-yellow');
            }
        }
    }

    function getSelectedPortalIds() {
        const ids = new Set();
        if (!currentlySelectedPortal) return ids;
        ids.add(currentlySelectedPortal);
        const p = allPortalsData.find(item => item.id === currentlySelectedPortal);
        if (p && p.linked && p.paired_with) {
            p.paired_with.forEach(peer => ids.add(peer.id));
        }
        return ids;
    }

    function getHoveredPortalIds() {
        const ids = new Set();
        if (currentlyHoveredPortal) {
            ids.add(currentlyHoveredPortal);
            const p = allPortalsData.find(item => item.id === currentlyHoveredPortal);
            if (p && p.linked && p.paired_with) {
                p.paired_with.forEach(peer => ids.add(peer.id));
            }
        }
        if (currentlyHoveredLine) {
            ids.add(currentlyHoveredLine.p1.id);
            ids.add(currentlyHoveredLine.p2.id);
        }
        return ids;
    }

    function isLineSelected(l) {
        if (!currentlySelectedPortal || !l) return false;
        const selectedIds = getSelectedPortalIds();
        return selectedIds.has(l.p1.id) && selectedIds.has(l.p2.id);
    }

    function isLineHovered(l) {
        if (!l) return false;
        if (currentlyHoveredLine && currentlyHoveredLine.pairKey === l.pairKey) return true;
        if (currentlyHoveredPortal) {
            return l.p1.id === currentlyHoveredPortal || l.p2.id === currentlyHoveredPortal;
        }
        return false;
    }

    function highlightPinYellow(portalId, highlight) {
        const pin = document.getElementById(`pin-${portalId}`);
        if (pin) {
            if (highlight) pin.classList.add('portal-highlight-yellow');
            else pin.classList.remove('portal-highlight-yellow');
        }
    }

    function syncSelectionAndHoverHighlights() {
        const selectedIds = getSelectedPortalIds();
        const hoveredIds = getHoveredPortalIds();

        allPortalsData.forEach(p => {
            const isSel = selectedIds.has(p.id);
            const isHov = hoveredIds.has(p.id);
            highlightPinYellow(p.id, isSel || isHov);

            const m = portalMarkersMap[p.id];
            if (m) {
                if (p.id === currentlySelectedPortal) {
                    m.setZIndexOffset(1000);
                } else if (isSel) {
                    m.setZIndexOffset(950);
                } else if (isHov) {
                    m.setZIndexOffset(600);
                } else {
                    m.setZIndexOffset(0);
                }
            }
        });

        portalLines.forEach(l => {
            const active = isLineSelected(l) || isLineHovered(l);
            setLineState(l, active);
        });
    }

    window.selectPortal = function (portalId, zoomToBounds = false, openPopup = true) {
        const p = allPortalsData.find(item => item.id === portalId);
        if (!p) return;

        suppressMapClickUntil = Date.now() + 350;
        currentlySelectedPortal = portalId;
        syncSelectionAndHoverHighlights();

        if (zoomToBounds && p.linked && p.paired_with && p.paired_with.length > 0 && mapInstance) {
            window.zoomToShowPortalRoute(portalId);
            return;
        }

        const marker = portalMarkersMap[portalId];
        if (marker) {
            if (openPopup) {
                marker.openPopup();
            }
            pulseMarkerPin(portalId);
        }
        if (!openPopup && p.linked && p.paired_with) {
            p.paired_with.forEach(peer => pulseMarkerPin(peer.id));
        }
    };

    function deselectPortal() {
        currentlySelectedPortal = null;
        if (mapInstance) {
            mapInstance.closePopup();
        }
        syncSelectionAndHoverHighlights();
    }

    function getIdealPortalZoom(p) {
        let minDist = Infinity;
        currentFilteredPortals.forEach(other => {
            if (other.id !== p.id) {
                const d = distance(p.x, p.z, other.x, other.z);
                if (d < minDist) minDist = d;
            }
        });
        if (minDist <= 2.5) return 9.0;   // Vertically stacked / ultra-dense hub
        if (minDist <= 12.0) return 8.25; // Portal hub ring
        if (minDist <= 40.0) return 6.75; // Multi-portal base compound
        return 4.75;                      // Isolated outpost
    }

    window.zoomIntoPortalHub = function (portalId) {
        const p = allPortalsData.find(item => item.id === portalId);
        if (!p || !mapInstance) return;

        cameraActionToken++;
        const myToken = cameraActionToken;
        suppressMapClickUntil = Date.now() + 450;

        const targetZoom = Math.max(8.75, mapInstance.getZoom() + 1.5);
        const clampedZoom = Math.min(MAX_ZOOM, targetZoom);
        const target = gameToLeaflet(p.x, p.z);

        let handled = false;
        const onArrived = () => {
            if (handled || myToken !== cameraActionToken) return;
            handled = true;
            updatePortalLayoutForZoom();
            window.selectPortal(portalId, false, true);
        };

        mapInstance.once('moveend', onArrived);
        setTimeout(onArrived, 1100);

        mapInstance.flyTo(target, clampedZoom, {
            duration: 0.85,
            easeLinearity: 0.25
        });
    };

    // Zoom all the way out to view the entire circular Midgard map
    window.fitWholeMap = function () {
        if (!mapInstance) return;
        cameraActionToken++;
        syncDynamicMinZoom();
        const bounds = [[0, 0], [MAP_HEIGHT, MAP_WIDTH]];
        mapInstance.flyToBounds(bounds, {
            paddingTopLeft: [32, 72],
            paddingBottomRight: [32, 52],
            duration: 1.0
        });
    };

    // Zoom out to show both ends of a portal route while keeping the route selected
    window.zoomToShowPortalRoute = function (portalId, peerId = null) {
        const p = allPortalsData.find(item => item.id === portalId);
        if (!p || !mapInstance) return;

        cameraActionToken++;
        const myToken = cameraActionToken;
        suppressMapClickUntil = Date.now() + 500;

        let points = [];
        let peerIds = [];
        if (peerId) {
            const peer = allPortalsData.find(item => item.id === peerId);
            if (peer) {
                points = [gameToLeaflet(p.x, p.z), gameToLeaflet(peer.x, peer.z)];
                peerIds = [peer.id];
            }
        } else if (p.paired_with && p.paired_with.length > 0) {
            points = [
                gameToLeaflet(p.x, p.z),
                ...p.paired_with.map(peer => gameToLeaflet(peer.x, peer.z))
            ];
            peerIds = p.paired_with.map(peer => peer.id);
        }

        if (points.length >= 2) {
            currentlySelectedPortal = portalId;
            currentlyHoveredPortal = null;
            currentlyHoveredLine = null;

            // Close any open popup so it neither obscures one end of the route nor pans the camera via _adjustPan
            mapInstance.closePopup();
            syncSelectionAndHoverHighlights();

            let settled = false;
            const onRouteFitted = () => {
                if (settled || myToken !== cameraActionToken) return;
                settled = true;
                updatePortalLayoutForZoom();
                syncSelectionAndHoverHighlights();
                pulseMarkerPin(portalId);
                peerIds.forEach(id => pulseMarkerPin(id));
            };

            mapInstance.once('moveend', onRouteFitted);
            setTimeout(onRouteFitted, 600);

            mapInstance.fitBounds(L.latLngBounds(points), {
                paddingTopLeft: [80, 90],
                paddingBottomRight: [80, 70],
                maxZoom: 9.0,
                animate: true
            });
        } else {
            window.flyToPortal(portalId);
        }
    };

    // Zoom to show all portals matching current search/filters
    window.fitMatchingPortals = function () {
        if (!mapInstance || !currentFilteredPortals || currentFilteredPortals.length === 0) {
            if (window.showToast) window.showToast('No matching portals to fit in view', 'info');
            return;
        }

        if (currentFilteredPortals.length === 1) {
            window.flyToPortal(currentFilteredPortals[0].id);
            return;
        }

        cameraActionToken++;
        const points = currentFilteredPortals.map(p => gameToLeaflet(p.x, p.z));
        mapInstance.fitBounds(L.latLngBounds(points), {
            padding: [80, 80],
            maxZoom: 9.0,
            animate: true
        });
    };

    window.flyToPortal = function (portalId) {
        const p = allPortalsData.find(item => item.id === portalId);
        if (!p || !mapInstance) return;

        cameraActionToken++;
        const myToken = cameraActionToken;
        suppressMapClickUntil = Date.now() + 450;

        const idealZoom = Math.min(MAX_ZOOM, Math.max(getIdealPortalZoom(p), mapInstance.getZoom()));
        const target = gameToLeaflet(p.x, p.z);

        let opened = false;
        const finishSelect = () => {
            if (opened || myToken !== cameraActionToken) return;
            opened = true;
            updatePortalLayoutForZoom();
            const finalPos = displayedLatLngMap[portalId];
            if (finalPos && mapInstance) {
                mapInstance.panTo(finalPos, { animate: true, duration: 0.25 });
            }
            window.selectPortal(portalId, false, true);
        };

        mapInstance.once('moveend', finishSelect);
        setTimeout(finishSelect, 1400);

        mapInstance.flyTo(target, idealZoom, {
            duration: 1.1,
            easeLinearity: 0.25
        });
    };

    window.flyToSpawn = function () {
        if (!currentMapData || !mapInstance) return;
        const target = gameToLeaflet(currentMapData.spawn.x, currentMapData.spawn.z);
        mapInstance.flyTo(target, 3.25, { duration: 1.2 });
    };

    function pulseMarkerPin(portalId) {
        const pin = document.getElementById(`pin-${portalId}`);
        if (pin) {
            pin.classList.add('portal-pin-bounce');
            setTimeout(() => pin.classList.remove('portal-pin-bounce'), 2000);
        }
    }

    function parseCoordinateQuery(query) {
        // Supports "x:1370 y:73", "x=1370 z=73", "1370, 73", "1370 73"
        const namedMatch = query.match(/x\s*[:=]?\s*(-?\d+(?:\.\d+)?)[\s,;]+(?:y|z)\s*[:=]?\s*(-?\d+(?:\.\d+)?)/i);
        if (namedMatch) {
            return { x: parseFloat(namedMatch[1]), z: parseFloat(namedMatch[2]) };
        }
        const pairMatch = query.match(/^(-?\d+(?:\.\d+)?)\s*[, ]\s*(-?\d+(?:\.\d+)?)$/);
        if (pairMatch) {
            return { x: parseFloat(pairMatch[1]), z: parseFloat(pairMatch[2]) };
        }
        return null;
    }

    // Filter Portals by Player, Link Status, Search String, or Coordinate Pair
    window.applyMapFilters = function () {
        const searchInput = document.getElementById('map-portal-search');
        const query = (searchInput ? searchInput.value : '').trim().toLowerCase();

        const clearBtn = document.getElementById('map-search-clear');
        if (clearBtn) {
            clearBtn.style.display = query.length > 0 ? 'flex' : 'none';
        }

        const playerSelect = document.getElementById('map-player-filter');
        const selectedPlayer = playerSelect ? playerSelect.value : 'all';

        const statusSelect = document.getElementById('map-status-filter');
        const selectedStatus = statusSelect ? statusSelect.value : 'all';

        const coordQuery = query ? parseCoordinateQuery(query) : null;

        const filtered = allPortalsData.filter(p => {
            if (selectedPlayer !== 'all' && p.author.toLowerCase() !== selectedPlayer.toLowerCase()) {
                return false;
            }
            if (selectedStatus === 'linked' && !p.linked) return false;
            if (selectedStatus === 'solo' && p.linked) return false;
            if (query) {
                if (coordQuery) {
                    return distance(p.x, p.z, coordQuery.x, coordQuery.z) <= 60.0;
                }
                const coordStr = `${p.x}, ${p.z}`;
                if (!p.tag.toLowerCase().includes(query) &&
                    !p.author.toLowerCase().includes(query) &&
                    !coordStr.includes(query)) {
                    return false;
                }
            }
            return true;
        });

        // Update markers & lines
        renderPortals(filtered);
        renderPortalDrawerList(filtered);

        // Update fit button label & count badge
        const fitBtn = document.getElementById('map-fit-btn');
        if (fitBtn) {
            fitBtn.innerText = filtered.length === allPortalsData.length ? '🎯 Fit All' : `🎯 Fit (${filtered.length})`;
        }

        const countBadge = document.getElementById('map-filtered-count');
        if (countBadge) {
            countBadge.textContent = `${filtered.length} / ${allPortalsData.length}`;
        }
    };

    window.clearMapSearch = function () {
        const searchInput = document.getElementById('map-portal-search');
        if (searchInput) searchInput.value = '';
        window.applyMapFilters();
    };

    window.toggleMapLayer = function (layerName) {
        if (!mapInstance) return;
        if (layerName === 'spawn') {
            const checkbox = document.getElementById('toggle-layer-spawn');
            if (checkbox && checkbox.checked) {
                if (!mapInstance.hasLayer(spawnLayer)) spawnLayer.addTo(mapInstance);
            } else {
                if (mapInstance.hasLayer(spawnLayer)) mapInstance.removeLayer(spawnLayer);
            }
        } else if (layerName === 'portals') {
            const checkbox = document.getElementById('toggle-layer-portals');
            if (checkbox && checkbox.checked) {
                if (!mapInstance.hasLayer(tethersLayer)) tethersLayer.addTo(mapInstance);
                if (!mapInstance.hasLayer(portalsLayer)) portalsLayer.addTo(mapInstance);
            } else {
                if (mapInstance.hasLayer(tethersLayer)) mapInstance.removeLayer(tethersLayer);
                if (mapInstance.hasLayer(portalsLayer)) mapInstance.removeLayer(portalsLayer);
            }
        } else if (layerName === 'lines') {
            const checkbox = document.getElementById('toggle-layer-lines');
            if (checkbox && checkbox.checked) {
                if (!mapInstance.hasLayer(linesLayer)) linesLayer.addTo(mapInstance);
            } else {
                if (mapInstance.hasLayer(linesLayer)) mapInstance.removeLayer(linesLayer);
            }
        }
    };

    window.togglePortalDrawer = function () {
        const drawer = document.getElementById('map-portal-drawer');
        const btn = document.getElementById('drawer-toggle-btn');
        if (!drawer) return;
        const isOpen = drawer.classList.toggle('open');
        if (btn) {
            btn.innerHTML = isOpen ? '✕ Directory' : '📜 Directory';
        }
        setTimeout(() => {
            if (mapInstance) {
                mapInstance.invalidateSize();
                syncDynamicMinZoom();
            }
        }, 300);
    };

    window.toggleMapFullscreen = function () {
        const container = document.getElementById('map-viewport-wrapper');
        if (!container) return;
        if (!document.fullscreenElement && !document.webkitFullscreenElement) {
            if (container.requestFullscreen) {
                container.requestFullscreen().catch(() => {});
            } else if (container.webkitRequestFullscreen) {
                container.webkitRequestFullscreen();
            }
        } else {
            if (document.exitFullscreen) {
                document.exitFullscreen().catch(() => {});
            } else if (document.webkitExitFullscreen) {
                document.webkitExitFullscreen();
            }
        }
    };

    function updateFullscreenButtonState() {
        const btn = document.getElementById('map-fullscreen-btn');
        const isFs = !!(document.fullscreenElement || document.webkitFullscreenElement);
        if (btn) {
            btn.innerHTML = isFs ? '🗗 Exit Fullscreen' : '⛶ Fullscreen';
            btn.title = isFs ? 'Exit Fullscreen Map' : 'Toggle Fullscreen Map';
        }
        if (mapInstance) {
            setTimeout(() => {
                if (mapInstance) {
                    mapInstance.invalidateSize();
                    syncDynamicMinZoom();
                }
            }, 150);
        }
    }

    document.addEventListener('fullscreenchange', updateFullscreenButtonState);
    document.addEventListener('webkitfullscreenchange', updateFullscreenButtonState);

    window.refreshMapData = async function (silent = false) {
        const btn = document.getElementById('map-refresh-btn');
        if (btn && !silent) {
            btn.classList.add('loading');
            btn.disabled = true;
        }

        try {
            const res = await fetch('/api/map/data');
            if (!res.ok) throw new Error('Failed to fetch map data');
            const data = await res.json();
            currentMapData = data;
            allPortalsData = data.portals || [];
            window.applyMapFilters();
            if (!silent && window.showToast) window.showToast('World map & portals updated from active chunk', 'success');
        } catch (e) {
            if (!silent && window.showToast) window.showToast('Error refreshing portals: ' + e.message, 'error');
        } finally {
            if (btn && !silent) {
                btn.classList.remove('loading');
                btn.disabled = false;
            }
        }
    };

    function populatePlayerFilter(portals) {
        const select = document.getElementById('map-player-filter');
        if (!select) return;
        const currentVal = select.value;

        const playersSet = new Set();
        portals.forEach(p => {
            if (p.author) playersSet.add(p.author);
        });

        const sorted = Array.from(playersSet).sort();
        let html = '<option value="all">🛡️ All Builders</option>';
        sorted.forEach(name => {
            html += `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`;
        });
        select.innerHTML = html;
        if (sorted.includes(currentVal)) select.value = currentVal;
    }

    function renderPortalDrawerList(portals) {
        const listEl = document.getElementById('map-drawer-portal-list');
        if (!listEl) return;

        if (portals.length === 0) {
            listEl.innerHTML = `<div style="text-align:center; padding: 2rem; color: var(--text-muted);">No portals matching filters</div>`;
            return;
        }

        listEl.innerHTML = portals.map(p => `
            <div class="drawer-portal-item" onclick="flyToPortal('${p.id}')">
                <div class="drawer-portal-left">
                    <span class="portal-status-dot ${p.linked ? 'is-linked' : 'is-solo'}" title="${p.linked ? 'Linked Network' : 'Solo'}"></span>
                    <div>
                        <div class="drawer-portal-tag">
                            ${p.linked ? '⚡' : '◇'} <strong>"${escapeHtml(p.tag)}"</strong>
                        </div>
                        <div class="drawer-portal-meta">
                            <span style="color: ${p.author_color || 'var(--text-secondary)'}; font-weight: 500;">
                                ${p.author_avatar || '🛡️'} ${escapeHtml(p.author)}
                            </span>
                            &bull; <span>[${p.x}, ${p.z}]</span>
                        </div>
                    </div>
                </div>
                <div style="display:flex; gap:0.25rem;">
                    ${p.linked ? `
                        <button type="button" class="btn btn-secondary btn-sm drawer-portal-btn" onclick="event.stopPropagation(); zoomToShowPortalRoute('${p.id}')" title="Zoom out to show both ends">
                            🔭
                        </button>
                    ` : ''}
                    <button type="button" class="btn btn-secondary btn-sm drawer-portal-btn" title="Center map on this portal">
                        🎯
                    </button>
                </div>
            </div>
        `).join('');
    }

    function distance(x1, z1, x2, z2) {
        const dx = x2 - x1;
        const dz = z2 - z1;
        return Math.sqrt(dx * dx + dz * dz);
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#39;');
    }
})();
