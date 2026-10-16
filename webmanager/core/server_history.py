import datetime
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.paths import GAME_LOG, LOGS_DIR, SERVER_DIR, SERVER_HISTORY_FILE, SERVER_HISTORY_LOG

RE_LOG_TIMESTAMP = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):")
RE_WORLD_LOAD = re.compile(r"Load world:\s*([^\s(]+)")
RE_CLEAN_SHUTDOWN = re.compile(r"(?:Game - OnApplicationQuit|ZNet Shutdown|ZNet OnDestroy|OnApplicationQuit|Net scene destroyed)")
RE_SAVE_NUMBER = re.compile(r"World save \(1/5\).*?=>\s*Save number\s*(\d+)")
RE_SAVE_CHUNK_DONE = re.compile(r"World save \(5/5\) done\.\s*Total time\s*\[([^\]]+)\]")
RE_SAVE_CLASSIC_DONE = re.compile(r"World saved\s*\(\s*([^)]+)\s*\)")


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
    """Thread-safe persistent server run history, uptime, and world save tracker."""

    def __init__(self, auto_load: bool = True):
        self.lock = threading.RLock()
        self.auto_load = auto_load
        self.runs: List[Dict[str, Any]] = []
        self._last_log_offset = 0
        self._last_log_inode = None
        self._pending_save_number: Optional[int] = None

        if self.auto_load:
            self._load_from_disk()
            if not self.runs:
                self._backfill_from_logs()
            else:
                self._backfill_saves_if_needed()

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

    def _backfill_saves_if_needed(self) -> None:
        """Populate save history for existing runs if not previously tracked."""
        with self.lock:
            needs_backfill = any("save_count" not in r for r in self.runs)
            if not needs_backfill:
                return

            print("[ServerHistory] Backfilling world save events from logs...")
            log_files = sorted(LOGS_DIR.glob("valheim_server_*.log"))

            cache: Dict[str, Tuple[Optional[datetime.datetime], List[Dict[str, Any]]]] = {}
            for lf in log_files:
                first_dt = None
                try:
                    with open(lf, "r", encoding="utf-8", errors="ignore") as f:
                        lines = f.readlines()
                    for line in lines:
                        m = RE_LOG_TIMESTAMP.match(line)
                        if m:
                            first_dt = parse_log_timestamp(m.group(1))
                            break
                    cache[lf.name] = (first_dt, self._extract_saves_from_lines(lines))
                except Exception as e:
                    print(f"[ServerHistory] Error caching {lf}: {e}")

            current_saves: List[Dict[str, Any]] = []
            if GAME_LOG.exists() and GAME_LOG.stat().st_size > 0:
                try:
                    with open(GAME_LOG, "r", encoding="utf-8", errors="ignore") as f:
                        current_saves = self._extract_saves_from_lines(f.readlines())
                except Exception:
                    current_saves = []

            claimed_logs = set()
            for r in self.runs:
                if "save_count" in r:
                    continue

                r_id = r.get("id", "")
                r_ts_str = r.get("start_time")
                r_dt = None
                if r_ts_str:
                    try:
                        r_dt = datetime.datetime.strptime(r_ts_str, "%Y-%m-%d %H:%M:%S")
                    except Exception:
                        pass

                matched_saves: List[Dict[str, Any]] = []

                if r.get("status") == "Running" and current_saves:
                    matched_saves = current_saves
                else:
                    raw_ts = r_id.replace("run_", "")
                    target_fname = f"valheim_server_{raw_ts}.log"
                    if target_fname in cache and target_fname not in claimed_logs:
                        matched_saves = cache[target_fname][1]
                        claimed_logs.add(target_fname)
                    elif r_dt:
                        best_lf = None
                        min_diff = 999999
                        for lf_name, (l_dt, l_saves) in cache.items():
                            if lf_name in claimed_logs or not l_dt:
                                continue
                            diff = abs((r_dt - l_dt).total_seconds())
                            if diff < min_diff:
                                min_diff = diff
                                best_lf = lf_name
                        if min_diff <= 60 and best_lf:
                            matched_saves = cache[best_lf][1]
                            claimed_logs.add(best_lf)

                r["save_count"] = len(matched_saves)
                r["last_save_time"] = matched_saves[-1]["time"] if matched_saves else None
                r["last_save_duration"] = matched_saves[-1].get("duration") if matched_saves else None
                r["last_save_number"] = matched_saves[-1].get("save_number") if matched_saves else None
                r["saves"] = matched_saves

            self._save_to_disk()
            print(f"[ServerHistory] Backfill complete. Populated saves across {len(self.runs)} runs.")

    def _extract_saves_from_lines(self, lines: List[str]) -> List[Dict[str, Any]]:
        """Extract all world save events from a sequence of log lines."""
        saves: List[Dict[str, Any]] = []
        pending_save_number: Optional[int] = None

        for line in lines:
            nm = RE_SAVE_NUMBER.search(line)
            if nm:
                try:
                    pending_save_number = int(nm.group(1))
                except Exception:
                    pending_save_number = None

            cm = RE_SAVE_CHUNK_DONE.search(line)
            if cm:
                duration = cm.group(1).strip()
                num = pending_save_number
                pending_save_number = None
                dt = None
                m = RE_LOG_TIMESTAMP.match(line)
                if m:
                    dt = parse_log_timestamp(m.group(1))
                if dt:
                    saves.append({
                        "time": format_dt(dt),
                        "timestamp": dt.timestamp(),
                        "duration": duration,
                        "save_number": num,
                    })
                continue

            cl = RE_SAVE_CLASSIC_DONE.search(line)
            if cl:
                duration = cl.group(1).strip()
                dt = None
                m = RE_LOG_TIMESTAMP.match(line)
                if m:
                    dt = parse_log_timestamp(m.group(1))
                if dt:
                    saves.append({
                        "time": format_dt(dt),
                        "timestamp": dt.timestamp(),
                        "duration": duration,
                        "save_number": None,
                    })

        return saves

    def _parse_log_file(self, log_path: Path, is_current: bool = False) -> Optional[Dict[str, Any]]:
        """Parse a single log file to extract run details and world save occurrences."""
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

        saves = self._extract_saves_from_lines(lines)

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
                "save_count": len(saves),
                "last_save_time": saves[-1]["time"] if saves else None,
                "last_save_duration": saves[-1].get("duration") if saves else None,
                "last_save_number": saves[-1].get("save_number") if saves else None,
                "saves": saves,
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
                "save_count": len(saves),
                "last_save_time": saves[-1]["time"] if saves else None,
                "last_save_duration": saves[-1].get("duration") if saves else None,
                "last_save_number": saves[-1].get("save_number") if saves else None,
                "saves": saves,
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
                "save_count": 0,
                "last_save_time": None,
                "last_save_duration": None,
                "last_save_number": None,
                "saves": [],
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
                    r.setdefault("save_count", 0)
                    r.setdefault("last_save_time", None)
                    r.setdefault("last_save_duration", None)
                    r.setdefault("last_save_number", None)
                    r.setdefault("saves", [])
                    self._save_to_disk()
                    self.process_new_logs()
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
                "save_count": 0,
                "last_save_time": None,
                "last_save_duration": None,
                "last_save_number": None,
                "saves": [],
            }
            self.runs.append(new_run)
            self._save_to_disk()
            self.process_new_logs()

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

    def reconcile_with_server_state(
        self,
        server_running: bool,
        pid: Optional[int] = None,
    ) -> None:
        """Reconcile tracked runs with actual server execution state."""
        with self.lock:
            modified = False
            now_dt = datetime.datetime.now()
            now_ts = now_dt.timestamp()

            if not server_running:
                # If server is not running, no run should be marked 'Running'
                for r in self.runs:
                    if r.get("status") == "Running":
                        r["status"] = "Stopped"
                        # Use GAME_LOG mtime if reasonable for more accurate stop timestamp
                        stop_ts = now_ts
                        if GAME_LOG.exists():
                            try:
                                log_mtime = GAME_LOG.stat().st_mtime
                                if log_mtime >= r.get("start_timestamp", 0):
                                    stop_ts = log_mtime
                            except Exception:
                                pass
                        stop_dt = datetime.datetime.fromtimestamp(stop_ts)
                        r["stop_time"] = format_dt(stop_dt)
                        r["stop_timestamp"] = stop_ts
                        start_ts = r.get("start_timestamp", stop_ts)
                        dur = max(0, int(stop_ts - start_ts))
                        r["duration_seconds"] = dur
                        r["duration_str"] = format_duration(dur)
                        if not r.get("exit_reason") or "Active" in r.get("exit_reason", ""):
                            r["exit_reason"] = "Stopped / Terminated"
                        modified = True

                        log_msg = (
                            f"[{r['stop_time']}] [STOPPED] Server run '{r.get('id')}' stopped. "
                            f"Duration: {r['duration_str']} | Reason: {r['exit_reason']}"
                        )
                        print(f"[ServerHistory] {log_msg}")
                        self._append_to_plain_log(log_msg)
            else:
                if pid:
                    has_active = False
                    for r in reversed(self.runs):
                        if r.get("status") == "Running":
                            if not has_active:
                                r["pid"] = pid
                                has_active = True
                            else:
                                r["status"] = "Stopped"
                                r["stop_time"] = format_dt(now_dt)
                                r["stop_timestamp"] = now_ts
                                dur = max(0, int(now_ts - r.get("start_timestamp", now_ts)))
                                r["duration_seconds"] = dur
                                r["duration_str"] = format_duration(dur)
                                r["exit_reason"] = "Closed prior to new start"
                                modified = True

            if modified:
                self._save_to_disk()

    def process_new_logs(self) -> None:
        """Incrementally read newly appended lines from GAME_LOG and record live saves."""
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

                if not new_lines:
                    return

                active_run: Optional[Dict[str, Any]] = None
                for r in reversed(self.runs):
                    if r.get("status") == "Running":
                        active_run = r
                        break

                modified = False
                for line in new_lines:
                    nm = RE_SAVE_NUMBER.search(line)
                    if nm:
                        try:
                            self._pending_save_number = int(nm.group(1))
                        except Exception:
                            self._pending_save_number = None

                    dur = None
                    num = None
                    is_save = False

                    cm = RE_SAVE_CHUNK_DONE.search(line)
                    if cm:
                        dur = cm.group(1).strip()
                        num = self._pending_save_number
                        self._pending_save_number = None
                        is_save = True
                    else:
                        cl = RE_SAVE_CLASSIC_DONE.search(line)
                        if cl:
                            dur = cl.group(1).strip()
                            num = None
                            is_save = True

                    if is_save:
                        dt = None
                        m = RE_LOG_TIMESTAMP.match(line)
                        if m:
                            dt = parse_log_timestamp(m.group(1))
                        if not dt:
                            dt = datetime.datetime.now()

                        time_str = format_dt(dt)
                        save_entry = {
                            "time": time_str,
                            "timestamp": dt.timestamp(),
                            "duration": dur,
                            "save_number": num,
                        }

                        if active_run:
                            if "saves" not in active_run or not isinstance(active_run["saves"], list):
                                active_run["saves"] = []
                            # Avoid duplicates
                            if not any(s.get("time") == time_str for s in active_run["saves"]):
                                active_run["saves"].append(save_entry)
                                active_run["save_count"] = len(active_run["saves"])
                                active_run["last_save_time"] = time_str
                                active_run["last_save_duration"] = dur
                                active_run["last_save_number"] = num
                                modified = True

                        world_name = active_run.get("world_name", "Midgard") if active_run else "Midgard"
                        save_details = f"World: '{world_name}'"
                        if num:
                            save_details += f" | Save #{num}"
                        if dur:
                            save_details += f" | Duration: {dur}"

                        log_msg = f"[{time_str}] [SAVE] World saved ({save_details})"
                        print(f"[ServerHistory] {log_msg}")
                        self._append_to_plain_log(log_msg)

                if modified:
                    self._save_to_disk()

            except Exception as e:
                print(f"[ServerHistory] Error in incremental log reading: {e}")

    def get_summary(self, server_running: bool = False, pid: Optional[int] = None) -> Dict[str, Any]:
        """Compute aggregated server uptime metrics, world saves, and formatted run list."""
        with self.lock:
            if server_running:
                self.process_new_logs()
            self.reconcile_with_server_state(server_running, pid=pid)
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

            total_saves = 0
            latest_save_time = None
            for r in formatted_runs:
                cnt = r.get("save_count", 0)
                total_saves += cnt
                if r.get("last_save_time"):
                    if latest_save_time is None or r["last_save_time"] > latest_save_time:
                        latest_save_time = r["last_save_time"]

            return {
                "total_runs": total_runs,
                "total_uptime_seconds": total_seconds,
                "total_uptime_str": format_duration(total_seconds),
                "longest_run_str": format_duration(longest_seconds),
                "total_saves": total_saves,
                "last_save_time": latest_save_time,
                "current_run": current_run,
                "recent_runs": recent_runs,
            }


# Global singleton instance
SERVER_HISTORY = ServerHistoryTracker()
