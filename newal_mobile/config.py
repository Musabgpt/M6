"""Runtime configuration: state directory, access token, workspace and providers."""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

from .providers import OpenAICompatibleProvider


class ConfigError(ValueError):
    pass


# Keyless free routes, from NewAl-Cloud (Setup.java) and M4's FreeLLMPool catalog (provider_routes.py).
# They are tried in order and all share one privacy tier, so fallback never moves data to a
# different tier. Availability and quotas are not guaranteed.
DEFAULT_PROVIDERS = [
    {"name": "kilo-auto-free", "base_url": "https://api.kilo.ai/api/gateway",
     "model": "kilo-auto/free", "privacy_tier": "free-shared"},
    {"name": "llm7-free", "base_url": "https://api.llm7.io/v1",
     "model": "default", "privacy_tier": "free-shared"},
    {"name": "pollinations-free", "base_url": "https://text.pollinations.ai/openai",
     "model": "openai-fast", "privacy_tier": "free-shared"},
    {"name": "ovhcloud-free", "base_url": "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1",
     "model": "Qwen3-Coder-30B-A3B-Instruct", "privacy_tier": "free-shared"},
]


def state_dir(env=os.environ) -> Path:
    return Path(env.get("NEWAL_STATE_DIR") or Path.home() / ".newal-mobile")


def workspace_dir(env=os.environ) -> Path:
    return Path(env.get("NEWAL_WORKSPACE") or Path.home() / "workspace")


def load_or_create_token(directory, env=os.environ) -> str:
    if env.get("NEWAL_TOKEN"):
        return env["NEWAL_TOKEN"].strip()
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "token"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    token = secrets.token_urlsafe(32)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(token + "\n")
    return token


def load_providers(path=None, env=os.environ) -> list[OpenAICompatibleProvider]:
    entries = DEFAULT_PROVIDERS
    if path and Path(path).exists():
        try:
            entries = json.loads(Path(path).read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ConfigError(f"providers file is not valid JSON: {exc}") from None
        if not isinstance(entries, list) or not entries:
            raise ConfigError("providers file must contain a non-empty list")
    providers = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ConfigError("each provider must be an object")
        for key in ("name", "base_url", "model", "privacy_tier"):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise ConfigError(f"provider field missing: {key}")
        url = entry["base_url"]
        if not (url.startswith("https://") or url.startswith("http://127.0.0.1")
                or url.startswith("http://localhost")):
            raise ConfigError(f"{entry['name']}: base_url must be https (or a loopback http URL)")
        key_env = entry.get("api_key_env")
        api_key = env.get(key_env, "") if key_env else ""
        if key_env and not api_key:
            continue  # no key in the environment: this provider is not usable, so skip it
        providers.append(OpenAICompatibleProvider(
            entry["name"], url, entry["model"], api_key=api_key, privacy_tier=entry["privacy_tier"]))
    return providers
