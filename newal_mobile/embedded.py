"""Entry point used by the Android APK, where Python runs inside the app (Chaquopy).

No Termux is needed: the APK calls start() once, the HTTP server runs on a daemon thread
inside the app process, and the WebView talks to it on 127.0.0.1 with the same bearer token.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

from . import server as server_module
from .config import ConfigError, load_providers
from .providers import FallbackChain

_lock = threading.Lock()
_server = None


def start(home: str, workspace: str, ui_dir: str, token: str, port: int = 8787) -> str:
    """Start the engine once. Returns "ok", or a short error message for the app to show."""
    global _server
    with _lock:
        if _server is not None:
            return "ok"
        if not token or len(token) < 32:
            return "token is too short"
        home_path = Path(home)
        home_path.mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("HOME", str(home_path))
        server_module.UI_DIR = Path(ui_dir)
        try:
            providers = load_providers(home_path / "providers.json")
        except ConfigError as exc:
            return f"config error: {exc}"
        if not providers:
            return "no usable providers"
        state = server_module.AppState(workspace, token, FallbackChain(providers), state_directory=home_path)
        try:
            httpd = server_module.make_server("127.0.0.1", int(port), state)
        except OSError as exc:
            return f"port {port} is busy ({exc.strerror or type(exc).__name__})"
        threading.Thread(target=httpd.serve_forever, name="newal-engine", daemon=True).start()
        _server = httpd
        return "ok"


def stop() -> None:
    global _server
    with _lock:
        if _server is not None:
            _server.shutdown()
            _server.server_close()
            _server = None
