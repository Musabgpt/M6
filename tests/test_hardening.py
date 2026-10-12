"""Hardening added after the first review: output redaction, undo, responsiveness, PWA files."""

import json
import os
import tempfile
import threading
import time
import unittest
import urllib.request

from newal_mobile.providers import FallbackChain, ProviderError
from newal_mobile.server import AppState, make_server
from newal_mobile.tools import ToolBox, redact_secrets

from .helpers import ScriptedProvider, call, text


class RedactionTests(unittest.TestCase):
    def test_secret_assignments_and_tokens_are_redacted(self):
        samples = {
            "API_KEY=abc123xyz": "abc123xyz",
            "export GROQ_TOKEN: gsk_abcdefghijklmnop": "gsk_abcdefghijklmnop",
            "Authorization: Bearer abcdefghijklmnop12345": "abcdefghijklmnop12345",
            "key sk-proj-ABCDEFGHIJKLMNOP": "sk-proj-ABCDEFGHIJKLMNOP",
            "-----BEGIN RSA PRIVATE KEY-----\nMIIB\n-----END RSA PRIVATE KEY-----": "MIIB",
        }
        for raw, secret in samples.items():
            with self.subTest(raw=raw):
                out = redact_secrets(raw)
                self.assertIn("[REDACTED]", out)
                self.assertNotIn(secret, out)

    def test_ordinary_code_text_is_left_alone(self):
        code = "def get_user():\n    return 1  # token count\n"
        self.assertEqual(redact_secrets(code), code)

    def test_command_output_is_redacted_before_it_reaches_the_model(self):
        with tempfile.TemporaryDirectory() as d:
            box = ToolBox(os.path.realpath(d), command_timeout_s=5)
            r = box.execute("run_command", {"command": "echo API_KEY=supersecret999"})
            result = box.approve(r["approval_id"], allow=True)
            self.assertTrue(result["ok"])
            self.assertIn("[REDACTED]", result["output"])
            self.assertNotIn("supersecret999", result["output"])


class UndoTests(unittest.TestCase):
    def setUp(self):
        self._ws = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._ws.name)
        self.box = ToolBox(self.root)

    def tearDown(self):
        self._ws.cleanup()

    def read(self, rel):
        with open(os.path.join(self.root, rel), encoding="utf-8") as handle:
            return handle.read()

    def test_accepted_edit_can_be_undone(self):
        with open(os.path.join(self.root, "a.py"), "w", encoding="utf-8") as handle:
            handle.write("return 1\n")
        r = self.box.execute("propose_edit", {"path": "a.py", "old_text": "return 1", "new_text": "return 2"})
        self.box.accept_change(r["change_id"])
        self.assertIn("return 2", self.read("a.py"))
        self.assertTrue(self.box.revert_change(r["change_id"])["ok"])
        self.assertEqual(self.read("a.py"), "return 1\n")

    def test_undo_of_a_created_file_removes_it(self):
        r = self.box.execute("propose_write", {"path": "new.txt", "content": "hi\n"})
        self.box.accept_change(r["change_id"])
        self.assertTrue(self.box.revert_change(r["change_id"])["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "new.txt")))

    def test_undo_refused_when_the_file_changed_since(self):
        r = self.box.execute("propose_write", {"path": "f.txt", "content": "first\n"})
        self.box.accept_change(r["change_id"])
        with open(os.path.join(self.root, "f.txt"), "w", encoding="utf-8") as handle:
            handle.write("someone else's edit\n")
        result = self.box.revert_change(r["change_id"])
        self.assertFalse(result["ok"])
        self.assertEqual(self.read("f.txt"), "someone else's edit\n")

    def test_only_accepted_changes_can_be_undone(self):
        r = self.box.execute("propose_write", {"path": "p.txt", "content": "x\n"})
        self.assertFalse(self.box.revert_change(r["change_id"])["ok"])
        self.box.accept_change(r["change_id"])
        self.assertTrue(self.box.revert_change(r["change_id"])["ok"])
        self.assertFalse(self.box.revert_change(r["change_id"])["ok"])


class SlowProvider:
    name = "slow"
    privacy_tier = "test"

    def chat(self, messages, tools):
        time.sleep(1.5)
        return {"role": "assistant", "content": "slow answer"}


class ResponsivenessAndHttpTests(unittest.TestCase):
    TOKEN = "hardening-token-0123456789abcdefghijklmnop"

    def setUp(self):
        self._ws = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self._ws.cleanup()

    def start(self, provider):
        state = AppState(self._ws.name, self.TOKEN, FallbackChain([provider]), command_timeout_s=5)
        self.state = state
        self.server = make_server("127.0.0.1", 0, state)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            "Authorization": "Bearer " + self.TOKEN, "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def test_status_stays_fast_while_a_model_call_is_running(self):
        self.start(SlowProvider())
        _, run = self.request("POST", "/api/runs", {"goal": "slow question"})
        worker = threading.Thread(target=self.request, args=("POST", f"/api/runs/{run['id']}/advance", {}))
        worker.start()
        time.sleep(0.2)  # the model call is now in progress
        started = time.monotonic()
        status, _ = self.request("POST", "/api/runs", {"goal": "a second, unrelated run"})
        elapsed = time.monotonic() - started
        worker.join()
        self.assertEqual(status, 201)
        self.assertLess(elapsed, 1.0, f"creating another run took {elapsed:.2f}s during a model call")

    def test_file_api_stays_fast_while_an_approved_command_runs(self):
        self.start(ScriptedProvider([call("run_command", {"command": "sleep 2"}), text("done")]))
        _, run = self.request("POST", "/api/runs", {"goal": "sleep"})
        _, snap = self.request("POST", f"/api/runs/{run['id']}/advance", {})
        approval_id = snap["pending_approval"]["approval_id"]
        worker = threading.Thread(target=self.request, args=("POST", f"/api/runs/{run['id']}/approval",
                                                             {"approval_id": approval_id, "allow": True}))
        worker.start()
        time.sleep(0.3)  # the command is now running
        started = time.monotonic()
        status, _ = self.request("GET", "/api/changes")
        elapsed = time.monotonic() - started
        worker.join()
        self.assertEqual(status, 200)
        self.assertLess(elapsed, 1.0, f"listing changes took {elapsed:.2f}s while a command was running")

    def test_undo_over_http(self):
        self.start(ScriptedProvider([call("propose_write", {"path": "u.txt", "content": "x\n"}), text("ok")]))
        _, run = self.request("POST", "/api/runs", {"goal": "make u.txt"})
        self.request("POST", f"/api/runs/{run['id']}/advance", {})
        change_id = self.request("GET", "/api/changes")[1]["changes"][0]["id"]
        self.assertTrue(self.request("POST", f"/api/changes/{change_id}/accept", {})[1]["ok"])
        status, body = self.request("POST", f"/api/changes/{change_id}/revert", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.state.workspace, "u.txt")))

    def test_manifest_and_icon_are_served_and_allowed_by_csp(self):
        self.start(ScriptedProvider([]))
        with urllib.request.urlopen(self.base + "/manifest.webmanifest", timeout=10) as resp:
            manifest = json.loads(resp.read())
            self.assertEqual(resp.status, 200)
            self.assertEqual(manifest["display"], "standalone")
            self.assertIn("manifest-src 'self'", resp.headers["Content-Security-Policy"])
        with urllib.request.urlopen(self.base + "/icon.svg", timeout=10) as resp:
            self.assertEqual(resp.headers["Content-Type"], "image/svg+xml")


if __name__ == "__main__":
    unittest.main()
