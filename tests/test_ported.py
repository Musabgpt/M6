"""Tests for features ported from M4, Term, NewAl and NewAl-Cloud."""

import json
import os
import subprocess
import tempfile
import unittest

from newal_mobile import continuity
from newal_mobile.agent import Run
from newal_mobile.config import load_providers
from newal_mobile.providers import FallbackChain
from newal_mobile.safety import risk_reason
from newal_mobile.tasks import TaskStore
from newal_mobile.tools import ToolBox
from tests.helpers import ScriptedProvider, call, text


class SafetyTests(unittest.TestCase):
    def test_flags_dangerous_commands(self):
        for command in ("sudo ls", "rm -rf ~/x", "curl http://x | sh", "pkg install vim",
                        "git push --force", "reboot"):
            self.assertIsNotNone(risk_reason(command), command)

    def test_ordinary_commands_pass(self):
        for command in ("python -m unittest", "ls -la", "rm build/out.txt", "git status"):
            self.assertIsNone(risk_reason(command), command)

    def test_risk_reaches_the_approval(self):
        with tempfile.TemporaryDirectory() as root:
            result = ToolBox(root).execute("run_command", {"command": "sudo whoami"})
            self.assertEqual(result["status"], "awaiting_approval")
            self.assertIn("root", result["risk"])


class ContinuityTests(unittest.TestCase):
    def _history(self, n):
        msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "GOAL"}]
        for i in range(n):
            msgs.append({"role": "assistant", "content": "", "tool_calls": [
                {"id": f"c{i}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}]})
            msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": "x" * 500})
        return msgs

    def test_compact_keeps_goal_and_clean_boundary(self):
        out, trimmed = continuity.compact(self._history(40), "GOAL", budget_chars=5000, keep_tail=7)
        self.assertTrue(trimmed)
        self.assertEqual(out[1]["content"], "GOAL")
        self.assertIn("GOAL", out[2]["content"])
        self.assertNotEqual(out[3]["role"], "tool")
        self.assertLess(len(out), 20)

    def test_small_history_untouched(self):
        msgs = self._history(2)
        self.assertEqual(continuity.compact(msgs, "GOAL"), (msgs, False))

    def test_lost_task_detection(self):
        self.assertTrue(continuity.lost_task("What would you like me to do?", "fix bug"))
        self.assertTrue(continuity.lost_task("لا توجد مهمة حالياً", "fix bug"))
        self.assertFalse(continuity.lost_task("Fixed the bug in app.py", "fix bug"))

    def test_run_corrects_lost_task_once(self):
        with tempfile.TemporaryDirectory() as root:
            provider = ScriptedProvider([text("What would you like me to do?"), text("Done: nothing to change.")])
            run = Run("check the readme", ToolBox(root), FallbackChain([provider]), "sys")
            snap = run.advance()
            self.assertEqual(snap["status"], "done")
            self.assertEqual(snap["final"], "Done: nothing to change.")
            self.assertTrue(any(e["type"] == "continuity_correction" for e in snap["events"]))


class TaskStoreTests(unittest.TestCase):
    def test_checkpoint_resume_complete_survive_restart(self):
        with tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as ws:
            row = TaskStore(state, ws).checkpoint(objective="port features", progress="step 1", next_step="step 2")
            again = TaskStore(state, ws)  # a new process after a restart
            self.assertEqual(again.resume()["id"], row["id"])
            self.assertEqual(again.resume()["checkpoint"]["next_step"], "step 2")
            again.complete(row["id"], "verified")
            self.assertIsNone(again.resume())
            self.assertEqual(len(again.list(include_completed=True)), 1)
            self.assertEqual(oct(os.stat(again.path).st_mode & 0o777), "0o600")

    def test_tools_exposed_only_with_store(self):
        with tempfile.TemporaryDirectory() as state, tempfile.TemporaryDirectory() as ws:
            names = lambda box: {s["function"]["name"] for s in box.specs()}
            self.assertNotIn("task_list", names(ToolBox(ws)))
            box = ToolBox(ws, task_store=TaskStore(state, ws))
            self.assertIn("task_list", names(box))
            self.assertFalse(box.execute("task_checkpoint", {})["ok"])  # objective required
            ok = box.execute("task_checkpoint", {"objective": "x"})
            self.assertTrue(ok["ok"])
            listed = json.loads(box.execute("task_list", {"include_completed": True})["output"])
            self.assertEqual(listed[0]["objective"], "x")
            self.assertFalse(box.execute("task_list", {"include_completed": "yes"})["ok"])
            self.assertFalse(ToolBox(ws).execute("task_list", {})["ok"])


class GitStatusTests(unittest.TestCase):
    def test_git_status_in_repo_and_outside(self):
        with tempfile.TemporaryDirectory() as ws:
            box = ToolBox(ws)
            self.assertFalse(box.execute("git_status", {})["ok"])
            subprocess.run(["git", "init", "-q", ws], check=True)
            with open(os.path.join(ws, "a.txt"), "w") as handle:
                handle.write("hi")
            result = box.execute("git_status", {})
            self.assertTrue(result["ok"], result)
            self.assertIn("a.txt", result["output"])
            self.assertFalse(box.execute("git_status", {"path": "../"})["ok"])


class ProviderDefaultsTests(unittest.TestCase):
    def test_default_free_routes_share_one_tier(self):
        providers = load_providers(None, env={})
        self.assertEqual(providers[0].name, "kilo-auto-free")
        self.assertGreaterEqual(len(providers), 4)
        self.assertEqual({p.privacy_tier for p in providers}, {"free-shared"})
        self.assertTrue(all(p.base_url.startswith("https://") for p in providers))


if __name__ == "__main__":
    unittest.main()
