import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# Add webmanager to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webmanager"))

from fastapi.testclient import TestClient
from app import app


class TestPlayersTab(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_partials_players_default_and_custom_id(self):
        with patch("app.get_current_user_from_request", return_value={"username": "drewfus", "role": "admin"}):
            # Default panel_id
            resp = self.client.get("/partials/players")
            self.assertEqual(resp.status_code, 200)
            self.assertIn('id="players-panel"', resp.text)
            self.assertIn("Viking Roster & Activity", resp.text)

            # Custom panel_id
            resp_custom = self.client.get("/partials/players?panel_id=custom-players-panel")
            self.assertEqual(resp_custom.status_code, 200)
            self.assertIn('id="custom-players-panel"', resp_custom.text)
            self.assertIn("panel_id=custom-players-panel", resp_custom.text)

    def test_partials_players_tab(self):
        with patch("app.get_current_user_from_request", return_value={"username": "drewfus", "role": "admin"}):
            resp = self.client.get("/partials/players-tab")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("Realm Adventurers Directory", resp.text)
            self.assertIn("Online Vikings", resp.text)
            self.assertIn("Viking Session History", resp.text)

    def test_index_renders_both_tabs(self):
        with patch("app.get_current_user_from_request", return_value={"username": "drewfus", "role": "admin"}):
            resp = self.client.get("/")
            self.assertEqual(resp.status_code, 200)
            # Check nav tabs has Players
            self.assertIn("switchTab('tab-players', this)", resp.text)
            self.assertIn("🛡️ Players", resp.text)
            # Check tab-players panel exists
            self.assertIn('id="tab-players"', resp.text)
            # Check dashboard still has Viking Roster & Activity
            self.assertIn('id="tab-dashboard"', resp.text)
            self.assertIn("Viking Roster & Activity", resp.text)

    def test_tabs_url_bookmarking_support(self):
        with patch("app.get_current_user_from_request", return_value={"username": "drewfus", "role": "admin"}):
            resp = self.client.get("/")
            self.assertEqual(resp.status_code, 200)
            self.assertIn('data-tab="tab-dashboard"', resp.text)
            self.assertIn('data-tab="tab-players"', resp.text)
            self.assertIn('data-tab="tab-backups"', resp.text)
            self.assertIn('data-tab="tab-config"', resp.text)
            self.assertIn('data-tab="tab-logs"', resp.text)
            self.assertIn('data-tab="tab-accounts"', resp.text)
            self.assertIn('TAB_SLUGS', resp.text)
            self.assertIn('getTabFromUrl', resp.text)
            self.assertIn('syncTabFromUrl', resp.text)

    def test_viking_session_history_admin_access(self):
        with patch("app.get_current_user_from_request", return_value={"username": "drewfus", "role": "admin"}):
            resp = self.client.get("/partials/players-tab")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("Viking Session History", resp.text)
            self.assertIn("Logged Sessions", resp.text)
            self.assertIn("admin-only", resp.text)

            resp_hist = self.client.get("/partials/session-history")
            self.assertEqual(resp_hist.status_code, 200)
            self.assertIn("Viking Session History", resp_hist.text)
            self.assertIn('class="card admin-only"', resp_hist.text)

            resp_acc = self.client.get("/partials/accounts")
            self.assertEqual(resp_acc.status_code, 200)
            self.assertIn('class="card admin-only"', resp_acc.text)

    def test_viking_session_history_non_admin_hidden(self):
        for non_admin_role in ["operator", "viewer"]:
            with patch("app.get_current_user_from_request", return_value={"username": "player1", "role": non_admin_role}):
                resp = self.client.get("/partials/players-tab")
                self.assertEqual(resp.status_code, 200)
                self.assertNotIn("Viking Session History", resp.text)
                self.assertNotIn("Logged Sessions", resp.text)

                resp_hist = self.client.get("/partials/session-history")
                self.assertEqual(resp_hist.status_code, 403)
                self.assertIn("Access Restricted to Administrators", resp_hist.text)

                resp_status = self.client.get("/api/status")
                self.assertEqual(resp_status.status_code, 200)
                data = resp_status.json()
                self.assertEqual(data.get("player_sessions"), [])


if __name__ == "__main__":
    unittest.main()
