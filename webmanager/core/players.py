import datetime
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.paths import GAME_LOG, LOGS_DIR, PLAYER_HISTORY_FILE

# Regex patterns for Valheim dedicated server log lines
RE_LOG_TIMESTAMP = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):\s*(.*)$")
RE_GOT_ZDOID = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):\s*Got character ZDOID from\s+(.+?)\s*:\s*(.+)$")
RE_CONNECTIONS_COUNT = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):\s*Connections\s+(\d+)\s+ZDOS:")
RE_PEER_DISCONNECT = re.compile(
    r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):\s*(?:RPC_Disconnect|Closing socket|Destroying peer|k_ESteamNetworkingConnectionState_ClosedByPeer|k_ESteamNetworkingConnectionState_ProblemDetectedLocally)"
)
RE_SERVER_SHUTDOWN = re.compile(
    r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):\s*(?:Game - OnApplicationQuit|ZNet Shutdown|Shutting down|ZNet OnDestroy|Sending disconnect msg)"
)


def parse_timestamp(ts_str: str) -> Optional[datetime.datetime]:
    """Parse 'MM/DD/YYYY HH:MM:SS' string to datetime object."""
    try:
        return datetime.datetime.strptime(ts_str.strip(), "%m/%d/%Y %H:%M:%S")
    except Exception:
        return None


def format_duration(seconds: Optional[int]) -> str:
    """Format seconds into readable Viking duration (e.g. '12m 45s', '2h 10m')."""
    if seconds is None or seconds < 0:
        return "--"
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec:02d}s"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {mins:02d}m"
    days, hrs = divmod(hours, 24)
    return f"{days}d {hrs:02d}h"


class PlayerTracker:
    """Thread-safe persistent player tracker and log event parser."""

    def __init__(self, auto_load: bool = True):
        self.lock = threading.RLock()
        self.auto_load = auto_load
        self.known_players: Dict[str, Dict[str, Any]] = {}
        self.sessions: List[Dict[str, Any]] = []
        self.events: List[Dict[str, Any]] = []
        self._last_log_offset: int = 0
        self._last_log_inode: Optional[int] = None

        if self.auto_load:
            self._load_from_disk()
            self._backfill_history_if_needed()

    def _load_from_disk(self) -> None:
        """Load persistent history from player_history.json if it exists."""
        with self.lock:
            if not PLAYER_HISTORY_FILE.exists():
                return
            try:
                with open(PLAYER_HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.known_players = data.get("known_players", {})
                    self.sessions = data.get("sessions", [])
                    self.events = data.get("events", [])
                    self._last_log_offset = data.get("last_log_offset", 0)
                    self._last_log_inode = data.get("last_log_inode")
            except Exception as e:
                print(f"[PlayerTracker] Error loading {PLAYER_HISTORY_FILE}: {e}")

    def _save_to_disk(self) -> None:
        """Atomically persist player state and history to disk."""
        if not self.auto_load:
            return
        with self.lock:
            try:
                data = {
                    "known_players": self.known_players,
                    "sessions": self.sessions,
                    "events": self.events[-500:],  # Retain last 500 events
                    "last_log_offset": self._last_log_offset,
                    "last_log_inode": self._last_log_inode,
                }
                tmp_path = PLAYER_HISTORY_FILE.with_suffix(".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp_path, PLAYER_HISTORY_FILE)
            except Exception as e:
                print(f"[PlayerTracker] Error saving {PLAYER_HISTORY_FILE}: {e}")

    def _backfill_history_if_needed(self) -> None:
        """Perform initial backfill from historical and current logs."""
        with self.lock:
            # Only backfill if we have no prior recorded players/sessions
            if self.known_players or self.sessions:
                return

            if LOGS_DIR.exists():
                for log_file in sorted(LOGS_DIR.glob("valheim_server_*.log")):
                    self._parse_log_file(log_file)

            if GAME_LOG.exists():
                self._parse_log_file(GAME_LOG)
                stat = GAME_LOG.stat()
                self._last_log_offset = stat.st_size
                self._last_log_inode = getattr(stat, "st_ino", None)

            self._save_to_disk()

    def _parse_log_file(self, log_path: Path) -> None:
        """Parse all lines of a log file to reconstruct events."""
        if not log_path.exists():
            return
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                self._process_log_lines(lines)
        except Exception as e:
            print(f"[PlayerTracker] Error reading {log_path}: {e}")

    def process_new_logs(self) -> None:
        """Incrementally read newly appended lines from GAME_LOG."""
        if not self.auto_load:
            return
        with self.lock:
            if not GAME_LOG.exists():
                self._last_log_offset = 0
                return

            try:
                stat = GAME_LOG.stat()
                curr_size = stat.st_size
                curr_inode = getattr(stat, "st_ino", None)

                # Detect log rotation or truncation
                if curr_size < self._last_log_offset or (self._last_log_inode and curr_inode != self._last_log_inode):
                    self._last_log_offset = 0

                self._last_log_inode = curr_inode

                if curr_size <= self._last_log_offset:
                    return

                with open(GAME_LOG, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(self._last_log_offset)
                    new_lines = f.readlines()
                    self._last_log_offset = f.tell()

                if new_lines:
                    self._process_log_lines(new_lines)
                    self._save_to_disk()
            except Exception as e:
                print(f"[PlayerTracker] Error in incremental log reading: {e}")

    def _process_log_lines(self, lines: List[str]) -> None:
        """Process log lines and update state and event history."""
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            # Check character ZDOID event
            zdoid_match = RE_GOT_ZDOID.match(line)
            if zdoid_match:
                ts_str, player_name, zdoid = zdoid_match.groups()
                player_name = player_name.strip()
                zdoid = zdoid.strip()

                if not player_name:
                    continue

                if zdoid in ("0:0", "0"):
                    self._record_logout(player_name, ts_str)
                else:
                    self._record_login(player_name, ts_str)
                continue

            # Check peer disconnect event
            peer_dc_match = RE_PEER_DISCONNECT.match(line)
            if peer_dc_match:
                ts_str = peer_dc_match.group(1)
                online_players = [p for p, d in self.known_players.items() if d.get("is_online", False)]
                if len(online_players) == 1:
                    self._record_logout(online_players[0], ts_str)
                continue

            # Check server connection count zero
            conn_match = RE_CONNECTIONS_COUNT.match(line)
            if conn_match:
                ts_str, count_str = conn_match.groups()
                count = int(count_str)
                if count == 0:
                    self._record_all_offline(ts_str)
                continue

            # Check server shutdown line
            shutdown_match = RE_SERVER_SHUTDOWN.match(line)
            if shutdown_match:
                ts_str = shutdown_match.group(1)
                self._record_all_offline(ts_str)

    def _record_login(self, player_name: str, ts_str: str) -> None:
        """Record player login event and activate session."""
        now_dt = parse_timestamp(ts_str)
        if player_name not in self.known_players:
            self.known_players[player_name] = {
                "name": player_name,
                "first_seen": ts_str,
                "last_seen": ts_str,
                "is_online": True,
                "current_session_start": ts_str,
                "total_playtime_seconds": 0,
                "total_sessions": 1,
            }
        else:
            player = self.known_players[player_name]
            player["last_seen"] = ts_str
            if not player.get("is_online", False):
                player["is_online"] = True
                player["current_session_start"] = ts_str
                player["total_sessions"] = player.get("total_sessions", 0) + 1

        # Check if an open session already exists
        open_session = next(
            (s for s in reversed(self.sessions) if s["player_name"] == player_name and s.get("logout_time") is None),
            None,
        )

        if not open_session:
            new_sess_id = f"sess_{len(self.sessions) + 1}_{int(now_dt.timestamp() if now_dt else 0)}"
            new_session = {
                "session_id": new_sess_id,
                "player_name": player_name,
                "login_time": ts_str,
                "logout_time": None,
                "duration_seconds": None,
            }
            self.sessions.append(new_session)

            self.events.append(
                {
                    "id": f"evt_{len(self.events) + 1}",
                    "player_name": player_name,
                    "event": "login",
                    "timestamp": ts_str,
                    "session_id": new_sess_id,
                }
            )

    def _record_logout(self, player_name: str, ts_str: str) -> None:
        """Record player logout event and close open session."""
        logout_dt = parse_timestamp(ts_str)
        if player_name in self.known_players:
            player = self.known_players[player_name]
            if player.get("is_online", False):
                player["is_online"] = False
                player["last_seen"] = ts_str
                player["current_session_start"] = None

        # Close open session for player
        open_session = next(
            (s for s in reversed(self.sessions) if s["player_name"] == player_name and s.get("logout_time") is None),
            None,
        )

        if open_session:
            open_session["logout_time"] = ts_str
            login_dt = parse_timestamp(open_session["login_time"])
            if login_dt and logout_dt and logout_dt >= login_dt:
                dur = int((logout_dt - login_dt).total_seconds())
                open_session["duration_seconds"] = dur
                if player_name in self.known_players:
                    self.known_players[player_name]["total_playtime_seconds"] = (
                        self.known_players[player_name].get("total_playtime_seconds", 0) + dur
                    )
            else:
                open_session["duration_seconds"] = 0

            self.events.append(
                {
                    "id": f"evt_{len(self.events) + 1}",
                    "player_name": player_name,
                    "event": "logout",
                    "timestamp": ts_str,
                    "session_id": open_session["session_id"],
                    "duration_seconds": open_session.get("duration_seconds"),
                    "duration_str": format_duration(open_session.get("duration_seconds")),
                }
            )

    def _record_all_offline(self, ts_str: str) -> None:
        """Mark all currently online players as offline (e.g. on server stop/empty)."""
        for player_name, pdata in self.known_players.items():
            if pdata.get("is_online", False):
                self._record_logout(player_name, ts_str)

    def on_server_stop(self, ts_str: Optional[str] = None) -> None:
        """Callback when Valheim server process stops."""
        with self.lock:
            if not ts_str:
                ts_str = datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")
            self._record_all_offline(ts_str)
            self._save_to_disk()

    def get_summary(self, server_running: bool = True) -> Dict[str, Any]:
        """Generate player summary for UI and API endpoints."""
        with self.lock:
            # First process any pending log lines
            self.process_new_logs()

            now = datetime.datetime.now()
            online_names: List[str] = []
            all_players_list: List[Dict[str, Any]] = []

            for name, data in self.known_players.items():
                is_online = data.get("is_online", False) and server_running
                if is_online:
                    online_names.append(name)

                # Compute current session duration if online
                session_time_str = None
                start_ts_str = data.get("current_session_start")
                if is_online and start_ts_str:
                    s_dt = parse_timestamp(start_ts_str)
                    if s_dt:
                        diff = max(0, int((now - s_dt).total_seconds()))
                        session_time_str = format_duration(diff)

                all_players_list.append(
                    {
                        "name": name,
                        "is_online": is_online,
                        "first_seen": data.get("first_seen", "--"),
                        "last_seen": data.get("last_seen", "--"),
                        "session_time": session_time_str,
                        "total_playtime": format_duration(data.get("total_playtime_seconds", 0)),
                        "total_sessions": data.get("total_sessions", 0),
                    }
                )

            # Sort: online players first, then alphabetically by name
            all_players_list.sort(key=lambda x: (not x["is_online"], x["name"].lower()))

            # Prepare formatted recent events (admin timeline)
            recent_events = []
            for evt in reversed(self.events[-100:]):
                e_type = evt.get("event")
                dur_str = evt.get("duration_str")
                if not dur_str and evt.get("duration_seconds") is not None:
                    dur_str = format_duration(evt["duration_seconds"])

                recent_events.append(
                    {
                        "id": evt.get("id"),
                        "player_name": evt.get("player_name"),
                        "event": e_type,
                        "timestamp": evt.get("timestamp"),
                        "duration_str": dur_str or ("Active Now" if e_type == "login" else "--"),
                    }
                )

            return {
                "online_count": len(online_names),
                "online_players": online_names,
                "known_count": len(all_players_list),
                "all_players": all_players_list,
                "recent_events": recent_events,
            }


# Singleton PlayerTracker instance
PLAYER_TRACKER = PlayerTracker()
