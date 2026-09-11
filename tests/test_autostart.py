import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add webmanager to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webmanager"))

from core.config import DEFAULT_CONFIG, load_config, save_config
from core.server import ValheimServerManager
from core.system import ensure_systemd_boot_service
from fastapi.testclient import TestClient

with patch("subprocess.Popen"):
    from app import app


class TestAutoStartOnBoot(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_config_file = Path(self.temp_dir.name) / "server_config.json"
        self.config_patcher = patch("core.config.CONFIG_FILE", self.test_config_file)
        self.config_patcher.start()

    def tearDown(self):
        self.config_patcher.stop()
        self.temp_dir.cleanup()

    def test_default_config_has_auto_start_on_boot(self):
        self.assertIn("auto_start_on_boot", DEFAULT_CONFIG)
        self.assertTrue(DEFAULT_CONFIG["auto_start_on_boot"])

    def test_load_and_save_auto_start_on_boot(self):
        # Save config with auto_start_on_boot = False
        ok, msg = save_config({"auto_start_on_boot": False})
        self.assertTrue(ok)
        loaded = load_config()
        self.assertFalse(loaded["auto_start_on_boot"])

        # Save config with auto_start_on_boot = True
        ok, msg = save_config({"auto_start_on_boot": True})
        self.assertTrue(ok)
        loaded = load_config()
        self.assertTrue(loaded["auto_start_on_boot"])

    @patch("core.server.find_existing_pid", return_value=None)
    @patch.object(ValheimServerManager, "_monitor_loop", return_value=None)
    @patch.object(ValheimServerManager, "start_server")
    def test_manager_auto_starts_when_enabled(self, mock_start, mock_monitor, mock_pid):
        with patch("core.server.load_config", return_value={"auto_start_on_boot": True, "password": "tester"}):
            mgr = ValheimServerManager()
            mock_start.assert_not_called()
            mgr.check_auto_start_on_boot()
            mock_start.assert_called_once_with(trigger="Auto-Start on Boot")

    @patch("core.server.find_existing_pid", return_value=None)
    @patch.object(ValheimServerManager, "_monitor_loop", return_value=None)
    @patch.object(ValheimServerManager, "start_server")
    def test_manager_does_not_auto_start_when_disabled(self, mock_start, mock_monitor, mock_pid):
        with patch("core.server.load_config", return_value={"auto_start_on_boot": False, "password": "tester"}):
            mgr = ValheimServerManager()
            mgr.check_auto_start_on_boot()
            mock_start.assert_not_called()

    @patch("core.server.find_existing_pid", return_value=12345)
    @patch.object(ValheimServerManager, "_monitor_loop", return_value=None)
    @patch.object(ValheimServerManager, "start_server")
    def test_manager_attaches_to_existing_pid_without_starting(self, mock_start, mock_monitor, mock_pid):
        with patch("core.server.load_config", return_value={"auto_start_on_boot": True, "password": "tester"}):
            mgr = ValheimServerManager()
            self.assertEqual(mgr.existing_pid, 12345)
            self.assertEqual(mgr.state, "Running")
            mgr.check_auto_start_on_boot()
            mock_start.assert_not_called()

    def test_api_config_endpoint_toggles_auto_start_on_boot(self):
        client = TestClient(app)
        with patch("app.get_current_user_from_request", return_value={"username": "admin", "role": "admin"}):
            # 1. Post with auto_start_on_boot checked
            resp = client.post(
                "/api/config",
                data={
                    "server_name": "ValheimTest",
                    "world_name": "ValheimTest",
                    "password": "testerpassword",
                    "port": "2456",
                    "playit_address": "",
                    "auto_start_on_boot": "true",
                    "auto_restart": "true",
                },
            )
            self.assertEqual(resp.status_code, 200)
            cfg = load_config()
            self.assertTrue(cfg.get("auto_start_on_boot"))

            # 2. Post with auto_start_on_boot unchecked (omitted from form data)
            resp = client.post(
                "/api/config",
                data={
                    "server_name": "ValheimTest",
                    "world_name": "ValheimTest",
                    "password": "testerpassword",
                    "port": "2456",
                    "playit_address": "",
                    "auto_restart": "true",
                },
            )
            self.assertEqual(resp.status_code, 200)
            cfg = load_config()
            self.assertFalse(cfg.get("auto_start_on_boot"))

    def test_ensure_systemd_boot_service(self):
        with patch("subprocess.run") as mock_sub:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "enabled\n"
            mock_sub.return_value = mock_res

            res = ensure_systemd_boot_service()
            self.assertIn("service_enabled", res)
            self.assertIn("linger_enabled", res)


if __name__ == "__main__":
    unittest.main()
