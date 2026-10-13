"""Bounded, resumable tool-use loop.

A run pauses whenever a tool needs the user's approval and continues only after that
approval is resolved. Budgets (steps, wall-clock time, identical repeated calls) stop
runaway loops. Looping was a real failure the phone user reported on another project.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import Counter

from . import continuity
from .providers import ProviderError


class RunError(ValueError):
    """Invalid use of a run: empty goal, or an action in the wrong state."""


class Run:
    def __init__(self, goal, toolbox, provider, system_prompt, *, max_steps=12, max_wall_s=300.0,
                 repeat_limit=3, clock=time.monotonic, run_id=None,
                 context_budget_chars=continuity.DEFAULT_BUDGET_CHARS):
        if not isinstance(goal, str) or not goal.strip():
            raise RunError("goal must be non-empty text")
        self.id = run_id or uuid.uuid4().hex[:12]
        self.lock = threading.Lock()  # one advance or approval at a time per run
        self.goal = goal.strip()
        self.toolbox = toolbox
        self.provider = provider  # any object with complete(messages, tools) -> (message, name)
        self.max_steps = max_steps
        self.max_wall_s = max_wall_s
        self.repeat_limit = repeat_limit
        self._clock = clock
        self._started = clock()
        self.messages = [{"role": "system", "content": system_prompt},
                         {"role": "user", "content": self.goal}]
        self.events: list[dict] = []
        self.status = "running"   # running | awaiting_approval | done | failed
        self.steps = 0
        self.final: str | None = None
        self.error: str | None = None
        self._queue: list[dict] = []   # tool calls of the current model turn, head first
        self._seen: Counter = Counter()
        self._approval_id: str | None = None
        self.context_budget_chars = context_budget_chars
        self._corrected = False

    def advance(self) -> dict:
        if self.status != "running":
            return self.snapshot()
        while self.status == "running":
            if self._queue:
                if self._drain_queue():
                    break
                continue
            if self.steps >= self.max_steps:
                self._fail("step budget reached")
                break
            if self._clock() - self._started > self.max_wall_s:
                self._fail("time budget reached")
                break
            self.steps += 1
            self.messages, trimmed = continuity.compact(self.messages, self.goal,
                                                        budget_chars=self.context_budget_chars)
            if trimmed:
                self._event("context_compacted", messages=len(self.messages))
            try:
                message, provider_name = self.provider.complete(self.messages, self.toolbox.specs())
            except ProviderError as exc:
                self._fail(f"no model answered: {exc}")
                break
            assistant = _clean_assistant(message)
            self.messages.append(assistant)
            calls = assistant.get("tool_calls", [])
            self._event("model", provider=provider_name, step=self.steps, tool_calls=len(calls))
            if not calls and not self._corrected and continuity.lost_task(assistant["content"], self.goal):
                self._corrected = True  # small free models sometimes forget the task; correct once
                self.messages.append({"role": "user", "content": continuity.correction(self.goal)})
                self._event("continuity_correction")
                continue
            if not calls:
                self.final = assistant["content"]
                self.status = "done"
                self._event("final", text=self.final)
                break
            self._queue = list(calls)
        return self.snapshot()

    def resolve_approval(self, approval_id: str, allow: bool) -> dict:
        if self.status != "awaiting_approval" or approval_id != self._approval_id:
            raise RunError("no matching approval is pending")
        result = self.toolbox.approve(approval_id, bool(allow))
        call = self._queue.pop(0)  # the paused call is still at the head of the queue
        call_id, name, args, _ = _parse_call(call)
        self._record_tool(call_id, name, args, result)
        self._approval_id = None
        self.status = "running"
        self._event("approval_resolved", approval_id=approval_id, allowed=bool(allow))
        return self.advance()

    def snapshot(self) -> dict:
        pending = None
        if self.status == "awaiting_approval":
            approval = self.toolbox.approvals.get(self._approval_id)
            pending = {"approval_id": self._approval_id, "command": approval.command if approval else "",
                       "risk": approval.risk if approval else None}
        return {"id": self.id, "status": self.status, "steps": self.steps, "final": self.final,
                "error": self.error, "pending_approval": pending, "events": list(self.events)}

    # ----- internals -------------------------------------------------------

    def _drain_queue(self) -> bool:
        """Run queued tool calls. Returns True when the run paused or failed."""
        while self._queue:
            call = self._queue[0]
            call_id, name, args, parse_error = _parse_call(call)
            if parse_error:
                result = {"ok": False, "output": parse_error}
            else:
                key = (name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                self._seen[key] += 1
                if self._seen[key] > self.repeat_limit:
                    self._fail(f"stopped: the model repeated the same {name} call more than "
                               f"{self.repeat_limit} times")
                    return True
                result = self.toolbox.execute(name, args)
            if result.get("status") == "awaiting_approval":
                self._approval_id = result["approval_id"]
                self.status = "awaiting_approval"
                self._event("approval_requested", approval_id=self._approval_id,
                            command=(args or {}).get("command", ""), risk=result.get("risk"))
                return True
            self._queue.pop(0)
            self._record_tool(call_id, name, args, result)
        return False

    def _record_tool(self, call_id, name, args, result) -> None:
        payload = {k: result[k] for k in ("ok", "output", "status", "change_id") if k in result}
        self.messages.append({"role": "tool", "tool_call_id": call_id,
                              "content": json.dumps(payload, ensure_ascii=False)})
        event = {"name": name, "ok": bool(result.get("ok")),
                 "summary": _summary(str(result.get("output", "")))}
        if isinstance(args, dict) and "path" in args:
            event["path"] = str(args["path"])[:300]
        if "change_id" in result:
            event["change_id"] = result["change_id"]
        self._event("tool", **event)

    def _fail(self, message: str) -> None:
        self.status = "failed"
        self.error = message
        self._event("error", text=message)

    def _event(self, kind: str, **fields) -> None:
        self.events.append({"type": kind, **fields})


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


def _summary(text: str) -> str:
    single = " ".join(text.split())
    return single[:160] + ("…" if len(single) > 160 else "")
