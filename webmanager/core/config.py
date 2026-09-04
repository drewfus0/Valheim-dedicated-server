import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple

from core.paths import CONFIG_FILE

DEFAULT_CONFIG: Dict[str, Any] = {
    "server_name": "ValheimTest",
    "world_name": "ValheimTest",
    "password": "tester",
    "port": "2456",
    "playit_address": "",
    "auto_restart": True,
}


def load_config() -> Dict[str, Any]:
    """Load configuration from server_config.json with default fallback."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                return {**DEFAULT_CONFIG, **cfg}
        except Exception as e:
            print(f"[Config] Error loading config: {e}")
    return DEFAULT_CONFIG.copy()


def save_config(new_cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate and atomically save server configuration."""
    current = load_config()
    current.update(new_cfg)

    pwd = current.get("password", "")
    if len(str(pwd)) < 5:
        return False, "Valheim password must be at least 5 characters long."

    try:
        # Atomic write to avoid partial writes
        temp_file = CONFIG_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=4)
        temp_file.replace(CONFIG_FILE)
        return True, "Configuration saved successfully."
    except Exception as e:
        return False, f"Failed to save configuration: {e}"
