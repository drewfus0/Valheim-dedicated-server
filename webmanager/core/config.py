import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple

from core.paths import CONFIG_FILE

DEFAULT_MODIFIERS: Dict[str, Any] = {
    "preset": "",          # "", "normal", "casual", "easy", "hard", "hardcore", "immersive", "hammer"
    "combat": "",          # "", "veryeasy", "easy", "hard", "veryhard"
    "death_penalty": "",   # "", "casual", "veryeasy", "easy", "hard", "hardcore"
    "resources": "",       # "", "muchless", "less", "more", "muchmore", "most"
    "raids": "",           # "", "none", "muchless", "less", "more", "muchmore"
    "portals": "",         # "", "casual", "hard", "veryhard"
    "nobuildcost": False,  # Free building & crafting
    "playerevents": False, # Player-based raid progression
    "passivemobs": False,  # Passive enemies until attacked
    "nomap": False,        # Disable map and minimap
    "reset_modifiers": False, # Reset world modifiers on next server launch
}

DEFAULT_CONFIG: Dict[str, Any] = {
    "server_name": "ValheimTest",
    "world_name": "ValheimTest",
    "password": "tester",
    "port": "2456",
    "playit_address": "",
    "auto_restart": True,
    "modifiers": DEFAULT_MODIFIERS.copy(),
}

VALID_PRESETS = {"", "normal", "casual", "easy", "hard", "hardcore", "immersive", "hammer"}
VALID_COMBAT = {"", "veryeasy", "easy", "hard", "veryhard"}
VALID_DEATH_PENALTY = {"", "casual", "veryeasy", "easy", "hard", "hardcore"}
VALID_RESOURCES = {"", "muchless", "less", "more", "muchmore", "most"}
VALID_RAIDS = {"", "none", "muchless", "less", "more", "muchmore"}
VALID_PORTALS = {"", "casual", "hard", "veryhard"}


def validate_modifiers(mods: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate world modifier options against Valheim specification."""
    if not isinstance(mods, dict):
        return False, "World modifiers must be a dictionary."

    preset = str(mods.get("preset", "")).strip().lower()
    if preset not in VALID_PRESETS:
        return False, f"Invalid modifier preset '{preset}'. Valid presets: {', '.join(sorted(p for p in VALID_PRESETS if p))}."

    combat = str(mods.get("combat", "")).strip().lower()
    if combat not in VALID_COMBAT:
        return False, f"Invalid combat modifier '{combat}'."

    death = str(mods.get("death_penalty", "")).strip().lower()
    if death not in VALID_DEATH_PENALTY:
        return False, f"Invalid death penalty modifier '{death}'."

    res = str(mods.get("resources", "")).strip().lower()
    if res not in VALID_RESOURCES:
        return False, f"Invalid resources modifier '{res}'."

    raids = str(mods.get("raids", "")).strip().lower()
    if raids not in VALID_RAIDS:
        return False, f"Invalid raids modifier '{raids}'."

    portals = str(mods.get("portals", "")).strip().lower()
    if portals not in VALID_PORTALS:
        return False, f"Invalid portals modifier '{portals}'."

    return True, ""


def load_config() -> Dict[str, Any]:
    """Load configuration from server_config.json with default fallback."""
    cfg_out = DEFAULT_CONFIG.copy()
    cfg_out["modifiers"] = DEFAULT_MODIFIERS.copy()

    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                saved = json.load(f)
                cfg_out.update(saved)
                # Deep-merge modifiers dictionary to preserve default keys
                saved_mods = saved.get("modifiers")
                if isinstance(saved_mods, dict):
                    merged_mods = DEFAULT_MODIFIERS.copy()
                    merged_mods.update(saved_mods)
                    cfg_out["modifiers"] = merged_mods
                else:
                    cfg_out["modifiers"] = DEFAULT_MODIFIERS.copy()
                return cfg_out
        except Exception as e:
            print(f"[Config] Error loading config: {e}")
    return cfg_out


def save_config(new_cfg: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate and atomically save server configuration."""
    current = load_config()
    current.update(new_cfg)

    pwd = current.get("password", "")
    if len(str(pwd)) < 5:
        return False, "Valheim password must be at least 5 characters long."

    mods = current.get("modifiers")
    if mods:
        ok_mods, err_mods = validate_modifiers(mods)
        if not ok_mods:
            return False, err_mods

    try:
        # Atomic write to avoid partial writes
        temp_file = CONFIG_FILE.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(current, f, indent=4)
        temp_file.replace(CONFIG_FILE)
        return True, "Configuration saved successfully."
    except Exception as e:
        return False, f"Failed to save configuration: {e}"
