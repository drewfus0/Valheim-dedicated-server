import unittest
from pathlib import Path
from unittest.mock import patch
import sys

# Ensure webmanager is in path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webmanager"))

from fastapi.testclient import TestClient
from app import app
from core.world_stats import WorldStatsTracker, extract_world_keys


class TestInGameTime(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_time_of_day_calculation_noon(self):
        tracker = WorldStatsTracker(auto_load=False)
        # Day 10, exact midday (fraction 0.50 -> 12:00)
        # 10 * 1800 + 0.5 * 1800 = 18000 + 900 = 18900
        tracker._saved_net_time = 18900.0
        tracker._saved_net_time_mtime = 1000000.0

        with patch("time.time", return_value=1000000.0):
            data = tracker.get_time_of_day_data(server_running=True, players_count=1)

        self.assertEqual(data["current_day"], 10)
        self.assertEqual(data["time_24h"], "12:00")
        self.assertEqual(data["time_12h"], "12:00 PM")
        self.assertTrue(data["is_day"])
        self.assertEqual(data["phase"], "Day")
        self.assertEqual(data["phase_icon"], "☀️")
        self.assertEqual(data["next_event_name"], "Sunset")
        self.assertEqual(data["next_event_icon"], "🌅")
        self.assertEqual(data["next_event_time"], "18:00")
        # 0.25 fraction remaining until sunset -> 0.25 * 1800 = 450 seconds = 7m 30s
        self.assertEqual(data["seconds_to_event"], 450)
        self.assertEqual(data["time_to_event_str"], "7m 30s")
        self.assertEqual(data["next_event_text"], "Sunset in 7m 30s")
        self.assertAlmostEqual(data["phase_progress_pct"], 50.0, places=1)

    def test_time_of_day_calculation_midnight(self):
        tracker = WorldStatsTracker(auto_load=False)
        # Day 15, exact midnight (fraction 0.0 -> 00:00)
        tracker._saved_net_time = 15 * 1800.0
        tracker._saved_net_time_mtime = 1000000.0

        with patch("time.time", return_value=1000000.0):
            data = tracker.get_time_of_day_data(server_running=True, players_count=1)

        self.assertEqual(data["current_day"], 15)
        self.assertEqual(data["time_24h"], "00:00")
        self.assertEqual(data["time_12h"], "12:00 AM")
        self.assertFalse(data["is_day"])
        self.assertEqual(data["phase"], "Night")
        self.assertEqual(data["phase_icon"], "🌙")
        self.assertEqual(data["next_event_name"], "Sunrise")
        self.assertEqual(data["next_event_icon"], "🌄")
        self.assertEqual(data["next_event_time"], "06:00")
        # 0.25 fraction remaining until sunrise -> 450s = 7m 30s
        self.assertEqual(data["seconds_to_event"], 450)
        self.assertEqual(data["time_to_event_str"], "7m 30s")
        self.assertEqual(data["next_event_text"], "Sunrise in 7m 30s")
        # Night elapsed: 0.25 of 0.50 -> 50%
        self.assertAlmostEqual(data["phase_progress_pct"], 50.0, places=1)

    def test_time_of_day_calculation_sunrise_and_sunset_boundaries(self):
        tracker = WorldStatsTracker(auto_load=False)

        # Sunrise at fraction 0.25 (06:00)
        tracker._saved_net_time = (20 + 0.25) * 1800.0
        tracker._saved_net_time_mtime = 1000.0
        with patch("time.time", return_value=1000.0):
            data_dawn = tracker.get_time_of_day_data(server_running=True, players_count=1)
        self.assertEqual(data_dawn["time_24h"], "06:00")
        self.assertTrue(data_dawn["is_day"])
        self.assertEqual(data_dawn["phase"], "Day")
        self.assertEqual(data_dawn["next_event_name"], "Sunset")
        self.assertEqual(data_dawn["seconds_to_event"], 900)  # 15 minutes of daytime

        # Sunset at fraction 0.75 (18:00)
        tracker._saved_net_time = (20 + 0.75) * 1800.0
        with patch("time.time", return_value=1000.0):
            data_dusk = tracker.get_time_of_day_data(server_running=True, players_count=1)
        self.assertEqual(data_dusk["time_24h"], "18:00")
        self.assertFalse(data_dusk["is_day"])
        self.assertEqual(data_dusk["phase"], "Night")
        self.assertEqual(data_dusk["next_event_name"], "Sunrise")
        self.assertEqual(data_dusk["seconds_to_event"], 900)  # 15 minutes of nighttime

    def test_time_freezes_when_no_players_or_stopped(self):
        tracker = WorldStatsTracker(auto_load=False)
        tracker._saved_net_time = 100000.0
        tracker._saved_net_time_mtime = 1000.0

        # When running with 0 players, elapsed time (500s) does NOT advance net_time
        with patch("time.time", return_value=1500.0):
            data_zero_players = tracker.get_time_of_day_data(server_running=True, players_count=0)
            self.assertEqual(data_zero_players["net_time"], 100000.0)
            self.assertTrue(data_zero_players["is_frozen"])

            data_stopped = tracker.get_time_of_day_data(server_running=False, players_count=2)
            self.assertEqual(data_stopped["net_time"], 100000.0)
            self.assertTrue(data_stopped["is_frozen"])

            # When running with >0 players, time advances by 500s
            data_active = tracker.get_time_of_day_data(server_running=True, players_count=1)
            self.assertEqual(data_active["net_time"], 100500.0)
            self.assertFalse(data_active["is_frozen"])

    def test_partials_status_renders_in_game_time_and_countdown(self):
        with patch("app.get_current_user_from_request", return_value={"username": "drewfus", "role": "admin"}):
            resp = self.client.get("/partials/status")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("In-Game Time", resp.text)
            self.assertIn("in-game-time-box", resp.text)
            # Must mention either Sunset in or Sunrise in
            self.assertTrue("Sunset in" in resp.text or "Sunrise in" in resp.text)

    def test_partials_world_summary_renders_in_game_clock_and_celestial_bar(self):
        with patch("app.get_current_user_from_request", return_value={"username": "drewfus", "role": "admin"}):
            resp = self.client.get("/partials/world-summary")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("In-Game Time & Day", resp.text)
            self.assertIn("celestial-cycle-bar", resp.text)
            self.assertTrue("Sunset in" in resp.text or "Sunrise in" in resp.text)

    def test_partials_header_status_renders_in_game_time(self):
        resp = self.client.get("/partials/header-status")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("status-pill", resp.text)
        # Server is running, check in-game time rendered
        self.assertTrue("☀️" in resp.text or "🌙" in resp.text)

    def test_api_status_exposes_time_of_day(self):
        with patch("app.get_current_user_from_request", return_value={"username": "drewfus", "role": "admin"}):
            resp = self.client.get("/api/status")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            world_stats = data.get("world_stats", {})
            self.assertIn("time_of_day", world_stats)
            tod = world_stats["time_of_day"]
            self.assertIn("time_24h", tod)
            self.assertIn("phase", tod)
            self.assertIn("next_event_text", tod)
            self.assertIn("day_fraction", tod)


if __name__ == "__main__":
    unittest.main()
