import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

# Add webmanager to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webmanager"))

from core.config import (
    DEFAULT_CONFIG,
    DEFAULT_MODIFIERS,
    load_config,
    save_config,
    validate_modifiers,
)
from core.server import build_valheim_command


class TestWorldModifiers(unittest.TestCase):
    def test_default_modifiers_structure(self):
        self.assertIn("modifiers", DEFAULT_CONFIG)
        mods = DEFAULT_CONFIG["modifiers"]
        self.assertEqual(mods["preset"], "")
        self.assertEqual(mods["combat"], "")
        self.assertEqual(mods["death_penalty"], "")
        self.assertEqual(mods["resources"], "")
        self.assertEqual(mods["raids"], "")
        self.assertEqual(mods["portals"], "")
        self.assertFalse(mods["nobuildcost"])
        self.assertFalse(mods["playerevents"])
        self.assertFalse(mods["passivemobs"])
        self.assertFalse(mods["nomap"])
        self.assertFalse(mods["reset_modifiers"])

    def test_validate_modifiers(self):
        # Valid cases
        ok, err = validate_modifiers({})
        self.assertTrue(ok)

        ok, err = validate_modifiers({
            "preset": "hard",
            "combat": "veryhard",
            "death_penalty": "casual",
            "resources": "most",
            "raids": "none",
            "portals": "casual",
            "nobuildcost": True,
        })
        self.assertTrue(ok)
        self.assertEqual(err, "")

        # Invalid preset
        ok, err = validate_modifiers({"preset": "supergodmode"})
        self.assertFalse(ok)
        self.assertIn("Invalid modifier preset", err)

        # Invalid combat
        ok, err = validate_modifiers({"combat": "impossible"})
        self.assertFalse(ok)
        self.assertIn("Invalid combat modifier", err)

        # Invalid death penalty
        ok, err = validate_modifiers({"death_penalty": "instant_death"})
        self.assertFalse(ok)
        self.assertIn("Invalid death penalty modifier", err)

        # Invalid resources
        ok, err = validate_modifiers({"resources": "infinite"})
        self.assertFalse(ok)
        self.assertIn("Invalid resources modifier", err)

        # Invalid raids
        ok, err = validate_modifiers({"raids": "nightly"})
        self.assertFalse(ok)
        self.assertIn("Invalid raids modifier", err)

        # Invalid portals
        ok, err = validate_modifiers({"portals": "anywhere"})
        self.assertFalse(ok)
        self.assertIn("Invalid portals modifier", err)

    def test_build_valheim_command_defaults(self):
        cfg = {
            "server_name": "ValheimTest",
            "port": "2456",
            "world_name": "MyWorld",
            "password": "secretpassword",
            "modifiers": DEFAULT_MODIFIERS.copy(),
        }
        cmd = build_valheim_command(cfg)
        self.assertIn("-name", cmd)
        self.assertIn("-port", cmd)
        self.assertIn("-world", cmd)
        self.assertIn("-password", cmd)
        self.assertNotIn("-preset", cmd)
        self.assertNotIn("-modifier", cmd)
        self.assertNotIn("-setkey", cmd)
        self.assertNotIn("-resetmodifiers", cmd)

    def test_build_valheim_command_with_preset_and_modifiers(self):
        cfg = {
            "server_name": "ValheimTest",
            "port": "2456",
            "world_name": "MyWorld",
            "password": "secretpassword",
            "modifiers": {
                "preset": "hard",
                "combat": "veryhard",
                "resources": "most",
                "raids": "none",
                "portals": "casual",
                "nomap": True,
                "nobuildcost": True,
            },
        }
        cmd = build_valheim_command(cfg)

        # -preset hard should appear before any -modifier
        preset_idx = cmd.index("-preset")
        self.assertEqual(cmd[preset_idx + 1], "hard")

        # Modifiers
        self.assertIn("-modifier", cmd)
        combat_idx = cmd.index("combat")
        self.assertEqual(cmd[combat_idx - 1], "-modifier")
        self.assertEqual(cmd[combat_idx + 1], "veryhard")
        self.assertLess(preset_idx, combat_idx)

        res_idx = cmd.index("resources")
        self.assertEqual(cmd[res_idx + 1], "most")

        raids_idx = cmd.index("raids")
        self.assertEqual(cmd[raids_idx + 1], "none")

        portals_idx = cmd.index("portals")
        self.assertEqual(cmd[portals_idx + 1], "casual")

        # Keys
        self.assertIn("nomap", cmd)
        self.assertIn("nobuildcost", cmd)
        self.assertNotIn("passivemobs", cmd)

    def test_build_valheim_command_reset_modifiers(self):
        cfg = {
            "server_name": "ValheimTest",
            "port": "2456",
            "world_name": "MyWorld",
            "password": "secretpassword",
            "modifiers": {
                "reset_modifiers": True,
                "preset": "casual",
            },
        }
        cmd = build_valheim_command(cfg)
        self.assertIn("-resetmodifiers", cmd)
        self.assertIn("-preset", cmd)
        self.assertLess(cmd.index("-resetmodifiers"), cmd.index("-preset"))

    def test_save_and_load_config_with_modifiers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_cfg = Path(tmpdir) / "server_config.json"
            with patch("core.config.CONFIG_FILE", tmp_cfg):
                # Save new config
                save_data = {
                    "server_name": "ModServer",
                    "password": "validpassword",
                    "modifiers": {
                        "preset": "hardcore",
                        "combat": "veryhard",
                        "nomap": True,
                    },
                }
                ok, msg = save_config(save_data)
                self.assertTrue(ok)

                # Load config
                loaded = load_config()
                self.assertEqual(loaded["server_name"], "ModServer")
                self.assertEqual(loaded["modifiers"]["preset"], "hardcore")
                self.assertEqual(loaded["modifiers"]["combat"], "veryhard")
                self.assertTrue(loaded["modifiers"]["nomap"])
                # Ensure missing defaults are backfilled
                self.assertFalse(loaded["modifiers"]["nobuildcost"])
                self.assertEqual(loaded["modifiers"]["raids"], "")

    def test_api_config_endpoint_with_modifiers(self):
        try:
            from fastapi.testclient import TestClient
            from app import app
        except ImportError:
            return

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_cfg = Path(tmpdir) / "server_config.json"
            tmp_cfg.write_text(json.dumps(DEFAULT_CONFIG))
            with patch("core.config.CONFIG_FILE", tmp_cfg):
                client = TestClient(app)
                # Mock admin user
                with patch("app.get_current_user_from_request", return_value={"username": "admin", "role": "admin"}):
                    form_data = {
                        "server_name": "APIWorldTest",
                        "world_name": "APIWorld",
                        "password": "apipassword",
                        "port": "2456",
                        "playit_address": "",
                        "auto_restart": "true",
                        "mod_preset": "hard",
                        "mod_combat": "hard",
                        "mod_death_penalty": "casual",
                        "mod_resources": "more",
                        "mod_raids": "less",
                        "mod_portals": "casual",
                        "mod_nobuildcost": "true",
                        "mod_playerevents": "true",
                        "mod_passivemobs": "true",
                        "mod_nomap": "true",
                        "mod_reset_modifiers": "true",
                    }
                    resp = client.post("/api/config", data=form_data)
                    self.assertEqual(resp.status_code, 200)
                    self.assertIn("World Modifiers", resp.text)

                    # Verify saved config
                    with open(tmp_cfg) as f:
                        saved = json.load(f)
                    self.assertEqual(saved["server_name"], "APIWorldTest")
                    mods = saved["modifiers"]
                    self.assertEqual(mods["preset"], "hard")
                    self.assertEqual(mods["combat"], "hard")
                    self.assertEqual(mods["death_penalty"], "casual")
                    self.assertEqual(mods["resources"], "more")
                    self.assertEqual(mods["raids"], "less")
                    self.assertEqual(mods["portals"], "casual")
                    self.assertTrue(mods["nobuildcost"])
                    self.assertTrue(mods["playerevents"])
                    self.assertTrue(mods["passivemobs"])
                    self.assertTrue(mods["nomap"])
                    self.assertTrue(mods["reset_modifiers"])


if __name__ == "__main__":
    unittest.main()

