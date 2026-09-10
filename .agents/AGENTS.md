# Project Context: Valheim Dedicated Server & Web Manager

## Project Summary
This project provides an automated, locally managed host setup for a Valheim Dedicated Server (Steam AppID `892970`) running on Fedora Linux (Framework 16 laptop). It features systemd sleep inhibition, Playit.gg UDP tunneling, and a modern asynchronous **FastAPI + HTMX** web management server (`app.py` / `manager.py`).

---

## Core Components & File Structure

```
/home/drewfus/.local/share/Steam/steamapps/common/Valheim dedicated server/
├── manager.py               # Bootstrap Launcher & Systemd Compatibility Wrapper
├── server_config.json       # Persisted server parameters
├── users.json               # PBKDF2 hashed user credentials and roles
├── valheim_server.log       # Valheim stdout/stderr log output file
├── valheim_server.x86_64    # Valheim dedicated server binary
├── backups/                 # Timestamped world backup zip archives
├── logs/                    # Rotated historical game server log files
├── webmanager/              # Web Server Application & UI Package
│   ├── app.py               # FastAPI Application Core & Endpoint Router (Port 8080)
│   ├── requirements.txt     # Python dependencies (fastapi, uvicorn, jinja2, etc.)
│   ├── .venv/               # Dedicated Python virtual environment
│   ├── core/                # Backend architecture modules
│   │   ├── paths.py         # Centralized root and web directory path resolver
│   │   ├── config.py        # Server config load/save & password validation
│   │   ├── auth.py          # User authentication, PBKDF2 hashing, sessions & RBAC
│   │   ├── playit.py        # Playit.gg live tunnel auto-detection
│   │   ├── system.py        # Power profile confirmation, sleep policy supervisor & safe reboot
│   │   ├── server.py        # Valheim process supervisor (SIGINT shutdown, PID attachment)
│   │   ├── logs.py          # Reverse-seeking log tailer & SSE stream generator
│   │   └── backups.py       # Backup lifecycle (create, list, download, restore, delete)
│   ├── templates/           # Jinja2 HTML Templates & HTMX Partials
│   │   ├── base.html        # Main HTML layout, nav tabs, script includes & toasts
│   │   ├── index.html       # Assembled dashboard tabs
│   │   ├── login.html       # Viking dark authentication screen
│   │   └── partials/        # Reactive HTMX components
│   │       ├── status_card.html
│   │       ├── header_status.html
│   │       ├── player_list.html
│   │       ├── connection_card.html
│   │       ├── terminal.html
│   │       ├── backup_table.html
│   │       ├── config_form.html
│   │       └── accounts_tab.html
│   └── static/              # Local static assets
│       ├── css/style.css    # Viking dark dashboard theme & animations
│       └── js/              # Vendored offline libraries (htmx.min.js, sse.js)
└── .agents/
    └── AGENTS.md            # AI Agent Project Specification (This File)
```

---

## Technical Specifications & Architectural Constraints

### 1. Web Manager (`app.py` & `manager.py`)
- **Runtime**: Python 3.14+ in local `.venv` using `FastAPI`, `Uvicorn`, `Jinja2`, `aiofiles`, and `HTMX 2.0`.
- **Server Port**: `8080` (HTTP).
- **Process Supervisor (`ValheimServerManager`)**:
  - Lifecycle states: `Stopped`, `Starting`, `Running`, `Stopping`.
  - Re-entrant thread safety (`threading.RLock()`).
  - **Graceful Shutdown**: Sends `signal.SIGINT` to allow Valheim up to 30 seconds to perform world save (`ZNet` flush) before process termination.
  - **Attached PID Management**: Correctly targets both spawned subprocesses and attached pre-existing PIDs (`self.existing_pid`) found via `pgrep -f valheim_server.x86_64`.
  - **Safe Log Rotation**: Archives existing `valheim_server.log` to `logs/valheim_server_<timestamp>.log` on startup before launching a new process.
  - **Non-blocking Log Tailing**: Reverse-seeks from EOF to retrieve only the last `N` lines rather than full-file reads.
  - **Real-Time Streaming**: Exposes Server-Sent Events (SSE) at `/api/stream/logs` streaming live terminal logs directly into HTMX without polling.
  - **Crash-Loop Throttling**: Tracks crash timestamps in `self.crash_history`. If 3 crashes occur within a 60-second window, `auto_restart` is paused to avoid rapid crash loops.
  - **Memory Metrics**: Queries process RSS via `ps -p <PID> -o %cpu,rss` and formats RAM as `MB` (if <1024MB) or `GB` (if >=1024MB).

### 2. Valheim Launch, Password & World Modifier Constraints
- **Launch Command**:
  ```bash
  LD_LIBRARY_PATH="./linux64:$LD_LIBRARY_PATH" SteamAppId="892970" ./valheim_server.x86_64 -name "<server_name>" -port <port> -world "<world_name>" -password "<password>" [-resetmodifiers] [-preset <preset>] [-modifier <category> <value>]... [-setkey <key>]...
  ```
- **CRITICAL PASSWORD RULE**: Valheim dedicated server requires passwords to be **at least 5 characters long**. Passwords under 5 characters cause immediate engine abort (`ZNet OnDestroy`). Both `start_server()` and `update_config()` enforce `len(password) >= 5`.
- **CRITICAL NETWORK RULE**: **Do NOT include `-crossplay`**. Playit.gg routes UDP traffic to `127.0.0.1:2456`. Enabling `-crossplay` breaks local loopback routing.
- **WORLD MODIFIERS SPECIFICATION**:
  - `-preset <value>`: `normal`, `casual`, `easy`, `hard`, `hardcore`, `immersive`, `hammer`. Preset must precede specific modifiers so it does not overwrite them.
  - `-modifier <category> <value>`:
    - `combat`: `veryeasy`, `easy`, `hard`, `veryhard`
    - `deathpenalty`: `casual`, `veryeasy`, `easy`, `hard`, `hardcore`
    - `resources`: `muchless`, `less`, `more`, `muchmore`, `most`
    - `raids`: `none`, `muchless`, `less`, `more`, `muchmore`
    - `portals`: `casual`, `hard`, `veryhard`
  - `-setkey <key>`: `nobuildcost`, `playerevents`, `passivemobs`, `nomap`
  - `-resetmodifiers`: wipes active world modifiers back to game defaults on start.
  - Valheim persists modifiers into the active `.db`/`.fwl` world save once initialized. `webmanager/core/server.py` builds the CLI vector via `build_valheim_command()`.

### 3. Systemd Integration (`valheim.service`) & Power Management
- Service location: `~/.config/systemd/user/valheim.service`
- Command: `/usr/bin/python3 manager.py`
- **Native Power & Display Policy**:
  - Automatically sets and confirms system power profile to `performance` (via D-Bus `net.hadess.PowerProfiles` / `tuned-adm`).
  - Configures power settings (KDE PowerDevil / GNOME) to disable auto-suspend on AC and configure lid-close action to turn off display (`LidAction=64`).
  - No `idle` inhibitor is held, allowing the host display to naturally blank/dim, turn off, and activate session auto-locking/logout on idle timeout while keeping the Valheim server running continuously in the background.
- `manager.py` auto-detects `.venv` and seamlessly starts the FastAPI application under Uvicorn.
- Runs completely in user space without requiring `sudo` privileges.

### 4. REST & HTMX Endpoint Map
- `GET /`: Serves the dashboard layout.
- `GET /partials/status`: Returns status card with live telemetry and start/stop/restart controls.
- `GET /partials/header-status`: Returns compact header status badge.
- `GET /partials/players`: Returns active Vikings list.
- `GET /partials/backups`: Returns dynamic backups table.
- `GET /partials/config`: Returns configuration form.
- `GET /api/world/random-seed`: Generates a randomized 10-character seed string.
- `POST /api/world/create`: Creates a new world with custom/random seed, pre-creation world backup, and auto-launch.
- `POST /api/server/start`, `POST /api/server/stop`, `POST /api/server/restart`: Server lifecycle actions.
- `POST /api/config`: Updates server configuration with instant validation.
- `POST /api/backups/create`: Creates a timestamped `.zip` backup of worlds.
- `POST /api/backups/restore/{filename}`: Safely restores world backup with automatic pre-restore safety snapshot.
- `DELETE /api/backups/delete/{filename}`: Deletes a backup archive.
- `GET /api/backups/download/{filename}`: Direct download of backup zip archive.
- `GET /api/stream/logs`: Server-Sent Events (SSE) live stream of `valheim_server.log`.
- `GET /api/status`, `GET /api/config`, `GET /api/backups`, `GET /api/logs`: JSON REST endpoints for backwards compatibility.

---

## Verification & Debugging Quick Reference

- **Check Service**: `systemctl --user status valheim.service --no-pager`
- **Check Service Logs**: `journalctl --user -u valheim.service -f`
- **Check HTTP API**: `curl -s http://127.0.0.1:8080/api/status`
- **Check Logs Stream**: `curl -N http://127.0.0.1:8080/api/stream/logs`
- **Tail Log**: `tail -n 50 valheim_server.log`
- **Check Processes**: `ps aux | grep -E "valheim|uvicorn"`
