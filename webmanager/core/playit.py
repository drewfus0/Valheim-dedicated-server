import json
import os
import re
import socket
import threading
import time
from typing import Optional

_CACHED_TUNNEL: Optional[str] = None
_LOCK = threading.Lock()
_MONITOR_STARTED = False

DEFAULT_SOCKET_PATH = "/run/playit/playitd.sock"
DEFAULT_LOG_PATH = "/var/log/playit/playit.log"


def fetch_tunnel_from_socket(
    target_port: str = "2456",
    sock_path: str = DEFAULT_SOCKET_PATH,
    timeout: float = 1.0,
) -> Optional[str]:
    """Passively query local playitd IPC Unix domain socket for active tunnel mappings."""
    if not os.path.exists(sock_path):
        return None

    sock = None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(sock_path)

        stream = sock.makefile("rwb")
        # Read the initial JSON hello handshake line
        hello_line = stream.readline()
        if not hello_line:
            return None

        # Request daemon state
        req = {
            "ipc_version": 2,
            "request_id": 1,
            "request": {"type": "get_state"},
        }
        stream.write(json.dumps(req).encode("utf-8") + b"\n")
        stream.flush()

        resp_line = stream.readline()
        if not resp_line:
            return None

        data = json.loads(resp_line.decode("utf-8", errors="ignore"))
        response = data.get("data", {}).get("response", {})
        state_data = response.get("data", {})
        tunnels = state_data.get("data", {}).get("tunnels", [])

        # Look for a tunnel matching the target port in destination (e.g., 127.0.0.1:2456)
        for tunnel in tunnels:
            if tunnel.get("is_disabled"):
                continue
            dest = str(tunnel.get("destination", ""))
            addr = tunnel.get("display_address")
            if addr and (f":{target_port}" in dest or dest == str(target_port)):
                return addr

        # Fallback: If tunnels exist, return first non-disabled tunnel display_address
        for tunnel in tunnels:
            if not tunnel.get("is_disabled") and tunnel.get("display_address"):
                return tunnel["display_address"]

    except Exception:
        pass
    finally:
        if sock:
            try:
                sock.close()
            except Exception:
                pass

    return None


def fetch_tunnel_from_log(
    target_port: str = "2456",
    log_path: str = DEFAULT_LOG_PATH,
) -> Optional[str]:
    """Passively read the tail of playit log file to extract tunnel mappings."""
    if not os.path.exists(log_path) or not os.access(log_path, os.R_OK):
        return None

    try:
        file_size = os.path.getsize(log_path)
        read_bytes = min(file_size, 65536)  # Read last 64KB
        with open(log_path, "rb") as f:
            if file_size > read_bytes:
                f.seek(file_size - read_bytes)
            content = f.read().decode("utf-8", errors="ignore")

        # Search for mapping patterns like: address.playit.gg:12345 => 127.0.0.1:2456
        matches = re.findall(
            r"([a-zA-Z0-9\.\-_]+\.[a-zA-Z0-9\.\-_]+:\d+)\s*=>\s*127\.0\.0\.1:(\d+)",
            content,
        )
        for public_addr, local_port in matches:
            if str(local_port) == str(target_port):
                return public_addr

        if matches:
            return matches[-1][0]  # Return most recent match
    except Exception:
        pass

    return None


def detect_playit_tunnel(target_port: str = "2456") -> Optional[str]:
    """Perform passive multi-tier detection for active Playit tunnels without spawning daemons."""
    # Tier 1: Direct IPC socket check
    addr = fetch_tunnel_from_socket(target_port)
    if addr:
        return addr

    # Tier 2: Passive log parsing fallback
    addr = fetch_tunnel_from_log(target_port)
    if addr:
        return addr

    return None


def _background_monitor_loop(interval_seconds: int = 60):
    global _CACHED_TUNNEL
    while True:
        try:
            tunnel = detect_playit_tunnel("2456")
            with _LOCK:
                _CACHED_TUNNEL = tunnel
        except Exception:
            pass
        time.sleep(interval_seconds)


def start_playit_monitor(interval_seconds: int = 60):
    """Start passive background worker to refresh active tunnel cache."""
    global _MONITOR_STARTED
    with _LOCK:
        if not _MONITOR_STARTED:
            _MONITOR_STARTED = True
            t = threading.Thread(
                target=_background_monitor_loop,
                args=(interval_seconds,),
                daemon=True,
                name="PlayitTunnelMonitor",
            )
            t.start()


def get_detected_playit_tunnel(target_port: str = "2456") -> Optional[str]:
    """Thread-safe instant read of cached Playit tunnel, with on-demand fallback."""
    start_playit_monitor()
    with _LOCK:
        cached = _CACHED_TUNNEL
    if cached:
        return cached

    # If cache is not populated yet, perform a quick passive query
    tunnel = detect_playit_tunnel(target_port)
    if tunnel:
        with _LOCK:
            _CACHED_TUNNEL = tunnel
    return tunnel
