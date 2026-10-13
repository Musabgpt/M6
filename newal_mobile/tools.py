"""Workspace tools for the phone agent.

Rules enforced in code, not only in the prompt:
- every path goes through the sandbox (no absolute paths, no ".." or symlink escapes);
- file writes are proposals; an accepted proposal is re-checked against the file on disk first;
- shell commands never start before the user approves them.
"""

from __future__ import annotations

import contextlib
import difflib
import fnmatch
import os
import re
import signal
import stat
import subprocess
import tempfile
import threading
import uuid
from dataclasses import dataclass

from .sandbox import SandboxError, resolve_in_workspace

MAX_READ_BYTES = 200_000
MAX_WRITE_BYTES = 500_000
MAX_OUTPUT_CHARS = 12_000
MAX_LIST_ENTRIES = 500
SEARCH_MAX_HITS = 200
GLOB_MAX_HITS = 300
SEARCH_MAX_FILE_BYTES = 1_000_000
DEFAULT_COMMAND_TIMEOUT_S = 60
SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", ".gradle", "build"})

# Best-effort guard only. Per-command approval is the real control.
HARD_BLOCKED = ("rm -rf /", "rm -rf ~", "mkfs", ":(){", "dd if=/dev/zero of=/dev/")

# Files that usually hold secrets. They are never read, searched or edited by the agent,
# because anything the agent reads is sent to the model provider (protocol law L14).
SENSITIVE_PREFIXES = (".env",)
SENSITIVE_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".keystore", ".jks", ".kdbx")
SENSITIVE_NAMES = frozenset({"id_rsa", "id_ed25519", "id_ecdsa", ".netrc", ".npmrc", ".pypirc",
                             "credentials", "secrets.json", "token"})
SENSITIVE_MESSAGE = "this file looks like it holds secrets, so the agent does not read or edit it"


def is_sensitive_name(name: str) -> bool:
    lower = name.lower()
    return (lower.startswith(SENSITIVE_PREFIXES) or lower.endswith(SENSITIVE_SUFFIXES)
            or lower in SENSITIVE_NAMES)

TOOL_SPECS = [
    {"type": "function", "function": {
        "name": "list_dir",
        "description": "List files and folders in a workspace directory. Folders end with '/'.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Relative directory path. Use '.' for the root."}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "read_file",
        "description": "Read a UTF-8 text file from the workspace, optionally only a line range (1-based, inclusive).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"}},
            "required": ["path"]}}},
    {"type": "function", "function": {
        "name": "search_text",
        "description": "Find a literal text string in workspace files. Skips .git, binaries and symlinks.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"},
            "path": {"type": "string", "description": "Directory to search. Defaults to '.'."}},
            "required": ["text"]}}},
    {"type": "function", "function": {
        "name": "glob_files",
        "description": "Find files by name pattern, e.g. '**/*.py', 'src/**/test_*.js' or '*.md'. Returns relative paths.",
        "parameters": {"type": "object", "properties": {
            "pattern": {"type": "string"},
            "path": {"type": "string", "description": "Directory to search. Defaults to '.'."}},
            "required": ["pattern"]}}},
    {"type": "function", "function": {
        "name": "propose_edit",
        "description": "Propose replacing exactly one occurrence of old_text with new_text in an existing file. Not applied until the user accepts.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"}},
            "required": ["path", "old_text", "new_text"]}}},
    {"type": "function", "function": {
        "name": "propose_write",
        "description": "Propose creating a new file or fully replacing an existing one. Not applied until the user accepts.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"}},
            "required": ["path", "content"]}}},
    {"type": "function", "function": {
        "name": "run_command",
        "description": "Run a bash command in the workspace. The user must approve it first; its output is unavailable until then.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string"}},
            "required": ["command"]}}},
]

_PARAMS = {spec["function"]["name"]: spec["function"]["parameters"] for spec in TOOL_SPECS}


class ToolRefusal(Exception):
    """A tool declined to act. The message goes back to the model as data."""


@dataclass
class Change:
    id: str
    path: str
    kind: str                 # "edit" or "write"
    old: str | None           # None means the file did not exist when proposed
    new: str
    status: str = "pending"   # pending | accepted | rejected | conflict | failed


@dataclass
class Approval:
    id: str
    command: str
    status: str = "pending"   # pending | approved | denied | done
    result: dict | None = None


# Best-effort redaction of command output before the model sees it (MX-B06). File contents are
# deliberately NOT redacted: rewriting code would break exact-text edits. Files with secret-like
# names are blocked instead (see SENSITIVE_NAMES).
_SECRET_ASSIGN = re.compile(r"(?i)\b([A-Z0-9_]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*)(\s*[=:]\s*)(\S+)")
_SECRET_BEARER = re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}")
_SECRET_TOKENS = re.compile(
    r"\b(sk-[A-Za-z0-9_-]{10,}|gsk_[A-Za-z0-9]{10,}|ghp_[A-Za-z0-9]{10,}|github_pat_[A-Za-z0-9_]{10,}"
    r"|AIza[0-9A-Za-z_-]{20,}|AKIA[0-9A-Z]{16})\b")
_SECRET_PEM = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----")


def redact_secrets(text: str) -> str:
    text = _SECRET_ASSIGN.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", text)
    text = _SECRET_BEARER.sub(lambda m: m.group(1) + "[REDACTED]", text)
    text = _SECRET_TOKENS.sub("[REDACTED]", text)
    return _SECRET_PEM.sub("[REDACTED]", text)


def _ok(output: str, **extra) -> dict:
    return {"ok": True, "output": _truncate(output), **extra}


def _err(message: str) -> dict:
    return {"ok": False, "output": _truncate(message)}


def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n...[truncated {len(text) - MAX_OUTPUT_CHARS} chars]"


def _validate(name: str, args) -> str | None:
    params = _PARAMS[name]
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    for key in params["required"]:
        if key not in args:
            return f"missing argument: {key}"
    for key, value in args.items():
        spec = params["properties"].get(key)
        if spec is None:
            return f"unknown argument: {key}"
        if spec["type"] == "string" and not isinstance(value, str):
            return f"{key} must be a string"
        if spec["type"] == "integer" and (isinstance(value, bool) or not isinstance(value, int)):
            return f"{key} must be an integer"
    return None


def _read_text(path) -> str:
    if path.stat().st_size > MAX_READ_BYTES:
        raise ToolRefusal("file is larger than 200 KB")
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise ToolRefusal("binary files are not supported")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ToolRefusal("file is not valid UTF-8") from None


def _atomic_write(target, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o644
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".newal-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        os.chmod(tmp, mode)
        os.replace(tmp, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def _run_shell(command: str, cwd: str, timeout: int) -> dict:
    try:
        proc = subprocess.Popen(["bash", "-c", command], cwd=cwd, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                start_new_session=True)
    except FileNotFoundError:
        return _err("bash is not available")
    except OSError as exc:
        return _err(f"could not start command: {exc.strerror or type(exc).__name__}")
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)  # kills the whole session, including children
        proc.communicate()
        return _err(f"command timed out after {timeout} seconds and was killed")
    text = redact_secrets((out + err).decode("utf-8", "replace"))
    return {"ok": proc.returncode == 0, "output": _truncate(f"exit code {proc.returncode}\n{text}")}


class ToolBox:
    def __init__(self, root, *, command_timeout_s: int = DEFAULT_COMMAND_TIMEOUT_S, runner=None):
        self.root = os.path.realpath(str(root))
        self.command_timeout_s = command_timeout_s
        self._runner = runner or _run_shell
        self._lock = threading.RLock()
        self.changes: dict[str, Change] = {}
        self.approvals: dict[str, Approval] = {}

    def specs(self) -> list[dict]:
        return TOOL_SPECS

    def execute(self, name, args) -> dict:
        if name not in _PARAMS:
            return _err(f"unknown tool: {name}")
        problem = _validate(name, args)
        if problem:
            return _err(problem)
        handler = getattr(self, "_t_" + name)  # name is already checked against _PARAMS
        try:
            return handler(**args)
        except (ToolRefusal, SandboxError) as exc:
            return _err(str(exc))
        except OSError as exc:
            return _err(f"filesystem error: {exc.strerror or type(exc).__name__}")

    # ----- read-only tools -------------------------------------------------

    def _t_list_dir(self, path: str) -> dict:
        target = resolve_in_workspace(self.root, path)
        if not target.is_dir():
            raise ToolRefusal("not a directory")
        names = [n for n in os.listdir(target) if n not in SKIP_DIRS]
        names.sort(key=lambda n: (not (target / n).is_dir(), n.lower()))
        lines = [n + "/" if (target / n).is_dir() else n for n in names[:MAX_LIST_ENTRIES]]
        if len(names) > MAX_LIST_ENTRIES:
            lines.append(f"...({len(names) - MAX_LIST_ENTRIES} more)")
        return _ok("\n".join(lines) or "(empty)")

    def _t_read_file(self, path: str, start_line: int | None = None, end_line: int | None = None) -> dict:
        target = resolve_in_workspace(self.root, path)
        if not target.is_file():
            raise ToolRefusal("not a file")
        if is_sensitive_name(target.name):
            raise ToolRefusal(SENSITIVE_MESSAGE)
        lines = _read_text(target).splitlines()
        first = max(1, start_line or 1)
        last = min(len(lines), end_line or len(lines))
        body = "\n".join(f"{i}: {lines[i - 1]}" for i in range(first, last + 1))
        return _ok(body or "(no lines in that range)")

    def _t_search_text(self, text: str, path: str = ".") -> dict:
        if not text:
            raise ToolRefusal("search text must not be empty")
        base = resolve_in_workspace(self.root, path)
        if not base.is_dir():
            raise ToolRefusal("not a directory")
        hits: list[str] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for filename in filenames:
                full = os.path.join(dirpath, filename)
                if os.path.islink(full) or is_sensitive_name(filename):
                    continue
                try:
                    if os.path.getsize(full) > SEARCH_MAX_FILE_BYTES:
                        continue
                    with open(full, "rb") as handle:
                        raw = handle.read()
                except OSError:
                    continue
                if b"\x00" in raw[:4096]:
                    continue
                rel = os.path.relpath(full, self.root)
                for number, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
                    if text in line:
                        hits.append(f"{rel}:{number}: {line.strip()[:200]}")
                        if len(hits) >= SEARCH_MAX_HITS:
                            return _ok("\n".join(hits) + "\n(limit reached)")
        return _ok("\n".join(hits) or "(no matches)")

    def _t_glob_files(self, pattern: str, path: str = ".") -> dict:
        pattern = pattern.strip().removeprefix("./")
        if not pattern:
            raise ToolRefusal("pattern must not be empty")
        base = resolve_in_workspace(self.root, path)
        if not base.is_dir():
            raise ToolRefusal("not a directory")
        # "**/" also matches files directly in the base directory.
        patterns = {pattern, pattern.replace("**/", "")}
        hits: list[str] = []
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for filename in sorted(filenames):
                rel_base = os.path.relpath(os.path.join(dirpath, filename), base).replace(os.sep, "/")
                if any(fnmatch.fnmatchcase(rel_base, p) for p in patterns) or (
                        "/" not in pattern and fnmatch.fnmatchcase(filename, pattern)):
                    hits.append(os.path.relpath(os.path.join(dirpath, filename), self.root))
                    if len(hits) >= GLOB_MAX_HITS:
                        return _ok("\n".join(hits) + "\n(limit reached)")
        return _ok("\n".join(hits) or "(no files match)")

    # ----- proposals (nothing is written here) -----------------------------

    def _t_propose_edit(self, path: str, old_text: str, new_text: str) -> dict:
        if old_text == "":
            raise ToolRefusal("old_text must not be empty")
        target = resolve_in_workspace(self.root, path)
        if is_sensitive_name(target.name):
            raise ToolRefusal(SENSITIVE_MESSAGE)
        if not target.is_file():
            raise ToolRefusal("file does not exist; use propose_write to create it")
        old = _read_text(target)
        count = old.count(old_text)
        if count != 1:
            raise ToolRefusal(f"old_text must occur exactly once in the file (found {count}); "
                              "include more surrounding lines")
        return self._propose(self._rel(target), "edit", old, old.replace(old_text, new_text, 1))

    def _t_propose_write(self, path: str, content: str) -> dict:
        if len(content.encode("utf-8")) > MAX_WRITE_BYTES:
            raise ToolRefusal("content is larger than 500 KB")
        target = resolve_in_workspace(self.root, path)
        if is_sensitive_name(target.name):
            raise ToolRefusal(SENSITIVE_MESSAGE)
        if target.is_dir():
            raise ToolRefusal("path is a directory")
        old = _read_text(target) if target.exists() else None
        if old == content:
            raise ToolRefusal("content is identical to the current file; nothing to propose")
        return self._propose(self._rel(target), "write", old, content)

    def _propose(self, rel: str, kind: str, old: str | None, new: str) -> dict:
        change = Change(id=uuid.uuid4().hex[:12], path=rel, kind=kind, old=old, new=new)
        with self._lock:
            self.changes[change.id] = change
        return _ok(f"proposed {'an edit' if kind == 'edit' else 'a write'} to {rel} (change {change.id}); "
                   "it is not applied until the user accepts it", change_id=change.id)

    def _rel(self, target) -> str:
        return os.path.relpath(str(target), self.root)

    # ----- commands (approval required) ------------------------------------

    def _t_run_command(self, command: str) -> dict:
        if not command.strip():
            raise ToolRefusal("command is empty")
        if any(pattern in command for pattern in HARD_BLOCKED):
            raise ToolRefusal("command matches a blocked pattern")
        approval = Approval(id=uuid.uuid4().hex[:12], command=command)
        with self._lock:
            self.approvals[approval.id] = approval
        return {"ok": True, "status": "awaiting_approval", "approval_id": approval.id,
                "output": "The user must approve this command before it runs. Its output is not available yet."}

    def approve(self, approval_id: str, allow: bool) -> dict:
        with self._lock:
            approval = self.approvals.get(approval_id)
            if approval is None or approval.status != "pending":
                return _err("no pending approval with that id")
            if not allow:
                approval.status = "denied"
                approval.result = _err("the user denied this command")
                return approval.result
            approval.status = "approved"
            command = approval.command
        # The command can run for a long time, so the lock is not held while it runs.
        result = self._runner(command, self.root, self.command_timeout_s)
        with self._lock:
            approval.status = "done"
            approval.result = result
        return result

    # ----- applying or discarding proposals --------------------------------

    def accept_change(self, change_id: str) -> dict:
        with self._lock:
            change = self.changes.get(change_id)
            if change is None:
                return _err("unknown change id")
            if change.status != "pending":
                return _err(f"change is already {change.status}")
            try:
                target = resolve_in_workspace(self.root, change.path)
                current = _read_text(target) if target.exists() else None
                if current != change.old:
                    change.status = "conflict"
                    return _err("the file changed after this proposal; ask the agent to read it again "
                                "and propose a new change")
                _atomic_write(target, change.new)
            except (ToolRefusal, SandboxError) as exc:
                change.status = "conflict"
                return _err(str(exc))
            except OSError as exc:
                change.status = "failed"
                return _err(f"could not write: {exc.strerror or type(exc).__name__}")
            change.status = "accepted"
            return _ok(f"applied change {change.id} to {change.path}")

    def reject_change(self, change_id: str) -> dict:
        with self._lock:
            change = self.changes.get(change_id)
            if change is None:
                return _err("unknown change id")
            if change.status != "pending":
                return _err(f"change is already {change.status}")
            change.status = "rejected"
            return _ok(f"rejected change {change.id}")

    def revert_change(self, change_id: str) -> dict:
        """Undo an accepted change, but only if the file still holds exactly what was written."""
        with self._lock:
            change = self.changes.get(change_id)
            if change is None:
                return _err("unknown change id")
            if change.status != "accepted":
                return _err(f"only accepted changes can be undone (status is {change.status})")
            try:
                target = resolve_in_workspace(self.root, change.path)
                current = _read_text(target) if target.exists() else None
                if current != change.new:
                    return _err("the file changed after this change was applied; undo refused to avoid losing edits")
                if change.old is None:
                    os.unlink(target)
                else:
                    _atomic_write(target, change.old)
            except (ToolRefusal, SandboxError) as exc:
                return _err(str(exc))
            except OSError as exc:
                return _err(f"could not undo: {exc.strerror or type(exc).__name__}")
            change.status = "reverted"
            return _ok(f"undid change {change.id} on {change.path}")

    def list_changes(self) -> list[dict]:
        with self._lock:
            return [{"id": c.id, "path": c.path, "kind": c.kind, "status": c.status,
                     "diff": _diff(c)} for c in self.changes.values()]


def _diff(change: Change) -> str:
    lines = difflib.unified_diff(
        (change.old or "").splitlines(keepends=True),
        change.new.splitlines(keepends=True),
        fromfile=("a/" + change.path) if change.old is not None else "/dev/null",
        tofile="b/" + change.path,
    )
    return _truncate("".join(lines))
