"""Durable task checkpoints, ported from M4's agent/newal/task_state.py.

Checkpoints are short summaries stored outside the workspace (in the state directory), one
file per workspace. They let the agent pick up long work after Termux or the phone restarts.
They never run anything in the background and never bypass approvals.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path

MAX_TASKS = 64
MAX_HISTORY = 20
MAX_FIELD = 4000
_ID = re.compile(r"[A-Za-z0-9._-]{1,80}")

TASK_SPECS = [
    {"type": "function", "function": {
        "name": "task_checkpoint",
        "description": "Save a short checkpoint of long work so it can be resumed after a restart. "
                       "Omit task_id to start a new task (objective is then required).",
        "parameters": {"type": "object", "properties": {
            "task_id": {"type": "string"},
            "objective": {"type": "string"},
            "progress": {"type": "string", "description": "What is actually done (verified)."},
            "next_step": {"type": "string"},
            "blocker": {"type": "string"},
            "evidence": {"type": "string", "description": "Short proof, e.g. a test result."}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "task_resume",
        "description": "Return one saved checkpoint. Omit task_id for the most recently active task.",
        "parameters": {"type": "object", "properties": {"task_id": {"type": "string"}}, "required": []}}},
    {"type": "function", "function": {
        "name": "task_complete",
        "description": "Mark a checkpointed task complete, only after its result was verified.",
        "parameters": {"type": "object", "properties": {
            "task_id": {"type": "string"}, "summary": {"type": "string"}}, "required": ["task_id"]}}},
    {"type": "function", "function": {
        "name": "task_list",
        "description": "List saved task checkpoints for this workspace.",
        "parameters": {"type": "object", "properties": {
            "include_completed": {"type": "boolean"}}, "required": []}}},
]


class TaskError(ValueError):
    pass


def _text(value, limit=MAX_FIELD) -> str:
    return str(value or "").strip()[:limit]


class TaskStore:
    def __init__(self, state_directory, workspace, *, clock=time.time):
        digest = hashlib.sha256(os.path.realpath(str(workspace)).encode("utf-8")).hexdigest()[:16]
        self.path = Path(state_directory) / "tasks" / f"{digest}.json"
        self._clock = clock
        self._lock = threading.RLock()

    # ----- storage ---------------------------------------------------------

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise TaskError(f"checkpoint store is unreadable: {type(exc).__name__}") from None
        tasks = data.get("tasks") if isinstance(data, dict) else None
        if not isinstance(tasks, dict):
            raise TaskError("checkpoint store has an invalid format")
        return tasks

    def _save(self, tasks: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "tasks": tasks}, handle, ensure_ascii=False, indent=1)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    @staticmethod
    def _id(value) -> str:
        value = _text(value, 80)
        if value and not _ID.fullmatch(value):
            raise TaskError("task_id may contain only letters, numbers, dot, underscore and dash")
        return value

    # ----- operations ------------------------------------------------------

    def checkpoint(self, task_id="", objective="", progress="", next_step="", blocker="", evidence="") -> dict:
        with self._lock:
            tasks = self._load()
            task_id = self._id(task_id)
            objective = _text(objective, 2000)
            now = self._clock()
            row = tasks.get(task_id) if task_id else None
            if row is None:
                if task_id:
                    raise TaskError("unknown task_id; omit it to start a new task")
                if not objective:
                    raise TaskError("a new checkpoint needs an objective")
                task_id = "task-" + hashlib.sha256(f"{objective}:{time.time_ns()}".encode()).hexdigest()[:10]
                row = {"id": task_id, "objective": objective, "created_at": now, "history": []}
            if objective:
                row["objective"] = objective
            point = {"progress": _text(progress), "next_step": _text(next_step), "blocker": _text(blocker),
                     "evidence": _text(evidence, 2000), "at": now}
            row.update(status="active", updated_at=now, checkpoint=point)
            row["history"] = (row.get("history") or [])[-(MAX_HISTORY - 1):] + [point]
            tasks[task_id] = row
            self._prune(tasks, task_id)
            self._save(tasks)
            return row

    def resume(self, task_id="") -> dict | None:
        with self._lock:
            tasks = self._load()
            task_id = self._id(task_id)
            if task_id:
                return tasks.get(task_id)
            active = [r for r in tasks.values() if r.get("status") == "active"]
            return max(active, key=lambda r: r.get("updated_at", 0)) if active else None

    def complete(self, task_id, summary="") -> dict:
        with self._lock:
            tasks = self._load()
            row = tasks.get(self._id(task_id))
            if row is None:
                raise TaskError("unknown task_id")
            row.update(status="complete", updated_at=self._clock(), summary=_text(summary))
            self._save(tasks)
            return row

    def list(self, include_completed=False) -> list[dict]:
        with self._lock:
            rows = [r for r in self._load().values() if include_completed or r.get("status") == "active"]
        rows.sort(key=lambda r: r.get("updated_at", 0), reverse=True)
        return [{"id": r["id"], "objective": r.get("objective", ""), "status": r.get("status"),
                 "next_step": (r.get("checkpoint") or {}).get("next_step", "")} for r in rows[:20]]

    @staticmethod
    def _prune(tasks: dict, keep: str) -> None:
        if len(tasks) <= MAX_TASKS:
            return
        order = sorted((r for r in tasks.values() if r["id"] != keep),
                       key=lambda r: (r.get("status") != "complete", r.get("updated_at", 0)))
        for row in order[:len(tasks) - MAX_TASKS]:
            tasks.pop(row["id"], None)

    def execute(self, name: str, args: dict) -> str:
        if name == "task_checkpoint":
            return json.dumps(self.checkpoint(**args), ensure_ascii=False)
        if name == "task_resume":
            return json.dumps(self.resume(**args) or {"active": False}, ensure_ascii=False)
        if name == "task_complete":
            return json.dumps(self.complete(**args), ensure_ascii=False)
        return json.dumps(self.list(**args), ensure_ascii=False)
