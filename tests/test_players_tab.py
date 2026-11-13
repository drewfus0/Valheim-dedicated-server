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
            self.assertIn('data-tab="tab-players"', resp.text)
            # Check dashboard still has Viking Roster & Activity
            self.assertIn('id="tab-dashboard"', resp.text)
            self.assertIn("Viking Roster & Activity", resp.text)

            # Check lazy-loaded tab-players partial
            resp_tab = self.client.get("/partials/players-tab")
            self.assertEqual(resp_tab.status_code, 200)
            self.assertIn('id="tab-players"', resp_tab.text)

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

    def test_event_driven_panels_no_polling_timers(self):
        with patch("app.get_current_user_from_request", return_value={"username": "drewfus", "role": "admin"}):
            # 1. Roster - SSE event-driven, no polling timers
            resp_players = self.client.get("/partials/players")
            self.assertEqual(resp_players.status_code, 200)
            self.assertIn("sse:playersUpdated delay:80ms", resp_players.text)
            self.assertIn("sse:statusChanged delay:80ms", resp_players.text)
            self.assertNotIn("every ", resp_players.text)

            # 2. Connection - SSE event-driven, no polling timers
            resp_conn = self.client.get("/partials/connection")
            self.assertEqual(resp_conn.status_code, 200)
            self.assertIn('hx-trigger="sse:configUpdated delay:80ms"', resp_conn.text)
            self.assertNotIn("every ", resp_conn.text)

            # 3. Accounts - SSE event-driven, no polling timers
            resp_acc = self.client.get("/partials/accounts")
            self.assertEqual(resp_acc.status_code, 200)
            self.assertIn('hx-trigger="sse:accountsUpdated delay:80ms"', resp_acc.text)
            self.assertNotIn("every ", resp_acc.text)

            # 4. Server History - SSE event-driven, no polling timers
            resp_srv = self.client.get("/partials/server-history")
            self.assertEqual(resp_srv.status_code, 200)
            self.assertIn('hx-trigger="sse:serverHistoryUpdated delay:80ms, sse:statusChanged delay:80ms"', resp_srv.text)
            self.assertNotIn("every ", resp_srv.text)

            # 5. Viking Session History - SSE event-driven, no polling timers
            resp_sess = self.client.get("/partials/session-history")
            self.assertEqual(resp_sess.status_code, 200)
            self.assertIn('hx-trigger="sse:sessionHistoryUpdated delay:80ms, sse:statusChanged delay:80ms"', resp_sess.text)
            self.assertNotIn("every ", resp_sess.text)

            # 6. Base layout & header status badge - SSE event-driven, zero polling timers
            resp_base = self.client.get("/")
            self.assertEqual(resp_base.status_code, 200)
            self.assertIn('sse-connect="/api/stream/events"', resp_base.text)
            self.assertIn('hx-trigger="sse:statusChanged delay:80ms"', resp_base.text)
            self.assertNotIn('hx-trigger="every ', resp_base.text)

            # 7. Status card - SSE event-driven, zero polling timers
            resp_stat = self.client.get("/partials/status")
            self.assertEqual(resp_stat.status_code, 200)
            self.assertIn('hx-trigger="sse:statusChanged delay:80ms"', resp_stat.text)
            self.assertNotIn("every 2s", resp_stat.text)

            # 8. Terminal retains separate sse-connect for logs
            self.assertIn('sse-connect="/api/stream/logs"', resp_base.text)


if __name__ == "__main__":
    unittest.main()
