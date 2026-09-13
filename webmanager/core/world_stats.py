import datetime
import json
import os
import re
import threading
import time
import zlib
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
        "name": "Bonemass",
        "title": "Gargantuan Swamp Ooze",
        "biome": "Swamp",
        "icon": "💀",
        "order": 3,
    },
    {
        "id": "moder",
        "key": "defeated_dragon",
        "shrine_code": "Dragon",
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
        "name": "Fader",
        "title": "Lord of the Ashlands",
        "biome": "Ashlands",
        "icon": "⚔️",
        "order": 7,
    },
]

# Quick lookup by shrine code
BOSS_SHRINES: Dict[str, Dict[str, str]] = {
    b["shrine_code"]: {
        "name": b["name"],
        "title": b["title"],
        "biome": b["biome"],
        "icon": b["icon"],
    }
    for b in FORSAKEN_BOSSES
}

# Regex parsers for Valheim dedicated server log lines
RE_RAID = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):\s*Random event set:(\w+)")
RE_DAY = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):\s*Time\s+[\d\.]+,\s+day:(\d+)(?:.*?skipspeed:([\d\.]+))?")
RE_LOCATION = re.compile(r"^(\d{2}/\d{2}/\d{4} \d{2}:\d{2}:\d{2}):\s*Found location of type\s+(\w+)")
RE_DUNGEON_LOAD = re.compile(r"Dungeon loaded with")
RE_ROOMS = re.compile(r"Placed\s+(\d+)\s+rooms")
RE_PORTALS = re.compile(r"ConnectPortals => Connected\s+(\d+)\s+portals|\[\s*Connected\s+(\d+)\s+portals\s*\]")
RE_WORLD_SAVE = re.compile(r"World save \(\d/\d\)|### Save World Thread Started|SaveSystem\.Reload|World auto backup saved")


def parse_timestamp(ts_str: str) -> Optional[datetime.datetime]:
    """Parse MM/DD/YYYY HH:MM:SS string to datetime."""
    try:
        return datetime.datetime.strptime(ts_str.strip(), "%m/%d/%Y %H:%M:%S")
    except Exception:
        return None


def extract_world_keys(world_name: str) -> Dict[str, Any]:
    """Extract global keys and activebosses count from Valheim world save (.db2 or .db)."""
    # 1. Look for chunked format: worlds_local/<world_name>/_main.*.db2
    chunked_dir = WORLD_SAVE_DIR / "worlds_local" / world_name
    db2_files = list(chunked_dir.glob("_main.*.db2"))
    target_file: Optional[Path] = None
    is_chunked = False
    if db2_files:
        target_file = max(db2_files, key=lambda p: p.stat().st_mtime)
        is_chunked = True
    else:
        # 2. Look for legacy .db
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

        self.total_dungeons_entered: int = 0
        self.total_dungeon_rooms: int = 0

        self.connected_portals: int = 0
        self.last_portal_update: Optional[str] = None

        self._last_log_offset: int = 0
        self._last_log_inode: Optional[int] = None
        self._last_disk_mtime: Optional[float] = None
        self._last_world_save_mtime: Optional[float] = None

        if self.auto_load:
            self._load_from_disk()
            self._backfill_if_needed()
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
                    self.total_dungeons_entered = data.get("total_dungeons_entered", 0)
                    self.total_dungeon_rooms = data.get("total_dungeon_rooms", 0)
                    self.connected_portals = data.get("connected_portals", 0)
                    self.last_portal_update = data.get("last_portal_update")
                    self._last_log_offset = data.get("last_log_offset", 0)
                    self._last_log_inode = data.get("last_log_inode")
                    self._last_world_save_mtime = data.get("last_world_save_mtime")
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
                    "raids_history": self.raids_history[-100:],  # Store up to last 100 raids
                    "discovered_bosses": self.discovered_bosses,
                    "slain_bosses": self.slain_bosses,
                    "boss_triumphs": self.boss_triumphs,
                    "active_bosses_count": self.active_bosses_count,
                    "total_dungeons_entered": self.total_dungeons_entered,
                    "total_dungeon_rooms": self.total_dungeon_rooms,
                    "connected_portals": self.connected_portals,
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
            self._save_to_disk()

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
            self.active_bosses_count = active_bosses
            self._last_world_save_mtime = mtime

            changed = False
            for boss in FORSAKEN_BOSSES:
                b_id = boss["id"]
                if boss["key"] in keys:
                    if b_id not in self.slain_bosses:
                        changed = True
                        if b_id == "eikthyr":
                            # Historical Eikthyr defeat on Day 36
                            defeat_entry = {
                                "id": b_id,
                                "name": boss["name"],
                                "title": boss["title"],
                                "biome": boss["biome"],
                                "icon": boss["icon"],
                                "order": boss["order"],
                                "slain_at": "09/12/2026 18:22:04",
                                "world_day": 36,
                            }
                        else:
                            defeat_entry = {
                                "id": b_id,
                                "name": boss["name"],
                                "title": boss["title"],
                                "biome": boss["biome"],
                                "icon": boss["icon"],
                                "order": boss["order"],
                                "slain_at": datetime.datetime.now().strftime("%m/%d/%Y %H:%M:%S"),
                                "world_day": self.current_day,
                            }
                        self.slain_bosses[b_id] = defeat_entry
                        if not any(t.get("id") == b_id for t in self.boss_triumphs):
                            self.boss_triumphs.append(defeat_entry)

            if changed or not WORLD_STATS_FILE.exists():
                self._save_to_disk()

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
                if loc_type in BOSS_SHRINES and loc_type not in self.discovered_bosses:
                    shrine = BOSS_SHRINES[loc_type].copy()
                    shrine["discovered_at"] = ts_str
                    self.discovered_bosses[loc_type] = shrine
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
            m_port = RE_PORTALS.search(line)
            if m_port:
                p_str = m_port.group(1) or m_port.group(2)
                try:
                    self.connected_portals = int(p_str)
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
            # Process any newly appended log lines and world save checks first
            self.process_new_logs()
            self.check_world_save()

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
                item = {
                    "id": b_id,
                    "name": boss["name"],
                    "title": boss["title"],
                    "biome": boss["biome"],
                    "icon": boss["icon"],
                    "order": boss["order"],
                    "status": "unlocated",
                }

                if b_id in self.slain_bosses:
                    item["status"] = "slain"
                    item["slain_at"] = self.slain_bosses[b_id].get("slain_at")
                    item["world_day"] = self.slain_bosses[b_id].get("world_day", 1)
                elif shrine_code in self.discovered_bosses:
                    if is_battle:
                        item["status"] = "summoned"
                    else:
                        item["status"] = "located"
                    item["discovered_at"] = self.discovered_bosses[shrine_code].get("discovered_at")
                elif is_battle and not self.slain_bosses:
                    # If active boss battle and no bosses slain yet, Eikthyr is being summoned
                    if b_id == "eikthyr":
                        item["status"] = "summoned"

                all_bosses.append(item)

            # Format discovered bosses list
            bosses_list = list(self.discovered_bosses.values())

            # Format recent raids list (reversed, latest first)
            recent_raids = list(reversed(self.raids_history[-10:]))

            # Format boss triumphs list (latest first)
            recent_triumphs = list(reversed(self.boss_triumphs))

            # Calculate paired portals
            portal_pairs = self.connected_portals // 2

            return {
                "current_day": self.current_day,
                "nights_slept": self.nights_slept,
                "last_day_timestamp": self.last_day_timestamp,
                "total_raids": self.total_raids,
                "active_raid": active,
                "recent_raids": recent_raids,
                "all_bosses": all_bosses,
                "slain_bosses_count": len(self.slain_bosses),
                "total_bosses_count": len(FORSAKEN_BOSSES),
                "discovered_bosses_count": len(self.discovered_bosses),
                "discovered_bosses": bosses_list,
                "active_bosses_count": self.active_bosses_count,
                "is_boss_battle_active": is_battle,
                "boss_triumphs": recent_triumphs,
                "latest_triumph": self.boss_triumphs[-1] if self.boss_triumphs else None,
                "dungeons_entered": self.total_dungeons_entered,
                "dungeon_rooms": self.total_dungeon_rooms,
                "connected_portals": self.connected_portals,
                "portal_pairs": portal_pairs,
            }


# Global singleton instance
WORLD_STATS = WorldStatsTracker()
