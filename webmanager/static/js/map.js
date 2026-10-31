/**
 * Valheim Dedicated Server - Interactive World Map Controller
 * Integrates Leaflet with 1:1 Valheim game coordinates, portal networks,
 * full-world zoom out, deep hub zoom-in, and automatic portal hub de-overlapping.
 */

(function () {
    window.WORLD_MAP_VERSION = '2.1';

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
    let lastMouseLatLng = null;

    const MAP_WIDTH = 6144;
    const MAP_HEIGHT = 6144;
    const CENTER_X = 3072;
    const CENTER_Y = 3072;
    const DISK_RADIUS_PX = 2629.6;
    const GAME_RADIUS = 10500;
    const SCALE = DISK_RADIUS_PX / GAME_RADIUS; // ~0.25044 px/m

    const MIN_ZOOM_FALLBACK = -4.5;
    const MAX_ZOOM = 8; // ~64 px/m (32x deeper than zoom 3) for dense portal hubs
    const DEOVERLAP_MIN_ZOOM = 3.5;
    const INLINE_LABEL_MIN_ZOOM = 5.25;

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
        const computedMin = Math.min(-4.25, Math.floor((fitZoom - 0.5) * 4) / 4);
        if (Number.isFinite(computedMin)) {
            mapInstance.setMinZoom(computedMin);
        }
    }

    function updateCoordHud(latlng) {
        const hudEl = document.getElementById('map-coord-hud');
        if (!hudEl || !mapInstance || !latlng) return;

        const zLevel = mapInstance.getZoom();
        const decimals = zLevel >= 4 ? 1 : 0;
        const coords = leafletToGame(latlng.lat, latlng.lng, decimals);
        const distFromCenter = Math.sqrt(coords.rawX * coords.rawX + coords.rawZ * coords.rawZ);

        if (distFromCenter > GAME_RADIUS + 200) {
            hudEl.innerHTML = `<span style="color: var(--status-stopped);">⚠️ Edge of Midgard (Void)</span> <span style="opacity:0.65;">[X: ${coords.x}, Z: ${coords.z}]</span>`;
        } else {
            hudEl.innerHTML = `<span>🧭 In-Game: <strong>X: ${coords.x}</strong>, <strong>Z: ${coords.z}</strong></span> <span style="opacity:0.65; font-size:0.75rem;">(Dist: ${Math.round(distFromCenter)}m)</span>`;
        }
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

        // 1. Initialize Leaflet Map with Flat Simple CRS & Wide Zoom Range
        const bounds = [[0, 0], [MAP_HEIGHT, MAP_WIDTH]];
        mapInstance = L.map(containerId, {
            crs: L.CRS.Simple,
            minZoom: MIN_ZOOM_FALLBACK,
            maxZoom: MAX_ZOOM,
            zoomSnap: 0.25,
            zoomDelta: 0.5,
            wheelPxPerZoomLevel: 80,
            attributionControl: false,
            maxBounds: [[-3000, -3000], [MAP_HEIGHT + 3000, MAP_WIDTH + 3000]],
            maxBoundsViscosity: 0.8
        });

        // 2. Base High-Res Map Image Overlay
        const imageUrl = initialData.image_url || '/static/img/world_map.webp';
        L.imageOverlay(imageUrl, bounds).addTo(mapInstance);

        // 3. Setup Layers (tethers underneath lines & pins)
        tethersLayer = L.layerGroup().addTo(mapInstance);
        linesLayer = L.layerGroup().addTo(mapInstance);
        portalsLayer = L.layerGroup().addTo(mapInstance);
        spawnLayer = L.layerGroup().addTo(mapInstance);

        // 4. Calibrate dynamic minZoom so whole map always fits any viewport
        syncDynamicMinZoom();

        // 5. Initial View: Center on Spawn
        const spawnPos = gameToLeaflet(initialData.spawn.x, initialData.spawn.z);
        mapInstance.setView(spawnPos, -0.5);

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
                            setLineState(lineRecord, true);
                            highlightPinYellow(p.id, true);
                            highlightPinYellow(peer.id, true);
                        });

                        polyline.on('mouseout', function () {
                            if (!isLineSelected(lineRecord)) {
                                setLineState(lineRecord, false);
                                if (currentlySelectedPortal !== p.id) highlightPinYellow(p.id, false);
                                if (currentlySelectedPortal !== peer.id) highlightPinYellow(peer.id, false);
                            }
                        });

                        // Click on line: select nearest endpoint and zoom to show both ends
                        polyline.on('click', function (e) {
                            L.DomEvent.stopPropagation(e);
                            if (e.originalEvent) e.originalEvent._portalClicked = true;

                            const p1LL = L.latLng(displayedLatLngMap[p.id] || start);
                            const p2LL = L.latLng(displayedLatLngMap[peer.id] || end);
                            const d1 = e.latlng.distanceTo(p1LL);
                            const d2 = e.latlng.distanceTo(p2LL);

                            const nearest = d1 <= d2 ? p : peer;
                            selectPortal(nearest.id, true);
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
            marker.bindTooltip(`
                <div style="font-weight:600; font-size:0.82rem; display:flex; align-items:center; gap:0.35rem;">
                    <span>${isLinked ? '⚡' : '◇'}</span>
                    <span style="color: var(--nordic-cyan);">"${escapeHtml(p.tag)}"</span>
                </div>
                <div style="font-size:0.75rem; color: var(--text-secondary);">
                    <span>${escapeHtml(p.author)}</span> &bull; <span>[${p.x}, ${p.z}]</span>${tooltipFloorText}
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
                                    <button type="button" class="stacked-sibling-chip" onclick="selectPortal('${sib.id}', false)" title="Switch selection to ${escapeHtml(sib.tag)} (Y: ${sib.y}m)">
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
                                    <button type="button" class="btn btn-secondary btn-sm" onclick="zoomToShowPortalRoute('${p.id}', '${peer.id}')" title="Zoom out to show both ends">
                                        🔭 Both Ends
                                    </button>
                                    <button type="button" class="btn btn-secondary btn-sm" onclick="flyToPortal('${peer.id}')" title="Pan Camera to this portal">
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
                        <button type="button" class="btn btn-secondary btn-sm popup-hub-zoom-row" style="flex:1; display:flex; align-items:center; justify-content:center; gap:0.35rem;" onclick="zoomIntoPortalHub('${p.id}')" title="Zoom closely into this portal hub to separate overlapping pins & show tags">
                            🔍 Zoom Hub
                        </button>
                    ` : ''}
                    ${isLinked ? `
                        <button type="button" class="btn btn-secondary btn-sm" style="flex:1; display:flex; align-items:center; justify-content:center; gap:0.35rem;" onclick="zoomToShowPortalRoute('${p.id}')" title="Zoom out to show both ends of this route">
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
                highlightPinYellow(p.id, true);

                if (isLinked && p.paired_with) {
                    p.paired_with.forEach(peer => highlightPinYellow(peer.id, true));
                    portalLines.forEach(l => {
                        if (l.p1.id === p.id || l.p2.id === p.id) {
                            setLineState(l, true);
                        }
                    });
                }
            });

            marker.on('mouseout', function () {
                currentlyHoveredPortal = null;
                if (currentlySelectedPortal !== p.id) {
                    highlightPinYellow(p.id, false);
                    if (isLinked && p.paired_with) {
                        p.paired_with.forEach(peer => {
                            if (currentlySelectedPortal !== peer.id) {
                                highlightPinYellow(peer.id, false);
                            }
                        });
                        portalLines.forEach(l => {
                            if (!isLineSelected(l)) {
                                setLineState(l, false);
                            }
                        });
                    }
                }
            });

            // Click: select portal
            marker.on('click', function (e) {
                if (e.originalEvent) e.originalEvent._portalClicked = true;
                selectPortal(p.id, false);
            });

            marker.on('popupclose', function () {
                if (currentlySelectedPortal === p.id) {
                    deselectPortal();
                }
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
            canvasEl.classList.toggle('map-hub-zoomed', zoom >= 5.75);
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

    function isLineSelected(l) {
        if (!currentlySelectedPortal) return false;
        return l.p1.id === currentlySelectedPortal || l.p2.id === currentlySelectedPortal;
    }

    function highlightPinYellow(portalId, highlight) {
        const pin = document.getElementById(`pin-${portalId}`);
        if (pin) {
            if (highlight) pin.classList.add('portal-highlight-yellow');
            else pin.classList.remove('portal-highlight-yellow');
        }
    }

    window.selectPortal = function (portalId, zoomToBounds = false) {
        const p = allPortalsData.find(item => item.id === portalId);
        if (!p) return;

        // Reset previous z-index offset
        if (currentlySelectedPortal && portalMarkersMap[currentlySelectedPortal]) {
            portalMarkersMap[currentlySelectedPortal].setZIndexOffset(0);
        }

        currentlySelectedPortal = portalId;

        // Reset pin highlights, then highlight selected and connected peers
        document.querySelectorAll('.portal-marker-pin').forEach(pin => {
            pin.classList.remove('portal-highlight-yellow');
        });
        highlightPinYellow(portalId, true);

        if (p.linked && p.paired_with) {
            p.paired_with.forEach(peer => highlightPinYellow(peer.id, true));
            portalLines.forEach(l => {
                if (l.p1.id === portalId || l.p2.id === portalId) {
                    setLineState(l, true);
                } else {
                    setLineState(l, false);
                }
            });

            if (zoomToBounds && mapInstance) {
                const points = [
                    gameToLeaflet(p.x, p.z),
                    ...p.paired_with.map(peer => gameToLeaflet(peer.x, peer.z))
                ];
                mapInstance.fitBounds(L.latLngBounds(points), {
                    padding: [100, 100],
                    maxZoom: 6.5,
                    animate: true
                });
            }
        } else {
            portalLines.forEach(l => setLineState(l, false));
        }

        const marker = portalMarkersMap[portalId];
        if (marker) {
            marker.setZIndexOffset(1000);
            marker.openPopup();
            pulseMarkerPin(portalId);
        }
    };

    function deselectPortal() {
        if (currentlySelectedPortal && portalMarkersMap[currentlySelectedPortal]) {
            portalMarkersMap[currentlySelectedPortal].setZIndexOffset(0);
        }
        currentlySelectedPortal = null;
        document.querySelectorAll('.portal-marker-pin').forEach(pin => {
            pin.classList.remove('portal-highlight-yellow');
        });
        portalLines.forEach(l => setLineState(l, false));
    }

    function getIdealPortalZoom(p) {
        let minDist = Infinity;
        currentFilteredPortals.forEach(other => {
            if (other.id !== p.id) {
                const d = distance(p.x, p.z, other.x, other.z);
                if (d < minDist) minDist = d;
            }
        });
        if (minDist <= 2.5) return 6.5;   // Vertically stacked / ultra-dense hub
        if (minDist <= 12.0) return 5.75; // Portal hub ring
        if (minDist <= 40.0) return 4.25; // Multi-portal base compound
        return 2.25;                      // Isolated outpost
    }

    window.zoomIntoPortalHub = function (portalId) {
        const p = allPortalsData.find(item => item.id === portalId);
        if (!p || !mapInstance) return;

        const targetZoom = Math.max(6.25, mapInstance.getZoom() + 1.5);
        const clampedZoom = Math.min(MAX_ZOOM, targetZoom);
        const target = gameToLeaflet(p.x, p.z);

        let handled = false;
        const onArrived = () => {
            if (handled) return;
            handled = true;
            updatePortalLayoutForZoom();
            window.selectPortal(portalId, false);
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
        syncDynamicMinZoom();
        const bounds = [[0, 0], [MAP_HEIGHT, MAP_WIDTH]];
        mapInstance.flyToBounds(bounds, {
            paddingTopLeft: [32, 72],
            paddingBottomRight: [32, 52],
            duration: 1.0
        });
    };

    // Zoom out to show both ends of a portal route
    window.zoomToShowPortalRoute = function (portalId, peerId = null) {
        const p = allPortalsData.find(item => item.id === portalId);
        if (!p || !mapInstance) return;

        let points = [];
        if (peerId) {
            const peer = allPortalsData.find(item => item.id === peerId);
            if (peer) {
                points = [gameToLeaflet(p.x, p.z), gameToLeaflet(peer.x, peer.z)];
            }
        } else if (p.paired_with && p.paired_with.length > 0) {
            points = [
                gameToLeaflet(p.x, p.z),
                ...p.paired_with.map(peer => gameToLeaflet(peer.x, peer.z))
            ];
        }

        if (points.length >= 2) {
            window.selectPortal(portalId, false);
            mapInstance.fitBounds(L.latLngBounds(points), {
                padding: [100, 100],
                maxZoom: 6.5,
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

        const points = currentFilteredPortals.map(p => gameToLeaflet(p.x, p.z));
        mapInstance.fitBounds(L.latLngBounds(points), {
            padding: [80, 80],
            maxZoom: 6.5,
            animate: true
        });
    };

    window.flyToPortal = function (portalId) {
        const p = allPortalsData.find(item => item.id === portalId);
        if (!p || !mapInstance) return;

        const idealZoom = Math.min(MAX_ZOOM, Math.max(getIdealPortalZoom(p), mapInstance.getZoom()));
        const target = gameToLeaflet(p.x, p.z);

        let opened = false;
        const finishSelect = () => {
            if (opened) return;
            opened = true;
            updatePortalLayoutForZoom();
            const finalPos = displayedLatLngMap[portalId];
            if (finalPos && mapInstance) {
                mapInstance.panTo(finalPos, { animate: true, duration: 0.25 });
            }
            window.selectPortal(portalId, false);
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
        mapInstance.flyTo(target, 0.75, { duration: 1.2 });
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

    window.refreshMapData = async function () {
        const btn = document.getElementById('map-refresh-btn');
        if (btn) {
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
            if (window.showToast) window.showToast('World map & portals updated from active chunk', 'success');
        } catch (e) {
            if (window.showToast) window.showToast('Error refreshing portals: ' + e.message, 'error');
        } finally {
            if (btn) {
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
