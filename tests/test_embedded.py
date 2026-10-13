"""The APK entry point: starts the engine in-process and serves the UI with the token."""

import json
import os
import socket
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from newal_mobile import embedded


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class EmbeddedTests(unittest.TestCase):
    def tearDown(self):
        embedded.stop()

    def test_start_serves_api_and_ui(self):
        ui = Path(__file__).resolve().parent.parent / "ui"
        token = "t" * 64
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as ws:
            port = _free_port()
            self.assertEqual(embedded.start(home, ws, str(ui), token, port), "ok")
            self.assertEqual(embedded.start(home, ws, str(ui), token, port), "ok")  # idempotent
            base = f"http://127.0.0.1:{port}"
            req = urllib.request.Request(base + "/api/status", headers={"Authorization": "Bearer " + token})
            with urllib.request.urlopen(req, timeout=5) as resp:
                self.assertEqual(json.loads(resp.read())["workspace"], os.path.realpath(ws))
            with urllib.request.urlopen(base + "/", timeout=5) as resp:
                self.assertIn(b"<html", resp.read())
            with self.assertRaises(urllib.error.HTTPError):
                urllib.request.urlopen(base + "/api/status", timeout=5)

    def test_rejects_short_token(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(embedded.start(d, d, d, "short", _free_port()), "token is too short")


if __name__ == "__main__":
    unittest.main()
