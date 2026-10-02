import datetime
import json
import os
import random
import re
import shutil
import string
import struct
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.paths import (
    BACKUP_DIR,
    CONFIG_FILE,
    GAME_LOG,
    LOGS_DIR,
    SERVER_DIR,
    WORLD_SAVE_DIR,
)


def dotnet_fw_hash(s: str) -> int:
    """Calculate 32-bit string hash code matching .NET Framework / Unity string.GetHashCode()."""
    hash1 = 5381
    hash2 = hash1
    chars = [ord(c) for c in s]
    if len(chars) % 2 != 0:
        chars.append(0)
    for i in range(0, len(chars), 2):
        hash1 = (((hash1 << 5) + hash1) ^ chars[i]) & 0xFFFFFFFF
        if i + 1 < len(chars):
            hash2 = (((hash2 << 5) + hash2) ^ chars[i + 1]) & 0xFFFFFFFF
    return (hash1 + (hash2 * 1566083941)) & 0xFFFFFFFF


def generate_random_seed(length: int = 10) -> str:
    """Generate a random alphanumeric Viking world seed."""
    chars = string.ascii_letters + string.digits
    return "".join(random.choice(chars) for _ in range(length))


def get_world_seed(world_name: str) -> Optional[str]:
    """Extract the seed string from a world's .fwl file if it exists."""
    for subdir in ["worlds_local", "worlds"]:
        fwl_file = WORLD_SAVE_DIR / subdir / f"{world_name}.fwl"
        if fwl_file.exists():
            try:
                with open(fwl_file, "rb") as f:
                    data = f.read()
                if len(data) < 12:
                    continue
                ver, = struct.unpack_from("<i", data, 0)
                off = 4
                if ver >= 26:
                    off += 4

                def _read_str(offset: int) -> Tuple[str, int]:
                    length = 0
                    shift = 0
                    while True:
                        b = data[offset]
                        offset += 1
                        length |= (b & 0x7F) << shift
                        if (b & 0x80) == 0:
                            break
                        shift += 7
                    val = data[offset:offset + length].decode("utf-8", errors="replace")
                    return val, offset + length

                _, off = _read_str(off)  # world name
                seed_name, _ = _read_str(off)  # seed string
                return seed_name
            except Exception:
                pass
    return None


def create_world_metadata(world_name: str, seed_name: str) -> Tuple[bool, str]:
    """
    Generate a Valheim world metadata (.fwl) file with the specified custom seed.
    Valheim Dedicated Server will automatically generate the corresponding world (.db)
    from this seed upon server startup.
    """
    clean_name = re.sub(r"[^a-zA-Z0-9_\- ]", "", world_name.strip())
    if not clean_name:
        return False, "Invalid world name. Use alphanumeric characters, spaces, dashes, or underscores."

    clean_seed = seed_name.strip()
    if not clean_seed:
        clean_seed = generate_random_seed()

    # Check if world already exists
    for subdir in ["worlds_local", "worlds"]:
        target_fwl = WORLD_SAVE_DIR / subdir / f"{clean_name}.fwl"
        target_db = WORLD_SAVE_DIR / subdir / f"{clean_name}.db"
        if target_fwl.exists() or target_db.exists():
            return False, f"A world named '{clean_name}' already exists."

    try:
        seed_hash = dotnet_fw_hash(clean_seed)
        seed_int_signed = seed_hash - 0x100000000 if seed_hash >= 0x80000000 else seed_hash
        uid = random.randint(1000000000, 9000000000)

        def _encode_str(s: str) -> bytes:
            s_bytes = s.encode("utf-8")
            length = len(s_bytes)
            len_bytes = bytearray()
            while length >= 0x80:
                len_bytes.append((length | 0x80) & 0xFF)
                length >>= 7
            len_bytes.append(length & 0xFF)
            return bytes(len_bytes) + s_bytes

        header = struct.pack("<ii", 48, 37)
        name_bytes = _encode_str(clean_name)
        seed_bytes = _encode_str(clean_seed)
        tail = struct.pack("<iqiib", seed_int_signed, uid, 2, 1, 0)

        fwl_data = header + name_bytes + seed_bytes + tail

        dest_dir = WORLD_SAVE_DIR / "worlds_local"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_file = dest_dir / f"{clean_name}.fwl"

        with open(dest_file, "wb") as f:
            f.write(fwl_data)

        return True, f"World metadata created for '{clean_name}' with seed '{clean_seed}'."
    except Exception as e:
        return False, f"Failed to create world metadata: {e}"


def get_active_world_folder(world_name: str) -> str:
    """Find whether world_name is in worlds_local or worlds, defaulting to worlds_local."""
    for folder in ["worlds_local", "worlds"]:
        wdir = WORLD_SAVE_DIR / folder
        if (wdir / f"{world_name}.fwl").exists() or (wdir / f"{world_name}.db").exists():
            return folder
    if (WORLD_SAVE_DIR / "worlds_local").exists():
        return "worlds_local"
    return "worlds"


def list_available_worlds() -> List[str]:
    """Scan world save directories and return unique, clean primary world names."""
    worlds = set()
    for subdir in ["worlds_local", "worlds"]:
        target_dir = WORLD_SAVE_DIR / subdir
        if target_dir.exists():
            for f in target_dir.glob("*.fwl"):
                stem = f.stem
                if not any(k in stem for k in ["_backup_", ".old", ".dang"]):
                    worlds.add(stem)
            for f in target_dir.glob("*.db"):
                stem = f.stem
                if not any(k in stem for k in ["_backup_", ".old", ".dang"]):
                    worlds.add(stem)
    return sorted(list(worlds)) or ["Dedicated"]


def list_backups() -> List[Dict[str, Any]]:
    """List existing backup zip files with size, formatted timestamp, and snapshot type."""
    if not BACKUP_DIR.exists():
        return []

    backups = []
    for f in sorted(BACKUP_DIR.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
        stat = f.stat()
        is_safety = "safety_pre_restore" in f.name
        is_v2 = False
        try:
            with zipfile.ZipFile(f, "r") as zf:
                is_v2 = "manifest.json" in zf.namelist()
        except Exception:
            pass
        backups.append({
            "filename": f.name,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "created": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "mtime": stat.st_mtime,
            "is_safety": is_safety,
            "is_v2": is_v2,
        })
    return backups


def get_backup_path(filename: str) -> Optional[Path]:
    """Safely resolve a backup file path, preventing directory traversal."""
    safe_name = Path(filename).name
    target = BACKUP_DIR / safe_name
    if target.exists() and target.is_file() and target.suffix == ".zip":
        return target
    return None


def inspect_backup(filename: str) -> Optional[Dict[str, Any]]:
    """Inspect backup archive and return detailed manifest and component flags."""
    target = get_backup_path(filename)
    if not target:
        return None
    try:
        with zipfile.ZipFile(target, "r") as zf:
            namelist = zf.namelist()
            is_v2 = "manifest.json" in namelist
            manifest = None
            if is_v2:
                try:
                    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                except Exception:
                    pass

            has_world = any(n.startswith("world/") or n.startswith("worlds_local/") or n.startswith("worlds/") for n in namelist)
            has_server_configs = any(n.startswith("configs/server/") for n in namelist)
            has_data = any(n.startswith("data/") for n in namelist)
            has_logs = any(n.startswith("logs/") for n in namelist)

            return {
                "filename": filename,
                "size_mb": round(target.stat().st_size / (1024 * 1024), 2),
                "is_v2": is_v2,
                "manifest": manifest,
                "has_world": has_world,
                "has_server_configs": has_server_configs,
                "has_data": has_data,
                "has_logs": has_logs,
                "file_count": len([n for n in namelist if not n.endswith("/")]),
            }
    except Exception:
        return None


def create_backup(
    world_name: str = "world",
    is_safety_snapshot: bool = False,
) -> Tuple[bool, str, Optional[str]]:
    """
    Create a compressed zip backup of:
    1. Active world directory ONLY (worlds_local OR worlds, not both).
    2. Server configs outside the worlds folder (adminlist.txt, bannedlist.txt, permittedlist.txt, prefs, etc.).
    3. Web manager configurations and state data (*.json in SERVER_DIR).
    4. Server and game logs (valheim_server.log, logs/*.log, Player.log).
    """
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    if is_safety_snapshot:
        zip_name = f"valheim_safety_pre_restore_{world_name}_{timestamp}.zip"
    else:
        zip_name = f"valheim_backup_{world_name}_{timestamp}.zip"
    zip_path = BACKUP_DIR / zip_name

    active_folder = get_active_world_folder(world_name)
    world_dir = WORLD_SAVE_DIR / active_folder

    # Identify all top-level files/directories that belong to active world
    active_world_root_items = []
    if world_dir.exists():
        for item in world_dir.iterdir():
            if (
                item.name == world_name
                or item.name.startswith(f"{world_name}.")
                or item.name.startswith(f"{world_name}_")
            ):
                active_world_root_items.append(item.name)
    active_world_root_items.sort()

    file_summary = {
        "world_files": 0,
        "server_configs": 0,
        "manager_data": 0,
        "logs": 0,
    }
    active_world_file_count = 0

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # 1. Active world folder ONLY (so worlds_local or worlds, not both)
            if world_dir.exists():
                for root, _, files in os.walk(world_dir):
                    for file in files:
                        fpath = Path(root) / file
                        rel_path = fpath.relative_to(world_dir)
                        arcname = f"world/{active_folder}/{rel_path}"
                        zf.write(fpath, arcname)
                        file_summary["world_files"] += 1

                        first_part = rel_path.parts[0] if rel_path.parts else ""
                        if (
                            first_part == world_name
                            or first_part.startswith(f"{world_name}.")
                            or first_part.startswith(f"{world_name}_")
                        ):
                            active_world_file_count += 1

            # 2. Server config files in WORLD_SAVE_DIR outside world directories
            server_config_filenames = [
                "adminlist.txt",
                "bannedlist.txt",
                "permittedlist.txt",
                "ps_blocked_players.txt",
                "blocked_players.txt",
                "prefs",
            ]
            for cname in server_config_filenames:
                cpath = WORLD_SAVE_DIR / cname
                if cpath.exists() and cpath.is_file():
                    zf.write(cpath, f"configs/server/{cname}")
                    file_summary["server_configs"] += 1

            # 3. Web manager configs and state data (.json files in SERVER_DIR)
            excluded_json = {"pyrightconfig.json", "server_config.example.json"}
            for jfile in SERVER_DIR.glob("*.json"):
                if jfile.name not in excluded_json and jfile.is_file():
                    zf.write(jfile, f"data/{jfile.name}")
                    file_summary["manager_data"] += 1

            # 4. Logs (valheim_server.log, logs/*.log, Player.log)
            if GAME_LOG.exists() and GAME_LOG.is_file():
                zf.write(GAME_LOG, "logs/server/valheim_server.log")
                file_summary["logs"] += 1

            if LOGS_DIR.exists():
                for lfile in LOGS_DIR.glob("*.log"):
                    if lfile.is_file():
                        zf.write(lfile, f"logs/history/{lfile.name}")
                        file_summary["logs"] += 1

            for plog_name in ["Player.log", "Player-prev.log"]:
                plog_path = WORLD_SAVE_DIR / plog_name
                if plog_path.exists() and plog_path.is_file():
                    zf.write(plog_path, f"logs/unity/{plog_name}")
                    file_summary["logs"] += 1

            # 5. Manifest metadata
            manifest = {
                "version": 2,
                "timestamp": timestamp,
                "world_name": world_name,
                "active_folder": active_folder,
                "active_world": {
                    "name": world_name,
                    "folder": active_folder,
                    "root_items": active_world_root_items,
                    "file_count": active_world_file_count,
                },
                "summary": file_summary,
            }
            zf.writestr("manifest.json", json.dumps(manifest, indent=2))

        size_mb = round(zip_path.stat().st_size / (1024 * 1024), 2)
        total_files = sum(file_summary.values())
        return (
            True,
            f"Backup created: {zip_name} ({size_mb} MB, {total_files} files in {active_folder}, configs, data & logs)",
            zip_name,
        )
    except Exception as e:
        if zip_path.exists():
            zip_path.unlink()
        return False, f"Backup failed: {e}", None


def restore_backup(
    filename: str,
    restore_world: bool = True,
    restore_configs: bool = True,
    restore_data: bool = True,
    restore_logs: bool = False,
    prune_active_world: bool = True,
    current_world_name: str = "world",
) -> Tuple[bool, str]:
    """
    Safely restore a backup archive to original locations with a safety snapshot taken first.
    Supports selective restoration of world files, server configs, web manager data, and logs.
    If prune_active_world is True, purges stale/newer chunks and files for the active world
    prior to extraction according to the manifest, leaving all other worlds untouched.
    """
    zip_path = get_backup_path(filename)
    if not zip_path:
        return False, f"Backup archive '{filename}' not found."

    try:
        # 1. Create safety snapshot of current state before extraction
        ok_snap, snap_msg, snap_name = create_backup(
            world_name=current_world_name,
            is_safety_snapshot=True,
        )
        if not ok_snap:
            return False, f"Pre-restore safety snapshot failed: {snap_msg}"

        restored_items = []

        with zipfile.ZipFile(zip_path, "r") as zf:
            namelist = zf.namelist()
            is_v2 = "manifest.json" in namelist
            manifest = None
            if is_v2:
                try:
                    manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
                except Exception:
                    pass

            # Zip Slip Security Verification
            for member in namelist:
                if member == "manifest.json":
                    continue
                if member.startswith("data/") or member.startswith("logs/server/"):
                    base_check = SERVER_DIR
                elif member.startswith("logs/history/"):
                    base_check = LOGS_DIR
                else:
                    base_check = WORLD_SAVE_DIR

                sub = member
                for prefix in ["world/", "configs/server/", "data/", "logs/server/", "logs/history/", "logs/unity/"]:
                    if member.startswith(prefix):
                        sub = member[len(prefix):]
                        break

                dest = (base_check / sub).resolve()
                if not str(dest).startswith(str(base_check.resolve())):
                    raise ValueError(f"Illegal path in archive: {member}")

            if is_v2:
                # 2A. V2 Archive extraction by selected components
                # A. World files
                if restore_world:
                    # Clean Purge for active world only (if requested)
                    if prune_active_world:
                        target_world = None
                        target_folder = "worlds_local"
                        manifest_root_items = []
                        if manifest and "active_world" in manifest:
                            target_world = manifest["active_world"].get("name")
                            target_folder = manifest["active_world"].get("folder", "worlds_local")
                            manifest_root_items = manifest["active_world"].get("root_items", [])
                        elif manifest and "world_name" in manifest:
                            target_world = manifest.get("world_name")
                            target_folder = manifest.get("active_folder", "worlds_local")
                        elif current_world_name:
                            target_world = current_world_name

                        if target_world:
                            purge_dir = WORLD_SAVE_DIR / target_folder
                            purged_count = 0
                            if purge_dir.exists():
                                for item in list(purge_dir.iterdir()):
                                    matches = False
                                    if manifest_root_items and item.name in manifest_root_items:
                                        matches = True
                                    elif (
                                        item.name == target_world
                                        or item.name.startswith(f"{target_world}.")
                                        or item.name.startswith(f"{target_world}_")
                                    ):
                                        matches = True

                                    if matches:
                                        try:
                                            if item.is_dir():
                                                shutil.rmtree(item)
                                            else:
                                                item.unlink()
                                            purged_count += 1
                                        except Exception as pe:
                                            print(f"[Backup] Warning: could not purge '{item.name}': {pe}")
                            if purged_count > 0:
                                restored_items.append(f"Purged {purged_count} old/orphaned items for '{target_world}'")

                    # Extract world files
                    world_count = 0
                    for m in namelist:
                        if m.startswith("world/") and not m.endswith("/"):
                            rel = m[len("world/"):]
                            dest = WORLD_SAVE_DIR / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(m) as src, open(dest, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            world_count += 1
                    if world_count > 0:
                        restored_items.append(f"World Files ({world_count})")

                # B. Server configs outside worlds folder
                if restore_configs:
                    cfg_count = 0
                    for m in namelist:
                        if m.startswith("configs/server/") and not m.endswith("/"):
                            rel = m[len("configs/server/"):]
                            dest = WORLD_SAVE_DIR / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(m) as src, open(dest, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            cfg_count += 1
                    if cfg_count > 0:
                        restored_items.append(f"Server Configs ({cfg_count})")

                # C. Web Manager configs and data (.json)
                if restore_data:
                    data_count = 0
                    for m in namelist:
                        if m.startswith("data/") and not m.endswith("/"):
                            rel = m[len("data/"):]
                            dest = SERVER_DIR / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(m) as src, open(dest, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            data_count += 1
                    if data_count > 0:
                        restored_items.append(f"Manager Data ({data_count})")

                # D. Logs
                if restore_logs:
                    log_count = 0
                    for m in namelist:
                        if m.endswith("/"):
                            continue
                        if m.startswith("logs/server/"):
                            rel = m[len("logs/server/"):]
                            dest = SERVER_DIR / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(m) as src, open(dest, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            log_count += 1
                        elif m.startswith("logs/history/"):
                            rel = m[len("logs/history/"):]
                            dest = LOGS_DIR / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(m) as src, open(dest, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            log_count += 1
                        elif m.startswith("logs/unity/"):
                            rel = m[len("logs/unity/"):]
                            dest = WORLD_SAVE_DIR / rel
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            with zf.open(m) as src, open(dest, "wb") as dst:
                                shutil.copyfileobj(src, dst)
                            log_count += 1
                    if log_count > 0:
                        restored_items.append(f"Logs ({log_count})")

            else:
                # 2B. Legacy V1 Archive extraction (worlds_local / worlds only)
                if restore_world:
                    if prune_active_world and current_world_name:
                        for folder in ["worlds_local", "worlds"]:
                            pdir = WORLD_SAVE_DIR / folder
                            if pdir.exists():
                                for item in list(pdir.iterdir()):
                                    if (
                                        item.name == current_world_name
                                        or item.name.startswith(f"{current_world_name}.")
                                        or item.name.startswith(f"{current_world_name}_")
                                    ):
                                        try:
                                            if item.is_dir():
                                                shutil.rmtree(item)
                                            else:
                                                item.unlink()
                                        except Exception:
                                            pass
                    WORLD_SAVE_DIR.mkdir(parents=True, exist_ok=True)
                    for member in namelist:
                        dest = (WORLD_SAVE_DIR / member).resolve()
                        if not str(dest).startswith(str(WORLD_SAVE_DIR.resolve())):
                            raise ValueError(f"Illegal path in legacy archive: {member}")
                    zf.extractall(WORLD_SAVE_DIR)
                    restored_items.append("Legacy World Files")

                if restore_configs or restore_data or restore_logs:
                    restored_items.append("(Non-world components not present in legacy v1 archive)")

        items_str = ", ".join(restored_items) if restored_items else "No components selected"
        return (
            True,
            f"Successfully restored '{filename}' [{items_str}]. Safety backup saved as '{snap_name}'.",
        )
    except Exception as e:
        return False, f"Restore failed: {e}"


def delete_backup(filename: str) -> Tuple[bool, str]:
    """Delete a backup archive."""
    zip_path = get_backup_path(filename)
    if not zip_path:
        return False, f"Backup archive '{filename}' not found."

    try:
        zip_path.unlink()
        return True, f"Backup '{filename}' deleted."
    except Exception as e:
        return False, f"Failed to delete backup: {e}"
