/**
 * Valheim Dedicated Server Manager - Spawn & Growth Timers
 * Tracks berries, crops, surtling cores, fermenters, spawners & custom timers.
 * Fully persistent across tab switches and reloads via localStorage.
 * Features a movable/draggable floating window (HUD) with circular progress rings and seconds countdown.
 */

(function() {
    'use strict';

    const STORAGE_KEY = 'valheim_timers_v2';
    const SOUND_KEY = 'valheim_timer_sound_enabled';
    const NOTIFY_KEY = 'valheim_timer_notify_enabled';
    const HUD_POS_KEY = 'valheim_timer_hud_pos';
    const HUD_MIN_KEY = 'valheim_timer_hud_minimized';
    const HUD_CLOSED_KEY = 'valheim_timer_hud_closed';

    // Preset Definitions with accurate Valheim in-game mechanics
    const PRESETS = [
        // Breeding, Livestock & Chickens (Eggs, Chicks, Hens)
        {
            id: 'egg_to_chicken',
            name: 'Egg ➔ Chick ➔ Chicken (Full Lifecycle)',
            category: 'breeding',
            icon: '🥚',
            durationSec: 1800,
            desc: '2-Stage lifecycle: Stage 1 Egg (30m) ➔ Stage 2 Chick (50m) [80m total]',
            stages: [
                { name: 'Egg Incubating (Warm & Sheltered)', icon: '🥚', durationSec: 1800 },
                { name: 'Chick Growing (Infant Chick ➔ Adult Hen)', icon: '🐥', durationSec: 3000 }
            ]
        },
        { id: 'egg_hatch', name: 'Egg Hatching (Egg ➔ Chick)', category: 'breeding', icon: '🥚', durationSec: 1800, desc: 'Warm egg near fire/hearth under roof hatches into chick (30m / 1800s)' },
        { id: 'chick_growth', name: 'Chick Growth (Chick ➔ Hen)', category: 'breeding', icon: '🐥', durationSec: 3000, desc: 'Chick matures into adult hen without food (~2 in-game days / 50m / 3000s)' },

        // Wild Berries & Forage (Spawn / Respawn)
        { id: 'raspberries', name: 'Raspberries', category: 'berries', icon: '🫐', durationSec: 18000, desc: 'Meadows bushes respawn (5h / 300m)' },
        { id: 'blueberries', name: 'Blueberries', category: 'berries', icon: '🫐', durationSec: 18000, desc: 'Black Forest bushes respawn (5h / 300m)' },
        { id: 'cloudberries', name: 'Cloudberries', category: 'berries', icon: '🫐', durationSec: 18000, desc: 'Plains bushes respawn (5h / 300m)' },
        { id: 'mushrooms_yellow', name: 'Yellow Mushrooms', category: 'berries', icon: '🍄', durationSec: 14400, desc: 'Burial Chambers / Crypts (4h / 240m)' },
        { id: 'mushrooms_red', name: 'Red Mushrooms', category: 'berries', icon: '🍄', durationSec: 14400, desc: 'Forest ground respawn (4h / 240m)' },
        { id: 'dandelions', name: 'Dandelions', category: 'berries', icon: '🌼', durationSec: 14400, desc: 'Meadows ground respawn (4h / 240m)' },

        // Farm Plants & Cultivation (Growth)
        { id: 'carrots', name: 'Carrots', category: 'crops', icon: '🥕', durationSec: 4500, desc: 'Standard crop growth (~1h 15m / 4000-5000s)' },
        { id: 'turnips', name: 'Turnips', category: 'crops', icon: '🥬', durationSec: 4500, desc: 'Swamp domestic crop (~1h 15m / 75m)' },
        { id: 'onions', name: 'Onions', category: 'crops', icon: '🧅', durationSec: 4500, desc: 'Mountain domestic crop (~1h 15m / 75m)' },
        { id: 'barley', name: 'Barley', category: 'crops', icon: '🌾', durationSec: 4500, desc: 'Plains grain cultivation (~1h 15m / 75m)' },
        { id: 'flax', name: 'Flax', category: 'crops', icon: '🌾', durationSec: 4500, desc: 'Plains linen crop (~1h 15m / 75m)' },
        { id: 'jotun_puffs', name: 'Jotun Puffs', category: 'crops', icon: '🍄', durationSec: 4500, desc: 'Mistlands farm fungus (~1h 15m / 75m)' },
        { id: 'magecap', name: 'Magecap', category: 'crops', icon: '✨', durationSec: 4500, desc: 'Mistlands magic mushroom (~1h 15m / 75m)' },
        { id: 'seed_crops', name: 'Seed Flowers', category: 'crops', icon: '🌱', durationSec: 4500, desc: 'Carrot/Turnip/Onion seed plants (~1h 15m)' },
        { id: 'tree_saplings', name: 'Tree Saplings', category: 'crops', icon: '🌲', durationSec: 3900, desc: 'Oak/Pine/Birch/Beech saplings (~1h 05m)' },
        { id: 'vineberry', name: 'Vineberry Cluster', category: 'crops', icon: '🍇', durationSec: 4500, desc: 'Ashlands vineyard growth (~1h 15m)' },

        // Surtling Cores & Spawners (Spawn)
        { id: 'surtling_geyser', name: 'Surtling Geyser (Core Farm)', category: 'spawners', icon: '🔥', durationSec: 300, desc: 'Swamp fire geyser (3 Surtlings for cores & coal, 5m)' },
        { id: 'greydwarf_nest', name: 'Greydwarf Spawner', category: 'spawners', icon: '🪵', durationSec: 8, desc: 'Black Forest nest spawn interval (8s)' },
        { id: 'skeleton_bonepile', name: 'Skeleton Bone Pile', category: 'spawners', icon: '💀', durationSec: 15, desc: 'Crypt / Dungeon spawner (15s)' },
        { id: 'body_pile', name: 'Body Pile (Draugr)', category: 'spawners', icon: '🧟', durationSec: 12, desc: 'Swamp draugr spawner interval (12s)' },
        { id: 'tar_pit', name: 'Tar Pit (Growths)', category: 'spawners', icon: '⚫', durationSec: 2700, desc: 'Plains tar pit respawn cycle (45m)' },

        // Base & Production
        { id: 'beehive_1', name: 'Beehive (1 Honey)', category: 'base', icon: '🍯', durationSec: 1200, desc: 'Generates 1 honey (20m / 1200s)' },
        { id: 'beehive_full', name: 'Beehive (Full 4 Honey)', category: 'base', icon: '🍯', durationSec: 4800, desc: 'Hive capacity fully maxed out (1h 20m / 80m)' },
        { id: 'fermenter_mead', name: 'Fermenter (Mead Batch)', category: 'base', icon: '🍺', durationSec: 2400, desc: 'Ferments mead base into 6 potions (40m / 2400s)' },
        { id: 'smelter_batch10', name: 'Smelter (10 Ore / Kiln)', category: 'base', icon: '⛏️', durationSec: 140, desc: 'Smelts 10 metal bars or coal (14s/ea = 2m 20s)' },
        { id: 'smelter_full', name: 'Smelter (Full 20/25 Load)', category: 'base', icon: '🔥', durationSec: 350, desc: 'Full kiln / smelter load (5m 50s)' },
        { id: 'eitr_refinery', name: 'Eitr Refinery (10 Eitr)', category: 'base', icon: '🔮', durationSec: 400, desc: 'Refines 10 soft tissue (40s/ea = 6m 40s)' },

        // Cycles & Buffs
        { id: 'day_night', name: 'Day / Night Cycle', category: 'cycles', icon: '☀️', durationSec: 1800, desc: 'Full Valheim Day (21m daytime + 9m night)' },
        { id: 'boss_power', name: 'Forsaken Boss Power', category: 'cycles', icon: '⚡', durationSec: 1200, desc: 'Boss power cooldown (20m total)' },
        { id: 'rested_buff', name: 'Rested Buff', category: 'cycles', icon: '🪑', durationSec: 1440, desc: 'Comfort 17 rested duration (~24m)' }
    ];

    let timers = [];
    let currentCategoryFilter = 'all';
    let currentTab = 'active'; // 'active' | 'presets' | 'custom' | 'guide'
    let audioCtx = null;
    let tickerInterval = null;

    // Draggable HUD state
    let isDragging = false;
    let dragStartX = 0;
    let dragStartY = 0;
    let initialHudLeft = 0;
    let initialHudTop = 0;
    let hudMinimized = localStorage.getItem(HUD_MIN_KEY) === 'true';
    let hudClosed = localStorage.getItem(HUD_CLOSED_KEY) === 'true';

    // Sound and Notification settings
    let soundEnabled = localStorage.getItem(SOUND_KEY) !== 'false';
    let notifyEnabled = localStorage.getItem(NOTIFY_KEY) === 'true';

    function initAudio() {
        if (!audioCtx) {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            if (AudioContext) {
                audioCtx = new AudioContext();
            }
        }
        if (audioCtx && audioCtx.state === 'suspended') {
            audioCtx.resume();
        }
    }

    function playVikingChime() {
        if (!soundEnabled) return;
        try {
            initAudio();
            if (!audioCtx) return;

            const now = audioCtx.currentTime;
            
            // Note 1: E5 (659.25 Hz)
            const osc1 = audioCtx.createOscillator();
            const gain1 = audioCtx.createGain();
            osc1.type = 'sine';
            osc1.frequency.setValueAtTime(659.25, now);
            gain1.gain.setValueAtTime(0.001, now);
            gain1.gain.exponentialRampToValueAtTime(0.2, now + 0.05);
            gain1.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
            osc1.connect(gain1);
            gain1.connect(audioCtx.destination);
            osc1.start(now);
            osc1.stop(now + 0.36);

            // Note 2: G#5 (830.61 Hz)
            const osc2 = audioCtx.createOscillator();
            const gain2 = audioCtx.createGain();
            osc2.type = 'triangle';
            osc2.frequency.setValueAtTime(830.61, now + 0.15);
            gain2.gain.setValueAtTime(0.001, now + 0.15);
            gain2.gain.exponentialRampToValueAtTime(0.22, now + 0.20);
            gain2.gain.exponentialRampToValueAtTime(0.001, now + 0.55);
            osc2.connect(gain2);
            gain2.connect(audioCtx.destination);
            osc2.start(now + 0.15);
            osc2.stop(now + 0.56);

            // Note 3: B5 (987.77 Hz)
            const osc3 = audioCtx.createOscillator();
            const gain3 = audioCtx.createGain();
            osc3.type = 'sine';
            osc3.frequency.setValueAtTime(987.77, now + 0.3);
            gain3.gain.setValueAtTime(0.001, now + 0.3);
            gain3.gain.exponentialRampToValueAtTime(0.25, now + 0.35);
            gain3.gain.exponentialRampToValueAtTime(0.0001, now + 0.9);
            osc3.connect(gain3);
            gain3.connect(audioCtx.destination);
            osc3.start(now + 0.3);
            osc3.stop(now + 0.95);
        } catch (e) {
            console.warn('[Valheim Timers] Audio chime failed:', e);
        }
    }

    function sendDesktopNotification(title, body, icon = '⏱️') {
        if (!notifyEnabled || !('Notification' in window)) return;
        if (Notification.permission === 'granted') {
            try {
                new Notification(title, {
                    body: body,
                    icon: '/static/favicon-32x32.png',
                    badge: '/static/favicon-16x16.png'
                });
            } catch (e) {
                console.warn('[Valheim Timers] Desktop notification error:', e);
            }
        }
    }

    function loadTimers() {
        try {
            const raw = localStorage.getItem(STORAGE_KEY);
            if (raw) {
                timers = JSON.parse(raw);
            } else {
                timers = [];
            }
        } catch (e) {
            timers = [];
        }
    }

    function saveTimers() {
        try {
            localStorage.setItem(STORAGE_KEY, JSON.stringify(timers));
        } catch (e) {
            console.error('[Valheim Timers] Save error:', e);
        }
        updateBadge();
        renderFloatingHud();
    }

    function formatDuration(sec) {
        if (sec < 60) return `${sec}s`;
        const m = Math.floor(sec / 60);
        const s = sec % 60;
        if (m < 60) {
            return s > 0 ? `${m}m ${s}s` : `${m}m`;
        }
        const h = Math.floor(m / 60);
        const rm = m % 60;
        return rm > 0 ? `${h}h ${rm}m` : `${h}h`;
    }

    function formatCountdown(sec) {
        if (sec <= 0) return '00:00:00';
        const h = Math.floor(sec / 3600);
        const m = Math.floor((sec % 3600) / 60);
        const s = Math.floor(sec % 60);
        const hh = h > 0 ? (h < 10 ? '0' + h : h) + ':' : '';
        const mm = (m < 10 ? '0' + m : m) + ':';
        const ss = s < 10 ? '0' + s : s;
        return hh + mm + ss;
    }

    function formatClockTime(timestamp) {
        const d = new Date(timestamp);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    }

    function addTimer(name, category, durationSec, note = '', icon = '⏱️', stages = null, currentStageIndex = 0) {
        initAudio();
        const now = Date.now();
        const timerStages = (stages && Array.isArray(stages) && stages.length > 0)
            ? JSON.parse(JSON.stringify(stages))
            : null;
        const curStageIdx = timerStages ? Math.max(0, Math.min(currentStageIndex, timerStages.length - 1)) : 0;
        const activeStage = timerStages ? timerStages[curStageIdx] : null;

        const effectiveDuration = activeStage ? activeStage.durationSec : durationSec;
        const effectiveIcon = activeStage ? (activeStage.icon || icon) : icon;
        const effectiveStageName = activeStage ? activeStage.name : '';

        const newTimer = {
            id: 'timer_' + now + '_' + Math.random().toString(36).substring(2, 6),
            name: name,
            category: category || 'custom',
            icon: effectiveIcon || '⏱️',
            note: note || '',
            durationSec: effectiveDuration,
            startedAt: now,
            targetAt: now + (effectiveDuration * 1000),
            paused: false,
            remainingSec: effectiveDuration,
            notified: false,
            stages: timerStages,
            currentStageIndex: curStageIdx,
            stageName: effectiveStageName
        };

        timers.unshift(newTimer);
        
        // Re-open floating HUD when user adds a timer
        hudClosed = false;
        localStorage.setItem(HUD_CLOSED_KEY, 'false');

        saveTimers();
        renderActiveTimers();
        renderFloatingHud();

        if (typeof window.showToast === 'function') {
            const toastMsg = timerStages 
                ? `Started lifecycle: ${name} (Stage 1: ${formatDuration(effectiveDuration)})`
                : `Started timer: ${name} (${formatDuration(effectiveDuration)})`;
            window.showToast(toastMsg, 'info');
        }

        // Switch to active tab in dropdown if open
        switchTimersTab('active');
    }

    function advanceTimerStage(id) {
        const t = timers.find(item => item.id === id);
        if (!t || !t.stages || t.currentStageIndex >= t.stages.length - 1) return;
        const now = Date.now();
        t.currentStageIndex += 1;
        const nextStage = t.stages[t.currentStageIndex];
        t.stageName = nextStage.name;
        t.icon = nextStage.icon || t.icon;
        t.durationSec = nextStage.durationSec;
        t.remainingSec = nextStage.durationSec;
        t.startedAt = now;
        t.targetAt = now + (nextStage.durationSec * 1000);
        t.paused = false;
        t.notified = false;

        saveTimers();
        renderActiveTimers();
        renderFloatingHud();

        playVikingChime();
        if (typeof window.showToast === 'function') {
            window.showToast(`Advanced to ${nextStage.name} (${formatDuration(nextStage.durationSec)})!`, 'info');
        }
    }

    function deleteTimer(id) {
        timers = timers.filter(t => t.id !== id);
        saveTimers();
        renderActiveTimers();
        renderFloatingHud();
    }

    function togglePauseTimer(id) {
        const t = timers.find(item => item.id === id);
        if (!t) return;
        const now = Date.now();

        if (t.paused) {
            // Resume
            t.paused = false;
            t.targetAt = now + (t.remainingSec * 1000);
            t.startedAt = t.targetAt - (t.durationSec * 1000);
        } else {
            // Pause
            const rem = Math.max(0, Math.round((t.targetAt - now) / 1000));
            t.remainingSec = rem;
            t.paused = true;
        }
        saveTimers();
        renderActiveTimers();
        renderFloatingHud();
    }

    function restartTimer(id, restartAllStages = true) {
        const t = timers.find(item => item.id === id);
        if (!t) return;
        const now = Date.now();

        if (t.stages && t.stages.length > 0 && restartAllStages) {
            t.currentStageIndex = 0;
            const firstStage = t.stages[0];
            t.stageName = firstStage.name;
            t.icon = firstStage.icon || t.icon;
            t.durationSec = firstStage.durationSec;
        }

        t.startedAt = now;
        t.targetAt = now + (t.durationSec * 1000);
        t.remainingSec = t.durationSec;
        t.paused = false;
        t.notified = false;
        saveTimers();
        renderActiveTimers();
        renderFloatingHud();
        if (typeof window.showToast === 'function') {
            const toastMsg = (t.stages && t.stages.length > 1)
                ? `Restarted lifecycle from Stage 1: ${t.name}`
                : `Restarted timer: ${t.name} (${formatDuration(t.durationSec)})`;
            window.showToast(toastMsg, 'info');
        }
    }

    function extendTimer(id, addSec = 300) {
        const t = timers.find(item => item.id === id);
        if (!t) return;
        t.durationSec += addSec;
        if (t.paused) {
            t.remainingSec += addSec;
        } else {
            t.targetAt += (addSec * 1000);
        }
        t.notified = false;
        saveTimers();
        renderActiveTimers();
        renderFloatingHud();
        if (typeof window.showToast === 'function') {
            window.showToast(`Extended "${t.name}" by +${addSec}s`, 'info');
        }
    }

    function clearCompletedTimers() {
        const now = Date.now();
        const beforeLen = timers.length;
        timers = timers.filter(t => {
            const rem = t.paused ? t.remainingSec : Math.round((t.targetAt - now) / 1000);
            return rem > 0;
        });
        saveTimers();
        renderActiveTimers();
        renderFloatingHud();
        const removed = beforeLen - timers.length;
        if (removed > 0 && typeof window.showToast === 'function') {
            window.showToast(`Cleared ${removed} finished timer${removed > 1 ? 's' : ''}`, 'info');
        }
    }

    function clearAllTimers() {
        if (timers.length === 0) return;
        if (confirm('Clear all timers?')) {
            timers = [];
            saveTimers();
            renderActiveTimers();
            renderFloatingHud();
        }
    }

    // Toggle dropdown open/close
    window.toggleTimersDropdown = function() {
        initAudio();
        const menu = document.getElementById('timers-dropdown-menu');
        const trigger = document.getElementById('timers-toggle-btn');
        if (!menu) return;

        const isHidden = menu.style.display === 'none' || !menu.classList.contains('show');
        if (isHidden) {
            menu.style.display = 'flex';
            void menu.offsetHeight;
            menu.classList.add('show');
            if (trigger) trigger.setAttribute('aria-expanded', 'true');
            renderActiveTimers();
            renderPresets();
        } else {
            closeTimersDropdown();
        }
    };

    window.closeTimersDropdown = function() {
        const menu = document.getElementById('timers-dropdown-menu');
        const trigger = document.getElementById('timers-toggle-btn');
        if (menu && menu.classList.contains('show')) {
            menu.classList.remove('show');
            setTimeout(() => {
                if (!menu.classList.contains('show')) {
                    menu.style.display = 'none';
                }
            }, 180);
            if (trigger) trigger.setAttribute('aria-expanded', 'false');
        }
    };

    // Tab switching inside dropdown
    window.switchTimersTab = function(tabName) {
        currentTab = tabName;
        document.querySelectorAll('.timer-tab-btn').forEach(btn => {
            btn.classList.toggle('active', btn.getAttribute('data-tab') === tabName);
        });

        const activePanel = document.getElementById('timers-panel-active');
        const presetsPanel = document.getElementById('timers-panel-presets');
        const customPanel = document.getElementById('timers-panel-custom');
        const guidePanel = document.getElementById('timers-panel-guide');

        if (activePanel) activePanel.style.display = tabName === 'active' ? 'block' : 'none';
        if (presetsPanel) presetsPanel.style.display = tabName === 'presets' ? 'block' : 'none';
        if (customPanel) customPanel.style.display = tabName === 'custom' ? 'block' : 'none';
        if (guidePanel) guidePanel.style.display = tabName === 'guide' ? 'block' : 'none';

        if (tabName === 'active') renderActiveTimers();
        if (tabName === 'presets') renderPresets();
    };

    // Filter presets
    window.filterTimerPresets = function(cat, btn) {
        currentCategoryFilter = cat;
        document.querySelectorAll('.timer-pill-btn').forEach(b => b.classList.remove('active'));
        if (btn) btn.classList.add('active');
        renderPresets();
    };

    // Quick add from preset
    window.startPresetTimer = function(presetId) {
        const p = PRESETS.find(item => item.id === presetId);
        if (!p) return;
        if (p.stages && p.stages.length > 0) {
            addTimer(p.name, p.category, p.stages[0].durationSec, '', p.stages[0].icon || p.icon, p.stages, 0);
        } else {
            addTimer(p.name, p.category, p.durationSec, '', p.icon);
        }
    };

    // Quick add with custom note prompt
    window.startPresetTimerWithNote = function(presetId) {
        const p = PRESETS.find(item => item.id === presetId);
        if (!p) return;
        const note = prompt(`Optional label or location for ${p.name} (e.g. "Main Base Coop"):`);
        if (note !== null) {
            if (p.stages && p.stages.length > 0) {
                addTimer(p.name, p.category, p.stages[0].durationSec, note.trim(), p.stages[0].icon || p.icon, p.stages, 0);
            } else {
                addTimer(p.name, p.category, p.durationSec, note.trim(), p.icon);
            }
        }
    };

    // Custom timer submission
    window.submitCustomTimer = function(e) {
        if (e) e.preventDefault();
        const nameInput = document.getElementById('ct_name');
        const hoursInput = document.getElementById('ct_hours');
        const minsInput = document.getElementById('ct_mins');
        const secsInput = document.getElementById('ct_secs');
        const noteInput = document.getElementById('ct_note');
        const iconInput = document.getElementById('ct_icon');

        const name = (nameInput && nameInput.value.trim()) || 'Custom Timer';
        const hours = parseInt((hoursInput && hoursInput.value) || '0', 10);
        const mins = parseInt((minsInput && minsInput.value) || '0', 10);
        const secs = parseInt((secsInput && secsInput.value) || '0', 10);
        const note = (noteInput && noteInput.value.trim()) || '';
        const icon = (iconInput && iconInput.value) || '⏱️';

        const totalSec = (hours * 3600) + (mins * 60) + secs;
        if (totalSec <= 0) {
            alert('Please specify a duration greater than 0.');
            return;
        }

        addTimer(name, 'custom', totalSec, note, icon);

        // Reset form
        if (nameInput) nameInput.value = '';
        if (hoursInput) hoursInput.value = '0';
        if (minsInput) minsInput.value = '15';
        if (secsInput) secsInput.value = '0';
        if (noteInput) noteInput.value = '';
    };

    // Sound toggle
    window.toggleTimerSound = function() {
        soundEnabled = !soundEnabled;
        localStorage.setItem(SOUND_KEY, soundEnabled);
        updateSoundButton();
        if (soundEnabled) {
            playVikingChime();
            if (typeof window.showToast === 'function') window.showToast('Timer chime enabled 🔔', 'info');
        } else {
            if (typeof window.showToast === 'function') window.showToast('Timer chime muted 🔕', 'info');
        }
    };

    function updateSoundButton() {
        const btn = document.getElementById('timer-sound-btn');
        if (btn) {
            btn.innerHTML = soundEnabled ? '🔔' : '🔕';
            btn.title = soundEnabled ? 'Sound alerts: ON (Click to mute)' : 'Sound alerts: OFF (Click to unmute)';
            btn.classList.toggle('active', soundEnabled);
        }
    }

    // Desktop notification toggle
    window.toggleTimerNotifications = function() {
        if (!('Notification' in window)) {
            alert('Desktop notifications are not supported in this browser.');
            return;
        }

        if (Notification.permission === 'granted') {
            notifyEnabled = !notifyEnabled;
            localStorage.setItem(NOTIFY_KEY, notifyEnabled);
            updateNotifyButton();
            if (typeof window.showToast === 'function') {
                window.showToast(notifyEnabled ? 'Desktop notifications enabled 📱' : 'Desktop notifications disabled', 'info');
            }
        } else if (Notification.permission !== 'denied') {
            Notification.requestPermission().then(permission => {
                if (permission === 'granted') {
                    notifyEnabled = true;
                    localStorage.setItem(NOTIFY_KEY, true);
                    updateNotifyButton();
                    sendDesktopNotification('Valheim Timers', 'Notifications activated! You will receive alerts when timers finish.');
                }
            });
        } else {
            alert('Notifications are blocked by your browser settings. Please enable notifications in your browser URL bar.');
        }
    };

    function updateNotifyButton() {
        const btn = document.getElementById('timer-notify-btn');
        if (btn) {
            const hasPerm = ('Notification' in window) && Notification.permission === 'granted';
            btn.classList.toggle('active', notifyEnabled && hasPerm);
            btn.title = notifyEnabled && hasPerm ? 'Desktop notifications: ON' : 'Desktop notifications: OFF (Click to enable)';
        }
    }

    function updateBadge() {
        const badge = document.getElementById('timers-active-badge');
        const tabCount = document.getElementById('timers-tab-count');
        const triggerBtn = document.getElementById('timers-toggle-btn');
        const now = Date.now();

        let activeCount = 0;
        let readyCount = 0;

        timers.forEach(t => {
            const rem = t.paused ? t.remainingSec : Math.round((t.targetAt - now) / 1000);
            if (rem <= 0) {
                readyCount++;
            } else {
                activeCount++;
            }
        });

        const totalActive = activeCount + readyCount;

        if (badge) {
            if (totalActive > 0) {
                badge.style.display = 'inline-flex';
                badge.innerText = totalActive;
                badge.classList.toggle('ready-pulse', readyCount > 0);
            } else {
                badge.style.display = 'none';
                badge.classList.remove('ready-pulse');
            }
        }

        if (tabCount) {
            tabCount.innerText = totalActive;
        }

        if (triggerBtn) {
            triggerBtn.classList.toggle('has-ready-timer', readyCount > 0);
        }
    }

    // =========================================================================
    // Draggable Floating Timers HUD (Movable Screen Window Widget)
    // =========================================================================

    function initDraggableHud() {
        const hud = document.getElementById('floating-timers-hud');
        const handle = document.getElementById('hud-drag-handle');
        if (!hud || !handle) return;

        // Restore saved position
        restoreHudPosition();

        // Restore minimized state
        if (hudMinimized) {
            hud.classList.add('minimized');
            const minBtn = document.getElementById('hud-min-btn');
            if (minBtn) minBtn.innerText = '□';
        }

        // Pointer Events for Dragging (Supports mouse, touch & pen)
        handle.addEventListener('pointerdown', function(e) {
            // Ignore clicks on control buttons inside header
            if (e.target.closest('button')) return;

            if (typeof window.hideMiniWidgetTooltip === 'function') {
                window.hideMiniWidgetTooltip();
            }

            isDragging = true;
            dragStartX = e.clientX;
            dragStartY = e.clientY;

            const rect = hud.getBoundingClientRect();
            initialHudLeft = rect.left;
            initialHudTop = rect.top;

            hud.classList.add('is-dragging');
            try {
                handle.setPointerCapture(e.pointerId);
            } catch (_) {}
            e.preventDefault();
        });

        handle.addEventListener('pointermove', function(e) {
            if (!isDragging) return;
            const dx = e.clientX - dragStartX;
            const dy = e.clientY - dragStartY;

            let newLeft = initialHudLeft + dx;
            let newTop = initialHudTop + dy;

            // Clamping within viewport boundaries
            const maxLeft = Math.max(10, window.innerWidth - hud.offsetWidth - 10);
            const maxTop = Math.max(10, window.innerHeight - hud.offsetHeight - 10);

            newLeft = Math.min(maxLeft, Math.max(10, newLeft));
            newTop = Math.min(maxTop, Math.max(10, newTop));

            hud.style.left = newLeft + 'px';
            hud.style.top = newTop + 'px';
            hud.style.right = 'auto';
            hud.style.bottom = 'auto';
        });

        const stopDrag = function(e) {
            if (!isDragging) return;
            isDragging = false;
            hud.classList.remove('is-dragging');
            try {
                handle.releasePointerCapture(e.pointerId);
            } catch (_) {}

            // Save position to localStorage
            const rect = hud.getBoundingClientRect();
            localStorage.setItem(HUD_POS_KEY, JSON.stringify({
                left: Math.round(rect.left),
                top: Math.round(rect.top)
            }));
        };

        handle.addEventListener('pointerup', stopDrag);
        handle.addEventListener('pointercancel', stopDrag);

        // Window resize reposition clamp
        window.addEventListener('resize', clampHudPosition);
    }

    function restoreHudPosition() {
        const hud = document.getElementById('floating-timers-hud');
        if (!hud) return;

        try {
            const raw = localStorage.getItem(HUD_POS_KEY);
            if (raw) {
                const pos = JSON.parse(raw);
                if (pos && typeof pos.left === 'number' && typeof pos.top === 'number') {
                    const hudWidth = hud.offsetWidth || 284;
                    const hudHeight = hud.offsetHeight || 140;
                    const maxLeft = Math.max(10, window.innerWidth - hudWidth - 10);
                    const maxTop = Math.max(10, window.innerHeight - hudHeight - 10);
                    const clampedLeft = Math.min(maxLeft, Math.max(10, pos.left));
                    const clampedTop = Math.min(maxTop, Math.max(10, pos.top));

                    hud.style.left = clampedLeft + 'px';
                    hud.style.top = clampedTop + 'px';
                    hud.style.right = 'auto';
                    hud.style.bottom = 'auto';
                    return;
                }
            }
        } catch (_) {}

        // Default: Top-Right
        hud.style.top = '85px';
        hud.style.right = '24px';
        hud.style.left = 'auto';
        hud.style.bottom = 'auto';
    }

    function clampHudPosition() {
        const hud = document.getElementById('floating-timers-hud');
        if (!hud || hud.style.display === 'none') return;
        const rect = hud.getBoundingClientRect();
        const maxLeft = Math.max(10, window.innerWidth - hud.offsetWidth - 10);
        const maxTop = Math.max(10, window.innerHeight - hud.offsetHeight - 10);
        const clampedLeft = Math.min(maxLeft, Math.max(10, rect.left));
        const clampedTop = Math.min(maxTop, Math.max(10, rect.top));
        hud.style.left = clampedLeft + 'px';
        hud.style.top = clampedTop + 'px';
        hud.style.right = 'auto';
        hud.style.bottom = 'auto';
    }

    window.resetHudPosition = function() {
        localStorage.removeItem(HUD_POS_KEY);
        restoreHudPosition();
        if (typeof window.showToast === 'function') {
            window.showToast('Reset floating window to top-right 📍', 'info');
        }
    };

    window.toggleHudMinimize = function() {
        const hud = document.getElementById('floating-timers-hud');
        const minBtn = document.getElementById('hud-min-btn');
        if (!hud) return;
        if (typeof window.hideMiniWidgetTooltip === 'function') {
            window.hideMiniWidgetTooltip();
        }
        hudMinimized = !hudMinimized;
        hud.classList.toggle('minimized', hudMinimized);
        localStorage.setItem(HUD_MIN_KEY, hudMinimized);
        if (minBtn) minBtn.innerText = hudMinimized ? '□' : '_';
    };

    window.closeTimersHud = function() {
        const hud = document.getElementById('floating-timers-hud');
        if (!hud) return;
        if (typeof window.hideMiniWidgetTooltip === 'function') {
            window.hideMiniWidgetTooltip();
        }
        hud.style.display = 'none';
        hudClosed = true;
        localStorage.setItem(HUD_CLOSED_KEY, 'true');
        if (typeof window.showToast === 'function') {
            window.showToast('Floating timers window hidden. Click 🪟 in Timers menu to restore.', 'info');
        }
    };

    window.openTimersHud = function() {
        hudClosed = false;
        localStorage.setItem(HUD_CLOSED_KEY, 'false');
        renderFloatingHud();
        restoreHudPosition();
        if (typeof window.showToast === 'function') {
            window.showToast('Movable floating timers window displayed 🪟', 'info');
        }
    };

    window.showMiniWidgetTooltip = function(e) {
        const el = e.currentTarget;
        if (!el) return;
        const name = el.getAttribute('data-timer-name') || el.getAttribute('title');
        if (!name) return;

        let tt = document.getElementById('hud-timer-tooltip');
        if (!tt) {
            tt = document.createElement('div');
            tt.id = 'hud-timer-tooltip';
            document.body.appendChild(tt);
        }
        tt.className = 'hud-timer-tooltip visible';
        tt.textContent = name;
        tt.style.display = 'block';

        const rect = el.getBoundingClientRect();
        const hud = document.getElementById('floating-timers-hud');
        const hudRect = hud ? hud.getBoundingClientRect() : null;

        const ttWidth = tt.offsetWidth || 120;
        const ttHeight = tt.offsetHeight || 26;

        let left = rect.left + (rect.width / 2) - (ttWidth / 2);
        left = Math.max(10, Math.min(window.innerWidth - ttWidth - 10, left));

        // Position cleanly above the floating HUD window aligned with hovered widget
        let top = (hudRect ? hudRect.top : rect.top) - ttHeight - 8;
        if (top < 10) {
            // If floating window is near the top of viewport, show cleanly below HUD
            top = (hudRect ? hudRect.bottom : rect.bottom) + 8;
        }

        tt.style.left = Math.round(left) + 'px';
        tt.style.top = Math.round(top) + 'px';
    };

    window.hideMiniWidgetTooltip = function() {
        const tt = document.getElementById('hud-timer-tooltip');
        if (tt) {
            tt.classList.remove('visible');
            setTimeout(() => {
                if (!tt.classList.contains('visible')) {
                    tt.style.display = 'none';
                }
            }, 120);
        }
    };

    /**
     * Render the Floating Timers HUD
     * Shows each active timer in its own movable box with:
     * - the icon
     * - a circle that slowly fills clockwise
     * - time in secs to go
     * - restart button
     */
    function renderFloatingHud() {
        const hud = document.getElementById('floating-timers-hud');
        const list = document.getElementById('hud-timers-list');
        const countBadge = document.getElementById('hud-count');
        if (!hud || !list) return;

        if (timers.length === 0 || hudClosed) {
            hud.style.display = 'none';
            hideMiniWidgetTooltip();
            return;
        }

        hud.style.display = 'flex';
        if (countBadge) countBadge.innerText = timers.length;

        const now = Date.now();
        let html = '';

        timers.forEach(t => {
            const rem = t.paused ? t.remainingSec : Math.max(0, Math.round((t.targetAt - now) / 1000));
            const elapsed = Math.max(0, t.durationSec - rem);
            const pct = Math.min(100, Math.max(0, (elapsed / t.durationSec) * 100));
            const isDone = rem <= 0;
            const offset = (100 - pct).toFixed(1);

            const catColors = {
                breeding: '#f59e0b',
                berries: 'var(--nordic-cyan)',
                crops: '#10b981',
                spawners: '#f97316',
                base: 'var(--nordic-gold)',
                cycles: '#8b5cf6'
            };
            const catColor = isDone ? '#10b981' : (catColors[t.category] || 'var(--nordic-cyan)');
            const secsText = isDone ? '0s' : `${rem}s`;
            let displayName = t.name;
            if (t.stages && t.stages.length > 1) {
                displayName += ` [Stage ${t.currentStageIndex + 1}/${t.stages.length}: ${t.stageName || ''}]`;
            }
            if (t.note) displayName += ` (${t.note})`;
            const safeDisplayName = escapeHtml(displayName);

            html += `
                <div class="hud-mini-widget ${isDone ? 'done' : ''} ${t.paused ? 'paused' : ''}"
                     id="hud_t_${t.id}"
                     title="${safeDisplayName}"
                     data-timer-name="${safeDisplayName}"
                     onmouseenter="showMiniWidgetTooltip(event)"
                     onmouseleave="hideMiniWidgetTooltip()"
                     onclick="toggleTimersDropdown()">
                    <!-- Icon inside filling circle -->
                    <div class="hud-mini-ring-wrap">
                        <svg class="hud-mini-svg" viewBox="0 0 36 36">
                            <path class="hud-mini-circle-bg"
                                  d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                                  fill="none" stroke="rgba(255,255,255,0.12)" stroke-width="3"/>
                            <path class="hud-mini-circle-fill ${isDone ? 'done' : ''}"
                                  stroke-dasharray="100, 100"
                                  stroke-dashoffset="${offset}"
                                  d="M18 2.0845 a 15.9155 15.9155 0 0 1 0 31.831 a 15.9155 15.9155 0 0 1 0 -31.831"
                                  fill="none" stroke="${catColor}" stroke-width="3" stroke-linecap="round"/>
                        </svg>
                        <span class="hud-mini-icon">${t.icon || '⏱️'}</span>
                    </div>

                    <!-- Only text: seconds -->
                    <span class="hud-mini-secs ${isDone ? 'done' : ''}">${secsText}</span>

                    <!-- Restart button -->
                    <button type="button" class="hud-mini-restart-btn"
                            onclick="event.stopPropagation(); restartTimer('${t.id}')"
                            title="Restart">
                        🔄
                    </button>
                </div>
            `;
        });

        list.innerHTML = html;
    }

    function renderPresets() {
        const container = document.getElementById('timers-presets-grid');
        if (!container) return;

        const filtered = PRESETS.filter(p => {
            if (currentCategoryFilter === 'all') return true;
            return p.category === currentCategoryFilter;
        });

        if (filtered.length === 0) {
            container.innerHTML = '<div style="color: var(--text-muted); text-align: center; padding: 1.5rem;">No presets match this filter.</div>';
            return;
        }

        let html = '';
        filtered.forEach(p => {
            const catColors = {
                breeding: '#f59e0b',
                berries: 'var(--nordic-cyan)',
                crops: '#10b981',
                spawners: '#f97316',
                base: 'var(--nordic-gold)',
                cycles: '#8b5cf6'
            };
            const catColor = catColors[p.category] || 'var(--text-secondary)';

            let badgeHtml = '';
            let quickStartBtnLabel = `▶️ Start (${p.durationSec}s)`;

            if (p.stages && p.stages.length > 1) {
                const totalSec = p.stages.reduce((acc, s) => acc + s.durationSec, 0);
                badgeHtml = `
                    <span class="timer-duration-badge" style="color: ${catColor}; border-color: ${catColor}44;" title="${p.stages.map(s => `${s.name} (${formatDuration(s.durationSec)})`).join(' ➔ ')}">
                        ${p.stages.length} Stages &bull; ${formatDuration(totalSec)} total
                    </span>
                `;
                quickStartBtnLabel = `▶️ Start Lifecycle (${formatDuration(totalSec)})`;
            } else {
                badgeHtml = `
                    <span class="timer-duration-badge" style="color: ${catColor}; border-color: ${catColor}44;">
                        ${p.durationSec}s (${formatDuration(p.durationSec)})
                    </span>
                `;
            }

            html += `
                <div class="timer-preset-card">
                    <div class="timer-preset-header">
                        <div class="timer-preset-title-wrap">
                            <span class="timer-preset-icon">${p.icon}</span>
                            <div>
                                <div class="timer-preset-name">${p.name}</div>
                                <div class="timer-preset-desc">${p.desc}</div>
                            </div>
                        </div>
                        ${badgeHtml}
                    </div>
                    <div class="timer-preset-actions">
                        <button type="button" class="btn btn-secondary btn-sm" onclick="startPresetTimerWithNote('${p.id}')" title="Start with custom label/location">
                            🏷️ + Note
                        </button>
                        <button type="button" class="btn btn-primary btn-sm timer-quick-start-btn" onclick="startPresetTimer('${p.id}')">
                            ${quickStartBtnLabel}
                        </button>
                    </div>
                </div>
            `;
        });

        container.innerHTML = html;
    }

    function renderActiveTimers() {
        const list = document.getElementById('timers-active-list');
        const footer = document.getElementById('timers-active-footer');
        if (!list) return;

        if (timers.length === 0) {
            list.innerHTML = `
                <div class="timers-empty-state">
                    <div style="font-size: 2.2rem; margin-bottom: 0.5rem;">⏱️</div>
                    <strong style="color: var(--text-primary); font-size: 0.95rem;">No Running Timers</strong>
                    <p style="color: var(--text-muted); font-size: 0.8125rem; margin: 0.35rem 0 1rem 0;">
                        Track chicken & egg incubation, berry respawns, farm crops, surtling cores, fermenters, and custom Viking timers.
                    </p>
                    <button type="button" class="btn btn-primary btn-sm" onclick="switchTimersTab('presets')">
                        ⚡ Browse Presets (Chickens, Crops, Cores)
                    </button>
                </div>
            `;
            if (footer) footer.style.display = 'none';
            return;
        }

        if (footer) footer.style.display = 'flex';

        const now = Date.now();
        let html = '';

        timers.forEach(t => {
            const rem = t.paused ? t.remainingSec : Math.max(0, Math.round((t.targetAt - now) / 1000));
            const elapsed = Math.max(0, t.durationSec - rem);
            const pct = Math.min(100, Math.max(0, (elapsed / t.durationSec) * 100));
            const isDone = rem <= 0;
            const hasMultipleStages = Boolean(t.stages && t.stages.length > 1);
            const hasNextStage = Boolean(hasMultipleStages && t.currentStageIndex < t.stages.length - 1);

            let cardClass = 'timer-active-card';
            if (isDone) cardClass += ' timer-card-done';
            if (t.paused) cardClass += ' timer-card-paused';

            let stageBadgeHtml = '';
            if (hasMultipleStages) {
                stageBadgeHtml = `
                    <span class="timer-stage-badge" title="Stage ${t.currentStageIndex + 1} of ${t.stages.length}">
                        Stage ${t.currentStageIndex + 1}/${t.stages.length}: ${escapeHtml(t.stageName || '')}
                    </span>
                `;
            }

            let subTextHtml = '';
            if (isDone) {
                const readyLabel = hasMultipleStages ? '🐔 ADULT CHICKEN READY!' : '✨ READY TO HARVEST!';
                subTextHtml = `<span class="timer-ready-badge">${readyLabel}</span>`;
            } else {
                let nextStageSnippet = '';
                if (hasNextStage) {
                    const nextSt = t.stages[t.currentStageIndex + 1];
                    nextStageSnippet = ` &bull; <span style="color: #fbbf24; font-weight: 500;">Next: ${escapeHtml(nextSt.name)}</span>`;
                }
                subTextHtml = `<span>${rem}s to go</span> &bull; <span>ETA: ${formatClockTime(t.targetAt)}</span>${nextStageSnippet}`;
            }

            html += `
                <div class="${cardClass}" id="card_${t.id}">
                    <div class="timer-card-top">
                        <div class="timer-card-info">
                            <span class="timer-card-icon">${t.icon || '⏱️'}</span>
                            <div class="timer-card-headings">
                                <div class="timer-card-title">
                                    <strong>${t.name}</strong>
                                    ${stageBadgeHtml}
                                    ${t.note ? `<span class="timer-card-note">📍 ${escapeHtml(t.note)}</span>` : ''}
                                </div>
                                <div class="timer-card-sub">
                                    ${subTextHtml}
                                </div>
                            </div>
                        </div>
                        <div class="timer-card-countdown ${isDone ? 'done' : ''} ${t.paused ? 'paused' : ''}">
                            ${isDone ? 'READY' : `${rem}s`}
                        </div>
                    </div>

                    <div class="timer-progress-bar-bg">
                        <div class="timer-progress-bar-fill ${isDone ? 'done' : ''} ${t.paused ? 'paused' : ''}" style="width: ${pct}%;"></div>
                    </div>

                    <div class="timer-card-actions">
                        ${isDone ? `
                            <button type="button" class="btn btn-secondary btn-sm" onclick="restartTimer('${t.id}')" title="Restart full lifecycle">
                                🔄 Restart (${t.durationSec}s)
                            </button>
                            <button type="button" class="btn btn-primary btn-sm" onclick="deleteTimer('${t.id}')" style="background: var(--status-running); border-color: var(--status-running);">
                                ✓ Done / Collected
                            </button>
                        ` : `
                            <div class="timer-action-group-left">
                                <button type="button" class="btn btn-secondary btn-sm" onclick="restartTimer('${t.id}')" title="Restart timer">
                                    🔄 Restart
                                </button>
                                ${hasNextStage ? `
                                    <button type="button" class="btn btn-secondary btn-sm" onclick="advanceTimerStage('${t.id}')" title="Skip or advance to next stage (${escapeHtml(t.stages[t.currentStageIndex + 1].name)})" style="color: #fbbf24; border-color: rgba(245, 158, 11, 0.35);">
                                        ⏭️ Next Stage
                                    </button>
                                ` : ''}
                                <button type="button" class="btn btn-secondary btn-sm" onclick="extendTimer('${t.id}', 300)" title="Add +300s (5m)">
                                    +300s
                                </button>
                                <button type="button" class="btn btn-secondary btn-sm" onclick="togglePauseTimer('${t.id}')" title="${t.paused ? 'Resume timer' : 'Pause timer'}">
                                    ${t.paused ? '▶️ Resume' : '⏸️ Pause'}
                                </button>
                            </div>
                            <button type="button" class="btn btn-danger btn-sm" onclick="deleteTimer('${t.id}')" title="Remove timer">
                                🗑️
                            </button>
                        `}
                    </div>
                </div>
            `;
        });

        list.innerHTML = html;
    }

    function tick() {
        if (timers.length === 0) {
            updateBadge();
            renderFloatingHud();
            return;
        }

        const now = Date.now();
        let changed = false;

        timers.forEach(t => {
            if (t.paused) return;

            const rem = Math.max(0, Math.round((t.targetAt - now) / 1000));
            if (rem <= 0 && !t.notified) {
                // Check if this is a multi-stage timer that should auto-advance to next stage
                if (t.stages && t.currentStageIndex < t.stages.length - 1) {
                    const finishedStage = t.stages[t.currentStageIndex];
                    t.currentStageIndex += 1;
                    const nextStage = t.stages[t.currentStageIndex];

                    // Transition to next stage
                    t.stageName = nextStage.name;
                    t.icon = nextStage.icon || t.icon;
                    t.durationSec = nextStage.durationSec;
                    t.remainingSec = nextStage.durationSec;
                    t.startedAt = now;
                    t.targetAt = now + (nextStage.durationSec * 1000);
                    t.notified = false;
                    changed = true;

                    // Sound chime
                    playVikingChime();

                    // Desktop Notification
                    sendDesktopNotification(
                        `🐣 Stage Complete: ${finishedStage.name}!`,
                        `"${t.name}": Advanced to Stage ${t.currentStageIndex + 1} (${nextStage.name}, ${formatDuration(nextStage.durationSec)}).`,
                        nextStage.icon || t.icon
                    );

                    // In-App Toast
                    if (typeof window.showToast === 'function') {
                        window.showToast(`🐣 ${finishedStage.name} finished! Now: ${nextStage.name}!`, 'success');
                    }
                    return;
                }

                // Final timer completion
                t.notified = true;
                changed = true;

                // Sound chime
                playVikingChime();

                // Desktop Notification
                const isHenLifecycle = Boolean(t.stages && t.stages.length > 1);
                const title = isHenLifecycle ? `🐔 Adult Chicken Ready!` : `⚔️ Valheim Timer Complete!`;
                const body = isHenLifecycle
                    ? `"${t.name}"${t.note ? ' (' + t.note + ')' : ''} has grown into an adult Hen ready for breeding & egg laying!`
                    : `"${t.name}"${t.note ? ' (' + t.note + ')' : ''} is ready to harvest or spawn!`;

                sendDesktopNotification(title, body, t.icon);

                // In-App Toast
                if (typeof window.showToast === 'function') {
                    const toastText = isHenLifecycle
                        ? `🐔 Ready: "${t.name}" has grown into a mature Hen!`
                        : `✨ Ready: "${t.name}" is ready to harvest!`;
                    window.showToast(toastText, 'success');
                }
            }
        });

        if (changed) {
            saveTimers();
        }

        updateBadge();
        renderFloatingHud();

        // If dropdown is open and showing active tab, re-render active cards
        const menu = document.getElementById('timers-dropdown-menu');
        if (menu && menu.classList.contains('show') && currentTab === 'active') {
            renderActiveTimers();
        }
    }

    function escapeHtml(text) {
        if (!text) return '';
        return text
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;')
            .replace(/'/g, '&#039;');
    }

    // Expose control functions to global window
    window.deleteTimer = deleteTimer;
    window.togglePauseTimer = togglePauseTimer;
    window.restartTimer = restartTimer;
    window.advanceTimerStage = advanceTimerStage;
    window.extendTimer = extendTimer;
    window.clearCompletedTimers = clearCompletedTimers;
    window.clearAllTimers = clearAllTimers;

    // Document ready initialization
    document.addEventListener('DOMContentLoaded', function() {
        loadTimers();
        updateSoundButton();
        updateNotifyButton();
        updateBadge();
        initDraggableHud();
        renderFloatingHud();

        // Close on clicking outside dropdown
        document.addEventListener('click', function(e) {
            const container = document.querySelector('.timers-dropdown-container');
            if (container && !container.contains(e.target)) {
                closeTimersDropdown();
            }
        });

        // Close on Escape key
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') {
                closeTimersDropdown();
            }
        });

        // Start countdown ticker
        if (tickerInterval) clearInterval(tickerInterval);
        tickerInterval = setInterval(tick, 1000);
    });

})();
