# Valheim Local Server & Web Manager

A self-hosted web control panel for running a Valheim Dedicated Server (Steam AppID `892970`) on Fedora Linux. Built for personal laptop hosting (e.g. Framework 16), it combines a FastAPI + HTMX dashboard with process supervision, Playit.gg tunnel detection, world backups and restores, player and world tracking, multi-user accounts, and an automatic no-sleep power policy.

---

## Features

### Server Control & Supervision
- **Start / Stop / Restart** from the dashboard. Stop sends `SIGINT` and allows up to 30 seconds for Valheim to save the world before escalating.
- **PID Attachment**: If the manager restarts while Valheim is already running, it re-attaches to the existing process so Stop/Restart still work.
- **Orphan Cleanup**: Terminates stale `valheim_server.x86_64` instances on launch to prevent UDP port conflicts.
- **Auto-Restart with Crash-Loop Throttling**: Restarts after a crash, but pauses `auto_restart` after 3 crashes within 60 seconds.
- **Auto-Start on Boot**: Optional `auto_start_on_boot` launches the game server when the manager starts.
- **Safe Host Reboot**: Stops the game server cleanly before rebooting the machine.

### Dashboard (tabs: Dashboard, Saga, Timeline, Players, Backups, Config, Logs, Accounts)
- **Live Telemetry**: CPU load, RAM usage, CPU core frequencies, uptime, power profile, and online player count.
- **Connection Card**: Direct/Steam query port, Playit.gg address, password and world seed, each copyable, plus one-click **Copy Join Info**.
- **Raid Alert Banner**: Shows active random events / raids parsed from the server log.
- **Spawn & Growth Timers**: Floating, draggable HUD for crops, berries, fermenters, breeding, spawners and custom timers, with optional sound and browser notifications (stored in the browser).
- **Mobile-Friendly Layout**: Responsive design with a dropdown tab menu on small screens.

### World Tracking
- **World Saga**: Boss progression from the world's global keys, plus manual "record defeat" (admin/operator).
- **In-Game Clock**: Current in-game day, time of day, and sunrise/sunset.
- **World Stats**: Global keys, portals, raid history and world summary bar.
- **Activity Timeline**: Scrollable, paginated timeline of server and player events with per-player swimlanes.

### Players
- **Online Detection**: Parses `valheim_server.log` for connected characters, filtering out `0:0` death/respawn events.
- **Session History**: Per-player join/leave history and play durations.
- **Player Management**: Hide, unhide, or delete players from the list (admin).

### Logs & History
- **Live Log Terminal**: Streamed over Server-Sent Events with auto-scroll.
- **Efficient Tailing**: Reads from the end of the log file instead of loading the whole file.
- **Log Rotation**: On restart, `valheim_server.log` is archived to `logs/` with a timestamp rather than overwritten.
- **Server History**: Records each server run and world-save events.

### Configuration
- **Server Settings**: Server name, world name, port, password (min 5 characters), Playit.gg address, auto-start and auto-restart.
- **World Selector & Creation**: Pick an existing world from the save folder, or create a new one with a custom or random seed.
- **World Modifiers**: Difficulty presets (Normal, Casual, Easy, Hard, Hardcore, Immersive, Hammer), combat, death penalty, resource rate, raid frequency, portal rules, global keys (no build cost, player-based raids, passive mobs, no map), and modifier reset on launch.

### Backups & Restore
- **Create**: Timestamped `.zip` containing the active world folder only (`worlds_local` **or** `worlds`), server config files (`adminlist.txt`, `bannedlist.txt`, `permittedlist.txt`, prefs), web manager JSON data, and logs.
- **Inspect, Download, Delete** backups from the Backups tab.
- **Selective Restore**: Choose world, configs, manager data, and/or logs. The server is stopped cleanly first, a safety snapshot is taken, and the active world folder can be cleared before restoring so no stray files remain.

### Accounts & Security
- **Login Required**: PBKDF2-hashed passwords with session cookies.
- **Roles**: `admin` (full control, user management), `operator` (server operations), `viewer` (read-only). Self-registered accounts start as `viewer`.
- **Account Management**: Create/delete users, change roles and passwords (admin).
- **Login History**: Records IP address and device for each sign-in, with a clear option.

### Playit.gg Tunnel
- **Auto-Detection**: Background monitor finds the public tunnel address for the game port from the Playit agent.

### Power & Performance Policy
- Sets and confirms the `performance` power profile (via D-Bus / TuneD).
- Blocks sleep/suspend (including on lid close) using `systemd-inhibit`, while still letting the screen turn off and the session lock.
- Configures KDE PowerDevil or GNOME power settings.
- Ensures the `valheim.service` systemd user unit is set up and enabled to start on boot.

---

## Technical Stack & Requirements

| Component | Specification |
| :--- | :--- |
| **OS / Hardware** | Fedora Linux (Framework 16 Laptop) |
| **Game Server** | Valheim Dedicated Server (Steam AppID `892970`) |
| **Tunneling** | Playit.gg (UDP tunneling to `127.0.0.1:2456`, `-crossplay` disabled) |
| **Web Manager** | Python 3, FastAPI, Uvicorn, Jinja2 |
| **Frontend** | HTMX + SSE (vendored), vanilla JS, custom CSS |
| **Web UI Port** | `8080` (HTTP) |
| **Game Ports** | `2456` (Base Game Port UDP), `2457` (Query Port UDP) |

---

## Architecture Overview

```
               [ Browser / Mobile UI ]
                          │
              (HTTP 8080 · HTMX · SSE)
                          ▼
            ┌───────────────────────────┐
            │  manager.py (launcher)    │
            │  → webmanager/app.py      │
            │    FastAPI + Uvicorn      │
            └─────────────┬─────────────┘
                          │
   ┌──────────────┬───────┴───────┬──────────────────┐
   ▼              ▼               ▼                  ▼
┌──────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│ valheim_ │ │ JSON state   │ │ World saves  │ │ Playit agent │
│ server   │ │ config,users,│ │ ~/.config/   │ │ (tunnel      │
│ .x86_64  │ │ history,stats│ │ unity3d/...  │ │  detection)  │
└────┬─────┘ └──────────────┘ └──────────────┘ └──────────────┘
     ▼
┌──────────────────────────┐
│ valheim_server.log       │──► players, events, timeline, saga
│ logs/ (rotated)          │
└──────────────────────────┘
```

---

## Setup

The repository is placed in the Valheim Dedicated Server install folder, alongside `valheim_server.x86_64`.

1. Create the virtual environment and install dependencies:
   ```bash
   python3 -m venv webmanager/.venv
   webmanager/.venv/bin/pip install -r webmanager/requirements.txt
   ```
2. Copy the example config and edit it:
   ```bash
   cp server_config.example.json server_config.json
   ```
3. Run the manager (it re-launches itself inside the venv automatically):
   ```bash
   python3 manager.py
   ```
4. Open `http://localhost:8080` (or `http://<LAN-IP>:8080`). On first run a default account is created in `users.json` with username `admin` and password `admin`. **Change this password immediately** from the Accounts tab.

---

## Managing the Systemd Service (No `sudo` Required)

The manager runs as a user-level systemd service (`valheim.service`).

```bash
systemctl --user status valheim.service
systemctl --user restart valheim.service
systemctl --user stop valheim.service
systemctl --user start valheim.service
journalctl --user -u valheim.service -f
```

---

## Running Tests

```bash
python3 -m unittest discover tests
```

---

## File Structure

```
Valheim dedicated server/
├── manager.py                  # Launcher: venv re-exec, power policy, starts Uvicorn on :8080
├── server_config.example.json  # Example server configuration
├── server_config.json          # Persisted server parameters (git-ignored)
├── users.json                  # Hashed user accounts & roles (git-ignored)
├── *_history.json, world_stats.json  # Manager state data (git-ignored)
├── valheim_server.log          # Current game server log
├── logs/                       # Rotated game logs & server history log
├── backups/                    # Timestamped backup zip archives
├── webmanager/
│   ├── app.py                  # FastAPI app & routes
│   ├── requirements.txt
│   ├── core/                   # auth, backups, config, logs, paths, players, playit,
│   │                           # server, server_history, system, timeline, world_stats
│   ├── templates/              # Jinja2 pages & HTMX partials
│   └── static/                 # CSS, htmx, sse.js, timers.js
├── tests/                      # unittest suite
├── docker/                     # Dockerfile for running the server in a container
├── docker_start_server.sh      # Docker launch helper
├── start_server*.sh            # Legacy/stock launch scripts
└── .agents/AGENTS.md           # AI assistant context & developer specifications
```

---

## Operational Notes & Troubleshooting

1. **Password Rules**: Valheim requires server passwords of at least 5 characters (enforced by the config form), and the password must not appear in the server name (not checked by the form, so Valheim will refuse to start).
2. **Playit.gg & Loopback Routing**: Playit.gg routes external traffic to `127.0.0.1:2456`. **Do not enable `-crossplay`**, as it breaks loopback routing.
3. **Steam Server Browser**: Friends connecting via the Steam Server Browser use the **Query Port** (`Base Port + 1`, default `2457`).
4. **Exposure**: The dashboard listens on `0.0.0.0:8080` over plain HTTP. Don't forward port 8080 to the internet without a reverse proxy providing TLS.
