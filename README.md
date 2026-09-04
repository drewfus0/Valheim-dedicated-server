# Valheim Local Server & Web Manager

A lightweight, standalone Python web management daemon and modern single-page control dashboard for hosting a Valheim Dedicated Server on Fedora Linux. Designed for personal laptop hosting (e.g., Framework 16) with automated sleep inhibition, Playit.gg UDP tunneling, live resource telemetry, and world backup tools.

---

## Features

- **Modern Responsive Web Dashboard**:
  - **Live Telemetry Cards**: Real-time CPU load (%), exact RAM usage (`MB`/`GB`), server uptime, and active player count.
  - **Online Character Detection**: Automatically parses `valheim_server.log` to track connected character names while filtering out `0:0` death/respawn events.
  - **Live Log Terminal**: Streaming console viewer with auto-scroll, refresh, and line-count selectors.
  - **Server Configuration UI**: Easily update Server Name, World Name, Port, and Password with instant validation (minimum 5 characters required by Valheim).
  - **World Backup Manager**: One-click world save backups creating timestamped `.zip` archives of `~/.config/unity3d/IronGate/Valheim/worlds_local`.

- **Resilient Process Supervision**:
  - **Graceful Shutdown**: Sends `SIGINT` (Ctrl+C) to allow Valheim up to 30 seconds to flush world data and save state before exiting.
  - **Orphan Process Cleanup**: Automatically detects and terminates stale `valheim_server.x86_64` instances on launch to prevent UDP port conflicts (`port 2457`).
  - **Crash-Loop Throttling**: Monitors process health and automatically pauses `auto_restart` if 3 consecutive crashes occur within 60 seconds.

- **Automated Performance & Power Policy**:
  - Automatically sets and confirms the system power profile to `performance` (via D-Bus / TuneD).
  - Configures system power settings to prevent automatic sleep/suspend, while allowing the screen to turn off and session auto-locking/logout to occur naturally on idle.
  - Configures laptop lid-close action to shut off the display without putting the server to sleep.

---

## Technical Stack & Requirements

| Component | Specification |
| :--- | :--- |
| **OS / Hardware** | Fedora Linux (Framework 16 Laptop) |
| **Game Server** | Valheim Dedicated Server (Steam AppID `892970`) |
| **Tunneling** | Playit.gg (UDP tunneling to `127.0.0.1:2456`, `-crossplay` disabled) |
| **Daemon** | Python 3 (`http.server`, `threading`, `subprocess`, `zipfile`) |
| **Web UI Port** | `8080` (HTTP) |
| **Game Ports** | `2456` (Base Game Port UDP), `2457` (Query Port UDP) |

---

## Architecture Overview

```
               [ Browser / Mobile UI ]
                          │
                   (HTTP Port 8080)
                          ▼
            ┌───────────────────────────┐
            │   python3 manager.py     │
            │ (Thread-Safe Supervisor)  │
            └─────────────┬─────────────┘
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
┌──────────────────┐           ┌──────────────────────┐
│ valheim_server   │           │ server_config.json   │
│   .x86_64        │           │ (Persistent Settings)│
└─────────┬────────┘           └──────────────────────┘
          │
          ▼
┌──────────────────┐
│ valheim_server   │
│      .log        │
└──────────────────┘
```

---

## Quick Start & Service Commands

### Web UI Access
Open your web browser and navigate to:
```
http://localhost:8080
```
or connect via your local network IP (e.g. `http://192.168.x.x:8080`).

### Managing the Systemd Service (No `sudo` Required)

The manager is configured as a user-level systemd service (`valheim.service`) with automated performance profile configuration.

- **Check Service Status**:
  ```bash
  systemctl --user status valheim.service
  ```

- **Restart Web Manager Service**:
  ```bash
  systemctl --user restart valheim.service
  ```

- **Stop Web Manager Service**:
  ```bash
  systemctl --user stop valheim.service
  ```

- **Start Web Manager Service**:
  ```bash
  systemctl --user start valheim.service
  ```

- **Follow Live Service Logs**:
  ```bash
  journalctl --user -u valheim.service -f
  ```

---

## File Structure

```
Valheim dedicated server/
├── manager.py               # Main Python Web Manager & HTTP Server (Port 8080)
├── server_config.json       # Persisted server parameters (Name, World, Password, Port)
├── valheim_server.log       # Valheim dedicated server stdout/stderr log
├── valheim_server.x86_64    # Steam Valheim dedicated server executable
├── backups/                 # Generated timestamped world save zip archives
└── .agents/
    └── AGENTS.md            # AI assistant context & developer specifications
```

---

## Operational Notes & Troubleshooting

1. **Password Length**:
   - Valheim dedicated server **requires passwords to be at least 5 characters long**. Setting a password shorter than 5 characters will cause Valheim to abort startup.

2. **Playit.gg & Loopback Routing**:
   - Playit.gg routes external traffic directly to `127.0.0.1:2456`.
   - **Do not enable `-crossplay`** in server launch arguments as crossplay breaks loopback routing.

3. **Steam Server Browser**:
   - External friends connecting via Steam Server Browser must connect using the **Query Port** (`Base Port + 1`, default: `2457`).
