"""HTTP layer: auth, host check, full run over HTTP, approval and change review over HTTP."""

import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request

from newal_mobile.providers import FallbackChain
from newal_mobile.server import AppState, make_server

from .helpers import ScriptedProvider, call, text

TOKEN = "test-token-0123456789abcdefghijklmnop"


class ServerTestCase(unittest.TestCase):
    def start(self, script):
        self._ws = tempfile.TemporaryDirectory()
        state = AppState(self._ws.name, TOKEN, FallbackChain([ScriptedProvider(script)]), command_timeout_s=5)
        self.workspace = state.workspace
        self.server = make_server("127.0.0.1", 0, state)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
        if hasattr(self, "_ws"):
            self._ws.cleanup()

    def request(self, method, path, body=None, token=TOKEN, host=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            req.add_header("Content-Type", "application/json")
        if token is not None:
            req.add_header("Authorization", "Bearer " + token)
        if host is not None:
            req.add_header("Host", host)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                return response.status, json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")


class AuthTests(ServerTestCase):
    def test_index_page_is_public_and_contains_the_app(self):
        self.start([])
        with urllib.request.urlopen(self.base + "/", timeout=10) as response:
            page = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("NewAl Mobile", page)
            self.assertIn("default-src 'none'", response.headers["Content-Security-Policy"])

    def test_api_requires_a_valid_token(self):
        self.start([])
        self.assertEqual(self.request("GET", "/api/status", token=None)[0], 401)
        self.assertEqual(self.request("GET", "/api/status", token="wrong")[0], 401)
        status, body = self.request("GET", "/api/status")
        self.assertEqual(status, 200)
        self.assertEqual(body["providers"], ["scripted"])

    def test_foreign_host_header_is_refused(self):
        self.start([])
        status, body = self.request("GET", "/api/status", host="evil.example:8787")
        self.assertEqual(status, 403)

    def test_unknown_route_is_404(self):
        self.start([])
        self.assertEqual(self.request("GET", "/api/nope")[0], 404)


class FlowTests(ServerTestCase):
    def test_full_run_over_http(self):
        self.start([text("hello from the model")])
        status, run = self.request("POST", "/api/runs", {"goal": "say hi"})
        self.assertEqual(status, 201)
        status, snap = self.request("POST", f"/api/runs/{run['id']}/advance", {})
        self.assertEqual(status, 200)
        self.assertEqual(snap["status"], "done")
        self.assertEqual(snap["final"], "hello from the model")

    def test_empty_goal_is_rejected(self):
        self.start([])
        self.assertEqual(self.request("POST", "/api/runs", {"goal": "   "})[0], 400)
        self.assertEqual(self.request("POST", "/api/runs", {"goal": 5})[0], 400)

    def test_approval_flow_over_http(self):
        self.start([call("run_command", {"command": "echo ok > made.txt"}), text("done")])
        _, run = self.request("POST", "/api/runs", {"goal": "make a file"})
        _, snap = self.request("POST", f"/api/runs/{run['id']}/advance", {})
        self.assertEqual(snap["status"], "awaiting_approval")
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "made.txt")))
        approval_id = snap["pending_approval"]["approval_id"]
        status, snap = self.request("POST", f"/api/runs/{run['id']}/approval",
                                    {"approval_id": approval_id, "allow": True})
        self.assertEqual(status, 200)
        self.assertEqual(snap["status"], "done")
        self.assertTrue(os.path.exists(os.path.join(self.workspace, "made.txt")))

    def test_approval_requires_a_real_boolean(self):
        self.start([call("run_command", {"command": "echo x"})])
        _, run = self.request("POST", "/api/runs", {"goal": "x"})
        self.request("POST", f"/api/runs/{run['id']}/advance", {})
        status, _ = self.request("POST", f"/api/runs/{run['id']}/approval",
                                 {"approval_id": "abc", "allow": "yes"})
        self.assertEqual(status, 400)

    def test_change_review_over_http(self):
        self.start([call("propose_write", {"path": "n.txt", "content": "x\n"}), text("proposed")])
        _, run = self.request("POST", "/api/runs", {"goal": "make n.txt"})
        self.request("POST", f"/api/runs/{run['id']}/advance", {})
        _, listing = self.request("GET", "/api/changes")
        change = listing["changes"][0]
        self.assertEqual(change["status"], "pending")
        self.assertIn("+x", change["diff"])
        self.assertFalse(os.path.exists(os.path.join(self.workspace, "n.txt")))
        status, result = self.request("POST", f"/api/changes/{change['id']}/accept", {})
        self.assertTrue(result["ok"])
        with open(os.path.join(self.workspace, "n.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "x\n")

    def test_path_traversal_through_the_file_api_is_blocked(self):
        self.start([])
        _, body = self.request("GET", "/api/file?path=../../../../etc/passwd")
        self.assertFalse(body["ok"])
        self.assertNotIn("root:", body["output"])


if __name__ == "__main__":
    unittest.main()
