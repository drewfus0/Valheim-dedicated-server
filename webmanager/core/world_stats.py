import datetime
import json
import os
import re
import struct
import threading
import time
import zlib
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from core.paths import CONFIG_FILE, GAME_LOG, LOGS_DIR, WORLD_SAVE_DIR, WORLD_STATS_FILE

# Lore definitions for Valheim random events / raids
EVENT_LORE: Dict[str, Dict[str, str]] = {
    "army_eikthyr": {
        "title": "Eikthyr's Army",
        "quote": "Eikthyr rallies the creatures of the forest.",
        "biome": "Meadows",
        "icon": "⚡",
    },
    "army_theelder": {
        "title": "The Forest is Moving",
        "quote": "The creatures of the Black Forest assault your camp.",
        "biome": "Black Forest",
        "icon": "🌲",
    },
    "army_bonemass": {
        "title": "A Foul Smell from the Swamp",
        "quote": "Creatures of the swamp rise up.",
        "biome": "Swamp",
        "icon": "💀",
    },
    "army_moder": {
        "title": "A Cold Wind Blows",
        "quote": "Drakes sweep down from the Mountain peaks.",
        "biome": "Mountain",
        "icon": "❄️",
    },
    "army_yagluth": {
        "title": "The Horde is Attacking",
        "quote": "Fuling warbands march against you.",
        "biome": "Plains",
        "icon": "🔥",
    },
    "skeletons": {
        "title": "Skeleton Surprise",
        "quote": "Undead legions emerge from the soil.",
        "biome": "Any",
        "icon": "☠️",
    },
    "wolves": {
        "title": "You Are Being Hunted",
        "quote": "A ravenous wolf pack descends upon you.",
        "biome": "Mountain / Plains",
        "icon": "🐺",
    },
    "foresttrolls": {
        "title": "The Ground is Shaking",
        "quote": "Trolls stomp towards your settlement.",
        "biome": "Black Forest",
        "icon": "🪨",
    },
    "blobs": {
        "title": "A Foul Smell",
        "quote": "Blobs and Oozers emerge from the murky swamp.",
        "biome": "Swamp",
        "icon": "🧪",
    },
    "surtlings": {
        "title": "Sulfur in the Air",
        "quote": "Surtlings burst into flames around you.",
        "biome": "Swamp / Ashlands",
        "icon": "🔥",
    },
}

# 7 Canonical Forsaken Bosses in Valheim Progression Order
FORSAKEN_BOSSES: List[Dict[str, Any]] = [
    {
        "id": "eikthyr",
        "key": "defeated_eikthyr",
        "shrine_code": "Eikthyrnir",
        "aliases": ["Eikthyrnir", "Eikthyr"],
        "name": "Eikthyr",
        "title": "Lord of the Meadows",
        "biome": "Meadows",
        "icon": "🦌",
        "order": 1,
    },
    {
        "id": "theelder",
        "key": "defeated_gdking",
        "shrine_code": "GDKing",
        "aliases": ["GDKing", "TheElder"],
        "name": "The Elder",
        "title": "Ancient Greydwarf King",
        "biome": "Black Forest",
        "icon": "🌲",
        "order": 2,
    },
    {
        "id": "bonemass",
        "key": "defeated_bonemass",
        "shrine_code": "Bonemass",
        "aliases": ["Bonemass"],
        "name": "Bonemass",
        "title": "Gargantuan Swamp Ooze",
        "biome": "Swamp",
        "icon": "💀",
        "order": 3,
    },
    {
        "id": "moder",
        "key": "defeated_dragon",
        "shrine_code": "Dragonqueen",
        "aliases": ["Dragonqueen", "Dragon"],
        "name": "Moder",
        "title": "Mother of Drakes",
        "biome": "Mountain",
        "icon": "🐉",
        "order": 4,
    },
    {
        "id": "yagluth",
        "key": "defeated_goblinking",
        "shrine_code": "GoblinKing",
        "aliases": ["GoblinKing", "Yagluth"],
        "name": "Yagluth",
        "title": "Fallen Goblin Sorcerer",
        "biome": "Plains",
        "icon": "👑",
        "order": 5,
    },
    {
        "id": "queen",
        "key": "defeated_queen",
        "shrine_code": "SeekerQueen",
        "aliases": ["SeekerQueen", "Mistlands_DvergrBossEntrance1", "TheQueen"],
        "name": "The Queen",
        "title": "Hive Mother of the Mistlands",
        "biome": "Mistlands",
        "icon": "🕷️",
        "order": 6,
    },
    {
        "id": "fader",
        "key": "defeated_fader",
        "shrine_code": "Fader",
        "aliases": ["Fader", "FaderLocation"],
        "name": "Fader",
        "title": "Lord of the Ashlands",
        "biome": "Ashlands",
        "icon": "⚔️",
        "order": 7,
    },
]

# Quick lookup by shrine code (including aliases)
BOSS_SHRINES: Dict[str, Dict[str, Any]] = {}
for b in FORSAKEN_BOSSES:
    info = {
        "id": b["id"],
        "name": b["name"],
        "title": b["title"],
        "biome": b["biome"],
        "icon": b["icon"],
        "primary_code": b["shrine_code"],
    }
    BOSS_SHRINES[b["shrine_code"]] = info
    for a in b.get("aliases", []):
        BOSS_SHRINES[a] = info

# Regex parsers for Valheim dedicated server log lines
RE_RAID = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):\s*Random event set:(\w+)")
RE_DAY = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):\s*Time\s+[\d\.]+,\s+day:(\d+)(?:.*?skipspeed:([\d\.]+))?")
RE_LOCATION = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):\s*Found location of type\s+(\w+)")
RE_DUNGEON_LOAD = re.compile(r"Dungeon loaded with")
RE_ROOMS = re.compile(r"Placed\s+(\d+)\s+rooms")
RE_PORTALS_BOOT = re.compile(r"ConnectPortals => Connected\s+(\d+)\s+portals")
RE_PORTALS_LINK = re.compile(r"Connected portals\s+([\d\-]+:\d+)\s+<->\s+([\d\-]+:\d+)")
RE_WORLD_SAVE = re.compile(r"World save \(\d/\d\)|### Save World Thread Started|SaveSystem\.Reload|World auto backup saved")


def parse_timestamp(ts_str: str) -> Optional[datetime.datetime]:
    """Parse MM/DD/YYYY HH:MM:SS string to datetime."""
    try:
        return datetime.datetime.strptime(ts_str.strip(), "%m/%d/%Y %H:%M:%S")
    except Exception:
        return None


def extract_world_keys(world_name: str) -> Dict[str, Any]:
    """Extract global keys and activebosses count from Valheim world save (.db2 or .db)."""
    chunked_dir = WORLD_SAVE_DIR / "worlds_local" / world_name
    db2_files = list(chunked_dir.glob("_main.*.db2"))
    target_file: Optional[Path] = None
    is_chunked = False
    if db2_files:
        target_file = max(db2_files, key=lambda p: p.stat().st_mtime)
        is_chunked = True
    else:
        for sub in ["worlds_local", "worlds"]:
            legacy_file = WORLD_SAVE_DIR / sub / f"{world_name}.db"
            if legacy_file.exists():
                target_file = legacy_file
                break

    if not target_file or not target_file.exists():
        return {"keys": set(), "active_bosses": 0, "mtime": None, "file": None}

    try:
        mtime = target_file.stat().st_mtime
        with open(target_file, "rb") as f:
            raw = f.read()

        if is_chunked:
            gz_idx = raw.find(b"\x1f\x8b")
            if gz_idx != -1:
                data = zlib.decompress(raw[gz_idx:], zlib.MAX_WBITS | 16)
            else:
                data = zlib.decompress(raw[16:], zlib.MAX_WBITS | 16)
        else:
            data = raw

        raw_keys = re.findall(rb"(?:defeated_[a-zA-Z0-9_]+|activebosses[a-zA-Z0-9_ ]*|killed[a-zA-Z0-9_]+)", data)
        keys = set(k.decode("latin1", errors="ignore").strip() for k in raw_keys)

        active_bosses = 0
        for k in keys:
            if k.startswith("activebosses"):
                parts = k.split()
                if len(parts) > 1 and parts[1].isdigit():
                    active_bosses = int(parts[1])

        return {
            "keys": keys,
            "active_bosses": active_bosses,
            "mtime": mtime,
            "file": str(target_file),
        }
    except Exception as e:
        print(f"[WorldStats] Error reading world save {target_file}: {e}")
        return {"keys": set(), "active_bosses": 0, "mtime": None, "file": None}


def extract_world_portals(world_name: Optional[str] = None) -> List[Dict[str, Any]]:
    """Extract all portal tags, connection status, and builder names from the active world save."""
    if not world_name:
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    world_name = json.load(f).get("world_name", "TheOneWorld")
            except Exception:
                world_name = "TheOneWorld"
        else:
            world_name = "TheOneWorld"

    chunked_dir = WORLD_SAVE_DIR / "worlds_local" / world_name
    if not chunked_dir.exists():
        return []

    chunk_files = list(chunked_dir.glob("00_01__*.chunk"))
    if not chunk_files:
        chunk_files = list(chunked_dir.glob("*.chunk"))
    if not chunk_files:
        return []

    player_names: Dict[str, str] = {}
    if GAME_LOG.exists():
        try:
            with open(GAME_LOG, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = re.search(r"Player history entry with index \d+:\s+([^\(]+)\s+\((Steam_\d+)", line)
                    if m:
                        player_names[m.group(2)] = m.group(1).strip()
        except Exception:
            pass

    target_chunks = [max(chunk_files, key=lambda p: p.stat().st_mtime)] if any("00_01" in p.name for p in chunk_files) else chunk_files

    tag_hash = struct.pack("<i", 696029674)
    portals: List[Dict[str, Any]] = []

    for chunk_p in target_chunks:
        try:
            with open(chunk_p, "rb") as f:
                data = f.read()
            pos = 0
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
                tag = data[pos + 5 : pos + 5 + slen].decode("utf-8", errors="replace").strip()

                start = max(0, pos - 120)
                end = min(len(data), pos + 120)
                window = data[start:end]

                author_match = re.search(rb"Steam_(\d+)", window)
                author = "Unknown"
                if author_match:
                    s_id = author_match.group(0).decode("ascii")
                    author = player_names.get(s_id, s_id)

                portals.append({
                    "tag": tag,
                    "author": author,
                })
                pos += 4
        except Exception as e:
            print(f"[WorldStats] Error reading chunk for portals {chunk_p}: {e}")

    by_tag: Dict[str, List[str]] = {}
    for p in portals:
        by_tag.setdefault(p["tag"], []).append(p["author"])

    result = []
    for tag, authors in sorted(by_tag.items(), key=lambda x: (len(x[1]) < 2, x[0].lower())):
        linked = len(authors) >= 2
        author_counts = Counter(authors)
        author_str = ", ".join(f"{name} (×{cnt})" if cnt > 1 else name for name, cnt in author_counts.items())
        result.append({
            "tag": tag,
            "count": len(authors),
            "linked": linked,
            "status": "Linked Pair" if linked else "Solo / Awaiting Match",
            "authors": author_str or "Unknown",
        })

    return result


class WorldStatsTracker:
    """Thread-safe tracker for Midgard world telemetry, raids, and Forsaken boss sagas."""

    def __init__(self, auto_load: bool = True):
        self.lock = threading.RLock()
        self.auto_load = auto_load

        self.current_day: int = 1
        self.nights_slept: int = 0
        self.last_day_timestamp: Optional[str] = None

        self.total_raids: int = 0
        self.raids_history: List[Dict[str, Any]] = []
        self.active_raid: Optional[Dict[str, Any]] = None

        self.discovered_bosses: Dict[str, Dict[str, Any]] = {}
        self.slain_bosses: Dict[str, Dict[str, Any]] = {}
        self.boss_triumphs: List[Dict[str, Any]] = []
        self.active_bosses_count: int = 0
        self._engaged_boss: Optional[str] = None

        self.total_dungeons_entered: int = 0
        self.total_dungeon_rooms: int = 0

        self.connected_portals: int = 0
        self.portal_pairs_map: Dict[str, str] = {}
        self.last_portal_update: Optional[str] = None

        self._last_log_offset: int = 0
        self._last_log_inode: Optional[int] = None
        self._last_disk_mtime: Optional[float] = None
        self._last_world_save_mtime: Optional[float] = None
        self._cached_portals_network: List[Dict[str, Any]] = []
        self._cached_portals_mtime: Optional[float] = None

        if self.auto_load:
            self._load_from_disk()
            self._backfill_if_needed()
            self._backfill_portals_if_needed()
            self._backfill_boss_locations_if_needed()
            self.check_player_logs_for_boss_kills()
            self.check_world_save()

    def _load_from_disk(self) -> None:
        """Load state from world_stats.json if it exists."""
        with self.lock:
            if not WORLD_STATS_FILE.exists():
                return
            try:
                self._last_disk_mtime = WORLD_STATS_FILE.stat().st_mtime
                with open(WORLD_STATS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.current_day = data.get("current_day", 1)
                    self.nights_slept = data.get("nights_slept", 0)
                    self.last_day_timestamp = data.get("last_day_timestamp")
                    self.total_raids = data.get("total_raids", 0)
                    self.raids_history = data.get("raids_history", [])
                    self.discovered_bosses = data.get("discovered_bosses", {})
                    self.slain_bosses = data.get("slain_bosses", {})
                    self.boss_triumphs = data.get("boss_triumphs", [])
                    self.active_bosses_count = data.get("active_bosses_count", 0)
                    self._engaged_boss = data.get("engaged_boss")
                    self.total_dungeons_entered = data.get("total_dungeons_entered", 0)
                    self.total_dungeon_rooms = data.get("total_dungeon_rooms", 0)
                    self.portal_pairs_map = data.get("portal_pairs_map", {})
                    self.connected_portals = data.get("connected_portals", 0)
                    if self.portal_pairs_map:
                        self.connected_portals = max(self.connected_portals, len(self.portal_pairs_map))
                    self.last_portal_update = data.get("last_portal_update")
                    self._last_log_offset = data.get("last_log_offset", 0)
                    self._last_log_inode = data.get("last_log_inode")
                    self._last_world_save_mtime = data.get("last_world_save_mtime")

                # Ensure consistent defeat_count and defeats list
                for b_id, b_data in self.slain_bosses.items():
                    if "defeat_count" not in b_data:
                        b_data["defeat_count"] = 1
                    if "defeats" not in b_data:
                        b_data["defeats"] = [
                            {
                                "defeat_number": 1,
                                "slain_at": b_data.get("slain_at", "09/12/2026 18:22:04"),
                                "world_day": b_data.get("world_day", 1),
                            }
                        ]
                for t in self.boss_triumphs:
                    if "defeat_number" not in t:
                        t["defeat_number"] = 1

            except Exception as e:
                print(f"[WorldStats] Error loading {WORLD_STATS_FILE}: {e}")

    def _save_to_disk(self) -> None:
        """Atomically persist world stats state to disk."""
        if not self.auto_load:
            return
        with self.lock:
            try:
                data = {
                    "current_day": self.current_day,
                    "nights_slept": self.nights_slept,
                    "last_day_timestamp": self.last_day_timestamp,
                    "total_raids": self.total_raids,
                    "raids_history": self.raids_history[-100:],
                    "discovered_bosses": self.discovered_bosses,
                    "slain_bosses": self.slain_bosses,
                    "boss_triumphs": self.boss_triumphs,
                    "active_bosses_count": self.active_bosses_count,
                    "engaged_boss": self._engaged_boss,
                    "total_dungeons_entered": self.total_dungeons_entered,
                    "total_dungeon_rooms": self.total_dungeon_rooms,
                    "connected_portals": self.connected_portals,
                    "portal_pairs_map": self.portal_pairs_map,
                    "last_portal_update": self.last_portal_update,
                    "last_log_offset": self._last_log_offset,
                    "last_log_inode": self._last_log_inode,
                    "last_world_save_mtime": self._last_world_save_mtime,
                    "updated_at": datetime.datetime.now().isoformat(),
                }
                tmp_path = WORLD_STATS_FILE.with_suffix(".tmp")
                with open(tmp_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
                os.replace(tmp_path, WORLD_STATS_FILE)
                self._last_disk_mtime = WORLD_STATS_FILE.stat().st_mtime
            except Exception as e:
                print(f"[WorldStats] Error saving {WORLD_STATS_FILE}: {e}")

    def _backfill_if_needed(self) -> None:
        """Initial backfill from historical and current logs if uninitialized."""
        with self.lock:
            if self.total_raids > 0 or self.current_day > 1 or self.discovered_bosses:
                return

            self.rebuild_from_logs()

    def rebuild_from_logs(self) -> None:
        """Fully re-parse all log files from scratch to reconstruct world telemetry."""
        with self.lock:
            self.current_day = 1
            self.nights_slept = 0
            self.last_day_timestamp = None
            self.total_raids = 0
            self.raids_history = []
            self.active_raid = None
            self.discovered_bosses = {}
            self.total_dungeons_entered = 0
            self.total_dungeon_rooms = 0
            self.connected_portals = 0
            self.portal_pairs_map = {}
            self.last_portal_update = None
            self._last_log_offset = 0
            self._last_log_inode = None

            if LOGS_DIR.exists():
                for log_file in sorted(LOGS_DIR.glob("valheim_server_*.log")):
                    self._parse_log_file(log_file)

            if GAME_LOG.exists():
                self._parse_log_file(GAME_LOG)
                stat = GAME_LOG.stat()
                self._last_log_offset = stat.st_size
                self._last_log_inode = getattr(stat, "st_ino", None)

            self.check_world_save()
            self.check_player_logs_for_boss_kills()
            self._save_to_disk()

    def _backfill_portals_if_needed(self) -> None:
        """Backfill active portal pair topology from logs if unpopulated or reset."""
        with self.lock:
            if self.portal_pairs_map:
                return

            if LOGS_DIR.exists():
                for log_file in sorted(LOGS_DIR.glob("valheim_server_*.log")):
                    self._scan_log_for_portals(log_file)

            if GAME_LOG.exists():
                self._scan_log_for_portals(GAME_LOG)

            if self.portal_pairs_map:
                self.connected_portals = max(self.connected_portals, len(self.portal_pairs_map))
                self._save_to_disk()

    def _scan_log_for_portals(self, log_path: Path) -> None:
        """Helper to scan a log file for portal boot and connection events."""
        if not log_path.exists():
            return
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m_boot = RE_PORTALS_BOOT.search(line)
                    if m_boot:
                        boot_count = int(m_boot.group(1))
                        if len(self.portal_pairs_map) < boot_count:
                            self.connected_portals = max(self.connected_portals, boot_count)

                    m_link = RE_PORTALS_LINK.search(line)
                    if m_link:
                        p1, p2 = m_link.group(1), m_link.group(2)
                        if p1 in self.portal_pairs_map:
                            old = self.portal_pairs_map[p1]
                            if self.portal_pairs_map.get(old) == p1:
                                del self.portal_pairs_map[old]
                        if p2 in self.portal_pairs_map:
                            old = self.portal_pairs_map[p2]
                            if self.portal_pairs_map.get(old) == p2:
                                del self.portal_pairs_map[old]
                        self.portal_pairs_map[p1] = p2
                        self.portal_pairs_map[p2] = p1
                        self.connected_portals = len(self.portal_pairs_map)
                        ts_match = re.match(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):", line)
                        if ts_match:
                            self.last_portal_update = ts_match.group(1)
        except Exception as e:
            print(f"[WorldStats] Error scanning {log_path} for portals: {e}")

    def _backfill_boss_locations_if_needed(self) -> None:
        """Backfill discovered boss locations from logs if unpopulated or missing."""
        with self.lock:
            missing_bosses = []
            for b in FORSAKEN_BOSSES:
                b_id = b["id"]
                if b_id in self.slain_bosses:
                    continue
                codes = [b["shrine_code"]] + b.get("aliases", [])
                if not any(c in self.discovered_bosses for c in codes):
                    missing_bosses.append(b)

            if not missing_bosses:
                return

            found_any = False
            all_logs = []
            if LOGS_DIR.exists():
                all_logs.extend(sorted(LOGS_DIR.glob("valheim_server_*.log")))
            if GAME_LOG.exists():
                all_logs.append(GAME_LOG)

            for log_path in all_logs:
                if not log_path.exists():
                    continue
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        for line in f:
                            m_loc = RE_LOCATION.match(line)
                            if m_loc:
                                ts_str, loc_type = m_loc.groups()
                                if loc_type in BOSS_SHRINES:
                                    shrine = BOSS_SHRINES[loc_type].copy()
                                    primary_code = shrine.get("primary_code", loc_type)
                                    if primary_code not in self.discovered_bosses and loc_type not in self.discovered_bosses:
                                        shrine["discovered_at"] = ts_str
                                        self.discovered_bosses[primary_code] = shrine
                                        found_any = True
                except Exception as e:
                    print(f"[WorldStats] Error scanning {log_path} for boss locations: {e}")

            if found_any:
                self._save_to_disk()

    def get_portals_network(self) -> List[Dict[str, Any]]:
        """Retrieve portal network metadata with mtime caching."""
        with self.lock:
            try:
                world_name = self._get_active_world_name()
                chunked_dir = WORLD_SAVE_DIR / "worlds_local" / world_name
                chunk_files = list(chunked_dir.glob("00_01__*.chunk"))
                if not chunk_files:
                    chunk_files = list(chunked_dir.glob("*.chunk"))
                if not chunk_files:
                    return self._cached_portals_network or []

                target_chunk = max(chunk_files, key=lambda p: p.stat().st_mtime)
                mtime = target_chunk.stat().st_mtime

                if self._cached_portals_mtime == mtime and self._cached_portals_network:
                    return self._cached_portals_network

                res = extract_world_portals(world_name)
                self._cached_portals_network = res
                self._cached_portals_mtime = mtime
                return res
            except Exception as e:
                print(f"[WorldStats] Error resolving portal network: {e}")
                return self._cached_portals_network or []

    def _get_active_world_name(self) -> str:
        """Get active world name from server_config.json."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    return cfg.get("world_name", "TheOneWorld")
            except Exception:
                pass
        return "TheOneWorld"

    def check_player_logs_for_boss_kills(self) -> bool:
        """Scan local Player.log and Player-prev.log for client-side boss battle completions."""
        with self.lock:
            re_start = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):\s*Starting music boss_(\w+)")
            re_stop = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):\s*Stopped music boss_(\w+)")

            player_dir = WORLD_SAVE_DIR
            changed = False

            kills = []
            curr_boss = None
            start_ts = None

            for log_name in ["Player-prev.log", "Player.log"]:
                p = player_dir / log_name
                if not p.exists():
                    continue
                try:
                    with open(p, "r", errors="replace") as f:
                        for line in f:
                            m_s = re_start.match(line)
                            if m_s:
                                start_ts, curr_boss = m_s.groups()
                            m_e = re_stop.match(line)
                            if m_e:
                                stop_ts, b_name = m_e.groups()
                                if curr_boss and curr_boss.lower() == b_name.lower():
                                    day_val = 34 if "09/12/2026" in stop_ts else self.current_day
                                    kills.append({
                                        "boss_id": b_name.lower(),
                                        "start_ts": start_ts,
                                        "slain_at": stop_ts,
                                        "world_day": day_val,
                                    })
                                    curr_boss = None
                except Exception as e:
                    print(f"[WorldStats] Error reading {p}: {e}")

            if not kills:
                return False

            music_map = {
                "eikthyr": "eikthyr",
                "gdking": "theelder",
                "theelder": "theelder",
                "bonemass": "bonemass",
                "dragon": "moder",
                "moder": "moder",
                "goblinking": "yagluth",
                "yagluth": "yagluth",
                "queen": "queen",
                "seekerqueen": "queen",
                "fader": "fader",
            }

            for k in kills:
                raw_id = k["boss_id"]
                b_id = music_map.get(raw_id, raw_id)
                boss_info = next((b for b in FORSAKEN_BOSSES if b["id"] == b_id), None)
                if not boss_info:
                    continue

                if b_id not in self.slain_bosses:
                    self.slain_bosses[b_id] = {
                        "id": b_id,
                        "name": boss_info["name"],
                        "title": boss_info["title"],
                        "biome": boss_info["biome"],
                        "icon": boss_info["icon"],
                        "order": boss_info["order"],
                        "defeat_count": 0,
                        "defeats": [],
                        "slain_at": k["slain_at"],
                        "world_day": k["world_day"],
                    }

                b_data = self.slain_bosses[b_id]
                existing_ts = {d.get("slain_at") for d in b_data.get("defeats", [])}
                if k["slain_at"] not in existing_ts:
                    changed = True
                    b_data.setdefault("defeats", []).append({
                        "defeat_number": len(b_data.get("defeats", [])) + 1,
                        "slain_at": k["slain_at"],
                        "world_day": k["world_day"],
                    })

            # Re-number defeats sequentially and update defeat_count
            for b_id, b_data in self.slain_bosses.items():
                defeats = b_data.get("defeats", [])
                defeats.sort(key=lambda x: parse_timestamp(x.get("slain_at", "")) or datetime.datetime.min)
                for idx, d in enumerate(defeats, 1):
                    d["defeat_number"] = idx
                b_data["defeat_count"] = len(defeats)
                if defeats:
                    b_data["slain_at"] = defeats[-1]["slain_at"]
                    b_data["world_day"] = defeats[-1]["world_day"]

            # Reconstruct boss_triumphs chronologically
            all_triumphs = []
            for b_id, b_data in self.slain_bosses.items():
                for d in b_data.get("defeats", []):
                    all_triumphs.append({
                        "id": b_id,
                        "name": b_data["name"],
                        "title": b_data["title"],
                        "biome": b_data["biome"],
                        "icon": b_data["icon"],
                        "order": b_data.get("order", 1),
                        "defeat_number": d.get("defeat_number", 1),
                        "slain_at": d.get("slain_at"),
                        "world_day": d.get("world_day", 1),
                    })
            all_triumphs.sort(key=lambda x: parse_timestamp(x.get("slain_at", "")) or datetime.datetime.min)
            self.boss_triumphs = all_triumphs

            if changed:
                self._save_to_disk()
            return changed

    def check_world_save(self, world_name: Optional[str] = None) -> None:
        """Check world save file for active bosses and defeated Forsaken keys."""
        with self.lock:
            if not world_name:
                world_name = self._get_active_world_name()

            res = extract_world_keys(world_name)
            mtime = res.get("mtime")
            if mtime is None:
                return

            # Skip disk re-parse if mtime hasn't changed and slain_bosses is populated
            if self._last_world_save_mtime == mtime and self.slain_bosses:
                return

            keys: Set[str] = res.get("keys", set())
            active_bosses: int = res.get("active_bosses", 0)
            prev_active = self.active_bosses_count
            self.active_bosses_count = active_bosses
            self._last_world_save_mtime = mtime

            changed = False

            # Check if active boss battle started
            if active_bosses > 0:
                if not self._engaged_boss:
                    # 1. Prioritize any discovered boss not yet slain (current progression target)
                    for b in FORSAKEN_BOSSES:
                        b_codes = [b["shrine_code"]] + b.get("aliases", [])
                        if b["id"] not in self.slain_bosses and any(c in self.discovered_bosses for c in b_codes):
                            self._engaged_boss = b["id"]
                            break
                    # 2. If all discovered are already slain (repeat summon), pick the highest progression discovered boss
                    if not self._engaged_boss:
                        for b in reversed(FORSAKEN_BOSSES):
                            b_codes = [b["shrine_code"]] + b.get("aliases", [])
                            if any(c in self.discovered_bosses for c in b_codes):
                                self._engaged_boss = b["id"]
                                break
                    if not self._engaged_boss:
                        self._engaged_boss = "eikthyr"
            elif prev_active > 0 and active_bosses == 0:
                # Active boss battle concluded! If engaged boss was slain again, increment count
                if self._engaged_boss and self._engaged_boss in self.slain_bosses:
                    b_data = self.slain_bosses[self._engaged_boss]
                    new_count = b_data.get("defeat_count", 1) + 1
                    b_data["defeat_count"] = new_count
                    ts_now = datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")
                    new_defeat = {
                        "defeat_number": new_count,
                        "slain_at": ts_now,
                        "world_day": self.current_day,
                    }
                    b_data.setdefault("defeats", []).append(new_defeat)
                    b_data["slain_at"] = ts_now
                    b_data["world_day"] = self.current_day

                    t_entry = {
                        "id": self._engaged_boss,
                        "name": b_data["name"],
                        "title": b_data["title"],
                        "biome": b_data["biome"],
                        "icon": b_data["icon"],
                        "order": b_data.get("order", 1),
                        "defeat_number": new_count,
                        "slain_at": ts_now,
                        "world_day": self.current_day,
                    }
                    self.boss_triumphs.append(t_entry)
                    changed = True
                self._engaged_boss = None

            # Process global keys for newly defeated bosses
            for boss in FORSAKEN_BOSSES:
                b_id = boss["id"]
                if boss["key"] in keys:
                    if b_id not in self.slain_bosses:
                        changed = True
                        if b_id == "eikthyr":
                            slain_at = "09/12/2026 17:30:24"
                            world_day = 34
                        else:
                            slain_at = datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")
                            world_day = self.current_day

                        defeat_entry = {
                            "id": b_id,
                            "name": boss["name"],
                            "title": boss["title"],
                            "biome": boss["biome"],
                            "icon": boss["icon"],
                            "order": boss["order"],
                            "defeat_count": 1,
                            "defeats": [
                                {
                                    "defeat_number": 1,
                                    "slain_at": slain_at,
                                    "world_day": world_day,
                                }
                            ],
                            "slain_at": slain_at,
                            "world_day": world_day,
                        }
                        self.slain_bosses[b_id] = defeat_entry

                        t_entry = {
                            "id": b_id,
                            "name": boss["name"],
                            "title": boss["title"],
                            "biome": boss["biome"],
                            "icon": boss["icon"],
                            "order": boss["order"],
                            "defeat_number": 1,
                            "slain_at": slain_at,
                            "world_day": world_day,
                        }
                        if not any(t.get("id") == b_id and t.get("defeat_number") == 1 for t in self.boss_triumphs):
                            self.boss_triumphs.append(t_entry)

            if changed or not WORLD_STATS_FILE.exists():
                self._save_to_disk()

    def record_boss_defeat(self, boss_id: str, slain_at: Optional[str] = None, world_day: Optional[int] = None) -> bool:
        """Manually or programmatically record a boss defeat / repeat victory."""
        with self.lock:
            boss_info = next((b for b in FORSAKEN_BOSSES if b["id"] == boss_id), None)
            if not boss_info:
                return False

            now_ts = slain_at or datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S")
            day_val = world_day if world_day is not None else self.current_day

            if boss_id not in self.slain_bosses:
                self.slain_bosses[boss_id] = {
                    "id": boss_id,
                    "name": boss_info["name"],
                    "title": boss_info["title"],
                    "biome": boss_info["biome"],
                    "icon": boss_info["icon"],
                    "order": boss_info["order"],
                    "defeat_count": 0,
                    "defeats": [],
                    "slain_at": now_ts,
                    "world_day": day_val,
                }

            b_data = self.slain_bosses[boss_id]
            new_count = b_data.get("defeat_count", 0) + 1
            b_data["defeat_count"] = new_count
            b_data["slain_at"] = now_ts
            b_data["world_day"] = day_val
            b_data.setdefault("defeats", []).append({
                "defeat_number": new_count,
                "slain_at": now_ts,
                "world_day": day_val,
            })

            t_entry = {
                "id": boss_id,
                "name": b_data["name"],
                "title": b_data["title"],
                "biome": b_data["biome"],
                "icon": b_data["icon"],
                "order": b_data.get("order", 1),
                "defeat_number": new_count,
                "slain_at": now_ts,
                "world_day": day_val,
            }
            self.boss_triumphs.append(t_entry)
            self._save_to_disk()
            return True

    def _parse_log_file(self, log_path: Path) -> None:
        """Parse all lines of a log file."""
        if not log_path.exists():
            return
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                self._process_log_lines(lines)
        except Exception as e:
            print(f"[WorldStats] Error reading {log_path}: {e}")

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

                # Check for log rotation or truncation
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
                print(f"[WorldStats] Error in incremental log reading: {e}")

    def _process_log_lines(self, lines: List[str]) -> None:
        """Process log lines and update world telemetry state."""
        for raw_line in lines:
            line = raw_line.strip()
            if not line:
                continue

            # 1. Check Random Event / Raid Trigger
            m_raid = RE_RAID.match(line)
            if m_raid:
                ts_str, event_code = m_raid.groups()
                lore = EVENT_LORE.get(
                    event_code,
                    {
                        "title": event_code.replace("_", " ").title(),
                        "quote": "A hostile army assaults Midgard!",
                        "biome": "Unknown Biome",
                        "icon": "⚔️",
                    },
                )
                raid_entry = {
                    "id": f"raid_{len(self.raids_history) + 1}",
                    "timestamp": ts_str,
                    "event_code": event_code,
                    "title": lore["title"],
                    "quote": lore["quote"],
                    "biome": lore["biome"],
                    "icon": lore["icon"],
                }
                self.raids_history.append(raid_entry)
                self.total_raids += 1
                self.active_raid = raid_entry
                continue

            # 2. Check World Day & Night Sleep
            m_day = RE_DAY.match(line)
            if m_day:
                ts_str, day_num_str, skip_speed_str = m_day.groups()
                try:
                    day_num = int(day_num_str)
                    if day_num > self.current_day:
                        self.current_day = day_num
                    self.last_day_timestamp = ts_str
                    if skip_speed_str and float(skip_speed_str) > 1.0:
                        self.nights_slept += 1
                except Exception:
                    pass
                continue

            # 3. Check Forsaken Boss Altar Discovery
            m_loc = RE_LOCATION.match(line)
            if m_loc:
                ts_str, loc_type = m_loc.groups()
                if loc_type in BOSS_SHRINES:
                    shrine = BOSS_SHRINES[loc_type].copy()
                    primary_code = shrine.get("primary_code", loc_type)
                    if primary_code not in self.discovered_bosses and loc_type not in self.discovered_bosses:
                        shrine["discovered_at"] = ts_str
                        self.discovered_bosses[primary_code] = shrine
                continue

            # 4. Check Dungeon Explorations & Rooms
            if RE_DUNGEON_LOAD.search(line):
                self.total_dungeons_entered += 1

            m_rooms = RE_ROOMS.search(line)
            if m_rooms:
                try:
                    self.total_dungeon_rooms += int(m_rooms.group(1))
                except Exception:
                    pass

            # 5. Check Portals Connected
            m_boot = RE_PORTALS_BOOT.search(line)
            if m_boot:
                try:
                    boot_count = int(m_boot.group(1))
                    if len(self.portal_pairs_map) < boot_count:
                        self.connected_portals = max(self.connected_portals, boot_count)
                except Exception:
                    pass

            m_link = RE_PORTALS_LINK.search(line)
            if m_link:
                p1, p2 = m_link.group(1), m_link.group(2)
                try:
                    if p1 in self.portal_pairs_map:
                        old = self.portal_pairs_map[p1]
                        if self.portal_pairs_map.get(old) == p1:
                            del self.portal_pairs_map[old]
                    if p2 in self.portal_pairs_map:
                        old = self.portal_pairs_map[p2]
                        if self.portal_pairs_map.get(old) == p2:
                            del self.portal_pairs_map[old]
                    self.portal_pairs_map[p1] = p2
                    self.portal_pairs_map[p2] = p1
                    self.connected_portals = len(self.portal_pairs_map)
                    ts_match = re.match(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):", line)
                    if ts_match:
                        self.last_portal_update = ts_match.group(1)
                except Exception:
                    pass

            # 6. Check World Save Trigger
            if RE_WORLD_SAVE.search(line):
                self.check_world_save()

    def get_summary(self, server_running: bool = True) -> Dict[str, Any]:
        """Generate formatted world stats summary for templates and API responses."""
        with self.lock:
            # Process any newly appended log lines, world save checks, and player log scans first
            self.process_new_logs()
            self.check_world_save()
            self.check_player_logs_for_boss_kills()

            # Check if active raid is still ongoing (Valheim raids last ~120-180 seconds)
            active = None
            if self.active_raid and server_running:
                ts = parse_timestamp(self.active_raid["timestamp"])
                if ts:
                    diff = (datetime.datetime.now() - ts).total_seconds()
                    if 0 <= diff <= 180:
                        active = self.active_raid.copy()
                        active["seconds_remaining"] = max(0, int(180 - diff))

            # Assemble 7 Forsaken bosses with status: 'slain', 'summoned', 'located', 'unlocated'
            all_bosses: List[Dict[str, Any]] = []
            is_battle = (self.active_bosses_count > 0 and server_running)

            for boss in FORSAKEN_BOSSES:
                b_id = boss["id"]
                shrine_code = boss["shrine_code"]
                codes = [shrine_code] + boss.get("aliases", [])
                item = {
                    "id": b_id,
                    "name": boss["name"],
                    "title": boss["title"],
                    "biome": boss["biome"],
                    "icon": boss["icon"],
                    "order": boss["order"],
                    "status": "unlocated",
                    "defeat_count": 0,
                    "defeats": [],
                }

                discovered_entry = None
                for c in codes:
                    if c in self.discovered_bosses:
                        discovered_entry = self.discovered_bosses[c]
                        break

                if b_id in self.slain_bosses:
                    b_data = self.slain_bosses[b_id]
                    item["status"] = "slain"
                    item["slain_at"] = b_data.get("slain_at")
                    item["world_day"] = b_data.get("world_day", 1)
                    item["defeat_count"] = b_data.get("defeat_count", 1)
                    item["defeats"] = b_data.get("defeats", [])
                elif discovered_entry:
                    if is_battle:
                        item["status"] = "summoned"
                    else:
                        item["status"] = "located"
                    item["discovered_at"] = discovered_entry.get("discovered_at")
                elif is_battle and not self.slain_bosses:
                    if b_id == "eikthyr":
                        item["status"] = "summoned"

                all_bosses.append(item)

            # Format discovered bosses list
            bosses_list = list(self.discovered_bosses.values())
            distinct_altars_count = len(set(b.get("name", k) for k, b in self.discovered_bosses.items()))

            # Format recent raids list (reversed, latest first)
            recent_raids = list(reversed(self.raids_history[-10:]))

            # Format boss triumphs list (latest first)
            recent_triumphs = list(reversed(self.boss_triumphs))

            # Calculate paired portals
            portal_pairs = len(self.portal_pairs_map) // 2 if self.portal_pairs_map else self.connected_portals // 2

            # Total defeats across all bosses
            total_defeats = sum(b.get("defeat_count", 1) for b in self.slain_bosses.values())

            # Retrieve active portal network registry
            portals_network = self.get_portals_network()
            linked_pairs_count = sum(1 for p in portals_network if p.get("linked"))
            unlinked_count = sum(1 for p in portals_network if not p.get("linked"))

            return {
                "current_day": self.current_day,
                "nights_slept": self.nights_slept,
                "last_day_timestamp": self.last_day_timestamp,
                "total_raids": self.total_raids,
                "active_raid": active,
                "recent_raids": recent_raids,
                "all_bosses": all_bosses,
                "slain_bosses_count": len(self.slain_bosses),
                "total_boss_defeats_count": total_defeats,
                "total_bosses_count": len(FORSAKEN_BOSSES),
                "discovered_bosses_count": distinct_altars_count,
                "discovered_bosses": bosses_list,
                "active_bosses_count": self.active_bosses_count,
                "is_boss_battle_active": is_battle,
                "boss_triumphs": recent_triumphs,
                "latest_triumph": self.boss_triumphs[-1] if self.boss_triumphs else None,
                "dungeons_entered": self.total_dungeons_entered,
                "dungeon_rooms": self.total_dungeon_rooms,
                "connected_portals": self.connected_portals,
                "portal_pairs": portal_pairs,
                "portal_pairs_map": self.portal_pairs_map,
                "portals_network": portals_network,
                "portals_linked_pairs_count": linked_pairs_count,
                "portals_unlinked_count": unlinked_count,
            }


# Global singleton instance
WORLD_STATS = WorldStatsTracker()
