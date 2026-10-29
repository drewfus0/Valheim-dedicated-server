import json
import os
import re
import struct
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.paths import CONFIG_FILE, GAME_LOG, LOGS_DIR, WORLD_SAVE_DIR
from core.players import PLAYER_TRACKER

# Map constants matching the 6144x6144 high-res Valheim world export for seed 6d8gTfltRB
MAP_IMAGE_WIDTH = 6144
MAP_IMAGE_HEIGHT = 6144
MAP_CENTER_X = 3072.0
MAP_CENTER_Y = 3072.0
MAP_DISK_RADIUS_PX = 2629.6
GAME_WORLD_RADIUS = 10500.0  # Valheim disk edge radius in meters
MAP_SCALE = MAP_DISK_RADIUS_PX / GAME_WORLD_RADIUS  # ~0.25044 pixels per game meter

# Spawn location for seed 6d8gTfltRB (Sacrificial Stones altar in zone 2,0)
DEFAULT_SPAWN = {
    "name": "Sacrificial Stones",
    "x": 90.8,
    "y": 36.0,
    "z": 36.0,
    "px": round(MAP_CENTER_X + 90.8 * MAP_SCALE, 1),
    "py": round(MAP_CENTER_Y - 36.0 * MAP_SCALE, 1),
}

_MAP_CACHE_LOCK = threading.RLock()
_CACHED_MAP_DATA: Optional[Dict[str, Any]] = None
_CACHED_MTIME: Optional[float] = None
_CACHED_WORLD: Optional[str] = None


def game_to_map_pixel(x: float, z: float) -> Tuple[float, float]:
    """Convert in-game 3D coordinate (x East/West, z North/South) to 6144x6144 pixel coordinates."""
    px = round(MAP_CENTER_X + x * MAP_SCALE, 1)
    py = round(MAP_CENTER_Y - z * MAP_SCALE, 1)
    return px, py


def map_pixel_to_game(px: float, py: float) -> Tuple[float, float]:
    """Convert 6144x6144 pixel coordinates back to in-game meters (x, z)."""
    x = round((px - MAP_CENTER_X) / MAP_SCALE, 1)
    z = round((MAP_CENTER_Y - py) / MAP_SCALE, 1)
    return x, z


def get_active_world_name() -> str:
    """Retrieve active world name from server configuration or fallback."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get("world_name", "TheOneWorld")
        except Exception:
            pass
    return "TheOneWorld"


def extract_portals_from_world(world_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Scan world chunk data for all portals, tags, builder player identities, and 3D locations."""
    if not world_name:
        world_name = get_active_world_name()

    chunked_dir = WORLD_SAVE_DIR / "worlds_local" / world_name
    if not chunked_dir.exists():
        return []

    # Valheim chunked worlds store global items in 00_01__*.chunk
    target_chunks = list(chunked_dir.glob("00_01__*.chunk"))
    if not target_chunks:
        target_chunks = list(chunked_dir.glob("*.chunk"))
    if not target_chunks:
        return []

    target_chunk = max(target_chunks, key=lambda p: p.stat().st_mtime)

    try:
        data = target_chunk.read_bytes()
    except Exception as e:
        print(f"[Map] Error reading chunk file {target_chunk}: {e}")
        return []

    # Resolve Steam IDs to human character names
    steam_id_map = getattr(PLAYER_TRACKER, "steam_id_to_player", {})
    # Check server log fallback
    log_names = {}
    if GAME_LOG.exists():
        try:
            with open(GAME_LOG, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.search(r"Player history entry with index \d+:\s+([^\(]+)\s+\((Steam_\d+)", line)
                    if m:
                        log_names[m.group(2)] = m.group(1).strip()
        except Exception:
            pass

    tag_hash = struct.pack("<i", 696029674)  # GetStableHashCode("tag")
    pos = 0
    prev_end = 0
    raw_portals = []
    idx = 0

    while True:
        pos = data.find(tag_hash, pos)
        if pos == -1:
            break
        if pos + 5 > len(data):
            break

        slen = data[pos + 4]
        if slen > 100:
            pos += 4
            continue

        raw_tag = data[pos + 5 : pos + 5 + slen].decode("utf-8", errors="replace").strip()
        display_tag = raw_tag if raw_tag else "(Unlabeled)"

        # Builder detection: check window for Steam ID
        window = data[max(0, pos - 120) : min(len(data), pos + 120)]
        author_m = re.search(rb"Steam_(\d+)", window)
        author_raw = author_m.group(0).decode("ascii") if author_m else "Unknown"
        clean_steam_id = author_raw.replace("Steam_", "")

        author_name = steam_id_map.get(clean_steam_id) or log_names.get(author_raw, author_raw)
        player_style = PLAYER_TRACKER.get_player_style(author_name)

        # Coordinate unpacking: find valid (x, y, z) vector preceding portal tag
        candidate = None
        for off in range(prev_end, pos - 11):
            try:
                x, y, z = struct.unpack_from("<fff", data, off)
                if -GAME_WORLD_RADIUS <= x <= GAME_WORLD_RADIUS and -100 <= y <= 2000 and -GAME_WORLD_RADIUS <= z <= GAME_WORLD_RADIUS:
                    if abs(x) > 1 and abs(z) > 1:
                        candidate = (round(x, 1), round(y, 1), round(z, 1))
            except Exception:
                pass

        if not candidate:
            candidate = (0.0, 0.0, 0.0)

        px, py = game_to_map_pixel(candidate[0], candidate[2])

        idx += 1
        raw_portals.append({
            "id": f"portal_{idx}",
            "tag": display_tag,
            "raw_tag": raw_tag,
            "author": author_name,
            "author_id": author_raw,
            "author_color": player_style["color"],
            "author_avatar": player_style["avatar"],
            "x": candidate[0],
            "y": candidate[1],
            "z": candidate[2],
            "px": px,
            "py": py,
        })

        prev_end = pos + 5 + slen
        pos += 4

    # Group portals by tag to identify paired links and connection routes
    by_tag: Dict[str, List[Dict[str, Any]]] = {}
    for p in raw_portals:
        by_tag.setdefault(p["tag"].lower(), []).append(p)

    for p in raw_portals:
        peers = by_tag[p["tag"].lower()]
        is_linked = len(peers) >= 2
        p["linked"] = is_linked
        p["pair_count"] = len(peers)
        p["status"] = f"Linked Network ({len(peers)} Nodes)" if is_linked else "Solo / Awaiting Match"
        p["paired_with"] = [
            {
                "id": o["id"],
                "tag": o["tag"],
                "x": o["x"],
                "y": o["y"],
                "z": o["z"],
                "px": o["px"],
                "py": o["py"],
                "author": o["author"],
                "author_color": o["author_color"],
            }
            for o in peers
            if o["id"] != p["id"]
        ]

    # Sort portals: linked first, then alphabetically by tag
    raw_portals.sort(key=lambda p: (not p["linked"], p["tag"].lower(), p["id"]))
    return raw_portals


def get_world_map_data(world_name: Optional[str] = None, force_refresh: bool = False) -> Dict[str, Any]:
    """Retrieve complete world map dataset including spawn, portals, and telemetry."""
    global _CACHED_MAP_DATA, _CACHED_MTIME, _CACHED_WORLD

    if not world_name:
        world_name = get_active_world_name()

    chunked_dir = WORLD_SAVE_DIR / "worlds_local" / world_name
    latest_chunk: Optional[Path] = None
    chunk_mtime = 0.0

    if chunked_dir.exists():
        chunks = list(chunked_dir.glob("00_01__*.chunk"))
        if not chunks:
            chunks = list(chunked_dir.glob("*.chunk"))
        if chunks:
            latest_chunk = max(chunks, key=lambda p: p.stat().st_mtime)
            chunk_mtime = latest_chunk.stat().st_mtime

    with _MAP_CACHE_LOCK:
        if not force_refresh and _CACHED_MAP_DATA and _CACHED_WORLD == world_name and _CACHED_MTIME == chunk_mtime:
            return _CACHED_MAP_DATA

        portals = extract_portals_from_world(world_name)
        linked_count = sum(1 for p in portals if p["linked"])
        solo_count = sum(1 for p in portals if not p["linked"])
        unique_tags = len({p["tag"].lower() for p in portals})

        data = {
            "world_name": world_name,
            "seed": "6d8gTfltRB",
            "spawn": DEFAULT_SPAWN,
            "bounds": {
                "min_x": -GAME_WORLD_RADIUS,
                "max_x": GAME_WORLD_RADIUS,
                "min_z": -GAME_WORLD_RADIUS,
                "max_z": GAME_WORLD_RADIUS,
                "width_px": MAP_IMAGE_WIDTH,
                "height_px": MAP_IMAGE_HEIGHT,
                "center_x_px": MAP_CENTER_X,
                "center_y_px": MAP_CENTER_Y,
                "scale": MAP_SCALE,
            },
            "image_url": "/static/img/world_map.webp",
            "portals": portals,
            "stats": {
                "total_portals": len(portals),
                "linked_portals": linked_count,
                "solo_portals": solo_count,
                "unique_tags": unique_tags,
                "chunk_file": latest_chunk.name if latest_chunk else None,
            },
        }

        _CACHED_MAP_DATA = data
        _CACHED_MTIME = chunk_mtime
        _CACHED_WORLD = world_name
        return data
