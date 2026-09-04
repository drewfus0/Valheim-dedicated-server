import datetime
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.paths import GAME_LOG, LOGS_DIR, SERVER_DIR, SERVER_HISTORY_FILE, SERVER_HISTORY_LOG

RE_LOG_TIMESTAMP = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):")
RE_WORLD_LOAD = re.compile(r"Load world:\s*([^\s(]+)")
RE_CLEAN_SHUTDOWN = re.compile(r"(?:Game - OnApplicationQuit|ZNet Shutdown|ZNet OnDestroy|OnApplicationQuit|Net scene destroyed)")


def parse_log_timestamp(ts_str: str) -> Optional[datetime.datetime]:
    """Parse 'MM/DD/YYYY HH:MM:SS' string to datetime object."""
    try:
        return datetime.datetime.strptime(ts_str.strip(), "%m/%d/%Y %H:%M:%S")
    except Exception:
        return None


def format_dt(dt: datetime.datetime) -> str:
    """Format datetime object into standard 'YYYY-MM-DD HH:MM:SS' string."""
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def format_duration(seconds: Optional[float]) -> str:
    """Format seconds into readable Viking duration (e.g. '45s', '12m 30s', '2h 10m', '3d 4h 15m')."""
    if seconds is None or seconds < 0:
        return "--"
    sec = int(seconds)
    if sec < 60:
        return f"{sec}s"
    minutes, s = divmod(sec, 60)
    if minutes < 60:
        return f"{minutes}m {s:02d}s"
    hours, m = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {m:02d}m"
    days, h = divmod(hours, 24)
    return f"{days}d {h:02d}h {m:02d}m"


class ServerHistoryTracker:
    """Thread-safe persistent server run history and uptime tracker."""

    def __init__(self, auto_load: bool = True):
        self.lock = threading.RLock()
        self.auto_load = auto_load
        self.runs: List[Dict[str, Any]] = []

        if self.auto_load:
            self._load_from_disk()
            if not self.runs:
                self._backfill_from_logs()

    def _load_from_disk(self) -> None:
        """Load persistent history from server_history.json if present."""
        with self.lock:
            if not SERVER_HISTORY_FILE.exists():
                return
            try:
                with open(SERVER_HISTORY_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.runs = data.get("runs", [])
            except Exception as e:
                print(f"[ServerHistory] Error loading {SERVER_HISTORY_FILE}: {e}")

    def _save_to_disk(self) -> None:
        """Atomically persist server history to server_history.json."""
        if not self.auto_load:
            return
        with self.lock:
            try:
                data = {
                    "runs": self.runs,
                    "updated_at": datetime.datetime.now().isoformat(),
                }
                tmp_path = SERVER_HISTORY_FILE.with_suffix(".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp_path, SERVER_HISTORY_FILE)
            except Exception as e:
                print(f"[ServerHistory] Error saving {SERVER_HISTORY_FILE}: {e}")

    def _append_to_plain_log(self, log_line: str) -> None:
        """Append a timestamped line to logs/server_history.log."""
        try:
            LOGS_DIR.mkdir(parents=True, exist_ok=True)
            with open(SERVER_HISTORY_LOG, "a", encoding="utf-8") as f:
                f.write(log_line.rstrip() + "\n")
        except Exception as e:
            print(f"[ServerHistory] Error appending to {SERVER_HISTORY_LOG}: {e}")

    def _backfill_from_logs(self) -> None:
        """Parse historical logs in logs/ and current valheim_server.log to reconstruct past server runs."""
        with self.lock:
            found_runs: List[Dict[str, Any]] = []

            # Check all rotated historical logs
            log_files = sorted(LOGS_DIR.glob("valheim_server_*.log"))
            for log_file in log_files:
                run_entry = self._parse_log_file(log_file, is_current=False)
                if run_entry:
                    found_runs.append(run_entry)

            # Check current valheim_server.log if it exists
            if GAME_LOG.exists() and GAME_LOG.stat().st_size > 0:
                current_entry = self._parse_log_file(GAME_LOG, is_current=True)
                if current_entry:
                    found_runs.append(current_entry)

            self.runs = found_runs
            self._save_to_disk()

            # Initialize plain-text server history log if it doesn't exist
            if not SERVER_HISTORY_LOG.exists() and self.runs:
                try:
                    LOGS_DIR.mkdir(parents=True, exist_ok=True)
                    with open(SERVER_HISTORY_LOG, "w", encoding="utf-8") as f:
                        f.write("# Valheim Dedicated Server Run History Log\n")
                        f.write(f"# Created: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                        f.write("# " + "=" * 70 + "\n\n")
                        for r in self.runs:
                            status_label = "RUNNING" if r.get("status") == "Running" else "STOPPED"
                            f.write(
                                f"[{r.get('start_time')}] [{status_label}] Server run '{r.get('id')}' | "
                                f"World: {r.get('world_name', 'Unknown')} | Port: {r.get('port', 2456)} | "
                                f"Duration: {r.get('duration_str', '--')} | Status: {r.get('exit_reason', '--')}\n"
                            )
                except Exception as e:
                    print(f"[ServerHistory] Error initializing {SERVER_HISTORY_LOG}: {e}")

    def _parse_log_file(self, log_path: Path, is_current: bool = False) -> Optional[Dict[str, Any]]:
        """Parse a single log file to extract run details."""
        start_dt: Optional[datetime.datetime] = None
        stop_dt: Optional[datetime.datetime] = None
        world_name = "ValheimTest"
        is_clean_shutdown = False

        try:
            with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
        except Exception:
            return None

        if not lines:
            return None

        for line in lines:
            m = RE_LOG_TIMESTAMP.match(line)
            if m:
                dt = parse_log_timestamp(m.group(1))
                if dt:
                    if start_dt is None:
                        start_dt = dt
                    stop_dt = dt

            wm = RE_WORLD_LOAD.search(line)
            if wm:
                world_name = wm.group(1)

            if RE_CLEAN_SHUTDOWN.search(line):
                is_clean_shutdown = True

        if not start_dt:
            # Fallback to file mtime if no timestamp lines found
            mtime = datetime.datetime.fromtimestamp(log_path.stat().st_mtime)
            start_dt = mtime
            stop_dt = mtime

        if stop_dt is None:
            stop_dt = start_dt

        # For current log, check if valheim_server.x86_64 is actually currently running
        is_running_now = False
        pid: Optional[int] = None
        if is_current:
            try:
                import subprocess
                out = subprocess.check_output(["pgrep", "-f", "valheim_server.x86_64"], text=True)
                pids = [int(p.strip()) for p in out.strip().split() if p.strip().isdigit()]
                if pids:
                    is_running_now = True
                    pid = pids[0]
            except Exception:
                is_running_now = False

        run_id = f"run_{start_dt.strftime('%Y%m%d_%H%M%S')}"

        if is_running_now:
            now = time.time()
            start_ts = start_dt.timestamp()
            duration_sec = max(0, int(now - start_ts))
            return {
                "id": run_id,
                "start_time": format_dt(start_dt),
                "stop_time": None,
                "start_timestamp": start_ts,
                "stop_timestamp": None,
                "duration_seconds": duration_sec,
                "duration_str": "Active Now",
                "status": "Running",
                "exit_reason": "Active (Running)",
                "pid": pid,
                "server_name": "ValheimTest",
                "world_name": world_name,
                "port": 2456,
                "source": "live",
            }
        else:
            duration_sec = max(0, int((stop_dt - start_dt).total_seconds()))
            exit_reason = "Clean Shutdown" if is_clean_shutdown else "Terminated / Stopped"
            return {
                "id": run_id,
                "start_time": format_dt(start_dt),
                "stop_time": format_dt(stop_dt),
                "start_timestamp": start_dt.timestamp(),
                "stop_timestamp": stop_dt.timestamp(),
                "duration_seconds": duration_sec,
                "duration_str": format_duration(duration_sec),
                "status": "Stopped",
                "exit_reason": exit_reason,
                "pid": None,
                "server_name": "ValheimTest",
                "world_name": world_name,
                "port": 2456,
                "source": "backfilled" if not is_current else "historical",
            }

    def on_server_start(
        self,
        pid: int,
        server_name: str,
        world_name: str,
        port: Any,
        trigger: str = "Manual",
    ) -> None:
        """Record the start of a server run session."""
        with self.lock:
            now_dt = datetime.datetime.now()
            now_ts = now_dt.timestamp()

            # Close any previous unclosed 'Running' session
            for r in self.runs:
                if r.get("status") == "Running":
                    r["status"] = "Stopped"
                    r["stop_time"] = format_dt(now_dt)
                    r["stop_timestamp"] = now_ts
                    start_ts = r.get("start_timestamp", now_ts)
                    dur = max(0, int(now_ts - start_ts))
                    r["duration_seconds"] = dur
                    r["duration_str"] = format_duration(dur)
                    r["exit_reason"] = "Closed prior to new start"

            run_id = f"run_{now_dt.strftime('%Y%m%d_%H%M%S')}"
            new_run = {
                "id": run_id,
                "start_time": format_dt(now_dt),
                "stop_time": None,
                "start_timestamp": now_ts,
                "stop_timestamp": None,
                "duration_seconds": 0,
                "duration_str": "Active Now",
                "status": "Running",
                "exit_reason": f"Active ({trigger})",
                "pid": pid,
                "server_name": server_name,
                "world_name": world_name,
                "port": port,
                "source": "live",
            }
            self.runs.append(new_run)
            self._save_to_disk()

            log_msg = (
                f"[{format_dt(now_dt)}] [STARTED] Valheim server started (PID: {pid}, "
                f"World: '{world_name}', Port: {port}, Name: '{server_name}', Trigger: {trigger})"
            )
            print(f"[ServerHistory] {log_msg}")
            self._append_to_plain_log(log_msg)

    def on_server_attached(
        self,
        pid: int,
        server_name: str,
        world_name: str,
        port: Any,
        start_time_ts: Optional[float] = None,
    ) -> None:
        """Ensure an active run session exists when supervisor attaches to an existing PID."""
        with self.lock:
            # Check if there is already a matching running run
            for r in self.runs:
                if r.get("status") == "Running" and (r.get("pid") == pid or r.get("pid") is None):
                    r["pid"] = pid
                    r["server_name"] = server_name
                    r["world_name"] = world_name
                    r["port"] = port
                    self._save_to_disk()
                    return

            now_dt = datetime.datetime.now()
            now_ts = now_dt.timestamp()
            effective_ts = start_time_ts if start_time_ts else now_ts
            effective_dt = datetime.datetime.fromtimestamp(effective_ts)

            run_id = f"run_{effective_dt.strftime('%Y%m%d_%H%M%S')}"
            new_run = {
                "id": run_id,
                "start_time": format_dt(effective_dt),
                "stop_time": None,
                "start_timestamp": effective_ts,
                "stop_timestamp": None,
                "duration_seconds": max(0, int(now_ts - effective_ts)),
                "duration_str": "Active Now",
                "status": "Running",
                "exit_reason": "Active (Attached PID)",
                "pid": pid,
                "server_name": server_name,
                "world_name": world_name,
                "port": port,
                "source": "live",
            }
            self.runs.append(new_run)
            self._save_to_disk()

            log_msg = (
                f"[{format_dt(now_dt)}] [ATTACHED] Attached to running Valheim server PID {pid} "
                f"(World: '{world_name}', Port: {port})"
            )
            print(f"[ServerHistory] {log_msg}")
            self._append_to_plain_log(log_msg)

    def on_server_stop(self, exit_reason: str = "Clean Shutdown") -> None:
        """Record server shutdown or exit."""
        with self.lock:
            now_dt = datetime.datetime.now()
            now_ts = now_dt.timestamp()

            found_run: Optional[Dict[str, Any]] = None
            for r in reversed(self.runs):
                if r.get("status") == "Running":
                    found_run = r
                    break

            if found_run:
                found_run["status"] = "Stopped"
                found_run["stop_time"] = format_dt(now_dt)
                found_run["stop_timestamp"] = now_ts
                start_ts = found_run.get("start_timestamp", now_ts)
                dur = max(0, int(now_ts - start_ts))
                found_run["duration_seconds"] = dur
                found_run["duration_str"] = format_duration(dur)
                found_run["exit_reason"] = exit_reason
                dur_str = found_run["duration_str"]
                pid_info = f"PID {found_run.get('pid')}" if found_run.get("pid") else "Server"
            else:
                dur_str = "--"
                pid_info = "Server"

            self._save_to_disk()

            log_msg = (
                f"[{format_dt(now_dt)}] [STOPPED] {pid_info} stopped. "
                f"Duration: {dur_str} | Reason: {exit_reason}"
            )
            print(f"[ServerHistory] {log_msg}")
            self._append_to_plain_log(log_msg)

    def get_summary(self, server_running: bool = False) -> Dict[str, Any]:
        """Compute aggregated server uptime metrics and formatted run list."""
        with self.lock:
            now_ts = time.time()
            total_runs = len(self.runs)
            total_seconds = 0
            longest_seconds = 0
            formatted_runs: List[Dict[str, Any]] = []

            for r in self.runs:
                entry = dict(r)
                is_running = entry.get("status") == "Running"

                if is_running:
                    if server_running:
                        start_ts = entry.get("start_timestamp", now_ts)
                        dur = max(0, int(now_ts - start_ts))
                        entry["duration_seconds"] = dur
                        entry["duration_str"] = "Active Now"
                        total_seconds += dur
                        if dur > longest_seconds:
                            longest_seconds = dur
                    else:
                        # Server is actually stopped; treat duration as recorded
                        dur = entry.get("duration_seconds", 0)
                        entry["duration_str"] = format_duration(dur)
                        total_seconds += dur
                        if dur > longest_seconds:
                            longest_seconds = dur
                else:
                    dur = entry.get("duration_seconds")
                    if dur is None:
                        start_ts = entry.get("start_timestamp")
                        stop_ts = entry.get("stop_timestamp")
                        if start_ts and stop_ts:
                            dur = max(0, int(stop_ts - start_ts))
                        else:
                            dur = 0
                        entry["duration_seconds"] = dur
                    entry["duration_str"] = format_duration(dur)
                    total_seconds += dur
                    if dur > longest_seconds:
                        longest_seconds = dur

                formatted_runs.append(entry)

            # Most recent runs first
            recent_runs = list(reversed(formatted_runs))

            current_run = None
            if server_running and formatted_runs:
                for r in reversed(formatted_runs):
                    if r.get("status") == "Running":
                        current_run = r
                        break

            return {
                "total_runs": total_runs,
                "total_uptime_seconds": total_seconds,
                "total_uptime_str": format_duration(total_seconds),
                "longest_run_str": format_duration(longest_seconds),
                "current_run": current_run,
                "recent_runs": recent_runs,
            }


# Global singleton instance
SERVER_HISTORY = ServerHistoryTracker()
