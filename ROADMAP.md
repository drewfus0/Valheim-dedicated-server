# Valheim Server Manager - Roadmap

**Last Updated**: 2026-10-29  
**Host Environment**: Fedora Linux (Framework 16 Laptop)  
**Game Server**: Valheim Dedicated Server (Steam AppID `892970`)  
**Manager**: `manager.py` → `webmanager/app.py` (FastAPI + HTMX, Port 8080) (`valheim.service`)  
**Network**: Playit.gg UDP Tunnel (`playit.service` -> `127.0.0.1:2456`)

---

## 1. Resolved Issues (from the 2026-08-19 audit)

| Issue | Resolution |
| :--- | :--- |
| `stop_server()` failed on pre-existing PIDs | Stop/Restart now target either the spawned process or the attached `existing_pid`, with graceful `SIGINT` and 30-second timeout. |
| Whole-log parsing every poll | Replaced with a reverse-seeking tail reader and an SSE log stream. |
| Log truncated on restart | `valheim_server.log` is rotated into `logs/` with a timestamp before each launch. |
| Incomplete backup lifecycle | Download, delete, inspect, and selective restore (with clean shutdown and safety snapshot) are implemented. |
| Open management port, no auth | Login with PBKDF2-hashed passwords, sessions, and `admin` / `operator` / `viewer` roles. |

---

## 2. Implementation Roadmap

### Phase 1: Stability & Core Fixes ✅
- [x] **Fix Attached Process Lifecycle**: Stop/Restart send `SIGINT` to pre-existing PIDs.
- [x] **Efficient Log Tailing**: Reverse-seek reader for the console and status polling.
- [x] **Log Rotation**: Timestamped archives in `logs/` on startup/restart.

### Phase 2: World & Server Administration
- [x] **World Save Selector**: Detects existing worlds and populates a dropdown.
- [x] **World Creation**: New worlds with custom or random seeds.
- [x] **Backup Download / Delete / Inspect**.
- [x] **Selective Restore**: World, configs, manager data, logs; clean shutdown, safety snapshot, optional prune of active world.
- [x] **Backup Scope**: Active world folder only, plus server configs, manager JSON data, and logs.
- [x] **Connection Helper**: Query port, Playit address, password and seed with one-click "Copy Join Info".
- [ ] **Scheduled Backups**: Background backup timer (e.g. every 6 hours).
- [ ] **Backup Retention**: Configurable maximum number/age of backups with automatic pruning.
- [ ] **Access Lists UI**: Manage `adminlist.txt`, `permittedlist.txt` and `bannedlist.txt` from the dashboard (currently only included in backups/restores).

### Phase 3: Dashboard UI/UX & Security
- [x] **Manager Authentication**: Login, sessions, roles, account management, login history.
- [x] **World Saga & Timeline**: Boss progression, in-game clock, sunrise/sunset, per-player activity timeline.
- [x] **Spawn & Growth Timers HUD**.
- [x] **Raid Alerts** and **Server History**.
- [ ] **Live Telemetry Charts**: Historical CPU & RAM line charts.
- [ ] **Log Filtering & Search**: Filter logs by category (errors, connections, world saves) and search text.
- [ ] **Default Credential Hardening**: Force a password change on first login of the auto-created `admin` / `admin` account.
- [ ] **Server Name Validation**: Reject passwords that appear in the server name (Valheim refuses to start).
- [ ] **HTTPS / CSRF Protection**: TLS (or documented reverse proxy) and CSRF tokens for state-changing requests.

### Housekeeping
- [ ] **Retire Legacy Scripts**: `start_server_custom.sh` (old `http.server` dashboard) and the stock `start_server.sh` launch the server with hard-coded settings and `-crossplay`; remove them or mark them as unsupported.
- [ ] **Docker Path**: Decide whether the `docker/` setup is still supported and, if so, document it alongside the web manager.
