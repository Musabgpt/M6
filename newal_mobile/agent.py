"""Bounded, resumable, multi-turn tool-use loop (a "session").

A session is a conversation: the user sends a message, the agent works with tools until it
answers, and the user can send a follow-up that continues the same conversation.

A turn pauses whenever a tool needs the user's approval and continues only after that
approval is resolved. Budgets (steps per turn, wall-clock time per turn, identical repeated
calls) stop runaway loops. A turn can also be stopped by the user between steps.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import Counter

from .providers import ProviderError

MAX_CONTEXT_CHARS = 150_000   # rough budget for the messages we send; older tool output is trimmed first
KEEP_RECENT_MESSAGES = 8      # never trim the newest messages
MAX_TODOS = 30
TODO_STATES = ("pending", "in_progress", "completed")

TODO_SPEC = {"type": "function", "function": {
    "name": "todo_write",
    "description": ("Replace the task list shown to the user. Use it for work with several steps: "
                    "list the steps, keep exactly one in_progress, and mark each completed as soon as it is done."),
    "parameters": {"type": "object", "properties": {
        "todos": {"type": "array", "description": "The full, updated task list.", "items": {
            "type": "object", "properties": {
                "content": {"type": "string"},
                "status": {"type": "string", "enum": list(TODO_STATES)}},
            "required": ["content", "status"]}}},
        "required": ["todos"]}}}


class RunError(ValueError):
    """Invalid use of a run: empty goal, or an action in the wrong state."""


class Run:
    def __init__(self, goal, toolbox, provider, system_prompt, *, max_steps=25, max_wall_s=600.0,
                 repeat_limit=3, clock=time.monotonic, run_id=None, settings=None):
        if not isinstance(goal, str) or not goal.strip():
            raise RunError("goal must be non-empty text")
        self.id = run_id or uuid.uuid4().hex[:12]
        self.lock = threading.Lock()  # one advance or approval at a time per run
        self.goal = goal.strip()
        self.title = _title(self.goal)
        self.toolbox = toolbox
        self.provider = provider  # any object with complete(messages, tools) -> (message, name)
        self.max_steps = max_steps
        self.max_wall_s = max_wall_s
        self.repeat_limit = repeat_limit
        # Shared, live settings owned by the app: {"mode": "ask" | "accept_edits"}.
        self.settings = settings if settings is not None else {"mode": "ask"}
        self._clock = clock
        self._turn_started = clock()
        self.created = time.time()
        self.updated = self.created
        self.messages = [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": self.goal}]
        self.events: list[dict] = [{"type": "user", "text": self.goal}]
        self.status = "running"   # running | awaiting_approval | done | failed | stopped
        self.steps = 0            # total model calls in this session
        self.turn_steps = 0       # model calls in the current turn (budgeted)
        self.final: str | None = None
        self.error: str | None = None
        self.todos: list[dict] = []
        self.allowed_commands: set[str] = set()   # "Allow always" for this session, exact text only
        self.working = False      # True while a background thread is advancing this run
        self._cancel = threading.Event()
        self._queue: list[dict] = []   # tool calls of the current model turn, head first
        self._seen: Counter = Counter()
        self._approval_id: str | None = None

    # ----- public API ------------------------------------------------------

    def specs(self) -> list[dict]:
        return list(self.toolbox.specs()) + [TODO_SPEC]

    def add_user_message(self, text) -> dict:
        """Start a new turn in the same conversation."""
        if not isinstance(text, str) or not text.strip():
            raise RunError("message must be non-empty text")
        if self.status in ("running", "awaiting_approval"):
            raise RunError("the agent is still working; wait or stop it first")
        text = text.strip()
        self.messages.append({"role": "user", "content": text})
        self._event("user", text=text)
        self.status = "running"
        self.final = None
        self.error = None
        self.turn_steps = 0
        self._turn_started = self._clock()
        self._seen.clear()
        self._cancel.clear()
        return self.snapshot()

    def cancel(self) -> dict:
        """Ask the run to stop. It stops before the next model call or tool call."""
        if self.status == "awaiting_approval":
            # Nothing is executing: deny the pending command so the conversation stays consistent.
            approval_id = self._approval_id
            self.toolbox.approve(approval_id, False)
            call = self._queue.pop(0)
            call_id, name, args, _ = _parse_call(call)
            self._record_tool(call_id, name, args, {"ok": False, "output": "the user stopped the run"})
            self._approval_id = None
            self._event("approval_resolved", approval_id=approval_id, allowed=False)
            self._stop()
        elif self.status == "running":
            self._cancel.set()
            if not self.working:
                self._stop()
        return self.snapshot()

    def advance(self) -> dict:
        if self.status != "running":
            return self.snapshot()
        while self.status == "running":
            if self._cancel.is_set():
                self._stop()
                break
            if self._queue:
                if self._drain_queue():
                    break
                continue
            if self.turn_steps >= self.max_steps:
                self._fail("step budget reached; send a message to continue")
                break
            if self._clock() - self._turn_started > self.max_wall_s:
                self._fail("time budget reached; send a message to continue")
                break
            self.steps += 1
            self.turn_steps += 1
            self._event("thinking", step=self.steps)
            try:
                message, provider_name = self.provider.complete(_trimmed(self.messages), self.specs())
            except ProviderError as exc:
                self._fail(f"no model answered: {exc}")
                break
            assistant = _clean_assistant(message)
            self.messages.append(assistant)
            calls = assistant.get("tool_calls", [])
            self._event("model", provider=provider_name, step=self.steps, tool_calls=len(calls))
            if not calls:
                self.final = assistant["content"]
                self.status = "done"
                self._event("final", text=self.final)
                break
            if assistant["content"].strip():
                self._event("note", text=assistant["content"])
            if self._cancel.is_set():
                # The model asked for tools but the user pressed Stop: answer every call so the
                # transcript stays valid for the next turn, then stop.
                for call in calls:
                    call_id, name, args, _ = _parse_call(call)
                    self._record_tool(call_id, name, args, {"ok": False, "output": "the user stopped the run"})
                self._stop()
                break
            self._queue = list(calls)
        return self.snapshot()

    def resolve_approval(self, approval_id: str, allow: bool, always: bool = False) -> dict:
        if self.status != "awaiting_approval" or approval_id != self._approval_id:
            raise RunError("no matching approval is pending")
        approval = self.toolbox.approvals.get(approval_id)
        if allow and always and approval is not None:
            self.allowed_commands.add(approval.command)
        result = self.toolbox.approve(approval_id, bool(allow))
        call = self._queue.pop(0)  # the paused call is still at the head of the queue
        call_id, name, args, _ = _parse_call(call)
        self._record_tool(call_id, name, args, result)
        self._approval_id = None
        self.status = "running"
        self._event("approval_resolved", approval_id=approval_id, allowed=bool(allow), always=bool(allow and always))
        return self.advance()

    def snapshot(self) -> dict:
        pending = None
        if self.status == "awaiting_approval":
            approval = self.toolbox.approvals.get(self._approval_id)
            pending = {"approval_id": self._approval_id, "command": approval.command if approval else ""}
        return {"id": self.id, "title": self.title, "status": self.status, "working": self.working,
                "steps": self.steps, "final": self.final, "error": self.error,
                "pending_approval": pending, "todos": [dict(t) for t in self.todos],
                "events": list(self.events)}

    def summary(self) -> dict:
        return {"id": self.id, "title": self.title, "status": self.status,
                "created": self.created, "updated": self.updated, "turns": sum(
                    1 for e in self.events if e.get("type") == "user")}

    # ----- persistence -----------------------------------------------------

    def to_dict(self) -> dict:
        return {"version": 1, "id": self.id, "goal": self.goal, "title": self.title,
                "created": self.created, "updated": self.updated, "status": self.status,
                "steps": self.steps, "final": self.final, "error": self.error,
                "todos": self.todos, "allowed_commands": sorted(self.allowed_commands),
                "messages": self.messages, "events": self.events}

    @classmethod
    def from_dict(cls, data: dict, toolbox, provider, system_prompt, **kwargs) -> "Run":
        run = cls(data["goal"], toolbox, provider, system_prompt, run_id=data["id"], **kwargs)
        run.title = str(data.get("title") or run.title)
        run.created = float(data.get("created") or run.created)
        run.updated = float(data.get("updated") or run.created)
        run.steps = int(data.get("steps") or 0)
        run.final = data.get("final")
        run.error = data.get("error")
        run.todos = [t for t in data.get("todos") or [] if isinstance(t, dict)][:MAX_TODOS]
        run.allowed_commands = {c for c in data.get("allowed_commands") or [] if isinstance(c, str)}
        messages = [m for m in data.get("messages") or [] if isinstance(m, dict) and "role" in m]
        if messages:
            # Always use the current system prompt, never one read from disk.
            run.messages = [{"role": "system", "content": system_prompt}] + [
                m for m in messages if m["role"] != "system"]
        run.events = [e for e in data.get("events") or [] if isinstance(e, dict)]
        status = data.get("status")
        if status in ("done", "failed", "stopped"):
            run.status = status
        else:
            # A turn that was working or waiting when the server stopped cannot be resumed safely.
            _close_open_tool_calls(run.messages)
            run.status = "stopped"
            run.events.append({"type": "error", "text": "the server restarted while this turn was in progress; "
                                                        "send a message to continue"})
        return run

    # ----- internals -------------------------------------------------------

    def _drain_queue(self) -> bool:
        """Run queued tool calls. Returns True when the run paused, stopped or failed."""
        while self._queue:
            if self._cancel.is_set():
                for call in self._queue:
                    call_id, name, args, _ = _parse_call(call)
                    self._record_tool(call_id, name, args, {"ok": False, "output": "the user stopped the run"})
                self._queue = []
                self._stop()
                return True
            call = self._queue[0]
            call_id, name, args, parse_error = _parse_call(call)
            if parse_error:
                result = {"ok": False, "output": parse_error}
            else:
                key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                self._seen[key] += 1
                if self._seen[key] > self.repeat_limit:
                    self._fail_with_open_calls(f"stopped: the model repeated the same {name} call more than "
                                               f"{self.repeat_limit} times")
                    return True
                self._event("tool_start", name=name, label=_label(name, args))
                if name == "todo_write":
                    result = self._todo_write(args)
                else:
                    result = self.toolbox.execute(name, args)
            if result.get("status") == "awaiting_approval":
                command = (args or {}).get("command", "")
                if command in self.allowed_commands:
                    self._event("auto_approved", command=command)
                    result = self.toolbox.approve(result["approval_id"], True)
                else:
                    self._approval_id = result["approval_id"]
                    self.status = "awaiting_approval"
                    self._event("approval_requested", approval_id=self._approval_id, command=command)
                    return True
            auto_applied = False
            if result.get("change_id") and self.settings.get("mode") == "accept_edits":
                applied = self.toolbox.accept_change(result["change_id"])
                auto_applied = bool(applied.get("ok"))
                result = dict(result, output=applied["output"] if auto_applied else
                              result["output"] + "\n(auto-apply failed: " + applied["output"] + ")")
            self._queue.pop(0)
            self._record_tool(call_id, name, args, result, auto_applied=auto_applied)
        return False

    def _todo_write(self, args) -> dict:
        todos = (args or {}).get("todos")
        if not isinstance(todos, list):
            return {"ok": False, "output": "todos must be a list"}
        clean = []
        for item in todos[:MAX_TODOS]:
            if not isinstance(item, dict) or not isinstance(item.get("content"), str):
                return {"ok": False, "output": "each todo needs text content and a status"}
            status = item.get("status") if item.get("status") in TODO_STATES else "pending"
            clean.append({"content": item["content"].strip()[:200], "status": status})
        self.todos = clean
        self._event("todos", todos=[dict(t) for t in clean])
        done = sum(1 for t in clean if t["status"] == "completed")
        return {"ok": True, "output": f"task list updated ({done}/{len(clean)} completed)"}

    def _record_tool(self, call_id, name, args, result, auto_applied=False) -> None:
        payload = {k: result[k] for k in ("ok", "output", "status", "change_id") if k in result}
        self.messages.append({"role": "tool", "tool_call_id": call_id,
                              "content": json.dumps(payload, ensure_ascii=False)})
        if name == "todo_write" and result.get("ok"):
            return  # the "todos" event already shows it
        event = {"name": name, "ok": bool(result.get("ok")), "label": _label(name, args),
                 "summary": _tool_summary(name, result)}
        if isinstance(args, dict) and "path" in args:
            event["path"] = str(args["path"])[:300]
        if "change_id" in result:
            event["change_id"] = result["change_id"]
            event["auto_applied"] = auto_applied
        self._event("tool", **event)

    def _fail_with_open_calls(self, message: str) -> None:
        for call in self._queue:
            call_id, name, args, _ = _parse_call(call)
            self.messages.append({"role": "tool", "tool_call_id": call_id,
                                  "content": json.dumps({"ok": False, "output": message})})
        self._queue = []
        self._fail(message)

    def _fail(self, message: str) -> None:
        self.status = "failed"
        self.error = message
        self._event("error", text=message)

    def _stop(self) -> None:
        self.status = "stopped"
        self._cancel.clear()
        self._event("stopped")

    def _event(self, kind: str, **fields) -> None:
        self.updated = time.time()
        self.events.append({"type": kind, **fields})


def _title(goal: str) -> str:
    single = " ".join(goal.split())
    return single[:60] + ("…" if len(single) > 60 else "")


def _label(name: str, args) -> str:
    """Short human label for a tool call, e.g. 'app.py' or 'npm test'."""
    if not isinstance(args, dict):
        return ""
    for key in ("path", "pattern", "command", "text"):
        value = args.get(key)
        if isinstance(value, str) and value:
            single = " ".join(value.split())
            return single[:120] + ("…" if len(single) > 120 else "")
    if name == "todo_write":
        return f"{len(args.get('todos') or [])} tasks"
    return ""


def _trimmed(messages: list[dict]) -> list[dict]:
    """Copy of the messages that fits the context budget. Oldest tool outputs are shortened first."""
    total = sum(len(m.get("content") or "") for m in messages)
    if total <= MAX_CONTEXT_CHARS:
        return messages
    out = [dict(m) for m in messages]
    for m in out[:-KEEP_RECENT_MESSAGES]:
        if total <= MAX_CONTEXT_CHARS:
            break
        if m["role"] == "tool" and len(m.get("content") or "") > 200:
            total -= len(m["content"]) - 60
            m["content"] = json.dumps({"output": "[older tool output removed to save space]"})
    return out


def _close_open_tool_calls(messages: list[dict]) -> None:
    """Answer any tool call that has no tool result yet, so the next request is valid."""
    answered = {m.get("tool_call_id") for m in messages if m.get("role") == "tool"}
    for m in list(messages):
        for call in m.get("tool_calls") or []:
            if call.get("id") not in answered:
                messages.append({"role": "tool", "tool_call_id": call.get("id"),
                                 "content": json.dumps({"ok": False, "output": "not run: the server restarted"})})
                answered.add(call.get("id"))


def _clean_assistant(message) -> dict:
    """Keep only the fields we store, and guarantee every tool call has an id and string arguments."""
    if not isinstance(message, dict):
        message = {}
    cleaned = {"role": "assistant", "content": message.get("content") or ""}
    calls = []
    for raw in message.get("tool_calls") or []:
        if not isinstance(raw, dict):
            continue
        fn = raw.get("function") if isinstance(raw.get("function"), dict) else {}
        arguments = fn.get("arguments")
        if not isinstance(arguments, str):
            arguments = json.dumps(arguments or {}, ensure_ascii=False)
        calls.append({
            "id": str(raw.get("id") or f"call_{uuid.uuid4().hex[:10]}"),
            "type": "function",
            "function": {"name": str(fn.get("name", "")), "arguments": arguments},
        })
    if calls:
        cleaned["tool_calls"] = calls
    return cleaned


def _parse_call(call: dict):
    call_id = call["id"]
    name = call["function"]["name"]
    try:
        args = json.loads(call["function"]["arguments"])
    except ValueError:
        return call_id, name, None, "arguments are not valid JSON"
    if not isinstance(args, dict):
        return call_id, name, None, "arguments must be a JSON object"
    return call_id, name, args, None


def _tool_summary(name: str, result: dict) -> str:
    """What the phone shows under a tool line. File contents are summarised, not dumped."""
    output = str(result.get("output", ""))
    if result.get("ok"):
        lines = output.splitlines()
        if name == "read_file":
            return f"Read {len(lines)} lines"
        if name in ("list_dir", "glob_files", "search_text"):
            if output.startswith("(no ") or output == "(empty)":
                return output.strip("()")
            noun = {"list_dir": "entries", "glob_files": "files", "search_text": "matches"}[name]
            count = len([line for line in lines if not line.startswith("(limit")])
            return f"Found {count} {noun}" + (" (limit reached)" if output.endswith("(limit reached)") else "")
    return _summary(output)


def _summary(text: str) -> str:
    single = " ".join(text.split())
    return single[:160] + ("…" if len(single) > 160 else "")
