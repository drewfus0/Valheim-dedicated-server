import asyncio
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webmanager"))

from core.logs import log_stream_generator, read_journalctl_tail, read_log_tail
from core.paths import LINUX64_DIR, SERVER_BIN, SERVER_DIR
from core.server import (
    VALHEIM_SERVICE_NAME,
    ValheimServerManager,
    build_valheim_command,
    format_systemd_exec_start,
    generate_valheim_service_unit,
    get_systemctl_status_output,
    get_systemd_service_status,
    write_valheim_service_unit,
)


class TestSystemdHeadlessArchitecture(unittest.TestCase):
    def test_generate_valheim_service_unit_matches_headless_spec(self):
        cfg = {
            "server_name": "ValheimTest",
            "port": "2456",
            "world_name": "TheOneWorld",
            "password": "tester",
            "auto_restart": True,
            "modifiers": {
                "resources": "more",
            },
        }
        unit_text = generate_valheim_service_unit(cfg)

        self.assertIn("[Unit]", unit_text)
        self.assertIn("Description=Valheim Dedicated Server (Headless Context)", unit_text)
        self.assertIn("After=network.target", unit_text)
        self.assertIn("[Service]", unit_text)
        self.assertIn("Type=simple", unit_text)
        self.assertIn("Restart=on-failure", unit_text)
        self.assertIn(f"WorkingDirectory={SERVER_DIR}", unit_text)
        self.assertIn(f'Environment="LD_LIBRARY_PATH={LINUX64_DIR}"', unit_text)
        self.assertIn('Environment="SteamAppId=892970"', unit_text)
        self.assertIn("UnsetEnvironment=DISPLAY WAYLAND_DISPLAY", unit_text)

        expected_exec = (
            f'"{SERVER_BIN}" '
            "-name ValheimTest -port 2456 -world TheOneWorld -password tester -modifier resources more"
        )
        self.assertIn(f"ExecStart={expected_exec}", unit_text)
        self.assertIn("[Install]", unit_text)
        self.assertIn("WantedBy=default.target", unit_text)

    def test_write_valheim_service_unit_writes_and_reloads_daemon(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            unit_path = Path(tmpdir) / "valheim.service"
            cfg = {
                "server_name": "ValheimTest",
                "port": "2456",
                "world_name": "TheOneWorld",
                "password": "tester",
                "modifiers": {"resources": "more"},
            }
            with patch("core.server.subprocess.run") as mock_run:
                ok = write_valheim_service_unit(cfg, unit_path=unit_path, reload_daemon=True)
                self.assertTrue(ok)
                self.assertTrue(unit_path.exists())
                content = unit_path.read_text(encoding="utf-8")
                self.assertIn("Description=Valheim Dedicated Server (Headless Context)", content)
                mock_run.assert_called_once_with(
                    ["systemctl", "--user", "daemon-reload"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )

    def test_get_systemd_service_status_parses_running_and_stopped(self):
        with patch("core.server.subprocess.run") as mock_run, patch(
            "core.server._is_valheim_binary_pid", return_value=True
        ):
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = (
                "ActiveState=active\n"
                "SubState=running\n"
                "MainPID=42424\n"
                "ExecMainStatus=0\n"
                "Result=success\n"
            )
            mock_run.return_value = mock_res

            status = get_systemd_service_status()
            self.assertEqual(status["state"], "Running")
            self.assertEqual(status["main_pid"], 42424)
            self.assertEqual(status["active_state"], "active")

            mock_res.stdout = (
                "ActiveState=inactive\n"
                "SubState=dead\n"
                "MainPID=0\n"
                "ExecMainStatus=0\n"
                "Result=success\n"
            )
            status_stopped = get_systemd_service_status()
            self.assertEqual(status_stopped["state"], "Stopped")
            self.assertIsNone(status_stopped["main_pid"])

    def test_get_systemctl_status_output_invokes_systemctl_status(self):
        with patch("core.server.subprocess.run") as mock_run:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "● valheim.service - Valheim Dedicated Server (Headless Context)\n   Active: active (running)"
            mock_run.return_value = mock_res

            code, out = get_systemctl_status_output()
            self.assertEqual(code, 0)
            self.assertIn("Valheim Dedicated Server (Headless Context)", out)
            mock_run.assert_called_once_with(
                ["systemctl", "--user", "status", VALHEIM_SERVICE_NAME, "--no-pager"],
                capture_output=True,
                text=True,
                timeout=3,
            )

    @patch("core.server.find_existing_pid", return_value=None)
    @patch.object(ValheimServerManager, "_monitor_loop", return_value=None)
    @patch.object(ValheimServerManager, "_journal_sync_loop", return_value=None)
    def test_lifecycle_start_stop_restart_use_systemctl(self, mock_journal, mock_monitor, mock_pid):
        cfg = {
            "server_name": "ValheimTest",
            "port": "2456",
            "world_name": "TheOneWorld",
            "password": "tester",
            "modifiers": {"resources": "more"},
        }
        with patch("core.server.load_config", return_value=cfg), patch(
            "core.server.write_valheim_service_unit", return_value=True
        ), patch("core.server.ValheimServerManager._rotate_logs"), patch(
            "core.server.SERVER_HISTORY"
        ), patch(
            "core.server.PLAYER_TRACKER"
        ), patch(
            "core.server.get_systemd_service_status",
            return_value={"state": "Running", "main_pid": 55555, "active_state": "active", "sub_state": "running"},
        ), patch(
            "core.server.subprocess.run"
        ) as mock_run:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stderr = ""
            mock_run.return_value = mock_res

            mgr = ValheimServerManager()

            # 1. Start server
            ok, msg = mgr.start_server()
            self.assertTrue(ok)
            for _ in range(20):
                if mgr.state == "Running":
                    break
                time.sleep(0.05)
            self.assertEqual(mgr.state, "Running")
            self.assertEqual(mgr.existing_pid, 55555)
            mock_run.assert_any_call(
                ["systemctl", "--user", "start", VALHEIM_SERVICE_NAME],
                capture_output=True,
                text=True,
                timeout=20,
            )

            # 2. Restart server
            with patch("os.kill", side_effect=OSError()):
                ok, msg = mgr.restart_server()
                self.assertTrue(ok)
                for _ in range(20):
                    if any(
                        call.args and call.args[0] == ["systemctl", "--user", "restart", VALHEIM_SERVICE_NAME]
                        for call in mock_run.call_args_list
                    ):
                        break
                    time.sleep(0.05)
                mock_run.assert_any_call(
                    ["systemctl", "--user", "restart", VALHEIM_SERVICE_NAME],
                    capture_output=True,
                    text=True,
                    timeout=40,
                )

            # Wait for restart thread to finish setting Running
            for _ in range(20):
                if mgr.state == "Running":
                    break
                time.sleep(0.05)

            # 3. Stop server
            with patch("os.kill", side_effect=OSError()):
                ok, msg = mgr.stop_server()
                self.assertTrue(ok)
                for _ in range(20):
                    if mgr.state == "Stopped":
                        break
                    time.sleep(0.05)
                self.assertEqual(mgr.state, "Stopped")
                mock_run.assert_any_call(
                    ["systemctl", "--user", "stop", VALHEIM_SERVICE_NAME],
                    capture_output=True,
                    text=True,
                    timeout=40,
                )

    def test_read_journalctl_tail_and_read_log_tail(self):
        with patch("core.logs.subprocess.run") as mock_run:
            mock_res = MagicMock()
            mock_res.returncode = 0
            mock_res.stdout = "11/12/2026 02:00:00: Zonesystem Start\n11/12/2026 02:00:05: World saved ( 42.10ms )\n"
            mock_run.return_value = mock_res

            out = read_log_tail(num_lines=50)
            self.assertIn("Zonesystem Start", out)
            self.assertIn("World saved", out)
            mock_run.assert_called_once_with(
                [
                    "journalctl",
                    "--user",
                    "-u",
                    "valheim.service",
                    "--invocation=0",
                    "-n",
                    "50",
                    "-o",
                    "cat",
                    "--no-pager",
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )

    def test_log_stream_generator_streams_from_journalctl(self):
        async def _run_stream():
            mock_proc = MagicMock()
            mock_proc.returncode = None
            mock_proc.stdout = MagicMock()
            mock_proc.stdout.readline = AsyncMock(
                side_effect=[
                    b"11/12/2026 02:10:00: Got handshake from client 76561198000000001\n",
                    b"",
                ]
            )
            mock_proc.terminate = MagicMock()
            mock_proc.wait = AsyncMock(return_value=0)

            with patch("core.logs.read_log_tail", return_value="11/12/2026 02:00:00: Initial line\n"), patch(
                "asyncio.create_subprocess_exec", new=AsyncMock(return_value=mock_proc)
            ) as mock_exec:
                chunks = []
                async for item in log_stream_generator(initial_lines=10):
                    chunks.append(item)

                self.assertEqual(len(chunks), 2)
                self.assertIn("Initial line", chunks[0])
                self.assertIn("Got handshake from client", chunks[1])
                mock_exec.assert_called_once()
                self.assertEqual(
                    mock_exec.call_args.args[:4],
                    ("journalctl", "--user", "-u", "valheim.service"),
                )

        asyncio.run(_run_stream())


if __name__ == "__main__":
    unittest.main()
