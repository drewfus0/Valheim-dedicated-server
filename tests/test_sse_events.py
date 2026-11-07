import asyncio
import json
import re
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

# Add webmanager to sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "webmanager"
sys.path.insert(0, str(WEB_DIR))

from fastapi.testclient import TestClient
from app import app, make_toast_headers
from core.events import EVENT_BUS, EventBus, format_sse_frame


class TestSSEZeroPollingTemplates(unittest.TestCase):
    """Static & rendered audit verifying zero HTMX polling intervals remain."""

    def test_no_every_polling_or_from_body_in_any_template(self):
        templates_dir = WEB_DIR / "templates"
        html_files = sorted(templates_dir.rglob("*.html"))
        self.assertGreater(len(html_files), 10)

        hx_trigger_pattern = re.compile(r'hx-trigger="([^"]*)"')
        for html_file in html_files:
            content = html_file.read_text(encoding="utf-8")
            for match in hx_trigger_pattern.finditer(content):
                trigger_val = match.group(1)
                self.assertNotIn(
                    "every ",
                    trigger_val,
                    f"Polling trigger found in {html_file.relative_to(PROJECT_ROOT)}: {trigger_val}",
                )
                self.assertNotIn(
                    "from:body",
                    trigger_val,
                    f"Stale local-only from:body trigger found in {html_file.relative_to(PROJECT_ROOT)}: {trigger_val}",
                )

    def test_rendered_partials_use_sse_triggers_and_data_telemetry(self):
        client = TestClient(app)
        with patch("app.get_current_user_from_request", return_value={"username": "drewfus", "role": "admin"}):
            endpoints = [
                "/",
                "/partials/status",
                "/partials/header-status",
                "/partials/players",
                "/partials/players-tab",
                "/partials/session-history",
                "/partials/server-history",
                "/partials/connection",
                "/partials/backups",
                "/partials/accounts",
                "/partials/world-summary",
                "/partials/world-saga",
                "/partials/raid-alert",
                "/partials/tabs/timeline",
            ]
            for url in endpoints:
                resp = client.get(url)
                self.assertEqual(resp.status_code, 200, f"Unexpected status for {url}")
                for match in re.finditer(r'hx-trigger="([^"]*)"', resp.text):
                    self.assertNotIn("every ", match.group(1), f"Polling trigger rendered in {url}")

            # Check data-telemetry hooks exist on status and header partials
            resp_status = client.get("/partials/status")
            self.assertIn('data-telemetry="cpu"', resp_status.text)
            self.assertIn('data-telemetry="mem"', resp_status.text)
            self.assertIn('data-telemetry="uptime"', resp_status.text)
            self.assertIn('data-telemetry="tod-24h"', resp_status.text)

            resp_saga = client.get("/partials/world-saga")
            self.assertIn('data-telemetry="tod-day-time"', resp_saga.text)
            self.assertIn('data-telemetry="tod-badge"', resp_saga.text)

            resp_summary = client.get("/partials/world-summary")
            self.assertIn('data-telemetry="tod-day-time"', resp_summary.text)
            self.assertIn('data-telemetry="tod-phase-bar"', resp_summary.text)

    def test_sse_containers_do_not_leak_hx_target_to_children(self):
        """Ensure SSE-triggered wrapper divs never set hx-target (which HTMX inherits onto child elements like .timeline-sentinel)."""
        templates_dir = WEB_DIR / "templates"
        tag_pattern = re.compile(r"<[a-zA-Z0-9]+\b[^>]*\bhx-trigger=\"sse:[^\"]*\"[^>]*>", re.S)
        for html_file in sorted(templates_dir.rglob("*.html")):
            content = html_file.read_text(encoding="utf-8")
            for match in tag_pattern.finditer(content):
                tag = match.group(0)
                self.assertNotIn(
                    "hx-target=",
                    tag,
                    f"SSE container sets inheritable hx-target in {html_file.relative_to(PROJECT_ROOT)}: {tag}",
                )

        chunk_html = (templates_dir / "partials" / "timeline_chunk.html").read_text(encoding="utf-8")
        self.assertIn('hx-target="this"', chunk_html)


class TestEventBusUnit(unittest.TestCase):
    """Unit tests for EventBus pub/sub, thread-safety, Last-Event-ID replay, and shutdown."""

    def test_multi_subscriber_fanout_and_thread_publish(self):
        async def scenario():
            bus = EventBus(history_maxlen=32, queue_maxsize=16)
            q1, h1 = bus.subscribe()
            q2, h2 = bus.subscribe()
            try:
                self.assertTrue(bus.has_subscribers())
                self.assertEqual(bus.subscriber_count(), 2)

                # Publish from a background OS thread (like ValheimServerManager._monitor_loop)
                done = threading.Event()

                def bg_worker():
                    bus.publish_many(["statusChanged", "playersUpdated", "statusChanged"])
                    bus.publish("telemetry", {"cpu": 12.5, "server_running": True})
                    done.set()

                t = threading.Thread(target=bg_worker)
                t.start()
                done.wait(timeout=2.0)
                t.join(timeout=2.0)

                # Allow call_soon_threadsafe callbacks to flush on the loop
                await asyncio.sleep(0.05)

                # publish_many deduplicated duplicate "statusChanged", so 2 discrete + 1 telemetry = 3 frames each
                frames_1 = [await asyncio.wait_for(q1.get(), timeout=1.0) for _ in range(3)]
                frames_2 = [await asyncio.wait_for(q2.get(), timeout=1.0) for _ in range(3)]
                self.assertEqual(frames_1, frames_2)
                self.assertIn("id: 1\nevent: statusChanged\n", frames_1[0])
                self.assertIn("id: 2\nevent: playersUpdated\n", frames_1[1])
                self.assertIn("event: telemetry\n", frames_1[2])
                self.assertNotIn("id:", frames_1[2])
            finally:
                bus.unsubscribe(h1)
                bus.unsubscribe(h2)
                self.assertFalse(bus.has_subscribers())

        asyncio.run(scenario())

    def test_last_event_id_replay_and_deduplication(self):
        bus = EventBus(history_maxlen=10)
        id1 = bus.publish("statusChanged")
        bus.publish("telemetry", {"cpu": 5.0})  # Ephemeral: must not increment ID or replay
        id2 = bus.publish("playersUpdated")
        id3 = bus.publish("statusChanged")  # Newer statusChanged supersedes id1

        self.assertEqual((id1, id2, id3), (1, 2, 3))

        missed = bus.get_missed_frames(0)
        # Should contain playersUpdated (id 2) and statusChanged (id 3), without telemetry or duplicate statusChanged
        self.assertEqual(len(missed), 2)
        self.assertIn("id: 2\nevent: playersUpdated\n", missed[0])
        self.assertIn("id: 3\nevent: statusChanged\n", missed[1])

        # Caught-up client gets nothing
        self.assertEqual(bus.get_missed_frames(3), [])

    def test_ring_buffer_overflow_replays_full_refresh_set(self):
        bus = EventBus(history_maxlen=3)
        for _ in range(6):
            bus.publish("playersUpdated")
        # Client reconnecting from id 1 fell behind the 3-entry ring buffer
        missed = bus.get_missed_frames(1)
        self.assertGreaterEqual(len(missed), 9)
        joined = "".join(missed)
        self.assertIn("event: statusChanged", joined)
        self.assertIn("event: configUpdated", joined)
        self.assertIn("event: mapUpdated", joined)

    def test_signal_shutdown_wakes_stream_immediately(self):
        async def scenario():
            bus = EventBus()
            gen = bus.event_stream_generator(
                request=None,
                initial_telemetry_fn=lambda: {"server_running": False, "cpu": 0.0},
                keepalive_seconds=30.0,
            )
            first = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
            self.assertIn("event: telemetry", first)
            second = await asyncio.wait_for(gen.__anext__(), timeout=1.0)
            self.assertIn("event: connected", second)

            # Trigger shutdown after 30ms; generator must exit well before 30s keepalive
            asyncio.get_running_loop().call_later(0.03, bus.signal_shutdown)
            with self.assertRaises(StopAsyncIteration):
                await asyncio.wait_for(gen.__anext__(), timeout=1.0)
            self.assertFalse(bus.has_subscribers())

        asyncio.run(scenario())


class TestSSEEndpointAndBroadcasts(unittest.TestCase):
    """Integration tests for /api/stream/events auth, framing, and mutation broadcasts."""

    def setUp(self):
        EVENT_BUS.reset_shutdown()
        self.client = TestClient(app)

    def test_stream_events_requires_authentication(self):
        with patch("app.get_current_user_from_request", return_value=None):
            resp = self.client.get("/api/stream/events")
            self.assertEqual(resp.status_code, 401)

    def test_stream_events_emits_initial_telemetry_and_live_broadcast(self):
        async def scenario():
            class FakeDisconnectAfterLiveRequest:
                def __init__(self):
                    self.calls = 0

                async def is_disconnected(self):
                    self.calls += 1
                    if self.calls == 1:
                        EVENT_BUS.publish("configUpdated", {"source": "unit-test"})
                        return False
                    return True

            gen = EVENT_BUS.event_stream_generator(
                request=FakeDisconnectAfterLiveRequest(),
                initial_telemetry_fn=lambda: {
                    "server_now": 1700000000.0,
                    "server_running": True,
                    "cpu": 14.2,
                    "mem": "2.40 GB",
                    "world_clock": {"net_time": 3600.0, "net_time_ref": 1700000000.0, "current_day": 2, "nights_slept": 1, "is_frozen": False},
                },
                keepalive_seconds=5.0,
            )
            frames = []
            async for chunk in gen:
                frames.append(chunk)

            self.assertEqual(len(frames), 3)
            self.assertIn("event: telemetry\n", frames[0])
            self.assertIn('"mem": "2.40 GB"', frames[0])
            self.assertIn("event: connected\n", frames[1])
            self.assertIn("event: configUpdated\n", frames[2])
            self.assertIn('"source": "unit-test"', frames[2])

        asyncio.run(scenario())

    def test_make_toast_headers_isolates_toast_and_broadcasts_sse(self):
        before_id = EVENT_BUS.current_event_id()
        headers = make_toast_headers(
            "Configuration saved!",
            "success",
            configUpdated=True,
            statusChanged=True,
        )
        after_id = EVENT_BUS.current_event_id()

        # HX-Trigger should ONLY carry showToast for the initiating tab
        hx_payload = json.loads(headers["HX-Trigger"])
        self.assertEqual(list(hx_payload.keys()), ["showToast"])
        self.assertEqual(hx_payload["showToast"]["value"], "Configuration saved!")

        # Meanwhile, both configUpdated and statusChanged were published onto EVENT_BUS
        self.assertEqual(after_id, before_id + 2)
        missed = EVENT_BUS.get_missed_frames(before_id)
        joined = "".join(missed)
        self.assertIn("event: configUpdated", joined)
        self.assertIn("event: statusChanged", joined)

    def test_mutating_endpoints_publish_sse_events(self):
        admin_user = {"username": "drewfus", "role": "admin"}
        with patch("app.get_current_user_from_request", return_value=admin_user), \
             patch("app.require_auth", return_value=None):
            # 1. Hide player
            start_id = EVENT_BUS.current_event_id()
            with patch("app.PLAYER_TRACKER.hide_player", return_value=True):
                resp = self.client.post("/api/players/hide/Ragnar")
                self.assertEqual(resp.status_code, 200)
                missed = "".join(EVENT_BUS.get_missed_frames(start_id))
                self.assertIn("event: playersUpdated", missed)
                self.assertIn("event: sessionHistoryUpdated", missed)

            # 2. Record boss defeat
            start_id = EVENT_BUS.current_event_id()
            with patch("app.WORLD_STATS.record_boss_defeat", return_value=True):
                resp = self.client.post("/api/boss/record-defeat/eikthyr")
                self.assertEqual(resp.status_code, 200)
                missed = "".join(EVENT_BUS.get_missed_frames(start_id))
                self.assertIn("event: worldSagaUpdated", missed)
                self.assertIn("event: raidAlertUpdated", missed)
                self.assertIn("event: timelineUpdated", missed)

            # 3. Clear login history
            start_id = EVENT_BUS.current_event_id()
            with patch("app.clear_login_history", return_value=None):
                resp = self.client.post("/api/users/login-history/clear")
                self.assertEqual(resp.status_code, 200)
                missed = "".join(EVENT_BUS.get_missed_frames(start_id))
                self.assertIn("event: accountsUpdated", missed)

            # 4. Refresh map data
            start_id = EVENT_BUS.current_event_id()
            with patch("app.get_world_map_data", return_value={"world_name": "Dedicated", "portals": [], "stats": {"total_portals": 0}}):
                resp = self.client.post("/api/map/refresh")
                self.assertEqual(resp.status_code, 200)
                missed = "".join(EVENT_BUS.get_missed_frames(start_id))
                self.assertIn("event: worldSagaUpdated", missed)
                self.assertIn("event: mapUpdated", missed)


if __name__ == "__main__":
    unittest.main()
