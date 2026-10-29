# Valheim Server Manager: Current-State Specification (v1)

| | |
| :--- | :--- |
| **Status** | As-built. Describes what the code does today, not what it should do. |
| **Baseline** | Commit `67b6d7c` on `main` (World Map tab), 2026-10-30 |
| **Purpose** | Input to a first-principles redesign (step 2). |

This document catalogues every feature in the current version, the data each one depends on, who can use it, and how the pieces fit together. Section 10 lists inconsistencies and risks found while reading the code, so the redesign can decide what to keep, merge, fix, or drop.

---

## Contents

1. [System Overview](#1-system-overview)
2. [Users & Permissions](#2-users--permissions)
3. [Feature Catalogue](#3-feature-catalogue)
4. [Data & Persistence](#4-data--persistence)
5. [Data Sources Parsed](#5-data-sources-parsed)
6. [HTTP Interface](#6-http-interface)
7. [Live-Update Model](#7-live-update-model)
8. [Runtime & Environment](#8-runtime--environment)
9. [Test Coverage](#9-test-coverage)
10. [Observations for the Redesign](#10-observations-for-the-redesign)

---

## 1. System Overview

### 1.1 What it is

A single-host web control panel for a Valheim Dedicated Server (Steam AppID `892970`) running on a Fedora Linux laptop. One Python process supervises the game server, parses its logs and save files, and serves a browser dashboard to the host owner and their friends.

### 1.2 Context

```
 Friends (game clients) ──UDP──► Playit.gg tunnel ──► 127.0.0.1:2456 ─┐
                                                                      ▼
 Browser (LAN / mobile) ──HTTP :8080──► manager.py ──spawns──► valheim_server.x86_64
                                          │                           │
                                          │ reads/writes              │ writes
                                          ▼                           ▼
                         JSON state files, backups/,      valheim_server.log,
                         logs/, ~/.config/systemd         ~/.config/unity3d/IronGate/Valheim/
                                                          (worlds, configs, Player.log)
```

### 1.3 Components

| Component | Location | Responsibility |
| :--- | :--- | :--- |
| Launcher | `manager.py` | Re-executes inside the venv, applies the power policy, runs Uvicorn on `0.0.0.0:8080` with `reload=True`. |
| Web app | `webmanager/app.py` | FastAPI routes: HTML pages, HTMX partials, JSON, SSE. Role checks are done per route. |
| Server supervisor | `core/server.py` | `ValheimServerManager` singleton: process lifecycle, monitor thread, status aggregation. |
| Config | `core/config.py` | Load, validate and save `server_config.json`, including world modifiers. |
| Auth | `core/auth.py` | Users, password hashing, in-memory sessions, login audit log. |
| Backups | `core/backups.py` | Backup create/list/inspect/restore/delete, world `.fwl` creation, seed reading. |
| Logs | `core/logs.py` | Tail reader and SSE log stream generator. |
| Player tracker | `core/players.py` | `PLAYER_TRACKER` singleton: player identities, sessions, deaths, colours, hide/delete. |
| Server history | `core/server_history.py` | `SERVER_HISTORY` singleton: server runs, uptime, world saves. |
| World stats | `core/world_stats.py` | `WORLD_STATS` singleton: in-game day and time, raids, bosses, altars, dungeons, portals, sleep. |
| Timeline | `core/timeline.py` | `TIMELINE_ENGINE`: merges the other trackers into hour-sized chronological chunks. |
| Map | `core/map.py` | Portal extraction from world chunk files, map coordinate model. |
| Playit | `core/playit.py` | Background detection of the Playit.gg public tunnel address. |
| System | `core/system.py` | CPU clocks, power profile, sleep inhibition, systemd unit and linger, host reboot. |
| UI | `templates/`, `static/` | Jinja2 pages with HTMX partials, custom CSS, Leaflet map, timers HUD. |

### 1.4 Runtime model

- **Process**: one Uvicorn worker (with dev auto-reload) hosting FastAPI.
- **Background threads**:
  - Server monitor loop (every 3 s): reads new log lines into the three trackers and detects crashes.
  - Playit monitor (every 60 s).
  - Short-lived threads for start, stop, restart and reboot.
  - A detached `systemd-inhibit sleep infinity` process.
- **Game server**: spawned with `start_new_session=True`, so it survives restarts of the web process. On startup the manager re-attaches to an already-running server, found with `pgrep -f valheim_server.x86_64`.
- **State**: module-level singletons created on import, persisted to JSON files beside the server binary.

---

## 2. Users & Permissions

### 2.1 Roles

| Role | Intended scope (as described in the Accounts UI) |
| :--- | :--- |
| `admin` | Full access: server controls, config, backups, users. |
| `operator` | Server controls and creating backups. Config is read-only. |
| `viewer` | Read-only: dashboard, telemetry, logs. |

The first run creates `admin` / `admin`. Anyone who can reach the login page can **self-register** and gets the `viewer` role.

### 2.2 Permission matrix (as enforced server-side)

| Capability | Viewer | Operator | Admin |
| :--- | :---: | :---: | :---: |
| View dashboard, saga, timeline, map, logs, backups list | ✅ | ✅ | ✅ |
| See server password, seed, Playit address (connection card, `/api/status`) | ✅ | ✅ | ✅ |
| Start / stop / restart server | ❌ | ✅ | ✅ |
| Reboot host | ❌ | Only if all cores < 600 MHz | ✅ |
| Record boss defeat | ❌ | ✅ | ✅ |
| Create backup | ❌ | ✅ | ✅ |
| Download backup | ❌ | ✅ | ✅ |
| Restore / delete backup | ❌ | ❌ | ✅ |
| Edit config, create world | ❌ | ❌ | ✅ |
| View session history, hidden players | ❌ | ❌ | ✅ |
| Hide / unhide / delete / recolour player | ❌ | ❌ | ✅ |
| Manage users, view/clear login audit | ❌ | ❌ | ✅ |
| Change own password | ✅ | ✅ | ✅ |

---

## 3. Feature Catalogue

Each feature has an ID for reference in the redesign.

### F1. Server Lifecycle

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F1.1 | Start | Validates password ≥ 5 chars. Force-kills (`SIGKILL`) any orphan `valheim_server.x86_64`, rotates the log, builds the CLI (see F2), and spawns with `LD_LIBRARY_PATH=linux64` and `SteamAppId=892970`. State goes `Starting` → `Running` as soon as the process spawns, not when the world is loaded. |
| F1.2 | Stop | Sends `SIGINT` and waits up to 30 s. A spawned process is then sent `SIGTERM`. An attached PID is sent `SIGKILL` after the wait **even if it already exited** (the call is harmless in that case). Closes open player sessions and records the run as a clean stop. |
| F1.3 | Restart | Stop, poll up to 35 s for `Stopped`, then start. Recorded with trigger "Web Restart". |
| F1.4 | Attach on startup | If a server is already running, adopts its PID, works out uptime from `ps etimes`, and records an "attached" run. |
| F1.5 | Crash detection & auto-restart | The monitor detects an unexpected exit, records it as an abnormal exit, and restarts after 5 s if `auto_restart` is on. Auto-restart is skipped once 3 crashes happen within 60 s. There is no reset or notification. |
| F1.6 | Auto-start on boot | If `auto_start_on_boot` is on and nothing is running when the web app starts, the server is started (trigger "Auto-Start on Boot"). This also fires on every dev auto-reload of the web process if the server is stopped. |
| F1.7 | Status | State, PID, CPU % and RSS from `ps`, and uptime `HH:MM:SS`. |
| F1.8 | Host reboot | Stops the server, waits 2 s (not for the stop to finish), then tries in turn: `systemctl reboot --ignore-inhibitors`, `sudo -n systemctl reboot`, logind D-Bus `Reboot`, and `sudo -n reboot`. |
| F1.9 | CPU throttle alert | If every core reads < 600 MHz, the status card shows a warning and an "Emergency Reboot Host" button. |

### F2. Configuration & World Modifiers

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F2.1 | Server settings | Server name, world name (dropdown of detected worlds), password (≥ 5), port, Playit address override, auto-start on boot, auto-restart. Saved atomically to `server_config.json`. |
| F2.2 | World modifiers | Preset (normal, casual, easy, hard, hardcore, immersive, hammer, or custom), combat, death penalty, resources, raids, portals. Validated against fixed lists. |
| F2.3 | Global keys | `nobuildcost`, `playerevents`, `passivemobs`, `nomap`, passed as `-setkey`. |
| F2.4 | Reset modifiers | One-shot `-resetmodifiers` flag, cleared automatically after the next launch. |
| F2.5 | CLI builder | `-name -port -world -password [-resetmodifiers] [-preset] [-modifier k v]… [-setkey k]…`. The preset goes before individual modifiers. `-crossplay` is never added. |
| F2.6 | Apply semantics | Saving config does **not** restart the server. Changes apply on the next start. |

### F3. World Management

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F3.1 | World discovery | Lists `*.fwl` and `*.db` names in `worlds_local/` and `worlds/`, excluding backup, `.old` and `.dang` files. Falls back to `["Dedicated"]` if none are found. |
| F3.2 | Create world | Admin enters a name (sanitised) and an optional seed (random 10-char if blank). Options: back up the current world first, switch the active world, start the server. Writes a hand-built `.fwl` (version 37) to `worlds_local/`; Valheim generates the world on first launch. |
| F3.3 | Random seed | `GET /api/world/random-seed` returns a 10-char alphanumeric seed. This endpoint has **no auth check**. |
| F3.4 | Seed display | Reads the seed string from the active world's `.fwl` and shows it on the connection card. |

### F4. Backups & Restore

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F4.1 | Create | Zip `valheim_backup_<world>_<timestamp>.zip` (format v2, with `manifest.json`) containing: <br>• `world/<folder>/…`: the **entire** `worlds_local` or `worlds` folder, whichever holds the active world. This includes every other world in that folder. <br>• `configs/server/…`: `adminlist.txt`, `bannedlist.txt`, `permittedlist.txt`, blocked-player lists, `prefs`. <br>• `data/…`: every `*.json` in the server dir (config, users, histories, stats). This includes **password hashes and the server password**. <br>• `logs/…`: current server log, rotated logs, `Player.log`/`Player-prev.log`. <br>Manual only: no schedule, no retention. |
| F4.2 | List | Newest first, with size, date, and type (safety snapshot / full v2 / legacy v1 world-only). |
| F4.3 | Inspect | The restore dialog shows file count, version, and which components are present. |
| F4.4 | Restore | Admin chooses world, server configs, manager data, logs, "clean restore" (purge the active world's files first), and restart afterwards. The flow: stop server → **safety snapshot** (`valheim_safety_pre_restore_…zip`) → zip-slip check → extract the selected parts to their original locations → optionally start. Legacy v1 archives restore world files only. |
| F4.5 | Download | Operator/admin. Served as `application/zip`. |
| F4.6 | Delete | Admin. The path is restricted to `backups/*.zip`. |

### F5. Logs

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F5.1 | Rotation | On each start, the current `valheim_server.log` is copied to `logs/valheim_server_<timestamp>.log`, then truncated. No pruning, so `logs/` grows without limit. |
| F5.2 | Live terminal | SSE stream: the last 100 lines, then new bytes every 0.5 s. Handles truncation. The UI has auto-scroll and a client-side clear. Shown on both the Dashboard and Logs tabs. |
| F5.3 | Raw server history log | `GET /api/server/history/log` returns `logs/server_history.log` as plain text. |
| F5.4 | Tail reader | Reverse-seek reader for the last N lines. |

### F6. Player Tracking

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F6.1 | Online detection | Login comes from a `Got character ZDOID from <name> : <id>` line with a non-zero ID. Logout comes from `Closing socket <steamid>` for an online player, `Connections 0`, or a shutdown line. |
| F6.2 | Steam ID correlation | Pairs `Got connection SteamID` handshakes with the next character login in FIFO order. Also learns names from `Player history entry` lines. |
| F6.3 | Deaths | A ZDOID of `0:0` counts as a death; the session stays open. Per-player and total counts. |
| F6.4 | Sessions & playtime | Login/logout events, session duration, total playtime, session count, first and last seen. `last_seen` is refreshed by `Sent to <steamid>` lines. |
| F6.5 | Backfill | If the history is empty, rebuilds it from every rotated log plus the current log. |
| F6.6 | Player directory (Players tab) | Stat tiles (online, known, deaths, hidden, sessions, server state). Table with filter (all / visible / hidden), status, playtime, sessions, deaths, first and last active, and Steam ID. |
| F6.7 | Player colours & avatars | Each player gets a colour and emoji avatar from a 12-colour palette (5 player names are hard-coded with fixed colours). Admins can change the colour. The colour is used on every screen that shows a player. |
| F6.8 | Hide / unhide | Hidden players are excluded from counts and lists for non-admins. |
| F6.9 | Delete | Removes the player and their sessions and events. Reappears if they log in again. |
| F6.10 | Session history (admin) | The last 100 login, logout and death events with durations. The panel height can be resized. |

### F7. Server Run History & World Saves

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F7.1 | Runs | Each run records start/stop, duration, world, port, PID, trigger (dashboard, restart, auto-start, crash restart, attached), and exit status (clean / abnormal / attached terminated). |
| F7.2 | World saves | Counts saves from `World save (1/5)…(5/5)` and classic `World saved (…)` lines, per run, with last save time. |
| F7.3 | Summary | Total uptime, longest run, total runs, total saves, current run. |
| F7.4 | Plain-text log | Human-readable lines appended to `logs/server_history.log`. |
| F7.5 | Backfill | Rebuilds runs from rotated logs if history is empty. Backfills saves for existing runs. |

### F8. World Stats ("Saga")

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F8.1 | In-game calendar & clock | Base time comes from `net_time` read out of the world save (`.db2` / `.db`) or from `Time …, day:N` log lines. The clock advances in real time **only while the server runs and players are online**. 1 day = 1800 s, dawn at 06:00, dusk at 18:00. Shows phase and sub-phase, a 24 h clock, and a countdown to sunrise or sunset. |
| F8.2 | Nights slept | Counted from `Time` lines with `skipspeed > 1`. |
| F8.3 | Raids | `Random event set:<code>` mapped to a title, quote, biome and icon (`EVENT_LORE`). Tracks active raid, history, and total. Time remaining is estimated from a fixed 180 s raid length. |
| F8.4 | Boss progression | The 7 Forsaken bosses in order (Eikthyr → Fader). Each has a status: unlocated → altar located → engaged → slain ×N, with the in-game day and time of defeat. |
| F8.5 | Boss sources | Global keys (`defeated_*`) and `activebosses` read from the world save. Boss music start/stop in `Player.log` (the host's **own game client** log, so it only works if the host plays on the same machine). Altar discovery from `Found location of type …`. |
| F8.6 | Manual boss defeat | Operator/admin can "Record Defeat" / "+1 Kill". |
| F8.7 | Dungeons | Counts dungeons entered and rooms placed. |
| F8.8 | Portals (log-based) | Portal count at boot and pair links from `ConnectPortals` lines. |
| F8.9 | Raid / boss alert banner | Global banner while a raid or boss battle is active, polled every 3 s. |
| F8.10 | World summary bar | Compact version of the saga header on the Dashboard. |

### F9. Activity Timeline

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F9.1 | Chronology | Vertical timeline from now backwards in 1-hour chunks. Consecutive idle hours collapse into one "idle" block. Infinite scroll loads the next chunk when revealed. |
| F9.2 | Lanes | One swimlane per player showing presence (sessions) and deaths, in player colours. |
| F9.3 | Events | Sunrise and sunset markers (computed from in-game time), boss defeats, raids, altar discoveries. |
| F9.4 | Filters | Toggle sunrise, sunset, bosses, raids and altars. Stored in `sessionStorage`. |
| F9.5 | Highlighting | Hovering a player highlights their activity across the timeline. |

### F10. World Map

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F10.1 | Map view | Leaflet viewer over a **static 6144×6144 image exported for one specific seed (`6d8gTfltRB`)**, with spawn at fixed coordinates. Mouse-over shows game coordinates, and there's a fullscreen mode. |
| F10.2 | Portals | Parsed from the active world's chunk files (`00_01__*.chunk`): tag, builder, position, linked or solo. Drawn as markers, with routes between linked pairs. |
| F10.3 | Portal directory & filters | Search by tag or builder, filter by builder or link status, and layer toggles (portals, routes, spawn). |
| F10.4 | Refresh | `POST /api/map/refresh` re-scans the chunk files. Otherwise results are cached by chunk modification time. |

### F11. Connection Info & Playit.gg

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F11.1 | Connection card | Server name, password, Playit address (manual, or auto-detected with a "Live" badge), port, active world, modifier badges, seed. Per-field copy buttons, plus "Copy Join Info" (name, address, password). If no Playit address is set, the copied address falls back to `127.0.0.1:<port>`. |
| F11.2 | Tunnel detection | First tries the `playitd` IPC socket (`/run/playit/playitd.sock`, `get_state`), then the tail of `/var/log/playit/playit.log`. Matches the tunnel whose destination is the game port, otherwise uses the first one found. The background monitor always checks port **2456**, whatever port is configured. |

### F12. Host Telemetry & Power Policy

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F12.1 | CPU clocks | Min, avg and max MHz across cores from `/proc/cpuinfo` (sysfs fallback). |
| F12.2 | Power profile | Reads via `net.hadess.PowerProfiles`, then `tuned-adm`, then the CPU governor. |
| F12.3 | Power policy (on every web process start) | Sets the `performance` profile. Writes KDE PowerDevil settings (no auto-suspend on AC, lid action = turn off screen). Sets GNOME `sleep-inactive-ac-type=nothing`. Spawns a `systemd-inhibit` lock for lid-switch and sleep. |
| F12.4 | Boot service | Rewrites `~/.config/systemd/user/valheim.service` to wrap the command in `systemd-inhibit` if it isn't already. Enables the unit and runs `loginctl enable-linger`. The unit file itself is not in the repo. |

### F13. Accounts, Authentication & Audit

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F13.1 | Login | Username is case-insensitive. PBKDF2-SHA256 with 100k iterations and a random salt. Session cookie `valheim_session` (HttpOnly, SameSite=Lax, not Secure) lasting 7 days, extended on every request. |
| F13.2 | Sessions | Held **in memory only**, so every restart or dev reload logs everyone out. The role is cached in the session, so a role change applies only after the user logs in again. |
| F13.3 | Self-registration | Public. Creates a `viewer` and logs them in. |
| F13.4 | User admin | Create (any role), delete, change role. The last admin can't be removed or demoted. Usernames are ≥ 3 chars; passwords ≥ 4 chars. |
| F13.5 | Change own password | Modal in the header. No current-password check. |
| F13.6 | Login audit | Success, failure and registration events, with IP (`X-Forwarded-For` trusted), parsed browser and OS, and raw user agent. Capped at 100 entries. Admin can clear it. A fake "admin success" entry is added if the log is empty. |

### F14. Timers HUD (client-side only)

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F14.1 | Presets | About 35 in-game timers: breeding, berries and mushrooms, crops, spawners (surtling geyser, greydwarf nest, …), base production (beehive, fermenter, smelter, eitr refinery), and cycles (day/night, boss power, rested). Custom timers are also possible. |
| F14.2 | HUD | Floating, draggable, minimisable window with circular progress rings and per-second countdown. |
| F14.3 | Alerts | Optional sound and browser notifications. |
| F14.4 | Storage | `localStorage` only, so timers are per browser and not shared between players. They also run on real time, not in-game time. |

### F15. UI Shell

| ID | Feature | Behaviour |
| :--- | :--- | :--- |
| F15.1 | Tabs | Dashboard, World Map, Saga, Timeline, Players, Backups, Config, Logs, Accounts (admin). Content is loaded lazily via `/partials/tabs/{name}`. The active tab is kept in the URL hash, so it can be bookmarked. |
| F15.2 | Header status | Server state badge, players, in-game time. Polled every 3 s. |
| F15.3 | Toasts | Every action returns `HX-Trigger: showToast`. Many actions also broadcast refresh events (see §7). |
| F15.4 | Mobile | Responsive layout with a dropdown tab menu on narrow screens. Has a PWA manifest and icons. |
| F15.5 | Theme | Dark "Viking" theme. Styling is split between a 3.7k-line `style.css` and extensive inline `style=""` attributes in templates. |

---

## 4. Data & Persistence

All paths are relative to the server install directory unless absolute.

| File / Dir | Owner | Contents | In backup |
| :--- | :--- | :--- | :---: |
| `server_config.json` | config | Server settings, plaintext server password, modifiers | ✅ data |
| `users.json` | auth | Usernames, salts, hashes, roles | ✅ data |
| `login_history.json` | auth | Login audit (≤ 100) | ✅ data |
| `player_history.json` | players | Known players, sessions, events, Steam ID maps, deleted list | ✅ data |
| `server_history.json` | server_history | Runs and saves | ✅ data |
| `world_stats.json` | world_stats | Day, raids, bosses, altars, dungeons, portals, sleep events | ✅ data |
| `valheim_server.log` | server | Current game stdout/stderr | ✅ logs |
| `logs/valheim_server_*.log` | server | Rotated game logs (unbounded) | ✅ logs |
| `logs/server_history.log` | server_history | Human-readable run log | ✅ logs |
| `backups/*.zip` | backups | Archives (unbounded) | ❌ |
| `~/.config/unity3d/IronGate/Valheim/worlds_local/`, `worlds/` | Valheim | World `.fwl`, `.db`, chunked dirs | ✅ world |
| `~/.config/unity3d/IronGate/Valheim/*.txt`, `prefs` | Valheim | Admin, ban and permit lists | ✅ configs |
| `~/.config/unity3d/IronGate/Valheim/Player*.log` | Valheim client | Host's game client log | ✅ logs |
| `~/.config/systemd/user/valheim.service` | system | User unit (edited in place) | ❌ |
| Browser `localStorage` / `sessionStorage` | UI | Timers, HUD position, timeline filters | ❌ |
| Process memory | auth | Sessions | ❌ |

Writes are mostly atomic (temp file then rename). Every tracker keeps its own JSON file, lock, and log offset.

---

## 5. Data Sources Parsed

The app infers most of its state by parsing text and binary files it doesn't own. These dependencies are the most fragile part of the system, because a Valheim update can change any of them.

| Source | Pattern / structure | Used by |
| :--- | :--- | :--- |
| Server log | `MM/DD/YYYY HH:MM:SS:` timestamp prefix | all trackers |
| | `Got character ZDOID from <name> : <id>` | login (F6.1), death if `0:0` (F6.3) |
| | `Got connection SteamID <id>` / `Closing socket <id>` / `Sent to <id>` | Steam correlation, logout, last seen |
| | `Player history entry with index N: <name> (Steam_<id>,` | name ↔ Steam ID |
| | `Connections <n> ZDOS:` | all offline when n = 0 |
| | `OnApplicationQuit` / `ZNet Shutdown` / `ZNet OnDestroy` / … | shutdown |
| | `Load world: <name>` | run's world |
| | `World save (1/5) … Save number N`, `World save (5/5) done. Total time [..]`, `World saved (..)` | save counting |
| | `Random event set:<code>` | raids |
| | `Time <t>, day:<n> … nextm … skipspeed` | day, net time, sleep |
| | `Found location of type <type>` | boss altars |
| | `Dungeon loaded with` / `Placed <n> rooms` | dungeons |
| | `ConnectPortals => Connected <n> portals` / `Connected portals a <-> b` | portal counts |
| `Player.log`, `Player-prev.log` | `Starting/Stopped music boss_<id>` | boss battle and kill detection |
| World `.fwl` | Binary: version, name, seed string, seed hash, UID | seed display, world creation |
| World `_main.*.db2` / `.db` | Binary: global keys, `activebosses`, `net_time` | boss keys, in-game clock |
| World chunks `00_01__*.chunk` | Binary ZDO scan for portals (tag, builder, position) | map |
| `playitd.sock` / `playit.log` | JSON IPC `get_state` / `addr => 127.0.0.1:port` | Playit address |
| `/proc/cpuinfo`, sysfs, D-Bus, `tuned-adm`, `systemd-inhibit`, `loginctl`, `ps`, `pgrep` | System commands | telemetry, power policy, supervision |

---

## 6. HTTP Interface

`A` = admin, `O` = operator, `V` = viewer, `—` = no auth check.

### 6.1 Pages & partials (HTML)

| Method | Path | Auth | Purpose |
| :--- | :--- | :---: | :--- |
| GET | `/login` | — | Login and registration page |
| GET | `/` | V | Dashboard shell |
| GET | `/partials/tabs/{name}` | V (accounts: A) | Lazy tab content (`dashboard`, `map`, `saga`, `timeline`, `players`, `backups`, `config`, `logs`, `accounts` and aliases) |
| GET | `/partials/status` | V | Status card, emits change triggers |
| GET | `/partials/header-status` | **—** | Header badge |
| GET | `/partials/raid-alert` | **—** | Raid / boss banner |
| GET | `/partials/world-saga` | V | Boss progression card |
| GET | `/partials/world-summary` | V | Saga summary bar |
| GET | `/partials/players` | V | Player list (`panel_id` param) |
| GET | `/partials/players-tab` | V | Players tab |
| GET | `/partials/session-history` | A | Session events |
| GET | `/partials/server-history` | V | Run history |
| GET | `/partials/backups` | V | Backup table |
| GET | `/partials/backup/restore-modal/{file}` | A | Restore dialog |
| GET | `/partials/config` | V | Config form (read-only for non-admins) |
| GET | `/partials/connection` | V | Connection card |
| GET | `/partials/accounts` | A | Accounts panel |
| GET | `/partials/timeline/chunk` | V | Next timeline chunk (`before_ts`) |

### 6.2 Actions

| Method | Path | Auth | Purpose |
| :--- | :--- | :---: | :--- |
| POST | `/api/auth/login` · `/register` · `/logout` | — | Session management |
| POST | `/api/auth/change-password` | V | Own password |
| POST | `/api/server/start` · `/stop` · `/restart` | O | Lifecycle |
| POST | `/api/system/reboot` | A (O if throttled) | Host reboot |
| POST | `/api/config` | A | Save config |
| GET | `/api/world/random-seed` | **—** | Random seed |
| POST | `/api/world/create` | A | Create world |
| POST | `/api/backups/create` | O | Create backup |
| POST | `/api/backups/restore/{file}` | A | Restore |
| DELETE | `/api/backups/delete/{file}` | A | Delete |
| GET | `/api/backups/download/{file}` | O | Download |
| POST | `/api/boss/record-defeat/{id}` | O | Manual boss kill |
| POST | `/api/players/hide/{name}` · `/unhide/{name}` · `/set-color/{name}` | A | Player admin |
| POST/DELETE | `/api/players/delete/{name}` | A | Delete player |
| POST | `/api/users/create` · `/update-role/{u}` · `/login-history/clear` | A | User admin |
| DELETE | `/api/users/delete/{u}` | A | Delete user |

### 6.3 Data & streams

| Method | Path | Auth | Purpose |
| :--- | :--- | :---: | :--- |
| GET | `/api/status` | V | Full status JSON (non-admins get hidden players and sessions filtered out) |
| GET | `/api/stream/logs` | V | SSE `log-chunk` events |
| GET | `/api/server/history/log` | V | Plain-text run log |
| GET | `/api/map/data` | V | Map dataset |
| POST | `/api/map/refresh` | V | Re-scan portals (any role) |

Responses for actions are HTML fragments for HTMX, not JSON. There is no versioned JSON API. `.agents/AGENTS.md` lists `GET /api/config`, `/api/backups` and `/api/logs`, but **they don't exist**.

---

## 7. Live-Update Model

The UI combines fixed-interval polling with server-sent HTMX events.

| Element | Trigger |
| :--- | :--- |
| Status card | every **2 s** |
| Header status | load, `statusChanged`, every **3 s** |
| Raid alert banner | `statusChanged`, `raidAlertUpdated`, every **3 s** |
| World saga card | every **5 s**, `worldSagaUpdated` |
| World summary bar | every **5 s**, `worldSagaUpdated` |
| Player list | `statusChanged`, `playersUpdated` |
| Session history | `sessionHistoryUpdated`, `statusChanged` |
| Server history | `serverHistoryUpdated` |
| Connection card | `configUpdated` |
| Backups table | `refreshBackups` |
| Accounts | `accountsUpdated` |
| Log terminal | SSE, 0.5 s server-side poll |
| Timeline | `revealed` (infinite scroll) |

`/partials/status` compares the current snapshot with the previous one (held in a **module-global, shared across all browsers**) and emits `statusChanged`, `sessionHistoryUpdated`, `serverHistoryUpdated`, `worldSagaUpdated` and `raidAlertUpdated` when values change. Every action response also broadcasts `statusChanged`, `configUpdated`, `serverHistoryUpdated` and `sessionHistoryUpdated`, whether or not the action affected them.

Each poll calls `SERVER_MANAGER.get_status_data()`, which shells out to `ps`, and to `busctl` / `systemd-inhibit` / `tuned-adm` for the power status, reads `/proc/cpuinfo`, parses the `.fwl` for the seed, and runs every tracker's summary. With one open tab that's roughly 5 full status builds every 5 seconds, and it scales with the number of open tabs.

---

## 8. Runtime & Environment

| Aspect | As-built |
| :--- | :--- |
| OS | Fedora Linux, systemd user session, KDE Plasma 6 or GNOME |
| Python | 3.x in `webmanager/.venv` (AGENTS.md says 3.14+). FastAPI ≥ 0.115, Uvicorn ≥ 0.30, Jinja2, python-multipart, aiofiles |
| Frontend | HTMX 2 + SSE extension, Leaflet, vanilla JS. All vendored, no build step |
| Install location | Repo files live **inside** the Valheim server install dir, next to `valheim_server.x86_64` |
| Service | `valheim.service` user unit. Per AGENTS.md it uses `KillMode=process`, so the game survives service restarts. The unit file isn't in the repo, so this can't be confirmed here. |
| Mode | Uvicorn `reload=True` (development hot reload) in normal operation |
| Network | HTTP only on `0.0.0.0:8080`. Game on UDP 2456/2457 via Playit |
| Legacy | `start_server.sh` (stock, uses `-crossplay`), `start_server_custom.sh` (old bash + `http.server` dashboard), `start_server_xterm.sh`, `docker/` + `docker_start_server.sh` (stock Iron Gate Docker image). None are used by the web manager. |

---

## 9. Test Coverage

`python3 -m unittest discover tests`: 6 files, 38 tests.

| File | Covers |
| :--- | :--- |
| `test_autostart.py` | Auto-start config default/persistence, start/no-start/attach logic, `/api/config` toggle, systemd unit check |
| `test_modifiers.py` | Modifier validation, CLI builder (defaults, presets, reset), config round-trip, `/api/config` |
| `test_in_game_time.py` | Time-of-day maths (noon, midnight, boundaries, freeze), rendering in partials, `/api/status` |
| `test_players_tab.py` | Players partials, tab routing, session-history admin gating, no polling on event-driven panels |
| `test_playit.py` | Socket and log detection, fallbacks |
| `test_server_history.py` | Save extraction (chunked and classic), summaries, live processing |

**Not covered**: auth and sessions, permissions per route, backup create/restore, world creation (`.fwl` writer), player log parsing, raid and boss detection, timeline, map/portal extraction, process stop and crash handling. Importing `app` creates real singletons that run `pgrep` and start monitor threads against real paths.

---

## 10. Observations for the Redesign

These are factual findings from reading the code, grouped by theme. They aren't fixes; they're inputs to the first-principles review.

### 10.1 Security

1. **Default credentials** `admin` / `admin` with no forced change.
2. **Open self-registration**: anyone who can reach port 8080 gets a viewer account, which can see the **server password** on the connection card and in `/api/status`.
3. **Unauthenticated endpoints**: `/partials/header-status`, `/partials/raid-alert`, `/api/world/random-seed`.
4. **No CSRF protection**, and the cookie is not `Secure`. Plain HTTP only.
5. **`X-Forwarded-For` trusted unconditionally** (and Uvicorn `forwarded_allow_ips="*"`), so audit IPs can be spoofed.
6. Changing your own password doesn't require the current one. The minimum app password length is 4.
7. **Backups contain secrets** (password hashes, plaintext server password) and are downloadable by operators.
8. Player names and usernames from logs and forms are rendered into HTML and `HX-Trigger` headers. Jinja auto-escaping covers templates, but some error HTML is built with f-strings (for example the registration error).
9. The fake "admin success" audit entry pollutes the audit trail.

### 10.2 Correctness & Robustness

1. "Running" is set when the process spawns, not when the world is ready to accept players.
2. Host reboot waits a fixed 2 s rather than for the 30 s graceful stop to finish.
3. Stopping an attached PID sends `SIGKILL` after the wait without checking whether the process already exited, and orphan cleanup on start uses `SIGKILL` with no chance to save.
4. Crash-loop pause is silent and permanent until the next manual start, with no UI indication.
5. The backup is described as "active world only" but zips the **whole** world folder (all worlds in `worlds_local`).
6. Sessions are lost on every restart or reload, and role changes don't apply until the user logs in again.
7. The Playit monitor always checks port 2456, whatever port is configured.
8. The map is hard-wired to one seed's image and spawn point, so it is wrong for any other world.
9. Boss-kill detection depends on the host's own **game client** log (`Player.log`), not the server.
10. Status-change detection uses one global "last state" shared by every client, so one browser's poll can swallow another browser's change event.
11. Auto-start on boot also fires on dev auto-reload when the server is stopped.
12. Default values disagree between modules: world name is `ValheimTest` (config, server), `TheOneWorld` (map), `world` (backups) or `Dedicated` (world list); password is `tester` (config) vs `changeme123` (example).

### 10.3 Architecture & Duplication

1. **Three independent log parsers** (players, server history, world stats), each with its own file offset, rotation handling, backfill, timestamp parser and JSON store. Each reads the same log every 3 s.
2. **One status object for everything**: `get_status_data()` mixes process, players, history, world, system, power and config, and is rebuilt on every poll for every partial, even ones that need a single field.
3. **Route boilerplate**: almost every route repeats auth lookup, role check, and template rendering. Role checks are hand-written strings per route rather than a shared policy.
4. **Business logic in routes**: world creation and restore coordinate stop/backup/start inside `app.py`.
5. **Singletons with side effects at import time** (process detection, threads, disk reads), which makes testing and reuse hard.
6. **Duplicate UI**: the saga header and summary bar share markup. The terminal appears on two tabs. The player list appears on Dashboard and Players.
7. **Styling split** between a 3.7k-line stylesheet and hundreds of inline styles, and a 1.3k-line timers script.
8. **Hard-coded game knowledge** (boss list, raid lore, timer durations, named player colours, map calibration) lives in code rather than data files.
9. **Install coupling**: the repo lives inside the Steam install directory, so paths are relative to the binary.
10. **Uvicorn dev reload in production** watches source files of the running service.

### 10.4 Performance

1. Polling at 2 to 5 s per panel, each running several subprocesses (`ps`, `busctl`, `systemd-inhibit`, `tuned-adm`) and file parses. This scales with open browser tabs.
2. `Player.log` and `Player-prev.log` are fully re-read on **every** world-stats summary, which runs on every status poll. Binary world files are re-parsed whenever their mtime changes.
3. Unbounded growth: `logs/`, `backups/`, and the in-memory and on-disk event lists for raids and sleep events.

### 10.5 Scope & Product

1. The features fall into roughly three groups: **server operations** (F1–F5, F11–F13), **community / world chronicle** (F6–F10), and **player utilities** (F14). They're mixed together across tabs.
2. Roadmap items still open: scheduled backups and retention, editing admin/ban/permit lists in the UI, telemetry charts, log search, HTTPS.
3. Legacy scripts and the Docker setup are unrelated to the current system.
4. `.agents/AGENTS.md` and parts of `README.md` describe endpoints and behaviour that differ from the code (see §6.3 and 10.2.5).
