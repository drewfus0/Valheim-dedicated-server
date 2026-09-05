import asyncio
import datetime
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.backups import get_world_seed
from core.config import load_config, save_config
from core.logs import GAME_LOG
from core.paths import GAME_LOG, LINUX64_DIR, LOGS_DIR, SERVER_BIN, SERVER_DIR
from core.players import PLAYER_TRACKER
from core.playit import get_detected_playit_tunnel
from core.server_history import SERVER_HISTORY
from core.system import get_cpu_frequencies, get_system_power_status


def find_existing_pid() -> Optional[int]:
    """Find PID of running valheim_server.x86_64 instance if any."""
    try:
        out = subprocess.check_output(["pgrep", "-f", "valheim_server.x86_64"], text=True)
        pids = [int(p.strip()) for p in out.strip().split() if p.strip().isdigit()]
        if pids:
            return pids[0]
    except Exception:
        pass
    return None


class ValheimServerManager:
    def __init__(self):
        self.lock = threading.RLock()
        self.config = load_config()
        self.process: Optional[subprocess.Popen] = None
        self.existing_pid: Optional[int] = None
        self.state: str = "Stopped"  # Stopped, Starting, Running, Stopping
        self.start_time: Optional[float] = None
        self.crash_history: List[float] = []
        self._monitor_task_running = True

        # Check for pre-existing process on initialization
        pid = find_existing_pid()
        if pid:
            print(f"[Manager] Attached to pre-existing Valheim server PID {pid}.")
            self.existing_pid = pid
            self.state = "Running"
            self.start_time = time.time()
            SERVER_HISTORY.on_server_attached(
                pid=pid,
                server_name=str(self.config.get("server_name", "ValheimTest")),
                world_name=str(self.config.get("world_name", "ValheimTest")),
                port=self.config.get("port", 2456),
                start_time_ts=self.start_time,
            )
        else:
            SERVER_HISTORY.reconcile_with_server_state(server_running=False)

        # Start background monitor thread
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.monitor_thread.start()

    def get_active_pid(self) -> Optional[int]:
        with self.lock:
            if self.process and self.process.poll() is None:
                return self.process.pid
            return self.existing_pid

    def get_status_data(self) -> Dict[str, Any]:
        with self.lock:
            state = self.state
            start_t = self.start_time
            pid = self.process.pid if self.process and self.process.poll() is None else self.existing_pid
            cfg = self.config.copy()

        cpu, mem = "0.0", "0 MB"
        if pid and state == "Running":
            try:
                out = subprocess.check_output(["ps", "-p", str(pid), "-o", "%cpu,rss"], text=True)
                lines = out.strip().split("\n")
                if len(lines) > 1:
                    parts = lines[1].strip().split()
                    cpu = parts[0]
                    rss_kb = float(parts[1])
                    rss_mb = rss_kb / 1024.0
                    if rss_mb >= 1024:
                        mem = f"{rss_mb / 1024.0:.2f} GB"
                    else:
                        mem = f"{int(rss_mb)} MB"
            except Exception:
                pass

        uptime_str = "--"
        if start_t and state == "Running":
            elapsed = int(time.time() - start_t)
            hours, remainder = divmod(elapsed, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"{hours:02d}:{minutes:02d}:{seconds:02d}"

        p_summary = PLAYER_TRACKER.get_summary(server_running=(state == "Running"))
        s_summary = SERVER_HISTORY.get_summary(server_running=(state == "Running"), pid=pid)

        playit_addr = cfg.get("playit_address")
        is_auto = False
        if not playit_addr:
            detected = get_detected_playit_tunnel(str(cfg.get("port", "2456")))
            if detected:
                playit_addr = detected
                is_auto = True

        cpu_freq = get_cpu_frequencies()
        power_status = get_system_power_status()

        return {
            "status": state,
            "cpu": cpu,
            "mem": mem,
            "uptime": uptime_str,
            "pid": pid,
            "players_count": p_summary["online_count"],
            "players_list": p_summary["online_players"],
            "known_players_count": p_summary["known_count"],
            "all_players": p_summary["all_players"],
            "player_sessions": p_summary["recent_events"],
            "server_history": s_summary,
            "server_runs": s_summary["recent_runs"],
            "cpu_freq": cpu_freq,
            "power_status": power_status,
            "auto_restart": cfg.get("auto_restart", True),
            "config": {
                "server_name": cfg.get("server_name", "ValheimTest"),
                "world_name": cfg.get("world_name", "ValheimTest"),
                "world_seed": get_world_seed(str(cfg.get("world_name", "ValheimTest"))) or "",
                "port": cfg.get("port", "2456"),
                "password": cfg.get("password", "tester"),
                "playit_address": playit_addr or "",
                "playit_auto_detected": is_auto,
                "masked_password": "•" * len(str(cfg.get("password", ""))),
            },
        }

    def _rotate_logs(self):
        """Archive existing valheim_server.log to logs/ with timestamp."""
        if GAME_LOG.exists() and GAME_LOG.stat().st_size > 0:
            try:
                LOGS_DIR.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                archived_path = LOGS_DIR / f"valheim_server_{timestamp}.log"
                shutil.copy2(GAME_LOG, archived_path)
                print(f"[Manager] Archived previous log to {archived_path}")
            except Exception as e:
                print(f"[Manager] Failed to rotate log file: {e}")

    def start_server(self) -> Tuple[bool, str]:
        with self.lock:
            self.config = load_config()
            pwd = str(self.config.get("password", "tester"))
            if len(pwd) < 5:
                return False, "Valheim password must be at least 5 characters long."
            if self.state in ["Running", "Starting"]:
                return False, "Server is already starting or running."

            # Clean up any orphaned process
            pid = find_existing_pid()
            if pid:
                print(f"[Manager] Cleaning up orphaned process {pid} before start...")
                try:
                    os.kill(pid, signal.SIGKILL)
                except Exception:
                    pass

            self.existing_pid = None
            self.process = None
            self.state = "Starting"

        def _do_start():
            try:
                self._rotate_logs()

                env = os.environ.copy()
                env["LD_LIBRARY_PATH"] = f"{LINUX64_DIR}:" + env.get("LD_LIBRARY_PATH", "")
                env["SteamAppId"] = "892970"

                cmd = [
                    str(SERVER_BIN),
                    "-name", str(self.config.get("server_name", "ValheimTest")),
                    "-port", str(self.config.get("port", "2456")),
                    "-world", str(self.config.get("world_name", "ValheimTest")),
                    "-password", str(self.config.get("password", "tester")),
                ]

                print(f"[Manager] Launching Valheim dedicated server: {' '.join(cmd)}")
                with open(GAME_LOG, "w", encoding="utf-8") as f:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=f,
                        stderr=subprocess.STDOUT,
                        env=env,
                        cwd=str(SERVER_DIR),
                    )

                with self.lock:
                    self.process = proc
                    self.state = "Running"
                    self.start_time = time.time()
                SERVER_HISTORY.on_server_start(
                    pid=proc.pid,
                    server_name=str(self.config.get("server_name", "ValheimTest")),
                    world_name=str(self.config.get("world_name", "ValheimTest")),
                    port=self.config.get("port", 2456),
                    trigger="Web Dashboard",
                )
                print(f"[Manager] Valheim dedicated server successfully started (PID {proc.pid}).")
            except Exception as e:
                with self.lock:
                    self.state = "Stopped"
                print(f"[Manager] Failed to start server: {e}")

        t = threading.Thread(target=_do_start, daemon=True)
        t.start()
        return True, "Server launch initiated."

    def stop_server(self) -> Tuple[bool, str]:
        with self.lock:
            if self.state in ["Stopped", "Stopping"]:
                return False, "Server is already stopped or stopping."

            proc = self.process
            ex_pid = self.existing_pid

            if not proc and not ex_pid:
                self.state = "Stopped"
                return False, "No active server process detected."

            self.state = "Stopping"

        def _do_stop():
            try:
                print("[Manager] Sending SIGINT (Ctrl+C) for graceful world save...")
                if proc and proc.poll() is None:
                    proc.send_signal(signal.SIGINT)
                    for _ in range(30):
                        if proc.poll() is not None:
                            break
                        time.sleep(1)
                    if proc.poll() is None:
                        print("[Manager] SIGINT timeout reached. Terminating process...")
                        proc.terminate()
                        proc.wait(timeout=5)
                elif ex_pid:
                    try:
                        os.kill(ex_pid, signal.SIGINT)
                        for _ in range(30):
                            try:
                                os.kill(ex_pid, 0)
                                time.sleep(1)
                            except OSError:
                                break
                        # Force kill if still running
                        try:
                            os.kill(ex_pid, signal.SIGKILL)
                        except OSError:
                            pass
                    except OSError:
                        pass
            except Exception as e:
                print(f"[Manager] Error during shutdown: {e}")
            finally:
                with self.lock:
                    self.process = None
                    self.existing_pid = None
                    self.state = "Stopped"
                    self.start_time = None
                PLAYER_TRACKER.on_server_stop()
                SERVER_HISTORY.on_server_stop("Clean Shutdown (User Requested)")
                print("[Manager] Server cleanly stopped.")

        t = threading.Thread(target=_do_stop, daemon=True)
        t.start()
        return True, "Graceful server shutdown initiated."

    def restart_server(self) -> Tuple[bool, str]:
        ok, msg = self.stop_server()
        if not ok and self.state != "Stopped":
            return False, msg

        def _do_restart():
            for _ in range(35):
                with self.lock:
                    st = self.state
                if st == "Stopped":
                    break
                time.sleep(1)
            self.start_server()

        t = threading.Thread(target=_do_restart, daemon=True)
        t.start()
        return True, "Server restart initiated."

    def update_config(self, new_cfg: Dict[str, Any]) -> Tuple[bool, str]:
        ok, msg = save_config(new_cfg)
        if ok:
            with self.lock:
                self.config = load_config()
        return ok, msg

    def _monitor_loop(self):
        while self._monitor_task_running:
            time.sleep(3)
            with self.lock:
                state = self.state
                proc = self.process
                ex_pid = self.existing_pid
                auto_restart = self.config.get("auto_restart", True)

            if state == "Running":
                PLAYER_TRACKER.process_new_logs()
                if proc:
                    ret = proc.poll()
                    if ret is not None:
                        now = time.time()
                        self.crash_history.append(now)
                        self.crash_history = [t for t in self.crash_history if now - t < 60]
                        print(f"[Manager] Process exited unexpectedly with code {ret}")

                        with self.lock:
                            self.process = None
                            self.start_time = None
                            self.state = "Stopped"
                        PLAYER_TRACKER.on_server_stop()
                        SERVER_HISTORY.on_server_stop(f"Process Exited Unexpectedly (code {ret})")

                        if auto_restart:
                            if len(self.crash_history) >= 3:
                                print("[Manager] Crash loop detected (3 crashes in 60s). Pausing auto-restart.")
                            else:
                                print("[Manager] Auto-restart enabled. Restarting in 5s...")
                                time.sleep(5)
                                self.start_server()
                elif ex_pid:
                    try:
                        os.kill(ex_pid, 0)
                    except OSError:
                        print(f"[Manager] Attached process {ex_pid} terminated.")
                        with self.lock:
                            self.existing_pid = None
                            self.start_time = None
                            self.state = "Stopped"
                        PLAYER_TRACKER.on_server_stop()
                        SERVER_HISTORY.on_server_stop("Attached Process Terminated")


# Global singleton instance
SERVER_MANAGER = ValheimServerManager()
