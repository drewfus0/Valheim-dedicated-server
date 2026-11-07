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
from core.events import EVENT_BUS
from core.logs import GAME_LOG
from core.paths import GAME_LOG, LINUX64_DIR, LOGS_DIR, SERVER_BIN, SERVER_DIR
from core.players import PLAYER_TRACKER
from core.playit import get_detected_playit_tunnel
from core.server_history import SERVER_HISTORY
from core.system import get_cpu_frequencies, get_system_power_status
from core.world_stats import WORLD_STATS


def find_existing_pid() -> Optional[int]:
    """Find PID of running valheim_server.x86_64 instance if any."""
    try:
        out = subprocess.check_output(["pgrep", "-f", "valheim_server.x86_64"], text=True)
        pids = [int(p.strip()) for p in out.strip().split() if p.strip().isdigit()]
        if pids:
            return pids[0]
    except Exception:
        pass
def build_valheim_command(cfg: Dict[str, Any]) -> List[str]:
    """Build the Valheim dedicated server CLI argument vector including world modifiers."""
    cmd = [
        str(SERVER_BIN),
        "-name", str(cfg.get("server_name", "ValheimTest")),
        "-port", str(cfg.get("port", "2456")),
        "-world", str(cfg.get("world_name", "ValheimTest")),
        "-password", str(cfg.get("password", "tester")),
    ]

    mods = cfg.get("modifiers")
    if isinstance(mods, dict):
        # 1. Reset world modifiers if requested
        if mods.get("reset_modifiers"):
            cmd.append("-resetmodifiers")

        # 2. Preset (must precede individual modifiers to avoid overriding them)
        preset = str(mods.get("preset", "")).strip().lower()
        if preset and preset not in ("default", "custom"):
            cmd.extend(["-preset", preset])

        # 3. Graduated modifiers
        combat = str(mods.get("combat", "")).strip().lower()
        if combat and combat != "default":
            cmd.extend(["-modifier", "combat", combat])

        death = str(mods.get("death_penalty", "")).strip().lower()
        if death and death != "default":
            cmd.extend(["-modifier", "deathpenalty", death])

        res = str(mods.get("resources", "")).strip().lower()
        if res and res != "default":
            cmd.extend(["-modifier", "resources", res])

        raids = str(mods.get("raids", "")).strip().lower()
        if raids and raids != "default":
            cmd.extend(["-modifier", "raids", raids])

        portals = str(mods.get("portals", "")).strip().lower()
        if portals and portals != "default":
            cmd.extend(["-modifier", "portals", portals])

        # 4. Binary key toggles (-setkey)
        for key in ("nobuildcost", "playerevents", "passivemobs", "nomap"):
            if mods.get(key):
                cmd.extend(["-setkey", key])

    return cmd


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
        self._prev_fingerprint: Optional[Dict[str, Any]] = None
        self._last_telemetry_sig: Optional[Tuple[Any, ...]] = None
        self._last_telemetry_push_ts: float = 0.0

        # Check for pre-existing process on initialization
        pid = find_existing_pid()
        if pid:
            print(f"[Manager] Attached to pre-existing Valheim server PID {pid}.")
            self.existing_pid = pid
            self.state = "Running"
            try:
                out = subprocess.check_output(["ps", "-p", str(pid), "-o", "etimes="], text=True)
                self.start_time = time.time() - int(out.strip())
            except Exception:
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

    def check_auto_start_on_boot(self):
        """If auto_start_on_boot is enabled and no server process is running, start it."""
        with self.lock:
            pid = self.get_active_pid()
            if not pid and self.state == "Stopped":
                cfg = load_config()
                if cfg.get("auto_start_on_boot", True):
                    print("[Manager] Auto-Start on Boot is enabled. Launching Valheim dedicated server...")
                    self.start_server(trigger="Auto-Start on Boot")

    def get_active_pid(self) -> Optional[int]:
        with self.lock:
            if self.process and self.process.poll() is None:
                return self.process.pid
            return self.existing_pid

    def is_running(self) -> bool:
        with self.lock:
            return self.state in ("Running", "Starting") or self.get_active_pid() is not None

    @staticmethod
    def _sample_process_metrics(pid: Optional[int], state: str) -> Tuple[str, str]:
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
        return cpu, mem

    def get_telemetry_snapshot(
        self,
        cpu_freq: Optional[Dict[str, Any]] = None,
        power_status: Optional[Dict[str, Any]] = None,
        p_summary: Optional[Dict[str, Any]] = None,
        w_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Build a compact JSON telemetry & clock-anchor payload for SSE streaming."""
        with self.lock:
            state = self.state
            start_t = self.start_time
            pid = self.process.pid if self.process and self.process.poll() is None else self.existing_pid

        server_running = (state == "Running")
        cpu, mem = self._sample_process_metrics(pid, state)

        if p_summary is None:
            p_summary = PLAYER_TRACKER.get_summary(server_running=server_running)
        players_count = p_summary.get("online_count", 0)

        if cpu_freq is None:
            cpu_freq = get_cpu_frequencies()
        if power_status is None:
            power_status = get_system_power_status()

        now_ts = time.time()
        if w_summary is not None:
            tod = w_summary.get("time_of_day") or WORLD_STATS.get_time_of_day_data(
                server_running=server_running,
                players_count=players_count,
            )
            nights_slept = w_summary.get("nights_slept", WORLD_STATS.nights_slept)
            active_raid = w_summary.get("active_raid")
        else:
            tod = WORLD_STATS.get_time_of_day_data(
                server_running=server_running,
                players_count=players_count,
            )
            nights_slept = WORLD_STATS.nights_slept
            active_raid = None
            if WORLD_STATS.active_raid and server_running:
                try:
                    from core.world_stats import parse_timestamp as _parse_ws_ts

                    r_ts = _parse_ws_ts(WORLD_STATS.active_raid.get("timestamp", ""))
                    if r_ts:
                        diff = (datetime.datetime.now() - r_ts).total_seconds()
                        if 0 <= diff <= 180:
                            active_raid = {
                                "seconds_remaining": max(0, int(180 - diff)),
                            }
                except Exception:
                    pass

        raid_end_ts: Optional[float] = None
        if active_raid and active_raid.get("seconds_remaining") is not None:
            raid_end_ts = round(now_ts + float(active_raid["seconds_remaining"]), 2)

        return {
            "status": state,
            "pid": pid,
            "server_running": server_running,
            "start_time": round(start_t, 3) if start_t else None,
            "server_now": round(now_ts, 3),
            "cpu": cpu,
            "mem": mem,
            "players_count": players_count,
            "cpu_freq": {
                "min_mhz": cpu_freq.get("min_mhz", 0),
                "max_mhz": cpu_freq.get("max_mhz", 0),
                "cores_count": cpu_freq.get("cores_count", 0),
                "all_under_600": bool(cpu_freq.get("all_under_600", False)),
            },
            "power_profile": (power_status or {}).get("profile", "unknown"),
            "world_clock": {
                "net_time": round(float(tod.get("net_time", 0.0)), 3),
                "net_time_ref": round(now_ts, 3),
                "is_frozen": bool(tod.get("is_frozen", True)),
                "current_day": int(tod.get("current_day", 1)),
                "nights_slept": int(nights_slept),
            },
            "raid_end_ts": raid_end_ts,
        }

    def get_status_data(self) -> Dict[str, Any]:
        with self.lock:
            state = self.state
            start_t = self.start_time
            pid = self.process.pid if self.process and self.process.poll() is None else self.existing_pid
            cfg = self.config.copy()

        cpu, mem = self._sample_process_metrics(pid, state)

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
            "hidden_players_count": p_summary.get("hidden_count", 0),
            "total_known_players_count": p_summary.get("total_known_count", len(p_summary["all_players"])),
            "total_deaths": p_summary.get("total_deaths", 0),
            "all_players": p_summary["all_players"],
            "player_sessions": p_summary["recent_events"],
            "server_history": s_summary,
            "server_runs": s_summary["recent_runs"],
            "world_stats": WORLD_STATS.get_summary(server_running=(state == "Running"), players_count=p_summary["online_count"]),
            "cpu_freq": cpu_freq,
            "power_status": power_status,
            "auto_start_on_boot": cfg.get("auto_start_on_boot", True),
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
                "modifiers": cfg.get("modifiers", {}),
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

    def start_server(self, trigger: str = "Web Dashboard") -> Tuple[bool, str]:
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

        EVENT_BUS.publish("statusChanged")

        def _do_start():
            try:
                self._rotate_logs()

                env = os.environ.copy()
                env["LD_LIBRARY_PATH"] = f"{LINUX64_DIR}:" + env.get("LD_LIBRARY_PATH", "")
                env["SteamAppId"] = "892970"

                cmd = build_valheim_command(self.config)

                # If one-time reset_modifiers flag was active, clear it for subsequent starts
                mods = self.config.get("modifiers")
                if isinstance(mods, dict) and mods.get("reset_modifiers"):
                    cleaned_mods = mods.copy()
                    cleaned_mods["reset_modifiers"] = False
                    save_config({"modifiers": cleaned_mods})
                    with self.lock:
                        self.config = load_config()
                    EVENT_BUS.publish("configUpdated")

                print(f"[Manager] Launching Valheim dedicated server: {' '.join(cmd)}")
                with open(GAME_LOG, "w", encoding="utf-8") as f:
                    proc = subprocess.Popen(
                        cmd,
                        stdout=f,
                        stderr=subprocess.STDOUT,
                        env=env,
                        cwd=str(SERVER_DIR),
                        start_new_session=True,
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
                    trigger=trigger,
                )
                print(f"[Manager] Valheim dedicated server successfully started (PID {proc.pid}).")
                EVENT_BUS.publish_many(["statusChanged", "serverHistoryUpdated"])
                if EVENT_BUS.has_subscribers():
                    EVENT_BUS.publish("telemetry", self.get_telemetry_snapshot())
            except Exception as e:
                with self.lock:
                    self.state = "Stopped"
                print(f"[Manager] Failed to start server: {e}")
                EVENT_BUS.publish("statusChanged")

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

        EVENT_BUS.publish("statusChanged")

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
                EVENT_BUS.publish_many([
                    "statusChanged",
                    "playersUpdated",
                    "sessionHistoryUpdated",
                    "serverHistoryUpdated",
                    "raidAlertUpdated",
                ])
                if EVENT_BUS.has_subscribers():
                    EVENT_BUS.publish("telemetry", self.get_telemetry_snapshot())

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
            self.start_server(trigger="Web Restart")

        t = threading.Thread(target=_do_restart, daemon=True)
        t.start()
        return True, "Server restart initiated."

    def update_config(self, new_cfg: Dict[str, Any]) -> Tuple[bool, str]:
        ok, msg = save_config(new_cfg)
        if ok:
            with self.lock:
                self.config = load_config()
        return ok, msg

    @staticmethod
    def _build_fingerprint(
        state: str,
        active_pid: Optional[int],
        cpu_freq: Dict[str, Any],
        power_status: Dict[str, Any],
        p_summary: Dict[str, Any],
        s_summary: Dict[str, Any],
        w_summary: Dict[str, Any],
    ) -> Dict[str, Any]:
        active_raid = w_summary.get("active_raid") or {}
        return {
            "status": (
                state,
                active_pid,
                bool(cpu_freq.get("all_under_600", False)),
                (power_status or {}).get("profile"),
            ),
            "players": (
                p_summary.get("online_count", 0),
                tuple(p_summary.get("online_players", [])),
                p_summary.get("known_count", 0),
                p_summary.get("hidden_count", 0),
                p_summary.get("total_deaths", 0),
            ),
            "sessions": len(p_summary.get("recent_events", [])),
            "server_history": (
                s_summary.get("total_runs", 0),
                s_summary.get("total_saves", 0),
                s_summary.get("last_save_time"),
            ),
            "raid_alert": (
                bool(w_summary.get("active_raid")),
                active_raid.get("id"),
                bool(w_summary.get("is_boss_battle_active", False)),
                w_summary.get("active_bosses_count", 0),
            ),
            "world_saga": (
                w_summary.get("current_day", 1),
                w_summary.get("nights_slept", 0),
                w_summary.get("total_raids", 0),
                w_summary.get("slain_bosses_count", 0),
                w_summary.get("total_boss_defeats_count", 0),
                w_summary.get("discovered_bosses_count", 0),
                bool(w_summary.get("is_boss_battle_active", False)),
                w_summary.get("connected_portals", 0),
                w_summary.get("portal_pairs", 0),
                w_summary.get("portals_linked_pairs_count", 0),
                w_summary.get("portals_unlinked_count", 0),
                w_summary.get("dungeons_entered", 0),
                w_summary.get("dungeon_rooms", 0),
            ),
        }

    def _monitor_loop(self):
        while self._monitor_task_running:
            time.sleep(3)
            try:
                with self.lock:
                    state = self.state
                    proc = self.process
                    ex_pid = self.existing_pid
                    auto_restart = self.config.get("auto_restart", True)

                crashed = False
                if state == "Running":
                    PLAYER_TRACKER.process_new_logs()
                    WORLD_STATS.process_new_logs()
                    SERVER_HISTORY.process_new_logs()
                    if proc:
                        ret = proc.poll()
                        if ret is not None:
                            crashed = True
                            now = time.time()
                            self.crash_history.append(now)
                            self.crash_history = [t for t in self.crash_history if now - t < 60]
                            print(f"[Manager] Process exited unexpectedly with code {ret}")

                            with self.lock:
                                self.process = None
                                self.start_time = None
                                self.state = "Stopped"
                                state = "Stopped"
                            PLAYER_TRACKER.on_server_stop()
                            SERVER_HISTORY.on_server_stop(f"Process Exited Unexpectedly (code {ret})")
                            EVENT_BUS.publish_many([
                                "statusChanged",
                                "playersUpdated",
                                "sessionHistoryUpdated",
                                "serverHistoryUpdated",
                                "raidAlertUpdated",
                            ])
                            if EVENT_BUS.has_subscribers():
                                EVENT_BUS.publish("telemetry", self.get_telemetry_snapshot())

                            if auto_restart:
                                if len(self.crash_history) >= 3:
                                    print("[Manager] Crash loop detected (3 crashes in 60s). Pausing auto-restart.")
                                else:
                                    print("[Manager] Auto-restart enabled. Restarting in 5s...")
                                    time.sleep(5)
                                    self.start_server(trigger="Auto-Restart (Crash)")
                    elif ex_pid:
                        try:
                            os.kill(ex_pid, 0)
                        except OSError:
                            crashed = True
                            print(f"[Manager] Attached process {ex_pid} terminated.")
                            with self.lock:
                                self.existing_pid = None
                                self.start_time = None
                                self.state = "Stopped"
                                state = "Stopped"
                            PLAYER_TRACKER.on_server_stop()
                            SERVER_HISTORY.on_server_stop("Attached Process Terminated")
                            EVENT_BUS.publish_many([
                                "statusChanged",
                                "playersUpdated",
                                "sessionHistoryUpdated",
                                "serverHistoryUpdated",
                                "raidAlertUpdated",
                            ])
                            if EVENT_BUS.has_subscribers():
                                EVENT_BUS.publish("telemetry", self.get_telemetry_snapshot())

                if crashed:
                    continue

                with self.lock:
                    state = self.state
                    active_pid = self.process.pid if self.process and self.process.poll() is None else self.existing_pid

                server_running = (state == "Running")
                cpu_freq = get_cpu_frequencies()
                power_status = get_system_power_status()
                p_summary = PLAYER_TRACKER.get_summary(server_running=server_running)
                s_summary = SERVER_HISTORY.get_summary(server_running=server_running, pid=active_pid)
                w_summary = WORLD_STATS.get_summary(
                    server_running=server_running,
                    players_count=p_summary.get("online_count", 0),
                )

                fp = self._build_fingerprint(
                    state=state,
                    active_pid=active_pid,
                    cpu_freq=cpu_freq,
                    power_status=power_status,
                    p_summary=p_summary,
                    s_summary=s_summary,
                    w_summary=w_summary,
                )

                events_to_emit: List[str] = []
                prev = self._prev_fingerprint
                if prev is not None:
                    if fp["status"] != prev["status"]:
                        events_to_emit.append("statusChanged")
                    if fp["players"] != prev["players"]:
                        events_to_emit.extend(["playersUpdated", "statusChanged"])
                    if fp["sessions"] != prev["sessions"]:
                        events_to_emit.append("sessionHistoryUpdated")
                    if fp["server_history"] != prev["server_history"]:
                        events_to_emit.append("serverHistoryUpdated")
                    if fp["raid_alert"] != prev["raid_alert"]:
                        events_to_emit.append("raidAlertUpdated")
                    if fp["world_saga"] != prev["world_saga"]:
                        events_to_emit.append("worldSagaUpdated")

                self._prev_fingerprint = fp

                if events_to_emit:
                    EVENT_BUS.publish_many(events_to_emit)

                if EVENT_BUS.has_subscribers() and (server_running or events_to_emit):
                    snap = self.get_telemetry_snapshot(
                        cpu_freq=cpu_freq,
                        power_status=power_status,
                        p_summary=p_summary,
                        w_summary=w_summary,
                    )
                    sig = (
                        snap["status"],
                        snap["cpu"],
                        snap["mem"],
                        snap["cpu_freq"]["min_mhz"],
                        snap["cpu_freq"]["max_mhz"],
                        snap["world_clock"]["is_frozen"],
                        snap["world_clock"]["nights_slept"],
                        snap["raid_end_ts"],
                    )
                    now_ts = time.time()
                    if (
                        bool(events_to_emit)
                        or sig != self._last_telemetry_sig
                        or (now_ts - self._last_telemetry_push_ts) >= 15.0
                    ):
                        self._last_telemetry_sig = sig
                        self._last_telemetry_push_ts = now_ts
                        EVENT_BUS.publish("telemetry", snap)
            except Exception as e:
                print(f"[Manager] Error in monitor loop: {e}")


# Global singleton instance
SERVER_MANAGER = ValheimServerManager()
