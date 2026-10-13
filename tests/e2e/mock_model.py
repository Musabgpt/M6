#!/usr/bin/env python3
"""Rule-based OpenAI-compatible mock model for end-to-end tests. It is NOT a real model."""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def decide(messages):
    # Sessions are multi-turn: the rules follow the newest user message.
    goal = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    last = messages[-1]

    def call(name, args):
        return {"role": "assistant", "content": "", "tool_calls": [{
            "id": f"c{len(messages)}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}]}

    if "return 1" in goal:
        if last["role"] == "user":
            return call("read_file", {"path": "app.py"})
        if last["role"] == "tool" and "return 1" in last["content"]:
            return call("propose_edit", {"path": "app.py", "old_text": "return 1", "new_text": "return 2"})
        return {"role": "assistant", "content": "Proposed the change for your review."}
    if goal.startswith("plan"):
        if last["role"] == "user":
            return call("todo_write", {"todos": [
                {"content": "Read the code", "status": "completed"},
                {"content": "Write the fix", "status": "in_progress"}]})
        return {"role": "assistant", "content": "Plan ready.\n\n```python\nprint('hi')\n```"}
    if goal.startswith("slow"):
        if last["role"] == "user":
            import time
            time.sleep(3)   # gives the browser time to press Stop while the model is "thinking"
            return call("run_command", {"command": "echo never > never.txt"})
        return {"role": "assistant", "content": "should not get here"}
    if goal.startswith("run"):
        if last["role"] == "user":
            target = "denied.txt" if "deny" in goal else "approved.txt"
            return call("run_command", {"command": f"echo done > {target}"})
        return {"role": "assistant", "content": "Command handled."}
    return {"role": "assistant", "content": "No rule matched."}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        payload = json.dumps({"choices": [{"message": decide(body["messages"])}]}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
