"""Local HTTP server: the phone browser UI and its JSON API. Binds to 127.0.0.1 only."""

from __future__ import annotations

import argparse
import hmac
import json
import os
import re
import sys
import threading
import traceback
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

from . import __version__
from .agent import Run, RunError
from .config import ConfigError, load_or_create_token, load_providers, state_dir, workspace_dir
from .prompt import SYSTEM_PROMPT
from .providers import FallbackChain
from .tools import DEFAULT_COMMAND_TIMEOUT_S, ToolBox

UI_DIR = Path(__file__).resolve().parent.parent / "ui"
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json; charset=utf-8"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}
ALLOWED_HOSTS = {"127.0.0.1", "localhost"}   # blocks DNS-rebinding attacks against the loopback server
MAX_BODY = 64_000
MAX_GOAL_CHARS = 4_000
RUN_RE = re.compile(r"/api/runs/([0-9a-f]{12})(/advance|/approval)?")
CHANGE_RE = re.compile(r"/api/changes/([0-9a-f]{12})/(accept|reject|revert)")
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
       "connect-src 'self'; img-src 'self' data:; manifest-src 'self'; base-uri 'none'; "
       "frame-ancestors 'none'")


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


class AppState:
    MAX_RUNS = 20

    def __init__(self, workspace, token: str, chain, *, system_prompt: str = SYSTEM_PROMPT,
                 max_steps: int = 12, max_wall_s: float = 300.0,
                 command_timeout_s: int = DEFAULT_COMMAND_TIMEOUT_S):
        self.workspace = os.path.realpath(str(workspace))
        os.makedirs(self.workspace, exist_ok=True)
        self.token = token
        self.chain = chain
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.max_wall_s = max_wall_s
        self.toolbox = ToolBox(self.workspace, command_timeout_s=command_timeout_s)
        self.runs: OrderedDict[str, Run] = OrderedDict()
        self.lock = threading.RLock()

    def status(self) -> dict:
        return {"version": __version__, "workspace": self.workspace,
                "providers": [p.name for p in self.chain.providers]}

    def tool(self, name: str, args: dict) -> dict:
        return self.toolbox.execute(name, args)

    def create_run(self, goal) -> dict:
        if not isinstance(goal, str):
            raise ApiError(400, "goal must be text")
        if len(goal) > MAX_GOAL_CHARS:
            raise ApiError(400, f"goal is longer than {MAX_GOAL_CHARS} characters")
        with self.lock:
            try:
                run = Run(goal, self.toolbox, self.chain, self.system_prompt,
                          max_steps=self.max_steps, max_wall_s=self.max_wall_s)
            except RunError as exc:
                raise ApiError(400, str(exc)) from None
            self.runs[run.id] = run
            while len(self.runs) > self.MAX_RUNS:
                self.runs.popitem(last=False)
            return run.snapshot()

    def _run(self, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if run is None:
            raise ApiError(404, "unknown run")
        return run

    def get_run(self, run_id: str) -> dict:
        with self.lock:
            run = self._run(run_id)
        return run.snapshot()

    def advance(self, run_id: str) -> dict:
        with self.lock:
            run = self._run(run_id)
        with run.lock:  # other runs and the file/status API stay responsive during a model call
            return run.advance()

    def approve(self, run_id: str, body: dict) -> dict:
        approval_id = body.get("approval_id")
        allow = body.get("allow")
        if not isinstance(approval_id, str) or not isinstance(allow, bool):
            raise ApiError(400, "approval_id (text) and allow (true or false) are required")
        with self.lock:
            run = self._run(run_id)
        with run.lock:
            try:
                return run.resolve_approval(approval_id, allow)
            except RunError as exc:
                raise ApiError(409, str(exc)) from None

    def decide_change(self, change_id: str, action: str) -> dict:
        if action == "accept":
            return self.toolbox.accept_change(change_id)
        if action == "revert":
            return self.toolbox.revert_change(change_id)
        return self.toolbox.reject_change(change_id)


def _query(query: dict, key: str, default=None) -> str:
    values = query.get(key)
    if not values:
        if default is None:
            raise ApiError(400, f"missing query parameter: {key}")
        return default
    return values[0]


def route(state: AppState, method: str, path: str, query: dict, body: dict):
    if method == "GET":
        if path == "/api/status":
            return 200, state.status()
        if path == "/api/tree":
            return 200, state.tool("list_dir", {"path": _query(query, "path", ".")})
        if path == "/api/file":
            return 200, state.tool("read_file", {"path": _query(query, "path")})
        if path == "/api/changes":
            return 200, {"changes": state.toolbox.list_changes()}
        match = RUN_RE.fullmatch(path)
        if match and match.group(2) is None:
            return 200, state.get_run(match.group(1))
    elif method == "POST":
        if path == "/api/runs":
            return 201, state.create_run(body.get("goal"))
        match = RUN_RE.fullmatch(path)
        if match and match.group(2) == "/advance":
            return 200, state.advance(match.group(1))
        if match and match.group(2) == "/approval":
            return 200, state.approve(match.group(1), body)
        change = CHANGE_RE.fullmatch(path)
        if change:
            return 200, state.decide_change(change.group(1), change.group(2))
    raise ApiError(404, "not found")


class Handler(BaseHTTPRequestHandler):
    server_version = "NewAlMobile/" + __version__

    def log_message(self, fmt, *args):  # keep the phone log quiet; never log headers or tokens
        pass

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        host = self.headers.get("Host", "").split(":")[0]
        if host not in ALLOWED_HOSTS:
            return self._send_json(403, {"error": "host not allowed"})
        path, _, query_string = self.path.partition("?")
        if method == "GET" and path in STATIC:
            filename, content_type = STATIC[path]
            return self._send((UI_DIR / filename).read_bytes(), content_type, 200)
        if not self._authorized():
            return self._send_json(401, {"error": "unauthorized"})
        try:
            body = self._read_body() if method == "POST" else {}
            status, payload = route(self.server.state, method, path, parse_qs(query_string), body)
        except ApiError as exc:
            status, payload = exc.status, {"error": exc.message}
        except Exception:  # log the trace to the Termux console, return nothing sensitive
            traceback.print_exc(file=sys.stderr)
            status, payload = 500, {"error": "internal error"}
        self._send_json(status, payload)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[len("Bearer "):].encode("utf-8"),
                                   self.server.state.token.encode("utf-8"))

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise ApiError(400, "invalid Content-Length") from None
        if length > MAX_BODY:
            raise ApiError(413, "request body too large")
        if length == 0:
            return {}
        try:
            data = json.loads(self.rfile.read(length))
        except ValueError:
            raise ApiError(400, "body must be JSON") from None
        if not isinstance(data, dict):
            raise ApiError(400, "body must be a JSON object")
        return data

    def _send_json(self, status: int, payload) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(body, "application/json; charset=utf-8", status)

    def _send(self, body: bytes, content_type: str, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)


class _Server(ThreadingHTTPServer):
    daemon_threads = True


def make_server(host: str, port: int, state: AppState) -> _Server:
    if host != "127.0.0.1":
        raise ValueError("NewAl Mobile only binds to 127.0.0.1")
    server = _Server((host, port), Handler)
    server.state = state
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="newal_mobile",
                                     description="NewAl Mobile phone agent (listens on 127.0.0.1 only).")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--providers", default=None, help="path to a providers JSON file")
    args = parser.parse_args(argv)

    home = state_dir()
    token = load_or_create_token(home)
    try:
        providers = load_providers(args.providers or (home / "providers.json"))
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    if not providers:
        print("no usable providers: check the api_key_env variables in providers.json", file=sys.stderr)
        return 2
    state = AppState(args.workspace or workspace_dir(), token, FallbackChain(providers))
    server = make_server("127.0.0.1", args.port, state)
    print(f"NewAl Mobile on http://127.0.0.1:{args.port}  (open this in the phone browser)")
    print(f"Access token file: {home / 'token'}  (paste it into the page once)")
    print("Providers:", ", ".join(p.name for p in providers))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
