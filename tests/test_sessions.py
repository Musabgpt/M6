"""Sessions: follow-up turns, stop, task list, auto-apply mode, allow-always, persistence, glob, NEWAL.md."""

import json
import os
import tempfile
import threading
import time
import unittest

from newal_mobile.agent import Run, RunError, _trimmed, MAX_CONTEXT_CHARS
from newal_mobile.providers import FallbackChain
from newal_mobile.server import AppState, make_server
from newal_mobile.tools import ToolBox

from .helpers import ScriptedProvider, call, text

SYSTEM = "SYSTEM RULES"


class SessionTests(unittest.TestCase):
    def setUp(self):
        self._ws = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._ws.name)
        self.box = ToolBox(self.root, command_timeout_s=5)

    def tearDown(self):
        self._ws.cleanup()

    def write(self, rel, data):
        full = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as handle:
            handle.write(data)

    def make_run(self, script, **kwargs):
        provider = ScriptedProvider(script)
        return Run("first request", self.box, FallbackChain([provider]), SYSTEM, **kwargs), provider

    def test_follow_up_turn_continues_the_same_conversation(self):
        run, provider = self.make_run([text("first answer"), text("second answer")])
        self.assertEqual(run.advance()["final"], "first answer")
        run.add_user_message("and now this")
        snap = run.advance()
        self.assertEqual(snap["final"], "second answer")
        roles = [m["role"] for m in run.messages]
        self.assertEqual(roles, ["system", "user", "assistant", "user", "assistant"])
        self.assertEqual([e["text"] for e in snap["events"] if e["type"] == "user"],
                         ["first request", "and now this"])

    def test_follow_up_is_refused_while_working(self):
        run, _ = self.make_run([call("run_command", {"command": "echo hi"})])
        run.advance()
        self.assertEqual(run.status, "awaiting_approval")
        with self.assertRaises(RunError):
            run.add_user_message("too early")

    def test_step_budget_is_per_turn(self):
        script = [call("list_dir", {"path": "."}), text("a"), call("list_dir", {"path": "."}), text("b")]
        run, _ = self.make_run(script, max_steps=2)
        self.assertEqual(run.advance()["status"], "done")
        run.add_user_message("again")
        self.assertEqual(run.advance()["final"], "b")
        self.assertEqual(run.steps, 4)

    def test_stop_while_waiting_for_approval_denies_and_stops(self):
        run, _ = self.make_run([call("run_command", {"command": "touch made.txt"})])
        run.advance()
        snap = run.cancel()
        self.assertEqual(snap["status"], "stopped")
        self.assertFalse(os.path.exists(os.path.join(self.root, "made.txt")))
        self.assertEqual(run.messages[-1]["role"], "tool")  # transcript stays valid
        run.add_user_message("continue")  # a stopped session accepts a new turn

    def test_stop_requested_during_a_model_call_stops_before_tools(self):
        run, _ = self.make_run([])
        gate = threading.Event()

        class SlowProvider:
            providers = []

            def complete(self, messages, tools):
                run.working = True
                run.cancel()      # the user presses Stop while the model is thinking
                gate.set()
                return call("run_command", {"command": "touch never.txt"}), "slow"

        run.provider = SlowProvider()
        snap = run.advance()
        self.assertTrue(gate.is_set())
        self.assertEqual(snap["status"], "stopped")
        self.assertFalse(os.path.exists(os.path.join(self.root, "never.txt")))
        self.assertEqual(run.messages[-1]["role"], "tool")

    def test_todo_write_updates_the_visible_task_list(self):
        todos = [{"content": "read code", "status": "completed"},
                 {"content": "fix bug", "status": "in_progress"},
                 {"content": "run tests", "status": "bogus"}]
        run, _ = self.make_run([call("todo_write", {"todos": todos}), text("ok")])
        snap = run.advance()
        self.assertEqual([t["status"] for t in snap["todos"]], ["completed", "in_progress", "pending"])
        self.assertIn("todos", [e["type"] for e in snap["events"]])
        self.assertIn("1/3 completed", run.messages[3]["content"])

    def test_accept_edits_mode_applies_proposals_immediately(self):
        self.write("a.py", "x = 1\n")
        settings = {"mode": "accept_edits"}
        run, _ = self.make_run([call("propose_edit", {"path": "a.py", "old_text": "x = 1", "new_text": "x = 2"}),
                                text("done")], settings=settings)
        snap = run.advance()
        with open(os.path.join(self.root, "a.py"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "x = 2\n")
        tool = next(e for e in snap["events"] if e["type"] == "tool")
        self.assertTrue(tool["auto_applied"])
        self.assertIn("applied change", run.messages[3]["content"])
        # Undo still works on auto-applied changes.
        self.assertTrue(self.box.revert_change(tool["change_id"])["ok"])

    def test_ask_mode_keeps_proposals_pending(self):
        self.write("a.py", "x = 1\n")
        run, _ = self.make_run([call("propose_edit", {"path": "a.py", "old_text": "x = 1", "new_text": "x = 2"}),
                                text("done")])
        run.advance()
        with open(os.path.join(self.root, "a.py"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "x = 1\n")

    def test_allow_always_skips_approval_for_the_same_exact_command_only(self):
        script = [call("run_command", {"command": "echo one >> log.txt"}), text("first"),
                  call("run_command", {"command": "echo one >> log.txt"}),
                  call("run_command", {"command": "echo two >> log.txt"})]
        run, _ = self.make_run(script)
        snap = run.advance()
        run.resolve_approval(snap["pending_approval"]["approval_id"], True, always=True)
        self.assertEqual(run.status, "done")
        run.add_user_message("again")
        snap = run.advance()
        self.assertEqual(snap["status"], "awaiting_approval")   # the different command still asks
        self.assertEqual(snap["pending_approval"]["command"], "echo two >> log.txt")
        with open(os.path.join(self.root, "log.txt"), encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "one\none\n")
        self.assertIn("auto_approved", [e["type"] for e in snap["events"]])

    def test_round_trip_through_disk_closes_open_turns(self):
        run, _ = self.make_run([call("run_command", {"command": "echo hi"})])
        run.advance()
        data = json.loads(json.dumps(run.to_dict()))
        restored = Run.from_dict(data, self.box, FallbackChain([ScriptedProvider([text("resumed")])]), "NEW SYSTEM")
        self.assertEqual(restored.status, "stopped")
        self.assertEqual(restored.messages[0]["content"], "NEW SYSTEM")
        self.assertEqual(restored.messages[-1]["role"], "tool")
        restored.add_user_message("go on")
        self.assertEqual(restored.advance()["final"], "resumed")

    def test_context_trimming_keeps_recent_messages(self):
        big = "x" * (MAX_CONTEXT_CHARS // 4)
        messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
        for i in range(6):
            messages.append({"role": "tool", "tool_call_id": str(i), "content": big})
        messages += [{"role": "user", "content": "recent"}] * 8
        trimmed = _trimmed(messages)
        self.assertLessEqual(sum(len(m["content"]) for m in trimmed), MAX_CONTEXT_CHARS)
        self.assertEqual(trimmed[-8:], messages[-8:])
        self.assertEqual(len(messages[2]["content"]), len(big))  # original untouched

    def test_glob_files(self):
        self.write("a.py", "")
        self.write("src/b.py", "")
        self.write("src/deep/c.py", "")
        self.write("src/d.js", "")
        self.write("node_modules/x.py", "")
        out = self.box.execute("glob_files", {"pattern": "**/*.py"})["output"].splitlines()
        self.assertEqual(sorted(out), ["a.py", os.path.join("src", "b.py"), os.path.join("src", "deep", "c.py")])
        out = self.box.execute("glob_files", {"pattern": "*.js", "path": "src"})["output"]
        self.assertEqual(out, os.path.join("src", "d.js"))
        self.assertFalse(self.box.execute("glob_files", {"pattern": "*", "path": "../"})["ok"])


TOKEN = "test-token-0123456789abcdefghijklmnop"


class SessionServerTests(unittest.TestCase):
    def setUp(self):
        self._ws = tempfile.TemporaryDirectory()
        self._state = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._ws.cleanup()
        self._state.cleanup()

    def state(self, script):
        return AppState(self._ws.name, TOKEN, FallbackChain([ScriptedProvider(script)]),
                        command_timeout_s=5, sessions_dir=os.path.join(self._state.name, "sessions"))

    def test_sessions_survive_a_restart(self):
        app = self.state([text("hello")])
        run = app.create_run("hi")
        app.advance(run["id"])
        again = self.state([text("second")])
        listed = again.list_runs()["runs"]
        self.assertEqual([r["id"] for r in listed], [run["id"]])
        self.assertEqual(again.get_run(run["id"])["final"], "hello")
        again.add_message(run["id"], {"text": "more"})
        self.assertEqual(again.advance(run["id"])["final"], "second")
        mode = os.stat(os.path.join(self._state.name, "sessions", run["id"] + ".json")).st_mode & 0o777
        self.assertEqual(mode, 0o600)

    def test_background_advance_reports_progress_then_finishes(self):
        app = self.state([text("bg done")])
        run = app.create_run("hi")
        app.advance(run["id"], background=True)
        for _ in range(100):
            snap = app.get_run(run["id"])
            if snap["status"] == "done" and not snap["working"]:
                break
            time.sleep(0.02)
        self.assertEqual(snap["final"], "bg done")

    def test_project_notes_are_added_to_the_system_prompt(self):
        with open(os.path.join(self._ws.name, "NEWAL.md"), "w", encoding="utf-8") as handle:
            handle.write("Use tabs.")
        app = self.state([text("ok")])
        run = app.create_run("hi")
        self.assertIn("Use tabs.", app.runs[run["id"]].messages[0]["content"])
        self.assertTrue(app.status()["project_notes"])

    def test_settings_validate_mode(self):
        app = self.state([])
        self.assertEqual(app.update_settings({"mode": "accept_edits"})["mode"], "accept_edits")
        from newal_mobile.server import ApiError
        with self.assertRaises(ApiError):
            app.update_settings({"mode": "yolo"})

    def test_http_routes_for_sessions(self):
        import urllib.request
        app = self.state([text("one"), text("two")])
        server = make_server("127.0.0.1", 0, app)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def req(method, path, body=None):
            data = None if body is None else json.dumps(body).encode()
            r = urllib.request.Request(base + path, data=data, method=method,
                                       headers={"Authorization": "Bearer " + TOKEN,
                                                "Content-Type": "application/json"})
            with urllib.request.urlopen(r, timeout=10) as resp:
                return json.loads(resp.read())
        try:
            run = req("POST", "/api/runs", {"goal": "hi"})
            req("POST", f"/api/runs/{run['id']}/advance", {})
            req("POST", f"/api/runs/{run['id']}/messages", {"text": "next"})
            snap = req("POST", f"/api/runs/{run['id']}/advance", {})
            self.assertEqual(snap["final"], "two")
            self.assertEqual(len(req("GET", "/api/runs")["runs"]), 1)
            self.assertEqual(req("POST", "/api/settings", {"mode": "accept_edits"})["mode"], "accept_edits")
            self.assertEqual(req("POST", f"/api/runs/{run['id']}/cancel", {})["status"], "done")
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
