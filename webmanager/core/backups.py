import datetime
import os
import random
import re
import shutil
import string
import struct
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.paths import BACKUP_DIR, WORLD_SAVE_DIR


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
    """List existing backup zip files with size and formatted timestamp."""
    if not BACKUP_DIR.exists():
        return []

    backups = []
    for f in sorted(BACKUP_DIR.glob("*.zip"), key=lambda x: x.stat().st_mtime, reverse=True):
        stat = f.stat()
        backups.append({
            "filename": f.name,
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "created": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "mtime": stat.st_mtime,
        })
    return backups


def get_backup_path(filename: str) -> Optional[Path]:
    """Safely resolve a backup file path, preventing directory traversal."""
    safe_name = Path(filename).name
    target = BACKUP_DIR / safe_name
    if target.exists() and target.is_file() and target.suffix == ".zip":
        return target
    return None


def create_backup(world_name: str = "world") -> Tuple[bool, str, Optional[str]]:
    """Create a compressed zip backup of Valheim world directories."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    zip_name = f"valheim_backup_{world_name}_{timestamp}.zip"
    zip_path = BACKUP_DIR / zip_name

    target_dirs = [WORLD_SAVE_DIR / "worlds_local", WORLD_SAVE_DIR / "worlds"]
    found_any = False

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for tdir in target_dirs:
                if tdir.exists():
                    found_any = True
                    for root, _, files in os.walk(tdir):
                        for file in files:
                            fpath = Path(root) / file
                            arcname = fpath.relative_to(WORLD_SAVE_DIR)
                            zf.write(fpath, arcname)

        if not found_any:
            if zip_path.exists():
                zip_path.unlink()
            return False, f"No world save directory found at {WORLD_SAVE_DIR}", None

        size_mb = round(zip_path.stat().st_size / (1024 * 1024), 2)
        return True, f"Backup created successfully: {zip_name} ({size_mb} MB)", zip_name
    except Exception as e:
        return False, f"Backup failed: {e}", None


def restore_backup(filename: str) -> Tuple[bool, str]:
    """Safely restore a world backup archive, taking a safety snapshot first."""
    zip_path = get_backup_path(filename)
    if not zip_path:
        return False, f"Backup archive '{filename}' not found."

    try:
        # 1. Create safety snapshot of current worlds before extraction
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        safety_zip = BACKUP_DIR / f"valheim_safety_pre_restore_{timestamp}.zip"
        target_dirs = [WORLD_SAVE_DIR / "worlds_local", WORLD_SAVE_DIR / "worlds"]

        with zipfile.ZipFile(safety_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for tdir in target_dirs:
                if tdir.exists():
                    for root, _, files in os.walk(tdir):
                        for file in files:
                            fpath = Path(root) / file
                            arcname = fpath.relative_to(WORLD_SAVE_DIR)
                            zf.write(fpath, arcname)

        # 2. Extract selected archive into WORLD_SAVE_DIR
        WORLD_SAVE_DIR.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, "r") as zf:
            # Verify paths inside zip to prevent zip slip
            for member in zf.namelist():
                dest = (WORLD_SAVE_DIR / member).resolve()
                if not str(dest).startswith(str(WORLD_SAVE_DIR.resolve())):
                    raise ValueError(f"Illegal path in archive: {member}")
            zf.extractall(WORLD_SAVE_DIR)

        return True, f"Successfully restored '{filename}'. Pre-restore safety backup saved as '{safety_zip.name}'."
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
