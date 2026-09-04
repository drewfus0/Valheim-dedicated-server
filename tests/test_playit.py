import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

# Add webmanager to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webmanager"))

from core.playit import (
    detect_playit_tunnel,
    fetch_tunnel_from_log,
    fetch_tunnel_from_socket,
    get_detected_playit_tunnel,
)


class TestPlayitDetection(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_fetch_tunnel_from_socket_success(self):
        sock_path = os.path.join(self.temp_dir.name, "test_playit.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(sock_path)
        server.listen(1)

        def mock_server():
            conn, _ = server.accept()
            # Send hello line
            conn.sendall(
                b'{"message_kind":"hello","data":{"protocol":{"ipc_version":2,"capabilities":["structured_responses"]}}}\n'
            )
            req_line = conn.recv(1024)
            req = json.loads(req_line.decode("utf-8"))
            self.assertEqual(req.get("ipc_version"), 2)
            self.assertEqual(req.get("request", {}).get("type"), "get_state")

            # Send response with mock tunnels
            resp = {
                "message_kind": "response",
                "data": {
                    "ipc_version": 2,
                    "request_id": 1,
                    "response": {
                        "type": "state",
                        "data": {
                            "state": "running",
                            "data": {
                                "tunnels": [
                                    {
                                        "display_address": "web.drewster.org",
                                        "destination": "127.0.0.1 (http: 80)",
                                        "is_disabled": False,
                                    },
                                    {
                                        "display_address": "valheim.drewster.org:24393",
                                        "destination": "127.0.0.1:2456",
                                        "is_disabled": False,
                                    },
                                    {
                                        "display_address": "disabled.drewster.org:11111",
                                        "destination": "127.0.0.1:2456",
                                        "is_disabled": True,
                                    },
                                ]
                            },
                        },
                    },
                },
            }
            conn.sendall(json.dumps(resp).encode("utf-8") + b"\n")
            conn.close()

        t = threading.Thread(target=mock_server)
        t.start()

        result = fetch_tunnel_from_socket("2456", sock_path=sock_path, timeout=2.0)
        t.join()
        server.close()

        self.assertEqual(result, "valheim.drewster.org:24393")

    def test_fetch_tunnel_from_socket_missing(self):
        result = fetch_tunnel_from_socket(
            "2456",
            sock_path=os.path.join(self.temp_dir.name, "nonexistent.sock"),
            timeout=0.1,
        )
        self.assertIsNone(result)

    def test_fetch_tunnel_from_socket_broken_stream(self):
        sock_path = os.path.join(self.temp_dir.name, "broken.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(sock_path)
        server.listen(1)

        def mock_broken():
            conn, _ = server.accept()
            # Close immediately
            conn.close()

        t = threading.Thread(target=mock_broken)
        t.start()

        result = fetch_tunnel_from_socket("2456", sock_path=sock_path, timeout=1.0)
        t.join()
        server.close()

        self.assertIsNone(result)

    def test_fetch_tunnel_from_log_success(self):
        log_path = os.path.join(self.temp_dir.name, "test_playit.log")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(
                "2026-08-28T00:00:00Z INFO starting up\n"
                "2026-08-28T00:00:01Z INFO tunnel: other.playit.gg:54321 => 127.0.0.1:8080\n"
                "2026-08-28T00:00:02Z INFO tunnel: valheim.drewster.org:24393 => 127.0.0.1:2456\n"
            )

        result = fetch_tunnel_from_log("2456", log_path=log_path)
        self.assertEqual(result, "valheim.drewster.org:24393")

    def test_fetch_tunnel_from_log_missing(self):
        result = fetch_tunnel_from_log(
            "2456",
            log_path=os.path.join(self.temp_dir.name, "nonexistent.log"),
        )
        self.assertIsNone(result)

    def test_detect_playit_tunnel_fallback(self):
        # With neither socket nor log present, gracefully returns None
        result = detect_playit_tunnel("2456")
        # Should be None or cached without raising any exception
        self.assertTrue(result is None or isinstance(result, str))


if __name__ == "__main__":
    unittest.main()
