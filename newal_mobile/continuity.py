"""Keeps long runs inside the model's context without losing the task.

Ported from M4's agent/newal/continuity.py and NewAl's ConversationWindow.java:
- the system prompt and the user's real goal are never dropped or rewritten;
- older turns are replaced by a deterministic note owned by the assistant (never a fake user turn);
- the tail always starts at a clean boundary, so no tool result is orphaned from its call;
- an answer that claims there is no task (a common failure of small free models) is caught once.
"""

from __future__ import annotations

import json
import re

DEFAULT_BUDGET_CHARS = 60_000
KEEP_TAIL_MESSAGES = 12
MAX_EVIDENCE = 900

_ABANDONED = (
    re.compile(r"\b(?:i (?:don.t|do not|cannot) have (?:a |any )?task|"
               r"there(?: is|.s) (?:no|nothing) (?:task|to continue)|"
               r"what would you like (?:me )?to (?:do|work on)|"
               r"tell me (?:the goal|what you want))\b", re.I),
    re.compile(r"(?:ما (?:عندي|في|عندنا) (?:مهمة|شي)|شو بدك (?:أعمل|اعمل)|"
               r"قلّي شو (?:بدك|المطلوب)|لا توجد (?:مهمة|مهام)|لم (?:تحدد|تُحدد) (?:مهمة|المهمة)|"
               r"ماذا تريد (?:أن|ان) (?:أفعل|افعل))"),
)


def size(messages) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)


def _evidence(dropped) -> str:
    rows = []
    for message in dropped:
        if message.get("role") == "tool":
            rows.append(re.sub(r"\s+", " ", str(message.get("content", "")))[:170])
        elif message.get("role") == "assistant":
            for call in message.get("tool_calls") or []:
                fn = call.get("function", {})
                rows.append(f"called {fn.get('name')} {str(fn.get('arguments', ''))[:80]}")
    return " | ".join(rows[-6:])[:MAX_EVIDENCE]


def compact(messages, goal: str, *, budget_chars=DEFAULT_BUDGET_CHARS, keep_tail=KEEP_TAIL_MESSAGES):
    """Return (messages, compacted?). messages[0] is the system prompt and messages[1] the goal."""
    if size(messages) <= budget_chars or len(messages) <= keep_tail + 3:
        return messages, False
    head = messages[:2]
    start = max(2, len(messages) - keep_tail)
    while start < len(messages) and messages[start].get("role") == "tool":
        start += 1  # never begin the tail with a tool result whose call was dropped
    if start >= len(messages):
        return messages, False
    dropped = messages[2:start]
    note = {"role": "assistant", "content": "\n".join([
        "[Conversation notes written by the runtime — not a new user message. Verify with tools.]",
        f"Earlier steps were trimmed to fit the context ({len(dropped)} messages).",
        "Active request (copied from the user's real message): " + goal[:2000],
        "Recent actions from the trimmed part (not proof of completion): " + (_evidence(dropped) or "(none)"),
        "Keep working on the active request. Do not ask for a new task. Do not claim completion without a tool result.",
    ])}
    return head + [note] + messages[start:], True


def lost_task(answer: str, goal: str) -> bool:
    if not (goal or "").strip():
        return False
    text = (answer or "")[:1800]
    return any(pattern.search(text) for pattern in _ABANDONED)


def correction(goal: str) -> str:
    return ("[Runtime continuity correction — not a new user request.] The active task is: "
            f"{goal[:2000]}. Your previous reply said there was no task. Continue with the tools "
            "and the real workspace state. Do not invent finished actions.")
