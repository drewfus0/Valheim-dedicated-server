/**
 * Valheim Dedicated Server - Interactive World Map Controller
 * Integrates Leaflet with 1:1 Valheim game coordinates, portal networks, and spawn tracking.
 */

(function () {
    let mapInstance = null;
    let spawnLayer = null;
    let portalsLayer = null;
    let linesLayer = null;
    let allPortalsData = [];
    let portalMarkersMap = {};
    let portalLines = [];
    let currentMapData = null;

    const MAP_WIDTH = 6144;
    const MAP_HEIGHT = 6144;
    const CENTER_X = 3072;
    const CENTER_Y = 3072;
    const DISK_RADIUS_PX = 3064;
    const GAME_RADIUS = 10500;
    const SCALE = DISK_RADIUS_PX / GAME_RADIUS; // ~0.2918 px/m

    function gameToLeaflet(x, z) {
        const px = CENTER_X + x * SCALE;
        const py = CENTER_Y - z * SCALE;
        // Leaflet L.CRS.Simple: lat = MAP_HEIGHT - py, lng = px
        return [MAP_HEIGHT - py, px];
    }

    function leafletToGame(lat, lng) {
        const px = lng;
        const py = MAP_HEIGHT - lat;
        const x = Math.round((px - CENTER_X) / SCALE);
        const z = Math.round((CENTER_Y - py) / SCALE);
        return { x, z, px: Math.round(px), py: Math.round(py) };
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

        // 1. Initialize Leaflet Map with Flat Simple CRS
        const bounds = [[0, 0], [MAP_HEIGHT, MAP_WIDTH]];
        mapInstance = L.map(containerId, {
            crs: L.CRS.Simple,
            minZoom: -2,
            maxZoom: 3,
            zoomSnap: 0.25,
            zoomDelta: 0.5,
            attributionControl: false,
            maxBounds: [[-600, -600], [MAP_HEIGHT + 600, MAP_WIDTH + 600]],
            maxBoundsViscosity: 0.8
        });

        // 2. Base High-Res Map Image Overlay
        const imageUrl = initialData.image_url || '/static/img/world_map.webp';
        L.imageOverlay(imageUrl, bounds).addTo(mapInstance);

        // 3. Setup Layers
        linesLayer = L.layerGroup().addTo(mapInstance);
        portalsLayer = L.layerGroup().addTo(mapInstance);
        spawnLayer = L.layerGroup().addTo(mapInstance);

        // 4. Initial View: Center on Spawn
        const spawnPos = gameToLeaflet(initialData.spawn.x, initialData.spawn.z);
        mapInstance.setView(spawnPos, -0.5);

        // 5. Render POIs
        renderSpawn(initialData.spawn);
        renderPortals(allPortalsData);

        // 6. Live Coordinates HUD on Mouse Move
        mapInstance.on('mousemove', function (e) {
            const coords = leafletToGame(e.latlng.lat, e.latlng.lng);
            const distFromCenter = Math.sqrt(coords.x * coords.x + coords.z * coords.z);
            const hudEl = document.getElementById('map-coord-hud');
            if (hudEl) {
                if (distFromCenter > GAME_RADIUS + 200) {
                    hudEl.innerHTML = `<span style="color: var(--status-stopped);">⚠️ Edge of Midgard (Void)</span> <span style="opacity:0.65;">[X: ${coords.x}, Z: ${coords.z}]</span>`;
                } else {
                    hudEl.innerHTML = `<span>🧭 In-Game: <strong>X: ${coords.x}</strong>, <strong>Z: ${coords.z}</strong></span> <span style="opacity:0.65; font-size:0.75rem;">(Dist: ${Math.round(distFromCenter)}m)</span>`;
                }
            }
        });

        // 7. Ensure size calculation is correct after HTMX load
        setTimeout(() => {
            if (mapInstance) mapInstance.invalidateSize();
        }, 150);

        // Populate drawer and player filter dropdown
        populatePlayerFilter(allPortalsData);
        renderPortalDrawerList(allPortalsData);
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

    function renderPortals(portalsList) {
        if (!portalsLayer || !linesLayer) return;
        portalsLayer.clearLayers();
        linesLayer.clearLayers();
        portalMarkersMap = {};
        portalLines = [];

        // 1. Draw Connection Lines between paired portals first (underneath markers)
        const drawnPairs = new Set();
        portalsList.forEach(p => {
            if (p.linked && p.paired_with && p.paired_with.length > 0) {
                p.paired_with.forEach(peer => {
                    const pairKey = [p.id, peer.id].sort().join('--');
                    if (!drawnPairs.has(pairKey)) {
                        drawnPairs.add(pairKey);
                        const start = gameToLeaflet(p.x, p.z);
                        const end = gameToLeaflet(peer.x, peer.z);
                        const polyline = L.polyline([start, end], {
                            color: p.author_color || '#38bdf8',
                            weight: 2.2,
                            opacity: 0.65,
                            dashArray: '6, 8',
                            className: 'portal-network-route'
                        }).addTo(linesLayer);

                        polyline.bindTooltip(`⚡ Connected Tag: "${p.tag}" (${p.author} ↔ ${peer.author})`, {
                            sticky: true,
                            className: 'viking-line-tooltip'
                        });

                        portalLines.push({
                            polyline,
                            pairKey,
                            p1: p.id,
                            p2: peer.id,
                            tag: p.tag
                        });
                    }
                });
            }
        });

        // 2. Render Portal Markers
        portalsList.forEach(p => {
            const latlng = gameToLeaflet(p.x, p.z);
            const isLinked = p.linked;
            const pColor = p.author_color || '#38bdf8';

            const portalIcon = L.divIcon({
                className: 'valheim-portal-icon-wrapper',
                html: `
                    <div class="portal-marker-pin ${isLinked ? 'is-linked' : 'is-solo'}" 
                         id="pin-${p.id}" 
                         style="--portal-color: ${pColor};"
                         title="Portal: ${escapeHtml(p.tag)} (${p.author})">
                        <div class="portal-halo"></div>
                        <div class="portal-gem">${isLinked ? '⚡' : '◇'}</div>
                        <span class="portal-player-badge" style="background: ${pColor};" title="Built by ${escapeHtml(p.author)}">${p.author_avatar || '🛡️'}</span>
                    </div>
                `,
                iconSize: [32, 32],
                iconAnchor: [16, 16]
            });

            const marker = L.marker(latlng, { icon: portalIcon }).addTo(portalsLayer);
            portalMarkersMap[p.id] = marker;

            // Hover tooltip
            marker.bindTooltip(`
                <div style="font-weight:600; font-size:0.82rem; display:flex; align-items:center; gap:0.35rem;">
                    <span>${isLinked ? '⚡' : '◇'}</span>
                    <span style="color: var(--nordic-cyan);">"${escapeHtml(p.tag)}"</span>
                </div>
                <div style="font-size:0.75rem; color: var(--text-secondary);">
                    <span>${escapeHtml(p.author)}</span> &bull; <span>[${p.x}, ${p.z}]</span>
                </div>
            `, {
                direction: 'top',
                offset: [0, -14],
                className: 'viking-portal-tooltip'
            });

            // Click Popup with complete Viking metadata & Destination Fly buttons
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
                                <button type="button" class="btn btn-secondary btn-sm" onclick="flyToPortal('${peer.id}')" title="Pan Camera to this portal">
                                    ✈️ Fly To
                                </button>
                            </div>
                        `).join('')}
                    </div>
                `;
            }

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
                            <strong>${p.y}m</strong>
                        </div>
                        ${pairedHtml}
                    </div>
                </div>
            `);

            // Highlight connected routes on hover
            marker.on('mouseover', function () {
                highlightPortalRoutes(p.id, true);
            });
            marker.on('mouseout', function () {
                highlightPortalRoutes(p.id, false);
            });
        });
    }

    function highlightPortalRoutes(portalId, highlight) {
        portalLines.forEach(l => {
            if (l.p1 === portalId || l.p2 === portalId) {
                l.polyline.setStyle({
                    weight: highlight ? 4.5 : 2.2,
                    opacity: highlight ? 1.0 : 0.65,
                    color: highlight ? 'var(--nordic-gold)' : undefined
                });
            }
        });
    }

    window.flyToPortal = function (portalId) {
        const p = allPortalsData.find(item => item.id === portalId);
        if (!p || !mapInstance) return;

        const target = gameToLeaflet(p.x, p.z);
        mapInstance.flyTo(target, 1.25, {
            duration: 1.2,
            easeLinearity: 0.25
        });

        setTimeout(() => {
            const marker = portalMarkersMap[portalId];
            if (marker) {
                marker.openPopup();
                pulseMarkerPin(portalId);
            }
        }, 1300);
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

    // Filter Portals by Player, Link Status, or Search String
    window.applyMapFilters = function () {
        const searchInput = document.getElementById('map-portal-search');
        const query = (searchInput ? searchInput.value : '').trim().toLowerCase();

        const playerSelect = document.getElementById('map-player-filter');
        const selectedPlayer = playerSelect ? playerSelect.value : 'all';

        const statusSelect = document.getElementById('map-status-filter');
        const selectedStatus = statusSelect ? statusSelect.value : 'all';

        const filtered = allPortalsData.filter(p => {
            if (selectedPlayer !== 'all' && p.author.toLowerCase() !== selectedPlayer.toLowerCase()) {
                return false;
            }
            if (selectedStatus === 'linked' && !p.linked) return false;
            if (selectedStatus === 'solo' && p.linked) return false;
            if (query && !p.tag.toLowerCase().includes(query) && !p.author.toLowerCase().includes(query)) {
                return false;
            }
            return true;
        });

        // Update markers & lines
        renderPortals(filtered);
        renderPortalDrawerList(filtered);

        // Update count badge
        const countBadge = document.getElementById('map-filtered-count');
        if (countBadge) {
            countBadge.textContent = `${filtered.length} / ${allPortalsData.length}`;
        }
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
                if (!mapInstance.hasLayer(portalsLayer)) portalsLayer.addTo(mapInstance);
            } else {
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
            btn.innerHTML = isOpen ? '✕ Close Directory' : '📜 Portal Directory';
        }
        setTimeout(() => {
            if (mapInstance) mapInstance.invalidateSize();
        }, 300);
    };

    window.toggleMapFullscreen = function () {
        const container = document.getElementById('map-viewport-wrapper');
        if (!container) return;
        if (!document.fullscreenElement) {
            container.requestFullscreen().catch(() => {});
        } else {
            document.exitFullscreen().catch(() => {});
        }
        setTimeout(() => {
            if (mapInstance) mapInstance.invalidateSize();
        }, 200);
    };

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
            applyMapFilters();
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
                <button type="button" class="btn btn-secondary btn-sm drawer-portal-btn" title="Center map on this portal">
                    🎯
                </button>
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
