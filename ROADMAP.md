# Valheim Server Manager - Technical Audit & Roadmap

**Date**: 2026-08-19  
**Host Environment**: Fedora Linux (Framework 16 Laptop)  
**Game Server**: Valheim Dedicated Server (Steam AppID `892970`)  
**Daemon**: `manager.py` (Port 8080) (`valheim.service`)  
**Network**: Playit.gg UDP Tunnel (`playit.service` -> `127.0.0.1:2456`)

---

## 1. System Health & Architecture Status

| Component | Status | Details |
| :--- | :--- | :--- |
| **Valheim Dedicated Server** | 🟢 Running | World: `ValheimTest`, Port: `2456`, CPU: ~7%, RAM: ~1.1 GB |
| **Web Manager Daemon** | 🟢 Running | `manager.py` on Port `8080` (HTTP) |
| **Systemd Service** | 🟢 Active | `valheim.service` with performance profile & no-sleep policy |
| **Playit.gg Tunnel** | 🟢 Active | Direct UDP routing to `127.0.0.1:2456` (`-crossplay` disabled) |
| **Save Location** | 🟢 Verified | `~/.config/unity3d/IronGate/Valheim/worlds_local/` |

---

## 2. Technical Audit & Identified Issues

### 2.1 Critical Bug: `stop_server()` Fails on Reconnected / Pre-existing PIDs
- **Location**: `manager.py` (`stop_server()`)
- **Problem**: When `manager.py` starts while Valheim is already running (e.g., after service restart), `find_existing_pid()` sets `self.existing_pid`, leaving `self.process = None`. If the user clicks **Stop** or **Restart**, `stop_server()` checks `if not self.process...` and immediately aborts, returning `"Server process is not running"` and setting `self.state = "Stopped"` while the server process remains active in the background.
- **Remediation**: Unify process termination logic to target either `self.process` or `self.existing_pid` with graceful `signal.SIGINT` and a 30-second timeout before escalation.

### 2.2 High I/O & Memory Inefficiency from Whole-Log Parsing
- **Location**: `manager.py` (`parse_players()` and `/api/logs`)
- **Problem**: Every 2 seconds (`/api/status`) and 3 seconds (`/api/logs`), the daemon executes `f.readlines()` on `valheim_server.log`. As uptime increases and logs grow to hundreds of megabytes, this causes repetitive full-file disk reads, memory spikes, and CPU overhead.
- **Remediation**: Implement a tail-seeking reader (seeking from EOF) to retrieve only the last `N` lines for the console and maintain an incremental log pointer for player session events.

### 2.3 Destructive Log Truncation on Restart
- **Location**: `manager.py` (`start_server()`)
- **Problem**: `start_server()` opens `valheim_server.log` with mode `'w'`, silently wiping previous server logs and crash history whenever the server restarts.
- **Remediation**: Implement automated log rotation (e.g., archiving `valheim_server.log` to `logs/valheim_server_<timestamp>.log` or `valheim_server.log.old`) before spawning a new process.

### 2.4 Incomplete World Backup Operations
- **Location**: `manager.py` (`create_backup()` / `/api/backups`)
- **Problem**: Backups can be created and listed, but the UI lacks endpoints to:
  - Download backup zip archives to client machines.
  - One-click restore a world from a chosen backup archive.
  - Delete old or unwanted backups.
  - Schedule automated recurring backups (e.g. every 6 hours with max retention limit).
- **Remediation**: Add REST endpoints and UI actions for `/api/backups/download`, `/api/backups/restore`, and `/api/backups/delete`, plus a background backup scheduler.

### 2.5 Open Management Port (No Basic Auth)
- **Location**: `manager.py` (`socketserver.TCPServer(("", 8080), ...)`)
- **Problem**: Port 8080 binds to `0.0.0.0` without authentication or CSRF tokens. Anyone on the local network (or external if port 8080 is routed) can change server passwords, stop the game, or trigger actions.
- **Remediation**: Add optional PIN / dashboard password authentication or session tokens.

---

## 3. Implementation Roadmap

### Phase 1: Stability & Core Fixes (Immediate Priority)
- [ ] **Fix Attached Process Lifecycle**: Ensure `stop_server()` and `restart_server()` correctly send `SIGINT` to pre-existing PIDs (`self.existing_pid`).
- [ ] **Efficient Log Tailing**: Replace `f.readlines()` with a chunked reverse-seek algorithm for `/api/logs` and status polling.
- [ ] **Log Rotation**: Rotate and timestamp historical logs on startup/restart rather than truncating.

### Phase 2: World & Server Administration
- [ ] **World Save Selector**: Auto-detect existing worlds in `~/.config/unity3d/IronGate/Valheim/worlds_local` and populate a dropdown in the UI.
- [ ] **Complete Backup Lifecycle**:
  - Direct `.zip` download endpoint from UI.
  - Safe one-click restore (auto-saves current state before restore).
  - Backup deletion and configurable retention limits.
  - Automated background backup timer (e.g. every 6 hours).
- [ ] **Access Lists UI**: Manage `adminlist.txt`, `permittedlist.txt` (whitelist), and `bannedlist.txt` from a dedicated UI tab.
- [ ] **Connection Helper**: Display Local IP, Steam Query Port (`2457`), and Playit tunnel address with one-click "Copy Join Info".

### Phase 3: Dashboard UI/UX & Security
- [ ] **Live Telemetry Charts**: Real-time CPU & RAM historical line charts.
- [ ] **Log Filtering & Search**: Filter logs by categories (Errors, Connections, World Saves).
- [ ] **Manager Authentication**: Add configurable dashboard password / PIN protection.
