"""Sandbox, tool validation, change review and approval gating."""

import os
import pathlib
import tempfile
import time
import unittest

from newal_mobile.sandbox import SandboxError, resolve_in_workspace
from newal_mobile.tools import ToolBox


class ToolTestCase(unittest.TestCase):
    def setUp(self):
        self._ws = tempfile.TemporaryDirectory()
        self._outside = tempfile.TemporaryDirectory()
        self.root = os.path.realpath(self._ws.name)
        self.box = ToolBox(self.root, command_timeout_s=2)

    def tearDown(self):
        self._ws.cleanup()
        self._outside.cleanup()

    def write(self, rel, data, mode="w"):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, mode, **({} if "b" in mode else {"encoding": "utf-8"})) as handle:
            handle.write(data)
        return path

    def read(self, rel):
        with open(os.path.join(self.root, rel), encoding="utf-8") as handle:
            return handle.read()


class SandboxTests(ToolTestCase):
    def test_absolute_and_home_paths_are_rejected(self):
        for bad in ("/etc/passwd", "~/secret"):
            with self.assertRaises(SandboxError):
                resolve_in_workspace(self.root, bad)

    def test_dotdot_escape_is_rejected(self):
        r = self.box.execute("read_file", {"path": "../../etc/passwd"})
        self.assertFalse(r["ok"])
        self.assertIn("escapes", r["output"])

    def test_symlink_escape_is_rejected(self):
        with open(os.path.join(self._outside.name, "secret.txt"), "w", encoding="utf-8") as handle:
            handle.write("top secret")
        os.symlink(self._outside.name, os.path.join(self.root, "link"))
        r = self.box.execute("read_file", {"path": "link/secret.txt"})
        self.assertFalse(r["ok"])
        self.assertIn("escapes", r["output"])
        self.assertNotIn("top secret", r["output"])

    def test_root_itself_is_allowed(self):
        self.assertEqual(resolve_in_workspace(self.root, "."), pathlib.Path(self.root))


class ReadOnlyToolTests(ToolTestCase):
    def test_list_dir_sorts_folders_first_and_hides_git(self):
        self.write("a.txt", "x")
        os.makedirs(os.path.join(self.root, "src"))
        os.makedirs(os.path.join(self.root, ".git"))
        r = self.box.execute("list_dir", {"path": "."})
        lines = r["output"].split("\n")
        self.assertTrue(r["ok"])
        self.assertEqual(lines[0], "src/")
        self.assertIn("a.txt", lines)
        self.assertNotIn(".git/", lines)

    def test_read_file_numbers_lines_and_honours_range(self):
        self.write("f.txt", "one\ntwo\nthree\n")
        r = self.box.execute("read_file", {"path": "f.txt", "start_line": 2, "end_line": 3})
        self.assertEqual(r["output"], "2: two\n3: three")

    def test_read_file_refuses_binary(self):
        self.write("bin.dat", b"\x00\x01\x02", mode="wb")
        r = self.box.execute("read_file", {"path": "bin.dat"})
        self.assertFalse(r["ok"])
        self.assertIn("binary", r["output"])

    def test_search_finds_text_and_skips_git(self):
        self.write("a.txt", "find the needle here\n")
        self.write(".git/config", "needle in git\n")
        r = self.box.execute("search_text", {"text": "needle"})
        self.assertTrue(r["ok"])
        self.assertIn("a.txt:1:", r["output"])
        self.assertNotIn(".git", r["output"])

    def test_invalid_arguments_are_rejected_before_running(self):
        cases = [
            ("read_file", {}, "missing argument: path"),
            ("read_file", {"path": "a", "extra": 1}, "unknown argument: extra"),
            ("read_file", {"path": 5}, "path must be a string"),
            ("read_file", {"path": "a", "start_line": True}, "start_line must be an integer"),
            ("read_file", "not-an-object", "arguments must be a JSON object"),
            ("delete_everything", {}, "unknown tool"),
        ]
        for name, args, expected in cases:
            with self.subTest(name=name, args=args):
                r = self.box.execute(name, args)
                self.assertFalse(r["ok"])
                self.assertIn(expected, r["output"])


class ChangeReviewTests(ToolTestCase):
    def test_edit_is_only_a_proposal_until_accepted(self):
        self.write("app.py", "def a():\n    return 1\n")
        r = self.box.execute("propose_edit", {"path": "app.py", "old_text": "return 1", "new_text": "return 2"})
        self.assertTrue(r["ok"])
        self.assertIn("not applied", r["output"])
        self.assertIn("return 1", self.read("app.py"))
        diff = self.box.list_changes()[0]["diff"]
        self.assertIn("-    return 1", diff)
        self.assertIn("+    return 2", diff)
        self.assertTrue(self.box.accept_change(r["change_id"])["ok"])
        self.assertIn("return 2", self.read("app.py"))

    def test_rejected_change_leaves_file_alone(self):
        self.write("app.py", "keep\n")
        r = self.box.execute("propose_edit", {"path": "app.py", "old_text": "keep", "new_text": "drop"})
        self.assertTrue(self.box.reject_change(r["change_id"])["ok"])
        self.assertEqual(self.read("app.py"), "keep\n")
        self.assertEqual(self.box.list_changes()[0]["status"], "rejected")

    def test_edit_needs_exactly_one_match(self):
        self.write("f.txt", "x\nx\n")
        r = self.box.execute("propose_edit", {"path": "f.txt", "old_text": "x", "new_text": "y"})
        self.assertFalse(r["ok"])
        self.assertIn("found 2", r["output"])
        self.assertEqual(self.box.list_changes(), [])

    def test_write_creates_missing_folders_on_accept(self):
        r = self.box.execute("propose_write", {"path": "new/deep/hello.txt", "content": "hi\n"})
        self.assertTrue(r["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "new")))
        self.assertTrue(self.box.accept_change(r["change_id"])["ok"])
        self.assertEqual(self.read("new/deep/hello.txt"), "hi\n")

    def test_file_changed_after_proposal_is_a_conflict(self):
        self.write("f.txt", "a")
        r = self.box.execute("propose_edit", {"path": "f.txt", "old_text": "a", "new_text": "b"})
        self.write("f.txt", "external edit")  # someone else changed the file
        result = self.box.accept_change(r["change_id"])
        self.assertFalse(result["ok"])
        self.assertEqual(self.read("f.txt"), "external edit")
        self.assertEqual(self.box.list_changes()[0]["status"], "conflict")

    def test_no_op_write_is_refused(self):
        self.write("same.txt", "same")
        r = self.box.execute("propose_write", {"path": "same.txt", "content": "same"})
        self.assertFalse(r["ok"])


class SensitiveFileTests(ToolTestCase):
    def test_env_and_key_files_are_not_read(self):
        self.write(".env", "API_KEY=abc\n")
        self.write("deploy/server.pem", "-----BEGIN PRIVATE KEY-----\n")
        for rel in (".env", "deploy/server.pem"):
            with self.subTest(rel=rel):
                r = self.box.execute("read_file", {"path": rel})
                self.assertFalse(r["ok"])
                self.assertIn("secrets", r["output"])
                self.assertNotIn("abc", r["output"])

    def test_search_skips_sensitive_files(self):
        self.write(".env", "TOKEN_VALUE=needle\n")
        self.write("app.py", "needle here\n")
        r = self.box.execute("search_text", {"text": "needle"})
        self.assertIn("app.py", r["output"])
        self.assertNotIn(".env", r["output"])

    def test_sensitive_files_cannot_be_edited_by_proposal(self):
        self.write(".env", "A=1\n")
        r = self.box.execute("propose_edit", {"path": ".env", "old_text": "A=1", "new_text": "A=2"})
        self.assertFalse(r["ok"])
        r = self.box.execute("propose_write", {"path": ".env", "content": "A=3\n"})
        self.assertFalse(r["ok"])
        self.assertEqual(self.box.list_changes(), [])


class CommandApprovalTests(ToolTestCase):
    def test_command_never_runs_before_approval(self):
        marker = os.path.join(self.root, "marker.txt")
        r = self.box.execute("run_command", {"command": "echo ran > marker.txt"})
        self.assertEqual(r["status"], "awaiting_approval")
        self.assertFalse(os.path.exists(marker))

    def test_denied_command_does_not_run(self):
        marker = os.path.join(self.root, "marker.txt")
        r = self.box.execute("run_command", {"command": "echo ran > marker.txt"})
        result = self.box.approve(r["approval_id"], allow=False)
        self.assertFalse(result["ok"])
        self.assertFalse(os.path.exists(marker))

    def test_approved_command_runs_once_and_reports_exit_code(self):
        marker = os.path.join(self.root, "marker.txt")
        r = self.box.execute("run_command", {"command": "echo ran > marker.txt"})
        result = self.box.approve(r["approval_id"], allow=True)
        self.assertTrue(result["ok"])
        self.assertIn("exit code 0", result["output"])
        self.assertTrue(os.path.exists(marker))
        again = self.box.approve(r["approval_id"], allow=True)  # the same approval cannot run twice
        self.assertFalse(again["ok"])

    def test_timeout_kills_the_command(self):
        box = ToolBox(self.root, command_timeout_s=1)
        r = box.execute("run_command", {"command": "sleep 30"})
        started = time.monotonic()
        result = box.approve(r["approval_id"], allow=True)
        self.assertFalse(result["ok"])
        self.assertIn("timed out", result["output"])
        self.assertLess(time.monotonic() - started, 10)

    def test_catastrophic_pattern_is_blocked_even_before_approval(self):
        r = self.box.execute("run_command", {"command": "rm -rf /"})
        self.assertFalse(r["ok"])
        self.assertIn("blocked", r["output"])


if __name__ == "__main__":
    unittest.main()
