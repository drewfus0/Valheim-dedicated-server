import datetime
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add webmanager to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webmanager"))

from core.server_history import ServerHistoryTracker


class TestServerHistorySaves(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.temp_path = Path(self.temp_dir.name)

        # Create isolated tracker instance
        self.tracker = ServerHistoryTracker(auto_load=False)
        self.tracker.runs = []

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_extract_saves_chunk_and_classic_formats(self):
        lines = [
            "10/11/2026 12:17:33: World save (1/5) Cloud & Backup checks done [0ms] => Save number 1515\n",
            "10/11/2026 12:17:33: World save (2/5) Chunks writing done [15ms]\n",
            "10/11/2026 12:17:33: World save (3/5) DB2 writing done [7ms]\n",
            "10/11/2026 12:17:33: World save (4/5) FWL writing done [0ms]\n",
            "10/11/2026 12:17:33: World save (5/5) done. Total time [361ms]\n",
            "08/27/2026 07:08:49: World saved ( 47.364ms )\n",
        ]
        saves = self.tracker._extract_saves_from_lines(lines)
        self.assertEqual(len(saves), 2)

        # Chunk format
        self.assertEqual(saves[0]["time"], "2026-10-11 12:17:33")
        self.assertEqual(saves[0]["duration"], "361ms")
        self.assertEqual(saves[0]["save_number"], 1515)

        # Classic format
        self.assertEqual(saves[1]["time"], "2026-08-27 07:08:49")
        self.assertEqual(saves[1]["duration"], "47.364ms")
        self.assertIsNone(saves[1]["save_number"])

    def test_get_summary_aggregates_world_saves(self):
        self.tracker.runs = [
            {
                "id": "run_1",
                "status": "Stopped",
                "duration_seconds": 3600,
                "save_count": 2,
                "last_save_time": "2026-10-10 14:00:00",
                "saves": [
                    {"time": "2026-10-10 13:30:00", "duration": "200ms", "save_number": 10},
                    {"time": "2026-10-10 14:00:00", "duration": "220ms", "save_number": 11},
                ],
            },
            {
                "id": "run_2",
                "status": "Running",
                "duration_seconds": 1800,
                "save_count": 1,
                "last_save_time": "2026-10-10 15:30:00",
                "saves": [
                    {"time": "2026-10-10 15:30:00", "duration": "250ms", "save_number": 12},
                ],
            },
        ]

        summary = self.tracker.get_summary(server_running=False)
        self.assertEqual(summary["total_runs"], 2)
        self.assertEqual(summary["total_saves"], 3)
        self.assertEqual(summary["last_save_time"], "2026-10-10 15:30:00")
        self.assertEqual(len(summary["recent_runs"]), 2)
        self.assertEqual(summary["recent_runs"][0]["save_count"], 1)

    def test_process_new_logs_live_recording(self):
        from unittest.mock import patch

        fake_game_log = self.temp_path / "fake_valheim_server.log"
        fake_plain_log = self.temp_path / "fake_server_history.log"
        fake_history_file = self.temp_path / "fake_server_history.json"

        self.tracker.auto_load = True
        self.tracker.runs = [
            {
                "id": "run_active",
                "status": "Running",
                "world_name": "Midgard",
                "port": 2456,
                "save_count": 0,
                "last_save_time": None,
                "saves": [],
            }
        ]

        with patch("core.server_history.GAME_LOG", fake_game_log), \
             patch("core.server_history.SERVER_HISTORY_LOG", fake_plain_log), \
             patch("core.server_history.SERVER_HISTORY_FILE", fake_history_file):

            # Initial write
            fake_game_log.write_text("10/11/2026 12:40:45: Server starting\n", encoding="utf-8")
            self.tracker.process_new_logs()
            self.assertEqual(self.tracker.runs[0]["save_count"], 0)

            # Append world save lines
            with open(fake_game_log, "a", encoding="utf-8") as f:
                f.write("10/11/2026 13:10:45: World save (1/5) Cloud & Backup checks done [0ms] => Save number 1516\n")
                f.write("10/11/2026 13:10:45: World save (5/5) done. Total time [312ms]\n")

            self.tracker.process_new_logs()
            self.assertEqual(self.tracker.runs[0]["save_count"], 1)
            self.assertEqual(self.tracker.runs[0]["last_save_time"], "2026-10-11 13:10:45")
            self.assertEqual(self.tracker.runs[0]["last_save_duration"], "312ms")
            self.assertEqual(self.tracker.runs[0]["last_save_number"], 1516)

            # Verify plain text log was appended with [SAVE]
            plain_content = fake_plain_log.read_text(encoding="utf-8")
            self.assertIn("[SAVE] World saved (World: 'Midgard' | Save #1516 | Duration: 312ms)", plain_content)


if __name__ == "__main__":
    unittest.main()
