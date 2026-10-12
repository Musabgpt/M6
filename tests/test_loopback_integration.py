"""Full-stack check over real sockets: a loopback OpenAI-compatible model server, the real
provider client (urllib + JSON), the real agent loop, and the real HTTP API.

The mock model is rule-based, so this verifies wire formats and control flow, not model quality.
"""

import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from newal_mobile.providers import FallbackChain, OpenAICompatibleProvider
from newal_mobile.server import AppState, make_server

TOKEN = "integration-token-0123456789abcdefghijk"


def mock_decide(messages):
    """Pick the next assistant turn from the conversation so far."""
    last = messages[-1]
    if last["role"] == "user":
        return {"role": "assistant", "content": "", "tool_calls": [{
            "id": "m1", "type": "function",
            "function": {"name": "read_file", "arguments": json.dumps({"path": "app.py"})}}]}
    if last["role"] == "tool" and "return 1" in last["content"]:
        return {"role": "assistant", "content": "", "tool_calls": [{
            "id": "m2", "type": "function",
            "function": {"name": "propose_edit", "arguments": json.dumps(
                {"path": "app.py", "old_text": "return 1", "new_text": "return 2"})}}]}
    if last["role"] == "tool" and "change_id" in last["content"]:
        return {"role": "assistant", "content": "I proposed the change; you can review it."}
    return {"role": "assistant", "content": "I could not follow the expected path."}


class MockModelHandler(BaseHTTPRequestHandler):
    seen_auth = []

    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        MockModelHandler.seen_auth.append(self.headers.get("Authorization"))
        assert body["tools"], "tools must be sent"
        message = mock_decide(body["messages"])
        payload = json.dumps({"choices": [{"message": message}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class LoopbackIntegrationTest(unittest.TestCase):
    def setUp(self):
        self._ws = tempfile.TemporaryDirectory()
        self.model = ThreadingHTTPServer(("127.0.0.1", 0), MockModelHandler)
        threading.Thread(target=self.model.serve_forever, daemon=True).start()
        model_url = f"http://127.0.0.1:{self.model.server_address[1]}/v1"
        provider = OpenAICompatibleProvider("mock-local", model_url, "mock-1", api_key="mock-key",
                                            privacy_tier="local")
        state = AppState(self._ws.name, TOKEN, FallbackChain([provider]), command_timeout_s=5)
        with open(os.path.join(state.workspace, "app.py"), "w", encoding="utf-8") as handle:
            handle.write("def a():\n    return 1\n")
        self.workspace = state.workspace
        self.server = make_server("127.0.0.1", 0, state)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        for srv in (self.server, self.model):
            srv.shutdown()
            srv.server_close()
        self._ws.cleanup()

    def call(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers={"Authorization": "Bearer " + TOKEN,
                                              "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())

    def test_read_propose_review_accept_over_real_sockets(self):
        run = self.call("POST", "/api/runs", {"goal": "change return 1 to return 2"})
        snap = self.call("POST", f"/api/runs/{run['id']}/advance", {})
        self.assertEqual(snap["status"], "done", snap)
        types = [e["type"] for e in snap["events"]]
        self.assertEqual(types.count("tool"), 2)
        self.assertIn("Bearer mock-key", MockModelHandler.seen_auth)

        changes = self.call("GET", "/api/changes")["changes"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["status"], "pending")
        with open(os.path.join(self.workspace, "app.py"), encoding="utf-8") as handle:
            self.assertIn("return 1", handle.read())  # still untouched before acceptance

        result = self.call("POST", f"/api/changes/{changes[0]['id']}/accept", {})
        self.assertTrue(result["ok"])
        with open(os.path.join(self.workspace, "app.py"), encoding="utf-8") as handle:
            self.assertIn("return 2", handle.read())


if __name__ == "__main__":
    unittest.main()
