/**
 * Real-Time Telemetry & Celestial Clock Interpolator
 * ==================================================
 * Subscribes to 'telemetry' frames on /api/stream/events (zero HTTP polling)
 * and keeps CPU%, RAM, Host CPU clocks, Server Uptime, Midgard In-Game Clock,
 * Sunrise/Sunset progress bars, and Raid countdowns smoothly updated.
 */
(function () {
    'use strict';

    let latestTelemetry = null;
    let clockSkewSec = 0;
    let tickerId = null;

    function pad2(n) {
        return String(Math.max(0, Math.floor(n))).padStart(2, '0');
    }

    function formatUptime(elapsedSec) {
        const sec = Math.max(0, Math.floor(elapsedSec));
        const hours = Math.floor(sec / 3600);
        const rem = sec % 3600;
        const minutes = Math.floor(rem / 60);
        const seconds = rem % 60;
        return `${pad2(hours)}:${pad2(minutes)}:${pad2(seconds)}`;
    }

    function computeTimeOfDay(wc, nowServerSec) {
        let currentNetTime = wc.net_time || 0;
        if (!wc.is_frozen && typeof wc.net_time_ref === 'number') {
            currentNetTime += Math.max(0, nowServerSec - wc.net_time_ref);
        }

        const calcDay = Math.floor(currentNetTime / 1800.0);
        const dayNumber = Math.max(wc.current_day || 1, calcDay);
        const dayFraction = (((currentNetTime / 1800.0) % 1.0) + 1.0) % 1.0;

        const daySeconds = dayFraction * 86400.0;
        const clockHours = Math.floor(daySeconds / 3600);
        const clockMinutes = Math.floor((daySeconds % 3600) / 60);

        const period = clockHours < 12 ? 'AM' : 'PM';
        const h12 = (clockHours % 12) || 12;
        const time12h = `${h12}:${pad2(clockMinutes)} ${period}`;
        const time24h = `${pad2(clockHours)}:${pad2(clockMinutes)}`;

        const isDay = dayFraction >= 0.25 && dayFraction < 0.75;
        let phase, phaseIcon, subPhase, realSecondsToEvent, nextEventName, nextEventIcon, nextEventTime, phaseProgress;

        if (isDay) {
            phase = 'Day';
            phaseIcon = '☀️';
            if (clockHours < 12) subPhase = 'Morning';
            else if (clockHours < 16) subPhase = 'Midday';
            else subPhase = 'Late Afternoon';

            realSecondsToEvent = (0.75 - dayFraction) * 1800.0;
            nextEventName = 'Sunset';
            nextEventIcon = '🌅';
            nextEventTime = '18:00';
            phaseProgress = ((dayFraction - 0.25) / 0.50) * 100.0;
        } else {
            phase = 'Night';
            phaseIcon = '🌙';
            if (clockHours >= 18 && clockHours < 21) subPhase = 'Dusk / Twilight';
            else if (clockHours >= 21 || clockHours < 4) subPhase = 'Midnight / Deep Night';
            else subPhase = 'Dawn Twilight';

            let fractionToSunrise, nightElapsedFrac;
            if (dayFraction >= 0.75) {
                fractionToSunrise = (1.0 - dayFraction) + 0.25;
                nightElapsedFrac = dayFraction - 0.75;
            } else {
                fractionToSunrise = 0.25 - dayFraction;
                nightElapsedFrac = dayFraction + 0.25;
            }
            realSecondsToEvent = fractionToSunrise * 1800.0;
            nextEventName = 'Sunrise';
            nextEventIcon = '🌄';
            nextEventTime = '06:00';
            phaseProgress = (nightElapsedFrac / 0.50) * 100.0;
        }

        phaseProgress = Math.max(0.0, Math.min(100.0, phaseProgress));
        const pctRounded = Math.round(phaseProgress * 10) / 10;

        const remM = Math.floor(realSecondsToEvent / 60);
        const remS = Math.floor(realSecondsToEvent % 60);
        const timeToEventStr = remM > 0 ? `${remM}m ${pad2(remS)}s` : `${remS}s`;
        const intSec = Math.max(0, Math.floor(realSecondsToEvent));
        const unpaddedCountdown = intSec >= 60 ? `${Math.floor(intSec / 60)}m ${intSec % 60}s` : `${intSec}s`;
        const nextEventText = `${nextEventName} in ${timeToEventStr}`;

        return {
            currentDay: dayNumber,
            nightsSlept: wc.nights_slept || 0,
            time12h,
            time24h,
            isDay,
            phase,
            phaseIcon,
            subPhase,
            phaseProgressPct: pctRounded,
            nextEventName,
            nextEventIcon,
            nextEventTime,
            timeToEventStr,
            unpaddedCountdown,
            nextEventText,
            isFrozen: !!wc.is_frozen
        };
    }

    function setText(el, text) {
        if (el && el.textContent !== text) {
            el.textContent = text;
        }
    }

    function applyGauges(data) {
        if (!data) return;

        document.querySelectorAll('[data-telemetry="cpu"]').forEach(el => {
            setText(el, `${data.cpu}%`);
        });

        document.querySelectorAll('[data-telemetry="mem"]').forEach(el => {
            setText(el, `${data.mem}`);
        });

        if (data.cpu_freq && data.cpu_freq.max_mhz > 0) {
            document.querySelectorAll('[data-telemetry="cpu-clocks"]').forEach(el => {
                setText(el, `${data.cpu_freq.min_mhz} - ${data.cpu_freq.max_mhz}`);
                const box = el.closest('.metric-box');
                if (box) {
                    box.title = `Min: ${data.cpu_freq.min_mhz} MHz / Max: ${data.cpu_freq.max_mhz} MHz across ${data.cpu_freq.cores_count} cores`;
                }
            });
        }

        if (data.power_profile) {
            document.querySelectorAll('[data-telemetry="power-profile"]').forEach(el => {
                setText(el, `⚡ ${data.power_profile}`);
            });
        }
    }

    function applyClocks() {
        if (!latestTelemetry) return;

        const nowServerSec = (Date.now() / 1000) + clockSkewSec;

        // 1. Server Uptime
        const uptimeStr = (latestTelemetry.server_running && typeof latestTelemetry.start_time === 'number')
            ? formatUptime(nowServerSec - latestTelemetry.start_time)
            : '--';

        document.querySelectorAll('[data-telemetry="uptime"]').forEach(el => {
            setText(el, uptimeStr);
        });

        // 2. Active Raid Remaining Countdown
        if (typeof latestTelemetry.raid_end_ts === 'number') {
            const remRaid = Math.max(0, Math.round(latestTelemetry.raid_end_ts - nowServerSec));
            document.querySelectorAll('[data-telemetry="raid-remaining"]').forEach(el => {
                setText(el, `⏳ ~${remRaid}s remaining`);
            });
        }

        // 3. Midgard In-Game Clock & Celestial Cycle
        if (latestTelemetry.world_clock) {
            const tod = computeTimeOfDay(latestTelemetry.world_clock, nowServerSec);

            document.querySelectorAll('[data-telemetry="tod-header"]').forEach(el => {
                setText(el, `${tod.phaseIcon} ${tod.time24h}`);
                el.style.color = tod.isDay ? '#f59e0b' : '#c084fc';
                el.title = `${tod.time12h} | ${tod.nextEventText} (Day ${tod.currentDay})`;
            });

            document.querySelectorAll('[data-telemetry="tod-icon"]').forEach(el => {
                setText(el, tod.phaseIcon);
            });

            document.querySelectorAll('[data-telemetry="tod-phase"]').forEach(el => {
                setText(el, tod.phase);
                el.style.color = tod.isDay ? '#f59e0b' : '#a78bfa';
            });

            document.querySelectorAll('[data-telemetry="tod-subphase"]').forEach(el => {
                setText(el, tod.subPhase);
            });

            document.querySelectorAll('[data-telemetry="tod-24h"]').forEach(el => {
                setText(el, tod.time24h);
                if (el.dataset.telemetryColor === 'clock') {
                    el.style.color = tod.isDay ? '#f59e0b' : '#c084fc';
                }
            });

            document.querySelectorAll('[data-telemetry="tod-12h"]').forEach(el => {
                setText(el, tod.time12h);
            });

            document.querySelectorAll('[data-telemetry="tod-12h-paren"]').forEach(el => {
                setText(el, `(${tod.time12h})`);
            });

            document.querySelectorAll('[data-telemetry="tod-24h-paren"]').forEach(el => {
                setText(el, `(${tod.time24h})`);
            });

            document.querySelectorAll('[data-telemetry="tod-day"]').forEach(el => {
                setText(el, `Day ${tod.currentDay}`);
            });

            document.querySelectorAll('[data-telemetry="tod-day-sun"]').forEach(el => {
                setText(el, `☀️ Day ${tod.currentDay}`);
            });

            document.querySelectorAll('[data-telemetry="tod-day-paren"]').forEach(el => {
                setText(el, `(Day ${tod.currentDay})`);
            });

            document.querySelectorAll('[data-telemetry="tod-day-time"]').forEach(el => {
                setText(el, `Day ${tod.currentDay} • ${tod.time24h}`);
                el.style.color = tod.isDay ? 'var(--nordic-cyan)' : '#c084fc';
            });

            document.querySelectorAll('[data-telemetry="tod-calendar-sub"]').forEach(el => {
                setText(el, `🌙 ${tod.nightsSlept} Slept • ${tod.subPhase}`);
            });

            document.querySelectorAll('[data-telemetry="tod-badge"]').forEach(el => {
                setText(el, `${tod.phaseIcon} ${tod.time24h} • ${tod.nextEventText}`);
                el.style.background = tod.isDay ? 'rgba(245, 158, 11, 0.15)' : 'rgba(167, 139, 250, 0.15)';
                el.style.color = tod.isDay ? '#f59e0b' : '#c084fc';
                el.style.borderColor = tod.isDay ? 'rgba(245, 158, 11, 0.3)' : 'rgba(167, 139, 250, 0.3)';
            });

            document.querySelectorAll('[data-telemetry="tod-phase-title"]').forEach(el => {
                setText(el, `${tod.phase} Phase • ${tod.subPhase}`);
                el.style.color = tod.isDay ? '#f59e0b' : '#c084fc';
            });

            document.querySelectorAll('[data-telemetry="tod-next-icon"]').forEach(el => {
                setText(el, tod.nextEventIcon);
            });

            document.querySelectorAll('[data-telemetry="tod-next-text"]').forEach(el => {
                setText(el, tod.nextEventText);
                if (el.dataset.telemetryColor === 'accent') {
                    el.style.color = tod.isDay ? '#fbbf24' : '#e9d5ff';
                }
            });

            document.querySelectorAll('[data-telemetry="tod-next-row"]').forEach(el => {
                el.style.color = tod.isDay ? '#fbbf24' : '#d8b4fe';
            });

            document.querySelectorAll('[data-telemetry="tod-next-label"]').forEach(el => {
                setText(el, `${tod.nextEventName} in`);
            });

            document.querySelectorAll('[data-telemetry="tod-next-countdown"]').forEach(el => {
                setText(el, tod.unpaddedCountdown);
            });

            document.querySelectorAll('[data-telemetry="tod-next-clock"]').forEach(el => {
                setText(el, `(${tod.nextEventTime})`);
            });

            document.querySelectorAll('[data-telemetry="tod-frozen"]').forEach(el => {
                el.style.display = tod.isFrozen ? '' : 'none';
            });

            document.querySelectorAll('[data-telemetry="tod-phase-bar"]').forEach(el => {
                el.style.width = `${tod.phaseProgressPct}%`;
                if (el.classList.contains('celestial-progress-bar')) {
                    el.classList.toggle('day', tod.isDay);
                    el.classList.toggle('night', !tod.isDay);
                } else {
                    el.style.background = tod.isDay
                        ? 'linear-gradient(90deg, #f59e0b, #fb923c)'
                        : 'linear-gradient(90deg, #6366f1, #a855f7)';
                }
                if (el.parentElement) {
                    el.parentElement.title = `${tod.phaseProgressPct}% of ${tod.phase} elapsed`;
                }
            });

            document.querySelectorAll('[data-telemetry="tod-phase-pin"]').forEach(el => {
                el.style.left = `${tod.phaseProgressPct}%`;
                setText(el, tod.phaseIcon);
                el.title = `Current In-Game Time: ${tod.time12h}`;
            });

            document.querySelectorAll('[data-telemetry="tod-box"]').forEach(el => {
                const pausedSuffix = tod.isFrozen ? ' (Clock paused - no players online)' : '';
                el.title = `Valheim In-Game Clock: ${tod.time12h} (${tod.subPhase}) | ${tod.nextEventText}${pausedSuffix}`;
            });
        }
    }

    function syncTickerState() {
        const needsTick = !!(
            latestTelemetry && (
                latestTelemetry.server_running ||
                typeof latestTelemetry.raid_end_ts === 'number'
            )
        );
        if (needsTick && !tickerId) {
            tickerId = window.setInterval(applyClocks, 1000);
        } else if (!needsTick && tickerId) {
            window.clearInterval(tickerId);
            tickerId = null;
        }
    }

    function onTelemetryMessage(evt) {
        if (!evt || !evt.data) return;
        try {
            const payload = JSON.parse(evt.data);
            latestTelemetry = payload;
            if (typeof payload.server_now === 'number') {
                clockSkewSec = payload.server_now - (Date.now() / 1000);
            }
            applyGauges(payload);
            applyClocks();
            syncTickerState();
        } catch (err) {
            console.error('[Telemetry] Failed to parse SSE telemetry payload:', err);
        }
    }

    function attachTelemetryListener(source) {
        if (!source || source._valheimTelemetryAttached) return;
        source._valheimTelemetryAttached = true;
        source.addEventListener('telemetry', onTelemetryMessage);
        source.addEventListener('mapUpdated', function () {
            if (document.getElementById('world-map-canvas') && typeof window.refreshMapData === 'function') {
                window.refreshMapData(true);
            }
        });
    }

    // Wrap htmx.createEventSource so we subscribe to 'telemetry' before the first byte arrives
    if (typeof window.htmx !== 'undefined') {
        const origCreate = window.htmx.createEventSource || function (url) {
            return new EventSource(url, { withCredentials: true });
        };
        window.htmx.createEventSource = function (url) {
            const source = origCreate(url);
            if (typeof url === 'string' && url.indexOf('/api/stream/events') !== -1) {
                attachTelemetryListener(source);
            }
            return source;
        };
    }

    document.addEventListener('htmx:sseOpen', function (evt) {
        if (evt.detail && evt.detail.source && evt.target === document.body) {
            attachTelemetryListener(evt.detail.source);
        }
    });

    // Re-apply latest gauges and clocks immediately after any HTMX partial/tab swap
    document.addEventListener('htmx:afterSwap', function () {
        if (latestTelemetry) {
            applyGauges(latestTelemetry);
            applyClocks();
        }
    });
})();
